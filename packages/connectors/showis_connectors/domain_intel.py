"""Anahtarsız domain istihbarat connector'ı: crt.sh, DoH, RDAP, Wayback CDX.

Dört uç da herkese açıktır ve anahtar gerektirmez:

* crt.sh — Sertifika şeffaflığı kayıtlarından alt alan adları.
* Google Public DNS (DoH) — A/AAAA/MX/NS/TXT çözümlemesi.
* rdap.org — Kayıt tarihi, registrar, ad sunucuları ve durum bilgisi.
* Wayback CDX — Arşivde ilk/son görülme ve örnek URL'ler.

Ağ erişimi ``showis_engine.http.HttpClient`` üzerinden gider; SSRF kapısı,
robots politikası ve HTTP istek bütçesi korunur. Hatalar
``DomainIntelError`` ile anlaşılır Türkçe mesaja çevrilir; pivot katmanı bunu
``PivotError`` olarak yükseltir.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any

from showis_engine.http import BudgetExceededError, HttpClient

CRT_SH_URL = "https://crt.sh/"
#: Görüntüleme/provenance için joker sorgu adresi (indirme ucu değil).
CRT_SH_PAGE = "https://crt.sh/?q=%25.{domain}"
DNS_DOH_URL = "https://dns.google/resolve"
DNS_DOH_PAGE = "https://dns.google/resolve?name={domain}"
RDAP_URL = "https://rdap.org/domain/{domain}"
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_PAGE = (
    "https://web.archive.org/cdx/search/cdx?url={domain}/*&output=json&collapse=urlkey"
)

#: Tek uç yanıtı için üst sınır (bayt). Daha büyük yanıt işlenmez.
MAX_RESPONSE_BYTES = 1_500_000
#: Tek HTTP isteği için toplam süre (saniye); HttpClient timeout'u ayrıca uygular.
REQUEST_DEADLINE = 20.0
MAX_SUBDOMAINS = 100
MAX_DNS_VALUES = 20
MAX_TXT_LENGTH = 512
MAX_WAYBACK_ROWS = 40

_DNS_TYPES: tuple[tuple[str, int], ...] = (
    ("A", 1),
    ("AAAA", 28),
    ("MX", 15),
    ("NS", 2),
    ("TXT", 16),
)
_VERIFICATION_PREFIXES: tuple[str, ...] = (
    "google-site-verification=",
    "ms=",
    "apple-domain-verification=",
    "facebook-domain-verification=",
    "atlassian-domain-verification=",
    "adobe-idp-site-verification=",
    "docusign=",
    "shopify-verification=",
)
_HOST_RE = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)
_QUOTED_TXT_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


class DomainIntelError(RuntimeError):
    """Tek bir kamuya açık uçtaki hata; mesaj kullanıcıya gösterilebilir."""


@dataclass
class DnsRecords:
    """Bir alan adının A/AAAA/MX/NS/TXT çözümlemesi."""

    a: list[str] = field(default_factory=list)
    aaaa: list[str] = field(default_factory=list)
    mx: list[str] = field(default_factory=list)
    ns: list[str] = field(default_factory=list)
    txt: list[str] = field(default_factory=list)
    spf: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.a or self.aaaa or self.mx or self.ns or self.txt)


def _timeout_message(label: str) -> str:
    return f"{label}: istek zaman aşımına uğradı"


async def _get_json(
    client: HttpClient,
    url: str,
    *,
    label: str,
    params: dict[str, str] | None = None,
    accept: str = "application/json",
    not_found_ok: bool = False,
) -> Any:
    """GET + JSON; boyut sınırı ve anlaşılır Türkçe hata uygular."""
    try:
        response = await asyncio.wait_for(
            client.get(
                url,
                params=params,
                headers={"Accept": accept},
                check_robots=False,
                follow_redirects=True,
                max_retries=1,
            ),
            timeout=REQUEST_DEADLINE,
        )
    except asyncio.TimeoutError as exc:
        raise DomainIntelError(_timeout_message(label)) from exc
    except BudgetExceededError as exc:
        raise DomainIntelError(f"{label}: HTTP istek bütçesi tükendi") from exc
    except PermissionError as exc:
        raise DomainIntelError(f"{label}: politika erişimi engelledi") from exc
    except Exception as exc:  # noqa: BLE001 - ağ hatası tek mesaja çevrilir
        if "timeout" in str(exc).lower():
            raise DomainIntelError(_timeout_message(label)) from exc
        raise DomainIntelError(
            f"{label}: bağlantı kurulamadı ({type(exc).__name__})"
        ) from exc

    if response.status_code == 404 and not_found_ok:
        return None
    if response.status_code == 429:
        raise DomainIntelError(f"{label}: hız sınırı (HTTP 429); sonra tekrar deneyin")
    if response.status_code >= 400:
        raise DomainIntelError(f"{label}: HTTP {response.status_code} döndürdü")

    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise DomainIntelError(
            f"{label}: yanıt boyutu sınırı aşıldı ({MAX_RESPONSE_BYTES} bayt)"
        )
    raw = response.content or b""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DomainIntelError(
            f"{label}: yanıt boyutu sınırı aşıldı ({MAX_RESPONSE_BYTES} bayt)"
        )
    if not raw:
        raise DomainIntelError(f"{label}: boş yanıt döndü")
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise DomainIntelError(f"{label}: geçersiz JSON döndürdü") from exc


def _normalize_host(value: str) -> str | None:
    text = str(value or "").strip().lower()
    while text.startswith("*."):
        text = text[2:]
    text = text.strip().rstrip(".")
    if not text or len(text) > 253:
        return None
    if not _HOST_RE.match(text):
        return None
    return text


def normalize_domain(value: str) -> str:
    """Pivot girdisini tekil alan adına indirger; geçersizse DomainIntelError."""
    text = str(value or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.strip().rstrip(".")
    if text.startswith("www."):
        text = text[4:]
    if not text:
        raise DomainIntelError("geçerli bir alan adı gerekli")
    if not text.isascii():
        try:
            text = text.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise DomainIntelError("alan adı uluslararası biçimde çözümlenemedi") from exc
    name = _normalize_host(text)
    if name is None or "." not in name:
        raise DomainIntelError(f"geçerli bir alan adı değil: {value}")
    return name


def _unquote_txt(value: str) -> str:
    """TXT kaydındaki parçalı tırnakları birleştirir."""
    pieces = _QUOTED_TXT_RE.findall(str(value or ""))
    text = "".join(pieces) if pieces else str(value or "").strip().strip('"')
    return text.strip()[:MAX_TXT_LENGTH]


async def fetch_subdomains(
    client: HttpClient, domain: str, *, limit: int = MAX_SUBDOMAINS
) -> list[str]:
    """crt.sh sertifika şeffaflığı kayıtlarından tekil alt alan adları."""
    payload = await _get_json(
        client,
        CRT_SH_URL,
        label="crt.sh",
        params={"q": f"%.{domain}", "output": "json"},
    )
    if not isinstance(payload, list):
        raise DomainIntelError("crt.sh: beklenmeyen yanıt biçimi")
    names: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        for raw_name in str(row.get("name_value") or "").splitlines():
            name = _normalize_host(raw_name)
            if name and name != domain and name.endswith(f".{domain}"):
                names.add(name)
    return sorted(names)[: max(0, limit)]


def _parse_mx(value: str) -> str | None:
    parts = value.split()
    host = parts[-1] if parts else ""
    name = _normalize_host(host)
    return name


def _parse_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


async def fetch_dns_records(client: HttpClient, domain: str) -> DnsRecords:
    """DoH ile A/AAAA/MX/NS/TXT kayıtları; TXT'den SPF/doğrulama ipuçları."""
    records = DnsRecords()
    failures: list[str] = []
    responded = 0

    for rtype, code in _DNS_TYPES:
        try:
            payload = await _get_json(
                client,
                DNS_DOH_URL,
                label=f"DNS {rtype}",
                params={"name": domain, "type": rtype},
                accept="application/dns-json",
            )
        except DomainIntelError as exc:
            failures.append(str(exc))
            continue
        if not isinstance(payload, dict):
            failures.append(f"DNS {rtype}: beklenmeyen yanıt biçimi")
            continue
        responded += 1
        status = payload.get("Status")
        if status not in (0, None):
            # 3 = NXDOMAIN, 2 = SERVFAIL: bu tür için kayıt yok.
            continue
        values: list[str] = []
        for answer in payload.get("Answer") or []:
            if not isinstance(answer, dict):
                continue
            answer_type = answer.get("type")
            if answer_type is not None and answer_type != code:
                continue
            data = str(answer.get("data") or "").strip()
            if data:
                values.append(data)

        if rtype == "A":
            records.a = [ip for ip in (_parse_ip(v) for v in values) if ip][:MAX_DNS_VALUES]
        elif rtype == "AAAA":
            records.aaaa = [ip for ip in (_parse_ip(v) for v in values) if ip][:MAX_DNS_VALUES]
        elif rtype == "MX":
            hosts = [host for host in (_parse_mx(v) for v in values) if host]
            records.mx = hosts[:MAX_DNS_VALUES]
        elif rtype == "NS":
            hosts = [host for host in (_normalize_host(v) for v in values) if host]
            records.ns = hosts[:MAX_DNS_VALUES]
        else:
            txt = [text for text in (_unquote_txt(v) for v in values) if text]
            records.txt = txt[:MAX_DNS_VALUES]
            records.spf = [text for text in txt if text.lower().startswith("v=spf1")][:5]
            records.verification = [
                text
                for text in txt
                if text.lower().startswith(_VERIFICATION_PREFIXES)
            ][:10]

    if responded == 0:
        detail = failures[0] if failures else "bilinmeyen hata"
        raise DomainIntelError(f"DNS çözümlemesi başarısız: {detail}")
    records.warnings = failures
    return records


def _vcard_fn(entity: Any) -> str | None:
    if not isinstance(entity, dict):
        return None
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2 or not isinstance(vcard[1], list):
        return None
    for item in vcard[1]:
        if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
            text = str(item[3] or "").strip()
            if text:
                # Redakte değerler ("REDACTED FOR PRIVACY") olduğu gibi korunur.
                return text
    return None


def _rdap_registrar(payload: dict[str, Any]) -> str | None:
    entities = payload.get("entities")
    if not isinstance(entities, list):
        return None
    fallback: str | None = None
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = _vcard_fn(entity)
        if not name:
            continue
        roles = entity.get("roles")
        if isinstance(roles, list) and "registrar" in roles:
            return name
        if fallback is None:
            fallback = name
    return fallback


async def fetch_rdap_record(client: HttpClient, domain: str) -> dict[str, Any]:
    """rdap.org kaydı: tarihler, registrar, ad sunucuları, durumlar."""
    payload = await _get_json(
        client,
        RDAP_URL.format(domain=domain),
        label="RDAP",
        not_found_ok=True,
    )
    if payload is None:
        raise DomainIntelError("RDAP: alan adı kaydı bulunamadı")
    if not isinstance(payload, dict):
        raise DomainIntelError("RDAP: beklenmeyen yanıt biçimi")

    dates: dict[str, str] = {}
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction") or "").strip().lower()
        date = str(event.get("eventDate") or "").strip()
        if action and date:
            dates[action] = date

    nameservers: list[str] = []
    for ns in payload.get("nameservers") or []:
        if not isinstance(ns, dict):
            continue
        name = _normalize_host(str(ns.get("ldhName") or ns.get("unicodeName") or ""))
        if name and name not in nameservers:
            nameservers.append(name)

    statuses = [
        str(status).strip()
        for status in (payload.get("status") or [])
        if str(status).strip()
    ]
    return {
        "handle": str(payload.get("handle") or "").strip() or None,
        "registrar": _rdap_registrar(payload),
        "created_at": dates.get("registration"),
        "updated_at": dates.get("last changed") or dates.get("last update"),
        "expires_at": dates.get("expiration"),
        "nameservers": nameservers[:MAX_DNS_VALUES],
        "statuses": statuses[:10],
        "source": "rdap.org",
        "source_url": RDAP_URL.format(domain=domain),
    }


def _archive_time(stamp: str) -> str:
    """`20150101120000` → `2015-01-01T12:00:00Z`; biçim bilinmiyorsa ham değer."""
    text = str(stamp or "").strip()
    if len(text) >= 14 and text[:14].isdigit():
        return (
            f"{text[0:4]}-{text[4:6]}-{text[6:8]}T"
            f"{text[8:10]}:{text[10:12]}:{text[12:14]}Z"
        )
    return text


async def fetch_wayback_history(
    client: HttpClient, domain: str, *, limit: int = MAX_WAYBACK_ROWS
) -> dict[str, Any]:
    """Wayback CDX: ilk/son görülme, kayıt sayısı ve örnek URL'ler."""
    payload = await _get_json(
        client,
        WAYBACK_CDX_URL,
        label="Wayback CDX",
        params={
            "url": f"{domain}/*",
            "output": "json",
            "limit": str(max(1, limit)),
            "collapse": "urlkey",
            "fl": "timestamp,original",
        },
    )
    rows = payload if isinstance(payload, list) else []
    timestamps: list[str] = []
    samples: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        stamp = str(row[0] or "").strip()
        if not stamp.isdigit():
            continue  # başlık satırı ("timestamp", "original")
        timestamps.append(stamp)
        original = str(row[1] or "").strip()
        if (
            original.startswith(("http://", "https://"))
            and len(original) <= 1900
            and all(sample["url"] != original for sample in samples)
            and len(samples) < 8
        ):
            samples.append({"timestamp": stamp, "url": original})
    return {
        "count": len(timestamps),
        "first_seen": _archive_time(min(timestamps)) if timestamps else None,
        "last_seen": _archive_time(max(timestamps)) if timestamps else None,
        "samples": samples,
        "source": "web.archive.org",
        "source_url": WAYBACK_PAGE.format(domain=domain),
    }

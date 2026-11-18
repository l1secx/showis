#!/usr/bin/env python3
"""WhatsMyName (WMN) verisini Showis SiteSpec JSON'una donusturur.

Girdi, WMN deposundaki ``wmn-data.json`` dosyasidir (CC BY-SA 4.0,
Copyright (C) 2015-2026 Micah Hoffman). Veri **kopyalanmaz, turetilir**:
her WMN kaydi Showis'in ``SiteSpec`` alanlarina eslenir, uymayanlar
gerekcesiyle dislanir ve sayaclari raporda tutulur.

Donusum kurallari (bkz. docs/superpowers/specs/2026-11-18-osint-depth-design.md §A)::

    uri_check `{account}`  -> url_template `{u}`
    e_code                 -> expect_status
    e_string               -> present_regex   (re.escape; WMN literal substring)
    m_string               -> absent_regex    (re.escape)
    m_code                 -> absent_statuses (tek elemanli liste)
    valid: false           -> dislanir
    post_body dolu         -> dislanir (motor GET-only)
    protection cloudflare/captcha/ddos-guard/anubis -> dislanir (bypass yok)
    cat                    -> category; platform = "web"
    name                   -> key (slug; cakisma -> -2, -3 ...)

Ek kurallar:

* ``headers`` yalnizca **allowlist**'teki adlarla tasinir (``Accept``,
  ``Accept-Language``); digerleri (``Cookie``, ``Authorization``, ``X-*``,
  ``Referer``, ``User-Agent``) dusurulur ve ``_generated.headers_dropped``
  sayacinda raporlanir (D4: fail-closed).
* Kategoriler **filtrelenmez**: ``cat`` degeri (``xx NSFW xx`` dahil) korunur.
  Tarama politikasi (orn. NSFW dislama) tuketici katmanin isidir.

Ek guvenlik kurallari (motorun siniflandirma sirasina uyar):

* ``m_code == e_code`` ise kod ayirt edici degildir -> ``absent_statuses``
  bos birakilir, "yok" sinyali yalnizca ``absent_regex``'ten gelir. Ikisi de
  yoksa kayit ``ambiguous`` olarak dislanir (yanlis pozitif uretmemek icin).
* ``http://`` sablonlari ``https://``'e yukseltilir (cikti sozlesmesi https
  ister; https basarisiz olursa sonuc "unknown" olur, yanlis pozitif olmaz).

Kullanim::

    .venv/bin/python scripts/import_wmn_sites.py
    .venv/bin/python scripts/import_wmn_sites.py --source /tmp/opencode/wmn-source.json
    .venv/bin/python scripts/import_wmn_sites.py --fetch --out /tmp/wmn-sites.json

Cikis kodu: dogrulama hatasi varsa 1, aksi halde 0. Ag erisimi yalnizca
``--fetch`` verildiginde kullanilir.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/tmp/opencode/wmn-source.json")
DEFAULT_OUT = (
    REPO_ROOT / "packages/connectors/showis_connectors/sitecheck/data/wmn-sites.json"
)

#: Resmi kaynak (--fetch icin; testlerde kullanilmaz)
WMN_JSON_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
WMN_PROJECT_URL = "https://github.com/WebBreacher/WhatsMyName"

#: Bu koruma katmanlarina takilan siteler dislanir: motor bot-duvari asamaz.
BLOCKED_PROTECTIONS = frozenset({"cloudflare", "captcha", "ddos-guard", "anubis"})

#: D4 — tasinabilecek istek basliklari (allowlist). Disi **hicbir** baslik
#: aktarilmaz; boylece upstream'e eklenen bir oturum cerezi/anahtari sessizce
#: ucuncu tarafa gitmez (fail-closed). ``Cookie``/``Authorization``/``X-*``
#: bu yuzden hicbir zaman ciktiya yazilmaz.
ALLOWED_HEADERS = frozenset({"accept", "accept-language"})

#: CC BY-SA 4.0 zorunlu atif alanlari (§3(a)): lisans adi, URI, kaynak,
#: telif, katkida bulunanlar, degisiklik bildirimi.
LICENSE_NAME = "CC BY-SA 4.0"
LICENSE_URI = "https://creativecommons.org/licenses/by-sa/4.0/"
LICENSE_COPYRIGHT = "Copyright (C) 2015-2026 Micah Hoffman"
LICENSE_NOTE = "Derived from wmn-data.json; converted to Showis SiteSpec JSON."


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Kayit adini URL/anahtar dostu slug'a cevirir (``arabic``/``site``)."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "site"


def normalize_url(url: str) -> str:
    """Tekrar tespiti icin URL'i normalize eder (parça/port/sondaki ``/``)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _regex(value: Any) -> str | None:
    """WMN literal substring'ini kacirilmis regex'e cevirir."""
    text = _text(value)
    return re.escape(text) if text else None


def _blocked_protection(record: Mapping[str, Any]) -> bool:
    raw = record.get("protection") or []
    if isinstance(raw, str):
        raw = [raw]
    values = {str(p).strip().lower() for p in raw if str(p).strip()}
    return bool(values & BLOCKED_PROTECTIONS)


def _headers(record: Mapping[str, Any]) -> tuple[dict[str, str], int]:
    """Kayittaki basliklari allowlist'ten gecirir.

    Donus: ``(tasinan_basliklar, dusurulen_sayi)``. Dusurulen sayi > 0 ise
    kayit en az bir baslik kaybetmistir (sayac icin).
    """
    raw = record.get("headers")
    if not isinstance(raw, Mapping):
        return {}, 0
    kept: dict[str, str] = {}
    dropped = 0
    for name, value in raw.items():
        text_name = str(name).strip()
        if not text_name:
            continue
        if text_name.lower() in ALLOWED_HEADERS and str(value):
            kept[text_name] = str(value)
        else:
            dropped += 1
    return kept, dropped


# ---------------------------------------------------------------------------
# Donusum
# ---------------------------------------------------------------------------


@dataclass
class ConversionResult:
    sites: list[dict[str, Any]] = field(default_factory=list)
    #: dislama sayaclari (Wmn kaydi adedi)
    excluded: dict[str, int] = field(
        default_factory=lambda: {
            "invalid": 0,
            "post": 0,
            "protected": 0,
            "ambiguous": 0,
            "duplicate": 0,
        }
    )
    sites_total: int = 0
    http_upgraded: int = 0
    #: baslik blogu tasinan tutulan kayit sayisi
    headers_carried: int = 0
    #: en az bir basligi allowlist disi oldugu icin dusurulen tutulan kayit sayisi
    headers_dropped: int = 0
    #: dusurulen toplam baslik sayisi
    headers_removed: int = 0
    #: dislanan kayitlar: (ad, neden)
    dropped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def kept(self) -> int:
        return len(self.sites)


def _convert_record(record: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Tek WMN kaydini SiteSpec sozlugune cevirir.

    Donus: ``(site_dict, None)`` veya ``(None, dislama_nedeni)``.
    """
    if record.get("valid") is False:
        return None, "invalid"

    if _text(record.get("post_body")):
        return None, "post"

    if _blocked_protection(record):
        return None, "protected"

    uri = _text(record.get("uri_check"))
    if "{account}" not in uri:
        # Tum ornekler post_body'li; yine de savunmaci davran.
        return None, "post"

    url_template = uri.replace("{account}", "{u}")
    if url_template.startswith("http://"):
        url_template = "https://" + url_template[len("http://") :]

    expect_status = record.get("e_code")
    if not isinstance(expect_status, int) or isinstance(expect_status, bool):
        return None, "invalid"

    m_code = record.get("m_code")
    absent_statuses: list[int] = []
    if isinstance(m_code, int) and not isinstance(m_code, bool) and m_code != expect_status:
        # m_code == e_code ise kod "var"/"yok" ayrimi yapamaz; motor once
        # absent_statuses'a baktigi icin her yanit "yok" olurdu.
        absent_statuses = [m_code]

    absent_regex = _regex(record.get("m_string"))
    if not absent_statuses and absent_regex is None:
        # Ne kod ne metin sinyali var: "yok" karari verilemez -> yanlis pozitif.
        return None, "ambiguous"

    site = {
        "name": _text(record.get("name")) or "?",
        "url_template": url_template,
        "expect_status": expect_status,
        "present_regex": _regex(record.get("e_string")),
        "absent_regex": absent_regex,
        "absent_statuses": absent_statuses,
        "category": _text(record.get("cat")) or "misc",
        "platform": "web",
        "source": "wmn",
    }
    return site, None


def convert(wmn: Mapping[str, Any]) -> ConversionResult:
    """WMN dokümanini (``sites`` listesi) Showis SiteSpec kayitlarina cevirir."""
    records = wmn.get("sites") or []
    if not isinstance(records, list):
        raise ConversionError("WMN verisi 'sites' listesi icermiyor")

    result = ConversionResult(sites_total=len(records))
    used_keys: set[str] = set()
    seen_templates: dict[str, str] = {}
    http_upgraded = 0

    for record in records:
        if not isinstance(record, Mapping):
            result.excluded["invalid"] += 1
            continue
        name = _text(record.get("name")) or "?"
        site, reason = _convert_record(record)
        if site is None:
            assert reason is not None
            result.excluded[reason] = result.excluded.get(reason, 0) + 1
            result.dropped.append((name, reason))
            continue
        if _text(record.get("uri_check")).startswith("http://"):
            http_upgraded += 1

        normalized = normalize_url(site["url_template"])
        if normalized in seen_templates:
            result.excluded["duplicate"] += 1
            result.dropped.append(
                (name, f"duplicate:{seen_templates[normalized]}")
            )
            continue
        seen_templates[normalized] = name

        base = slugify(site["name"])
        key = base
        suffix = 2
        while key in used_keys:
            key = f"{base}-{suffix}"
            suffix += 1
        used_keys.add(key)

        headers, dropped_headers = _headers(record)
        if headers:
            site["headers"] = headers
            result.headers_carried += 1
        if dropped_headers:
            result.headers_dropped += 1
            result.headers_removed += dropped_headers
        result.sites.append({"key": key, **site})

    result.http_upgraded = http_upgraded
    return result


class ConversionError(RuntimeError):
    """Girdi beklenen WMN semasinda degil."""


# ---------------------------------------------------------------------------
# Dogrulama
# ---------------------------------------------------------------------------


def validate_sites(sites: list[Mapping[str, Any]]) -> list[str]:
    """Cikti sozlesmesi kontrolleri; bos liste = temiz."""
    issues: list[str] = []
    seen: set[str] = set()
    for site in sites:
        key = site.get("key")
        if not key:
            issues.append("key bos olan kayit var")
            continue
        if key in seen:
            issues.append(f"{key}: key tekrarlaniyor")
        seen.add(key)

        template = str(site.get("url_template") or "")
        if not template.startswith("https://"):
            issues.append(f"{key}: url_template https ile baslamiyor ({template})")
        if "{u}" not in template:
            issues.append(f"{key}: url_template '{{u}}' icermiyor")

        expect = site.get("expect_status")
        if not isinstance(expect, int) or isinstance(expect, bool):
            issues.append(f"{key}: expect_status int degil ({expect!r})")

        for fname in ("present_regex", "absent_regex"):
            pattern = site.get(fname)
            if pattern is None:
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                issues.append(f"{key}: {fname} derlenemiyor ({exc})")

        statuses = site.get("absent_statuses")
        if not isinstance(statuses, list) or not all(
            isinstance(s, int) and not isinstance(s, bool) for s in statuses
        ):
            issues.append(f"{key}: absent_statuses int listesi degil ({statuses!r})")

        if "headers" in site:
            headers = site["headers"]
            if not isinstance(headers, dict) or not headers:
                issues.append(f"{key}: headers bos veya nesne degil ({headers!r})")
            elif not all(
                isinstance(name, str) and isinstance(value, str)
                for name, value in headers.items()
            ):
                issues.append(f"{key}: headers anahtar/degerleri string degil")
            else:
                extra = {n.lower() for n in headers} - ALLOWED_HEADERS
                if extra:
                    issues.append(
                        f"{key}: headers allowlist disi baslik iceriyor ({sorted(extra)})"
                    )
    return issues


# ---------------------------------------------------------------------------
# Doküman
# ---------------------------------------------------------------------------


def license_block(wmn: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``_license`` zarfini uretir (D5 / CC BY-SA §3(a)).

    ``authors`` kaynaktaki ``authors`` listesinden gelir; yoksa telif satirina
    duser. Veri dosyasi tek basina dolasabilmeli: lisans adi + URI + kaynak +
    telif + katkida bulunanlar + degisiklik bildirimi hep burada.
    """
    raw_authors = (wmn or {}).get("authors")
    authors = [
        str(item).strip()
        for item in (raw_authors if isinstance(raw_authors, list) else [])
        if str(item).strip()
    ]
    return {
        "name": LICENSE_NAME,
        "uri": LICENSE_URI,
        "source": WMN_PROJECT_URL,
        "copyright": LICENSE_COPYRIGHT,
        "authors": authors or [LICENSE_COPYRIGHT],
        "adapted": True,
        "note": LICENSE_NOTE,
    }


def build_document(
    wmn: Mapping[str, Any], *, source_label: str = "wmn-data.json"
) -> tuple[dict[str, Any], ConversionResult]:
    """Cikti zarfini (``_license`` / ``_generated`` / ``sites``) uretir."""
    result = convert(wmn)
    document = {
        "_license": license_block(wmn),
        "_generated": {
            "from": source_label,
            "sites_total": result.sites_total,
            "kept": result.kept,
            "excluded": dict(result.excluded),
            "http_upgraded": result.http_upgraded,
            "headers_carried": result.headers_carried,
            "headers_dropped": result.headers_dropped,
            "headers_removed": result.headers_removed,
            "header_allowlist": sorted(ALLOWED_HEADERS),
        },
        "sites": result.sites,
    }
    return document, result


# ---------------------------------------------------------------------------
# Girdi / cikti
# ---------------------------------------------------------------------------


def fetch_source(destination: Path, *, url: str = WMN_JSON_URL) -> Path:
    """Kaynagi indirir (yalnizca ``--fetch`` ile; testler bunu kullanmaz)."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (sabit URL)
        payload = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def load_source(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConversionError(f"kaynak bulunamadi: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConversionError(f"kaynak gecerli JSON degil ({path}): {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        raise ConversionError(f"kaynak WMN semasinda degil (sites listesi yok): {path}")
    return data


def write_output(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WhatsMyName verisini Showis SiteSpec JSON'una donusturur."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"WMN wmn-data.json yolu (varsayilan: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"uretilen JSON yolu (varsayilan: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="kaynak dosyayi yoksa/istenirse resmi URL'den indir (ag erisimi)",
    )
    args = parser.parse_args(argv)

    try:
        if args.fetch:
            fetch_source(args.source)
            print(f"kaynak indirildi: {args.source} <- {WMN_JSON_URL}")
        wmn = load_source(args.source)
        document, result = build_document(wmn)
    except ConversionError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    issues = validate_sites(document["sites"])
    if issues:
        print(f"HATA: {len(issues)} dogrulama sorunu", file=sys.stderr)
        for issue in issues[:20]:
            print(f"  - {issue}", file=sys.stderr)
        if len(issues) > 20:
            print(f"  ... {len(issues) - 20} sorun daha", file=sys.stderr)
        return 1

    write_output(args.out, document)

    excluded = result.excluded
    print(f"kaynak      : {args.source}")
    print(f"cikti       : {args.out}")
    print(f"WMN kayit   : {result.sites_total}")
    print(f"tutulan     : {result.kept}")
    print(
        "dislanan    : "
        f"invalid={excluded['invalid']} post={excluded['post']} "
        f"protected={excluded['protected']} ambiguous={excluded['ambiguous']} "
        f"duplicate={excluded['duplicate']}"
    )
    print(f"http->https : {result.http_upgraded}")
    print(
        f"baslik      : {result.headers_carried} kayitta allowlist basligi tasindi"
        f" ({result.headers_dropped} kayit, {result.headers_removed} baslik dusuruldu)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

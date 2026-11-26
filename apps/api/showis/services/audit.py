"""Audit log yazimi (SEC-13).

Kurallar:
* Sır/PII **değeri** yazılmaz; yalnız kimlikler, alan adları ve durum geçişleri.
* `actor_ip` ve `user_agent` şemada kolon değil (migration yok) — `details`
  içinde saklanır: `details["actor_ip"]`, `details["user_agent"]`.
* `actor` varsayılanı tek kullanıcılı operatör modeli: ``operator``.
"""

import ipaddress
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import showis.db.session as db_session_module
from fastapi import Request
from showis.db.models import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.audit")

#: User-agent alanı için üst sınır (log şişmesini önler)
_MAX_USER_AGENT = 256

#: IP adresi için izin verilen karakterler (IPv4/IPv6) ve üst uzunluk
_IP_RE = re.compile(r"^[0-9A-Fa-f:.]{1,64}$")


def _clean_ip(value: str | None) -> str | None:
    """Başlıktan gelen aday IP'yi doğrular; geçersizse None.

    Başlıklar istemci tarafından doldurulabilir: yalnız IP karakterleri kabul
    edilir (boşluk, CRLF, noktalı virgül, virgül kırıntısı atılır).
    """
    if not value:
        return None
    candidate = value.strip().split()[0] if value.strip() else ""
    if not candidate or not _IP_RE.match(candidate):
        return None
    return candidate


#: T-356: XFF/X-Real-IP'ye yalnızca bu CIDR'lerden gelen peer'lar için güvenilir.
#: Varsayılan = loopback + docker köprüleri (üretimde `SHOWIS_TRUSTED_PROXIES` ile daraltın).
_TRUSTED_PROXIES_ENV = "SHOWIS_TRUSTED_PROXIES"
_DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16"

#: (ham env değeri, ayrıştırılmış ağlar) — her istekte yeniden parse etmemek için
_trusted_networks_cache: tuple[str, list[ipaddress.IPv4Network | ipaddress.IPv6Network]] | None = (
    None
)


def trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """`SHOWIS_TRUSTED_PROXIES` CIDR listesi (geçersiz değerler atlanır)."""
    global _trusted_networks_cache
    raw = (os.getenv(_TRUSTED_PROXIES_ENV) or "").strip() or _DEFAULT_TRUSTED_PROXIES
    if _trusted_networks_cache is not None and _trusted_networks_cache[0] == raw:
        return _trusted_networks_cache[1]

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(
                "Gecersiz %s girdisi %r atlandi (CIDR beklenir)", _TRUSTED_PROXIES_ENV, entry
            )
    _trusted_networks_cache = (raw, networks)
    return networks


def _in_networks(
    ip: str | None, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip_from_headers(
    headers: Mapping[str, str], client_host: str | None = None
) -> str | None:
    """İstemci IP'sini **güvenilir proxy** modeliyle çözer (D-01/T-356).

    Tehdit: istemci `X-Forwarded-For` uydurabilir. API yalnız Caddy/tailnet
    arkasından erişilebilir olsa da container ağından doğrudan çağrılabildiği
    için başlıklara koşulsuz güvenmek adli izi sahteleştirir (Ayşe T-350).

    Algoritma:

    1. **Peer (TCP kaynağı) güvenilir proxy CIDR'lerinde değilse** → `XFF` ve
       `X-Real-IP` **yok sayılır**, peer döner. (Dışarıdan gelen sahte başlık etkisiz.)
    2. Peer güvenilirse: `X-Real-IP` (Caddy'nin tek atlama değeri) tercih edilir.
    3. Aksi halde `X-Forwarded-For` **soldan sağa** okunur ve **sağdan ilk
       güvenilmeyen** IP döner: zincirin sağındaki güvenilir proxy atlamaları
       atlanır, ilk güvenilmeyen (gerçek istemci) seçilir. Tüm atlamalar
       güvenilirse en soldaki değer döner (tümü proxy).
    4. Hiçbiri yoksa peer döner.

    Not: Caddy (T-319) `XFF`'i **ezer** (`header_up`), yani normalde zincir tek
    elemanlıdır; çok atlamalı dal savunma derinliğidir.
    """
    networks = trusted_proxy_networks()
    peer = _clean_ip(client_host)

    if not _in_networks(peer, networks):
        # Güvenilmeyen peer: başlıklar saldırgan kontrolünde → yok say
        return peer

    real_ip = _clean_ip(headers.get("x-real-ip"))
    if real_ip:
        return real_ip

    forwarded = headers.get("x-forwarded-for") or ""
    chain = [ip for ip in (_clean_ip(part) for part in forwarded.split(",")) if ip]
    if chain:
        for candidate in reversed(chain):
            if not _in_networks(candidate, networks):
                return candidate
        return chain[0]

    return peer


def client_ip_from_forwarded(
    headers: Mapping[str, str], client_host: str | None = None
) -> str | None:
    """Edge auth için: yalnız **X-Forwarded-For** zincirine güvenir.

    `X-Real-IP` Caddy `forward_auth` bloğunda set edilmiyor ve istemciden
    geçebiliyor (T17-03); bu yüzden edge hız sınırı anahtarında yok sayılır.
    Peer güvenilir proxy değilse başlıklar tamamen yok sayılır.
    """
    networks = trusted_proxy_networks()
    peer = _clean_ip(client_host)
    if not _in_networks(peer, networks):
        return peer
    forwarded = headers.get("x-forwarded-for") or ""
    chain = [ip for ip in (_clean_ip(part) for part in forwarded.split(",")) if ip]
    if chain:
        for candidate in reversed(chain):
            if not _in_networks(candidate, networks):
                return candidate
        return chain[0]
    return peer


def client_ip(request: Request) -> str | None:
    """İstemci IP'si (güvenilir proxy kontrolü: peer → X-Real-IP → XFF zinciri)."""
    client = request.client
    return client_ip_from_headers(request.headers, client.host if client else None)


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:_MAX_USER_AGENT] if value else None


def user_agent_from_headers(headers: Mapping[str, str]) -> str | None:
    value = headers.get("user-agent")
    return value[:_MAX_USER_AGENT] if value else None


async def record_audit(
    db: AsyncSession,
    action: str,
    case_id: str | None = None,
    actor: str = "operator",
    target: str | None = None,
    details: dict[str, Any] | None = None,
    actor_ip: str | None = None,
    user_agent_header: str | None = None,
) -> AuditLog:
    """Audit log kaydı oluşturur ve kaydeder.

    `actor_ip` / `user_agent_header` şemada ayrı kolon olmadığı için `details`
    içine yazılır (migration yok). `actor_ip=None` ise anahtar eklenmez.
    """
    payload: dict[str, Any] = dict(details or {})
    if actor_ip:
        payload.setdefault("actor_ip", actor_ip)
    if user_agent_header:
        payload.setdefault("user_agent", user_agent_header[:_MAX_USER_AGENT])

    log_entry = AuditLog(
        case_id=case_id,
        actor=actor,
        action=action,
        target=target,
        details=payload,
    )
    db.add(log_entry)
    await db.commit()
    await db.refresh(log_entry)
    return log_entry


async def record_request_audit(
    db: AsyncSession,
    request: Request,
    action: str,
    case_id: str | None = None,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """`record_audit` + istekten IP/user-agent (tek satırlık çağrı için)."""
    return await record_audit(
        db=db,
        action=action,
        case_id=case_id,
        target=target,
        details=details,
        actor_ip=client_ip(request),
        user_agent_header=user_agent(request),
    )


async def record_request_audit_safe(
    db: AsyncSession,
    request: Request,
    action: str,
    case_id: str | None = None,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Commit sonrası audit: hata işlemi 500'e düşürmesin.

    Audit **ayrı kısa oturumda** yazılır (çağıran oturumun engine'ine bağlı);
    böylece çağıranın transaction'ı ve ORM nesneleri etkilenmez, hata yolunda
    `rollback` ile expire olup `MissingGreenlet` üretmez (T17-04).
    Hata `error` seviyesinde loglanır; istemci başarılı işlemi tekrar deneyip
    çift kayıt üretmez.
    """
    try:
        bind = getattr(db, "bind", None) or db_session_module.engine
        async with AsyncSession(bind=bind, expire_on_commit=False) as audit_db:
            return await record_request_audit(
                db=audit_db,
                request=request,
                action=action,
                case_id=case_id,
                target=target,
                details=details,
            )
    except Exception:  # noqa: BLE001 - audit işlemi engellemez
        logger.exception("Audit yazilamadi (action=%s); islem etkilenmedi", action)
        return None


# ---------------------------------------------------------------------------
# Audit hız sınırı (T-343 / T-340 bulgu #4)
# ---------------------------------------------------------------------------

#: `auth.failed` / `request.rejected` için varsayılan kayıt hakkı (60 sn'de, IP+aksiyon)
DEFAULT_PER_MINUTE = 5
#: 503 (token yapılandırılmamış) yapılandırma hatasıdır: dakikada 1 kayıt yeter
CONFIG_ERROR_PER_MINUTE = 1
#: Pencere uzunluğu (sn)
THROTTLE_WINDOW_SECONDS = 60.0
#: Bellek koruması: spoof edilmiş XFF ile sınırsız anahtar üretilmesin
MAX_BUCKETS = 4096
_PER_MINUTE_ENV = "SHOWIS_AUTH_AUDIT_PER_MINUTE"


def _now() -> float:
    """Testlerde monkeypatch edilebilir saat (monotonic)."""
    return time.monotonic()


def per_minute_limit() -> int:
    raw = os.getenv(_PER_MINUTE_ENV)
    if not raw:
        return DEFAULT_PER_MINUTE
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        return DEFAULT_PER_MINUTE


@dataclass
class _Bucket:
    window_start: float
    count: int = 0
    suppressed: int = 0


@dataclass
class AuditThrottle:
    """(peer|claimed|action|status) başına pencere tabanlı audit hız sınırı.

    Amaç: 401/413 selinin `audit_logs`'u şişirmesini ve DB bağlantı havuzunu
    tüketmesini engellemek — yani **reddin kendisi DoS vektörü olmasın**.

    Davranış: pencere içinde `capacity` kadar kayıt yazılır; fazlası sayılır
    (bastırılır). Pencere dolduğunda (bir sonraki olayda) bastırılanlar için
    **tek bir özet** kaydı yazılır (`details["suppressed"]`).
    """

    window_seconds: float = THROTTLE_WINDOW_SECONDS
    max_buckets: int = MAX_BUCKETS
    buckets: dict[str, _Bucket] = field(default_factory=dict)
    #: Sayaçlar (gözlemlenebilirlik/testler)
    hard_suppressed: int = 0

    def check(self, key: str, capacity: int, now: float) -> tuple[bool, int]:
        """(izin_verildi, bastirilan_ozet_sayisi).

        Dönen ikinci değer > 0 ise çağıran `suppressed` alanlı özet kaydı yazmalı.
        """
        bucket = self.buckets.get(key)
        if bucket is None:
            if len(self.buckets) >= self.max_buckets:
                # En eski anahtarı at (dict insertion order) — sınırsız büyüme yok
                self.buckets.pop(next(iter(self.buckets)), None)
            self.buckets[key] = _Bucket(window_start=now, count=1)
            return True, 0

        summary = 0
        if now - bucket.window_start >= self.window_seconds:
            summary = bucket.suppressed
            bucket.window_start = now
            bucket.count = 0
            bucket.suppressed = 0

        if bucket.count < capacity:
            bucket.count += 1
            return True, summary

        bucket.suppressed += 1
        self.hard_suppressed += 1
        return False, summary


_audit_throttle = AuditThrottle()


def audit_throttle() -> AuditThrottle:
    return _audit_throttle


def reset_audit_throttle() -> None:
    """Sayaçları sıfırlar (testler / yapılandırma değişikliği)."""
    global _audit_throttle
    _audit_throttle = AuditThrottle()


async def record_throttled_event(
    db: AsyncSession,
    *,
    action: str,
    peer_ip: str | None,
    claimed_ip: str | None,
    status_code: int,
    method: str,
    path: str,
    user_agent_header: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> tuple[bool, int]:
    """401/413 gibi güvenlik olaylarını **hız sınırlı** yazar.

    Anahtar `peer|claimed|action|status`: XFF uydurarak sayaç atlatılamaz çünkü
    peer IP (TCP kaynağı) da anahtarda. Returns: (kayit_yazildi, bastirilan).
    """
    capacity = CONFIG_ERROR_PER_MINUTE if status_code == 503 else per_minute_limit()
    key = f"{peer_ip or '-'}|{claimed_ip or '-'}|{action}|{status_code}"
    allowed, suppressed = _audit_throttle.check(key, capacity, _now())
    target = f"{method} {path}"[:200]
    base: dict[str, Any] = {"status_code": status_code}
    if extra_details:
        base.update(extra_details)

    if suppressed:
        await record_audit(
            db=db,
            action=action,
            target=target,
            details={
                **base,
                "suppressed": suppressed,
                "window_seconds": int(_audit_throttle.window_seconds),
                "summary": True,
            },
            actor_ip=claimed_ip,
            user_agent_header=user_agent_header,
        )
    if allowed:
        await record_audit(
            db=db,
            action=action,
            target=target,
            details=dict(base),
            actor_ip=claimed_ip,
            user_agent_header=user_agent_header,
        )
    return allowed, suppressed


async def list_audit_logs(
    db: AsyncSession,
    case_id: str | None = None,
    limit: int = 500,
) -> list[AuditLog]:
    """Audit loglarını döner."""
    stmt = select(AuditLog)
    if case_id:
        stmt = stmt.where(AuditLog.case_id == case_id)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())

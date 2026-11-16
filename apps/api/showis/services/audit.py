"""Audit log yazimi (SEC-13).

Kurallar:
* Sır/PII **değeri** yazılmaz; yalnız kimlikler, alan adları ve durum geçişleri.
* `actor_ip` ve `user_agent` şemada kolon değil (migration yok) — `details`
  içinde saklanır: `details["actor_ip"]`, `details["user_agent"]`.
* `actor` varsayılanı tek kullanıcılı operatör modeli: ``operator``.
"""

import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from showis.db.models import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


def client_ip_from_headers(
    headers: Mapping[str, str], client_host: str | None = None
) -> str | None:
    """İstemci IP'sini güven sırasıyla çözer (D-01 düzeltmesi).

    Güven modeli: API yalnızca Caddy/tailnet arkasından erişilebilir (UFW `DROP`,
    yalnız `tailscale0`) ve Caddy `X-Forwarded-For`'u **kendisi yazar** (T-319).
    Bu yüzden:

    1. ``X-Real-IP`` — Caddy'nin doğrudan yazdığı tek atlama değeri (en güvenilir).
    2. ``X-Forwarded-For``'un **son** değeri — zincirin en yakın atlaması. **İlk**
       değere asla güvenilmez: istemci onu uydurabilir (adli iz sahte olur).
    3. ``request.client.host`` — başlık yoksa (doğrudan bağlantı).

    Değerler `_clean_ip()` ile doğrulanır; geçersizse bir sonraki kaynağa düşülür.
    """
    real_ip = _clean_ip(headers.get("x-real-ip"))
    if real_ip:
        return real_ip

    forwarded = headers.get("x-forwarded-for") or ""
    if forwarded:
        last = _clean_ip(forwarded.split(",")[-1])
        if last:
            return last

    return _clean_ip(client_host)


def client_ip(request: Request) -> str | None:
    """İstemci IP'si (X-Real-IP → XFF son → client.host)."""
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

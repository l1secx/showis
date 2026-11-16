"""Audit log yazimi (SEC-13).

Kurallar:
* Sır/PII **değeri** yazılmaz; yalnız kimlikler, alan adları ve durum geçişleri.
* `actor_ip` ve `user_agent` şemada kolon değil (migration yok) — `details`
  içinde saklanır: `details["actor_ip"]`, `details["user_agent"]`.
* `actor` varsayılanı tek kullanıcılı operatör modeli: ``operator``.
"""

from typing import Any

from fastapi import Request
from showis.db.models import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: User-agent alanı için üst sınır (log şişmesini önler)
_MAX_USER_AGENT = 256


def client_ip(request: Request) -> str | None:
    """İstemci IP'sini çıkarır.

    Güven sınırı: API yalnızca Caddy/tailnet arkasından erişilebilir (UFW `DROP`,
    yalnız `tailscale0`), bu yüzden `X-Forwarded-For` başlığına güvenilebilir.
    Yine de zincirin **ilk** değeri alınır ve değer 64 karakterle sınırlanır;
    başlık yoksa `request.client.host` kullanılır.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    client = request.client
    if client is not None and client.host:
        return client.host[:64]
    return None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
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

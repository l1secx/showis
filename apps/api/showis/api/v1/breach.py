"""Sızıntı yardımcı uçları: Pwned Passwords k-anonimlik proxy'si."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from showis.db.session import get_db
from showis.services.audit import record_request_audit_safe
from showis.services.breach.common import BreachProviderError
from showis.services.breach.pwned_passwords import fetch_range
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/breach", tags=["breach"])


@router.get("/password-range/{prefix}")
async def password_range(
    request: Request,
    db: AsyncSession = Depends(get_db),
    prefix: str = Path(pattern=r"^[0-9A-Fa-f]{5}$", description="SHA-1'in ilk 5 hex karakteri"),
):
    """Parola SHA-1 önekini (5 karakter) Pwned Passwords'e iletir.

    Tam hash/parola sunucuya gönderilmemelidir; tarayıcı eşleşmeyi yerelde yapar.
    """
    try:
        rows = await fetch_range(prefix)
    except BreachProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Önek denetim kaydına yazılmaz; yalnız eylem ve eşleşme sayısı tutulur.
    await record_request_audit_safe(
        db=db,
        request=request,
        action="breach.password_range",
        details={"matches": len(rows)},
    )
    return {"prefix": prefix.upper(), "suffixes": rows}

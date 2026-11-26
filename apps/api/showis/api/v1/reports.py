"""Rapor indirme uçları."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from showis.db.models import Report
from showis.db.session import get_db
from showis.services.audit import record_request_audit_safe
from showis.services.report_service import resolve_report_path, safe_case_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/reports", tags=["reports"])

#: Biçim -> (media type, ek başlıklar)
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "json": "application/json",
    # HTML tarayıcıda render edilebilir: autoescape'e ek olarak kaynak kısıtı
    "html": "text/html; charset=utf-8",
}

#: HTML raporu izole bir belge olarak sunulur (script/iframe/connect yok).
HTML_CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'"


@router.get("/{report_id}/download")
async def download_report(
    report_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Report).where(Report.id == report_id))
    report = res.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Rapor bulunamadı")
    if not report.storage_key:
        raise HTTPException(status_code=404, detail="Rapor dosyası eksik")

    # T112-R3: yeni yerleşim `REPORT_DIR/<case>/<ad>`; eski düz dosyalar
    # (`REPORT_DIR/<ad>`) da okunur — `storage_key` kayıtlı yol önce denenir.
    path: Path | None = resolve_report_path(report.storage_key, report.case_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Rapor dosyası diskte yok")

    # SEC-13: rapor indirme (tam PII içerir) adli iz bırakır — içerik yazılmaz
    await record_request_audit_safe(
        db=db,
        request=request,
        action="report.download",
        case_id=report.case_id,
        target=f"Report: {report.id}",
        details={"format": report.format},
    )

    # Dosya adı: istemciye giden Content-Disposition başlığında yalnız güvenli
    # belirteç kullanılır (CRLF/tırnak enjeksiyonu ve path traversal engellenir).
    safe_case = safe_case_token(report.case_id)
    media_type = _MEDIA_TYPES.get(report.format, "application/octet-stream")
    filename = f"showis-report-{safe_case}.{report.format}"
    headers = {}
    if report.format == "html":
        headers["Content-Security-Policy"] = HTML_CONTENT_SECURITY_POLICY
        headers["X-Content-Type-Options"] = "nosniff"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers=headers or None,
    )

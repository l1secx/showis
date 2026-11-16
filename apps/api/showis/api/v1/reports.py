"""Rapor indirme uçları."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from showis.db.models import Report
from showis.db.session import get_db
from showis.services.audit import record_request_audit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{report_id}/download")
async def download_report(
    report_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Report).where(Report.id == report_id))
    report = res.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not report.storage_key:
        raise HTTPException(status_code=404, detail="Report file missing")

    path = Path(report.storage_key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report file missing on disk")

    # SEC-13: rapor indirme (tam PII icerir) adli iz birakir — icerik yazilmaz
    await record_request_audit(
        db=db,
        request=request,
        action="report.download",
        case_id=report.case_id,
        target=f"Report: {report.id}",
        details={"format": report.format},
    )

    media_type = "application/pdf" if report.format == "pdf" else "application/json"
    filename = f"showis-report-{report.case_id}.{report.format}"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
    )

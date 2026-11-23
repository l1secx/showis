"""Tarama sonrası sınırlı kanıt zenginleştirme: screenshot + Wayback."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from showis.db.models import Case, Evidence, Finding, Scan
from showis_engine.browser import BrowserClient
from showis_engine.http import EvidenceStore, HttpClient
from showis_engine.policy import SSRFViolationError, guard_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.enrichment")

DEFAULT_LIMIT = 3


async def _archive_snapshot(url: str) -> tuple[bytes, str, int] | None:
    cdx = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={url}&output=json&filter=statuscode:200&limit=5&collapse=urlkey"
    )
    try:
        resp = await HttpClient(enable_ssrf_guard=True).get(
            cdx, check_robots=False, follow_redirects=True
        )
        if resp.status_code != 200:
            return None
        rows = json.loads(resp.content or b"[]")
        if not isinstance(rows, list) or len(rows) < 2:
            return None
        return resp.content, cdx, resp.status_code
    except Exception:
        return None


async def capture_scan_evidence(
    db: AsyncSession,
    case: Case,
    scan: Scan,
    findings: list[Finding],
    *,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """En yüksek skorlu URL'ler için PNG; 404 ise Wayback JSON kanıtı ekler."""
    if os.getenv("SHOWIS_SCREENSHOT_CAPTURE", "1") == "0":
        return 0
    candidates = sorted(
        [f for f in findings if f.url and f.status != "rejected"],
        key=lambda f: (-float(f.score or 0), f.id),
    )[: max(0, limit)]
    if not candidates:
        return 0

    existing = {
        (row.source_url or "", row.kind)
        for row in (
            await db.execute(select(Evidence).where(Evidence.case_id == case.id))
        ).scalars().all()
    }
    store = EvidenceStore()
    browser = BrowserClient(
        os.getenv("BROWSER_SERVICE_URL", "http://browser:8081")
    )
    added = 0
    retention = datetime.now(timezone.utc) + timedelta(days=int(case.retention_days or 30))

    for finding in candidates:
        url = finding.url
        if not url:
            continue
        if (url, "png") in existing:
            continue
        try:
            await asyncio.to_thread(guard_url, url)
        except SSRFViolationError:
            logger.info("Kanıt hedefi SSRF korumasıyla atlandı: %s", finding.id)
            continue
        try:
            result = await browser.render(url)
        except Exception as exc:
            logger.warning("Screenshot render hatası (%s): %s", finding.id, type(exc).__name__)
            continue

        payload: bytes | None = None
        kind = "png"
        http_status = result.status_code
        source = url
        if result.screenshot_b64 and not result.blocked:
            try:
                payload = base64.b64decode(result.screenshot_b64)
            except (ValueError, TypeError):
                payload = None
        elif result.status_code in (404, 410):
            archived = await _archive_snapshot(url)
            if archived:
                payload, source, http_status = archived
                kind = "json"

        if not payload:
            continue
        ev = store.store_bytes(payload, source_url=source, kind=kind, http_status=http_status)
        db.add(
            Evidence(
                id=ev.id,
                case_id=case.id,
                source_url=ev.source_url,
                kind=kind,
                content_hash=ev.content_hash,
                storage_key=ev.storage_key,
                http_status=http_status,
                user_agent=os.getenv("SHOWIS_SCREENSHOT_USER_AGENT", "showis-browser"),
                fetched_at=datetime.now(timezone.utc),
                expires_at=retention,
            )
        )
        finding.evidence_ids = list(dict.fromkeys([*(finding.evidence_ids or []), ev.id]))
        existing.add((source, kind))
        added += 1

    if added:
        await db.commit()
    return added

"""Kayıtlı izleme: yeni sızıntı ve yeni hesap bulgularını bildirime çevirir.

- Sızıntı: havuzdaki e-postalar anahtarsız sağlayıcı zincirinde yeniden kontrol
  edilir; daha önce görülmeyen ihlal veya infostealer bulgusu uyarı üretir. İlk
  kontrol yalnız taban çizgisidir (uyarı yazmaz).
- Hesap: son iki tamamlanmış taramanın anlık görüntüsünde yeni çıkan bulgular.

Uyarılar `(vaka, dedupe_key)` ile tekildir. Vakada webhook varsa SSRF korumalı
olarak bildirilir. Yalnız okuma/kontrol yapılır; havuza öğe yazılmaz.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from showis.db.models import Case, PoolItem, Scan, WatchAlert
from showis.services.audit import record_audit
from showis.services.breach import BreachError, check_email
from showis_engine.http import HttpClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.watch")

MAX_WATCHED_EMAILS = 10
BREACH_RECHECK_HOURS = 24
MAX_CASES_PER_TICK = 20


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _breach_names(report: dict[str, Any] | None) -> set[str]:
    return {
        str(b.get("name") or b.get("title") or "").strip().lower()
        for b in (report or {}).get("breaches") or []
        if str(b.get("name") or b.get("title") or "").strip()
    }


def _stealer_count(report: dict[str, Any] | None) -> int:
    return int(((report or {}).get("infostealer") or {}).get("count") or 0)


async def _add_alert(
    db: AsyncSession,
    *,
    case_id: str,
    kind: str,
    item_id: str | None,
    dedupe_key: str,
    title: str,
    detail: dict[str, Any],
) -> WatchAlert | None:
    exists = (
        await db.execute(
            select(WatchAlert.id).where(
                WatchAlert.case_id == case_id, WatchAlert.dedupe_key == dedupe_key
            )
        )
    ).scalar_one_or_none()
    if exists:
        return None
    alert = WatchAlert(
        case_id=case_id,
        kind=kind,
        item_id=item_id,
        dedupe_key=dedupe_key[:200],
        title=title[:300],
        detail=detail,
    )
    try:
        async with db.begin_nested():
            db.add(alert)
            await db.flush()
    except IntegrityError:  # eşzamanlı çalışma aynı uyarıyı yazmış
        return None
    return alert


async def check_breaches(db: AsyncSession, case: Case) -> list[WatchAlert]:
    items = list(
        (
            await db.execute(
                select(PoolItem)
                .where(
                    PoolItem.case_id == case.id,
                    PoolItem.kind == "email",
                    PoolItem.status != "rejected",
                )
                .order_by(PoolItem.created_at.asc())
                .limit(MAX_WATCHED_EMAILS)
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    alerts: list[WatchAlert] = []
    for item in items:
        previous = (item.attributes or {}).get("breach_check")
        checked_at = (previous or {}).get("checked_at")
        if checked_at:
            try:
                if now - _as_utc(datetime.fromisoformat(checked_at)) < timedelta(
                    hours=BREACH_RECHECK_HOURS
                ):
                    continue
            except ValueError:
                pass
        try:
            report = await check_email(db, item.value)
        except BreachError as exc:
            logger.info("İzleme sızıntı kontrolü başarısız: %s", str(exc)[:120])
            continue
        item.attributes = {**(item.attributes or {}), "breach_check": report}
        if previous is None:
            continue  # taban çizgisi
        known = _breach_names(previous)
        for breach in report.get("breaches") or []:
            name = str(breach.get("name") or breach.get("title") or "").strip()
            if not name or name.lower() in known:
                continue
            alert = await _add_alert(
                db,
                case_id=case.id,
                kind="breach",
                item_id=item.id,
                dedupe_key=f"breach:{item.id}:{name.lower()}",
                title=f"Yeni sızıntı: {name} ({item.value})",
                detail={
                    "email": item.value,
                    "breach": name,
                    "breach_date": breach.get("breach_date"),
                    "data_classes": list(breach.get("data_classes") or [])[:20],
                },
            )
            if alert:
                alerts.append(alert)
        if _stealer_count(report) > _stealer_count(previous):
            alert = await _add_alert(
                db,
                case_id=case.id,
                kind="breach",
                item_id=item.id,
                dedupe_key=f"stealer:{item.id}:{_stealer_count(report)}",
                title=f"Yeni infostealer bulgusu ({item.value})",
                detail={"email": item.value, "count": _stealer_count(report)},
            )
            if alert:
                alerts.append(alert)
    return alerts


async def check_new_accounts(db: AsyncSession, case: Case) -> list[WatchAlert]:
    scans = list(
        (
            await db.execute(
                select(Scan)
                .where(Scan.case_id == case.id, Scan.status == "completed")
                .order_by(Scan.created_at.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(scans) < 2:
        return []
    latest = (scans[0].stats or {}).get("snapshot") or {}
    older = (scans[1].stats or {}).get("snapshot") or {}
    seen = {f["id"] for f in older.get("findings", [])}
    alerts: list[WatchAlert] = []
    for finding in latest.get("findings", []):
        if finding["id"] in seen:
            continue
        platform = str(finding.get("platform") or "hesap")
        alert = await _add_alert(
            db,
            case_id=case.id,
            kind="account",
            item_id=None,
            dedupe_key=f"finding:{finding['id']}",
            title=f"Yeni hesap bulgusu: {platform} — {finding.get('label') or ''}".strip(" —"),
            detail={
                "scan_id": scans[0].id,
                "finding_id": finding["id"],
                "platform": platform,
                "status": finding.get("status"),
                "score": finding.get("score"),
            },
        )
        if alert:
            alerts.append(alert)
    return alerts


async def _notify_webhook(case: Case, alerts: list[WatchAlert]) -> None:
    if not case.webhook_url or not alerts:
        return
    try:
        client = HttpClient(enable_ssrf_guard=True, use_host_limiter=False)
        await client.post(
            case.webhook_url,
            json={
                "event": "watch.alert",
                "case_id": case.id,
                "alerts": [
                    {"kind": a.kind, "title": a.title, "detail": a.detail} for a in alerts[:20]
                ],
            },
            check_robots=False,
            max_retries=1,
        )
    except Exception as exc:  # noqa: BLE001 - bildirim izlemeyi bozmaz
        logger.warning("İzleme webhook'u gönderilemedi (%s): %s", case.id, type(exc).__name__)


async def run_watch(db: AsyncSession, case: Case) -> dict[str, Any]:
    """Tek vaka için izlemeyi çalıştırır; yeni uyarıları döner."""
    breach_alerts = await check_breaches(db, case)
    account_alerts = await check_new_accounts(db, case)
    alerts = [*breach_alerts, *account_alerts]
    await db.commit()
    if alerts:
        await record_audit(
            db=db,
            action="watch.alerts",
            case_id=case.id,
            target=f"Case: {case.id}",
            details={"breach": len(breach_alerts), "account": len(account_alerts)},
        )
        await db.commit()
        await _notify_webhook(case, alerts)
    return {"breach": len(breach_alerts), "account": len(account_alerts), "total": len(alerts)}


async def run_due_watches(db: AsyncSession, *, limit: int = MAX_CASES_PER_TICK) -> dict[str, int]:
    """İzleme açık aktif vakaların tümü için `run_watch` çalıştırır."""
    cases = (
        (
            await db.execute(
                select(Case)
                .where(Case.monitor_enabled.is_(True), Case.status == "active")
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    total = 0
    for case in cases:
        try:
            total += (await run_watch(db, case))["total"]
        except Exception:  # noqa: BLE001 - bir vaka diğerlerini durdurmaz
            logger.warning("İzleme başarısız: case=%s", case.id, exc_info=True)
            await db.rollback()
    return {"cases": len(cases), "alerts": total}

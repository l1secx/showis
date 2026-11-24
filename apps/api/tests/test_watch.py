"""Kayıtlı izleme: yeni sızıntı/hesap uyarıları, tekilleştirme, uçlar."""

from __future__ import annotations

import pytest
import test_pool_relations as _rel
import test_research_workspace as _workspace
from showis.db.models import Case as DbCase
from showis.db.models import Scan, WatchAlert
from showis.services import watch as watch_mod
from showis.services.breach import BreachError
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory
_seed_case = _workspace._seed_case
_item = _rel._item


def _report(
    *names: str,
    stealers: int = 0,
    checked_at: str = "2020-01-01T00:00:00+00:00",
    providers: list[str] | None = None,
):
    return {
        "providers": providers or [],
        "email": "a@example.com",
        "breaches": [
            {"name": n, "breach_date": "2024-01-01", "data_classes": ["Email"]} for n in names
        ],
        "infostealer": {"count": stealers} if stealers else None,
        "checked_at": checked_at,
    }


async def _case(db, case_id):
    return (await db.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()


@pytest.mark.asyncio
async def test_breach_watch_baseline_then_new_breach_alert(db_factory, monkeypatch):
    case_id = await _seed_case(db_factory)
    reports = iter([_report("Alfa"), _report("Alfa", "Beta"), _report("Alfa", "Beta", stealers=2)])

    async def fake_check(db, email):
        return next(reports)

    monkeypatch.setattr(watch_mod, "check_email", fake_check)
    async with db_factory() as db:
        await _item(db, case_id, "email", "a@example.com", source="user", status="confirmed")
        await _item(db, case_id, "email", "rej@example.com", status="rejected")
        await db.commit()
        case = await _case(db, case_id)

        # 1) İlk kontrol yalnız taban çizgisi: uyarı yok.
        assert (await watch_mod.run_watch(db, case))["total"] == 0
        # Süre dolmadan yeniden kontrol edilmez: eski checked_at ile süre dolmuş sayılır.
        # 2) Yeni ihlal "Beta" → bir uyarı; "Alfa" tekrar uyarı üretmez.
        second = await watch_mod.run_watch(db, case)
        assert second == {"breach": 1, "account": 0, "total": 1}
        # 3) Yeni infostealer bulgusu.
        third = await watch_mod.run_watch(db, case)
        assert third["breach"] == 1
        alerts = (
            (await db.execute(select(WatchAlert).order_by(WatchAlert.created_at))).scalars().all()
        )
        assert [a.kind for a in alerts] == ["breach", "breach"]
        assert "Beta" in alerts[0].title and "infostealer" in alerts[1].title


@pytest.mark.asyncio
async def test_breach_watch_respects_recheck_window_and_provider_failure(db_factory, monkeypatch):
    from datetime import datetime, timezone

    case_id = await _seed_case(db_factory)
    calls: list[str] = []

    async def fake_check(db, email):
        calls.append(email)
        raise BreachError("sağlayıcı yok")

    monkeypatch.setattr(watch_mod, "check_email", fake_check)
    async with db_factory() as db:
        fresh = await _item(db, case_id, "email", "fresh@example.com", source="user")
        fresh.attributes = {
            "breach_check": _report("Alfa", checked_at=datetime.now(timezone.utc).isoformat())
        }
        await _item(db, case_id, "email", "none@example.com", source="user")
        await db.commit()
        case = await _case(db, case_id)
        assert (await watch_mod.run_watch(db, case))["total"] == 0
    assert calls == ["none@example.com"]  # taze kayıt atlandı, hata uyarı üretmedi


async def _scan(db, case_id, finding_ids, created):
    scan = Scan(
        case_id=case_id,
        status="completed",
        mode="O2",
        budget={},
        stats={
            "snapshot": {
                "findings": [
                    {"id": f, "platform": "github", "label": f"u-{f}", "status": "x", "score": 0.5}
                    for f in finding_ids
                ]
            }
        },
        created_at=created,
    )
    db.add(scan)
    await db.flush()
    return scan


@pytest.mark.asyncio
async def test_account_watch_alerts_on_new_findings_once(db_factory):
    from datetime import datetime, timedelta, timezone

    case_id = await _seed_case(db_factory)
    now = datetime.now(timezone.utc)
    async with db_factory() as db:
        case = await _case(db, case_id)
        await _scan(db, case_id, ["f1"], now - timedelta(days=2))
        # Tek tarama: karşılaştırma yok.
        assert (await watch_mod.run_watch(db, case))["total"] == 0
        await _scan(db, case_id, ["f1", "f2"], now - timedelta(days=1))
        first = await watch_mod.run_watch(db, case)
        again = await watch_mod.run_watch(db, case)
        assert first == {"breach": 0, "account": 1, "total": 1}
        assert again["total"] == 0  # dedupe


@pytest.mark.asyncio
async def test_watch_endpoints(db_factory, client, monkeypatch):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        db.add(
            WatchAlert(case_id=case_id, kind="account", dedupe_key="k1", title="Yeni", detail={})
        )
        db.add(
            WatchAlert(case_id=case_id, kind="breach", dedupe_key="k2", title="Başka", detail={})
        )
        await db.commit()
    base = f"/api/v1/cases/{case_id}/watch"
    listing = (await client.get(f"{base}/alerts")).json()
    assert listing["total"] == 2
    first = listing["items"][0]["id"]
    seen = await client.post(f"{base}/alerts/{first}/seen")
    assert seen.status_code == 200 and seen.json()["seen_at"]
    assert (await client.get(f"{base}/alerts", params={"unseen": True})).json()["total"] == 1
    assert (await client.post(f"{base}/alerts/yok/seen")).status_code == 404
    assert (await client.post("/api/v1/cases/yok/watch/run")).status_code == 404
    ran = (await client.post(f"{base}/run")).json()
    assert ran == {"breach": 0, "account": 0, "total": 0}


@pytest.mark.asyncio
async def test_run_due_watches_only_enabled_active_cases(db_factory, monkeypatch):
    case_id = await _seed_case(db_factory)
    seen: list[str] = []

    async def fake_run(db, case):
        seen.append(case.id)
        return {"total": 3}

    monkeypatch.setattr(watch_mod, "run_watch", fake_run)
    async with db_factory() as db:
        assert await watch_mod.run_due_watches(db) == {"cases": 0, "alerts": 0}
        (await _case(db, case_id)).monitor_enabled = True
        await db.commit()
        assert await watch_mod.run_due_watches(db) == {"cases": 1, "alerts": 3}
    assert seen == [case_id]


@pytest.mark.asyncio
async def test_partial_provider_report_does_not_poison_baseline(db_factory, monkeypatch):
    case_id = await _seed_case(db_factory)
    reports = iter(
        [
            _report(providers=["hudsonrock"]),  # XON çöktü: eksik taban çizgisi
            _report("Eski1", "Eski2", providers=["xposedornot", "hudsonrock"]),  # XON döndü
            _report("Eski1", "Eski2", "Yeni", providers=["xposedornot", "hudsonrock"]),
            _report("Eski1", providers=["hudsonrock"]),  # bu sefer XON yok: atlanmalı
            _report("Eski1", "Eski2", "Yeni", providers=["xposedornot", "hudsonrock"]),
        ]
    )

    async def fake_check(db, email):
        return next(reports)

    monkeypatch.setattr(watch_mod, "check_email", fake_check)
    async with db_factory() as db:
        await _item(db, case_id, "email", "a@example.com", source="user", status="confirmed")
        await db.commit()
        case = await _case(db, case_id)
        assert (await watch_mod.run_watch(db, case))["total"] == 0  # taban çizgisi
        # Sağlayıcı geri geldi: tarihsel ihlaller sahte uyarı üretmez.
        assert (await watch_mod.run_watch(db, case))["total"] == 0
        # Gerçekten yeni ihlal uyarı üretir.
        assert (await watch_mod.run_watch(db, case))["breach"] == 1
        # Eksik rapor taban çizgisini ezmez ve uyarı üretmez...
        assert (await watch_mod.run_watch(db, case))["total"] == 0
        # ...bu yüzden tam rapor dönünce aynı ihlal ikinci kez uyarılmaz.
        assert (await watch_mod.run_watch(db, case))["total"] == 0
        alerts = (await db.execute(select(WatchAlert))).scalars().all()
        assert [a.detail["breach"] for a in alerts] == ["Yeni"]

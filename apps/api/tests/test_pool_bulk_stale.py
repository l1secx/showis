"""Havuz toplu durum ucu ve muhakeme bayat işareti (Beta-2 / Paket C).

Değişmezler:
* Toplu durum yalnız **kullanıcı** kimlikli çağrıyla çalışır; AI yolu bu ucu kullanmaz.
* Tek transaction: durum güncellemesi + audit aynı commit'te yazılır.
* Yalnız yol vakasının öğeleri güncellenir; başka vakanın kimlikleri `skipped` sayılır.
* Onay/ret/düzenleme sonrası muhakeme çıktıları bayat işaretlenir; korelasyon
  veya hipotez üretimi çalışınca işaret temizlenir.
"""

from __future__ import annotations

import uuid

import pytest
import test_research_workspace as _workspace
from showis.db.models import AuditLog, PoolItem
from showis.db.models import Case as DbCase
from showis.services.pool import create_item
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory
_seed_case = _workspace._seed_case

FORWARDED_IP = "203.0.113.9"


async def _ai_item(db, case_id: str, value: str) -> PoolItem:
    item, _ = await create_item(
        db,
        case_id=case_id,
        kind="organization",
        label=value,
        value=value,
        source="ai",
        status="proposed",
    )
    await db.flush()
    return item


async def _other_case(factory) -> str:
    async with factory() as session:
        session.add(
            DbCase(
                id="case-other",
                name="Öteki Vaka",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        await session.commit()
    return "case-other"


async def _stale_at(http_client, case_id: str) -> str | None:
    res = await http_client.get(f"/api/v1/cases/{case_id}/pool")
    assert res.status_code == 200, res.text
    return res.json()["reasoning_stale_at"]


@pytest.mark.asyncio
async def test_bulk_status_updates_only_own_case_and_audits(db_factory, client):
    case_id = await _seed_case(db_factory)
    other_id = await _other_case(db_factory)
    async with db_factory() as db:
        first = await _ai_item(db, case_id, "Alfa Lojistik")
        second = await _ai_item(db, case_id, "Beta Lojistik")
        foreign = await _ai_item(db, other_id, "Gama Lojistik")
        await db.commit()
        ids = [first.id, second.id, foreign.id, first.id]

    res = await client.post(
        f"/api/v1/cases/{case_id}/pool/bulk",
        json={"item_ids": ids, "status": "confirmed"},
        headers={"X-Forwarded-For": FORWARDED_IP},
    )
    assert res.status_code == 200, res.text
    # Tekrarlanan kimlik bir kez sayılır; başka vakanın öğesi skipped olur.
    assert res.json() == {"updated": 2, "skipped": 1}

    async with db_factory() as db:
        rows = {row.id: row for row in (await db.execute(select(PoolItem))).scalars()}
        audits = list(
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "pool.items.bulk_status")
                )
            )
            .scalars()
            .all()
        )
    assert rows[first.id].status == "confirmed"
    assert rows[second.id].status == "confirmed"
    assert rows[foreign.id].status == "proposed"
    entry = audits[-1]
    assert entry.details["status"] == "confirmed"
    assert entry.details["updated"] == 2
    assert entry.details["skipped"] == 1
    assert entry.details["actor_ip"] == FORWARDED_IP
    assert entry.case_id == case_id

    # Toplu karar muhakeme çıktılarını bayat işaretler.
    assert await _stale_at(client, case_id) is not None


@pytest.mark.asyncio
async def test_bulk_status_validation(db_factory, client):
    case_id = await _seed_case(db_factory)
    base = f"/api/v1/cases/{case_id}/pool/bulk"
    assert (
        await client.post(base, json={"item_ids": [], "status": "confirmed"})
    ).status_code == 422
    too_many = [str(uuid.uuid4()) for _ in range(201)]
    assert (
        await client.post(base, json={"item_ids": too_many, "status": "confirmed"})
    ).status_code == 422
    assert (
        await client.post(
            base, json={"item_ids": [str(uuid.uuid4())], "status": "proposed"}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/cases/yok-boyle-vaka/pool/bulk",
            json={"item_ids": [str(uuid.uuid4())], "status": "rejected"},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_bulk_status_requires_user_identity(db_factory, client, monkeypatch):
    import showis.db.session as db_session_module

    case_id = await _seed_case(db_factory)
    monkeypatch.setattr(db_session_module, "async_session_factory", db_factory)
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SHOWIS_API_TOKEN", "dogru-token")
    body = {"item_ids": [str(uuid.uuid4())], "status": "confirmed"}
    anonymous = await client.post(f"/api/v1/cases/{case_id}/pool/bulk", json=body)
    assert anonymous.status_code == 401
    authorized = await client.post(
        f"/api/v1/cases/{case_id}/pool/bulk",
        json=body,
        headers={"X-API-Token": "dogru-token"},
    )
    assert authorized.status_code == 200, authorized.text


@pytest.mark.asyncio
async def test_reasoning_stale_marked_and_cleared(db_factory, client):
    case_id = await _seed_case(db_factory)
    assert await _stale_at(client, case_id) is None

    async with db_factory() as db:
        item = await _ai_item(db, case_id, "Delta Lojistik")
        await db.commit()
        item_id = item.id

    # Kullanıcı onayı muhakemeyi bayatlatır.
    approved = await client.patch(
        f"/api/v1/cases/{case_id}/pool/{item_id}", json={"status": "confirmed"}
    )
    assert approved.status_code == 200, approved.text
    assert await _stale_at(client, case_id) is not None

    correlated = await client.post(f"/api/v1/cases/{case_id}/reasoning/correlate")
    assert correlated.status_code == 200, correlated.text
    assert await _stale_at(client, case_id) is None

    # Düzenleme de bayatlatır; hipotez üretimi temizler.
    edited = await client.patch(
        f"/api/v1/cases/{case_id}/pool/{item_id}", json={"label": "Delta Holding"}
    )
    assert edited.status_code == 200, edited.text
    assert await _stale_at(client, case_id) is not None

    generated = await client.post(f"/api/v1/cases/{case_id}/reasoning/hypotheses/generate")
    assert generated.status_code == 200, generated.text
    assert await _stale_at(client, case_id) is None

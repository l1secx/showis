"""T-327: bulgu API'sinde authenticity alanları + liste filtreleri."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Entity as DbEntity
from showis.db.models import Finding as DbFinding
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.main import app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

CASE_ID = "case-t327"


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.fixture
async def client(db_factory):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed(db_factory: async_sessionmaker[AsyncSession]) -> None:
    async with db_factory() as session:
        session.add(
            DbCase(
                id=CASE_ID,
                name="Authenticity",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(DbTarget(case_id=CASE_ID, first_name="Ada", last_name="Lovelace", hints={}))
        session.add(DbScan(id="scan-t327", case_id=CASE_ID, mode="O2", status="completed"))
        rows = [
            # (platform, status, score, authenticity, reasons)
            ("github", "confirmed", 0.92, 0.85, ["hesap 9 yaşında", "120 takipçi"]),
            ("gitlab", "candidate", 0.71, 0.55, ["bio var"]),
            ("mastodon", "lead", 0.44, 0.10, ["hesap 2 gün önce açılmış", "0 gönderi"]),
            ("web", "candidate", 0.30, None, None),
        ]
        for platform, status, score, authenticity, reasons in rows:
            entity_id = f"{platform}:ada"
            session.add(
                DbEntity(
                    id=entity_id,
                    case_id=CASE_ID,
                    type="account",
                    platform=platform,
                    label="ada",
                    attributes={"username": "ada"},
                )
            )
            session.add(
                DbFinding(
                    id=f"finding:{entity_id}",
                    case_id=CASE_ID,
                    scan_id="scan-t327",
                    entity_id=entity_id,
                    platform=platform,
                    label=platform,
                    status=status,
                    score=score,
                    breakdown={"name": score},
                    authenticity=authenticity,
                    authenticity_reasons=reasons,
                )
            )
        await session.commit()


async def test_authenticity_alanlari_dolu_ve_null_serilesir(client, db_factory):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases/{CASE_ID}/findings")
    assert res.status_code == 200, res.text
    body = {row["platform"]: row for row in res.json()}

    assert body["github"]["authenticity"] == 0.85
    assert body["github"]["authenticity_reasons"] == ["hesap 9 yaşında", "120 takipçi"]
    # NULL alanlar korunur (eski istemciler için ek alan)
    assert body["web"]["authenticity"] is None
    assert body["web"]["authenticity_reasons"] is None
    # Eski alanlar değişmedi
    assert body["github"]["status"] == "confirmed"
    assert body["github"]["score"] == 0.92
    assert "breakdown" in body["github"]


async def test_min_authenticity_filtresi(client, db_factory):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases/{CASE_ID}/findings?min_authenticity=0.5")
    assert res.status_code == 200, res.text
    platforms = [row["platform"] for row in res.json()]
    assert platforms == ["github", "gitlab"]  # skora göre azalan
    assert all(row["authenticity"] >= 0.5 for row in res.json())

    # authenticity NULL olan bulgu filtreye takılmaz
    res = await client.get(f"/api/v1/cases/{CASE_ID}/findings?min_authenticity=0")
    platforms = [row["platform"] for row in res.json()]
    assert platforms == ["github", "gitlab", "mastodon"]
    assert "web" not in platforms


async def test_status_coklu_filtre(client, db_factory):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases/{CASE_ID}/findings?status=candidate,confirmed")
    assert res.status_code == 200, res.text
    assert [row["platform"] for row in res.json()] == ["github", "gitlab", "web"]

    res = await client.get(f"/api/v1/cases/{CASE_ID}/findings?status=lead")
    assert [row["platform"] for row in res.json()] == ["mastodon"]


async def test_status_ve_authenticity_birlikte(client, db_factory):
    await _seed(db_factory)
    res = await client.get(
        f"/api/v1/cases/{CASE_ID}/findings?status=candidate&min_authenticity=0.5"
    )
    assert res.status_code == 200, res.text
    assert [row["platform"] for row in res.json()] == ["gitlab"]


@pytest.mark.parametrize(
    "query",
    [
        "min_authenticity=1.5",
        "min_authenticity=-0.1",
        "min_authenticity=nan",
        "min_authenticity=abc",
        "status=bogus",
        "status=candidate,bogus",
        "status=,",
    ],
)
async def test_gecersiz_filtre_422(client, db_factory, query):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases/{CASE_ID}/findings?{query}")
    assert res.status_code == 422, res.text


async def test_export_authenticity_alanlarini_icerir(client, db_factory):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases/{CASE_ID}/export")
    assert res.status_code == 200, res.text
    payload = res.json()
    findings = {row["platform"]: row for row in payload["findings"]}

    assert findings["github"]["authenticity"] == 0.85
    assert findings["github"]["authenticity_reasons"] == [
        "hesap 9 yaşında",
        "120 takipçi",
    ]
    assert findings["web"]["authenticity"] is None
    # Export eskiden beri var olan alanları korur
    for key in ("id", "entity_id", "platform", "status", "score", "breakdown"):
        assert key in findings["github"]

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.session import get_db
from showis.main import app
from showis.services.scanner import ScannerService
from showis_contracts import (
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class MockConnector:
    name: str = "mock_conn"
    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A
    supported_kinds: set[str] = {"mock_kind"}

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        return [
            TaskSpec(
                id="t-mock-1",
                connector=self.name,
                platform=self.platform,
                kind="mock_kind",
                params={"username": "mockuser"},
            ),
            TaskSpec(
                id="t-mock-2",
                connector=self.name,
                platform=self.platform,
                kind="mock_kind",
                params={"username": "mockuser2"},
            ),
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        return [
            Observation(
                id=f"obs-{task.id}",
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.PROFILE,
                subject=task.params.get("username", ""),
                tier=self.tier,
                data={"login": task.params.get("username"), "name": "Mock User"},
            )
        ]

    def normalize(self, obs: Observation):
        from showis_contracts import Entity, EntityType

        ent = Entity(
            id=f"mock:{obs.subject}",
            type=EntityType.ACCOUNT,
            platform=Platform.GITHUB,
            label=obs.subject,
            attributes={"name": "Mock User"},
        )
        return [ent], []


@pytest.fixture
async def test_api_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import showis.db.session as session_module

    orig_factory = session_module.async_session_factory
    session_module.async_session_factory = session_factory

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield session_factory
    app.dependency_overrides.clear()
    session_module.async_session_factory = orig_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_case_crud_and_scan_lifecycle(test_api_db, monkeypatch):
    # Arka plan taramasi gercek connector + ag cagrisi yapar; testte devre disi.
    async def _noop_scan(scan_id: str) -> None:
        return None

    monkeypatch.setattr("showis.api.v1.cases.run_scan_in_background", _noop_scan)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a case
        create_res = await client.post(
            "/api/v1/cases",
            json={
                "name": "Investigation Alpha",
                "purpose": "Self-check",
                "legal_basis": "self_check",
                "target": {
                    "first_name": "Test",
                    "last_name": "Person",
                    "hints": {"city": "Istanbul"},
                },
                "seeds": [{"platform": "github", "username": "testperson"}],
            },
        )
        assert create_res.status_code == 201
        case_id = create_res.json()["id"]

        # 2. Get case details
        get_res = await client.get(f"/api/v1/cases/{case_id}")
        assert get_res.status_code == 200
        case_data = get_res.json()
        assert case_data["target"]["first_name"] == "Test"
        assert len(case_data["seeds"]) == 1

        # 3. Start scan
        scan_res = await client.post(
            f"/api/v1/cases/{case_id}/scan",
            json={
                "mode": "O2",
                "budget": {"max_tasks": 1},
            },  # limit max_tasks to 1 to test budget enforcement
        )
        assert scan_res.status_code == 200
        scan_id = scan_res.json()["scan_id"]

        # 4. Run ScannerService directly with MockConnector
        async with test_api_db() as session:
            scanner = ScannerService(connectors=[MockConnector()])
            await scanner.execute_scan(scan_id, session)

        # 5. Check scan status
        status_res = await client.get(f"/api/v1/scans/{scan_id}")
        assert status_res.status_code == 200
        scan_status = status_res.json()
        assert scan_status["status"] == "completed"
        # Since max_tasks was 1, only 1 task executed
        assert scan_status["stats"]["executed_tasks"] == 1

        # 6. Check findings
        findings_res = await client.get(f"/api/v1/cases/{case_id}/findings")
        assert findings_res.status_code == 200
        findings = findings_res.json()
        assert len(findings) == 1
        assert findings[0]["label"] == "mockuser"

        # 7. Cancel endpoint check
        cancel_res = await client.post(f"/api/v1/scans/{scan_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"

        # 8. Delete case cascade
        del_res = await client.delete(f"/api/v1/cases/{case_id}")
        assert del_res.status_code == 204


@pytest.mark.asyncio
async def test_artimli_finding_yazimi_entity_satirini_olusturur(test_api_db):
    """Artimli skorlama, entity DB'ye yazilmadan finding yazabilmeli (FK regresyonu)."""
    from showis.db.models import Case as DbCase
    from showis.db.models import Scan as DbScan
    from showis_contracts import Entity, EntityType, Target

    async with test_api_db() as session:
        case = DbCase(
            id="case-incremental",
            name="Incremental",
            purpose="test",
            legal_basis="self_check",
            retention_days=7,
        )
        scan = DbScan(id="scan-incremental", case_id=case.id, mode="O2", status="running")
        session.add_all([case, scan])
        await session.commit()

        entity = Entity(
            id="mock:fresh-user",
            type=EntityType.ACCOUNT,
            platform=Platform.GITHUB,
            label="fresh-user",
            attributes={"username": "fresh-user", "name": "Test Person"},
        )
        scanner = ScannerService(connectors=[])
        await scanner._write_findings(
            session,
            scan,
            case,
            Target(first_name="Test", last_name="Person"),
            [entity],
            set(),
            notify=False,
            explain=False,
        )

    async with test_api_db() as session:
        from showis.db.models import Finding as DbFinding
        from sqlalchemy import select

        rows = (await session.execute(select(DbFinding))).scalars().all()
        assert [r.id for r in rows] == ["finding:mock:fresh-user"]

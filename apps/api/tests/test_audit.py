import pytest
from showis.db.base import Base
from showis.services.audit import list_audit_logs, record_audit
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_and_query_audit_logs(db_session: AsyncSession):
    # 1. Record an audit event
    log1 = await record_audit(
        db=db_session,
        action="case.create",
        case_id="case-100",
        actor="local-user",
        target="Case: Ahmet Yilmaz",
        details={"legal_basis": "consent", "purpose": "self_check"},
    )
    assert log1.id is not None
    assert log1.action == "case.create"
    assert log1.details["legal_basis"] == "consent"

    # 2. Record second audit event for another case
    await record_audit(
        db=db_session,
        action="scan.start",
        case_id="case-200",
        actor="system",
        target="Scan: scan-1",
        details={"mode": "O2"},
    )

    # 3. Query all logs
    all_logs = await list_audit_logs(db=db_session)
    assert len(all_logs) == 2

    # 4. Query logs filtered by case_id
    case_100_logs = await list_audit_logs(db=db_session, case_id="case-100")
    assert len(case_100_logs) == 1
    assert case_100_logs[0].action == "case.create"
    assert case_100_logs[0].target == "Case: Ahmet Yilmaz"

"""P-005/DEC-010: entity/finding/edge kimlikleri case kapsamında izole."""

from __future__ import annotations

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Edge as DbEdge
from showis.db.models import Entity as DbEntity
from showis.db.models import Finding as DbFinding
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.services.scanner import (
    ScannerService,
    scoped_edge,
    scoped_edge_id,
    scoped_entity_id,
    scoped_finding_id,
)
from showis_contracts import Edge as EdgeContract
from showis_contracts import EdgeType, Entity, EntityType, Platform, Target
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_scoping_helpers_are_deterministic_and_bounded():
    first = scoped_entity_id("case-a", "github:ada")
    second = scoped_entity_id("case-b", "github:ada")
    assert first != second
    assert first == scoped_entity_id("case-a", "github:ada")
    assert len(first) <= 96
    # Bulgu kimliği entity kimliğinden türer; farklı case'lerin entity'leri farklıdır.
    assert scoped_finding_id("case-a", first) != scoped_finding_id("case-b", second)
    assert "~c" in first and "#" not in first
    assert len(scoped_edge_id("case-a", "edge:cross:1")) <= 96
    assert scoped_edge_id("case-a", "edge:cross:1").startswith("ec:")
    edge = EdgeContract(
        id="edge:cross:1",
        type=EdgeType.SAME_PERSON,
        source_entity_id="github:ada",
        target_entity_id="gitlab:ada",
    )
    scoped = scoped_edge("case-a", edge)
    assert scoped.id.startswith("ec:")
    assert scoped.source_entity_id == scoped_entity_id("case-a", "github:ada")
    assert scoped.target_entity_id == scoped_entity_id("case-a", "gitlab:ada")


@pytest.mark.asyncio
async def test_same_entity_in_two_cases_writes_isolated_rows(db_factory):
    async with db_factory() as db:
        for case_id, scan_id in (("case-a", "scan-a"), ("case-b", "scan-b")):
            db.add(
                DbCase(
                    id=case_id,
                    name="Case",
                    purpose="test",
                    legal_basis="self_check",
                    retention_days=7,
                )
            )
            db.add(
                DbTarget(
                    case_id=case_id,
                    first_name="Ada",
                    last_name="Lovelace",
                    hints={},
                    review_required=False,
                )
            )
            db.add(DbScan(id=scan_id, case_id=case_id, mode="O2", status="running"))
        await db.commit()

        service = ScannerService(connectors=[])
        for case_id, scan_id in (("case-a", "scan-a"), ("case-b", "scan-b")):
            case = (await db.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
            scan = (await db.execute(select(DbScan).where(DbScan.id == scan_id))).scalar_one()
            entity = Entity(
                id="github:ada",
                type=EntityType.ACCOUNT,
                platform=Platform.GITHUB,
                label="ada",
                attributes={"username": "ada", "name": "Ada Lovelace"},
            )
            await service._write_findings(
                db,
                scan,
                case,
                Target(first_name="Ada", last_name="Lovelace"),
                [entity],
                set(),
                notify=False,
                explain=False,
            )

    async with db_factory() as db:
        entities = (await db.execute(select(DbEntity))).scalars().all()
        findings = (await db.execute(select(DbFinding))).scalars().all()
    assert len(entities) == 2
    assert {row.case_id for row in entities} == {"case-a", "case-b"}
    assert len({row.id for row in entities}) == 2
    assert len(findings) == 2
    assert {row.case_id for row in findings} == {"case-a", "case-b"}
    assert len({row.id for row in findings}) == 2
    for finding in findings:
        assert finding.entity_id in {row.id for row in entities}
        assert finding.id == scoped_finding_id(finding.case_id, finding.entity_id)
    # Kenar tablosu bu senaryoda boş kalır; sözleşme testi yukarıda.
    assert (await db.execute(select(DbEdge))).scalars().all() == []


def test_foreign_case_suffix_is_not_trusted():
    """T17-01: başka case'in ~c soneki taşınamaz; kimlik yeniden üretilir."""
    raw = "web:https://ornek.example/~cdeadbeef"
    case_a = scoped_entity_id("case-a", raw)
    case_b = scoped_entity_id("case-b", raw)
    assert case_a != case_b
    assert "deadbeef" not in case_a
    assert case_a == scoped_entity_id("case-a", "web:https://ornek.example/")
    # Aynı vaka için yeniden kapsamlama idempotent.
    assert scoped_entity_id("case-a", case_a) == case_a


@pytest.mark.asyncio
async def test_persist_entities_recomputes_foreign_scope(db_factory):
    async with db_factory() as db:
        case = DbCase(
            id="case-own",
            name="Own",
            purpose="test",
            legal_basis="self_check",
            retention_days=7,
        )
        db.add(case)
        await db.commit()
        foreign = scoped_entity_id("case-foreign", "web:https://ornek.example/x")
        entity = Entity(
            id=foreign,
            type=EntityType.ACCOUNT,
            platform=Platform.WEB,
            label="x",
            attributes={},
        )
        service = ScannerService(connectors=[])
        await service._persist_entities(db, case, [entity])

    async with db_factory() as db:
        rows = (await db.execute(select(DbEntity))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == scoped_entity_id("case-own", "web:https://ornek.example/x")
    assert rows[0].id != foreign
    assert rows[0].case_id == "case-own"

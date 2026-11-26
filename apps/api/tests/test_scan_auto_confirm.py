"""DEC-002: otomatik tarama `confirmed` yazamaz; skor eşiği `candidate`'a iner."""

from __future__ import annotations

from typing import Any

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Finding as DbFinding
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.services.scanner import ScannerService, clamp_machine_finding_status
from showis_contracts import (
    ConfidenceBreakdown,
    Entity,
    EntityType,
    Finding,
    FindingStatus,
    Platform,
    Target,
)
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


def test_clamp_helper():
    assert clamp_machine_finding_status(FindingStatus.CONFIRMED) == FindingStatus.CANDIDATE
    assert clamp_machine_finding_status(FindingStatus.CANDIDATE) == FindingStatus.CANDIDATE
    assert clamp_machine_finding_status(FindingStatus.LEAD) == FindingStatus.LEAD
    assert clamp_machine_finding_status(FindingStatus.REJECTED) == FindingStatus.REJECTED


@pytest.mark.asyncio
async def test_write_findings_never_writes_confirmed(db_factory):
    entity = Entity(
        id="github:kerem",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="kerem",
        attributes={"username": "kerem"},
    )

    class _Resolver:
        def resolve_entity(self, ent: Entity, target: Target, **_: Any) -> Finding:
            return Finding(
                id="finding:github:kerem",
                case_id="case-auto",
                scan_id="scan-auto",
                entity_id=ent.id,
                platform=ent.platform,
                url="https://github.com/kerem",
                label="kerem",
                status=FindingStatus.CONFIRMED,
                score=0.91,
                breakdown=ConfidenceBreakdown(total=0.91),
                explanation="yüksek skor",
                evidence_ids=[],
            )

    async with db_factory() as db:
        db.add(
            DbCase(
                id="case-auto",
                name="Auto Confirm",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        db.add(
            DbTarget(
                case_id="case-auto",
                first_name="Kerem",
                last_name="Boz",
                hints={},
                review_required=False,
            )
        )
        db.add(DbScan(id="scan-auto", case_id="case-auto", mode="O2", status="running"))
        await db.commit()
        case = (await db.execute(select(DbCase).where(DbCase.id == "case-auto"))).scalar_one()
        scan = (await db.execute(select(DbScan).where(DbScan.id == "scan-auto"))).scalar_one()

        service = ScannerService(connectors=[])
        service.resolver = _Resolver()  # type: ignore[assignment]
        await service._write_findings(
            db, scan, case, Target(first_name="Kerem", last_name="Boz"), [entity], set()
        )

    async with db_factory() as db:
        row = (await db.execute(select(DbFinding))).scalar_one()
    assert row.status == "candidate"
    assert row.verified_at is None

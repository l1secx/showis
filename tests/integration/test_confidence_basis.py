"""E2 — `Finding.confidence_basis` kaliciligi (D8 / 0008 migration).

Senaryo: sahte bir connector iki entity uretir; biri bio'sunda digerinin
profilini linkler -> resolver `confidence_basis="cross_link"` hesaplar. Bu deger
DB'ye yazilmali ve `GET /cases/{id}/findings` yanitinda donmeli ("neden
CONFIRMED" sorusunun kalici cevabi).

Ag yok; sqlite + sahte connector.
"""

from __future__ import annotations

import uuid

from showis.db.models import Case as DbCase
from showis.db.models import Finding as DbFinding
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.services.scanner import ScannerService
from showis_contracts import (
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    TaskSpec,
)
from sqlalchemy import select


class _EntityConnector:
    """Sabit entity listesi donduren sahte connector (ag yok)."""

    name = "fixedentities"
    platform = Platform.GITHUB
    tier = RiskTier.A
    supported_kinds = {"profile"}

    def __init__(self, entities: list[Entity]) -> None:
        self._entities = entities

    async def plan(self, ctx):
        return [
            TaskSpec(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind="profile",
                params={},
            )
        ]

    async def run(self, task, ctx):
        return [
            Observation(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=entity.platform,
                kind=ObservationKind.PROFILE,
                subject=entity.label,
                tier=self.tier,
                data={},
            )
            for entity in self._entities
        ]

    def normalize(self, obs):
        return list(self._entities), []


async def _scan(db_factory, entities: list[Entity]) -> tuple[str, str]:
    case_id = str(uuid.uuid4())
    scan_id = str(uuid.uuid4())
    async with db_factory() as session:
        session.add(
            DbCase(
                id=case_id,
                name="Basis Test",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(
            DbTarget(
                case_id=case_id,
                first_name="Kerem",
                last_name="Boz",
                hints={},
                review_required=False,
            )
        )
        session.add(
            DbScan(id=scan_id, case_id=case_id, mode="O2", status="running", budget={})
        )
        await session.commit()

    async with db_factory() as session:
        await ScannerService(connectors=[_EntityConnector(entities)]).execute_scan(
            scan_id, session
        )
    return case_id, scan_id


def _entities_with_cross_link() -> list[Entity]:
    """GitHub entity bio'sunda GitLab profilini linkler -> cross_link sinyali."""
    return [
        Entity(
            id="github:kboz",
            type=EntityType.ACCOUNT,
            platform=Platform.GITHUB,
            label="kboz",
            url="https://github.com/kboz",
            attributes={
                "username": "kboz",
                "name": "Kerem Boz",
                "links": ["https://gitlab.com/kboz"],
            },
        ),
        Entity(
            id="gitlab:kboz",
            type=EntityType.ACCOUNT,
            platform=Platform.GITLAB,
            label="kboz",
            url="https://gitlab.com/kboz",
            attributes={"username": "kboz", "name": "Kerem Boz"},
        ),
    ]


async def test_confidence_basis_db_ve_api_roundtrip(db_factory, client):
    case_id, _ = await _scan(db_factory, _entities_with_cross_link())

    async with db_factory() as session:
        rows = (
            (await session.execute(select(DbFinding).where(DbFinding.case_id == case_id)))
            .scalars()
            .all()
        )
    assert rows, "bulgu yazilmali"
    github_row = next(row for row in rows if row.url and "github.com" in row.url)
    assert github_row.confidence_basis == "cross_link"
    # kolon NOT NULL: hicbir satir None olamaz
    assert all(isinstance(row.confidence_basis, str) for row in rows)

    res = await client.get(f"/api/v1/cases/{case_id}/findings")
    assert res.status_code == 200, res.text
    payload = {item["id"]: item for item in res.json()}
    assert payload[github_row.id]["confidence_basis"] == "cross_link"


async def test_confidence_basis_bagimsiz_sinyal_yoksa_weak(db_factory, client):
    entity = Entity(
        id="github:yalniz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="yalniz",
        url="https://github.com/yalniz",
        attributes={"username": "yalniz", "name": "Yalniz Kisi"},
    )
    case_id, _ = await _scan(db_factory, [entity])

    async with db_factory() as session:
        row = (
            await session.execute(select(DbFinding).where(DbFinding.case_id == case_id))
        ).scalars().one()
    assert row.confidence_basis == "weak"

    res = await client.get(f"/api/v1/cases/{case_id}/findings")
    assert res.status_code == 200, res.text
    assert res.json()[0]["confidence_basis"] == "weak"


async def test_confidence_basis_export_yanitinda(db_factory, client):
    case_id, _ = await _scan(db_factory, _entities_with_cross_link())

    res = await client.get(f"/api/v1/cases/{case_id}/export")
    assert res.status_code == 200, res.text
    findings = res.json()["findings"]
    assert findings
    assert any(item.get("confidence_basis") == "cross_link" for item in findings)


# ---------------------------------------------------------------------------
# Migration (statik: hicbir veritabanina UYGULANMAZ)
# ---------------------------------------------------------------------------


def test_migration_0008_zinciri_ve_kolonu():
    """Migration dosyasi mevcut, 0007'den turer ve kolonu ekler/dusurur."""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "apps/api/alembic/versions"
    path = versions / "0008_finding_confidence_basis.py"
    assert path.exists(), f"migration yok: {path}"

    source = path.read_text(encoding="utf-8")
    assert 'revision: str = "0008_finding_confidence_basis"' in source
    assert 'down_revision: Union[str, None] = "0007_target_identity"' in source
    assert 'op.add_column(\n        "findings"' in source
    assert '"confidence_basis"' in source
    assert 'op.drop_column("findings", "confidence_basis")' in source
    # veri gocu/backfill yok: varsayilan bos
    assert 'server_default=""' in source


def test_tek_alembic_head_var():
    """Zincir tek uca inmeli (0008 head)."""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "apps/api/alembic/versions"
    revisions: dict[str, str | None] = {}
    for file in versions.glob("0*.py"):
        text = file.read_text(encoding="utf-8")
        rev = None
        down = None
        for line in text.splitlines():
            if line.startswith("revision: str = "):
                rev = line.split('"')[1]
            elif line.startswith("down_revision: Union[str, None] = "):
                rest = line.split("= ", 1)[1]
                down = None if rest.startswith("None") else rest.split('"')[1]
        if rev:
            revisions[rev] = down
    heads = set(revisions) - {d for d in revisions.values() if d}
    assert heads == {"0008_finding_confidence_basis"}, heads

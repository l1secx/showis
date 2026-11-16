"""T-346: kimlik id'lerinin DB sınırını (VARCHAR 96) aşmaması.

Kanıt (Deniz T-321 A/B): 3/3 O2 koşusu `unexpected_failure`; hata
`rendered.py:377` → `hackthebox:<uuid>` 46 karakter (eski sınır 36).
Migration 0005 ile kolonlar 96'ya çıktı; bu testler **sınır aşımı sınıfını**
tümüyle kapatır: entity id, türev finding id ve edge id'leri ≤96.
"""

from __future__ import annotations

import asyncio

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Edge as DbEdge
from showis.db.models import Entity as DbEntity
from showis.db.models import Finding as DbFinding
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.services.scanner import ScannerService
from showis_connectors import RenderedProfileConnector
from showis_contracts import (
    FINDING_ID_PREFIX,
    IDENTITY_ID_MAX_LENGTH,
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
    make_entity_id,
    make_finding_id,
)
from showis_engine.identity_edges import EDGE_ID_MAX_LENGTH, db_safe_edge_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

#: T-321 kanıtındaki gerçek biçim: HackTheBox kullanıcı kimliği bir UUID
HTB_UUID = "9f8d4c2e-1a3b-4c5d-8e7f-00112233aabb"
LONG_IDENTIFIER = "u" * 200


# ---------------------------------------------------------------------------
# (a) 46 karakterlik HTB kimliği: sınırın altında → DEĞİŞMEZ
# ---------------------------------------------------------------------------


def test_htb_uuid_id_sinirin_altinda_kalir_ve_degismez():
    entity_id = make_entity_id(Platform.HACKTHEBOX.value, HTB_UUID)

    assert entity_id == f"hackthebox:{HTB_UUID}"
    # T-321 kanıtı: bu uzunluk (46-47) eski 36'lık sınırı aşıyordu
    assert len(entity_id) == len("hackthebox:") + len(HTB_UUID) > 36
    assert len(entity_id) <= IDENTITY_ID_MAX_LENGTH
    # Deterministik (yeniden taramada aynı id → SEC-15 karar koruma)
    assert make_entity_id(Platform.HACKTHEBOX.value, HTB_UUID) == entity_id


# ---------------------------------------------------------------------------
# (b) Aşırı uzun identifier: hash'lenir
# ---------------------------------------------------------------------------


def test_uzun_identifier_hashlenir_ve_deterministik():
    first = make_entity_id("github", LONG_IDENTIFIER)
    second = make_entity_id("github", LONG_IDENTIFIER)

    assert len(first) <= IDENTITY_ID_MAX_LENGTH
    assert first == second
    assert first.startswith("github:")
    assert LONG_IDENTIFIER not in first
    # Farklı girdi → farklı id
    assert first != make_entity_id("github", LONG_IDENTIFIER + "x")


def test_sinirdaki_uzunluklar_tam_sinirda_kalir():
    prefix = "hackthebox:"
    just_fits = "a" * (IDENTITY_ID_MAX_LENGTH - len(prefix))
    entity_id = make_entity_id("hackthebox", just_fits)
    assert len(entity_id) == IDENTITY_ID_MAX_LENGTH
    assert entity_id == f"hackthebox:{just_fits}"

    one_over = "a" * (IDENTITY_ID_MAX_LENGTH - len(prefix) + 1)
    assert len(make_entity_id("hackthebox", one_over)) < IDENTITY_ID_MAX_LENGTH


# ---------------------------------------------------------------------------
# (c) Rendered connector + uçtan uca tarama: taşma yok
# ---------------------------------------------------------------------------


class _FakeBrowser:  # normalize() için gerekli değil, yalnız constructor
    pass


def _rendered(platform: Platform) -> RenderedProfileConnector:
    connector = RenderedProfileConnector()
    connector.platform = platform
    return connector


def test_rendered_connector_uzun_kimlikle_sinir_asmiyor():
    connector = _rendered(Platform.HACKTHEBOX)
    obs = Observation(
        id="obs-long",
        connector="rendered",
        platform=Platform.HACKTHEBOX,
        kind=ObservationKind.PROFILE,
        subject=HTB_UUID,
        tier=RiskTier.B,
        data={"username": LONG_IDENTIFIER, "display_name": "Test"},
    )

    entities, _edges = connector.normalize(obs)
    assert entities, "normalize entity üretmeli"
    entity = entities[0]
    assert len(entity.id) <= IDENTITY_ID_MAX_LENGTH
    # Kısa (UUID) kimlik korunur: 46 karakterlik id değişmez
    obs_short = Observation(
        id="obs-short",
        connector="rendered",
        platform=Platform.HACKTHEBOX,
        kind=ObservationKind.PROFILE,
        subject=HTB_UUID,
        tier=RiskTier.B,
        data={"username": HTB_UUID, "display_name": "Test"},
    )
    short_entities, _ = connector.normalize(obs_short)
    assert short_entities[0].id == f"hackthebox:{HTB_UUID}"


class LongIdConnector:
    """Uzun kimlik üreten connector (HTB render yolunu taklit eder)."""

    name = "longid"
    platform = Platform.HACKTHEBOX
    tier = RiskTier.A

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        return [
            TaskSpec(
                id="long-1",
                connector=self.name,
                platform=self.platform,
                kind="probe",
                params={"username": LONG_IDENTIFIER},
            )
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        return [
            Observation(
                id="obs-long-1",
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.PROFILE,
                subject=str(task.params["username"]),
                tier=self.tier,
                data={"login": str(task.params["username"]), "display_name": "Ada"},
            )
        ]

    def normalize(self, obs: Observation) -> tuple[list[Entity], list]:
        identifier = str(obs.data.get("username") or obs.subject)
        return (
            [
                Entity(
                    id=make_entity_id(self.platform.value, identifier),
                    type=EntityType.ACCOUNT,
                    platform=self.platform,
                    label="ada",
                    attributes={"username": identifier},
                )
            ],
            [],
        )


@pytest.mark.asyncio
async def test_uzun_kimlikle_tarama_tamamlanir_ve_tum_idler_sinirda():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                DbCase(
                    id="case-t346",
                    name="Uzun ID",
                    purpose="test",
                    legal_basis="self_check",
                    retention_days=7,
                )
            )
            session.add(DbTarget(case_id="case-t346", first_name="Ada", last_name="Lovelace"))
            session.add(DbScan(id="scan-t346", case_id="case-t346", mode="O1", status="queued"))
            await session.commit()

        async with factory() as session:
            scanner = ScannerService(connectors=[LongIdConnector()])
            await asyncio.wait_for(
                scanner.execute_scan("scan-t346", session), timeout=60.0
            )

        async with factory() as session:
            scan = (
                await session.execute(select(DbScan).where(DbScan.id == "scan-t346"))
            ).scalar_one()
            entities = list(
                (await session.execute(select(DbEntity))).scalars().all()
            )
            findings = list(
                (await session.execute(select(DbFinding))).scalars().all()
            )
            edges = list((await session.execute(select(DbEdge))).scalars().all())

        assert scan.status == "completed", scan.stats
        assert scan.stats["executed_tasks"] == 1
        assert entities and findings
        for entity in entities:
            assert len(entity.id) <= IDENTITY_ID_MAX_LENGTH
        for finding in findings:
            assert len(finding.id) <= IDENTITY_ID_MAX_LENGTH
            assert len(finding.entity_id) <= IDENTITY_ID_MAX_LENGTH
        for edge in edges:
            assert len(edge.id) <= IDENTITY_ID_MAX_LENGTH
            assert len(edge.source_entity_id) <= IDENTITY_ID_MAX_LENGTH
            assert len(edge.target_entity_id) <= IDENTITY_ID_MAX_LENGTH
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# (d) Finding id: kısa id'ler değişmez, uzunlar hash'lenir (deterministik)
# ---------------------------------------------------------------------------


def test_finding_id_kisa_entity_icin_degismez():
    assert make_finding_id("github:ada") == f"{FINDING_ID_PREFIX}github:ada"
    # 46 karakterlik HTB id'si de sınırın altında (54) → değişmez
    htb = make_entity_id(Platform.HACKTHEBOX.value, HTB_UUID)
    assert make_finding_id(htb) == f"{FINDING_ID_PREFIX}{htb}"


def test_finding_id_uzun_entity_icin_hashlenir_ve_sabittir():
    long_entity_id = "hackthebox:" + "x" * 90  # 101 karakter
    first = make_finding_id(long_entity_id)
    second = make_finding_id(long_entity_id)

    assert len(first) <= IDENTITY_ID_MAX_LENGTH
    assert first == second, "taramalar arası sabit olmalı (SEC-15 karar koruma)"
    assert first.startswith(FINDING_ID_PREFIX)
    assert long_entity_id not in first


# ---------------------------------------------------------------------------
# (e) Geriye uyum: mevcut kısa id'ler değişmiyor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform,identifier",
    [
        ("github", "ada"),
        ("gitlab", "ada.lovelace"),
        ("mastodon", "ada"),
        ("stackoverflow", "jon"),
        ("web", "https://example.com/ada"),
    ],
)
def test_kisa_idler_degismedi(platform, identifier):
    expected = f"{platform}:{identifier}"
    assert make_entity_id(platform, identifier) == expected
    assert make_finding_id(expected) == f"{FINDING_ID_PREFIX}{expected}"


# ---------------------------------------------------------------------------
# (f) Edge id sınırı migration ile 96'ya çıktı
# ---------------------------------------------------------------------------


def test_edge_id_siniri_96():
    assert EDGE_ID_MAX_LENGTH == 96
    short = "edge:1234567890"
    assert db_safe_edge_id(short) == short

    long_id = "edge:" + "y" * 120
    hashed = db_safe_edge_id(long_id)
    assert len(hashed) <= EDGE_ID_MAX_LENGTH
    assert db_safe_edge_id(long_id) == hashed  # deterministik

"""T-301 / ROADMAP 4.1 — authenticity (sahte/atıl hesap) skoru testleri."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from showis_contracts import Entity, EntityType, FindingStatus, Platform, Target
from showis_engine.authenticity import (
    NEUTRAL_SCORE,
    WEIGHTS,
    compute_authenticity,
    parse_timestamp,
)
from showis_engine.llm_explainer import FindingExplainer
from showis_engine.resolver import Resolver

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _age(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _rich_profile() -> dict:
    return {
        "username": "keremboz",
        "name": "Kerem Boz",
        "bio": "Security researcher working on OSINT tooling and detection engineering.",
        "avatar_url": "https://avatars.githubusercontent.com/u/1234?v=4",
        "created_at": _age(5 * 365),
        "public_repos": 42,
        "followers": 310,
    }


# ---------------------------------------------------------------------------
# 1. Nötr / veri yok
# ---------------------------------------------------------------------------


def test_veri_yoksa_notr_yarim():
    result = compute_authenticity({}, now=NOW)
    assert result.score == NEUTRAL_SCORE == 0.5
    assert result.signals == {}
    assert any("yetersiz veri" in reason for reason in result.reasons)


def test_sadece_bilinmeyen_alanlar_notr():
    result = compute_authenticity({"username": "abc", "platform": "web"}, now=NOW)
    assert result.score == 0.5
    assert result.used_signals == []


# ---------------------------------------------------------------------------
# 2. Boş / yeni hesap -> düşük
# ---------------------------------------------------------------------------


def test_bos_ve_yeni_hesap_dusuk():
    attrs = {
        "username": "user88213",
        "name": None,
        "bio": "",
        "avatar_url": "https://example.com/identicon/88213.png",
        "created_at": _age(2),
        "public_repos": 0,
        "followers": 0,
    }
    result = compute_authenticity(attrs, now=NOW)
    assert result.score < 0.35, result
    assert result.is_low is True
    joined = " | ".join(result.reasons)
    assert "çok yeni" in joined
    assert "etkinlik yok" in joined
    assert "bio boş" in joined
    assert "varsayılan avatar" in joined
    assert "görünen ad yok" in joined
    assert set(result.used_signals) == {"account_age", "activity", "bio", "avatar", "name"}


def test_terk_edilmis_hesap_dusuk():
    """Eski ama tamamen boş hesap: yaş sinyali pozitif olsa da toplam düşük."""
    attrs = {
        "name": None,
        "bio": "",
        "avatar_url": "",
        "created_at": _age(1200),
        "public_repos": 0,
    }
    result = compute_authenticity(attrs, now=NOW)
    assert result.score < 0.35, result
    assert result.is_low is True
    assert result.score < compute_authenticity(_rich_profile(), now=NOW).score


# ---------------------------------------------------------------------------
# 3. Olgun / aktif hesap -> yüksek
# ---------------------------------------------------------------------------


def test_olgun_aktif_hesap_yuksek():
    result = compute_authenticity(_rich_profile(), now=NOW)
    assert result.score >= 0.7, result
    assert result.is_low is False
    assert "yıllık" in " ".join(result.reasons)


def test_yeni_ama_canli_hesap_orta():
    """3 aylık ama dolu profil: düşük değil, olgun kadar yüksek de değil."""
    attrs = _rich_profile() | {"created_at": _age(100)}
    young = compute_authenticity(attrs, now=NOW)
    mature = compute_authenticity(_rich_profile(), now=NOW)
    assert young.score < mature.score


# ---------------------------------------------------------------------------
# 4. Tek sinyal / kısmi veri
# ---------------------------------------------------------------------------


def test_tek_sinyal_yumusak_etki():
    """Yalnız bio var: skor nötre yakın kalmalı (tek kanıt aşırı iddia etmez)."""
    result = compute_authenticity({"bio": "merhaba"}, now=NOW)
    assert result.used_signals == ["bio"]
    assert 0.5 < result.score < 0.7


def test_tek_negatif_sinyal_notrden_uzaklasir_ama_tabana_cakmaz():
    result = compute_authenticity({"created_at": _age(1)}, now=NOW)
    assert result.used_signals == ["account_age"]
    assert 0.2 < result.score < 0.35


def test_bir_sinyal_eksik_digerleri_tam():
    with_age = compute_authenticity(_rich_profile(), now=NOW)
    without_age = compute_authenticity(
        {k: v for k, v in _rich_profile().items() if k != "created_at"}, now=NOW
    )
    assert "account_age" not in without_age.used_signals
    assert without_age.score > 0.6
    assert abs(with_age.score - without_age.score) < 0.3


def test_okunamayan_tarih_sinyali_atlar():
    result = compute_authenticity({"created_at": "dun aksam"}, now=NOW)
    assert result.score == 0.5
    assert any("okunamadı" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# 5. Cross-platform Sybil / atıl çoklu hesap
# ---------------------------------------------------------------------------


def test_hepsi_bos_coklu_platform_supheli():
    empty = {
        "name": None,
        "bio": "",
        "avatar_url": "",
        "public_repos": 0,
    }
    single = compute_authenticity(empty, platforms_with_same_username=1, now=NOW)
    multi = compute_authenticity(empty, platforms_with_same_username=4, now=NOW)
    assert "cross_platform" in multi.used_signals
    assert multi.score < single.score
    assert any("tüm profiller boş" in reason for reason in multi.reasons)


def test_coklu_platform_dolu_profil_supheli_degil():
    result = compute_authenticity(
        _rich_profile(), platforms_with_same_username=5, now=NOW
    )
    assert result.signals["cross_platform"] == pytest.approx(0.70)
    assert result.score > 0.7


# ---------------------------------------------------------------------------
# 6. Yardımcılar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2024-05-01T10:00:00Z",
        "2024-05-01T10:00:00",
        "2024-05-01",
        1714557600,  # epoch saniye
        1714557600000,  # epoch milisaniye
    ],
)
def test_parse_timestamp_bicimleri(value):
    parsed = parse_timestamp(value)
    assert parsed is not None
    assert parsed.year == 2024


def test_parse_timestamp_gecersiz():
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
    assert parse_timestamp("gecersiz") is None
    assert parse_timestamp(-5) is None


def test_agirliklar_dokumante_edilmis():
    assert sum(WEIGHTS.values()) == pytest.approx(1.10)


# ---------------------------------------------------------------------------
# 7. Resolver entegrasyonu — skor/eşik DEĞİŞMEZ
# ---------------------------------------------------------------------------


def _entity(attributes: dict) -> Entity:
    return Entity(
        id="github:keremboz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="keremboz",
        url="https://github.com/keremboz",
        attributes=attributes,
    )


def test_resolver_skoru_degistirmez_authenticity_ekler():
    resolver = Resolver()
    target = Target(first_name="Kerem", last_name="Boz")
    attrs = {"name": "Kerem Boz", "bio": "", "created_at": _age(1), "public_repos": 0}
    finding = resolver.resolve_entity(
        _entity(attrs), target, case_id="c1", scan_id="s1"
    )

    # Mevcut formül: yalnız name_similarity 0.20 -> LEAD (eşik değişmedi)
    assert finding.score == 0.20
    assert finding.status == FindingStatus.LEAD
    assert finding.breakdown.total == 0.20
    assert (
        finding.breakdown.username_exact_match
        + finding.breakdown.name_similarity
        + finding.breakdown.cross_link
        + finding.breakdown.bio_location_company
        + finding.breakdown.commit_email_or_pgp
        + finding.breakdown.avatar_exact_hash
        + finding.breakdown.contradiction
        == pytest.approx(finding.score)
    )
    assert finding.authenticity is not None
    assert finding.authenticity < 0.35
    assert finding.authenticity_reasons


def test_resolver_verilen_authenticity_skorunu_kullanir():
    resolver = Resolver()
    target = Target(first_name="Kerem", last_name="Boz")
    finding = resolver.resolve_entity(
        _entity(_rich_profile()),
        target,
        case_id="c1",
        scan_id="s1",
        authenticity_score=0.12,
    )
    assert finding.authenticity == 0.12
    # Skor yine yalnız güven sinyallerinden: name 0.20
    assert finding.score == 0.20


def test_resolver_bozuk_authenticity_skorunu_yok_sayar():
    resolver = Resolver()
    target = Target(first_name="Kerem", last_name="Boz")
    finding = resolver.resolve_entity(
        _entity({"name": "Kerem Boz"}),
        target,
        case_id="c1",
        scan_id="s1",
        authenticity_score="yuksek",  # type: ignore[arg-type]
    )
    assert 0.0 <= float(finding.authenticity) <= 1.0


def test_resolver_veri_yoksa_notr_authenticity():
    resolver = Resolver()
    finding = resolver.resolve_entity(
        _entity({}), Target(first_name="Kerem", last_name="Boz"), case_id="c1", scan_id="s1"
    )
    assert finding.authenticity == 0.5


# ---------------------------------------------------------------------------
# 8. Açıklama — deterministik düşük güvenilirlik cümlesi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dusuk_authenticity_aciklamaya_eklenir():
    resolver = Resolver()
    target = Target(first_name="Kerem", last_name="Boz")
    # T-301: Resolver compute_authenticity için geçerli anı (now) kullanır.
    # 1 günlük çok yeni hesap sinyali için güncel zamana göre 1 gün öncesi verilir.
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    finding = resolver.resolve_entity(
        _entity({"name": "Kerem Boz", "bio": "", "created_at": one_day_ago}),
        target,
        case_id="c1",
        scan_id="s1",
    )
    explanation = await FindingExplainer(llm_client=None).explain(finding, target)
    assert "Düşük güvenilirlik" in explanation
    assert "authenticity" in explanation
    # skor değişmedi
    assert finding.score == 0.20


@pytest.mark.asyncio
async def test_yuksek_authenticity_aciklamaya_eklenmez():
    resolver = Resolver()
    target = Target(first_name="Kerem", last_name="Boz")
    finding = resolver.resolve_entity(
        _entity(_rich_profile()), target, case_id="c1", scan_id="s1"
    )
    explanation = await FindingExplainer(llm_client=None).explain(finding, target)
    assert "Düşük güvenilirlik" not in explanation


@pytest.mark.asyncio
async def test_authenticity_yoksa_aciklama_degismez():
    """authenticity None (ör. eski kayıt) -> cümle eklenmez."""
    resolver = Resolver()
    target = Target(first_name="Kerem", last_name="Boz")
    finding = resolver.resolve_entity(
        _entity({"name": "Kerem Boz"}), target, case_id="c1", scan_id="s1"
    )
    finding.authenticity = None
    finding.authenticity_reasons = []
    explanation = await FindingExplainer(llm_client=None).explain(finding, target)
    assert "Düşük güvenilirlik" not in explanation


# ---------------------------------------------------------------------------
# T-326: authenticity DB'ye yazılır (0004 migration kolonları)
# ---------------------------------------------------------------------------


async def _run_scan_with_entity(session_factory, entity: Entity, *, scan_id: str, case_id: str):
    """Tek entity'lik sahte tarama koşar (skill'i DB'ye yazar)."""
    from showis.db.models import Case as DbCase
    from showis.db.models import Scan as DbScan
    from showis.db.models import Target as DbTarget
    from showis.services.scanner import ScannerService
    from showis_contracts import Observation, ObservationKind, RiskTier, TaskSpec

    class _Conn:
        name = "fake"
        platform = entity.platform
        tier = RiskTier.A
        supported_kinds = {"probe"}

        async def plan(self, ctx):
            return [
                TaskSpec(
                    id=f"{scan_id}-t1",
                    connector=self.name,
                    platform=entity.platform,
                    kind="probe",
                    params={},
                )
            ]

        async def run(self, task, ctx):
            return [
                Observation(
                    id=f"obs-{scan_id}",
                    connector=self.name,
                    platform=entity.platform,
                    kind=ObservationKind.PROFILE,
                    subject=entity.label,
                    tier=self.tier,
                    data={},
                )
            ]

        def normalize(self, obs):
            return [entity], []

    async with session_factory() as session:
        # Yeniden tarama senaryosunda vaka hedefi zaten var olabilir (idempotent kurulum)
        if await session.get(DbCase, case_id) is None:
            session.add(
                DbCase(
                    id=case_id,
                    name="Auth Test",
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
        session.add(DbScan(id=scan_id, case_id=case_id, mode="O2", status="running", budget={}))
        await session.commit()

    async with session_factory() as session:
        await ScannerService(connectors=[_Conn()]).execute_scan(scan_id, session)


@pytest.fixture
async def scan_db(tmp_path, monkeypatch):
    from showis.db import models as _models  # noqa: F401 - Base.metadata'yi doldurur
    from showis.db.base import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    monkeypatch.setenv("SHOWIS_EVIDENCE_DIR", str(tmp_path / "evidence"))
    # Bellek içi sqlite: tüm oturumlar aynı bağlantıyı paylaşsın (StaticPool).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_tarama_authenticityyi_db_yazar(scan_db):
    from showis.db.models import Finding as DbFinding
    from sqlalchemy import select

    entity = Entity(
        id="github:kboz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="kboz",
        attributes={
            "username": "kboz",
            "name": "Kerem Boz",
            "bio": "",
            "avatar_url": "",
            "created_at": "2026-11-15T00:00:00Z",  # 1 günlük
            "public_repos": 0,
        },
    )
    await _run_scan_with_entity(scan_db, entity, scan_id="scan-a", case_id="case-a")

    async with scan_db() as session:
        row = (await session.execute(select(DbFinding))).scalars().one()
    assert row.authenticity is not None
    assert 0.0 <= row.authenticity <= 1.0
    assert row.authenticity < 0.35, "boş/yeni hesap düşük authenticity almalı"
    assert row.authenticity_reasons, "gerekçeler DB'ye yazılmalı"
    assert any("sinyaller" in reason for reason in row.authenticity_reasons)


@pytest.mark.asyncio
async def test_veri_yoksa_notr_authenticity_db_yazilir(scan_db):
    from showis.db.models import Finding as DbFinding
    from sqlalchemy import select

    entity = Entity(
        id="github:bos",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="bos",
        attributes={"username": "bos"},
    )
    await _run_scan_with_entity(scan_db, entity, scan_id="scan-b", case_id="case-b")

    async with scan_db() as session:
        row = (await session.execute(select(DbFinding))).scalars().one()
    assert row.authenticity == pytest.approx(0.5)
    assert row.authenticity_reasons


@pytest.mark.asyncio
async def test_onaylanmis_bulgu_karari_korunur_authenticity_guncellenir(scan_db):
    """SEC-15: kullanıcı kararı korunur, authenticity (skor gibi) güncellenir."""
    from datetime import datetime, timezone

    from showis.db.models import Finding as DbFinding
    from sqlalchemy import select

    entity = Entity(
        id="github:kboz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="kboz",
        attributes={"username": "kboz", "name": "Kerem Boz"},
    )
    await _run_scan_with_entity(scan_db, entity, scan_id="scan-c", case_id="case-c")

    # Kullanıcı bulguyu onayladı
    async with scan_db() as session:
        row = (await session.execute(select(DbFinding))).scalars().one()
        row.status = "confirmed"
        row.verified_at = datetime.now(timezone.utc)
        row.authenticity = 0.99  # eski/yanlış değer: yeniden tarama düzeltmeli
        await session.commit()

    # Yeniden tarama (aynı vaka/bulgu kimliği)
    await _run_scan_with_entity(scan_db, entity, scan_id="scan-c2", case_id="case-c")

    async with scan_db() as session:
        row = (await session.execute(select(DbFinding))).scalars().one()
    assert row.status == "confirmed", "kullanıcı kararı korunmalı (SEC-15)"
    assert row.verified_at is not None
    assert row.authenticity != pytest.approx(0.99), "authenticity güncellenmeli"
    assert row.authenticity_reasons

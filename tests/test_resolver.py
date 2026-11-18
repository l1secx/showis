import pytest
from showis_contracts import (
    ConfidenceBreakdown,
    Entity,
    EntityType,
    FindingStatus,
    Platform,
    Target,
)
from showis_engine.llm_explainer import FindingExplainer
from showis_engine.resolver import (
    BASIS_AVATAR,
    BASIS_COMMIT_EMAIL,
    BASIS_CROSS_LINK,
    BASIS_WEAK,
    Resolver,
    classify_status,
    compute_confidence_basis,
)


def test_resolver_confirmed_scenario():
    resolver = Resolver()
    target = Target(
        first_name="Ahmet",
        last_name="Yılmaz",
        hints={"city": "Ankara", "company": "CyberSec Inc"},
    )
    entity = Entity(
        id="github:ahmetyilmaz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="ahmetyilmaz",
        url="https://github.com/ahmetyilmaz",
        attributes={
            "name": "Ahmet Yılmaz",
            "location": "Ankara, Turkey",
            "company": "CyberSec Inc",
        },
    )

    finding = resolver.resolve_entity(
        entity,
        target,
        case_id="c-1",
        scan_id="s-1",
        known_platforms_with_username={"github", "tryhackme"},  # +0.30
        has_cross_link=True,  # +0.25
        # name match +0.20, location+company +0.10
    )

    # 0.30 + 0.20 + 0.25 + 0.10 = 0.85 >= 0.80 -> CONFIRMED
    assert finding.score == 0.85
    assert finding.status == FindingStatus.CONFIRMED
    assert finding.confidence_basis == BASIS_CROSS_LINK
    assert finding.breakdown.username_exact_match == 0.30
    assert finding.breakdown.name_similarity == 0.20
    assert finding.breakdown.cross_link == 0.25
    assert finding.breakdown.bio_location_company == 0.10


def test_resolver_candidate_scenario():
    resolver = Resolver()
    target = Target(first_name="Selin", last_name="Kara", hints={"city": "Istanbul"})
    entity = Entity(
        id="github:selinkara",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="selinkara",
        attributes={"name": "Selin Kara", "location": "Istanbul"},
    )

    finding = resolver.resolve_entity(
        entity,
        target,
        case_id="c-1",
        scan_id="s-1",
        known_platforms_with_username={"github", "hackthebox"},  # +0.30
        has_cross_link=False,
    )

    # 0.30 + 0.20 (name) + 0.05 (city) = 0.55 -> CANDIDATE (0.50-0.79)
    assert finding.score == 0.55
    assert finding.status == FindingStatus.CANDIDATE


def test_resolver_rejected_and_contradiction():
    resolver = Resolver()
    target = Target(first_name="Can", last_name="Demir")
    entity = Entity(
        id="github:candemir",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="candemir",
        attributes={"name": "Can Demir"},
    )

    finding = resolver.resolve_entity(
        entity,
        target,
        case_id="c-1",
        scan_id="s-1",
        has_contradiction=True,  # -0.20
    )

    # name: 0.20, contradiction: -0.20 -> 0.00 -> REJECTED
    assert finding.score == 0.0
    assert finding.status == FindingStatus.REJECTED


@pytest.mark.asyncio
async def test_explainer_does_not_change_score():
    resolver = Resolver()
    target = Target(first_name="Ahmet", last_name="Yılmaz")
    entity = Entity(
        id="github:ayilmaz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="ayilmaz",
        attributes={"name": "Ahmet Yılmaz"},
    )

    finding = resolver.resolve_entity(entity, target, case_id="c-1", scan_id="s-1")
    initial_score = finding.score

    explainer = FindingExplainer(llm_client=None)
    explanation = await explainer.explain(finding, target)

    assert finding.score == initial_score
    assert len(explanation) > 0
    assert "Ahmet" in explanation or "Github" in explanation or "ad" in explanation


# ---------------------------------------------------------------------------
# T-301: authenticity ayrı sinyal — güven skoru/eşikleri DEĞİŞMEZ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authenticity",
    [None, 0.0, 0.2, 0.5, 1.0],
)
def test_authenticity_skoru_statusu_degistirmez(authenticity):
    """Authenticity hangi değeri alırsa alsın score/status aynı kalmalı."""
    resolver = Resolver()
    target = Target(first_name="Ahmet", last_name="Yılmaz", hints={"city": "Ankara"})
    entity = Entity(
        id="github:ahmetyilmaz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="ahmetyilmaz",
        attributes={"name": "Ahmet Yılmaz", "location": "Ankara"},
    )

    finding = resolver.resolve_entity(
        entity,
        target,
        case_id="c-1",
        scan_id="s-1",
        known_platforms_with_username={"github", "gitlab"},
        authenticity_score=authenticity,
    )

    assert finding.score == 0.55
    assert finding.status == FindingStatus.CANDIDATE
    if authenticity is None:
        assert finding.authenticity is not None  # resolver kendi hesaplar
    else:
        assert finding.authenticity == pytest.approx(authenticity)


# ---------------------------------------------------------------------------
# Task 6: handle_exact — tek platformda birebir handle eşleşmesi (LEAD)
# ---------------------------------------------------------------------------


def _handle_target(nickname: str = "kerem") -> Target:
    return Target(kind="handle", nickname=nickname)


def _handle_entity(
    *, label: str = "kerem", platform: Platform = Platform.GITHUB, **attrs
) -> Entity:
    return Entity(
        id=f"{platform.value}:{label}",
        type=EntityType.ACCOUNT,
        platform=platform,
        label=label,
        attributes=dict(attrs),
    )


def test_single_platform_exact_handle_is_a_lead():
    f = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        _handle_target("kerem"),
        "c",
        "s",
        username_platform_count=1,
    )
    assert f.breakdown.handle_exact == 0.15
    assert f.breakdown.username_exact_match == 0.0
    assert f.score == 0.15
    assert f.status == FindingStatus.LEAD


def test_two_platforms_use_username_exact_not_handle_exact():
    f = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        _handle_target("kerem"),
        "c",
        "s",
        username_platform_count=2,
    )
    assert f.breakdown.username_exact_match == 0.30
    assert f.breakdown.handle_exact == 0.0
    assert f.score == 0.30


def test_handle_exact_falls_back_to_label_when_username_missing():
    """GitHub profil entity'leri yalnız `label` taşır (attrs'ta username yok)."""
    f = Resolver().resolve_entity(
        _handle_entity(label="Kerem"),
        _handle_target("kerem"),
        "c",
        "s",
        username_platform_count=1,
    )
    assert f.breakdown.handle_exact == 0.15
    assert f.status == FindingStatus.LEAD


def test_handle_exact_ignores_mismatched_handle():
    f = Resolver().resolve_entity(
        _handle_entity(label="someoneelse", username="someoneelse"),
        _handle_target("kerem"),
        "c",
        "s",
        username_platform_count=1,
    )
    assert f.breakdown.handle_exact == 0.0
    assert f.status == FindingStatus.REJECTED


@pytest.mark.parametrize(
    ("nickname", "entity_form"),
    [
        ("@kerem", "kerem"),  # nick başındaki @ eşleşmeyi engellemez
        ("şükrü", "sukru"),  # Türkçe karakterler ortak normalizer ile katlanır
    ],
)
def test_handle_exact_uses_shared_normalizer(nickname, entity_form):
    """Nick ve entity kimliği aynı normalizer'dan geçer (tek platform -> 0.15)."""
    f = Resolver().resolve_entity(
        _handle_entity(label=entity_form, username=entity_form),
        _handle_target(nickname),
        "c",
        "s",
        username_platform_count=1,
    )
    assert f.breakdown.handle_exact == 0.15
    assert f.status == FindingStatus.LEAD


@pytest.mark.parametrize("nickname", ["", "   ", "@"])
def test_empty_nickname_never_matches_handle(nickname):
    """Boş nick normalize edilince boş kalır; boş handle eşleşme sayılmaz."""
    f = Resolver().resolve_entity(
        _handle_entity(label="", username=""),
        Target(kind="handle", nickname=nickname),
        "c",
        "s",
        username_platform_count=1,
    )
    assert f.breakdown.handle_exact == 0.0
    assert f.status == FindingStatus.REJECTED


# ---------------------------------------------------------------------------
# Task 9: explainer handle sinyalini bilir, hedef etiketi display_name
# ---------------------------------------------------------------------------


class _RecordingClient:
    """LLM çağrısını ağa çıkmadan kaydeden sahte istemci."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages, schema=None, temperature=0.0, max_tokens=None):  # noqa: ANN001
        self.prompts.append(messages[0]["content"])
        return "LLM açıklaması"


@pytest.mark.asyncio
async def test_handle_lead_explained_by_handle_evidence():
    """Lead'in tek kanıtı handle eşleşmesi; açıklama 'kanıt zayıf' dememeli."""
    target = _handle_target("kerem")
    finding = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        target,
        "c",
        "s",
        username_platform_count=1,
    )
    assert finding.status == FindingStatus.LEAD

    explanation = await FindingExplainer(llm_client=None).explain(finding, target)
    assert "kullanıcı adı tek platformda birebir eşleşti" in explanation
    assert "kanıt kuvveti zayıf" not in explanation


@pytest.mark.asyncio
async def test_llm_prompt_uses_display_name_for_handle_target():
    target = _handle_target("kerem")
    finding = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        target,
        "c",
        "s",
        username_platform_count=1,
    )
    client = _RecordingClient()
    await FindingExplainer(llm_client=client).explain(finding, target)

    assert client.prompts
    prompt = client.prompts[0]
    target_section = prompt.split("HEDEF:", 1)[1].split("BULGU", 1)[0]
    assert "@kerem" in target_section
    assert "None" not in target_section


@pytest.mark.asyncio
async def test_llm_prompt_adds_nick_only_when_name_present():
    target = Target(kind="name", first_name="Kerem", last_name="Boz", nickname="kerem")
    finding = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        target,
        "c",
        "s",
        username_platform_count=1,
    )
    client = _RecordingClient()
    await FindingExplainer(llm_client=client).explain(finding, target)

    prompt = client.prompts[0]
    assert "Kerem Boz (@kerem)" in prompt


# ---------------------------------------------------------------------------
# C1: CONFIRMED = skor >= 0.80 VE bağımsız doğrulama sinyali
#     (spec 2026-11-18-osint-depth-design §C)
# ---------------------------------------------------------------------------


def _weak_max_target() -> Target:
    """Bağımsız doğrulama sinyali ÜRETMEYEN en zengin hedef ipucu seti."""
    return Target(
        first_name="Ahmet",
        last_name="Yılmaz",
        hints={"city": "Ankara", "company": "CyberSec Inc", "keyword": "pentest"},
    )


def _weak_max_entity() -> Entity:
    """username_exact (0.30) + name (0.20) + bio/location/company (0.15) = 0.65."""
    return Entity(
        id="github:ahmetyilmaz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="ahmetyilmaz",
        url="https://github.com/ahmetyilmaz",
        attributes={
            "name": "Ahmet Yılmaz",
            "location": "Ankara, Turkey",
            "company": "CyberSec Inc",
            "bio": "pentest uzmanı",
        },
    )


def _resolve_weak_max(**kwargs):
    return Resolver().resolve_entity(
        _weak_max_entity(),
        _weak_max_target(),
        "c-1",
        "s-1",
        known_platforms_with_username={"github", "tryhackme"},  # +0.30
        **kwargs,
    )


def test_bagimsiz_sinyal_olmadan_080_erisilemez():
    """Yapısal değişmez: zayıf sinyallerin toplamı eşiğin altında kalır (0.65 < 0.80).

    Ağırlıklar değişirse bu test kırılır: kırıldığında `classify_status`'un
    bağımsız-sinyal kapısı tek savunma hattı olarak kalır (aşağıdaki testler).
    """
    f = _resolve_weak_max()
    assert f.score == 0.65  # 0.30 username + 0.20 name + 0.15 bio
    assert f.status == FindingStatus.CANDIDATE
    assert f.confidence_basis == BASIS_WEAK


def test_weak_skor_080_uzerinde_olsa_da_candidate():
    """Kural doğrudan karar fonksiyonunda: 0.90 skor + bağımsız sinyal yok -> CANDIDATE."""
    breakdown = ConfidenceBreakdown(
        username_exact_match=0.30,
        name_similarity=0.20,
        bio_location_company=0.15,
        total=0.90,
    )
    assert compute_confidence_basis(breakdown) == BASIS_WEAK
    assert (
        classify_status(0.90, breakdown, has_contradiction=False)
        == FindingStatus.CANDIDATE
    )


def test_cross_link_bagimsiz_dogrulama_confirmed_yapar():
    f = _resolve_weak_max(has_cross_link=True)  # +0.25
    assert f.score == 0.90
    assert f.status == FindingStatus.CONFIRMED
    assert f.confidence_basis == BASIS_CROSS_LINK


def test_commit_email_bagimsiz_dogrulama_confirmed_yapar():
    """Tam eşik: 0.65 + 0.15 = 0.80 -> CONFIRMED (yalnız skor yetmez, sinyal şart)."""
    f = _resolve_weak_max(email_or_pgp_match=True)  # +0.15
    assert f.score == 0.80
    assert f.status == FindingStatus.CONFIRMED
    assert f.confidence_basis == BASIS_COMMIT_EMAIL


def test_avatar_tek_basina_candidate_ama_basis_avatar():
    """avatar_exact_hash 0.10: bugünkü ağırlıklarda tek başına 0.80'e yetmez.

    Gerekçe yine de taşınır (`confidence_basis="avatar"`), statü CANDIDATE kalır.
    """
    f = _resolve_weak_max(avatar_match=True)  # +0.10
    assert f.score == 0.75
    assert f.status == FindingStatus.CANDIDATE
    assert f.confidence_basis == BASIS_AVATAR


def test_avatar_basis_080_uzeri_skorda_confirmed():
    """Kural yazıldığı gibi: 0.85 + avatar sinyali -> CONFIRMED, basis "avatar".

    Bugünkü ağırlıklarla resolver girdisinden üretilemez (avatar 0.10 < 0.15
    eksik); ağırlık değişiminde kural aynı kalsın diye doğrudan test edilir.
    """
    breakdown = ConfidenceBreakdown(
        username_exact_match=0.30,
        name_similarity=0.20,
        bio_location_company=0.15,
        avatar_exact_hash=0.10,
        total=0.85,
    )
    assert compute_confidence_basis(breakdown) == BASIS_AVATAR
    assert (
        classify_status(0.85, breakdown, has_contradiction=False)
        == FindingStatus.CONFIRMED
    )


@pytest.mark.parametrize(
    ("signals", "expected_basis"),
    [
        (
            {"cross_link": 0.25, "commit_email_or_pgp": 0.15, "avatar_exact_hash": 0.10},
            BASIS_CROSS_LINK,
        ),
        (
            {"commit_email_or_pgp": 0.15, "avatar_exact_hash": 0.10},
            BASIS_COMMIT_EMAIL,
        ),
        ({"avatar_exact_hash": 0.10}, BASIS_AVATAR),
        ({}, BASIS_WEAK),
    ],
)
def test_confidence_basis_oncelik_sirasi(signals, expected_basis):
    """Öncelik: cross_link > commit_email > avatar > weak."""
    breakdown = ConfidenceBreakdown(**signals)
    assert compute_confidence_basis(breakdown) == expected_basis


def test_confidence_basis_skoru_degistirmez():
    """Basis yalnız gerekçe taşır: skor bileşenlerin toplamı ve breakdown.total ile aynı."""
    f = _resolve_weak_max(has_cross_link=True)
    components = (
        f.breakdown.username_exact_match
        + f.breakdown.handle_exact
        + f.breakdown.name_similarity
        + f.breakdown.cross_link
        + f.breakdown.bio_location_company
        + f.breakdown.commit_email_or_pgp
        + f.breakdown.avatar_exact_hash
        + f.breakdown.contradiction
    )
    assert f.breakdown.total == f.score
    assert components == pytest.approx(f.score)
    assert f.score == 0.90


def test_050_079_arasi_candidate_degismedi():
    """CANDIDATE eşiği (0.50) değişmedi; basis yalnız weak."""
    resolver = Resolver()
    target = Target(first_name="Selin", last_name="Kara", hints={"city": "Istanbul"})
    entity = Entity(
        id="github:selinkara",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="selinkara",
        attributes={"name": "Selin Kara", "location": "Istanbul"},
    )
    f = resolver.resolve_entity(
        entity,
        target,
        "c-1",
        "s-1",
        known_platforms_with_username={"github", "hackthebox"},
    )
    assert f.score == 0.55
    assert f.status == FindingStatus.CANDIDATE
    assert f.confidence_basis == BASIS_WEAK


def test_lead_kosulu_degismedi():
    """LEAD koşulu (isim/okul/handle benzerliği) aynı; basis weak."""
    f = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        _handle_target("kerem"),
        "c",
        "s",
        username_platform_count=1,
    )
    assert f.status == FindingStatus.LEAD
    assert f.confidence_basis == BASIS_WEAK


def test_contradiction_confirmed_esigini_dusurur():
    """cross_link olsa da çelişki skoru 0.70'e indirir -> CONFIRMED değil."""
    f = _resolve_weak_max(has_cross_link=True, has_contradiction=True)
    assert f.score == 0.70  # 0.90 - 0.20
    assert f.status == FindingStatus.CANDIDATE
    assert f.confidence_basis == BASIS_CROSS_LINK

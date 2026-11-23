import pytest
from showis_contracts import Entity, EntityType, FindingStatus, Platform, Target
from showis_engine.llm_explainer import FindingExplainer
from showis_engine.resolver import Resolver


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


@pytest.mark.asyncio
async def test_llm_prompt_never_renders_double_at():
    """R-05: legacy `nickname="@kerem"` (başında @) prompt'ta `@@kerem` olmaz."""
    target = Target(kind="handle", nickname="@kerem")
    finding = Resolver().resolve_entity(
        _handle_entity(label="kerem", username="kerem"),
        target,
        "c",
        "s",
        username_platform_count=1,
    )
    client = _RecordingClient()
    await FindingExplainer(llm_client=client).explain(finding, target)

    target_section = client.prompts[0].split("HEDEF:", 1)[1].split("BULGU", 1)[0]
    assert "@@kerem" not in target_section
    assert "@kerem" in target_section


# ---------------------------------------------------------------------------
# İsim benzerliği: sıra bağımsız ve baş harf toleranslı
# ---------------------------------------------------------------------------


def test_isim_soyad_ters_siralama_tam_puan():
    from showis_engine.resolver import calculate_name_similarity

    assert calculate_name_similarity("Ahmet Yılmaz", "Yılmaz Ahmet") == 0.20
    assert calculate_name_similarity("Ayşe Nur Yılmaz", "Ayşe Yılmaz") == pytest.approx(
        0.13, abs=0.01
    )
    assert calculate_name_similarity("Ahmet Yılmaz", "A. Yılmaz") == 0.20
    assert calculate_name_similarity("Ahmet Yılmaz", "Mehmet Yılmaz") < 0.20


def test_resolver_ters_isimli_entity_tam_isim_puani_alir():
    resolver = Resolver()
    target = Target(first_name="Ahmet", last_name="Yılmaz")
    entity = Entity(
        id="github:yilmazahmet",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="yilmazahmet",
        attributes={"name": "Yılmaz Ahmet"},
    )
    finding = resolver.resolve_entity(entity, target, case_id="c", scan_id="s")
    assert finding.breakdown.name_similarity == 0.20


def test_email_hedefi_birebir_eslesirse_candidate():
    resolver = Resolver()
    target = Target(kind="email", email="ada@example.com")
    entity = Entity(
        id="web:email:ada",
        type=EntityType.ACCOUNT,
        platform=Platform.WEB,
        label="ada@example.com",
        attributes={"email": "ada@example.com", "confidence_hint": 0.8},
    )
    finding = resolver.resolve_entity(entity, target, case_id="c", scan_id="s")
    assert finding.breakdown.identifier_match == 0.50
    assert finding.score == 0.50
    assert finding.status == FindingStatus.CANDIDATE


def test_domain_hedefi_yanlis_eslesirse_puan_yok():
    resolver = Resolver()
    target = Target(kind="domain", domain="example.com")
    entity = Entity(
        id="web:domain:other",
        type=EntityType.DOMAIN,
        platform=Platform.WEB,
        label="other.com",
        attributes={"domain": "other.com"},
    )
    finding = resolver.resolve_entity(entity, target, case_id="c", scan_id="s")
    assert finding.breakdown.identifier_match == 0.0

from showis_contracts import (
    Budget,
    ConfidenceBreakdown,
    Edge,
    EdgeType,
    Entity,
    EntityType,
    Evidence,
    ExtractionResult,
    Finding,
    FindingStatus,
    GithubProfile,
    HtbProfile,
    Observation,
    ObservationKind,
    Platform,
    QueryPlan,
    RiskTier,
    ScanContext,
    SearchQuery,
    Seed,
    Target,
    TaskSpec,
    ThmProfile,
    UsernameVariant,
    VariantSet,
    WebProfile,
)


def test_evidence_roundtrip():
    ev = Evidence(
        id="ev-1",
        source_url="https://api.github.com/users/test",
        kind="json",
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        storage_key="cases/1/ev-1.json",
        http_status=200,
        user_agent="Showis/0.1",
        truncated=False,
    )
    dumped = ev.model_dump_json()
    loaded = Evidence.model_validate_json(dumped)
    assert loaded == ev


def test_observation_roundtrip():
    obs = Observation(
        id="obs-1",
        connector="github",
        platform=Platform.GITHUB,
        kind=ObservationKind.PROFILE,
        subject="testuser",
        url="https://github.com/testuser",
        tier=RiskTier.A,
        data={"login": "testuser", "public_repos": 12},
        error=None,
        blocked=False,
    )
    dumped = obs.model_dump_json()
    loaded = Observation.model_validate_json(dumped)
    assert loaded.id == obs.id
    assert loaded.platform == Platform.GITHUB
    assert loaded.tier == RiskTier.A


def test_entity_and_edge_roundtrip():
    e1 = Entity(
        id="ent-1",
        type=EntityType.PERSON,
        platform=Platform.WEB,
        label="Ahmet Yılmaz",
        attributes={"city": "Ankara"},
    )
    e2 = Entity(
        id="ent-2",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="ahmetyilmaz",
        url="https://github.com/ahmetyilmaz",
    )
    edge = Edge(
        id="edge-1",
        type=EdgeType.HAS_ACCOUNT,
        source_entity_id=e1.id,
        target_entity_id=e2.id,
        confidence=0.85,
        reason="Username match and city match",
    )
    assert Entity.model_validate_json(e1.model_dump_json()) == e1
    assert Entity.model_validate_json(e2.model_dump_json()) == e2
    assert Edge.model_validate_json(edge.model_dump_json()) == edge


def test_finding_and_breakdown_roundtrip():
    breakdown = ConfidenceBreakdown(
        username_exact_match=0.30,
        name_similarity=0.20,
        cross_link=0.25,
        total=0.75,
    )
    finding = Finding(
        id="find-1",
        case_id="case-100",
        scan_id="scan-200",
        entity_id="ent-2",
        platform=Platform.GITHUB,
        url="https://github.com/ahmetyilmaz",
        label="ahmetyilmaz",
        status=FindingStatus.CANDIDATE,
        score=0.75,
        breakdown=breakdown,
        explanation="GitHub profili bulundu.",
    )
    loaded = Finding.model_validate_json(finding.model_dump_json())
    assert loaded.score == 0.75
    assert loaded.breakdown.total == 0.75


def test_tasks_and_context_roundtrip():
    target = Target(first_name="Ahmet", last_name="Yılmaz", hints={"city": "Istanbul"})
    seed = Seed(platform=Platform.GITHUB, username="ayilmaz")
    budget = Budget(max_tasks=50, max_cost_usd=1.0)
    ctx = ScanContext(
        case_id="c-1",
        scan_id="s-1",
        target=target,
        seeds=[seed],
        budget=budget,
        llm_session_id="uuid-123",
    )
    spec = TaskSpec(
        id="t-1",
        connector="github",
        platform=Platform.GITHUB,
        kind="get_user",
        params={"username": "ayilmaz"},
    )
    assert ScanContext.model_validate_json(ctx.model_dump_json()).case_id == "c-1"
    assert TaskSpec.model_validate_json(spec.model_dump_json()).connector == "github"


def test_profiles_roundtrip():
    gh = GithubProfile(login="octocat", name="The Octocat", public_repos=8)
    thm = ThmProfile(username="thm_hacker", rank=104, rooms_completed=45)
    htb = HtbProfile(username="htb_user", profile_id="12345", rank="Pro Hacker")
    web = WebProfile(url="https://example.com", title="Example", extracted_names=["Ahmet"])

    assert GithubProfile.model_validate_json(gh.model_dump_json()).login == "octocat"
    assert ThmProfile.model_validate_json(thm.model_dump_json()).username == "thm_hacker"
    assert HtbProfile.model_validate_json(htb.model_dump_json()).profile_id == "12345"
    assert WebProfile.model_validate_json(web.model_dump_json()).url == "https://example.com"


def test_llm_models_roundtrip():
    plan = QueryPlan(
        queries=[
            SearchQuery(
                query='"Ahmet Yılmaz" site:github.com',
                platform=Platform.GITHUB,
                priority=1,
            )
        ]
    )
    variants = VariantSet(
        variants=[
            UsernameVariant(username="ayilmaz", pattern="first_initial.last", confidence=0.8)
        ],
        name_forms=["Ahmet Yılmaz", "ayilmaz"],
    )
    extraction = ExtractionResult(
        display_name="Ahmet",
        username="ayilmaz",
        bio="Security researcher",
        links=["https://twitter.com/ayilmaz"],
        emails=["ayilmaz@example.com"],
        signals=["bio location matches Istanbul"],
    )

    assert len(QueryPlan.model_validate_json(plan.model_dump_json()).queries) == 1
    assert len(VariantSet.model_validate_json(variants.model_dump_json()).variants) == 1
    assert ExtractionResult.model_validate_json(extraction.model_dump_json()).username == "ayilmaz"


def test_target_display_name_prefers_full_name():
    t = Target(first_name="Kerem", last_name="Boz", nickname="kb")
    assert t.display_name == "Kerem Boz"


def test_target_display_name_falls_back_to_nick():
    assert Target(kind="handle", nickname="kerem").display_name == "@kerem"


def test_target_display_name_no_trailing_space_without_surname():
    assert Target(first_name="Kerem", nickname="kb").display_name == "Kerem"


def test_target_defaults_are_backward_compatible():
    t = Target(first_name="A", last_name="B")
    assert t.kind == "name" and t.nickname == ""


def test_target_display_name_is_computed_not_serialized():
    """`display_name` türetilmiş alandır: dump ve şemada görünmez."""
    t = Target(first_name="A", last_name="B")
    assert t.display_name == "A B"
    assert "display_name" not in t.model_dump()
    assert "display_name" not in Target.model_json_schema()["properties"]

"""Kimlik zinciri motoru: güven yayılımı, geri bağlantı, tahmin eleme, kalıcılık."""

from __future__ import annotations

import pytest
from showis.db.models import Case, PoolItem, PoolRelation
from showis.services.chain import (
    EXPAND_MIN_TRUST,
    ChainEngine,
    PageData,
    anchors_from_items,
    profile_identity,
    run_case_chain,
)
from showis.services.pool import create_item
from showis_connectors.account_intel import AccountIntel, AccountLink
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _intel(
    platform: str, handle: str, *, name: str = "", links=(), emails=(), **extra
) -> AccountIntel:
    intel = AccountIntel(
        platform=platform,
        handle=handle,
        profile_url=f"https://{platform}.com/{handle}",
        display_name=name,
        **extra,
    )
    for url, relation in links:
        intel.links.append(AccountLink(url=url, relation=relation))
    for email in emails:
        intel.emails.append((email, "github-commit"))
    return intel


INTEL = {
    ("github", "l1sec"): _intel(
        "github",
        "l1sec",
        name="Kerem Demir",
        links=[
            ("https://linktr.ee/l1sec", "owner"),
            ("https://x.com/l1sec_tw", "owner"),
            ("https://youtube.com/@l1tube", "readme"),
            ("https://github.com/torvalds", "readme"),  # takip edilen biri: atlanır
            ("https://github.com/l1sec/proje", "readme"),  # depo: profil değil
        ],
        emails=["kerem@l1sec.dev"],
    ),
    # Aynı kullanıcı adı, sahibine geri bağlanan GitLab hesabı -> doğrulanır.
    ("gitlab", "l1sec"): _intel(
        "gitlab",
        "l1sec",
        name="",
        links=[("https://linktr.ee/l1sec", "owner")],
        location="İstanbul",
        company="@acme",
    ),
    # Aynı kullanıcı adı, farklı kişi -> elenir.
    ("keybase", "l1sec"): _intel("keybase", "l1sec", name="Jane Smith"),
    ("devto", "l1sec"): None,
}

PAGES = {
    "https://linktr.ee/l1sec": PageData(
        url="https://linktr.ee/l1sec",
        links=[
            "https://www.instagram.com/l1sec.ig/",
            "https://github.com/l1sec",
            "https://medium.com/@l1writes",
        ],
    ),
}


class _Fake:
    def __init__(self) -> None:
        self.intel_calls: list[tuple[str, str]] = []
        self.page_calls: list[tuple[str, bool]] = []

    async def intel(self, network: str, handle: str):
        self.intel_calls.append((network, handle))
        return INTEL.get((network, handle))

    async def page(self, url: str, trusted: bool):
        self.page_calls.append((url, trusted))
        return PAGES.get(url)


def _engine(fake: _Fake, **kwargs) -> ChainEngine:
    return ChainEngine(
        intel_fetcher=fake.intel,
        page_fetcher=fake.page,
        email_fetcher=None,
        names=["Kerem Demir"],
        **kwargs,
    )


def test_profile_identity_maps_social_hosts():
    assert profile_identity("https://x.com/l1sec_tw")[:2] == ("x", "l1sec_tw")
    assert profile_identity("https://www.linkedin.com/in/kerem")[:2] == ("linkedin", "kerem")
    assert profile_identity("https://linktr.ee/l1sec")[:2] == ("linktr.ee", "l1sec")
    assert profile_identity("https://github.com/l1sec/repo") is None
    assert profile_identity("https://news.ycombinator.com/user?id=pg")[:2] == ("hackernews", "pg")


@pytest.mark.asyncio
async def test_chain_walks_owner_links_and_verifies_backlinks():
    fake = _Fake()
    engine = _engine(fake)
    engine.add_anchor_url("https://github.com/l1sec", item_id=None)
    result = await engine.run()
    nodes = {node.key: node for node in result.nodes}

    linktree = nodes["profile:linktr.ee:l1sec"]
    # Sahip alanı (0.95) + linktree'nin github'a geri bağlantısı -> çok yüksek güven.
    assert linktree.trust >= 0.99
    assert any(e.relation == "backlink" for e in linktree.evidence)
    assert nodes["profile:x:l1sec_tw"].trust >= 0.94
    # Zincirin ikinci halkası: linktree -> instagram.
    insta = nodes["profile:instagram:l1sec.ig"]
    assert insta.trust > 0.85 and insta.depth == 2
    assert nodes["email:kerem@l1sec.dev"].trust > 0.8
    # Başkasının GitHub profili ve depo bağlantısı zincire girmez.
    assert "profile:github:torvalds" not in nodes
    # Yalnız kullanıcı adı eşleşen ama sahibine geri bağlanan GitLab doğrulanır.
    gitlab = nodes["profile:gitlab:l1sec"]
    assert gitlab.trust >= EXPAND_MIN_TRUST
    assert any(e.relation == "backlink" for e in gitlab.evidence)
    # Farklı isimli Keybase hesabı ve olmayan dev.to hesabı elenir.
    assert nodes["profile:keybase:l1sec"].trust < 0.4
    assert nodes["profile:devto:l1sec"].trust == 0.0
    discovered = {n.key for n in result.discovered()}
    assert "profile:keybase:l1sec" not in discovered
    assert "profile:devto:l1sec" not in discovered
    # Onaylı (kullanıcı verdiği) sayfa robots istisnasıyla, keşfedilen sayfa robots ile okunur.
    assert ("https://linktr.ee/l1sec", False) in fake.page_calls


@pytest.mark.asyncio
async def test_chain_respects_fetch_budget_and_depth():
    fake = _Fake()
    engine = _engine(fake, max_fetches=1)
    engine.add_anchor_url("https://github.com/l1sec")
    result = await engine.run()
    assert result.fetches == 1
    assert fake.page_calls == []

    fake = _Fake()
    engine = _engine(fake, max_depth=0)
    engine.add_anchor_url("https://github.com/l1sec")
    result = await engine.run()
    assert fake.page_calls == []  # derinlik 1 düğümleri genişletilmez
    assert any(n.key == "profile:linktr.ee:l1sec" for n in result.discovered())


@pytest.mark.asyncio
async def test_unverified_handle_guess_is_not_persisted_without_corroboration():
    async def intel(network: str, handle: str):
        if network == "gitlab":
            return _intel("gitlab", handle, name="")  # var ama kanıt yok
        return None

    async def page(url: str, trusted: bool):
        return None

    engine = ChainEngine(intel_fetcher=intel, page_fetcher=page, names=[])
    engine.add_anchor_handle("l1sec")
    result = await engine.run()
    gitlab = next(n for n in result.nodes if n.key == "profile:gitlab:l1sec")
    # Yalnız kullanıcı adı eşleşmesi: düşük güven, genişletilmez.
    assert gitlab.trust == pytest.approx(0.5)
    assert gitlab.trust < EXPAND_MIN_TRUST


@pytest.mark.asyncio
async def test_name_match_needs_two_tokens():
    async def intel(network: str, handle: str):
        if network == "gitlab":
            return _intel("gitlab", handle, name="Kerem Başka")
        if network == "github":
            return _intel("github", handle, name="Kerem Demir")
        return None

    async def page(url: str, trusted: bool):
        return None

    engine = ChainEngine(intel_fetcher=intel, page_fetcher=page, names=["Kerem Demir"])
    engine.add_anchor_handle("l1sec")
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    assert nodes["profile:github:l1sec"].trust > 0.8  # ad + soyad eşleşti
    assert nodes["profile:gitlab:l1sec"].trust < 0.4  # yalnız ad eşleşmesi yetmez


@pytest.fixture
async def db_factory():
    from showis.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_case_chain_persists_proposed_items_and_relations(db_factory):
    async with db_factory() as db:
        case = Case(
            id="case-chain",
            name="Zincir",
            purpose="Herkese açık kimlik zinciri",
            legal_basis="public_sources",
        )
        db.add(case)
        await db.flush()
        anchor, _ = await create_item(
            db,
            case_id=case.id,
            kind="url",
            label="GitHub",
            value="https://github.com/l1sec",
            source="user",
        )
        await db.commit()

        fake = _Fake()
        engine = _engine(fake)
        result = await run_case_chain(db, case=case, engine=engine, status="proposed")
        await db.commit()

        items = (
            (await db.execute(select(PoolItem).where(PoolItem.case_id == case.id))).scalars().all()
        )
        by_value = {item.value: item for item in items}
        assert anchor.status == "confirmed"
        linktree = by_value["https://linktr.ee/l1sec"]
        # Invariant: AI/zincir confirmed yazamaz.
        assert {item.status for item in items if item.id != anchor.id} <= {"proposed"}
        assert linktree.confidence >= 0.99
        assert linktree.provenance["method"] == "chain"
        assert linktree.provenance["chain"] == ["github:l1sec"]
        assert "l1sec.ig" in by_value  # kullanıcı adı kaydı da yazılır
        assert "keybase" not in " ".join(item.label for item in items).lower()

        relations = (
            (await db.execute(select(PoolRelation).where(PoolRelation.case_id == case.id)))
            .scalars()
            .all()
        )
        types = {(r.src_item_id, r.dst_item_id, r.type) for r in relations}
        assert (anchor.id, linktree.id, "linked_from") in types
        # Karşılıklı bağlantı: linktree -> github geri bağlantısı same_person olur.
        assert any(r.type == "same_person" for r in relations)
        assert all(r.status in {"proposed", "candidate"} for r in relations)
        assert result.summary()["created"] >= 5
        # Doğrulanmış hesabın konum/kurum alanları bağlam kaydı olur.
        location = by_value["İstanbul"]
        assert location.kind == "location" and location.status == "proposed"
        assert by_value["acme"].kind == "organization"
        assert any(r.type == "located_in" and r.dst_item_id == location.id for r in relations)


@pytest.mark.asyncio
async def test_anchors_from_items_handles_platformless_username(db_factory):
    async with db_factory() as db:
        case = Case(id="c2", name="c", purpose="p", legal_basis="public_sources")
        db.add(case)
        await db.flush()
        item, _ = await create_item(
            db, case_id=case.id, kind="username", label="nick", value="l1sec", source="user"
        )
        fake = _Fake()
        engine = _engine(fake)
        assert anchors_from_items(engine, [item]) == 1
        networks = {n.network for n in engine.nodes.values()}
        assert networks == {"github", "gitlab", "keybase", "devto", "codeberg"}
        assert not any(n.anchor for n in engine.nodes.values())


@pytest.mark.asyncio
async def test_research_chain_phase_seeds_identity_and_verifies_leads(db_factory, monkeypatch):
    import time

    from showis.db.models import ResearchSession
    from showis.services import chain as chain_mod
    from showis.services import research as research_mod
    from showis.services.research_playbook import build_identity_profile

    fake = _Fake()

    async def fake_build_engine(db, *, max_fetches=30, **kwargs):
        return ChainEngine(
            intel_fetcher=fake.intel,
            page_fetcher=fake.page,
            email_fetcher=None,
            max_fetches=max_fetches,
        )

    monkeypatch.setattr(chain_mod, "build_engine", fake_build_engine)
    emitted: list[str] = []

    async def fake_emit(session_id, item, source_url=None):
        emitted.append(item.value)

    monkeypatch.setattr(research_mod, "emit_proposal", fake_emit)

    async with db_factory() as db:
        case = Case(id="c3", name="c", purpose="p", legal_basis="public_sources")
        db.add(case)
        await db.flush()
        await create_item(
            db,
            case_id=case.id,
            kind="url",
            label="GitHub",
            value="https://github.com/l1sec",
            source="user",
        )
        session = ResearchSession(
            id="sess-chain", case_id=case.id, title="t", question="q", mode="research"
        )
        db.add(session)
        await db.commit()
        runtime = research_mod.ResearchRuntime(
            db=db,
            session=session,
            case=case,
            target=None,
            client=None,  # type: ignore[arg-type]
            identity=build_identity_profile(first_name="Kerem", last_name="Demir"),
            pool_items=[],
            stats={"depth": "standard"},
            started=time.monotonic(),
        )
        created = await research_mod._run_chain_phase(runtime)
        assert created >= 5
        assert "https://linktr.ee/l1sec" in emitted
        # Doğrulanan kullanıcı adları ve e-posta sonraki sorgulara tohum olur.
        assert "l1sec.ig" in runtime.identity.handles
        assert "kerem@l1sec.dev" in runtime.identity.emails
        assert "l1sec_tw" in runtime.stats["discovered_handles"]
        assert "https://linktr.ee/l1sec" in runtime.page_seen
        assert "linktr.ee:l1sec" in runtime.chain_text
        assert runtime.stats["chain"]["high_confidence"] >= 4
        # Zincir tohumlarından öncelikli deterministik sorgular.
        texts = [q.text for q in runtime.chain_queries]
        assert '"l1sec_tw"' in texts
        assert '"l1sec_tw" "Kerem Demir"' in texts
        assert '"kerem@l1sec.dev"' in texts

        # Dalga sonrası: araştırmanın bulduğu GitLab profili zincirle doğrulanır.
        lead, _ = await create_item(
            db,
            case_id=case.id,
            kind="url",
            label="gitlab arama sonucu",
            value="https://gitlab.com/l1sec",
            source="ai",
            status="proposed",
            confidence=0.55,
        )
        dead, _ = await create_item(
            db,
            case_id=case.id,
            kind="url",
            label="dev.to arama sonucu",
            value="https://dev.to/yokhesap",
            source="ai",
            status="proposed",
            confidence=0.5,
        )
        await db.commit()
        await research_mod._run_chain_phase(runtime, verify_leads=True)
        await db.refresh(lead)
        await db.refresh(dead)
        assert lead.confidence >= 0.9
        assert dead.confidence <= 0.1
        assert dead.attributes.get("chain_missing") is True


def test_name_tokens_keep_word_boundaries_and_fold_turkish():
    from showis.services.chain import _fold, _name_tokens

    assert _name_tokens("Kerem Demir") == {"kerem", "demir"}
    assert _name_tokens("Çağrı Öztürk") == {"cagri", "ozturk"}
    assert _fold("Ali-Veli  YILMAZ") == "ali veli yilmaz"


def test_profile_identity_fediverse():
    fedi = profile_identity("https://infosec.exchange/@Ali")
    assert fedi is not None and fedi[:2] == ("mastodon", "ali@infosec.exchange")
    assert profile_identity("https://mastodon.social/@Gargron")[:2] == (
        "mastodon",
        "gargron@mastodon.social",
    )
    assert profile_identity("https://example.com/@ali/123") is None


@pytest.mark.asyncio
async def test_keybase_reverse_lookup_links_walled_account():
    """X profili okunamaz; ama onu kanıtlamış Keybase hesabı ters aramayla bulunur."""

    async def intel(network: str, handle: str):
        if (network, handle) == ("github", "l1sec"):
            return _intel("github", "l1sec", links=[("https://x.com/l1sec_tw", "owner")])
        if (network, handle) == ("keybase", "kerem"):
            return _intel(
                "keybase",
                "kerem",
                name="Kerem Demir",
                links=[("https://x.com/l1sec_tw", "proof"), ("https://kerem.dev", "proof")],
            )
        return None

    async def page(url: str, trusted: bool):
        return None

    calls: list[tuple[str, str]] = []

    async def reverse(service: str, name: str):
        calls.append((service, name))
        return ["kerem"] if (service, name) == ("twitter", "l1sec_tw") else []

    engine = ChainEngine(
        intel_fetcher=intel,
        page_fetcher=page,
        reverse_fetcher=reverse,
        names=["Kerem Demir"],
        probe_handles=False,
    )
    engine.add_anchor_url("https://github.com/l1sec")
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    assert ("github", "l1sec") in calls and ("twitter", "l1sec_tw") in calls
    keybase = nodes["profile:keybase:kerem"]
    assert keybase.trust > 0.95
    # Keybase kanıtı X hesabına geri bağlanır; kişisel alan adı zincire girer.
    assert any(e.relation == "backlink" for e in keybase.evidence)
    assert nodes["page:kerem.dev"].trust > 0.9


@pytest.mark.asyncio
async def test_organization_account_is_not_expanded():
    async def intel(network: str, handle: str):
        if (network, handle) == ("github", "l1sec"):
            return _intel("github", "l1sec", links=[("https://github.com/acme", "owner")])
        if (network, handle) == ("github", "acme"):
            org = _intel("github", "acme", links=[("https://x.com/acme_official", "owner")])
            org.account_type = "organization"
            return org
        return None

    async def page(url: str, trusted: bool):
        return None

    engine = ChainEngine(intel_fetcher=intel, page_fetcher=page, probe_handles=False)
    engine.add_anchor_url("https://github.com/l1sec")
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    assert nodes["profile:github:acme"].trust < 0.4
    assert "profile:x:acme_official" not in nodes


def test_display_name_equal_to_handle_is_neutral():
    engine = ChainEngine(
        intel_fetcher=None,  # type: ignore[arg-type]
        page_fetcher=None,  # type: ignore[arg-type]
        names=["Eugen Rochko"],
    )
    engine.add_anchor_handle("gargron")
    node = engine.nodes["profile:gitlab:gargron"]
    engine._check_name(node, "Gargron")
    assert node.name_match is None and node.trust == 0.5
    engine._check_name(node, "Yael Wells")
    assert node.name_match is False and node.trust == 0.25


@pytest.mark.asyncio
async def test_rate_limited_api_falls_back_to_profile_page_rel_me():
    from showis_connectors.account_intel import AccountIntelError

    async def intel(network: str, handle: str):
        raise AccountIntelError("GitHub: hız sınırı; sonra tekrar deneyin")

    pages: list[str] = []

    async def page(url: str, trusted: bool):
        pages.append(url)
        if url == "https://github.com/l1sec":
            return PageData(
                url=url,
                rel_me=["https://x.com/l1sec_tw"],
                links=["https://github.com/features", "https://x.com/github"],
            )
        return None

    engine = ChainEngine(intel_fetcher=intel, page_fetcher=page, probe_handles=False)
    engine.add_anchor_url("https://github.com/l1sec")
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    assert pages == ["https://github.com/l1sec"]
    assert nodes["profile:x:l1sec_tw"].trust > 0.9
    # Platformun kendi hesabı (marka süzgeci) zincire girmez.
    assert "profile:x:github" not in nodes


@pytest.mark.asyncio
async def test_name_only_case_verifies_search_lead_by_name_and_expands():
    """Çapa yok (yalnız isim): aramadan gelen GitHub ad+soyadla doğrulanır, zincir sürer."""

    async def intel(network: str, handle: str):
        if (network, handle) == ("github", "kdemir"):
            return _intel(
                "github",
                "kdemir",
                name="Kerem Demir",
                links=[("https://linktr.ee/kdemir", "owner")],
            )
        if (network, handle) == ("github", "kerem"):
            return _intel("github", "kerem", name="Kerem Yıldız")
        return None

    async def page(url: str, trusted: bool):
        if url == "https://linktr.ee/kdemir":
            return PageData(url=url, links=["https://www.instagram.com/kdemir.ig/"])
        return None

    engine = ChainEngine(
        intel_fetcher=intel, page_fetcher=page, names=["Kerem Demir"], probe_handles=False
    )
    engine.add_lead("https://github.com/kdemir", 0.55, note="arama")
    engine.add_lead("https://github.com/kerem", 0.62, note="arama")
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    assert nodes["profile:github:kdemir"].trust > 0.8
    assert nodes["profile:instagram:kdemir.ig"].trust > 0.7
    # Aynı ad, farklı soyad: arama sonucu güveni yarıya iner.
    assert nodes["profile:github:kerem"].trust < 0.35


@pytest.mark.asyncio
async def test_personal_site_colleague_account_is_down_weighted():
    """Kişisel site iş arkadaşının X hesabına bağlanır; sahibin X'i zaten biliniyor."""

    async def intel(network: str, handle: str):
        if (network, handle) == ("github", "l1sec"):
            return _intel(
                "github",
                "l1sec",
                links=[("https://l1sec.dev", "owner"), ("https://x.com/l1sec_tw", "owner")],
            )
        return None

    async def page(url: str, trusted: bool):
        if url == "https://l1sec.dev":
            return PageData(
                url=url,
                links=["https://x.com/meslektas", "https://www.linkedin.com/in/l1sec"],
            )
        return None

    engine = ChainEngine(intel_fetcher=intel, page_fetcher=page, probe_handles=False)
    engine.add_anchor_url("https://github.com/l1sec")
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    discovered = {n.key for n in result.discovered()}
    assert nodes["profile:x:meslektas"].trust < 0.4
    assert "profile:x:meslektas" not in discovered
    # Kimliğe benzeyen ve ağda ilk olan hesap korunur.
    assert nodes["profile:linkedin:l1sec"].trust > 0.75


@pytest.mark.asyncio
async def test_search_lead_alone_is_not_expanded_or_persisted():
    async def intel(network: str, handle: str):
        return _intel("github", handle, name="", links=[("https://x.com/baskasi", "owner")])

    async def page(url: str, trusted: bool):
        return None

    engine = ChainEngine(intel_fetcher=intel, page_fetcher=page, names=["Kerem Demir"])
    engine.add_lead("https://github.com/rastgele", 0.62)
    result = await engine.run()
    nodes = {n.key: n for n in result.nodes}
    assert nodes["profile:github:rastgele"].trust <= 0.55
    assert "profile:x:baskasi" not in nodes
    assert result.discovered() == []


@pytest.mark.asyncio
async def test_research_page_extraction_ignores_strangers_on_unrelated_pages(db_factory):
    import time

    from showis.db.models import ResearchSession
    from showis.services import research as research_mod
    from showis.services.research_playbook import ProfileRef, build_identity_profile
    from showis_contracts import Platform

    async with db_factory() as db:
        case = Case(id="c5", name="c", purpose="p", legal_basis="public_sources")
        session = ResearchSession(id="s5", case_id="c5", title="t", question="q", mode="research")
        db.add_all([case, session])
        await db.commit()
        runtime = research_mod.ResearchRuntime(
            db=db,
            session=session,
            case=case,
            target=None,
            client=None,  # type: ignore[arg-type]
            identity=build_identity_profile(
                first_name="Ben", last_name="Halpern", seed_usernames=["benhalpern"]
            ),
            pool_items=[],
            stats={"depth": "quick"},
            started=time.monotonic(),
        )

        def ref(name: str) -> ProfileRef:
            return ProfileRef(Platform.GITHUB, name, f"https://github.com/{name}")

        org_page = research_mod.FetchResult(
            url="https://github.com/virtual-coffee",
            title="Virtual Coffee",
            text="Virtual Coffee community members and projects",
            profiles=[ref("sophiabrandt"), ref("bendhalpern")],
            emails=["hello@virtualcoffee.io"],
            phones=["265 279 21 3"],
        )
        runtime.process_page(org_page)
        values = {(d.kind, d.value): d.confidence for d in runtime.drafts}
        assert ("username", "sophiabrandt") not in values
        assert values[("username", "bendhalpern")] == 0.6
        assert not any(kind in {"email", "phone"} for kind, _ in values)
        # Yabancı profil sayfası okunmak üzere kuyruğa da girmez.
        assert "https://github.com/sophiabrandt" not in runtime.pending_url_set

        runtime.drafts.clear()
        runtime.finding_keys.clear()
        own = research_mod.FetchResult(
            url="https://benhalpern.com",
            title="Ben Halpern",
            text="Hi, I am Ben Halpern",
            profiles=[ref("someproject")],
            emails=["hi@benhalpern.com"],
            phones=["+1 555 010 9999"],
        )
        runtime.process_page(own, trusted=True)
        kinds = {d.kind for d in runtime.drafts}
        assert {"username", "email", "phone"} <= kinds


@pytest.mark.asyncio
async def test_fallback_report_ranks_trusted_and_hides_low_confidence(db_factory):
    import time

    from showis.db.models import ResearchSession
    from showis.services import research as research_mod
    from showis.services.research_playbook import build_identity_profile

    async with db_factory() as db:
        case = Case(id="c6", name="c", purpose="p", legal_basis="public_sources")
        session = ResearchSession(id="s6", case_id="c6", title="t", question="q?", mode="research")
        db.add_all([case, session])
        await db.commit()
        runtime = research_mod.ResearchRuntime(
            db=db,
            session=session,
            case=case,
            target=None,
            client=None,  # type: ignore[arg-type]
            identity=build_identity_profile(first_name="Ben", last_name="Halpern"),
            pool_items=[],
            stats={"depth": "quick"},
            started=time.monotonic(),
        )
        runtime.chain_text = "- devto:ben güven=0.99 kanıt=owner zincir=devto:ben <- github:x"
        rows = [
            {
                "kind": "username",
                "value": "yabanci",
                "label": "",
                "status": "proposed",
                "confidence": 0.35,
                "source": "ai",
                "attributes": {},
            },
            {
                "kind": "username",
                "value": "benhalpern",
                "label": "",
                "status": "confirmed",
                "confidence": 0.0,
                "source": "user",
                "attributes": {},
            },
            {
                "kind": "username",
                "value": "bendhalpern",
                "label": "",
                "status": "proposed",
                "confidence": 0.95,
                "source": "ai",
                "attributes": {},
            },
        ]
        text = research_mod._fallback_report(
            runtime, pool_rows=rows, bio_markdown="", stop_reason="bitti"
        )
    assert "## Doğrulanmış kimlik zinciri" in text and "devto:ben" in text
    assert "yabanci" not in text and "1 düşük güvenli aday" in text
    assert text.index("benhalpern (onaylı)") < text.index("bendhalpern")
    assert "güven %95" in text


@pytest.mark.asyncio
async def test_watch_alerts_new_chain_account_once(db_factory):
    from showis.services.watch import check_chain_accounts

    async with db_factory() as db:
        case = Case(id="c7", name="c", purpose="p", legal_basis="public_sources")
        db.add(case)
        await db.flush()
        await create_item(
            db,
            case_id="c7",
            kind="url",
            label="gh",
            value="https://github.com/l1sec",
            source="user",
        )
        # Zincir daha önce kullanılmamışsa izleme sessizdir.
        assert await check_chain_accounts(db, case, engine=_engine(_Fake())) == []
        await create_item(
            db,
            case_id="c7",
            kind="url",
            label="linktree",
            value="https://linktr.ee/l1sec",
            source="ai",
            status="proposed",
            provenance={"method": "chain"},
        )
        await db.commit()
        alerts = await check_chain_accounts(db, case, engine=_engine(_Fake()))
        titles = " ".join(alert.title for alert in alerts)
        assert "linktr.ee" not in titles  # havuzda zaten var
        assert "x:l1sec_tw" in titles
        await db.commit()
        again = await check_chain_accounts(db, case, engine=_engine(_Fake()))
        assert again == []  # dedupe

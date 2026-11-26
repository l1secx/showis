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


def _intel(platform: str, handle: str, *, name: str = "", links=(), emails=()) -> AccountIntel:
    intel = AccountIntel(
        platform=platform,
        handle=handle,
        profile_url=f"https://{platform}.com/{handle}",
        display_name=name,
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
        "gitlab", "l1sec", name="", links=[("https://linktr.ee/l1sec", "owner")]
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
            await db.execute(select(PoolItem).where(PoolItem.case_id == case.id))
        ).scalars().all()
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
            await db.execute(select(PoolRelation).where(PoolRelation.case_id == case.id))
        ).scalars().all()
        types = {(r.src_item_id, r.dst_item_id, r.type) for r in relations}
        assert (anchor.id, linktree.id, "linked_from") in types
        # Karşılıklı bağlantı: linktree -> github geri bağlantısı same_person olur.
        assert any(r.type == "same_person" for r in relations)
        assert all(r.status in {"proposed", "candidate"} for r in relations)
        assert result.summary()["created"] >= 5


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
        assert networks == {"github", "gitlab", "keybase", "devto"}
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

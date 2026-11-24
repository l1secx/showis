"""Havuz ilişki grafı, korelasyon kuralları, pivotlar ve hipotezler."""

from __future__ import annotations

import httpx
import pytest
import respx
import test_research_workspace as _workspace
from showis.db.models import PoolItem, PoolRelation
from showis.services.pool import create_item
from showis.services.reasoning.correlate import (
    build_timeline,
    find_contradictions,
    run_correlation,
)
from showis.services.reasoning.graph import RelationError, upsert_relation
from showis.services.reasoning.hypotheses import HypothesisNarrative, generate_hypotheses
from showis.services.reasoning.scoring import combine, score
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory
_seed_case = _workspace._seed_case


async def _item(session, case_id, kind, value, **kw):
    item, _ = await create_item(
        session,
        case_id=case_id,
        kind=kind,
        label=kw.pop("label", value),
        value=value,
        source=kw.pop("source", "ai"),
        status=kw.pop("status", "proposed"),
        **kw,
    )
    await session.flush()
    return item


def test_scoring_does_not_compound_same_source_and_contradiction_lowers():
    one = [{"rule": "r", "source": "s", "weight": 2.0}]
    twice = one + [{"rule": "r", "source": "s", "weight": 1.0}]
    assert combine(twice) == 2.0
    two_sources = one + [{"rule": "r2", "source": "s2", "weight": 2.0}]
    assert score(two_sources) > score(one) > score([])
    assert score(two_sources, [{"rule": "c", "source": "x", "weight": 3.0}]) < score(two_sources)


@pytest.mark.asyncio
async def test_relation_invariants(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        a = await _item(db, case_id, "username", "keremboz", attributes={"platform": "github"})
        b = await _item(db, case_id, "email", "keremboz@example.com")
        for source in ("ai", "rule", "pivot"):
            row, _ = await upsert_relation(
                db,
                case_id=case_id,
                src_item_id=a.id,
                dst_item_id=b.id,
                type="uses_email",
                status="confirmed",
                source=source,
            )
            assert row.status == "proposed"
            await db.delete(row)
            await db.flush()
        user_row, _ = await upsert_relation(
            db,
            case_id=case_id,
            src_item_id=a.id,
            dst_item_id=b.id,
            type="uses_email",
            status="confirmed",
            source="user",
        )
        assert user_row.status == "confirmed"
        # Kural yeniden çalışınca kullanıcı kararı ezilmez.
        again, created = await upsert_relation(
            db,
            case_id=case_id,
            src_item_id=a.id,
            dst_item_id=b.id,
            type="uses_email",
            status="candidate",
            source="rule",
        )
        assert not created and again.status == "confirmed"
        with pytest.raises(RelationError):
            await upsert_relation(
                db, case_id=case_id, src_item_id=a.id, dst_item_id=a.id, type="same_person"
            )
        with pytest.raises(RelationError):
            await upsert_relation(
                db, case_id=case_id, src_item_id=a.id, dst_item_id="yok", type="same_person"
            )
        with pytest.raises(RelationError):
            await upsert_relation(
                db, case_id=case_id, src_item_id=a.id, dst_item_id=b.id, type="uydurma"
            )
        # Simetrik tür tek kenar olur.
        r1, _ = await upsert_relation(
            db, case_id=case_id, src_item_id=a.id, dst_item_id=b.id, type="same_person"
        )
        r2, created = await upsert_relation(
            db, case_id=case_id, src_item_id=b.id, dst_item_id=a.id, type="same_person"
        )
        assert r1.id == r2.id and not created


async def _correlation_fixture(db, case_id):
    gh = await _item(
        db,
        case_id,
        "username",
        "keremboz",
        label="github keremboz",
        attributes={"platform": "github"},
        provenance={"url": "https://linktr.ee/keremboz"},
    )
    x = await _item(
        db,
        case_id,
        "url",
        "https://x.com/keremboz",
        label="X",
        attributes={"platform": "x", "username": "keremboz"},
        provenance={"url": "https://linktr.ee/keremboz"},
    )
    return gh, x


@pytest.mark.asyncio
async def test_correlation_promotes_only_with_two_independent_sources(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        gh = await _item(
            db, case_id, "username", "keremboz", label="gh", attributes={"platform": "github"}
        )
        x = await _item(
            db,
            case_id,
            "url",
            "https://x.com/keremboz",
            label="X",
            attributes={"platform": "x", "username": "keremboz"},
        )
        await db.commit()
        first = await run_correlation(db, case_id)
        rels = (await db.execute(select(PoolRelation))).scalars().all()
        same = [r for r in rels if r.type == "same_person"]
        assert len(same) == 1 and same[0].status == "proposed"  # tek kaynak
        assert first["relations_created"] >= 1

        # İkinci bağımsız kanıt: aynı bağlantı toplayıcıda listeleniyor.
        for item in (gh, x):
            item.provenance = {"url": "https://linktr.ee/keremboz"}
        await db.commit()
        second = await run_correlation(db, case_id)
        await db.refresh(same[0])
        assert same[0].status == "candidate"
        assert second["promoted"] >= 1
        assert {e["rule"] for e in same[0].evidence} >= {"same_handle", "shared_aggregator"}
        # Tekrar çalıştırma yeni ilişki üretmez (idempotent).
        third = await run_correlation(db, case_id)
        assert third["relations_created"] == 0
        statuses = {r.status for r in (await db.execute(select(PoolRelation))).scalars()}
        assert "confirmed" not in statuses


@pytest.mark.asyncio
async def test_avatar_and_email_rules(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        a = await _item(
            db, case_id, "url", "https://a.example/p", attributes={"avatar_dhash": "ff00"}
        )
        b = await _item(
            db, case_id, "url", "https://b.example/p", attributes={"avatar_dhash": "ff00"}
        )
        user = await _item(db, case_id, "username", "keremboz2", attributes={"platform": "github"})
        mail = await _item(db, case_id, "email", "keremboz2@example.com")
        await db.commit()
        await run_correlation(db, case_id)
        rels = (await db.execute(select(PoolRelation))).scalars().all()
        kinds = {(r.type, tuple(sorted((r.src_item_id, r.dst_item_id)))) for r in rels}
        assert ("same_person", tuple(sorted((a.id, b.id)))) in kinds
        assert ("uses_email", tuple(sorted((user.id, mail.id)))) in kinds


@pytest.mark.asyncio
async def test_contradictions_and_timeline(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        person = await _item(db, case_id, "name", "Kerem Boz")
        ank = await _item(db, case_id, "location", "Ankara")
        izm = await _item(db, case_id, "location", "İzmir")
        ev1 = await _item(
            db, case_id, "event", "Konferans", attributes={"date": "2024-05-01", "place": "Ankara"}
        )
        ev2 = await _item(
            db, case_id, "event", "Toplantı", attributes={"date": "2024-05-01", "place": "Berlin"}
        )
        for target, rtype in (
            (ank, "located_in"),
            (izm, "located_in"),
            (ev1, "linked_from"),
            (ev2, "linked_from"),
        ):
            await upsert_relation(
                db, case_id=case_id, src_item_id=person.id, dst_item_id=target.id, type=rtype
            )
        await db.commit()
        items = list((await db.execute(select(PoolItem))).scalars())
        rels = list((await db.execute(select(PoolRelation))).scalars())
        found = find_contradictions(items, rels)
        assert {c.kind for c in found} == {"location_conflict", "timeline_conflict"}
        assert [row["date"] for row in build_timeline(items)] == ["2024-05-01", "2024-05-01"]

        result = await run_correlation(db, case_id)
        assert len(result["contradictions"]) == 2
        contra = (
            (await db.execute(select(PoolRelation).where(PoolRelation.type == "contradicts")))
            .scalars()
            .all()
        )
        assert len(contra) == 2 and all(r.status == "proposed" for r in contra)


class _FakeNarrator:
    """Native araç yolu olmayan sahte LLM: güveni şişirmeye çalışır."""

    async def complete(self, messages, schema=None, **kwargs):
        return HypothesisNarrative.model_validate(
            {
                "rationale": "İki hesap aynı toplayıcıda.",
                "next_checks": ["Avatarı karşılaştır"],
                "confidence": 0.999,
            }
        )


@pytest.mark.asyncio
async def test_hypothesis_score_comes_from_evidence_not_llm(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        gh, x = await _correlation_fixture(db, case_id)
        await db.commit()
        await run_correlation(db, case_id)
        plain = await generate_hypotheses(db, case_id=case_id)
        assert len(plain) == 1 and plain[0].status == "proposed"
        base = plain[0].confidence
        assert 0.0 < base < 0.999
        assert plain[0].source == "rule" and plain[0].next_checks

        with_llm = await generate_hypotheses(db, case_id=case_id, client=_FakeNarrator())
        assert with_llm[0].id == plain[0].id  # aynı küme → aynı hipotez güncellenir
        assert with_llm[0].confidence == base  # LLM güveni değiştiremez
        assert "toplayıcıda" in with_llm[0].rationale
        assert "Avatarı karşılaştır" in with_llm[0].next_checks

        # Kullanıcı kararı yeniden üretimde korunur.
        with_llm[0].status = "confirmed"
        await db.commit()
        again = await generate_hypotheses(db, case_id=case_id)
        assert again[0].status == "confirmed"


@pytest.mark.asyncio
async def test_reasoning_api_flow(db_factory, client):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        gh, x = await _correlation_fixture(db, case_id)
        await db.commit()
        gh_id, x_id = gh.id, x.id

    corr = await client.post(f"/api/v1/cases/{case_id}/reasoning/correlate")
    assert corr.status_code == 200, corr.text
    rels = (await client.get(f"/api/v1/cases/{case_id}/reasoning/relations")).json()["items"]
    assert any(r["type"] == "same_person" for r in rels)

    graph = (await client.get(f"/api/v1/cases/{case_id}/reasoning/graph")).json()
    assert graph["clusters"] and set(graph["clusters"][0]["item_ids"]) >= {gh_id, x_id}
    assert "same_person" in graph["summary"]

    gen = await client.post(f"/api/v1/cases/{case_id}/reasoning/hypotheses/generate")
    assert gen.status_code == 200, gen.text
    hypo = gen.json()["items"][0]
    assert hypo["status"] == "proposed" and hypo["supporting"]

    # Kullanıcı onayı: yalnız PATCH ile confirmed olur.
    patched = await client.patch(
        f"/api/v1/cases/{case_id}/reasoning/hypotheses/{hypo['id']}", json={"status": "confirmed"}
    )
    assert patched.json()["status"] == "confirmed"
    bad = await client.patch(
        f"/api/v1/cases/{case_id}/reasoning/hypotheses/{hypo['id']}", json={"status": "x"}
    )
    assert bad.status_code == 422

    rel_id = next(r["id"] for r in rels if r["type"] == "same_person")
    confirmed = await client.patch(
        f"/api/v1/cases/{case_id}/reasoning/relations/{rel_id}", json={"status": "confirmed"}
    )
    assert confirmed.json()["status"] == "confirmed"

    # Kullanıcı elle ilişki ekler; öğe silinince ilişkiler de gider.
    created = await client.post(
        f"/api/v1/cases/{case_id}/reasoning/relations",
        json={"src_item_id": gh_id, "dst_item_id": x_id, "type": "alias_of"},
    )
    assert created.status_code == 201 and created.json()["status"] == "confirmed"
    deleted = await client.delete(f"/api/v1/cases/{case_id}/pool/{x_id}")
    assert deleted.status_code == 204
    after = (await client.get(f"/api/v1/cases/{case_id}/reasoning/relations")).json()["items"]
    assert after == []

    manual = await client.post(
        f"/api/v1/cases/{case_id}/reasoning/hypotheses",
        json={"statement": "Elle girilen hipotez metni", "relation_ids": []},
    )
    assert manual.status_code == 201 and manual.json()["source"] == "user"


@pytest.mark.asyncio
async def test_pivots_derive_proposed_items_and_relations(db_factory, client):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        mail = await _item(
            db, case_id, "email", "kerem.boz@acme-corp.example", source="user", status="confirmed"
        )
        gmail = await _item(
            db, case_id, "email", "someone@gmail.com", source="user", status="confirmed"
        )
        handle = await _item(
            db, case_id, "username", "keremboz3", source="user", status="confirmed"
        )
        name = await _item(db, case_id, "name", "Ayşe Yılmaz", source="user", status="confirmed")
        rejected = await _item(db, case_id, "email", "no@example.com", status="rejected")
        await db.commit()
        ids = {
            "mail": mail.id,
            "gmail": gmail.id,
            "handle": handle.id,
            "name": name.id,
            "rej": rejected.id,
        }

    listed = await client.get(f"/api/v1/cases/{case_id}/reasoning/items/{ids['mail']}/pivots")
    assert {p["id"] for p in listed.json()["items"]} >= {
        "email_username",
        "email_domain",
        "email_gravatar",
        "email_breach",
    }

    base = f"/api/v1/cases/{case_id}/reasoning/items"
    user = (await client.post(f"{base}/{ids['mail']}/pivots/email_username")).json()
    assert user["items"][0]["kind"] == "username" and user["items"][0]["status"] == "proposed"
    dom = (await client.post(f"{base}/{ids['mail']}/pivots/email_domain")).json()
    assert dom["items"][0]["value"] == "acme-corp.example"
    skip = (await client.post(f"{base}/{ids['gmail']}/pivots/email_domain")).json()
    assert skip["items"] == [] and skip["notes"]
    grav = (await client.post(f"{base}/{ids['mail']}/pivots/email_gravatar")).json()
    assert grav["items"][0]["value"].startswith("https://gravatar.com/")
    prof = (await client.post(f"{base}/{ids['handle']}/pivots/username_profiles")).json()
    assert len(prof["items"]) == 8 and all(i["status"] == "proposed" for i in prof["items"])
    names = (await client.post(f"{base}/{ids['name']}/pivots/name_usernames")).json()
    assert names["items"] and all(i["kind"] == "username" for i in names["items"])

    # Uygunsuz/reddedilmiş/bilinmeyen pivotlar 422.
    assert (await client.post(f"{base}/{ids['handle']}/pivots/email_domain")).status_code == 422
    assert (await client.post(f"{base}/{ids['rej']}/pivots/email_username")).status_code == 422
    assert (await client.post(f"{base}/{ids['mail']}/pivots/yok")).status_code == 422

    async with db_factory() as db:
        statuses = {
            i.status
            for i in (await db.execute(select(PoolItem).where(PoolItem.source == "scan"))).scalars()
        }
        rel_status = {r.status for r in (await db.execute(select(PoolRelation))).scalars()}
    assert statuses == {"proposed"} and rel_status == {"proposed"}


@pytest.mark.asyncio
async def test_breach_and_url_fetch_pivots(db_factory, client, monkeypatch):
    from showis.services import research as research_mod
    from showis.services.research_playbook import ProfileRef
    from showis_contracts import Platform

    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        mail = await _item(
            db, case_id, "email", "kerem@example.com", source="user", status="confirmed"
        )
        page = await _item(
            db, case_id, "url", "https://linktr.ee/keremboz", source="user", status="confirmed"
        )
        await db.commit()
        mail_id, page_id = mail.id, page.id

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://api\.xposedornot\.com/v1/breach-analytics.*").mock(
            return_value=httpx.Response(404)
        )
        mock.get(url__regex=r"https://cavalier\.hudsonrock\.com/.*").mock(
            return_value=httpx.Response(404)
        )
        out = await client.post(
            f"/api/v1/cases/{case_id}/reasoning/items/{mail_id}/pivots/email_breach"
        )
    assert out.status_code == 200 and "Sızıntı" in out.json()["notes"][0]

    seen: dict[str, bool] = {}

    async def fake_fetch(url, *, timeout=15.0, respect_robots=True):
        seen["robots"] = respect_robots
        return research_mod.FetchResult(
            url=url,
            title="Kerem",
            text="x" * 80,
            digest="ab" * 32,
            profiles=[
                ProfileRef(
                    platform=Platform.GITHUB,
                    identifier="keremboz",
                    url="https://github.com/keremboz",
                )
            ],
            emails=["iletisim@example.org"],
        )

    monkeypatch.setattr(research_mod, "_fetch_public_page", fake_fetch)
    out = await client.post(f"/api/v1/cases/{case_id}/reasoning/items/{page_id}/pivots/url_fetch")
    assert out.status_code == 200, out.text
    assert seen["robots"] is False  # kullanıcının verdiği URL: robots istisnası
    kinds = {i["kind"] for i in out.json()["items"]}
    assert {"username", "url", "email"} <= kinds
    async with db_factory() as db:
        row = (await db.execute(select(PoolItem).where(PoolItem.id == page_id))).scalar_one()
    assert row.attributes["snapshot"]["sha256"] == "ab" * 32

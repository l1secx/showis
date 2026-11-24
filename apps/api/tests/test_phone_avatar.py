"""Faz 5: telefon pivotu/normalizasyonu ve avatar dHash'in havuza yayılması."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import test_pool_relations as _rel
import test_research_workspace as _workspace
from showis.services.phone import parse_phone, search_variants
from showis.services.reasoning import avatars as avatars_mod
from showis.services.reasoning.correlate import run_correlation
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory
_seed_case = _workspace._seed_case
_item = _rel._item


@pytest.mark.parametrize(
    ("raw", "e164", "country"),
    [
        ("0532 123 45 67", "+905321234567", "TR"),
        ("+90 (532) 123-45-67", "+905321234567", "TR"),
        ("00905321234567", "+905321234567", "TR"),
        ("532 123 45 67", "+905321234567", "TR"),
        ("+44 20 7946 0958", "+442079460958", "GB"),
        ("+1 202 555 0143", "+12025550143", "US"),
    ],
)
def test_parse_phone_normalizes(raw, e164, country):
    info = parse_phone(raw)
    assert info is not None and info.e164 == e164 and info.country == country


@pytest.mark.parametrize("raw", ["", "abc", "12345", "+0123456789", "5321234", "+90 532 12x 45 67"])
def test_parse_phone_rejects_invalid(raw):
    assert parse_phone(raw) is None


def test_search_variants_turkish_formats():
    info = parse_phone("+905321234567")
    assert info is not None
    assert search_variants(info) == ["+905321234567", "+90 532 123 45 67", "0532 123 45 67"]


@pytest.mark.asyncio
async def test_phone_item_gets_e164_attribute_and_dedupes_by_pivot(db_factory, client):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        phone = await _item(db, case_id, "phone", "0532 123 45 67", source="user")
        await db.commit()
        phone_id = phone.id
        assert phone.attributes["phone_e164"] == "+905321234567"

    base = f"/api/v1/cases/{case_id}/reasoning/items/{phone_id}/pivots"
    listed = {p["id"] for p in (await client.get(base)).json()["items"]}
    assert {"phone_normalize", "phone_search"} <= listed
    out = (await client.post(f"{base}/phone_normalize")).json()
    assert "+905321234567" in out["notes"][0] and "Türkiye" in out["notes"][0]


@pytest.mark.asyncio
async def test_phone_search_adds_proposed_urls_only(db_factory, client, monkeypatch):
    case_id = await _seed_case(db_factory)
    queries: list[str] = []

    class _Provider:
        async def search(self, query, *, limit=20, site=None):
            queries.append(query)
            return [
                SimpleNamespace(title="İlan", url="https://ilan.example/1", snippet="0532 …"),
                SimpleNamespace(title="Kötü", url="javascript:alert(1)", snippet=""),
            ]

    async def fake_provider(db):
        return _Provider()

    monkeypatch.setattr("showis.services.connectors_factory.build_search_provider", fake_provider)
    async with db_factory() as db:
        phone = await _item(db, case_id, "phone", "+905321234567", source="user")
        await db.commit()
        phone_id = phone.id
    out = (
        await client.post(f"/api/v1/cases/{case_id}/reasoning/items/{phone_id}/pivots/phone_search")
    ).json()
    assert queries == ['"+905321234567"', '"+90 532 123 45 67"']
    assert [i["value"] for i in out["items"]] == ["https://ilan.example/1"]
    assert out["items"][0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_same_phone_rule_links_items(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        await _item(db, case_id, "phone", "0532 123 45 67", source="user", status="confirmed")
        await _item(
            db, case_id, "url", "https://ilan.example/1", attributes={"phone_e164": "+905321234567"}
        )
        result = await run_correlation(db, case_id)
        assert result["matches"] >= 1


@pytest.mark.asyncio
async def test_hash_pool_avatars_and_near_match_correlation(db_factory, monkeypatch):
    case_id = await _seed_case(db_factory)
    hashes = {
        "https://img.example/a.png": ("a" * 64, "f0f0f0f0f0f0f0f0"),
        "https://img.example/b.png": ("b" * 64, "f0f0f0f0f0f0f0f1"),  # Hamming 1
        "https://img.example/c.png": ("c" * 64, "0f0f0f0f0f0f0f0f"),  # uzak
        "https://img.example/blank.png": ("d" * 64, "0000000000000000"),  # düz renk
        "https://img.example/blank2.png": ("e" * 64, "0000000000000000"),
    }
    fetched: list[str] = []

    async def fake_fetch(url, *args, **kwargs):
        fetched.append(url)
        return hashes.get(url, (None, None))

    monkeypatch.setattr(avatars_mod, "fetch_avatar_hashes", fake_fetch)
    async with db_factory() as db:
        for index, url in enumerate([*hashes, "https://img.example/404.png"]):
            await _item(
                db,
                case_id,
                "url",
                f"https://site{index}.example/u",
                attributes={"platform": f"p{index}", "avatar_url": url},
            )
        await db.commit()
        first = await avatars_mod.hash_pool_avatars(db, case_id)
        assert first == {"candidates": 6, "hashed": 5}
        # Başarısız (404) öğe yeniden denenmez; özetlenenler atlanır.
        second = await avatars_mod.hash_pool_avatars(db, case_id)
        assert second == {"candidates": 0, "hashed": 0}
        assert len(fetched) == 6

        result = await run_correlation(db, case_id)
        assert result["matches"] == 1  # yalnız a↔b; düz renk ve uzak avatar eşleşmez
        from showis.db.models import PoolRelation

        rels = (await db.execute(select(PoolRelation))).scalars().all()
        assert len(rels) == 1 and rels[0].type == "same_person" and rels[0].status == "proposed"
        assert "Hamming 1" in rels[0].evidence[0]["note"]


@pytest.mark.asyncio
async def test_avatar_endpoint_and_pivot_reject_bad_urls(db_factory, client, monkeypatch):
    case_id = await _seed_case(db_factory)

    async def fake_fetch(url, *args, **kwargs):
        return (None, None)

    monkeypatch.setattr(avatars_mod, "fetch_avatar_hashes", fake_fetch)
    async with db_factory() as db:
        item = await _item(
            db,
            case_id,
            "url",
            "https://x.example/u",
            attributes={"avatar_url": "file:///etc/passwd"},
        )
        bare = await _item(db, case_id, "url", "https://y.example/u")
        await db.commit()
        item_id, bare_id = item.id, bare.id
    base = f"/api/v1/cases/{case_id}/reasoning"
    assert (await client.post(f"{base}/avatars/hash")).json() == {"candidates": 1, "hashed": 0}
    none = (await client.post(f"{base}/items/{bare_id}/pivots/avatar_hash")).json()
    assert "avatar_url yok" in none["notes"][0]
    bad = (await client.post(f"{base}/items/{item_id}/pivots/avatar_hash")).json()
    assert "indirilemedi" in bad["notes"][0]
    assert (await client.post("/api/v1/cases/yok/reasoning/avatars/hash")).status_code == 404

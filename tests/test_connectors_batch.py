"""T-308: Stack Overflow / Bitbucket / Mastodon / Codeberg connector testleri.

Tum testler `respx` ile sahte HTTP kullanir — **gercek ag istegi yok**.
"""

from __future__ import annotations

import pytest
import respx
from showis_connectors import (
    BitbucketConnector,
    CodebergConnector,
    MastodonConnector,
    StackOverflowConnector,
)
from showis_contracts import Platform, RiskTier, ScanContext, Seed, Target, TaskSpec
from showis_engine.http import HttpClient


def _conn(cls):
    """Ag/SSRF/limiter yan etkisi olmayan connector ornegi."""
    return cls(http_client=HttpClient(enable_ssrf_guard=False, use_host_limiter=False))


def _ctx(seeds: list[Seed] | None = None) -> ScanContext:
    return ScanContext(
        case_id="case-t308",
        scan_id="scan-t308",
        target=Target(first_name="Ada", last_name="Lovelace", hints={"city": "Istanbul"}),
        seeds=seeds or [],
    )


def _task(username: str) -> TaskSpec:
    return TaskSpec(
        id=f"t-{username}",
        connector="x",
        platform=Platform.UNKNOWN,
        kind="fetch_profile",
        params={"username": username},
    )


# ---------------------------------------------------------------------------
# Stack Overflow (api.stackexchange.com)
# ---------------------------------------------------------------------------

SO_URL = (
    "https://api.stackexchange.com/2.3/users"
    "?inname=jonskeet&site=stackoverflow&filter=default&pagesize=10"
)


def _so_body(items: list[dict]) -> dict:
    return {"items": items, "has_more": False, "quota_remaining": 290}


@pytest.mark.asyncio
async def test_stackoverflow_basarili_profil():
    conn = _conn(StackOverflowConnector)
    body = _so_body(
        [
            {
                "user_id": 22656,
                "account_id": 11683,
                "display_name": "Jon Skeet",
                "reputation": 1420000,
                "location": "Reading, United Kingdom",
                "link": "https://stackoverflow.com/users/22656/jon-skeet",
                "profile_image": "https://i.sstatic.net/J5HMc.png?s=256",
                "creation_date": 1224432467,
                "badge_counts": {"gold": 900, "silver": 9000, "bronze": 90000},
            }
        ]
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(SO_URL).respond(200, json=body)
        fetch = await conn.fetch("jonskeet")

    assert fetch is not None
    assert fetch.data["name"] == "Jon Skeet"
    assert fetch.data["reputation"] == 1420000
    assert fetch.data["location"] == "Reading, United Kingdom"
    assert fetch.data["avatar_url"].startswith("https://i.sstatic.net/")
    assert fetch.data["created_at"].startswith("2008-10-")
    assert fetch.data["badge_counts"]["gold"] == 900
    assert fetch.data["match"] == "exact"
    assert fetch.url == "https://stackoverflow.com/users/22656/jon-skeet"


@pytest.mark.asyncio
async def test_stackoverflow_bulanik_eslesme_isaretlenir():
    conn = _conn(StackOverflowConnector)
    body = _so_body(
        [
            {"display_name": "Jon Skeet Fan", "reputation": 12, "link": "https://so/u/1"},
        ]
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(SO_URL).respond(200, json=body)
        fetch = await conn.fetch("jonskeet")

    assert fetch is not None
    assert fetch.data["match"] == "fuzzy"
    assert fetch.data["name"] == "Jon Skeet Fan"


@pytest.mark.asyncio
async def test_stackoverflow_yok_ve_hata_durumlari():
    conn = _conn(StackOverflowConnector)
    for status_code in (400, 404):
        with respx.mock(assert_all_called=False) as router:
            router.get(SO_URL).respond(status_code)
            assert await conn.fetch("jonskeet") is None

    # Bos items -> hesap yok
    with respx.mock(assert_all_called=False) as router:
        router.get(SO_URL).respond(200, json=_so_body([]))
        assert await conn.fetch("jonskeet") is None

    # error_id (orn. throttling) -> hesap yok olarak ele alinir
    with respx.mock(assert_all_called=False) as router:
        router.get(SO_URL).respond(200, json={"error_id": 502, "error_name": "throttle"})
        assert await conn.fetch("jonskeet") is None


@pytest.mark.asyncio
async def test_stackoverflow_normalize_alanlari():
    conn = _conn(StackOverflowConnector)
    body = _so_body(
        [{"display_name": "Ada", "reputation": 42, "link": "https://so/u/2", "creation_date": 0}]
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(
            "https://api.stackexchange.com/2.3/users"
            "?inname=ada&site=stackoverflow&filter=default&pagesize=10"
        ).respond(200, json=body)
        obs_list = await conn.run(_task("ada"), _ctx())

    entities, edges = conn.normalize(obs_list[0])
    assert edges == []
    entity = entities[0]
    assert entity.id == "stackoverflow:ada"
    assert entity.platform == Platform.STACKOVERFLOW
    assert entity.attributes["username"] == "ada"
    assert entity.attributes["name"] == "Ada"
    assert entity.attributes["reputation"] == 42
    assert entity.attributes["match"] == "exact"
    assert "avatar_url" not in entity.attributes  # kaynakta yok


# ---------------------------------------------------------------------------
# Bitbucket (api.bitbucket.org)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bitbucket_basarili_profil():
    conn = _conn(BitbucketConnector)
    body = {
        "username": "ada",
        "display_name": "Ada Lovelace",
        "account_id": "557058:abc",
        "created_on": "2019-04-01T12:00:00.000000+00:00",
        "location": "London",
        "links": {
            "html": {"href": "https://bitbucket.org/ada/"},
            "avatar": {"href": "https://bytebucket.org/ravatar/%7Babc%7D?ts=1"},
        },
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.bitbucket.org/2.0/users/ada").respond(200, json=body)
        fetch = await conn.fetch("ada")

    assert fetch is not None
    assert fetch.data["name"] == "Ada Lovelace"
    assert fetch.data["location"] == "London"
    assert fetch.data["created_at"] == "2019-04-01T12:00:00.000000+00:00"
    assert fetch.data["avatar_url"].startswith("https://bytebucket.org/")
    assert fetch.data["profile_url"] == "https://bitbucket.org/ada/"


@pytest.mark.asyncio
async def test_bitbucket_yok_ve_gizli_hesap():
    conn = _conn(BitbucketConnector)
    for status_code in (404, 403):
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.bitbucket.org/2.0/users/ghost").respond(status_code)
            assert await conn.fetch("ghost") is None


@pytest.mark.asyncio
async def test_bitbucket_normalize_entity_id_ve_alanlar():
    conn = _conn(BitbucketConnector)
    body = {
        "username": "ada",
        "display_name": "Ada Lovelace",
        "created_on": "2020-01-01T00:00:00+00:00",
        "links": {"html": {"href": "https://bitbucket.org/ada/"}},
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.bitbucket.org/2.0/users/ada").respond(200, json=body)
        obs_list = await conn.run(_task("ada"), _ctx())

    entities, _edges = conn.normalize(obs_list[0])
    entity = entities[0]
    assert entity.id == "bitbucket:ada"
    assert entity.url == "https://bitbucket.org/ada/"
    assert entity.attributes["created_at"].startswith("2020-01-01")
    assert entity.attributes["username"] == "ada"


# ---------------------------------------------------------------------------
# Mastodon (mastodon.social)
# ---------------------------------------------------------------------------

MASTODON_URL = "https://mastodon.social/api/v1/accounts/lookup?acct=ada"


@pytest.mark.asyncio
async def test_mastodon_basarili_profil_bio_temizlenir():
    conn = _conn(MastodonConnector)
    body = {
        "id": "108000",
        "username": "ada",
        "display_name": "Ada L.",
        "note": "<p>Security researcher &amp; <b>math</b> nerd</p>",
        "avatar": "https://files.mastodon.social/accounts/avatars/ada.png",
        "created_at": "2021-05-06T07:08:09.000Z",
        "followers_count": 1234,
        "following_count": 12,
        "statuses_count": 987,
        "url": "https://mastodon.social/@ada",
        "bot": False,
        "locked": True,
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(MASTODON_URL).respond(200, json=body)
        fetch = await conn.fetch("ada")

    assert fetch is not None
    assert fetch.data["name"] == "Ada L."
    assert fetch.data["bio"] == "Security researcher & math nerd"
    assert fetch.data["followers_count"] == 1234
    assert fetch.data["statuses_count"] == 987
    assert fetch.data["created_at"] == "2021-05-06T07:08:09.000Z"
    assert fetch.data["mastodon_id"] == "108000"


@pytest.mark.asyncio
async def test_mastodon_yok_ve_erisim_engelli():
    conn = _conn(MastodonConnector)
    for status_code in (404, 401, 403):
        with respx.mock(assert_all_called=False) as router:
            router.get(MASTODON_URL).respond(status_code)
            assert await conn.fetch("ada") is None

    with respx.mock(assert_all_called=False) as router:
        router.get(MASTODON_URL).respond(200, json={})
        assert await conn.fetch("ada") is None


@pytest.mark.asyncio
async def test_mastodon_at_isareti_normalize_edilir():
    conn = _conn(MastodonConnector)
    body = {"id": "1", "username": "ada", "url": "https://mastodon.social/@ada"}
    task = TaskSpec(
        id="t-at",
        connector="mastodon",
        platform=Platform.MASTODON,
        kind="fetch_profile",
        params={"username": "@ada"},
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(MASTODON_URL).respond(200, json=body)
        obs_list = await conn.run(task, _ctx())

    entities, _edges = conn.normalize(obs_list[0])
    assert entities[0].id == "mastodon:ada"
    assert entities[0].attributes["username"] == "ada"


# ---------------------------------------------------------------------------
# Codeberg (codeberg.org — Gitea API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codeberg_basarili_profil():
    conn = _conn(CodebergConnector)
    body = {
        "id": 4242,
        "login": "ada",
        "full_name": "Ada Lovelace",
        "description": "opensource & cryptography",
        "avatar_url": "https://codeberg.org/avatars/ada.png",
        "location": "Berlin",
        "created": "2019-02-03T04:05:06Z",
        "followers_count": 77,
        "following_count": 5,
        "website": "https://ada.example",
        "html_url": "https://codeberg.org/ada",
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://codeberg.org/api/v1/users/ada").respond(200, json=body)
        fetch = await conn.fetch("ada")

    assert fetch is not None
    assert fetch.data["name"] == "Ada Lovelace"
    assert fetch.data["bio"] == "opensource & cryptography"
    assert fetch.data["location"] == "Berlin"
    assert fetch.data["created_at"] == "2019-02-03T04:05:06Z"
    assert fetch.data["followers_count"] == 77
    assert fetch.data["profile_url"] == "https://codeberg.org/ada"


@pytest.mark.asyncio
async def test_codeberg_yok_hesap():
    conn = _conn(CodebergConnector)
    for status_code in (404, 403):
        with respx.mock(assert_all_called=False) as router:
            router.get("https://codeberg.org/api/v1/users/ghost").respond(status_code)
            assert await conn.fetch("ghost") is None


@pytest.mark.asyncio
async def test_codeberg_normalize_alanlari():
    conn = _conn(CodebergConnector)
    body = {
        "id": 7,
        "login": "ada",
        "full_name": "Ada",
        "created": "2020-06-01T00:00:00Z",
        "followers_count": 3,
        "html_url": "https://codeberg.org/ada",
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://codeberg.org/api/v1/users/ada").respond(200, json=body)
        obs_list = await conn.run(_task("ada"), _ctx())

    entities, _edges = conn.normalize(obs_list[0])
    entity = entities[0]
    assert entity.id == "codeberg:ada"
    assert entity.attributes["followers_count"] == 3
    assert entity.attributes["codeberg_id"] == 7
    assert entity.attributes["created_at"].startswith("2020-06-01")


# ---------------------------------------------------------------------------
# Ortak davranis: plan(), tier, var olmayan hesap observation'i
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,platform",
    [
        (StackOverflowConnector, Platform.STACKOVERFLOW),
        (BitbucketConnector, Platform.BITBUCKET),
        (MastodonConnector, Platform.MASTODON),
        (CodebergConnector, Platform.CODEBERG),
    ],
)
def test_connector_meta_ve_tier(cls, platform):
    conn = _conn(cls)
    assert conn.platform == platform
    assert conn.tier == RiskTier.A  # hizli public API -> O1'de de kosar
    assert "fetch_profile" in conn.supported_kinds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,username",
    [
        (StackOverflowConnector, "ada"),
        (BitbucketConnector, "ada"),
        (MastodonConnector, "ada"),
        (CodebergConnector, "ada"),
    ],
)
async def test_plan_seed_ve_varyant_gorevleri_uretir(cls, username):
    conn = _conn(cls)
    ctx = _ctx(seeds=[Seed(platform=conn.platform, username=username)])
    tasks = await conn.plan(ctx)

    assert tasks, "plan en az bir gorev uretmeli"
    assert tasks[0].params["username"] == username
    # Varyant gorevleri de eklenir ama max_variant_tasks ile sinirli
    assert len(tasks) <= 1 + conn.max_variant_tasks
    assert all(task.connector == conn.name for task in tasks)


@pytest.mark.asyncio
async def test_var_olmayan_hesap_entity_uretmez():
    """404 -> data.exists False -> normalize bos doner (bulgu uretmez)."""
    conn = _conn(CodebergConnector)
    with respx.mock(assert_all_called=False) as router:
        router.get("https://codeberg.org/api/v1/users/ghost").respond(404)
        obs_list = await conn.run(_task("ghost"), _ctx())

    assert len(obs_list) == 1
    assert obs_list[0].data["exists"] is False
    entities, edges = conn.normalize(obs_list[0])
    assert entities == [] and edges == []


# ---------------------------------------------------------------------------
# Factory / paket kaydi (22 -> 26 connector)
# ---------------------------------------------------------------------------


def test_dort_yeni_connector_paketten_export_edilir():
    import showis_connectors as pkg

    names = {
        StackOverflowConnector.name,
        BitbucketConnector.name,
        MastodonConnector.name,
        CodebergConnector.name,
    }
    assert names == {"stackoverflow", "bitbucket", "mastodon", "codeberg"}
    for cls in (StackOverflowConnector, BitbucketConnector, MastodonConnector, CodebergConnector):
        assert getattr(pkg, cls.__name__) is cls

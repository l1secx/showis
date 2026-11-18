"""B2 — e-posta pivotlari: GitHub commit aramasi + Keybase GitHub pivotu.

Ag yok: tum istekler `respx` ile mock'lanir. Gercek e-posta adresi kullanilmaz
(`ornek@example.com`).
"""

from __future__ import annotations

import uuid

import respx
from showis_connectors.publicapi import GitHubEmailConnector, KeybaseConnector, email_tag
from showis_contracts import Platform, ScanContext, Seed, Target
from showis_engine.http import HttpClient

EMAIL = "ornek@example.com"
GITHUB_COMMITS = "https://api.github.com/search/commits"


def _ctx(seeds: list[Seed]) -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(kind="handle", nickname="ornek"),
        seeds=seeds,
    )


def _connector(token: str | None = None) -> GitHubEmailConnector:
    return GitHubEmailConnector(
        token=token, http_client=HttpClient(enable_ssrf_guard=False)
    )


def _search_task(connector: GitHubEmailConnector, email: str = EMAIL):
    from showis_contracts import TaskSpec

    return TaskSpec(
        id=str(uuid.uuid4()),
        connector=connector.name,
        platform=Platform.GITHUB,
        kind="commits_by_email",
        params={"email": email, "email_tag": email_tag(email)},
    )


COMMITS_PAYLOAD = {
    "total_count": 2,
    "items": [
        {
            "html_url": "https://github.com/ghuser/repo/commit/abc123",
            "author": {"login": "ghuser"},
            "commit": {
                "author": {"name": "Ornek Kisi", "email": EMAIL},
            },
        },
        {
            # ayni login -> tek observation
            "html_url": "https://github.com/ghuser/other/commit/def456",
            "author": {"login": "ghuser"},
            "commit": {"author": {"name": "Ornek Kisi", "email": EMAIL}},
        },
        {
            "html_url": "https://github.com/ikinci/repo/commit/ghi789",
            "author": {"login": "ikinci"},
            "commit": {"author": {"name": "Ikinci Kisi", "email": EMAIL}},
        },
        # login'siz item atlanir
        {"html_url": "https://github.com/x/y/commit/zzz", "author": {}},
    ],
}


# ---------------------------------------------------------------------------
# Plan: token yok / var
# ---------------------------------------------------------------------------


async def test_token_yoksa_gorev_uretmez():
    connector = _connector(token=None)

    tasks = await connector.plan(_ctx([Seed(platform=Platform.GITHUB, email=EMAIL)]))

    assert tasks == []


async def test_token_yoksa_bilgi_logu_yazilir(caplog):
    connector = _connector(token=None)
    with caplog.at_level("INFO", logger="showis.publicapi"):
        await connector.plan(_ctx([Seed(platform=Platform.GITHUB, email=EMAIL)]))
    assert "token yok" in caplog.text
    # PII log'a yazilmaz
    assert EMAIL not in caplog.text


async def test_token_varsa_eposta_basina_tek_gorev():
    connector = _connector(token="t0ken")

    tasks = await connector.plan(
        _ctx(
            [
                Seed(platform=Platform.GITHUB, email=EMAIL),
                Seed(platform=Platform.REDDIT, email=EMAIL.upper()),  # dedupe
                Seed(platform=Platform.GITHUB, email="ikinci@example.com"),
                Seed(platform=Platform.GITHUB),  # e-posta yok
            ]
        )
    )

    assert len(tasks) == 2
    assert {t.kind for t in tasks} == {"commits_by_email"}
    assert {t.params["email"] for t in tasks} == {EMAIL, "ikinci@example.com"}
    # PII'siz etiket de tasinir
    assert all(t.params["email_tag"].startswith("sha256:") for t in tasks)


async def test_gecersiz_eposta_atlanir():
    connector = _connector(token="t0ken")

    tasks = await connector.plan(
        _ctx(
            [
                Seed(platform=Platform.GITHUB, email="satir\nkirilimi@example.com"),
                Seed(platform=Platform.GITHUB, email="bosluk lu@example.com"),
                Seed(platform=Platform.GITHUB, email="eposta-yok"),
                Seed(platform=Platform.GITHUB, email=EMAIL),
            ]
        )
    )

    assert [t.params["email"] for t in tasks] == [EMAIL]


# ---------------------------------------------------------------------------
# Run: commit aramasi
# ---------------------------------------------------------------------------


async def test_commit_aramasi_observation_uretir():
    connector = _connector(token="t0ken")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(GITHUB_COMMITS).respond(200, json=COMMITS_PAYLOAD)
        obs = await connector.run(_search_task(connector), _ctx([]))

    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer t0ken"
    assert request.headers["accept"].startswith("application/vnd.github+json")
    assert "author-email" in str(request.url)
    assert "per_page=10" in str(request.url)

    # ayni login tek observation; login'siz item atlanir
    assert [o.subject for o in obs] == ["ghuser", "ikinci"]
    first = obs[0]
    assert first.kind.value == "profile"
    assert first.data["username"] == "ghuser"
    assert first.data["url"] == "https://github.com/ghuser/repo/commit/abc123"
    assert first.data["name"] == "Ornek Kisi"
    assert first.data["public_email"] == EMAIL
    assert first.data["source"] == "github_commits"


async def test_eposta_url_ve_kanitta_maskelenir():
    connector = _connector(token="t0ken")

    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_COMMITS).respond(200, json=COMMITS_PAYLOAD)
        obs = await connector.run(_search_task(connector), _ctx([]))

    assert obs[0].evidence, "kanit saklanmali"
    for observation in obs:
        assert EMAIL not in str(observation.url)
        for ev in observation.evidence:
            assert EMAIL not in ev.source_url


async def test_rate_limit_istisna_firlatmaz():
    connector = _connector(token="t0ken")

    for status in (403, 429):
        with respx.mock(assert_all_called=False) as router:
            router.get(GITHUB_COMMITS).respond(status, json={"message": "rate limit"})
            obs = await connector.run(_search_task(connector), _ctx([]))

        assert len(obs) == 1
        assert obs[0].error == "github_email_rate_limited"
        assert connector.normalize(obs[0]) == ([], [])


async def test_eslesme_yoksa_exists_false():
    connector = _connector(token="t0ken")

    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_COMMITS).respond(200, json={"total_count": 0, "items": []})
        obs = await connector.run(_search_task(connector), _ctx([]))

    assert len(obs) == 1
    assert obs[0].data["exists"] is False
    assert obs[0].error is None
    assert connector.normalize(obs[0]) == ([], [])


async def test_normalize_github_entity_uretir():
    connector = _connector(token="t0ken")

    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_COMMITS).respond(200, json=COMMITS_PAYLOAD)
        obs = await connector.run(_search_task(connector), _ctx([]))

    entities, edges = connector.normalize(obs[0])
    assert edges == []
    assert len(entities) == 1
    entity = entities[0]
    assert entity.platform is Platform.GITHUB
    assert entity.label == "ghuser"
    assert entity.attributes["username"] == "ghuser"
    assert entity.attributes["public_email"] == EMAIL
    assert entity.attributes["github_link"] == "https://github.com/ghuser/repo/commit/abc123"
    assert entity.evidence_ids


# ---------------------------------------------------------------------------
# Keybase: GitHub kullanici adi pivotu (D9)
# ---------------------------------------------------------------------------


KEYBASE_LOOKUP = "https://keybase.io/_/api/1.0/user/lookup.json"

KEYBASE_PAYLOAD = {
    "status": {"code": 0},
    "them": [
        {
            "basics": {"username": "kbuser", "ctime": 1600000000},
            "profile": {"bio": "merhaba", "location": "Istanbul"},
            "proofs_summary": {
                "all": [
                    {"proof_type": "github", "nametag": "ghuser"},
                    {"proof_type": "twitter", "nametag": "twuser"},
                ]
            },
        }
    ],
}


async def test_keybase_github_pivotu_gorev_uretir():
    connector = KeybaseConnector(http_client=HttpClient(enable_ssrf_guard=False))

    tasks = await connector.plan(
        _ctx(
            [
                Seed(platform=Platform.GITHUB, username="ghuser"),
                Seed(platform=Platform.GITHUB, username="ghuser"),  # dedupe
                Seed(platform=Platform.KEYBASE, username="kbuser"),
            ]
        )
    )

    pivots = [t for t in tasks if t.kind == "profile_by_github"]
    assert len(pivots) == 1, "ayni GitHub login icin tek pivot gorevi"
    assert pivots[0].params["github"] == "ghuser"
    # mevcut username akisi bozulmadi: KEYBASE seed'i icin fetch_profile var
    keybase_tasks = [
        t for t in tasks if t.kind == "fetch_profile" and t.params.get("username") == "kbuser"
    ]
    assert len(keybase_tasks) == 1


async def test_keybase_github_pivotu_url_ve_ayristirma():
    connector = KeybaseConnector(http_client=HttpClient(enable_ssrf_guard=False))
    from showis_contracts import TaskSpec

    task = TaskSpec(
        id=str(uuid.uuid4()),
        connector="keybase",
        platform=Platform.KEYBASE,
        kind="profile_by_github",
        params={"github": "ghuser"},
    )

    with respx.mock(assert_all_called=False) as router:
        route = router.get(KEYBASE_LOOKUP).respond(200, json=KEYBASE_PAYLOAD)
        obs = await connector.run(task, _ctx([]))

    request = route.calls.last.request
    assert "github=ghuser" in str(request.url)
    assert "username=" not in str(request.url)
    assert "fields=basics,profile,proofs_summary" in str(request.url)

    assert len(obs) == 1
    data = obs[0].data
    assert data["username"] == "kbuser"
    assert data["github_link"] == "https://github.com/ghuser"
    assert data["twitter_link"] == "https://x.com/twuser"


async def test_keybase_github_pivotu_bulunamadi():
    connector = KeybaseConnector(http_client=HttpClient(enable_ssrf_guard=False))
    from showis_contracts import TaskSpec

    task = TaskSpec(
        id=str(uuid.uuid4()),
        connector="keybase",
        platform=Platform.KEYBASE,
        kind="profile_by_github",
        params={"github": "yokboyle"},
    )

    with respx.mock(assert_all_called=False) as router:
        router.get(KEYBASE_LOOKUP).respond(200, json={"status": {"code": 0}, "them": []})
        obs = await connector.run(task, _ctx([]))

    assert obs[0].data["exists"] is False
    assert connector.normalize(obs[0]) == ([], [])


async def test_keybase_username_akisi_bozulmadi():
    """Mevcut (username) akis ayni sekilde calismali: parametre `username`."""
    connector = KeybaseConnector(http_client=HttpClient(enable_ssrf_guard=False))
    from showis_contracts import TaskSpec

    task = TaskSpec(
        id=str(uuid.uuid4()),
        connector="keybase",
        platform=Platform.KEYBASE,
        kind="fetch_profile",
        params={"username": "kbuser"},
    )

    with respx.mock(assert_all_called=False) as router:
        route = router.get(KEYBASE_LOOKUP).respond(200, json=KEYBASE_PAYLOAD)
        obs = await connector.run(task, _ctx([]))

    assert "username=kbuser" in str(route.calls.last.request.url)
    assert obs[0].data["username"] == "kbuser"


async def test_keybase_eposta_ile_plan_uretilmez():
    """D9: Keybase e-posta parametresi desteklemiyor — e-posta seed'i gorev uretmez."""
    connector = KeybaseConnector(http_client=HttpClient(enable_ssrf_guard=False))

    tasks = await connector.plan(
        _ctx([Seed(platform=Platform.KEYBASE, email=EMAIL)])
    )

    # e-posta hicbir goreve donusmez (varyantlar yalniz nick'ten uretilir)
    assert all("email" not in t.params for t in tasks)
    assert all(t.kind != "profile_by_github" for t in tasks)


# ---------------------------------------------------------------------------
# Factory: token gecisi
# ---------------------------------------------------------------------------


def test_factory_tokeni_github_email_connectorina_gecirir(monkeypatch):
    """Ayar katmani token vermezse connector token'siz kurulur (gorev uretmez)."""
    connector = GitHubEmailConnector(token="")
    assert connector.token is None

    import asyncio

    tasks = asyncio.run(connector.plan(_ctx([Seed(platform=Platform.GITHUB, email=EMAIL)])))
    assert tasks == []

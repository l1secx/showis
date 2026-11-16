import pytest
import respx
from showis_contracts import EdgeType, Entity, EntityType, Platform
from showis_engine.links import (
    build_avatar_match_edges,
    build_cross_link_edges,
    entity_profile_key,
    extract_profile_links,
    extract_social_links,
    fetch_avatar_sha256,
    profile_links_from_attributes,
)

# ---------------------------------------------------------------------------
# links.py
# ---------------------------------------------------------------------------


def test_extract_profile_links_multi_platform():
    text = (
        "Bio: github.com/keremboz ve https://tryhackme.com/p/keremboz "
        "ayrica app.hackthebox.com/profile/abc123"
    )
    links = extract_profile_links(text)
    platforms = {lnk.platform for lnk in links}
    assert platforms == {Platform.GITHUB, Platform.TRYHACKME, Platform.HACKTHEBOX}
    by_platform = {lnk.platform: lnk.identifier for lnk in links}
    assert by_platform[Platform.GITHUB] == "keremboz"
    assert by_platform[Platform.TRYHACKME] == "keremboz"
    assert by_platform[Platform.HACKTHEBOX] == "abc123"


def test_extract_profile_links_dedup_and_reserved():
    text = "github.com/features github.com/foo github.com/foo"
    links = extract_profile_links(text)
    assert len(links) == 1
    assert links[0].identifier == "foo"


def test_extract_profile_links_extended_platforms():
    text = (
        "gitlab.com/janedoe reddit.com/user/janedoe medium.com/@janedoe "
        "dev.to/janedoe soundcloud.com/janedoe youtube.com/@janedoe "
        "keybase.io/janedoe chess.com/member/janedoe "
        "pypi.org/user/janedoe hub.docker.com/u/janedoe npmjs.com/~janedoe "
        "janedoe.substack.com janedoe.itch.io news.ycombinator.com/user?id=janedoe"
    )
    links = extract_profile_links(text)
    by_platform = {lnk.platform: lnk.identifier for lnk in links}
    assert by_platform[Platform.GITLAB] == "janedoe"
    assert by_platform[Platform.REDDIT] == "janedoe"
    assert by_platform[Platform.MEDIUM] == "janedoe"
    assert by_platform[Platform.DEVTO] == "janedoe"
    assert by_platform[Platform.SOUNDCLOUD] == "janedoe"
    assert by_platform[Platform.YOUTUBE] == "janedoe"
    assert by_platform[Platform.KEYBASE] == "janedoe"
    assert by_platform[Platform.CHESS] == "janedoe"
    assert by_platform[Platform.PYPI] == "janedoe"
    assert by_platform[Platform.DOCKERHUB] == "janedoe"
    assert by_platform[Platform.NPM] == "janedoe"
    assert by_platform[Platform.SUBSTACK] == "janedoe"
    assert by_platform[Platform.ITCHIO] == "janedoe"
    assert by_platform[Platform.HACKERNEWS] == "janedoe"


def test_extract_social_links():
    social = extract_social_links("twitter: @someone -> https://x.com/someone t.me/someone")
    assert social.get("twitter") == "someone"
    assert social.get("telegram") == "someone"


def test_profile_links_from_attributes():
    attrs = {
        "links": ["https://github.com/alice", "https://blog.example.com"],
        "github_link": "https://github.com/alice",
    }
    links = profile_links_from_attributes(attrs)
    assert any(lnk.platform == Platform.GITHUB and lnk.identifier == "alice" for lnk in links)


def test_build_cross_link_edges():
    src = Entity(
        id="tryhackme:keremboz",
        type=EntityType.ACCOUNT,
        platform=Platform.TRYHACKME,
        label="keremboz",
        attributes={"links": ["https://github.com/keremboz"]},
    )
    dst = Entity(
        id="github:keremboz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="keremboz",
        attributes={"login": "keremboz"},
    )
    edges = build_cross_link_edges(src, [src, dst])
    assert len(edges) == 1
    assert edges[0].type == EdgeType.CROSS_LINK
    assert edges[0].source_entity_id == "tryhackme:keremboz"
    assert edges[0].target_entity_id == "github:keremboz"
    assert edges[0].confidence == 0.9


def test_build_cross_link_edges_unknown_target_deterministic_id():
    src = Entity(
        id="thm:x",
        type=EntityType.ACCOUNT,
        platform=Platform.TRYHACKME,
        label="x",
        attributes={"links": ["https://github.com/nobody"]},
    )
    edges = build_cross_link_edges(src, [src])
    assert edges[0].target_entity_id == "github:nobody"


def test_build_avatar_match_edges():
    a = Entity(
        id="github:a",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="a",
        attributes={"avatar_sha256": "deadbeef"},
    )
    b = Entity(
        id="thm:b",
        type=EntityType.ACCOUNT,
        platform=Platform.TRYHACKME,
        label="b",
        attributes={"avatar_sha256": "deadbeef"},
    )
    c = Entity(
        id="gh:c",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="c",
        attributes={"avatar_sha256": "deadbeef"},
    )
    edges = build_avatar_match_edges([a, b, c])
    # a-b cross platform, b-c cross platform, a-c same platform atlanir
    pairs = {(e.source_entity_id, e.target_entity_id) for e in edges}
    assert ("github:a", "thm:b") in pairs
    assert ("thm:b", "gh:c") in pairs
    assert all(e.type == EdgeType.SAME_PERSON for e in edges)


def test_entity_profile_key():
    e = Entity(
        id="x",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="@Foo",
        attributes={"login": "foo"},
    )
    assert entity_profile_key(e) == (Platform.GITHUB, "foo")


@pytest.mark.asyncio
async def test_fetch_avatar_sha256():
    with respx.mock:
        respx.get("https://cdn.example.com/a.png").respond(content=b"\x89PNGfake")
        digest = await fetch_avatar_sha256("https://cdn.example.com/a.png")
    assert digest is not None
    assert len(digest) == 64


@pytest.mark.asyncio
async def test_fetch_avatar_sha256_failure_returns_none():
    with respx.mock:
        respx.get("https://cdn.example.com/missing.png").respond(status_code=404)
        digest = await fetch_avatar_sha256("https://cdn.example.com/missing.png")
    assert digest is None

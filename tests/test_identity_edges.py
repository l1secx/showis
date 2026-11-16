"""T-309 — kimlik grafiği edge üretimi testleri."""

from __future__ import annotations

import pytest
from showis_contracts import EdgeType, Entity, EntityType, Platform
from showis_engine.identity_edges import (
    AVATAR_DHASH_CONFIDENCE,
    AVATAR_SHA_CONFIDENCE,
    EMAIL_OR_PGP_CONFIDENCE,
    MAX_EDGES_PER_ENTITY,
    USERNAME_MAX_CONFIDENCE,
    build_identity_edges,
    db_safe_edge_id,
    edges_by_type,
    entity_username,
    pair_key,
    short_edge_id,
)


def _entity(
    platform: Platform,
    username: str,
    *,
    attributes: dict | None = None,
    evidence: list[str] | None = None,
) -> Entity:
    return Entity(
        id=f"{platform.value}:{username}",
        type=EntityType.ACCOUNT,
        platform=platform,
        label=username,
        url=f"https://{platform.value}.example/{username}",
        attributes={"username": username, **(attributes or {})},
        evidence_ids=evidence or [],
    )


def _same_username_group(username: str = "kboz", n: int = 3) -> list[Entity]:
    platforms = [
        Platform.GITHUB,
        Platform.GITLAB,
        Platform.REDDIT,
        Platform.KEYBASE,
        Platform.WEB,
        Platform.MEDIUM,
        Platform.NPM,
    ]
    return [_entity(platforms[i % len(platforms)], username) for i in range(n)]


# ---------------------------------------------------------------------------
# (a) Aynı kullanıcı adı — yıldız topolojisi
# ---------------------------------------------------------------------------


def test_uc_platformda_ayni_username_iki_edge_uretir():
    entities = _same_username_group("kboz", 3)
    edges = build_identity_edges(entities)

    assert len(edges) == 2
    assert all(edge.type == EdgeType.SAME_PERSON for edge in edges)
    # yıldız: hub (github) her iki uca da bağlanır
    hub = "github:kboz"
    assert {edge.source_entity_id for edge in edges} == {hub}
    assert {edge.target_entity_id for edge in edges} == {"gitlab:kboz", "reddit:kboz"}
    assert all("ortak kullanıcı adı 'kboz' (3 platform)" == edge.reason for edge in edges)
    # confidence = 0.35 + 0.05*(3-1) = 0.45
    assert all(edge.confidence == pytest.approx(0.45) for edge in edges)


def test_tek_platform_edge_uretmez():
    entities = [
        _entity(Platform.GITHUB, "keremboz"),
        _entity(Platform.GITHUB, "keremboz", attributes={"name": "Kerem"}),
    ]
    assert build_identity_edges(entities) == []


def test_farkli_usernameler_baglanmaz():
    entities = [
        _entity(Platform.GITHUB, "keremboz"),
        _entity(Platform.GITLAB, "kboz"),
        _entity(Platform.REDDIT, "bozkerem"),
    ]
    assert build_identity_edges(entities) == []


def test_username_case_ve_at_isareti_normalize_edilir():
    first = _entity(Platform.GITHUB, "KeremBoz")
    second = _entity(Platform.GITLAB, "keremboz")
    second.attributes["username"] = "@KeremBoz"
    edges = build_identity_edges([first, second])
    assert len(edges) == 1
    assert entity_username(second) == "keremboz"


# ---------------------------------------------------------------------------
# (c) Avatar sinyalleri
# ---------------------------------------------------------------------------


def test_ayni_avatar_sha256_090():
    entities = [
        _entity(Platform.GITHUB, "a", attributes={"avatar_sha256": "f" * 64}),
        _entity(Platform.GITLAB, "b", attributes={"avatar_sha256": "f" * 64}),
    ]
    edges = build_identity_edges(entities)
    avatar_edges = [e for e in edges if "SHA-256" in e.reason]
    assert len(avatar_edges) == 1
    assert avatar_edges[0].confidence == pytest.approx(AVATAR_SHA_CONFIDENCE) == 0.90


def test_dhash_dort_bit_fark_070():
    base = "0f0f0f0f0f0f0f0f"
    diff = "0f0f0f0f0f0f0f00"  # son nibble farklı -> 4 bit
    entities = [
        _entity(Platform.GITHUB, "a", attributes={"avatar_dhash": base}),
        _entity(Platform.GITLAB, "b", attributes={"avatar_dhash": diff}),
    ]
    edges = build_identity_edges(entities)
    dhash_edges = [e for e in edges if "dHash" in e.reason]
    assert len(dhash_edges) == 1
    assert dhash_edges[0].confidence == pytest.approx(AVATAR_DHASH_CONFIDENCE) == 0.70
    assert "mesafe=4" in dhash_edges[0].reason


def test_dhash_generic_ve_uzak_hash_eslesmez():
    entities = [
        _entity(Platform.GITHUB, "a", attributes={"avatar_dhash": "0000000000000000"}),
        _entity(Platform.GITLAB, "b", attributes={"avatar_dhash": "0000000000000000"}),
        _entity(Platform.REDDIT, "c", attributes={"avatar_dhash": "ffffffffffffffff"}),
        _entity(Platform.KEYBASE, "d", attributes={"avatar_dhash": "0123456789abcdef"}),
    ]
    assert build_identity_edges(entities) == []


# ---------------------------------------------------------------------------
# (d) E-posta / PGP
# ---------------------------------------------------------------------------


def test_ayni_eposta_085_ve_pii_maskeli():
    entities = [
        _entity(Platform.GITHUB, "a", attributes={"public_email": "Kerem@Example.com"}),
        _entity(Platform.GITLAB, "b", attributes={"commit_emails": ["kerem@example.com"]}),
    ]
    edges = build_identity_edges(entities)
    mail_edges = [e for e in edges if "e-posta" in e.reason]
    assert len(mail_edges) == 1
    assert mail_edges[0].confidence == pytest.approx(EMAIL_OR_PGP_CONFIDENCE) == 0.85
    assert "kerem@example.com" not in mail_edges[0].reason
    assert "***@example.com" in mail_edges[0].reason


def test_ayni_pgp_parmak_izi_baglar():
    fingerprint = "ABCD1234EF567890ABCD1234EF567890ABCD1234"
    entities = [
        _entity(Platform.GITHUB, "a", attributes={"pgp_fingerprint": fingerprint}),
        _entity(Platform.KEYBASE, "b", attributes={"pgp_keys": [fingerprint.lower()]}),
    ]
    edges = build_identity_edges(entities)
    pgp_edges = [e for e in edges if "PGP" in e.reason]
    assert len(pgp_edges) == 1
    assert pgp_edges[0].confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# (e) Gerçek örüntü: 24 platformda aynı kullanıcı adı
# ---------------------------------------------------------------------------


def test_yirmi_dort_platform_yirmi_uc_edge_ve_hub_github():
    platforms = [
        Platform.GITHUB,
        Platform.GITLAB,
        Platform.REDDIT,
        Platform.KEYBASE,
        Platform.WEB,
        Platform.MEDIUM,
        Platform.NPM,
        Platform.PYPI,
        Platform.DOCKERHUB,
        Platform.DEVTO,
        Platform.CHESS,
        Platform.STEAM,
        Platform.LEETCODE,
        Platform.CODEFORCES,
        Platform.SOUNDCLOUD,
        Platform.HUGGINGFACE,
        Platform.KITSU,
        Platform.WORDPRESS,
        Platform.HACKERNEWS,
        Platform.TRYHACKME,
        Platform.HACKTHEBOX,
        Platform.BLUESKY,
        Platform.GRAVATAR,
        Platform.BITBUCKET,
    ]
    entities = [_entity(platform, "kboz") for platform in platforms]
    edges = build_identity_edges(entities)

    assert len(edges) == 23
    assert all(edge.source_entity_id == "github:kboz" for edge in edges)
    assert len({edge.target_entity_id for edge in edges}) == 23
    # n=24 -> 0.35 + 0.05*23 = 1.50 -> üst sınır 0.85
    assert all(edge.confidence == pytest.approx(USERNAME_MAX_CONFIDENCE) for edge in edges)


def test_hub_onceligi_github_yoksa_gitlab_sonra_web():
    group = [
        _entity(Platform.KEYBASE, "kboz"),
        _entity(Platform.WEB, "kboz"),
        _entity(Platform.GITLAB, "kboz"),
    ]
    edges = build_identity_edges(group)
    assert {edge.source_entity_id for edge in edges} == {"gitlab:kboz"}


# ---------------------------------------------------------------------------
# (f) 20 kenar sınırı
# ---------------------------------------------------------------------------


def test_entity_basina_yirmi_kenar_siniri():
    platforms = [
        Platform.GITHUB,
        Platform.GITLAB,
        Platform.REDDIT,
        Platform.KEYBASE,
        Platform.WEB,
        Platform.MEDIUM,
        Platform.NPM,
        Platform.PYPI,
        Platform.DOCKERHUB,
        Platform.DEVTO,
        Platform.CHESS,
        Platform.STEAM,
        Platform.LEETCODE,
        Platform.CODEFORCES,
        Platform.SOUNDCLOUD,
        Platform.HUGGINGFACE,
        Platform.KITSU,
        Platform.WORDPRESS,
        Platform.HACKERNEWS,
        Platform.TRYHACKME,
        Platform.HACKTHEBOX,
        Platform.BLUESKY,
        Platform.GRAVATAR,
        Platform.BITBUCKET,
        Platform.TUMBLR,
    ]
    entities = [_entity(platform, "kboz") for platform in platforms]
    edges = build_identity_edges(entities)

    counts: dict[str, int] = {}
    for edge in edges:
        for endpoint in (edge.source_entity_id, edge.target_entity_id):
            counts[endpoint] = counts.get(endpoint, 0) + 1
    assert max(counts.values()) <= MAX_EDGES_PER_ENTITY
    assert len(edges) == MAX_EDGES_PER_ENTITY


# ---------------------------------------------------------------------------
# (g) Dedupe ve id güvenliği
# ---------------------------------------------------------------------------


def test_dedupe_ayni_cift_tek_kez_ve_simetrik():
    entities = [
        _entity(Platform.GITHUB, "kboz", attributes={"avatar_sha256": "a" * 64}),
        _entity(Platform.GITLAB, "kboz", attributes={"avatar_sha256": "a" * 64}),
    ]
    edges = build_identity_edges(entities)
    keys = [pair_key(edge) for edge in edges]
    assert len(keys) == len(set(keys)), "aynı (source,target,type) iki kez üretilmemeli"
    # username + avatar aynı çifti işaret ediyor -> tek kenar
    assert len(edges) == 1


def test_ayni_platform_iki_kayit_baglanmaz():
    entities = [
        _entity(Platform.GITHUB, "kboz", attributes={"avatar_sha256": "b" * 64}),
        _entity(Platform.GITHUB, "kboz", attributes={"avatar_sha256": "b" * 64}),
        _entity(Platform.GITLAB, "kboz", attributes={"avatar_sha256": "b" * 64}),
    ]
    edges = build_identity_edges(entities)
    for edge in edges:
        assert edge.source_entity_id.split(":")[0] != edge.target_entity_id.split(":")[0]


def test_edge_id_db_sinirinda_ve_deterministik():
    entities = _same_username_group("kboz", 4)
    first_run = build_identity_edges(entities)
    second_run = build_identity_edges(list(reversed(entities)))
    assert [e.id for e in first_run] == [e.id for e in second_run]
    assert all(len(edge.id) <= 36 for edge in first_run)


def test_uzun_id_hashlenir():
    long_id = "edge:cross_link:github:keremboz:twitter:keremboz:extra:padding"
    short = db_safe_edge_id(long_id)
    assert len(short) <= 36
    assert short.startswith("e:x:")
    assert db_safe_edge_id("edge:short") == "edge:short"
    assert short_edge_id("e:u", "a", "b") == short_edge_id("e:u", "a", "b")


def test_edges_by_type_sayimi():
    entities = [
        _entity(Platform.GITHUB, "kboz"),
        _entity(Platform.GITLAB, "kboz"),
        _entity(Platform.REDDIT, "kboz"),
    ]
    counts = edges_by_type(build_identity_edges(entities))
    assert counts == {"same_person": 2}


def test_cross_link_korunur():
    source = _entity(
        Platform.GITHUB,
        "keremboz",
        attributes={"bio": "https://gitlab.com/keremboz"},
    )
    target = _entity(Platform.GITLAB, "keremboz")
    edges = build_identity_edges([source, target])
    types = {edge.type for edge in edges}
    assert EdgeType.SAME_PERSON in types
    assert EdgeType.CROSS_LINK in types


def test_cross_link_kapatilabilir():
    source = _entity(
        Platform.GITHUB,
        "keremboz",
        attributes={"bio": "https://gitlab.com/otherperson"},
    )
    target = _entity(Platform.GITLAB, "otherperson")
    edges = build_identity_edges([source, target], include_cross_link=False)
    assert all(edge.type != EdgeType.CROSS_LINK for edge in edges)

"""Recursive pivot: keşfedilen profil bağlantılarından yeni hedefler."""

from __future__ import annotations

from showis.services.scanner import build_pivot_candidates
from showis_contracts import Entity, EntityType, Platform


def _entity(attrs: dict) -> Entity:
    return Entity(
        id="keybase:kerem",
        type=EntityType.ACCOUNT,
        platform=Platform.KEYBASE,
        label="kerem",
        attributes={"username": "kerem", **attrs},
    )


def test_link_alanindan_github_pivotu():
    candidates = build_pivot_candidates(
        [_entity({"links": ["https://github.com/keremboz"]})], set()
    )
    assert [(c.connector, c.username) for c in candidates] == [("github", "keremboz")]


def test_social_ve_proof_aliaslari():
    entity = _entity({"social": {"reddit": "@kboz", "twitter": "ignored"}})
    candidates = build_pivot_candidates(
        [entity, _entity({"proofs": [{"type": "hackernews", "nametag": "kboz"}]})],
        set(),
    )
    pairs = {(c.connector, c.username) for c in candidates}
    assert ("reddit", "kboz") in pairs
    assert ("hackernews", "kboz") in pairs


def test_zaten_bilinen_ve_gecersiz_adlar_atlanir():
    known = {("github", "keremboz")}
    candidates = build_pivot_candidates(
        [
            _entity(
                {
                    "links": [
                        "https://github.com/keremboz",
                        "https://github.com/../etc/passwd",
                        "https://example.com/not-a-profile",
                    ]
                }
            )
        ],
        known,
    )
    assert candidates == []


def test_pivot_sayisi_sinirli():
    links = [f"https://github.com/user{i}" for i in range(10)]
    candidates = build_pivot_candidates([_entity({"links": links})], set(), max_candidates=3)
    assert len(candidates) == 3

"""Kimlik kümesi (union-find) ve bağımsız sinyal özeti testleri."""

from __future__ import annotations

from dataclasses import dataclass

from showis.services.identity_graph import (
    cluster_id_for,
    compute_identity_clusters,
    corroboration_signals,
)


@dataclass
class _Entity:
    id: str
    platform: str
    evidence_ids: list[str]


@dataclass
class _Edge:
    source_entity_id: str
    target_entity_id: str
    type: str
    confidence: float
    evidence_ids: list[str]
    reason: str = ""


def _entities() -> list[_Entity]:
    return [
        _Entity("github:kerem", "github", ["ev-1"]),
        _Entity("gitlab:kerem", "gitlab", ["ev-2"]),
        _Entity("reddit:kerem", "reddit", []),
        _Entity("web:kerem-blog", "web", ["ev-3"]),
        _Entity("github:solo", "github", []),
    ]


def test_ayni_kullanici_adi_uclu_kume_olur():
    entities = _entities()
    edges = [
        _Edge(
            "github:kerem",
            "gitlab:kerem",
            "same_person",
            0.45,
            ["ev-9"],
            reason="ortak kullanıcı adı 'kerem' (2 platform)",
        ),
        _Edge(
            "github:kerem",
            "reddit:kerem",
            "same_person",
            0.45,
            [],
            reason="ortak kullanıcı adı 'kerem' (2 platform)",
        ),
        _Edge(
            "web:kerem-blog",
            "github:kerem",
            "cross_link",
            0.7,
            ["ev-10"],
            reason="bio bağlantısı",
        ),
    ]
    by_entity, clusters = compute_identity_clusters(entities, edges)

    assert len(by_entity["github:kerem"].entity_ids) == 4
    assert by_entity["github:kerem"].id == by_entity["web:kerem-blog"].id
    assert by_entity["github:kerem"].size == 4
    assert set(by_entity["github:kerem"].platforms) == {"github", "gitlab", "reddit", "web"}
    assert by_entity["github:kerem"].max_confidence == 0.7
    assert by_entity["github:kerem"].edge_count == 3
    # entity + edge kanıtları birleşir
    assert by_entity["github:kerem"].evidence_count == 5
    # En sık bağlantı gerekçesi küme özetinde görünür
    assert by_entity["github:kerem"].reasons[0] == "ortak kullanıcı adı 'kerem' (2 platform)"
    assert "bio bağlantısı" in by_entity["github:kerem"].reasons

    # Tek başına kalan entity kendi kümesinde (size=1)
    assert by_entity["github:solo"].size == 1
    assert by_entity["github:solo"].max_confidence == 0.0
    assert len(clusters) == 2
    assert clusters[0].size == 4  # büyük küme önce


def test_zayif_kenarlar_kume_yapmaz():
    entities = _entities()[:2]
    edges = [_Edge("github:kerem", "gitlab:kerem", "mentions", 0.99, [])]
    by_entity, _ = compute_identity_clusters(entities, edges)
    assert by_entity["github:kerem"].size == 1
    assert by_entity["gitlab:kerem"].size == 1


def test_bilinmeyen_uclu_kenar_yok_sayilir():
    entities = _entities()[:1]
    edges = [_Edge("github:kerem", "ghost:x", "same_person", 0.9, [])]
    by_entity, _ = compute_identity_clusters(entities, edges)
    assert by_entity["github:kerem"].size == 1


def test_kume_id_deterministik():
    first = cluster_id_for(["b", "a", "c"])
    second = cluster_id_for(["c", "a", "b"])
    assert first == second
    assert first.startswith("cluster:")
    assert len(first) == len("cluster:") + 12


def test_bos_girdi():
    assert compute_identity_clusters([], []) == ({}, [])


def test_corroboration_puanlari():
    signals = corroboration_signals(
        {
            "username_exact_match": 0.30,
            "name_similarity": 0.20,
            "bio_location_company": 0.0,
            "contradiction": -0.20,
            "total": 0.30,
        }
    )
    assert signals == ["username_exact_match", "name_similarity"]
    assert corroboration_signals(None) == []
    assert corroboration_signals({"bogus": 1.0}) == []

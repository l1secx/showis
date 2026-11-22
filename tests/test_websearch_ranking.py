"""Arama sonucu alaka sıralaması — OSINT gürültüsünü bastırma."""

from __future__ import annotations

from showis_connectors.websearch import SearchHit
from showis_connectors.websearch.ranking import rank_hits, score_hit


def _hit(url: str, title: str, snippet: str = "", rank: int = 1) -> SearchHit:
    return SearchHit(
        url=url,
        title=title,
        snippet=snippet,
        rank=rank,
        provider="test",
    )


def test_profil_sonucu_dizinden_once_gelir():
    hits = [
        _hit("https://spokeo.com/ayse-yilmaz", "Ayşe Yılmaz — Spokeo"),
        _hit("https://example.com/blog/ayse", "Ayşe Yılmaz biyografi"),
        _hit("https://github.com/ayseyilmaz", "Ayşe Yılmaz · GitHub", "developer"),
    ]
    ranked = rank_hits(hits, "Ayşe Yılmaz")
    assert ranked[0].url == "https://github.com/ayseyilmaz"
    assert ranked[-1].url == "https://spokeo.com/ayse-yilmaz"
    assert [hit.rank for hit in ranked] == [1, 2, 3]


def test_turkce_katlama_ile_sorgu_eslesir():
    hit = _hit("https://github.com/ayseyilmaz", "Ayşe Yılmaz profili")
    assert score_hit(hit, "ayse yilmaz") > 0
    assert score_hit(hit, "AYŞE YILMAZ") > 0


def test_ayni_profil_iki_kez_donmez():
    hits = [
        _hit("https://github.com/johndoe", "John Doe", "engineer", rank=1),
        _hit("https://github.com/johndoe/?tab=repositories", "John Doe repos", rank=2),
        _hit("https://gitlab.com/johndoe", "John Doe", "devops", rank=1),
    ]
    ranked = rank_hits(hits, "johndoe")
    assert len(ranked) == 2
    assert {hit.url for hit in ranked} == {
        "https://github.com/johndoe",
        "https://gitlab.com/johndoe",
    }


def test_spam_ve_indirme_sonucu_cezalanir():
    clean = _hit("https://github.com/kerem", "Kerem Boz · GitHub", "engineer")
    spam = _hit(
        "https://example.com/kerem-apk",
        "Kerem Boz APK download free",
        "download apk casino",
    )
    assert score_hit(clean, "kerem boz") > score_hit(spam, "kerem boz")


def test_limit_uygulanir():
    hits = [_hit(f"https://example{i}.com/x", f"sonuc {i}") for i in range(10)]
    assert len(rank_hits(hits, "sonuc", limit=3)) == 3

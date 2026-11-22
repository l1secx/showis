"""Arama sonucu sıralaması — OSINT sinyalini yükselt, çöpü bastır.

Sağlayıcılar kendi sıralarını döndürür; aynı sorgu için Brave/DDG/SerpAPI
sonuçları birleşince sıra anlamsızlaşır. Bu modül deterministik bir alaka
puanı uygular:

* sorgu ifadesi başlıkta/snippet'te/URL'de geçiyor mu,
* bilinen profil platformu mu (web dizini değil),
* kullanıcı adı URL yolunda birebir mi,
* reklam / people-search dizini / indirme çöplüğü mü (negatif).

Sıralama dışa bağımlı değildir; aynı girdi her zaman aynı sırayı verir.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import SearchHit

#: Türkçe/aksanlı harfleri ASCII'ye indirger (arama motoru metni için yeterli).
_FOLD = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
        "Â": "a",
        "â": "a",
        "Î": "i",
        "î": "i",
        "Û": "u",
        "û": "u",
    }
)

_QUERY_NOISE_RE = re.compile(r"[()\"']|\b(?:or|and|not|site)\b", re.IGNORECASE)

#: People-search / reklam / toplu dizin alan adları: profilden çok gürültü.
_LOW_QUALITY_DOMAINS = (
    "wikipedia.org",
    "whocallsme.com",
    "spokeo.com",
    "beenverified.com",
    "peekyou.com",
    "pipl.com",
    "peoplefinder.com",
    "rocketreach.co",
    "signalhire.com",
    "zoominfo.com",
    "leadiq.com",
    "apollo.io",
    "contactout.com",
    "hunter.io",
    "emailrep.io",
    "thatsthem.com",
    "fastpeoplesearch.com",
    "truepeoplesearch.com",
    "nuwber.com",
    "radaris.com",
    "usphonebook.com",
)

_SPAM_WORDS = (
    "login",
    "sign in",
    "sign up",
    "download",
    "apk",
    "coupon",
    "free trial",
    "casino",
    "betting",
    "job vacancy",
    "salary",
    "porn",
)


def _fold(value: str) -> str:
    return str(value or "").translate(_FOLD).lower()


def _query_terms(query: str) -> list[str]:
    cleaned = _QUERY_NOISE_RE.sub(" ", _fold(query))
    return [term for term in cleaned.split() if len(term) >= 2]


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _domain_penalty(host: str) -> float:
    if not host:
        return 0.0
    for domain in _LOW_QUALITY_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return -6.0
    return 0.0


def score_hit(hit: SearchHit, query: str) -> float:
    """Tek sonuç için 0..~20 arası alaka puanı (negatif olabilir)."""
    terms = _query_terms(query)
    title = _fold(hit.title)
    snippet = _fold(hit.snippet)
    url = _fold(hit.url)
    subject = _fold(hit.subject_id or "")
    phrase = _fold(" ".join(query.replace('"', " ").split()))

    score = 0.0
    if phrase and len(phrase) >= 3:
        if phrase in title:
            score += 8
        if phrase in snippet:
            score += 3
        if phrase in url:
            score += 4

    for term in terms:
        if term in title:
            score += 3
        if term in snippet:
            score += 1.5
        if term in url:
            score += 2
        if subject and term == subject:
            score += 4

    if subject and subject in url:
        score += 3
    if hit.platform.value != "web":
        score += 2

    haystack = f"{title} {snippet}"
    for word in _SPAM_WORDS:
        if word in haystack:
            score -= 2

    score += _domain_penalty(_host(hit.url))
    # Sağlayıcının kendi sırası eşitlik bozucudur, ana sinyal değil.
    score -= max(0, hit.rank - 1) * 0.05
    return round(score, 3)


def rank_hits(hits: list[SearchHit], query: str, *, limit: int = 20) -> list[SearchHit]:
    """Tekilleştirir, puanlar, sıralar ve `rank` alanlarını yeniden yazar."""
    best_by_url: dict[str, tuple[float, SearchHit]] = {}
    for hit in hits:
        canonical = hit.url.split("#")[0].rstrip("/").lower()
        score = score_hit(hit, query)
        existing = best_by_url.get(canonical)
        if existing is None or score > existing[0]:
            best_by_url[canonical] = (score, hit)

    # Aynı platform + aynı kullanıcı adı: iki farklı URL olsa da tek profildir.
    best_by_profile: dict[tuple[str, str], tuple[float, SearchHit]] = {}
    for score, hit in best_by_url.values():
        if hit.platform.value == "web" or not hit.subject_id:
            key = ("url", hit.url)
        else:
            key = (hit.platform.value, hit.subject_id.lower())
        existing = best_by_profile.get(key)
        if existing is None or score > existing[0]:
            best_by_profile[key] = (score, hit)

    ordered = sorted(
        best_by_profile.values(),
        key=lambda item: (-item[0], item[1].rank, item[1].url),
    )
    result = [hit for _, hit in ordered[:limit]]
    for index, hit in enumerate(result, start=1):
        hit.rank = index
    return result

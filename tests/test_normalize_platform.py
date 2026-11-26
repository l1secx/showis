"""URL → platform eşlemesi host sınırı (P-014)."""

from __future__ import annotations

from showis_connectors.websearch.normalize import extract_platform_from_url
from showis_contracts import Platform


def test_platform_host_siniri_false_positive_uretmez():
    assert extract_platform_from_url("https://glitch.io/u/ada")[0] is Platform.WEB
    assert extract_platform_from_url("https://notgithub.com/ada")[0] is Platform.WEB
    assert extract_platform_from_url("https://github.com.evil.tld/ada")[0] is Platform.WEB
    assert extract_platform_from_url("https://nott.me/ada")[0] is Platform.WEB


def test_platform_host_siniri_dogru_eslesir():
    assert extract_platform_from_url("https://www.github.com/ada") == (Platform.GITHUB, "ada")
    assert extract_platform_from_url("https://ada.itch.io") == (Platform.ITCHIO, "ada")
    assert extract_platform_from_url("https://t.me/ada") == (Platform.TELEGRAM, "ada")
    assert extract_platform_from_url("https://bsky.app/profile/ada.bsky.social")[0] is (
        Platform.BLUESKY
    )

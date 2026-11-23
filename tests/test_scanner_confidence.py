"""Site-check güven ağırlığının skor sinyaline etkisi."""

from __future__ import annotations

from showis.services.scanner import (
    _is_trusted_source,
    _source_confidence,
    _trusted_avatar_platforms,
    _trusted_username_platforms,
)
from showis_contracts import Entity, EntityType, Platform


def _entity(
    platform: Platform,
    username: str = "kerem",
    *,
    confidence: float | None = None,
    avatar: str | None = None,
    label: str | None = None,
) -> Entity:
    attrs = {"username": username}
    if confidence is not None:
        attrs["confidence_hint"] = confidence
    if avatar:
        attrs["avatar_sha256"] = avatar
    return Entity(
        id=f"{platform.value}:{username}",
        type=EntityType.ACCOUNT,
        platform=platform,
        label=label or username,
        attributes=attrs,
    )


def test_connector_kaynagi_varsayilan_guvenilir():
    assert _source_confidence(_entity(Platform.GITHUB)) == 1.0
    assert _is_trusted_source(_entity(Platform.GITHUB))


def test_generic_sitecheck_guvenilir_ama_optout_degil():
    generic = _entity(Platform.INSTAGRAM, confidence=0.65)
    optout = _entity(Platform.TIKTOK, confidence=0.5)
    assert _is_trusted_source(generic)
    assert not _is_trusted_source(optout)


def test_guvenilir_olmayan_platform_sayilmaz():
    entities = [
        _entity(Platform.GITHUB),
        _entity(Platform.INSTAGRAM, confidence=0.5),
    ]
    platforms = _trusted_username_platforms(entities)
    assert platforms["kerem"] == {"github"}


def test_guvenilir_iki_sitecheck_platformu_sayilir():
    entities = [
        _entity(Platform.INSTAGRAM, confidence=0.65),
        _entity(Platform.TIKTOK, confidence=0.65),
    ]
    platforms = _trusted_username_platforms(entities)
    assert platforms["kerem"] == {"instagram", "tiktok"}


def test_avatar_guveni_de_filtrelenir():
    entities = [
        _entity(Platform.GITHUB, avatar="abc"),
        _entity(Platform.INSTAGRAM, confidence=0.5, avatar="abc"),
    ]
    assert _trusted_avatar_platforms(entities) == {"abc": {"github"}}


def test_gecersiz_confidence_guvenli_varsayilan():
    entity = _entity(Platform.GITHUB)
    entity.attributes["confidence_hint"] = "bozuk"
    assert _source_confidence(entity) == 1.0

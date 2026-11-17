"""Hedef kimliği — `POST /cases` 201/422 matrisi (username-first spec §6.1/§6.3)."""

import pytest


@pytest.mark.parametrize(
    "target,expected",
    [
        ({"kind": "handle", "nickname": "kerem"}, 201),
        ({"kind": "handle"}, 422),
        ({"kind": "name", "first_name": "Kerem"}, 201),
        ({"kind": "name", "first_name": "Kerem", "last_name": "Boz"}, 201),
        ({"kind": "name", "last_name": "Boz"}, 422),
        ({"kind": "handle", "nickname": "ab"}, 422),
        ({"kind": "handle", "nickname": "__"}, 422),
        ({"kind": "handle", "nickname": "@Kerem"}, 201),
    ],
)
async def test_target_identity_matrix(client, target, expected):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Matris",
            "purpose": "x" * 10,
            "legal_basis": "self_check",
            "retention_days": 7,
            "target": target,
            "seeds": [],
        },
    )
    assert res.status_code == expected, res.text


async def test_hints_username_moves_into_nickname(client):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Matris",
            "purpose": "x" * 10,
            "legal_basis": "self_check",
            "retention_days": 7,
            "target": {
                "kind": "name",
                "first_name": "Kerem",
                "hints": {"username": "kb"},
            },
            "seeds": [],
        },
    )
    body = (await client.get(f"/api/v1/cases/{res.json()['id']}")).json()
    assert body["target"]["nickname"] == "kb"
    assert "username" not in body["target"]["hints"]


async def test_nickname_wins_over_hints_username(client):
    """Review Focus #2: iki kaynakta nick varsa tek kimlik kalmalı."""
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Matris",
            "purpose": "x" * 10,
            "legal_basis": "self_check",
            "retention_days": 7,
            "target": {
                "kind": "handle",
                "nickname": "kerem",
                "hints": {"username": "baska"},
            },
            "seeds": [],
        },
    )
    body = (await client.get(f"/api/v1/cases/{res.json()['id']}")).json()
    assert body["target"]["nickname"] == "kerem"
    assert "username" not in body["target"]["hints"]


async def _create(client, target: dict):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Matris",
            "purpose": "x" * 10,
            "legal_basis": "self_check",
            "retention_days": 7,
            "target": target,
            "seeds": [],
        },
    )
    assert res.status_code == 201, res.text
    detail = await client.get(f"/api/v1/cases/{res.json()['id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()["target"]


async def test_migrated_hint_nickname_is_truncated_to_column_width(client):
    """129–200 karakterlik legacy `hints.username` DB sinirini aşmamalı (500 yok)."""
    stored = await _create(
        client, {"kind": "name", "first_name": "Kerem", "hints": {"username": "a" * 129}}
    )
    assert stored["nickname"] == "a" * 128
    assert len(stored["nickname"]) == 128


async def test_missing_kind_defaults_to_name(client):
    """`kind` göndermeyen eski istemci aynı davranır (geriye uyumlu varsayılan)."""
    stored = await _create(client, {"first_name": "Kerem", "last_name": "Boz"})
    assert stored["kind"] == "name"
    assert (stored["first_name"], stored["last_name"]) == ("Kerem", "Boz")


async def test_handle_at_prefix_stripped_raw_value_stored(client):
    stored = await _create(client, {"kind": "handle", "nickname": "@Kerem"})
    assert stored["nickname"] == "Kerem"


async def test_name_mode_stores_name_and_nickname(client):
    stored = await _create(
        client, {"kind": "name", "first_name": "Kerem", "nickname": "kerem.boz"}
    )
    assert stored["kind"] == "name"
    assert (stored["first_name"], stored["nickname"]) == ("Kerem", "kerem.boz")


async def test_handle_mode_stores_full_identity(client):
    stored = await _create(
        client,
        {
            "kind": "handle",
            "nickname": "kerem",
            "first_name": "Kerem",
            "last_name": "Boz",
        },
    )
    assert stored["kind"] == "handle"
    assert (stored["nickname"], stored["first_name"], stored["last_name"]) == (
        "kerem",
        "Kerem",
        "Boz",
    )

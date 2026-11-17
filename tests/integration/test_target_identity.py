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

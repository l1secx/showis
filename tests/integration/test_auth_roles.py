"""Rol tabanlı erişim — admin/analyst/viewer ve vaka sahipliği."""

from __future__ import annotations

import pytest
from showis.core.security import hash_token
from showis.db.models import ApiUser


async def _user(db_factory, username: str, role: str, token: str) -> None:
    async with db_factory() as session:
        session.add(
            ApiUser(
                username=username,
                role=role,
                token_hash=hash_token(token),
                active=True,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_admin_kullanici_olusturur_ve_rol_doner(client, db_factory):
    created = await client.post(
        "/api/v1/auth/users",
        json={"username": "analyst-1", "role": "analyst"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "analyst"
    assert body["token"].startswith("showis_")

    me = await client.get(
        "/api/v1/auth/me", headers={"X-User-Token": body["token"]}
    )
    assert me.status_code == 200
    assert me.json()["username"] == "analyst-1"
    assert me.json()["role"] == "analyst"


@pytest.mark.asyncio
async def test_viewer_yazamaz_analyst_kendi_vakasini_yazar(client, db_factory):
    await _user(db_factory, "analyst-1", "analyst", "analyst-token")
    await _user(db_factory, "viewer-1", "viewer", "viewer-token")

    created = await client.post(
        "/api/v1/cases",
        headers={"X-User-Token": "analyst-token"},
        json={
            "name": "Analyst Vaka",
            "purpose": "rol testi uzun",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "target": {"kind": "handle", "nickname": "analystcase"},
        },
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    detail = await client.get(
        f"/api/v1/cases/{case_id}", headers={"X-User-Token": "analyst-token"}
    )
    assert detail.status_code == 200
    assert detail.json()["owner"] == "analyst-1"

    read = await client.get(
        f"/api/v1/cases/{case_id}", headers={"X-User-Token": "viewer-token"}
    )
    assert read.status_code == 200

    viewer_write = await client.patch(
        f"/api/v1/cases/{case_id}/owner",
        headers={"X-User-Token": "viewer-token"},
        json={"owner": "viewer-1"},
    )
    assert viewer_write.status_code == 403


@pytest.mark.asyncio
async def test_analyst_baska_vakayi_yazamaz(client, db_factory):
    await _user(db_factory, "analyst-1", "analyst", "analyst-token-1")
    await _user(db_factory, "analyst-2", "analyst", "analyst-token-2")

    created = await client.post(
        "/api/v1/cases",
        headers={"X-User-Token": "analyst-token-1"},
        json={
            "name": "Sahipli Vaka",
            "purpose": "sahiplik testi",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "target": {"kind": "handle", "nickname": "ownedcase"},
        },
    )
    case_id = created.json()["id"]

    denied = await client.patch(
        f"/api/v1/cases/{case_id}/owner",
        headers={"X-User-Token": "analyst-token-2"},
        json={"owner": "analyst-2"},
    )
    assert denied.status_code == 403

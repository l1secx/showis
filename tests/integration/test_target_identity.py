"""Hedef kimliği — `POST /cases` 201/422 matrisi (username-first spec §6.1/§6.3)."""

import re
from types import SimpleNamespace

import pytest
import pytest_asyncio
from showis.api.v1.cases import _db_target_to_contract


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


# ---------------------------------------------------------------------------
# Okuma yolları: detail + list + export aynı kimlik şeklini taşır
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def handle_case_id(client) -> str:
    """Ad/soyad yerine yalnız nick taşıyan `handle` vakası."""
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Handle",
            "purpose": "x" * 10,
            "legal_basis": "self_check",
            "retention_days": 7,
            "target": {"kind": "handle", "nickname": "kerem"},
            "seeds": [],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_detail_list_and_export_expose_identity(client, handle_case_id):
    detail = (await client.get(f"/api/v1/cases/{handle_case_id}")).json()
    assert detail["target"]["kind"] == "handle"
    assert detail["target"]["nickname"] == "kerem"
    listed = (await client.get("/api/v1/cases")).json()["items"]
    assert any(i["target"]["nickname"] == "kerem" for i in listed)
    export = (await client.get(f"/api/v1/cases/{handle_case_id}/export")).json()
    assert export["target"]["nickname"] == "kerem"


def test_contract_target_maps_none_to_empty_string():
    # DB satırı None taşısa bile kontrat "" üretmeli
    row = SimpleNamespace(
        first_name=None, last_name=None, nickname=None, kind=None, hints={}, review_required=False
    )
    t = _db_target_to_contract(row)
    assert (t.first_name, t.last_name, t.nickname, t.kind) == ("", "", "", "name")


# ---------------------------------------------------------------------------
# R2: handle-only hedefte varyant üretimi nick'i kaynak olarak kullanır
# ---------------------------------------------------------------------------


async def test_handle_only_scan_produces_variants(db_factory, monkeypatch):
    """Nick varyant üretimine geçmezse handle-only tarama kör kalır (0 varyant)."""
    from showis.db.models import Case as DbCase
    from showis.db.models import Scan as DbScan
    from showis.db.models import Target as DbTarget
    from showis.services.orchestrator import event_broadcaster
    from showis.services.scanner import ScannerService

    async with db_factory() as session:
        session.add_all(
            [
                DbCase(
                    id="case-handle",
                    name="Handle",
                    purpose="test",
                    legal_basis="self_check",
                    retention_days=7,
                ),
                DbTarget(
                    case_id="case-handle",
                    kind="handle",
                    first_name=None,
                    last_name=None,
                    nickname="kerem",
                    hints={},
                    review_required=False,
                ),
                DbScan(
                    id="scan-handle",
                    case_id="case-handle",
                    mode="O1",
                    status="queued",
                    budget={},
                ),
            ]
        )
        await session.commit()

    events: list[dict] = []
    original_broadcast = event_broadcaster.broadcast

    async def _record(scan_id, event_type, data):
        events.append({"type": event_type, "scan_id": scan_id, "data": data})
        await original_broadcast(scan_id, event_type, data)

    monkeypatch.setattr(event_broadcaster, "broadcast", _record)

    async with db_factory() as session:
        # Connector yok: ağ isteği yok, yalnız varyant üretimi ve planlama koşar.
        await ScannerService(connectors=[]).execute_scan("scan-handle", session)

    counts = [
        int(match.group(1))
        for event in events
        if event["type"] == "log"
        for match in [
            re.search(r"(\d+) kullanıcı adı varyantı", (event["data"] or {}).get("message", ""))
        ]
        if match
    ]
    assert counts, events
    assert counts[0] > 0

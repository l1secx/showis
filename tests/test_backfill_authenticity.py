"""T-367 — authenticity backfill aracı testleri.

Kapsam: yalnız NULL doldurulur, dolu değer ezilmez, kullanıcı kararı korunur,
entity'si olmayan bulgu atlanır, ikinci koşu 0 güncelleme (idempotent).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backfill_authenticity as bf  # noqa: E402


class FakeConn:
    """`ONLY NULL` semantiğini uygulayan sahte asyncpg bağlantısı."""

    def __init__(self, store: dict[str, dict]):
        self.store = store
        self.executed: list[tuple] = []

    async def execute(self, sql: str, *args):  # noqa: ANN001
        finding_id, score, reasons = args
        assert "authenticity IS NULL" in sql
        row = self.store[finding_id]
        if row["authenticity"] is not None:
            return "UPDATE 0"
        row["authenticity"] = score
        row["authenticity_reasons"] = json.loads(reasons)
        self.executed.append(args)
        return "UPDATE 1"


class FakePool:
    def __init__(self, findings: list[dict], entities: list[dict]):
        self.findings = {row["id"]: dict(row) for row in findings}
        self.entities = entities
        self.conn = FakeConn(self.findings)

    async def fetch(self, sql: str, *args):  # noqa: ANN001
        if "FROM findings" in sql:
            rows = [r for r in self.findings.values() if r["authenticity"] is None]
            if args:
                rows = [r for r in rows if r["case_id"] == args[0]]
            return rows
        if "FROM entities" in sql:
            rows = self.entities
            if args:
                rows = [r for r in rows if r["case_id"] == args[0]]
            return rows
        raise AssertionError(sql)

    def acquire(self):  # noqa: ANN201
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _entity(
    entity_id: str,
    case_id: str,
    platform: str,
    username: str,
    attributes: dict | None = None,
) -> dict:
    return {
        "id": entity_id,
        "case_id": case_id,
        "platform": platform,
        "label": username,
        "attributes": {"username": username, **(attributes or {})},
    }


def _finding(finding_id: str, case_id: str, entity_id: str, authenticity=None) -> dict:
    return {
        "id": finding_id,
        "case_id": case_id,
        "entity_id": entity_id,
        "authenticity": authenticity,
    }


EMPTY_PROFILE = {
    "name": None,
    "bio": "",
    "avatar_url": "",
    "created_at": "2026-11-15T00:00:00Z",
    "public_repos": 0,
}


@pytest.mark.asyncio
async def test_null_authenticity_doldurulur():
    pool = FakePool(
        findings=[_finding("f1", "c1", "e1")],
        entities=[_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)],
    )
    summary = await bf.run_backfill(pool, case_id="c1", apply=True)

    assert summary["candidates"] == 1
    assert summary["applied"] == 1
    assert pool.findings["f1"]["authenticity"] is not None
    assert 0.0 <= pool.findings["f1"]["authenticity"] <= 1.0
    assert pool.findings["f1"]["authenticity_reasons"], "gerekçeler yazılmalı"


@pytest.mark.asyncio
async def test_dolu_deger_ezilmez():
    pool = FakePool(
        findings=[_finding("f1", "c1", "e1", authenticity=0.77)],
        entities=[_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)],
    )
    summary = await bf.run_backfill(pool, case_id="c1", apply=True)

    assert summary["candidates"] == 0
    assert summary["applied"] == 0
    assert pool.findings["f1"]["authenticity"] == pytest.approx(0.77)


@pytest.mark.asyncio
async def test_kullanici_karari_alanlarina_dokunulmaz():
    finding = _finding("f1", "c1", "e1")
    finding.update({"status": "confirmed", "verified_at": "2026-11-16T10:00:00Z", "score": 0.85})
    pool = FakePool(
        findings=[finding],
        entities=[_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)],
    )
    await bf.run_backfill(pool, case_id="c1", apply=True)

    assert pool.findings["f1"]["status"] == "confirmed"
    assert pool.findings["f1"]["verified_at"] == "2026-11-16T10:00:00Z"
    assert pool.findings["f1"]["score"] == pytest.approx(0.85)
    assert pool.findings["f1"]["authenticity"] is not None


@pytest.mark.asyncio
async def test_entitysi_olmayan_bulgu_atlanir():
    pool = FakePool(
        findings=[_finding("f1", "c1", "yok-entity")],
        entities=[_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)],
    )
    summary = await bf.run_backfill(pool, case_id="c1", apply=True)

    assert summary["candidates"] == 0
    assert summary["skipped"] == 1
    assert summary["skipped_details"][0]["reason"] == "entity bulunamadı"


@pytest.mark.asyncio
async def test_idempotent_ikinci_kosu_sifir_guncelleme():
    pool = FakePool(
        findings=[_finding("f1", "c1", "e1")],
        entities=[_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)],
    )
    first = await bf.run_backfill(pool, case_id="c1", apply=True)
    second = await bf.run_backfill(pool, case_id="c1", apply=True)

    assert first["applied"] == 1
    assert second["applied"] == 0
    assert second["candidates"] == 0


def test_cross_platform_sayisi_case_entitylerinden_hesaplanir():
    """`platforms_with_same_username` aynı vakanın entity'lerinden gelir (scanner ile aynı)."""
    finding = _finding("f1", "c1", "e1")
    entities = [
        _entity("e1", "c1", "github", "kboz", EMPTY_PROFILE),
        _entity("e2", "c1", "gitlab", "kboz", EMPTY_PROFILE),
        _entity("e3", "c1", "reddit", "kboz", EMPTY_PROFILE),
    ]
    updates, skipped = bf.build_updates([finding], entities)
    assert not skipped
    assert updates[0].platforms == 3, "3 platformda aynı kullanıcı adı sayılmalı"
    assert any("platform" in reason for reason in updates[0].reasons)

    single, _ = bf.build_updates([finding], entities[:1])
    assert single[0].platforms == 1


def test_bos_profil_skoru_taban_sinirinda_kalir():
    """Boş profil (isim/bio/avatar/etkinlik yok) 0.30 üst sınırını aşmaz."""
    finding = _finding("f1", "c1", "e1")
    entities = [_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)]
    updates, _ = bf.build_updates([finding], entities)
    assert updates[0].score <= 0.30


@pytest.mark.asyncio
async def test_dry_run_yazmaz():
    pool = FakePool(
        findings=[_finding("f1", "c1", "e1")],
        entities=[_entity("e1", "c1", "github", "kboz", EMPTY_PROFILE)],
    )
    summary = await bf.run_backfill(pool, case_id="c1", apply=False)

    assert summary["candidates"] == 1
    assert summary["applied"] == 0
    assert pool.findings["f1"]["authenticity"] is None
    assert pool.conn.executed == []


@pytest.mark.asyncio
async def test_case_filtresi_diger_vakalara_dokunmaz():
    pool = FakePool(
        findings=[_finding("f1", "c1", "e1"), _finding("f2", "c2", "e2")],
        entities=[
            _entity("e1", "c1", "github", "kboz", EMPTY_PROFILE),
            _entity("e2", "c2", "gitlab", "baska", EMPTY_PROFILE),
        ],
    )
    summary = await bf.run_backfill(pool, case_id="c1", apply=True)

    assert summary["applied"] == 1
    assert pool.findings["f1"]["authenticity"] is not None
    assert pool.findings["f2"]["authenticity"] is None


def test_platform_sayimi_normalize_eder():
    rows = [
        {
            "case_id": "c1",
            "platform": "github",
            "label": "Kboz",
            "attributes": {"username": "@Kboz"},
        },
        {"case_id": "c1", "platform": "gitlab", "label": "kboz", "attributes": {}},
        {
            "case_id": "c1",
            "platform": "reddit",
            "label": "baska",
            "attributes": {"username": "baska"},
        },
    ]
    counts = bf.case_platform_counts(rows)
    assert counts[("c1", "kboz")] == 2
    assert counts[("c1", "baska")] == 1

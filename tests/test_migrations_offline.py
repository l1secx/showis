"""Z4-8 — Alembic migration zinciri **offline** SQL üretimiyle doğrulanır.

Veritabanına bağlanılmaz (`upgrade head --sql` yalnız SQL üretir); hiçbir
migration uygulanmaz. Amaç: 0007 backfill'i ve 0008 kolonunun gerçekten
üretilen DDL içinde olduğunu görmek (statik string kontrolünden güçlü).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"


def _offline_sql() -> str:
    env = {
        **os.environ,
        # Offline mod: yalniz SQL uretir, sunucuya baglanmaz.
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/showis",
        "PYTHONPATH": os.pathsep.join(
            str(ROOT / p)
            for p in (
                "apps/api",
                "packages/contracts",
                "packages/settings",
                "packages/llm",
                "packages/engine",
                "packages/connectors",
            )
        ),
    }
    result = subprocess.run(  # noqa: S603 - sabit komut, kabuk yok
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


@pytest.fixture(scope="module")
def sql() -> str:
    return _offline_sql()


def test_offline_sql_0008_confidence_basis_kolonu(sql):
    assert "confidence_basis" in sql
    assert "ADD COLUMN confidence_basis VARCHAR(16) DEFAULT '' NOT NULL" in sql


def test_offline_sql_0007_backfill_left_ifadesi(sql):
    """0007 nickname backfill'i `left(...)` ile üretilmeli."""
    assert "left(COALESCE(hints->>'nickname', hints->>'username'), 128)" in sql


def test_offline_sql_tek_head_ve_sira(sql):
    """0006 -> 0007 -> 0008 sirasi korunur."""
    assert sql.index("0006_query_indexes") < sql.index("0007_target_identity")
    assert sql.index("0007_target_identity") < sql.index(
        "0008_finding_confidence_basis", sql.index("0007_target_identity") + 1
    )

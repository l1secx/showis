#!/usr/bin/env python3
"""Tarama suresi kalibrasyonu — ``calibration.json`` uretir.

Neden: ``showis_engine.scan_modes._COST`` sabitleri tek bir canli taramadan
olculdu. Gercek gorev sureleri DB'de duruyor; tahmini onlardan kalibre etmek
hem dogrulugu hem de sunucu degistikce guncel kalmayi saglar.

Olcum kurali (onemli):

    Yalnizca ``tasks.started_at IS NOT NULL AND tasks.finished_at IS NOT NULL``
    ve ``status='done'`` satirlar kullanilir; sure = ``finished_at - started_at``.
    ``created_at`` planlama anidir, aradaki fark **kuyruk beklemesidir** ve
    yurutme suresine dahil edilmez. Eski (started_at NULL) satirlar atlanir —
    aksi halde sirali yurutmenin kuyruk gecikmesi maliyet sanilir.

Kaynak sirasi:

    1. ``db``    — DB'de yeterli ornek (varsayilan: maliyet anahtari basina 30).
    2. ``live``  — ``--live N`` verilirse her connector icin N kez gercek
                   ``run()`` cagrisi (AG'A CIKAR; varsayilan kapali).
    3. ``heuristic`` — hicbir olcum yoksa dosyaya bos ``costs`` yazilir ve
                   tahmin ``_COST`` varsayilanlarini kullanmaya devam eder.

Kullanim::

    # yalnizca rapor (hicbir sey yazmaz)
    python scripts/calibrate_scan.py --dry-run

    # DB'den kalibre et ve calibration.json'u yaz
    SHOWIS_DATABASE_URL=postgresql://... python scripts/calibrate_scan.py

    # DB'ye erisim yoksa: 3 connector x 2 gorev gercek kosu ile ornekleme
    python scripts/calibrate_scan.py --live 3 --target "Kerem Boz" --dry-run

DSN asla stdout'a yazilmaz; yalnizca env adi ve maskeli hedef raporlanir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _rel in (
    "apps/api",
    "packages/contracts",
    "packages/settings",
    "packages/llm",
    "packages/engine",
    "packages/connectors",
):
    _path = str(REPO_ROOT / _rel)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from showis_engine.scan_modes import (  # noqa: E402
    _COST,
    CALIBRATION_FILENAME,
    scan_concurrency,
)

DEFAULT_OUT = REPO_ROOT / "packages/engine/showis_engine" / CALIBRATION_FILENAME
DEFAULT_MIN_SAMPLES = 30
TASK_ROW_LIMIT = 5000
SCAN_LIMIT = 200
MAX_SETUP_SECONDS = 900.0
MAX_ENTITY_SECONDS = 15.0

#: connector adi -> maliyet anahtari (kind'dan bagimsiz)
_CONNECTOR_COST: dict[str, str] = {
    "websearch": "query",
    "tryhackme": "slow_task",
    "hackthebox": "slow_task",
    "sitecheck": "site_username",
    "sitecheck_search": "site_username",
    "rendered": "rendered_task",
}

#: gorev kind'i -> maliyet anahtari (connector eslesmesi yoksa)
_KIND_COST: dict[str, str] = {
    "search": "query",
    "search_and_check": "site_username",
    "check_username": "site_username",
    "search_users": "identifier_task",
    "search_commits": "identifier_task",
    "render": "rendered_task",
    "fetch_profile": "api_task",
    "get_user": "api_task",
}

_COST_KEYS = tuple(_COST)


class SchemaError(RuntimeError):
    """DB semasi beklenen kolonlari icermiyor."""


def percentile(values: list[float], q: float) -> float:
    """Dogrusal interpolasyonlu yuzdelik (statistics.quantiles'dan bagimsiz)."""
    if not values:
        raise ValueError("bos liste")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def cost_key_for(connector: str, kind: str) -> str:
    """Gorevi bir maliyet anahtarina esler (varsayilan: ``api_task``)."""
    mapping = _CONNECTOR_COST.get((connector or "").strip().lower())
    if mapping:
        return mapping
    kind_key = _KIND_COST.get((kind or "").strip().lower())
    return kind_key or "api_task"


def load_env_file(path: Path) -> dict[str, str]:
    """Basit ``KEY=VALUE`` dosyasi (``os.environ`` ezilmez)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def resolve_dsn(explicit: str | None, env_file: Path | None) -> str | None:
    """DSN: arguman > ``SHOWIS_DATABASE_URL`` > ``DATABASE_URL`` > .env > varsayilan."""
    if explicit:
        return explicit
    for name in ("SHOWIS_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    file_values = load_env_file(env_file) if env_file else {}
    user = file_values.get("POSTGRES_USER") or "showis"
    password = file_values.get("POSTGRES_PASSWORD") or "change-me"
    database = file_values.get("POSTGRES_DB") or "showis"
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    if env_file and env_file.is_file():
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    return None


def asyncpg_dsn(dsn: str) -> str:
    """SQLAlchemy surucusunu asyncpg'nin bekledigi bicime indirger."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix):]
    return dsn


def mask_dsn(dsn: str) -> str:
    """Rapor icin parolasiz hedef (sir sizdirmaz)."""
    tail = dsn.rsplit("@", 1)[-1]
    return f"***@{tail}" if "@" in dsn else tail


@dataclass
class GroupStats:
    connector: str
    kind: str
    count: int
    median: float
    p90: float


@dataclass
class Measurement:
    costs: dict[str, float] = field(default_factory=dict)
    groups: list[GroupStats] = field(default_factory=list)
    sample_counts: dict[str, int] = field(default_factory=dict)
    scans: int = 0
    task_rows: int = 0
    sources: dict[str, int] = field(default_factory=dict)


def aggregate_task_rows(
    rows: list[tuple[str, str, float]], min_samples: int
) -> tuple[dict[str, float], dict[str, int], list[GroupStats]]:
    """(connector, kind, saniye) satirlarindan maliyet anahtari bazli medyan.

    Maliyet anahtari basina gozlem sayisi ``min_samples`` altindaysa o anahtar
    **kalibre edilmez** (varsayilan korunur).
    """
    per_group: dict[tuple[str, str], list[float]] = {}
    per_key: dict[str, list[float]] = {}
    for connector, kind, secs in rows:
        if secs <= 0:
            continue
        per_group.setdefault((connector, kind), []).append(secs)
        per_key.setdefault(cost_key_for(connector, kind), []).append(secs)

    groups = [
        GroupStats(
            connector=connector,
            kind=kind,
            count=len(values),
            median=statistics.median(values),
            p90=percentile(values, 0.9),
        )
        for (connector, kind), values in sorted(per_group.items())
    ]

    costs: dict[str, float] = {}
    counts: dict[str, int] = {}
    for key, values in per_key.items():
        counts[key] = len(values)
        if len(values) >= min_samples:
            costs[key] = round(statistics.median(values), 2)
    return costs, counts, groups


def aggregate_overhead(
    setup_rows: list[tuple[str, float]],
    wall_rows: list[tuple[str, float]],
    task_rows: list[tuple[str, int, float]],
    min_samples: int,
) -> tuple[dict[str, float], dict[str, int]]:
    """``setup`` (scan → ilk gorev) ve ``entity`` (gorev basina yazim) maliyeti.

    entity = (scan duvar saati - gorev sureleri toplami - setup) / gorev sayisi
    """
    setup_by_scan: dict[str, float] = {}
    setup_values: list[float] = []
    for scan_id, value in setup_rows:
        if 0 <= value <= MAX_SETUP_SECONDS:
            setup_by_scan[scan_id] = value
            setup_values.append(value)

    task_by_scan = {scan_id: (n_done, task_secs) for scan_id, n_done, task_secs in task_rows}
    entity_values: list[float] = []
    for scan_id, wall in wall_rows:
        stats = task_by_scan.get(scan_id)
        if stats is None or scan_id not in setup_by_scan:
            continue
        n_done, task_secs = stats
        leftover = wall - task_secs - setup_by_scan[scan_id]
        if n_done > 0 and 0 < leftover < n_done * MAX_ENTITY_SECONDS:
            entity_values.append(leftover / n_done)

    costs: dict[str, float] = {}
    counts: dict[str, int] = {}
    overhead_min = max(3, min_samples // 10)
    if len(setup_values) >= overhead_min:
        costs["setup"] = round(statistics.median(setup_values), 2)
        counts["setup"] = len(setup_values)
    if len(entity_values) >= overhead_min:
        costs["entity"] = round(statistics.median(entity_values), 2)
        counts["entity"] = len(entity_values)
    return costs, counts


async def _fetch_schema_columns(pool: Any) -> set[str]:
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'tasks'"
    )
    return {str(r["column_name"]) for r in rows}


async def measure_db(dsn: str, min_samples: int) -> Measurement:
    """DB'den gercek yurutme surelerini okur (yalnizca SELECT)."""
    import asyncpg

    measurement = Measurement()
    pool = await asyncpg.create_pool(asyncpg_dsn(dsn), min_size=1, max_size=2)
    try:
        columns = await _fetch_schema_columns(pool)
        if "started_at" not in columns:
            raise SchemaError(
                "tasks.started_at kolonu yok — once 'alembic upgrade head' calistirin "
                "(0002_task_started_at). Eski satirlar kalibrasyona katilmaz."
            )

        rows = await pool.fetch(
            """
            SELECT connector, kind,
                   EXTRACT(EPOCH FROM (finished_at - started_at))::float8 AS secs
            FROM tasks
            WHERE status = 'done'
              AND started_at IS NOT NULL
              AND finished_at IS NOT NULL
              AND finished_at > started_at
            ORDER BY finished_at DESC
            LIMIT $1
            """,
            TASK_ROW_LIMIT,
        )
        task_tuples = [
            (str(r["connector"]), str(r["kind"]), float(r["secs"] or 0.0)) for r in rows
        ]
        costs, counts, groups = aggregate_task_rows(task_tuples, min_samples)
        measurement.task_rows = len(task_tuples)
        measurement.groups = groups
        measurement.costs.update(costs)
        measurement.sample_counts.update(counts)
        measurement.sources.update(counts)

        setup_rows, wall_rows, task_rows, scans = await _fetch_scan_rows(pool)
        overhead_costs, overhead_counts = aggregate_overhead(
            setup_rows, wall_rows, task_rows, min_samples
        )
        measurement.costs.update(overhead_costs)
        measurement.sample_counts.update(overhead_counts)
        measurement.sources.update(overhead_counts)
        measurement.scans = scans
    finally:
        await pool.close()
    return measurement


async def _fetch_scan_rows(
    pool: Any,
) -> tuple[
    list[tuple[str, float]],
    list[tuple[str, float]],
    list[tuple[str, int, float]],
    int,
]:
    """setup/entity hesabi icin ham satirlar (scan, duvar saati, gorev toplamlari)."""
    setup_rows = await pool.fetch(
        """
        SELECT s.id::text AS id,
               EXTRACT(EPOCH FROM (MIN(t.created_at) - s.started_at))::float8 AS setup
        FROM scans s
        JOIN tasks t ON t.scan_id = s.id
        WHERE s.started_at IS NOT NULL
          AND s.finished_at IS NOT NULL
          AND s.finished_at > s.started_at
        GROUP BY s.id, s.started_at
        HAVING MIN(t.created_at) >= s.started_at
        ORDER BY s.started_at DESC
        LIMIT $1
        """,
        SCAN_LIMIT,
    )
    wall_rows = await pool.fetch(
        """
        SELECT id::text AS id,
               EXTRACT(EPOCH FROM (finished_at - started_at))::float8 AS wall
        FROM scans
        WHERE started_at IS NOT NULL
          AND finished_at IS NOT NULL
          AND finished_at > started_at
        ORDER BY started_at DESC
        LIMIT $1
        """,
        SCAN_LIMIT,
    )
    task_rows = await pool.fetch(
        """
        SELECT scan_id::text AS scan_id,
               COUNT(*) FILTER (
                   WHERE started_at IS NOT NULL AND finished_at IS NOT NULL
               ) AS n_done,
               COALESCE(SUM(EXTRACT(EPOCH FROM (finished_at - started_at))) FILTER (
                   WHERE started_at IS NOT NULL
                     AND finished_at IS NOT NULL
                     AND finished_at > started_at
               ), 0)::float8 AS task_secs
        FROM tasks
        GROUP BY scan_id
        """
    )
    return (
        [(str(r["id"]), float(r["setup"] or 0.0)) for r in setup_rows],
        [(str(r["id"]), float(r["wall"] or 0.0)) for r in wall_rows],
        [
            (str(r["scan_id"]), int(r["n_done"] or 0), float(r["task_secs"] or 0.0))
            for r in task_rows
        ],
        len(wall_rows),
    )


async def measure_live(rounds: int, target_name: str, wanted: set[str]) -> Measurement:
    """Gercek connector ``run()`` cagrilariyla ornekleme (AG'A CIKAR)."""
    from showis.db.session import async_session_factory
    from showis.services.connectors_factory import build_connectors
    from showis_connectors.sitecheck.cache import get_default_cache
    from showis_contracts import ScanContext, Target

    parts = target_name.split()
    first_name = parts[0] if parts else "Kerem"
    last_name = " ".join(parts[1:]) or "Boz"
    target = Target(first_name=first_name, last_name=last_name, hints={})
    ctx = ScanContext(case_id="calibration", scan_id="calibration", target=target, seeds=[])

    measurement = Measurement()
    samples: dict[str, list[float]] = {}

    async with async_session_factory() as session:
        connectors = await build_connectors(session)
        for connector in connectors:
            key = cost_key_for(getattr(connector, "name", ""), "")
            if wanted and key not in wanted:
                continue
            try:
                tasks = await connector.plan(ctx)
            except Exception as exc:  # planlama bile ag isteyebilir
                print(f"  ! {connector.name}: plan basarisiz ({exc})", file=sys.stderr)
                continue
            if not tasks:
                continue
            for task in tasks[:1]:
                for _ in range(rounds):
                    get_default_cache().clear()  # onbellek olcumu bozmasin
                    started = time.perf_counter()
                    try:
                        await connector.run(task, ctx)
                    except Exception as exc:
                        print(
                            f"  ! {connector.name}/{task.kind}: run basarisiz ({exc})",
                            file=sys.stderr,
                        )
                        break
                    samples.setdefault(key, []).append(time.perf_counter() - started)
                print(f"  · {connector.name}/{task.kind}: {rounds} kosu -> {key}")

    for key, values in samples.items():
        if not values:
            continue
        measurement.costs[key] = round(statistics.median(values), 2)
        measurement.sample_counts[key] = len(values)
        measurement.sources[key] = len(values)
    return measurement


def build_payload(
    measurement: Measurement,
    live: Measurement | None,
    concurrency: int,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    costs = dict(measurement.costs)
    sources = dict(measurement.sources)
    if live:
        for key, value in live.costs.items():
            if key in costs:  # DB olcumu daha guvenilir; canli kosu onu ezmez
                continue
            costs[key] = value
            sources[key] = live.sources.get(key, 0)
    if measurement.costs:
        source = "db"
    elif costs:
        source = "live"
    else:
        source = "heuristic"
    return {
        "version": 1,
        "source": source,
        "sample_count": int(sum(sources.values())),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "costs": costs,
        "concurrency_observed": concurrency,
        "details": {
            "task_rows": measurement.task_rows,
            "scans": measurement.scans,
            "live_sampled": bool(live and live.costs),
            "db_min_samples": min_samples,
        },
    }


def print_report(measurement: Measurement, payload: dict[str, Any]) -> None:
    print("\n== Gorev gruplari (db, gercek yurutme) ==")
    if not measurement.groups:
        print("  (kayit yok — started_at doldurulmamis olabilir)")
    for group in measurement.groups:
        key = cost_key_for(group.connector, group.kind)
        print(
            f"  {group.connector:<18} {group.kind:<20} n={group.count:<4} "
            f"median={group.median:6.2f}s p90={group.p90:6.2f}s -> {key}"
        )

    print("\n== Maliyet anahtarlari ==")
    for key in _COST_KEYS:
        measured = payload["costs"].get(key)
        default = _COST[key]
        if measured is None:
            print(f"  {key:<18} {default:6.2f}s  (varsayilan — yeterli ornek yok)")
        else:
            delta = (measured - default) / default * 100 if default else 0.0
            print(f"  {key:<18} {measured:6.2f}s  (kalibre, varsayilana gore {delta:+.0f}%)")
    print(
        f"\n  source={payload['source']} sample_count={payload['sample_count']} "
        f"task_rows={measurement.task_rows} scans={measurement.scans} "
        f"concurrency={payload['concurrency_observed']}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tarama suresi kalibrasyonu")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="rapor ver, dosya yazma")
    mode_group.add_argument(
        "--apply", action="store_true", help="kalibrasyonu dosyaya yaz (varsayilan davranis)"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="cikti JSON yolu")
    parser.add_argument("--database-url", default=None, help="DSN (env'i ezer)")
    parser.add_argument(
        "--env-file",
        default=str(REPO_ROOT / "infra" / ".env"),
        help="POSTGRES_* degerlerini okuyacak env dosyasi",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=DEFAULT_MIN_SAMPLES,
        help="maliyet anahtari basina asgari gozlem (varsayilan 30)",
    )
    parser.add_argument(
        "--live",
        type=int,
        default=0,
        metavar="N",
        help="DB yetersizse N kez gercek run() kosar (AG'A CIKAR; varsayilan 0)",
    )
    parser.add_argument(
        "--target",
        default="Kerem Boz",
        help="--live icin hedef ad soyad",
    )
    return parser.parse_args(argv)


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    concurrency = scan_concurrency()
    env_file = Path(args.env_file) if args.env_file else None
    dsn = resolve_dsn(args.database_url, env_file)

    measurement = Measurement()
    if not dsn:
        print(
            "UYARI: DSN bulunamadi (SHOWIS_DATABASE_URL / DATABASE_URL / "
            f"{env_file} yok). DB kalibrasyonu atlandi.",
            file=sys.stderr,
        )
    else:
        print(f"DB: {mask_dsn(asyncpg_dsn(dsn))} (salt-okunur SELECT)")
        try:
            measurement = await measure_db(asyncpg_dsn(dsn), args.min_samples)
        except SchemaError as exc:
            print(f"HATA: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # baglanti/dsn
            print(f"UYARI: DB okunamadi ({type(exc).__name__}): {exc}", file=sys.stderr)

    live_measurement: Measurement | None = None
    missing = {k for k in _COST_KEYS if k not in measurement.costs}
    if args.live > 0 and missing:
        print(
            f"\nCANLI ORNEKLEME: {args.live} kosu/connector — gercek ag istekleri atilir "
            f"(eksik anahtarlar: {', '.join(sorted(missing))})"
        )
        live_measurement = await measure_live(args.live, args.target, missing)

    payload = build_payload(measurement, live_measurement, concurrency, args.min_samples)
    print_report(measurement, payload)
    summary = json.dumps(payload, indent=2, ensure_ascii=False)
    print("\n== calibration.json ==")
    print(summary)

    if args.dry_run:
        print("\n(dry-run: dosya yazilmadi)")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(summary + "\n", encoding="utf-8")
    print(f"\nYazildi: {out_path}")
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

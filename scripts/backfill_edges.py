#!/usr/bin/env python3
"""Kimlik grafiği edge backfill'i (T-309).

Mevcut bir vakanın entity'lerini DB'den okur, `build_identity_edges` ile kenarları
üretir ve **varsayılan olarak yazmadan** ne üretileceğini raporlar.

Kullanım::

    # sadece rapor (hiçbir şey yazmaz)
    python scripts/backfill_edges.py --case-id <uuid> --dry-run

    # yaz (Atlas onayı olmadan canlıya uygulamayın)
    python scripts/backfill_edges.py --case-id <uuid> --apply

DSN sırası: ``--database-url`` → ``SHOWIS_DATABASE_URL`` → ``DATABASE_URL`` →
``infra/.env`` (POSTGRES_* + ``POSTGRES_HOST``/``POSTGRES_PORT``).
DSN hiçbir çıktıda görünmez (maskeli hedef yazılır).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

from showis_contracts import Edge, Entity, EntityType, Platform  # noqa: E402
from showis_engine.identity_edges import (  # noqa: E402
    build_identity_edges,
    edges_by_type,
    entity_username,
)


def load_env_file(path: Path) -> dict[str, str]:
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
    import os

    if explicit:
        return explicit
    for name in ("SHOWIS_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    file_values = load_env_file(env_file) if env_file else {}
    if not env_file or not env_file.is_file():
        return None
    user = file_values.get("POSTGRES_USER") or "showis"
    password = file_values.get("POSTGRES_PASSWORD") or "change-me"
    database = file_values.get("POSTGRES_DB") or "showis"
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def asyncpg_dsn(dsn: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix) :]
    return dsn


def mask_dsn(dsn: str) -> str:
    tail = dsn.rsplit("@", 1)[-1]
    return f"***@{tail}" if "@" in dsn else tail


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_entity(row: Any) -> Entity | None:
    try:
        platform = Platform(str(row["platform"]))
    except ValueError:
        return None
    try:
        entity_type = EntityType(str(row["type"]))
    except ValueError:
        entity_type = EntityType.ACCOUNT
    return Entity(
        id=str(row["id"]),
        type=entity_type,
        platform=platform,
        label=str(row["label"] or ""),
        url=row["url"],
        attributes=_as_dict(row["attributes"]),
        # `entities` tablosunda evidence_ids kolonu yok (kanıt ilişkisi evidence tablosunda)
        evidence_ids=[],
    )


async def fetch_entities(pool: Any, case_id: str) -> list[Entity]:
    rows = await pool.fetch(
        """
        SELECT id, type, platform, label, url, attributes
        FROM entities
        WHERE case_id = $1
        ORDER BY id
        """,
        case_id,
    )
    entities: list[Entity] = []
    for row in rows:
        entity = _to_entity(row)
        if entity is not None:
            entities.append(entity)
    return entities


def summarize(entities: list[Entity], edges: list[Edge]) -> dict[str, Any]:
    by_username: dict[str, set[str]] = {}
    for entity in entities:
        by_username.setdefault(entity_username(entity), set()).add(entity.platform.value)
    groups = sorted(
        ((name, len(platforms)) for name, platforms in by_username.items() if len(platforms) > 1),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        "entities": len(entities),
        "edges": len(edges),
        "edges_by_type": edges_by_type(edges),
        "multi_platform_usernames": groups[:10],
        "samples": [
            {
                "type": edge.type.value,
                "source": edge.source_entity_id,
                "target": edge.target_entity_id,
                "confidence": edge.confidence,
                "reason": edge.reason,
            }
            for edge in edges[:5]
        ],
    }


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kimlik edge backfill (dry-run varsayılan)")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--env-file", default=str(REPO_ROOT / "infra" / ".env"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="raporla, yazma (varsayılan)")
    mode.add_argument("--apply", action="store_true", help="kenarları DB'ye yaz")
    args = parser.parse_args(argv)

    dsn = resolve_dsn(args.database_url, Path(args.env_file) if args.env_file else None)
    if not dsn:
        print("HATA: DSN bulunamadı (SHOWIS_DATABASE_URL/DATABASE_URL/infra/.env)", file=sys.stderr)
        return 2

    import asyncpg

    print(f"DB: {mask_dsn(asyncpg_dsn(dsn))} — case {args.case_id}")
    pool = await asyncpg.create_pool(asyncpg_dsn(dsn), min_size=1, max_size=2)
    try:
        entities = await fetch_entities(pool, args.case_id)
        if not entities:
            print("HATA: bu vakada entity yok", file=sys.stderr)
            return 1

        existing = await pool.fetchval(
            "SELECT count(*) FROM edges WHERE case_id = $1", args.case_id
        )
        edges = build_identity_edges(entities)
        report = summarize(entities, edges)
        report["existing_edges"] = int(existing or 0)
        print(json.dumps(report, indent=2, ensure_ascii=False))

        if not args.apply:
            print("\n(dry-run: hiçbir satır yazılmadı; yazmak için --apply)")
            return 0

        # FK güvenliği: uçları DB'de olmayan kenarları atla
        known_ids = {entity.id for entity in entities}
        writable = [
            edge
            for edge in edges
            if edge.source_entity_id in known_ids and edge.target_entity_id in known_ids
        ]
        skipped = len(edges) - len(writable)
        inserted = 0
        async with pool.acquire() as conn:
            async with conn.transaction():
                for edge in writable:
                    result = await conn.execute(
                        """
                        INSERT INTO edges
                            (id, case_id, type, source_entity_id, target_entity_id,
                             confidence, reason, evidence_ids)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::json)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        edge.id,
                        args.case_id,
                        edge.type.value,
                        edge.source_entity_id,
                        edge.target_entity_id,
                        edge.confidence,
                        edge.reason,
                        json.dumps(edge.evidence_ids),
                    )
                    if result.endswith("1"):
                        inserted += 1
        print(
            f"\nUYGULANDI: {inserted} yeni kenar, {len(writable) - inserted} zaten vardı, "
            f"{skipped} uçsuz atlandı"
        )
        return 0
    finally:
        await pool.close()


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

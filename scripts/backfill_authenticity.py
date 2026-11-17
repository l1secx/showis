#!/usr/bin/env python3
"""Mevcut bulgular için authenticity backfill'i (T-367).

Sorun: T-326 öncesi yazılmış bulgularda `findings.authenticity` NULL — UI chip'i
görünmüyor. Bu araç, entity niteliklerinden skoru **yeniden hesaplar** ve yalnız
boş alanları doldurur.

Garantiler:

* **Yalnız NULL**: `UPDATE ... WHERE id = $1 AND authenticity IS NULL` — mevcut
  skorlar asla ezilmez; ikinci koşu 0 güncelleme (idempotent).
* **Kullanıcı kararı korunur**: `status`, `verified_at`, `score`, `breakdown`,
  `explanation`, `evidence_ids` alanlarına dokunulmaz.
* `cross_platform` sinyali için aynı vaka içindeki entity'ler kullanıcı adına göre
  gruplanır (scanner'ın mantığının aynısı, `compute_authenticity` çağrısıyla).
* Varsayılan **dry-run**; yazmak için `--apply`.

Kullanım::

    python scripts/backfill_authenticity.py                 # tüm vakalar, dry-run
    python scripts/backfill_authenticity.py --case-id <id>
    python scripts/backfill_authenticity.py --apply

Konteyner içinden (read-only rootfs; `docker cp` çalışmaz)::

    docker exec -i showis-api python - --apply < scripts/backfill_authenticity.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
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

from showis_engine.authenticity import compute_authenticity  # noqa: E402


def normalize_username(value: Any) -> str:
    """Entity kullanıcı adı (scanner ile aynı kural: username/login → label)."""
    return str(value or "").strip().lstrip("@").strip().lower()


@dataclass
class Update:
    finding_id: str
    case_id: str
    entity_id: str
    score: float
    reasons: list[str]
    platforms: int


def case_platform_counts(entity_rows: list[Any]) -> dict[tuple[str, str], int]:
    """(case_id, normalize_username) → aynı adı taşıyan farklı platform sayısı."""
    by_group: dict[tuple[str, str], set[str]] = {}
    for row in entity_rows:
        attrs = _as_dict(row.get("attributes"))
        username = normalize_username(
            attrs.get("username") or attrs.get("login") or attrs.get("identifier")
        )
        if not username:
            # label okuyucu bir değer olabilir; boşluklu etiketler kullanıcı adı değil
            label = normalize_username(row.get("label"))
            if label and " " not in label and len(label) <= 64:
                username = label
        if not username:
            continue
        key = (str(row.get("case_id")), username)
        by_group.setdefault(key, set()).add(str(row.get("platform") or ""))
    return {key: len(platforms) for key, platforms in by_group.items()}


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


def build_updates(
    finding_rows: list[Any], entity_rows: list[Any]
) -> tuple[list[Update], list[dict[str, str]]]:
    """NULL authenticity'li bulgular için hesaplanacak güncellemeler.

    Returns:
        (updates, skipped) — skipped: entity'si bulunamayan bulgular (uyarı).
    """
    entities = {str(row["id"]): row for row in entity_rows}
    counts = case_platform_counts(entity_rows)
    updates: list[Update] = []
    skipped: list[dict[str, str]] = []

    for finding in finding_rows:
        if finding.get("authenticity") is not None:
            continue  # mevcut değer korunur (savunma katmanı; SQL de WHERE NULL)
        entity_id = str(finding.get("entity_id") or "")
        entity = entities.get(entity_id)
        if entity is None:
            skipped.append(
                {
                    "finding_id": str(finding.get("id")),
                    "reason": "entity bulunamadı",
                }
            )
            continue
        attrs = _as_dict(entity.get("attributes"))
        username = normalize_username(
            attrs.get("username") or attrs.get("login") or attrs.get("identifier")
        )
        if not username:
            label = normalize_username(entity.get("label"))
            if label and " " not in label and len(label) <= 64:
                username = label
        platforms = counts.get((str(finding.get("case_id")), username), 1) if username else 1
        result = compute_authenticity(attrs, platforms_with_same_username=platforms)
        updates.append(
            Update(
                finding_id=str(finding.get("id")),
                case_id=str(finding.get("case_id")),
                entity_id=entity_id,
                score=result.score,
                reasons=list(result.reasons),
                platforms=platforms,
            )
        )
    return updates, skipped


def summarize(updates: list[Update], skipped: list[dict[str, str]], applied: int) -> dict[str, Any]:
    by_bucket = {"dusuk (<0.35)": 0, "orta (0.35-0.65)": 0, "yuksek (>=0.65)": 0}
    for update in updates:
        if update.score < 0.35:
            by_bucket["dusuk (<0.35)"] += 1
        elif update.score < 0.65:
            by_bucket["orta (0.35-0.65)"] += 1
        else:
            by_bucket["yuksek (>=0.65)"] += 1
    return {
        "candidates": len(updates),
        "applied": applied,
        "skipped": len(skipped),
        "score_distribution": by_bucket,
    }


# ---------------------------------------------------------------------------
# DB katmani (asyncpg; testlerde fake ile degistirilebilir)
# ---------------------------------------------------------------------------


def resolve_dsn(explicit: str | None) -> str | None:
    import os

    if explicit:
        return explicit
    for name in ("SHOWIS_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    env_file = REPO_ROOT / "infra" / ".env"
    if env_file.is_file():
        values: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("\"'")
        user = values.get("POSTGRES_USER") or "showis"
        password = values.get("POSTGRES_PASSWORD") or "change-me"
        database = values.get("POSTGRES_DB") or "showis"
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    return None


def asyncpg_dsn(dsn: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix):]
    return dsn


def mask_dsn(dsn: str) -> str:
    tail = dsn.rsplit("@", 1)[-1]
    return f"***@{tail}" if "@" in dsn else tail


async def run_backfill(
    pool: Any,
    case_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Backfill çekirdeği: ``fetch``/``execute`` sunan asyncpg benzeri pool alır."""
    finding_filter = "AND f.case_id = $1" if case_id else ""
    params = [case_id] if case_id else []
    finding_rows = await pool.fetch(
        f"""
        SELECT f.id, f.case_id, f.entity_id, f.authenticity
        FROM findings f
        WHERE f.authenticity IS NULL {finding_filter}
        ORDER BY f.id
        """,
        *params,
    )
    entity_filter = "WHERE case_id = $1" if case_id else ""
    entity_rows = await pool.fetch(
        f"""
        SELECT id, case_id, platform, label, attributes
        FROM entities {entity_filter}
        """,
        *params,
    )

    updates, skipped = build_updates(
        [dict(r) for r in finding_rows], [dict(r) for r in entity_rows]
    )
    applied = 0
    if apply:
        async with pool.acquire() as conn:
            for update in updates:
                result = await conn.execute(
                    """
                    UPDATE findings
                       SET authenticity = $2,
                           authenticity_reasons = $3::json
                     WHERE id = $1
                       AND authenticity IS NULL
                    """,
                    update.finding_id,
                    update.score,
                    json.dumps(update.reasons),
                )
                if str(result).endswith("1"):
                    applied += 1

    return {
        **summarize(updates, skipped, applied),
        "skipped_details": skipped[:20],
        "sample": [
            {
                "finding_id": u.finding_id,
                "score": u.score,
                "platforms": u.platforms,
                "first_reason": u.reasons[0] if u.reasons else None,
            }
            for u in updates[:10]
        ],
    }


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bulgu authenticity backfill'i")
    parser.add_argument("--case-id", default=None, help="yalnız bu vaka")
    parser.add_argument("--all", action="store_true", help="tüm vakalar (varsayılan)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="DB'ye yaz")
    mode.add_argument("--dry-run", action="store_true", help="yazma (varsayılan)")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true", help="yalnız JSON çıktı")
    args = parser.parse_args(argv)

    dsn = resolve_dsn(args.database_url)
    if not dsn:
        print("HATA: DSN bulunamadı (SHOWIS_DATABASE_URL/DATABASE_URL/infra/.env)", file=sys.stderr)
        return 2

    import asyncpg

    print(
        f"DB: {mask_dsn(asyncpg_dsn(dsn))} | kapsam: "
        f"{'vaka ' + args.case_id if args.case_id else 'tüm vakalar'} | "
        f"mod: {'APPLY' if args.apply else 'dry-run'}"
    )
    pool = await asyncpg.create_pool(asyncpg_dsn(dsn), min_size=1, max_size=2)
    try:
        summary = await run_backfill(pool, case_id=args.case_id, apply=args.apply)
    finally:
        await pool.close()

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(
            f"aday={summary['candidates']} güncellenen={summary['applied']} "
            f"atlanan={summary['skipped']} dağılım={summary['score_distribution']}"
        )
        for row in summary["sample"]:
            print(
                f"  {row['finding_id'][:28]:<28} authenticity={row['score']:.2f} "
                f"platform={row['platforms']} | {row['first_reason']}"
            )
        if summary["skipped_details"]:
            print(f"  uyarı: {summary['skipped']} bulgu için entity yok")
        if not args.apply:
            print("(dry-run: yazılmadı; yazmak için --apply)")
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

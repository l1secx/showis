"""Operasyon uçları (T-329): Prometheus `/metrics` + derin sağlık kontrolü `/healthz`.

Tasarım notları:
* **Yeni bağımlılık yok** — Prometheus exposition formatı elle üretilir.
* Metrikler DB'den türetilen **anlık görüntülerdir** (silme sonrası azalabilir), bu yüzden
  `# TYPE … gauge` olarak ilan edilir; yalnız süreç-içi sayaçlar `counter`.
* 5 saniyelik bellek içi cache (varsayılan `SHOWIS_METRICS_CACHE_SECONDS`) — scrape sıklığı
  DB'yi yormasın.
* Sorgular tek turda: 1 UNION ALL sayım sorgusu + 1 küçük "son 20 tarama süresi" sorgusu
  (süre farkı Python'da hesaplanır; `extract(epoch …)` SQLite'ta yok).
* `require_token` bu router'a `main.py` içinde bağlanır (auth'suz erişim 401).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from showis.db.models import Scan
from showis.db.session import get_db
from showis_contracts import EdgeType, FindingStatus
from showis_engine.http import budget_exceeded_count
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

#: Token korumali uclar (`/api/v1/metrics`) — main.py'de `auth_required` ile baglanir.
router = APIRouter(tags=["ops"])
#: Kimliksiz uclar (`/healthz`) — container healthcheck'i gibi dis izlemeye acik,
#: yalniz {status, checks} doner (hassas veri yok).
public_router = APIRouter(tags=["ops"])

#: Cache TTL — scrape başına DB yükünü sınırlar.
CACHE_SECONDS = float(os.getenv("SHOWIS_METRICS_CACHE_SECONDS", "5"))
#: Disk boş alan eşiği (yüzde). Altına düşerse `/healthz` -> degraded.
DISK_MIN_FREE_PERCENT = float(os.getenv("SHOWIS_DISK_MIN_FREE_PERCENT", "10"))
#: Sağlık kontrolü alt bileşen zaman aşımı.
CHECK_TIMEOUT_SECONDS = float(os.getenv("SHOWIS_HEALTHCHECK_TIMEOUT", "3"))

SCAN_STATUSES: tuple[str, ...] = ("completed", "failed", "cancelled")
TASK_STATUSES: tuple[str, ...] = ("pending", "running", "done", "failed", "skipped")
#: Kontrat kaynağından türetilir (drift önlenir).
FINDING_STATUSES: tuple[str, ...] = tuple(status.value for status in FindingStatus)
#: `showis_contracts.enums.EdgeType` değerleri (küçük harf — DB'de böyle saklanır).
EDGE_TYPES: tuple[str, ...] = tuple(edge_type.value for edge_type in EdgeType)
DURATION_WINDOW = 20

_metrics_cache: dict[str, Any] = {"at": 0.0, "body": ""}


def app_version() -> str:
    """Uygulama sürümü (opsiyonel `SHOWIS_VERSION` → paket metadata → '0.1.0').

    CODE-02 notu: `showis-monorepo` root meta-paketi konteyner imajında kurulu
    DEĞİL (Dockerfile.api yalnız `packages/contracts` kurar), bu yüzden metadata
    araması Docker'da her zaman PackageNotFoundError döner ve '0.1.0' fallback
    geçerli. Gerçek sürüm göstermek için compose'dan `SHOWIS_VERSION` geç.
    """
    env = os.getenv("SHOWIS_VERSION")
    if env:
        return env
    try:
        return importlib.metadata.version("showis-monorepo")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _as_utc(value: datetime | str) -> datetime:
    """SQLite naif datetime'ları (ve string dönüşleri) UTC kabul eder."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _fmt(value: float) -> str:
    """Prometheus değer biçimi: tam sayıysa ondalıksız, değilse 6 hane."""
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _metric(lines: list[str], name: str, kind: str, help_text: str) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {kind}")


#: Sayımlar tek sorguda: (kind, label, value)
_COUNT_SQL = text(
    """
    SELECT 'scan' AS kind, status AS label, COUNT(*) AS value
      FROM scans WHERE status IN ('completed', 'failed', 'cancelled')
     GROUP BY status
    UNION ALL
    SELECT 'task', status, COUNT(*) FROM tasks GROUP BY status
    UNION ALL
    SELECT 'finding', status, COUNT(*) FROM findings GROUP BY status
    UNION ALL
    SELECT 'edge', type, COUNT(*) FROM edges GROUP BY type
    UNION ALL
    SELECT 'entity', '', COUNT(*) FROM entities
    """
)

#: Sure penceresi: ORM ile tipli okunur (SQLite metin, Postgres datetime dondurur).
_DURATION_SQL = (
    select(Scan.started_at, Scan.finished_at)
    .where(
        Scan.status == "completed",
        Scan.started_at.is_not(None),
        Scan.finished_at.is_not(None),
    )
    .order_by(Scan.finished_at.desc())
    .limit(DURATION_WINDOW)
)


async def _counts(session: AsyncSession) -> dict[tuple[str, str], int]:
    rows = (await session.execute(_COUNT_SQL)).all()
    return {(str(kind), str(label)): int(value) for kind, label, value in rows}


async def _scan_durations(session: AsyncSession) -> list[float]:
    rows = (await session.execute(_DURATION_SQL)).all()
    durations: list[float] = []
    for started_at, finished_at in rows:
        if started_at is None or finished_at is None:
            continue
        seconds = (_as_utc(finished_at) - _as_utc(started_at)).total_seconds()
        if seconds >= 0:
            durations.append(seconds)
    return durations


def render_metrics(
    counts: dict[tuple[str, str], int],
    durations: list[float],
    *,
    budget_exceeded: int,
) -> str:
    """Prometheus exposition biçiminde gövde üretir (saf fonksiyon — test edilebilir)."""
    lines: list[str] = []

    _metric(lines, "showis_scans_total", "gauge", "Tarama sayisi (duruma gore).")
    for status in SCAN_STATUSES:
        lines.append(f'showis_scans_total{{status="{status}"}} {counts.get(("scan", status), 0)}')

    _metric(lines, "showis_tasks_total", "gauge", "Tarama gorevi sayisi (duruma gore).")
    for status in TASK_STATUSES:
        lines.append(f'showis_tasks_total{{status="{status}"}} {counts.get(("task", status), 0)}')

    _metric(lines, "showis_findings_total", "gauge", "Bulgu sayisi (duruma gore).")
    for status in FINDING_STATUSES:
        lines.append(
            f'showis_findings_total{{status="{status}"}} {counts.get(("finding", status), 0)}'
        )

    _metric(lines, "showis_entities_total", "gauge", "Toplam varlik (entity) sayisi.")
    lines.append(f"showis_entities_total {counts.get(('entity', ''), 0)}")

    _metric(lines, "showis_edges_total", "gauge", "Iliski (edge) sayisi (turune gore).")
    # Bilinen turler her zaman yayinlanir (0 dahil); bilinmeyen turler de eklenir.
    seen_edges = {label for (kind, label) in counts if kind == "edge"}
    for edge_type in list(EDGE_TYPES) + sorted(seen_edges - set(EDGE_TYPES)):
        lines.append(
            f'showis_edges_total{{type="{edge_type}"}} {counts.get(("edge", edge_type), 0)}'
        )

    _metric(
        lines,
        "showis_scan_duration_seconds",
        "summary",
        f"Son {DURATION_WINDOW} tamamlanan taramanin sure ozeti (saniye).",
    )
    lines.append(f"showis_scan_duration_seconds_count {len(durations)}")
    lines.append(f"showis_scan_duration_seconds_sum {_fmt(sum(durations))}")
    lines.append(f"showis_scan_duration_seconds_max {_fmt(max(durations) if durations else 0)}")

    _metric(
        lines,
        "showis_http_request_budget_exceeded_total",
        "counter",
        "HTTP istek butcesi tukendigi icin reddedilen istek sayisi (surec omurlu).",
    )
    lines.append(f"showis_http_request_budget_exceeded_total {budget_exceeded}")

    _metric(lines, "showis_app_info", "gauge", "Uygulama bilgisi (deger her zaman 1).")
    lines.append(f'showis_app_info{{version="{app_version()}"}} 1')

    return "\n".join(lines) + "\n"


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    """Prometheus scrape ucu (token korumali; 5 sn cache)."""
    now = time.monotonic()
    if _metrics_cache["body"] and (now - float(_metrics_cache["at"])) < CACHE_SECONDS:
        return PlainTextResponse(
            _metrics_cache["body"],
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    counts = await _counts(db)
    durations = await _scan_durations(db)
    body = render_metrics(counts, durations, budget_exceeded=budget_exceeded_count())
    _metrics_cache["body"] = body
    _metrics_cache["at"] = now
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")


def reset_metrics_cache() -> None:
    """Testler için: cache'i temizler."""
    _metrics_cache["body"] = ""
    _metrics_cache["at"] = 0.0


# ---------------------------------------------------------------------------
# Derin sağlık kontrolü
# ---------------------------------------------------------------------------


async def _check_db(db: AsyncSession) -> dict[str, Any]:
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=CHECK_TIMEOUT_SECONDS)
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - sağlık kontrolü hata fırlatmaz
        return {"status": "error", "error": type(exc).__name__}


async def _check_redis() -> dict[str, Any]:
    try:
        from redis.asyncio import Redis

        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        client = Redis.from_url(url, socket_connect_timeout=CHECK_TIMEOUT_SECONDS)
        try:
            pong = await asyncio.wait_for(client.ping(), timeout=CHECK_TIMEOUT_SECONDS)
        finally:
            await client.aclose()
        return {"status": "ok" if pong else "error"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": type(exc).__name__}


def _check_disk() -> dict[str, Any]:
    base = Path(os.getenv("SHOWIS_DATA_DIR", "/app/data"))
    repo_data = Path(__file__).resolve().parents[4] / "data"
    for candidate in (base, base.parent, repo_data, Path.cwd() / "data", Path.cwd()):
        try:
            usage = shutil.disk_usage(candidate)
        except OSError:
            continue
        percent_free = (usage.free / usage.total * 100) if usage.total else 0.0
        ok = percent_free >= DISK_MIN_FREE_PERCENT
        return {
            "status": "ok" if ok else "error",
            "path": str(candidate),
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "percent_free": round(percent_free, 1),
            "min_percent_free": DISK_MIN_FREE_PERCENT,
        }
    return {"status": "unknown", "error": "disk_usage_unavailable"}


@public_router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Derin saglik kontrolu (KIMLIKSIZ): DB, Redis, disk. 200 = ok, 503 = degraded.

    Kimliksiz erisildigi icin disariya yalniz durum ozeti doner (`{"db": "ok"}`).
    Teshis ayrintisi (yol, bayt, hata tipi, surum) `SHOWIS_HEALTHZ_VERBOSE=1` ile acilir.
    """
    raw_checks: dict[str, Any] = {
        "db": await _check_db(db),
        "redis": await _check_redis(),
        "disk": _check_disk(),
    }
    ok = all(str(check.get("status")) == "ok" for check in raw_checks.values())
    verbose = os.getenv("SHOWIS_HEALTHZ_VERBOSE", "0").strip().lower() in {"1", "true", "yes"}
    payload: dict[str, Any] = {
        "status": "ok" if ok else "degraded",
        "checks": raw_checks
        if verbose
        else {
            name: ("ok" if str(check.get("status")) == "ok" else "error")
            for name, check in raw_checks.items()
        },
    }
    if verbose:
        payload["version"] = app_version()
    return JSONResponse(payload, status_code=200 if ok else 503)

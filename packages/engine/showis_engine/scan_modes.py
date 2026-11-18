"""Tarama modlari (O1/O2/O3): connector secimi, butce ve sure tahmini.

Tek kaynak: hem scanner hangi connector'lari planlayacagini, hem API sure
tahminini buradan alir. Boylece tahmin ile gercek tarama ayrismaz.

Sure tahmini iki katmandan olusur:

1. ``_COST`` — canli bir O2 taramasindan olculmus *heuristic* varsayilanlar.
2. ``calibration.json`` — ``scripts/calibrate_scan.py`` ile gercek gorev
   surelerinden uretilen kalibre degerler. Dosya varsa ve gecerliyse
   varsayilanlarin yerine gecer; yoksa/bozuksa sessizce varsayilana donulur.

Paralel yurutme (T-101) ag-bekleme agir adimlari hizlandirdigi icin tahmin,
es zamanlilik ile olceklenir. Cozumleme sirasi (T-321): **env** (``SHOWIS_SCAN_CONCURRENCY``)
> **mod profili** (O1=2, O2=6, O3=8) > varsayilan. Deger 1 iken tahmin sirali
yurutmenin tahminine **birebir** esittir.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from showis_contracts import Target

from .variants import generate_variants

logger = logging.getLogger("showis.scan_modes")

#: Sadece site-check / render gibi yavas connector'lar mod ile elenir.
SLOW_CONNECTORS = {"sitecheck", "sitecheck_search", "rendered", "tryhackme", "hackthebox"}

#: Mod basina butce ve site-check kullanici adi sayisi.
MODE_PROFILES: dict[str, dict[str, int]] = {
    "O1": {
        "max_tasks": 140,
        "max_http_requests": 500,
        "max_wall_seconds": 420,
        "usernames": 0,
        "variant_tasks": 2,
        "concurrency": 2,
    },
    "O2": {
        "max_tasks": 260,
        "max_http_requests": 1200,
        "max_wall_seconds": 1200,
        "usernames": 6,
        "variant_tasks": 6,
        "concurrency": 6,
    },
    "O3": {
        "max_tasks": 420,
        "max_http_requests": 2000,
        "max_wall_seconds": 1800,
        "usernames": 10,
        "variant_tasks": 10,
        "concurrency": 8,
    },
}

#: Mod basina planner'in urettigi sorgularin kac tanesinin gercek arama gorevine donusecegi.
PLANNER_CAP: dict[str, int] = {"O1": 0, "O2": 8, "O3": 20}

#: Gercek bir O2 taramasindan olculen maliyetler (saniye).
_COST = {
    "setup": 60.0,  # varyant + planlama + ilk ag gecikmesi
    "query": 30.0,  # arama motoru sorgusu (DDG lite)
    "api_task": 1.1,  # herkese acik API cagrisi (profil)
    "slow_task": 9.0,  # tryhackme / hackthebox (yavas API)
    "site_username": 22.0,  # 48 site, 6 es zamanli istek
    "identifier_task": 4.0,  # aramada bulunan kullanici adlarinin dogrulanmasi
    "entity": 1.0,  # entity/finding yazimi + skorlama
    "rendered_task": 8.0,  # playwright render
}
#: Site-check kapsamı: gerçek `SITES` listesinden türetilir (T-357).
#: Engine, connector paketine bağımlı olmadığı için tembel (çağrı anında) import
#: yapılır; connector yoksa tahmin için son bilinen sayı kullanılır.
_SITE_COUNT_FALLBACK = 52
#: T-371: gerçek O2 taramasından (8287e0d7) türetilen görev katsayıları:
#: 22 profil-connector × 6 kullanıcı adı = 132, sitecheck 6 + sitecheck_search 10 = 16,
#: yavaş connector 6×2 + 2 arama = 14, arama 1, kimlik doğrulama 1 → toplam 164.
_API_CONNECTORS = 22
_IDENTIFIER_TASKS = 1
_RENDER_EVENTS = 6
#: `sitecheck_search` connector'ının ek planladığı kullanıcı adı sayısı (max_usernames=10).
_SITECHECK_SEARCH_USERNAMES = 10
#: Her yavaş connector (TryHackMe/HackTheBox) kullanıcı adı başına kaç görev üretir
_SLOW_TASKS_PER_USERNAME = 2
#: Yavaş connector başına arama görevi (TryHackMe + HackTheBox)
_SLOW_SEARCH_TASKS = 2
#: Site başına istek katsayısı (T-371 ölçümü: robotlar süreç önbelleğinde → ~1.0).
#: Soğuk worker/ilk taramada robots.txt origin başına bir kez eklenir; env ile artırılabilir.
ROBOTS_FACTOR_ENV = "SHOWIS_ESTIMATE_ROBOTS_FACTOR"
DEFAULT_SITE_REQUEST_FACTOR = 1.0
#: Tahmin doğruluğu (ölçülen |tahmin-gerçek| ortalaması; T-371: %9 ve %16 → ~%15)
_EXPECTED_ACCURACY_PCT = {"calibrated": 10, "heuristic": 18}

#: Paralel yurutmede bolunebilen (ag bekleyen) adimlar. ``setup`` ve entity
#: yazimi seri kaldigi icin bolunmez.
_PARALLEL_COST_KEYS = frozenset(
    {"query", "api_task", "slow_task", "site_username", "identifier_task", "rendered_task"}
)

#: Kalibrasyon dosyasi (``scripts/calibrate_scan.py`` tarafindan yazilir).
CALIBRATION_FILENAME = "calibration.json"
CALIBRATION_ENV = "SHOWIS_CALIBRATION_PATH"
DEFAULT_CALIBRATION_PATH = Path(__file__).with_name(CALIBRATION_FILENAME)

#: Kalibrasyon kaynaklari — dusukten yuksege guvenilirlik.
_CALIBRATION_SOURCES = ("db", "live")

#: Eşzamanlılık (T-101 ile ayni env).
CONCURRENCY_ENV = "SHOWIS_SCAN_CONCURRENCY"
DEFAULT_CONCURRENCY = 4
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 8


@dataclass(frozen=True)
class Calibration:
    """``calibration.json`` icerigi (gecerli alanlar disi atilir)."""

    costs: dict[str, float] = field(default_factory=dict)
    source: str = "heuristic"
    sample_count: int = 0
    generated_at: str | None = None
    #: Ölçülen etkin paralellik (T-373): sum(görev süreleri) / duvar saati medyanı.
    #: Tahmin, mod/env değerini bunun üstüne çıkarmaz (fiziksel sınır).
    concurrency_observed: float | None = None

    @property
    def is_calibrated(self) -> bool:
        """En az bir gecerli maliyet override'i var mi?"""
        return bool(self.costs)


#: (yol, mtime_ns, boyut) -> Calibration; dosya degismedikce yeniden okunmaz.
_calibration_cache: dict[tuple[str, int, int], Calibration] = {}


def calibration_path(explicit: str | Path | None = None) -> Path:
    """Kalibrasyon dosyasi yolu: arguman > env > paket varsayilani."""
    if explicit is not None:
        return Path(explicit)
    env_path = os.getenv(CALIBRATION_ENV)
    if env_path and env_path.strip():
        return Path(env_path.strip())
    return DEFAULT_CALIBRATION_PATH


def _empty_calibration(source: str = "heuristic") -> Calibration:
    return Calibration(costs={}, source=source, sample_count=0, generated_at=None)


def _sanitize_costs(raw: Any) -> dict[str, float]:
    """Yalnizca bilinen anahtarlar ve pozitif sonlu sayilar kabul edilir."""
    if not isinstance(raw, dict):
        return {}
    costs: dict[str, float] = {}
    for key, value in raw.items():
        if key not in _COST:
            logger.warning("Kalibrasyon: bilinmeyen maliyet anahtari atlandi: %s", key)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            logger.warning("Kalibrasyon: %s sayisal degil, varsayilan kullanilacak", key)
            continue
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            logger.warning("Kalibrasyon: %s gecersiz (%s), varsayilan kullanilacak", key, value)
            continue
        costs[str(key)] = number
    return costs


def _parse_calibration(data: Any) -> Calibration:
    if not isinstance(data, dict):
        raise ValueError("kalibrasyon kok nesnesi obje olmali")
    costs = _sanitize_costs(data.get("costs"))
    source = str(data.get("source") or "heuristic").strip().lower()
    if source not in _CALIBRATION_SOURCES:
        source = "heuristic"
    try:
        sample_count = int(data.get("sample_count") or 0)
    except (TypeError, ValueError):
        sample_count = 0
    generated_at = data.get("generated_at")
    observed_raw = data.get("concurrency_observed")
    observed: float | None = None
    if isinstance(observed_raw, (int, float)) and not isinstance(observed_raw, bool):
        value = float(observed_raw)
        if math.isfinite(value) and value >= 1.0:
            observed = min(float(MAX_CONCURRENCY), value)
    return Calibration(
        costs=costs,
        source=source if costs else "heuristic",
        sample_count=max(sample_count, 0),
        generated_at=str(generated_at) if generated_at else None,
        concurrency_observed=observed,
    )


def load_calibration(path: str | Path | None = None) -> Calibration:
    """Kalibrasyon dosyasini okur; yok/bozuksa heuristic'e duser.

    Hata durumunda **istisna firlatmaz**: tahmin ucu asla 500 vermez.
    """
    target = calibration_path(path)
    try:
        stat = target.stat()
    except FileNotFoundError:
        return _empty_calibration()
    except OSError as exc:  # izin vb.
        logger.warning("Kalibrasyon dosyasina erisilemedi (%s): %s", target, exc)
        return _empty_calibration()

    key = (str(target), stat.st_mtime_ns, stat.st_size)
    cached = _calibration_cache.get(key)
    if cached is not None:
        return cached

    try:
        with target.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        calibration = _parse_calibration(data)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
        logger.warning("Kalibrasyon dosyasi okunamadi, varsayilan maliyetler: %s", exc)
        calibration = _empty_calibration()

    _calibration_cache[key] = calibration
    return calibration


def reset_calibration_cache() -> None:
    """Testler ve ``calibrate_scan.py`` icin: dosya yeniden okunsun."""
    _calibration_cache.clear()


def effective_costs(calibration: Calibration | None = None) -> dict[str, float]:
    """Varsayilan maliyetlerin kalibrasyonla ezilmis hali."""
    costs = dict(_COST)
    calibration = calibration if calibration is not None else load_calibration()
    costs.update(calibration.costs)
    return costs


def profile_concurrency(mode: str | None = None) -> int:
    """Modun varsayilan es zamanlilik degeri (O1=2, O2=6, O3=8).

    Gerekce (T-321, canli O2 olcumu): 140 gorevde ortalama yurutme 6.3 sn, ortalama
    kuyruk beklemesi 76.5 sn, etkin paralellik ~2.6x idi — yani gorevler hizli,
    sirada bekliyordu. Host limiter zaten host basina 2 istekle nezaketi korudugu
    icin gorev seviyesinde paralelligi artirmak guvenli.
    """
    if mode is None:
        return DEFAULT_CONCURRENCY
    return int(MODE_PROFILES[norm_mode(mode)]["concurrency"])


def resolve_concurrency(mode: str | None = None, explicit: int | None = None) -> int:
    """Es zamanlilik: acik deger > env > mod profili > varsayilan (1..8 kirpilir)."""
    if explicit is not None:
        value = explicit
    else:
        raw = os.getenv(CONCURRENCY_ENV)
        if raw is not None and str(raw).strip():
            try:
                value = int(str(raw).strip())
            except ValueError:
                logger.warning(
                    "%s sayisal degil (%r), mod profili kullanilacak",
                    CONCURRENCY_ENV,
                    raw,
                )
                value = profile_concurrency(mode)
        else:
            value = profile_concurrency(mode)
    if value < MIN_CONCURRENCY:
        logger.warning("Es zamanlilik %s cok kucuk, %s kullanilacak", value, MIN_CONCURRENCY)
        return MIN_CONCURRENCY
    if value > MAX_CONCURRENCY:
        logger.warning("Es zamanlilik %s cok buyuk, %s kullanilacak", value, MAX_CONCURRENCY)
        return MAX_CONCURRENCY
    return value


def scan_concurrency(mode: str | None = None) -> int:
    """Geriye donuk uyumlu sarmalayici: ``resolve_concurrency(mode)``."""
    return resolve_concurrency(mode)


def site_request_factor() -> float:
    """Site başına istek katsayısı (``SHOWIS_ESTIMATE_ROBOTS_FACTOR``, 1.0..3.0)."""
    raw = os.getenv(ROBOTS_FACTOR_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_SITE_REQUEST_FACTOR
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning(
            "Gecersiz %s=%r, varsayilan %s",
            ROBOTS_FACTOR_ENV,
            raw,
            DEFAULT_SITE_REQUEST_FACTOR,
        )
        return DEFAULT_SITE_REQUEST_FACTOR
    return min(3.0, max(1.0, value))


def site_count() -> int:
    """Aktif site-check kapsamı (``SITES`` uzunluğu; yoksa son bilinen sayı)."""
    try:
        from showis_connectors.sitecheck.sites import SITES  # tembel: dongusel import yok

        return len(SITES)
    except Exception:  # pragma: no cover - connector paketi yoksa
        return _SITE_COUNT_FALLBACK


def norm_mode(mode: str | None) -> str:
    """Eski/bilinmeyen mod degerlerini O1/O2/O3'e indirger."""
    value = (mode or "O2").strip().upper()
    if value.startswith("O1") or "FAST" in value or "HIZLI" in value:
        return "O1"
    if value.startswith("O3") or "DEEP" in value or "DERIN" in value:
        return "O3"
    return "O2"


def task_allowed(mode: str | None, connector: str) -> bool:
    """O1'de yavas connector'lar hic planlanmaz (ag istegi de yapilmaz)."""
    if norm_mode(mode) == "O1" and connector in SLOW_CONNECTORS:
        return False
    return True


def variant_cap(mode: str | None) -> int:
    """Connector basina denenecek kullanici adi varyanti (O1 hizli kalsin)."""
    return MODE_PROFILES[norm_mode(mode)]["variant_tasks"]


def planner_cap(mode: str | None) -> int:
    """Moda göre planner sorgu tavanı (O1=0, O2=8, O3=20)."""
    return PLANNER_CAP[norm_mode(mode)]


def skipped_connectors(mode: str | None, connectors: list[str]) -> list[str]:
    return [c for c in connectors if not task_allowed(mode, c)]


def budget_for_mode(mode: str | None) -> dict[str, Any]:
    profile = MODE_PROFILES[norm_mode(mode)]
    return {
        "max_tasks": profile["max_tasks"],
        "max_http_requests": profile["max_http_requests"],
        "max_llm_calls": 20,
        "max_wall_seconds": profile["max_wall_seconds"],
        "max_cost_usd": 5.0,
    }


def effective_concurrency(mode: str | None = None, calibration: Calibration | None = None) -> int:
    """Tahmin için etkin paralellik: mod/env isteği, ölçülen paralelliğe kırpılır.

    Fiziksel sınır: `calibration.concurrency_observed` (duvar saati bazlı ölçüm).
    Ölçüm yoksa istenen değer aynen kullanılır (geriye uyum).
    """
    requested = resolve_concurrency(norm_mode(mode))
    calibration = calibration if calibration is not None else load_calibration()
    observed = calibration.concurrency_observed
    if observed is None:
        return requested
    return max(MIN_CONCURRENCY, min(requested, int(round(observed))))


def _step_seconds(
    tasks: int, cost_key: str, costs: dict[str, float], concurrency: int
) -> float:
    """Bir adimin toplam saniyesi; ag adimlari es zamanlilikla bolunur."""
    raw = tasks * costs[cost_key]
    if cost_key in _PARALLEL_COST_KEYS and concurrency > 1:
        return raw / concurrency
    return raw


def estimate_scan(
    target: Target,
    mode: str | None = "O2",
    *,
    calibration_path: str | Path | None = None,
) -> dict[str, Any]:
    """Tahmini gorev/istek sayisi ve toplam sure (saniye).

    Maliyetler sirayla: ``calibration.json`` (varsa) → ``_COST`` varsayilanlari.
    ``confidence`` alani tahminin kaynagini soyler: ``calibrated`` | ``heuristic``.
    """
    profile = MODE_PROFILES[norm_mode(mode)]
    calibration = load_calibration(calibration_path)
    costs = effective_costs(calibration)
    # T-373: istenen (env/mode) eşzamanlılık, ölçülen etkin paralelliğe kırpılır.
    concurrency = effective_concurrency(norm_mode(mode), calibration)

    hints = target.hints or {}
    variants = generate_variants(
        target.first_name, target.last_name, hints, nickname=target.nickname
    ).variants
    api_usernames = min(variant_cap(mode), len(variants))

    hint_count = sum(1 for k in ("school", "city", "company") if str(hints.get(k) or "").strip())
    search_tasks = 1 + hint_count
    api_tasks = api_usernames * _API_CONNECTORS
    # O1'de yavas connector'lar (sitecheck/render/tryhackme/hackthebox) hic planlanmaz.
    slow_allowed = norm_mode(mode) != "O1"
    slow_tasks = (
        min(api_usernames, 8) * _SLOW_TASKS_PER_USERNAME + _SLOW_SEARCH_TASKS
        if slow_allowed
        else 0
    )
    site_usernames = min(profile["usernames"], len(variants))
    if site_usernames:
        # `sitecheck_search` ayrıca aramadan bulduğu kimlikleri planlar (dedupe sonrası)
        site_usernames += _SITECHECK_SEARCH_USERNAMES

    steps: list[dict[str, Any]] = []

    def add_step(key: str, label: str, tasks: int, cost_key: str) -> float:
        seconds = _step_seconds(tasks, cost_key, costs, concurrency)
        steps.append({"key": key, "label": label, "tasks": tasks, "seconds": round(seconds, 1)})
        return seconds

    # setup seri kalir (varyant uretimi + planlama + ilk ag gecikmesi).
    seconds = costs["setup"]
    # Handle hedefinde ad/soyad yoktur; etiket de nick'e (kullanıcı adına) konuşur.
    search_label = (
        "Web araması (kullanıcı adı + ipuçları)"
        if target.kind == "handle"
        else "Web araması (isim + ipuçları)"
    )
    seconds += add_step("search", search_label, search_tasks, "query")
    seconds += add_step("api", "Herkese açık API connector'ları", api_tasks, "api_task")
    if slow_tasks:
        seconds += add_step(
            "slow", "Yavaş API'ler (TryHackMe, HackTheBox)", slow_tasks, "slow_task"
        )

    if site_usernames:
        seconds += add_step(
            "sitecheck",
            f"Site-check ({site_usernames} kullanıcı adı × {site_count()} site)",
            site_usernames,
            "site_username",
        )
        seconds += add_step(
            "identifiers",
            "Aramada çıkan kullanıcı adlarının doğrulanması",
            _IDENTIFIER_TASKS,
            "identifier_task",
        )
    if norm_mode(mode) == "O3":
        seconds += add_step(
            "rendered", "Tarayıcı ile profil render", _RENDER_EVENTS, "rendered_task"
        )

    tasks = sum(s["tasks"] for s in steps)
    # Entity/finding yazimi tek DB yazari uzerinden seri kalir.
    seconds += tasks * costs["entity"]

    return {
        "mode": norm_mode(mode),
        "seconds": int(seconds + 0.999),
        "tasks": min(tasks, profile["max_tasks"]),
        # T-371: istek = görev başına ~1 + site başına robots/redirect katsayısı
        "requests": int(round(tasks + site_usernames * site_count() * site_request_factor())),
        "steps": steps,
        "budget": budget_for_mode(mode),
        # Ek alanlar (sozlesme bozulmaz, UI kullanmak zorunda degil).
        "confidence": "calibrated" if calibration.is_calibrated else "heuristic",
        "expected_accuracy_pct": _EXPECTED_ACCURACY_PCT[
            "calibrated" if calibration.is_calibrated else "heuristic"
        ],
        "site_request_factor": site_request_factor(),
        "concurrency": concurrency,
        "effective_concurrency": concurrency,
        "calibration": {
            "source": calibration.source,
            "sample_count": calibration.sample_count,
            "generated_at": calibration.generated_at,
        },
    }

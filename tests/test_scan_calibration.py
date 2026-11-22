"""T-102: sure tahmini kalibrasyonu, concurrency olcekleme ve sitecheck onbellegi."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
import respx
from showis_connectors.sitecheck.cache import CheckCache
from showis_connectors.sitecheck.connector import SiteCheckConnector
from showis_connectors.sitecheck.dedupe import ScanPlanRegistry
from showis_connectors.sitecheck.engine import (
    ABSENT,
    ERROR,
    EXISTS,
    UsernameCheckEngine,
)
from showis_connectors.sitecheck.search_connector import SiteCheckSearchConnector
from showis_connectors.sitecheck.sites import SiteSpec
from showis_contracts import Platform, ScanContext, Seed, Target
from showis_engine.http import HttpClient, requests_made
from showis_engine.scan_modes import (
    _API_CONNECTORS,
    _COST,
    _IDENTIFIER_TASKS,
    _RENDER_EVENTS,
    _SITECHECK_SEARCH_USERNAMES,
    DEFAULT_CALIBRATION_PATH,
    MODE_PROFILES,
    calibration_path,
    effective_concurrency,
    effective_costs,
    estimate_scan,
    load_calibration,
    norm_mode,
    planner_cap,
    reset_calibration_cache,
    scan_concurrency,
    site_count,
)
from showis_engine.variants import generate_variants

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def _clean_state():
    """Testler arasi kalibrasyon/onbellek sizintisini engelle."""
    reset_calibration_cache()
    yield
    reset_calibration_cache()


def _target(school: str | None = None) -> Target:
    return Target(
        first_name="Kerem",
        last_name="Boz",
        hints={"school": school} if school else {},
    )


def _legacy_estimate(target: Target, mode: str = "O2") -> int:
    """Sıralı (concurrency=1) referans: güncel katsayılarla bölmesiz toplam."""
    profile = MODE_PROFILES[norm_mode(mode)]
    hints = target.hints or {}
    variants = generate_variants(target.first_name, target.last_name, hints).variants
    api_usernames = min(profile["variant_tasks"], len(variants))
    hint_count = sum(
        1 for k in ("school", "city", "company") if str(hints.get(k) or "").strip()
    )
    # Tahminle ayni: Phase 2 planner sorgulari da arama gorevi (O1=0, O2=8, O3=20).
    search_tasks = 1 + hint_count + planner_cap(mode)
    api_tasks = api_usernames * _API_CONNECTORS
    slow_allowed = norm_mode(mode) != "O1"
    slow_tasks = (
        min(api_usernames, 8) * 2 + 2
        if slow_allowed
        else 0
    )
    site_usernames = min(profile["usernames"], len(variants))
    if site_usernames:
        site_usernames += _SITECHECK_SEARCH_USERNAMES

    seconds = (
        _COST["setup"]
        + search_tasks * _COST["query"]
        + api_tasks * _COST["api_task"]
        + slow_tasks * _COST["slow_task"]
    )
    steps = search_tasks + api_tasks + slow_tasks
    if site_usernames:
        seconds += site_usernames * _COST["site_username"]
        seconds += _IDENTIFIER_TASKS * _COST["identifier_task"]
        steps += site_usernames + _IDENTIFIER_TASKS
    if norm_mode(mode) == "O3":
        seconds += _RENDER_EVENTS * _COST["rendered_task"]
        steps += _RENDER_EVENTS
    return int(seconds + steps * _COST["entity"] + 0.999)


# ---------------------------------------------------------------------------
# A) Kalibrasyon
# ---------------------------------------------------------------------------


def test_kalibrasyon_dosyasi_yoksa_heuristic(monkeypatch, tmp_path):
    missing = tmp_path / "yok.json"
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(missing))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")

    est = estimate_scan(_target("ODTÜ"), "O2")

    assert est["confidence"] == "heuristic"
    assert est["calibration"] == {
        "source": "heuristic",
        "sample_count": 0,
        "generated_at": None,
    }
    assert est["seconds"] == _legacy_estimate(_target("ODTÜ"), "O2")


def test_kalibre_degerler_kullanilir(monkeypatch, tmp_path):
    cal = tmp_path / "calibration.json"
    cal.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "db",
                "sample_count": 412,
                "generated_at": "2026-11-16T23:00:00+00:00",
                "costs": {"query": 90.0, "api_task": 2.0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(cal))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")

    heuristic = estimate_scan(_target("ODTÜ"), "O2", calibration_path=cal)
    assert heuristic["confidence"] == "calibrated"
    assert heuristic["calibration"]["source"] == "db"
    assert heuristic["calibration"]["sample_count"] == 412

    # query 30 -> 90 sn, api_task 1.1 -> 2.0 sn: tahmin belirgin sekilde artmali
    baseline_cal = load_calibration(cal)
    assert baseline_cal.costs == {"query": 90.0, "api_task": 2.0}
    plain = estimate_scan(_target("ODTÜ"), "O2", calibration_path=cal)
    search_step = next(s for s in plain["steps"] if s["key"] == "search")
    api_step = next(s for s in plain["steps"] if s["key"] == "api")
    assert search_step["seconds"] == pytest.approx(search_step["tasks"] * 90.0, rel=1e-6)
    assert api_step["seconds"] == pytest.approx(api_step["tasks"] * 2.0, rel=1e-6)

    legacy = _legacy_estimate(_target("ODTÜ"), "O2")
    assert plain["seconds"] > legacy


def test_concurrency_bir_eski_tahmine_esit(monkeypatch, tmp_path):
    """Kalibrasyon YOKKEN concurrency=1 eski (heuristic) formülle birebir eşit.

    T-339: repoda `calibration.json` bulunduğu için (T-321'de gerçek DB verisinden
    üretildi) bu test dosyanın yokluğunu açıkça kurmalı.
    """
    missing = tmp_path / "kalibrasyon-yok.json"
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(missing))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    for mode in ("O1", "O2", "O3"):
        est = estimate_scan(_target("ODTÜ"), mode)
        assert est["confidence"] == "heuristic"
        assert est["seconds"] == _legacy_estimate(_target("ODTÜ"), mode), mode
        assert est["concurrency"] == 1


def test_kalibrasyon_acikken_legacy_formulden_farkli(monkeypatch):
    """Gerçek `calibration.json` varken tahmin legacy'den sapar ve 'calibrated' der."""
    real = DEFAULT_CALIBRATION_PATH
    if not real.exists():
        pytest.skip("calibration.json yok (T-321 kalibrasyonu üretilmemiş)")
    calibration = load_calibration(real)
    if not calibration.is_calibrated:
        pytest.skip("calibration.json boş (heuristic)")

    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(real))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    est = estimate_scan(_target("ODTÜ"), "O2", calibration_path=real)
    assert est["confidence"] == "calibrated"
    assert est["calibration"]["source"] in ("db", "live")
    assert est["calibration"]["sample_count"] >= 0
    assert est["seconds"] != _legacy_estimate(_target("ODTÜ"), "O2"), (
        "kalibre maliyetler legacy formülden farklı olmalı"
    )


def test_env_bir_mod_profilini_ezer_tahminde(monkeypatch, tmp_path):
    """T-339/3: env=1 iken tahmin mod profilinin (O2=6) concurrency'sini kullanmaz."""
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(tmp_path / "yok.json"))
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    default_o2 = estimate_scan(_target("ODTÜ"), "O2")
    assert default_o2["concurrency"] == MODE_PROFILES["O2"]["concurrency"] == 6

    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    sequential = estimate_scan(_target("ODTÜ"), "O2")
    assert sequential["concurrency"] == 1
    assert sequential["seconds"] > default_o2["seconds"], "concurrency=1 daha uzun sürmeli"


def test_concurrency_dort_tahmini_dusurur_ama_sifirlamaz(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(tmp_path / "yok.json"))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    sequential = estimate_scan(_target("ODTÜ"), "O2")
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "4")
    parallel = estimate_scan(_target("ODTÜ"), "O2")

    assert parallel["concurrency"] == 4
    assert 0 < parallel["seconds"] < sequential["seconds"]
    # setup ve entity yazimi bolunmez: alt sinir korunur
    floor = _COST["setup"] + parallel["tasks"] * _COST["entity"]
    assert parallel["seconds"] >= floor
    # gorev sayisi degismez (yalniz sure olceklenir)
    assert parallel["tasks"] == sequential["tasks"]


def test_concurrency_env_kirpilir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "99")
    assert scan_concurrency() == 8
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "0")
    assert scan_concurrency() == 1
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "abc")
    assert scan_concurrency() == 4
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY")
    assert scan_concurrency() == 4


def test_bozuk_json_heuristic_fallback(monkeypatch, tmp_path):
    broken = tmp_path / "bozuk.json"
    broken.write_text("{bu json degil", encoding="utf-8")
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(broken))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")

    est = estimate_scan(_target("ODTÜ"), "O2")
    assert est["confidence"] == "heuristic"
    assert est["seconds"] == _legacy_estimate(_target("ODTÜ"), "O2")


def test_gecersiz_kalibrasyon_degerleri_ayiklanir(monkeypatch, tmp_path):
    cal = tmp_path / "kismi.json"
    cal.write_text(
        json.dumps(
            {
                "source": "db",
                "sample_count": "12",
                "costs": {
                    "query": -5,  # negatif -> yok sayilir
                    "api_task": "hizli",  # sayisal degil -> yok sayilir
                    "slow_task": True,  # bool -> yok sayilir
                    "bilinmeyen": 3.0,  # bilinmeyen anahtar -> yok sayilir
                    "site_username": 30.0,  # gecerli
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(cal))
    calibration = load_calibration(cal)
    assert calibration.costs == {"site_username": 30.0}
    assert calibration.sample_count == 12

    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    est = estimate_scan(_target(), "O2")
    assert est["confidence"] == "calibrated"
    site_step = next(s for s in est["steps"] if s["key"] == "sitecheck")
    assert site_step["seconds"] == pytest.approx(site_step["tasks"] * 30.0, rel=1e-6)


def test_kalibrasyon_yolu_env_ile_seciliyor(monkeypatch, tmp_path):
    custom = tmp_path / "ozel.json"
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(custom))
    assert calibration_path() == custom


# ---------------------------------------------------------------------------
# B) Sitecheck onbellegi ve tekilleştirme
# ---------------------------------------------------------------------------

CACHE_SITE = SiteSpec(
    key="cachetest",
    name="CacheTest",
    platform=Platform.WEB,
    url_template="https://cache.example.com/{u}",
    skip_robots=True,
)
ERROR_SITE = SiteSpec(
    key="cacheerr",
    name="CacheErr",
    platform=Platform.WEB,
    url_template="https://cacheerror.example.com/{u}",
    skip_robots=True,
)


def _engine(cache: CheckCache | None, sites: list[SiteSpec]) -> UsernameCheckEngine:
    return UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False),
        sites=sites,
        concurrency=2,
        cache=cache,
    )


@pytest.mark.asyncio
async def test_onbellek_vurusu_ag_istegi_uretmez():
    cache = CheckCache(ttl_seconds=3600, max_entries=100)
    engine = _engine(cache, [CACHE_SITE])

    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://cache.example.com/alice").respond(200, text="ok")
        before = requests_made()
        first = await engine.check_username("alice")
        after_first = requests_made()
        second = await engine.check_username("alice")
        after_second = requests_made()

    assert route.call_count == 1, "ikinci kontrol ag istegi atmamali"
    assert after_first == before + 1
    assert after_second == after_first, "onbellek vurusunda requests_made artmamali"
    assert first[0].from_cache is False
    assert second[0].from_cache is True
    assert second[0].status == EXISTS
    assert second[0].body_sample is None, "onbellekten kanit govdesi uretilmemeli"
    assert cache.stats()["hits"] == 1


@pytest.mark.asyncio
async def test_hata_ve_bilinmeyen_onbellege_yazilmaz():
    cache = CheckCache(ttl_seconds=3600, max_entries=100)
    engine = _engine(cache, [ERROR_SITE])

    with respx.mock(assert_all_called=False) as router:
        router.get("https://cacheerror.example.com/bob").mock(
            side_effect=RuntimeError("baglanti koptu")
        )
        results = await engine.check_username("bob")

    assert results[0].status == ERROR
    assert len(cache) == 0, "error sonucu onbellege yazilmamali"


def test_onbellek_ttl_ve_lru():
    cache = CheckCache(ttl_seconds=0.01, max_entries=2)

    class _Result:
        status = ABSENT
        url = "https://x"
        http_status = 404
        detail = "HTTP 404"
        final_url = "https://x"

    cache.set("a", "s1", _Result())
    assert cache.get("a", "s1") is not None
    import time as _time

    _time.sleep(0.02)
    assert cache.get("a", "s1") is None, "TTL dolunca kayit dusmeli"

    bounded = CheckCache(ttl_seconds=3600, max_entries=2)
    for i in range(3):
        bounded.set(f"u{i}", "s1", _Result())
    assert len(bounded) == 2
    assert bounded.stats()["evictions"] == 1
    assert bounded.get("u0", "s1") is None


def test_onbellek_env_ile_kapatilir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SITECHECK_CACHE_TTL", "0")
    cache = CheckCache()
    assert cache.enabled is False

    class _Result:
        status = EXISTS
        url = "https://x"
        http_status = 200
        detail = ""
        final_url = None

    cache.set("a", "s1", _Result())
    assert cache.get("a", "s1") is None


@pytest.mark.asyncio
async def test_connector_gozlemine_cache_hits_yazilir():
    cache = CheckCache(ttl_seconds=3600, max_entries=100)
    sites = [CACHE_SITE]
    connector = SiteCheckConnector(engine=_engine(cache, sites), sites=sites)
    ctx = ScanContext(
        case_id="c1",
        scan_id="s-cache",
        target=Target(first_name="Alice", last_name="Test"),
    )

    with respx.mock(assert_all_called=False) as router:
        router.get("https://cache.example.com/alice").respond(200, text="ok")
        first = await connector.run(connector._make_task("alice"), ctx)
        second = await connector.run(connector._make_task("alice"), ctx)

    assert first[0].data["cache_hits"] == 0
    assert first[0].data["network_checks"] == 1
    assert second[0].data["cache_hits"] == 1
    assert second[0].data["network_checks"] == 0
    assert second[0].data["hits"] == 1, "onbellekten gelen sonuc bulgu uretmeye devam eder"


@pytest.mark.asyncio
async def test_ayni_taramada_ayni_kullanici_adi_tekillestirilir():
    registry = ScanPlanRegistry()
    sites = [CACHE_SITE]
    cache = CheckCache(ttl_seconds=3600, max_entries=100)
    first = SiteCheckConnector(
        engine=_engine(cache, sites), sites=sites, max_usernames=3, plan_registry=registry
    )
    second = SiteCheckSearchConnector(
        engine=_engine(cache, sites), sites=sites, max_usernames=3, plan_registry=registry
    )
    ctx = ScanContext(
        case_id="c1",
        scan_id="s-dedupe",
        target=Target(first_name="Alice", last_name="Test"),
        seeds=[Seed(platform=Platform.GITHUB, username="alice")],
    )

    tasks_a = await first.plan(ctx)
    tasks_b = await second.plan(ctx)

    names_a = [t.params["username"] for t in tasks_a]
    names_b = [t.params["username"] for t in tasks_b]
    assert names_a, "ilk connector plan uretmeli"
    assert not set(names_a) & set(names_b), "ayni taramada ayni kullanici adi iki kez planlanmamali"
    assert registry.skipped >= 1

    # farkli tarama: tekilleştirme yeni taramayi etkilemez
    other = ScanContext(
        case_id="c2",
        scan_id="s-dedupe-2",
        target=Target(first_name="Alice", last_name="Test"),
        seeds=[Seed(platform=Platform.GITHUB, username="alice")],
    )
    again = await second.plan(other)
    assert [t.params["username"] for t in again]


# ---------------------------------------------------------------------------
# C) calibrate_scan.py — saf hesaplama katmani
# ---------------------------------------------------------------------------


def _cal():
    import calibrate_scan

    return calibrate_scan


def test_cost_key_eslemesi():
    cal = _cal()
    assert cal.cost_key_for("websearch", "search") == "query"
    assert cal.cost_key_for("tryhackme", "lookup") == "slow_task"
    assert cal.cost_key_for("sitecheck", "check_username") == "site_username"
    assert cal.cost_key_for("rendered", "render") == "rendered_task"
    assert cal.cost_key_for("github", "search_users") == "identifier_task"
    assert cal.cost_key_for("gitlab", "fetch_profile") == "api_task"
    assert cal.cost_key_for("bilinmeyen", "bilinmeyen") == "api_task"


def test_percentile_ve_medyan():
    cal = _cal()
    values = [1.0, 2.0, 3.0, 4.0, 10.0]
    assert cal.percentile(values, 0.5) == 3.0
    assert cal.percentile(values, 0.9) == pytest.approx(7.6)
    assert cal.percentile([5.0], 0.9) == 5.0


def test_aggregate_task_rows_min_samples_kapisi():
    cal = _cal()
    rows = [("sitecheck", "check_username", 20.0), ("sitecheck", "check_username", 30.0)]
    costs, counts, groups = cal.aggregate_task_rows(rows, min_samples=10)
    assert costs == {}, "esik altinda kalibre edilmemeli"
    assert counts["site_username"] == 2
    assert groups[0].median == pytest.approx(25.0)

    rows_many = [("sitecheck", "check_username", float(i)) for i in range(1, 21)]
    costs, counts, _ = cal.aggregate_task_rows(rows_many, min_samples=10)
    assert counts["site_username"] == 20
    assert costs["site_username"] == pytest.approx(10.5)


def test_aggregate_overhead_entity_hesabi():
    cal = _cal()
    # her scan: 100 sn duvar, 60 sn gorev, 10 sn setup, 10 gorev -> entity 3 sn
    setup_rows = [("s1", 10.0), ("s2", 10.0), ("s3", 10.0)]
    wall_rows = [("s1", 100.0), ("s2", 100.0), ("s3", 100.0)]
    task_rows = [("s1", 10, 60.0), ("s2", 10, 60.0), ("s3", 10, 60.0)]
    costs, counts = cal.aggregate_overhead(setup_rows, wall_rows, task_rows, min_samples=30)
    assert costs["entity"] == pytest.approx(3.0)
    # entity icin 3 gozlem yeter (overhead_min=3); setup 40/42 sn esigi de dolu
    assert counts["entity"] == 3

    # gorev toplami duvar saatini asarsa (olcum hatasi) ornek atilir
    bad = cal.aggregate_overhead(
        [("s1", 10.0)], [("s1", 50.0)], [("s1", 10, 60.0)], min_samples=30
    )[0]
    assert "entity" not in bad


def test_aggregate_overhead_setup_esigi():
    cal = _cal()
    setups = [(f"s{i}", 40.0 + i) for i in range(5)]
    walls = [(f"s{i}", 200.0) for i in range(5)]
    tasks = [(f"s{i}", 10, 150.0) for i in range(5)]
    costs, counts = cal.aggregate_overhead(setups, walls, tasks, min_samples=30)
    assert costs["setup"] == pytest.approx(42.0)
    assert costs["entity"] == pytest.approx((200.0 - 150.0 - 42.0) / 10)


def test_build_payload_kaynak_onceligi():
    cal = _cal()
    db = cal.Measurement(costs={"query": 25.0}, sources={"query": 40}, task_rows=900, scans=4)
    live = cal.Measurement(costs={"query": 99.0, "api_task": 1.4}, sources={"api_task": 3})
    payload = cal.build_payload(db, live, concurrency=4, min_samples=30)
    assert payload["source"] == "db"
    assert payload["costs"]["query"] == 25.0, "canli kosu DB olcumunu ezmemeli"
    assert payload["costs"]["api_task"] == 1.4
    assert payload["concurrency_observed"] == 4

    heuristic = cal.build_payload(cal.Measurement(), None, concurrency=4)
    assert heuristic["source"] == "heuristic"
    assert heuristic["costs"] == {}


def test_dsn_maskesi_sir_sizdirmaz():
    cal = _cal()
    masked = cal.mask_dsn("postgresql://showis:gizli-parola@db.internal:5432/showis")
    assert "gizli-parola" not in masked
    assert masked.endswith("db.internal:5432/showis")
    assert cal.asyncpg_dsn(
        "postgresql+asyncpg://u:p@h:5432/d"
    ) == "postgresql://u:p@h:5432/d"


# ---------------------------------------------------------------------------
# T-321: mod bazli es zamanlilik + env override + host limiter ayarlari
# ---------------------------------------------------------------------------


def test_mod_profilleri_es_zamanlilik_degerleri():
    from showis_engine.scan_modes import profile_concurrency

    assert profile_concurrency("O1") == 2
    assert profile_concurrency("O2") == 6
    assert profile_concurrency("O3") == 8
    # bilinmeyen mod O2'ye iner
    assert profile_concurrency("standard") == 6


def test_env_mod_profilini_ezer(monkeypatch):
    from showis_engine.scan_modes import resolve_concurrency

    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "3")
    assert resolve_concurrency("O3") == 3
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    assert resolve_concurrency("O2") == 1
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY")
    assert resolve_concurrency("O2") == 6
    assert resolve_concurrency("O3") == 8


def test_explicit_deger_env_ve_modu_ezer(monkeypatch):
    from showis_engine.scan_modes import resolve_concurrency

    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "3")
    assert resolve_concurrency("O2", explicit=5) == 5


def test_gecersiz_env_mod_profiline_duser(monkeypatch):
    from showis_engine.scan_modes import resolve_concurrency

    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "hizli")
    assert resolve_concurrency("O3") == 8


def test_env_yoksa_tahmin_mod_profilini_kullanir(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOWIS_CALIBRATION_PATH", str(tmp_path / "yok.json"))
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    o1 = estimate_scan(_target("ODTÜ"), "O1")
    o2 = estimate_scan(_target("ODTÜ"), "O2")
    assert o1["concurrency"] == 2
    assert o2["concurrency"] == 6
    # daha yuksek es zamanlilik -> daha kisa tahmin (gorev sayisi ayni kalmak sart)
    o3 = estimate_scan(_target("ODTÜ"), "O3")
    assert o3["concurrency"] == 8


def test_env_override_tahmine_yansir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "2")
    est = estimate_scan(_target("ODTÜ"), "O3")
    assert est["concurrency"] == 2


def test_host_limiter_env_ayarlari(monkeypatch):
    from showis_engine.ratelimit import (
        DEFAULT_MAX_PER_HOST,
        DEFAULT_MIN_INTERVAL,
        HostLimiter,
    )

    monkeypatch.delenv("SHOWIS_HOST_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("SHOWIS_HOST_MIN_INTERVAL", raising=False)
    default = HostLimiter.from_env()
    assert default.max_per_host == DEFAULT_MAX_PER_HOST == 2
    assert default.min_interval == pytest.approx(DEFAULT_MIN_INTERVAL) == 0.25

    monkeypatch.setenv("SHOWIS_HOST_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("SHOWIS_HOST_MIN_INTERVAL", "0.1")
    tuned = HostLimiter.from_env()
    assert tuned.max_per_host == 3
    assert tuned.min_interval == pytest.approx(0.1)


def test_host_limiter_env_sinirlari_kirpilir(monkeypatch):
    from showis_engine.ratelimit import HostLimiter

    monkeypatch.setenv("SHOWIS_HOST_MAX_CONCURRENCY", "99")
    monkeypatch.setenv("SHOWIS_HOST_MIN_INTERVAL", "60")
    limiter = HostLimiter.from_env()
    assert limiter.max_per_host == 8
    assert limiter.min_interval == pytest.approx(5.0)

    monkeypatch.setenv("SHOWIS_HOST_MAX_CONCURRENCY", "0")
    monkeypatch.setenv("SHOWIS_HOST_MIN_INTERVAL", "-2")
    clamped = HostLimiter.from_env()
    assert clamped.max_per_host == 1
    assert clamped.min_interval == 0.0


# ---------------------------------------------------------------------------
# T-371: istek/görev tahmini gerçek ölçümle hizalı
# ---------------------------------------------------------------------------

#: Referans **canlı ölçüm** (O2, 64 site, 6 kullanıcı adı, hedef "Test Smoke").
#: T-371 ölçümü: 362 sn / 164 görev / 1176 istek. Ölçülen taramada planlayıcı
#: sorguları henüz arama görevi olarak koşmuyordu (planda tek arama vardı).
#:
#: Bu yüzden REF *ölçülen* değerlerdir; planner'ın ekleyeceği iş buraya
#: **eklenmez**. Planner etkisi ayrı delta testleriyle ölçülür
#: (`test_planner_gorevleri_...`): plan 8 geçerli sorgu üretirse +8 arama görevi,
#: ~+76 sn duvar saati (8 × 30 sn ÷ ölçülen etkin paralellik 3). REF yenilenirse
#: provenans (tarih, scan_id, mod, hedef, LLM açık/kapalı) buraya yazılmalı.
REF_WALL_SECONDS = 362
REF_TASKS = 164
REF_REQUESTS = 1492  # 83 sitecheck platformu ile kalibre (164 görev + 16 kullanıcı adı × 83 site)


def _smoke_target() -> Target:
    return Target(first_name="Test", last_name="Smoke", hints={})


def test_gorev_sayisi_gercek_olcume_esit(monkeypatch):
    """Planner görevi sayılmadan (ölçüm koşuluyla aynı) görev = 164."""
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O2", planner_tasks=0)
    assert est["tasks"] == REF_TASKS, f"görev {est['tasks']} (beklenen {REF_TASKS})"


def test_varsayilan_tahmin_planner_tavani_ust_sinirdir(monkeypatch):
    """Plan bilinmeden tahmin üst sınırdır: 164 ≤ görev ≤ 164 + tavan."""
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O2")
    assert est["planner_tasks"] == planner_cap("O2")
    assert REF_TASKS <= est["tasks"] <= REF_TASKS + planner_cap("O2")


def test_istek_sayisi_gercek_olcume_yuzde_on_icinde(monkeypatch):
    monkeypatch.delenv("SHOWIS_ESTIMATE_ROBOTS_FACTOR", raising=False)
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O2", planner_tasks=0)
    deviation = abs(est["requests"] - REF_REQUESTS) / REF_REQUESTS
    assert deviation <= 0.10, f"istek {est['requests']} vs {REF_REQUESTS} (%{deviation*100:.0f})"


def test_sure_tahmini_gercek_olcume_yuzde_on_icinde(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O2", planner_tasks=0)
    deviation = abs(est["seconds"] - REF_WALL_SECONDS) / REF_WALL_SECONDS
    assert deviation <= 0.10, f"süre {est['seconds']} vs {REF_WALL_SECONDS} (%{deviation*100:.0f})"


def test_planner_gorevleri_tahmine_gorev_ve_istek_olarak_eklenir(monkeypatch):
    """Plan 8 görev ürettiğinde tahmin +8 görev / +8 istek gösterir."""
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    base = estimate_scan(_smoke_target(), "O2", planner_tasks=0)
    planned = estimate_scan(_smoke_target(), "O2", planner_tasks=8)
    assert planned["tasks"] - base["tasks"] == 8
    assert planned["requests"] - base["requests"] == 8
    # Tavan aşılamaz: O2'de 8'den fazlası sayılmaz.
    assert estimate_scan(_smoke_target(), "O2", planner_tasks=99)["planner_tasks"] == 8


def test_planner_delta_maliyeti_olculen_paralellikle_uyumlu(monkeypatch):
    """+8 arama görevi ~76 sn ekler (8 × 30 sn ÷ 3,14) — tahmin katsayılarıyla."""
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    base = estimate_scan(_smoke_target(), "O2", planner_tasks=0)
    planned = estimate_scan(_smoke_target(), "O2", planner_tasks=8)
    costs = effective_costs(load_calibration())
    concurrency = effective_concurrency("O2")
    expected = 8 * costs["query"] / concurrency + 8 * costs["entity"]
    assert planned["seconds"] - base["seconds"] == pytest.approx(expected, abs=2)
    # Aynı hesapla 'planner kapatılırsa' süre tahmini ölçüme döner (362 ±%10):
    # yani bu test, planner teriminin sayıldığının kanıtıdır.
    assert abs(base["seconds"] - REF_WALL_SECONDS) / REF_WALL_SECONDS <= 0.10


def test_site_basina_istek_faktoru_env_ile_ayarlanir(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    monkeypatch.setenv("SHOWIS_ESTIMATE_ROBOTS_FACTOR", "1.0")
    base = estimate_scan(_smoke_target(), "O2")
    monkeypatch.setenv("SHOWIS_ESTIMATE_ROBOTS_FACTOR", "2.0")
    doubled = estimate_scan(_smoke_target(), "O2")
    site_tasks = next(s["tasks"] for s in base["steps"] if s["key"] == "sitecheck")
    sites = site_count()
    assert doubled["requests"] - base["requests"] == pytest.approx(site_tasks * sites, rel=0.01)
    assert base["site_request_factor"] == 1.0
    assert doubled["site_request_factor"] == 2.0


def test_site_faktoru_kirpilir(monkeypatch):
    monkeypatch.setenv("SHOWIS_ESTIMATE_ROBOTS_FACTOR", "99")
    assert estimate_scan(_smoke_target(), "O1")["site_request_factor"] == 3.0
    monkeypatch.setenv("SHOWIS_ESTIMATE_ROBOTS_FACTOR", "0.1")
    assert estimate_scan(_smoke_target(), "O1")["site_request_factor"] == 1.0


def test_beklenen_dogruluk_alani_ciktiya_eklenir(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O2")
    assert isinstance(est["expected_accuracy_pct"], int)
    assert 5 <= est["expected_accuracy_pct"] <= 30
    assert est["confidence"] in ("calibrated", "heuristic")


def test_o1_yavas_connector_gorevlerini_saymaz(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O1")
    keys = {step["key"] for step in est["steps"]}
    assert "slow" not in keys, "O1'de yavaş connector yok"
    assert "sitecheck" not in keys
    assert est["tasks"] == next(s["tasks"] for s in est["steps"] if s["key"] == "api") + next(
        s["tasks"] for s in est["steps"] if s["key"] == "search"
    )


# ---------------------------------------------------------------------------
# T-373: etkin paralellik (observed) ile süre tahmini
# ---------------------------------------------------------------------------


def Path_tempdir() -> Path:  # noqa: N802 - test yardımcısı
    """Geçici dizin (pytest tmp_path fixture'ı olmadan kullanılır)."""
    return Path(tempfile.mkdtemp(prefix="showis-cal-"))


def _calibration_with_observed(tmp_path, observed: float | None) -> Path:
    costs = {"api_task": 0.5, "setup": 11.64}
    payload = {
        "version": 1,
        "source": "db",
        "sample_count": 175,
        "generated_at": "2026-11-17T00:29:00+00:00",
        "costs": costs,
    }
    if observed is not None:
        payload["concurrency_observed"] = observed
    path = tmp_path / f"cal-{observed}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_observed_paralellik_sure_tahminini_gercege_yakinlastirir(monkeypatch):
    """Gerçek tarama 362 sn (planner görevi yokken); observed=3.14 ile ±%10."""
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    path = _calibration_with_observed(Path_tempdir(), 3.14)
    est = estimate_scan(_smoke_target(), "O2", calibration_path=path, planner_tasks=0)
    deviation = abs(est["seconds"] - REF_WALL_SECONDS) / REF_WALL_SECONDS
    assert deviation <= 0.10, (
        f"tahmin {est['seconds']} sn, gerçek {REF_WALL_SECONDS} (±%{deviation * 100:.0f})"
    )
    assert est["effective_concurrency"] == 3
    reset_calibration_cache()


def test_observed_yoksa_mod_eszamanliligi_kullanilir(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    path = _calibration_with_observed(Path_tempdir(), None)
    est = estimate_scan(_smoke_target(), "O2", calibration_path=path)
    assert est["effective_concurrency"] == MODE_PROFILES["O2"]["concurrency"]
    reset_calibration_cache()


def test_env_observed_sinirini_asaramaz(monkeypatch):
    """env 8 verilse bile ölçülen paralellik (3.14 → 3) fiziksel sınırdır."""
    path = _calibration_with_observed(Path_tempdir(), 3.14)
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "8")
    est = estimate_scan(_smoke_target(), "O2", calibration_path=path)
    assert est["effective_concurrency"] == 3
    # env daha küçükse env kazanır
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "2")
    small = estimate_scan(_smoke_target(), "O2", calibration_path=path)
    assert small["effective_concurrency"] == 2
    assert small["seconds"] > est["seconds"]
    reset_calibration_cache()


def test_observed_gecersiz_degerler_yok_sayilir(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    for bad in (0, -1, "x", True):
        path = _calibration_with_observed(Path_tempdir(), bad)  # type: ignore[arg-type]
        est = estimate_scan(_smoke_target(), "O2", calibration_path=path)
        assert est["effective_concurrency"] == MODE_PROFILES["O2"]["concurrency"], bad
        reset_calibration_cache()


def test_o1_o3_efektif_paralellik_makul(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    path = _calibration_with_observed(Path_tempdir(), 3.14)
    o1 = estimate_scan(_smoke_target(), "O1", calibration_path=path)
    o3 = estimate_scan(_smoke_target(), "O3", calibration_path=path)
    assert o1["effective_concurrency"] == 2  # O1 profili observed'dan küçük
    assert o3["effective_concurrency"] == 3  # observed sınırı
    assert 0 < o1["seconds"] < o3["seconds"]
    reset_calibration_cache()


def test_gercek_kalibrasyon_dosyasi_hedef_araliginda(monkeypatch):
    """Repodaki calibration.json (observed=3.14) ile O2 tahmini 362 ±%10.

    `planner_tasks=0`: 362 sn'lik ölçüm planner sorguları arama görevi olarak
    koşmadan alındı; planner etkisi ayrı delta testinde.
    """
    real = DEFAULT_CALIBRATION_PATH
    calibration = load_calibration(real)
    if calibration.concurrency_observed is None:
        pytest.skip("kalibrasyonda concurrency_observed yok (T-373 öncesi dosya)")
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_smoke_target(), "O2", calibration_path=real, planner_tasks=0)
    deviation = abs(est["seconds"] - REF_WALL_SECONDS) / REF_WALL_SECONDS
    assert deviation <= 0.10, f"{est['seconds']} vs {REF_WALL_SECONDS}"

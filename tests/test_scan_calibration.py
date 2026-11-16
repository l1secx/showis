"""T-102: sure tahmini kalibrasyonu, concurrency olcekleme ve sitecheck onbellegi."""

from __future__ import annotations

import json
import sys
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
    MODE_PROFILES,
    calibration_path,
    estimate_scan,
    load_calibration,
    norm_mode,
    reset_calibration_cache,
    scan_concurrency,
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
    """T-102 oncesi formül (sirali yurutme) — regresyon referansi."""
    profile = MODE_PROFILES[norm_mode(mode)]
    hints = target.hints or {}
    variants = generate_variants(target.first_name, target.last_name, hints).variants
    api_usernames = min(profile["variant_tasks"], len(variants))
    hint_count = sum(
        1 for k in ("school", "city", "company") if str(hints.get(k) or "").strip()
    )
    search_tasks = 1 + hint_count
    api_tasks = api_usernames * _API_CONNECTORS
    slow_tasks = min(api_usernames, 8) * 2
    site_usernames = min(profile["usernames"], len(variants))

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


def test_concurrency_bir_eski_tahmine_esit(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    for mode in ("O1", "O2", "O3"):
        est = estimate_scan(_target("ODTÜ"), mode)
        assert est["seconds"] == _legacy_estimate(_target("ODTÜ"), mode), mode
        assert est["concurrency"] == 1


def test_concurrency_dort_tahmini_dusurur_ama_sifirlamaz(monkeypatch):
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

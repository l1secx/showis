"""Tarama modu profilleri ve sure tahmini testleri."""

import re
from pathlib import Path

import pytest
from showis_contracts import Target
from showis_engine.scan_modes import (
    MODE_PROFILES,
    budget_for_mode,
    estimate_scan,
    norm_mode,
    planner_cap,
    skipped_connectors,
    task_allowed,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _target(school: str | None = None) -> Target:
    hints = {"school": school} if school else {}
    return Target(first_name="Kerem", last_name="Boz", hints=hints)


def handle_target(nickname: str) -> Target:
    return Target(kind="handle", nickname=nickname)


def test_norm_mode_bilinmeyen_deger_o2():
    assert norm_mode(None) == "O2"
    assert norm_mode("standard") == "O2"
    assert norm_mode("o3") == "O3"


@pytest.mark.parametrize("mode,expected", [("O1", 0), ("O2", 8), ("O3", 20), ("o3", 20), ("x", 8)])
def test_planner_cap_by_mode(mode, expected):
    assert planner_cap(mode) == expected


def test_o1_slow_connectorlari_atlar():
    assert task_allowed("O1", "sitecheck") is False
    assert task_allowed("O2", "sitecheck") is True
    assert skipped_connectors("O1", ["sitecheck", "github"]) == ["sitecheck"]


@pytest.mark.parametrize("mode", ["O1", "O2", "O3"])
def test_estimate_butceyle_tutarli(mode):
    est = estimate_scan(_target("ODTÜ"), mode)
    budget = budget_for_mode(mode)
    assert est["tasks"] <= budget["max_tasks"]
    assert est["seconds"] > 0
    assert est["steps"][0]["key"] == "search"
    assert est["mode"] == mode


def test_estimate_okul_ipucu_sorgu_sayisini_artirir():
    plain = estimate_scan(_target(), "O2")
    schooled = estimate_scan(_target("ODTÜ"), "O2")
    assert schooled["steps"][0]["tasks"] == plain["steps"][0]["tasks"] + 1


@pytest.mark.parametrize("mode", ["O1", "O2", "O3"])
def test_estimate_counts_planner_search_tasks(mode):
    """Phase 2 planner sorguları arama adımında sayılır (O1=0, O2=8, O3=20)."""
    est = estimate_scan(_target(), mode)
    assert est["steps"][0]["key"] == "search"
    assert est["steps"][0]["tasks"] == 1 + planner_cap(mode)


def test_o2_gercek_olcume_yakin_tahmin(monkeypatch):
    """Canlı O2 ölçümü (T-371: 362 sn / 164 görev / 1176 istek) ±%25 içinde kalmalı.

    Eski 604-657 sn'lik referans 48 site + 18 connector + LLM beklemesi dönemine
    aitti; T-333 (LLM hızlı fallback) ve T-357 (64 site) sonrası model güncellendi.
    Ölçüm Phase 2 planner aramalarından önce alındı; planner sorguları artık arama
    görevi olarak koştuğu için üst sınıf korunurken tahmin ~+80 sn büyüdü
    (bkz. `test_estimate_counts_planner_search_tasks`).
    """
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_target("ODTÜ"), "O2")
    assert 280 <= est["seconds"] <= 520, est["seconds"]
    assert 120 <= est["tasks"] <= 260
    assert {s["key"] for s in est["steps"]} >= {"search", "api", "sitecheck"}

    # Concurrency=1 sıralı üst sınırdır (bölme yok) — paralelden büyük olmalı.
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    sequential = estimate_scan(_target("ODTÜ"), "O2")
    assert sequential["seconds"] > est["seconds"]


# ---------------------------------------------------------------------------
# Task 9: handle hedefi tahmini — nick varyantları da sayılır
# ---------------------------------------------------------------------------


def test_estimate_counts_handle_variants():
    est = estimate_scan(handle_target("kerem"), "O2")
    assert est["tasks"] > 0
    site = next(s for s in est["steps"] if s["key"] == "sitecheck")
    assert site["tasks"] > 0


def test_search_label_is_mode_aware():
    """Arama adiminin etiketi hedef kimligine gore konusur (handle -> kullanici adi)."""
    name_est = estimate_scan(_target(), "O2")
    handle_est = estimate_scan(handle_target("kerem"), "O2")
    assert name_est["steps"][0]["label"] == "Web araması (isim + ipuçları)"
    assert handle_est["steps"][0]["label"] == "Web araması (kullanıcı adı + ipuçları)"


# ---------------------------------------------------------------------------
# A2b (D2/I5): sitecheck istek butcesi + site basina maliyet
# ---------------------------------------------------------------------------


def _sitecheck_step(est):
    return next(s for s in est["steps"] if s["key"] == "sitecheck")


@pytest.mark.parametrize("mode,budget", [("O1", 0), ("O2", 600), ("O3", 1200)])
def test_sitecheck_istek_butcesi_tahmine_yansir(monkeypatch, mode, budget):
    monkeypatch.delenv("SHOWIS_SITECHECK_REQUEST_BUDGET", raising=False)
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_target(), mode)

    assert est["sitecheck_requests"] == budget
    assert est["site_requests"] >= budget
    if budget:
        assert _sitecheck_step(est)["tasks"] > 0
    else:
        assert "sitecheck" not in {s["key"] for s in est["steps"]}


@pytest.mark.parametrize("mode", ["O1", "O2", "O3"])
def test_istek_tahmini_mod_butcesini_asmaz(monkeypatch, mode):
    """A2b: tahmin edilen istek sayisi mod butcesini gecemez (kapi keser)."""
    monkeypatch.delenv("SHOWIS_SITECHECK_REQUEST_BUDGET", raising=False)
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    est = estimate_scan(_target(), mode)

    assert est["requests"] <= MODE_PROFILES[norm_mode(mode)]["max_http_requests"]
    assert est["requests"] == min(
        est["requests_raw"], MODE_PROFILES[norm_mode(mode)]["max_http_requests"]
    )


def test_sitecheck_env_butcesi_tahmini_dusurur(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    monkeypatch.delenv("SHOWIS_SITECHECK_REQUEST_BUDGET", raising=False)
    base = estimate_scan(_target(), "O2")
    monkeypatch.setenv("SHOWIS_SITECHECK_REQUEST_BUDGET", "300")
    kucuk = estimate_scan(_target(), "O2")

    assert kucuk["sitecheck_requests"] == 300
    assert kucuk["seconds"] < base["seconds"]
    assert _sitecheck_step(kucuk)["seconds"] == pytest.approx(
        _sitecheck_step(base)["seconds"] / 2, rel=1e-2
    )


def test_sitecheck_suresi_site_basina_olceklenir(monkeypatch):
    """Sure site sayisiyla olceklenir; 'kullanici adi × 22 sn' degil."""
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    monkeypatch.delenv("SHOWIS_SITECHECK_REQUEST_BUDGET", raising=False)
    est = estimate_scan(_target(), "O2")
    step = _sitecheck_step(est)

    from showis_engine.scan_modes import _COST, _REFERENCE_SITES_PER_USERNAME

    expected = (
        est["sitecheck_requests"]
        * (_COST["site_username"] / _REFERENCE_SITES_PER_USERNAME)
        / est["effective_concurrency"]
    )
    assert step["seconds"] == pytest.approx(expected, rel=1e-3)
    # 48-site referansi korunur: 600 istek ≈ 12.5 "eski kullanici adi" maliyeti
    assert step["seconds"] > est["sitecheck_requests"] * 0.4 / est["effective_concurrency"]


def test_sitecheck_kullanici_adi_tavani_hizali():
    """Estimate ve connector ayni tavanı kullanmali (drift korumasi)."""
    from showis_connectors.sitecheck.connector import MAX_PLANNED_USERNAMES
    from showis_engine.scan_modes import SITECHECK_MAX_USERNAMES

    assert SITECHECK_MAX_USERNAMES == MAX_PLANNED_USERNAMES == 10


# ---------------------------------------------------------------------------
# Z4-2 (D10): sitecheck etiketi tek kaynaktan + e2e 4c ayni deseni kullanir
# ---------------------------------------------------------------------------


def test_sitecheck_label_bicimi():
    from showis_engine.scan_modes import SITECHECK_LABEL_ERE, sitecheck_label

    label = sitecheck_label(80, 600)
    assert label == "Site-check (80 site / 600 istek)"
    match = re.fullmatch(SITECHECK_LABEL_ERE, label)
    assert match and match.group(1) == "80" and match.group(2) == "600"


def test_e2e_betik_a_yni_deseni_kullanir():
    """Desen tek kaynaktan: e2e 4c adimi `SITECHECK_LABEL_ERE`'yi birebir tasimali."""
    from showis_engine.scan_modes import SITECHECK_LABEL_ERE

    script = (REPO_ROOT / "scripts" / "e2e_acceptance.sh").read_text(encoding="utf-8")
    assert SITECHECK_LABEL_ERE in script, (
        "e2e_acceptance.sh 4c adimi SITECHECK_LABEL_ERE ile ayni deseni kullanmali"
    )
    # Eski (bozuk) desen kalinti olarak durmamali
    assert "× (?<n>[0-9]+) site" not in script


def test_estimate_sitecheck_etiketi_tutarli(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    monkeypatch.delenv("SHOWIS_SITECHECK_REQUEST_BUDGET", raising=False)
    from showis_engine.scan_modes import SITECHECK_LABEL_ERE

    for mode in ("O2", "O3"):
        est = estimate_scan(_target("ODTÜ"), mode)
        step = _sitecheck_step(est)
        assert re.fullmatch(SITECHECK_LABEL_ERE, step["label"]), step["label"]
        # etiket aile butcesini tasir
        assert str(est["sitecheck_requests"]) in step["label"]

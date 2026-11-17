"""Tarama modu profilleri ve sure tahmini testleri."""

import pytest
from showis_contracts import Target
from showis_engine.scan_modes import (
    budget_for_mode,
    estimate_scan,
    norm_mode,
    skipped_connectors,
    task_allowed,
)


def _target(school: str | None = None) -> Target:
    hints = {"school": school} if school else {}
    return Target(first_name="Kerem", last_name="Boz", hints=hints)


def handle_target(nickname: str) -> Target:
    return Target(kind="handle", nickname=nickname)


def test_norm_mode_bilinmeyen_deger_o2():
    assert norm_mode(None) == "O2"
    assert norm_mode("standard") == "O2"
    assert norm_mode("o3") == "O3"


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


def test_o2_gercek_olcume_yakin_tahmin(monkeypatch):
    """Canlı O2 ölçümü (T-371: 362 sn / 164 görev / 1176 istek) ±%25 içinde kalmalı.

    Eski 604-657 sn'lik referans 48 site + 18 connector + LLM beklemesi dönemine
    aitti; T-333 (LLM hızlı fallback) ve T-357 (64 site) sonrası model güncellendi.
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

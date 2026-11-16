"""T-333 — LLM erişilemezken hızlı fallback + devre kesici.

Ölçülen sorun (canlı worker logu): planner her taramada 3 deneme × ~30 sn timeout
(~90 sn) harcıyordu. Bu testler yeni sözleşmeyi kilitler:

* istemci: `timeout=8` / `max_retries=1` (env ile ayarlanabilir), toplam ≤2 girişim,
* devre kesici: 3 ardışık hata → cooldown boyunca hiç çağrı yok; cooldown sonunda
  tek yarı-açık deneme; başarı sayacı sıfırlar,
* planner: `asyncio.wait_for` ile sınırlı, aşımda anında şablon planı; tarama
  başına ikinci deneme yok,
* explainer: toplam bütçe dolunca deterministik yerel açıklama,
* `scan.stats` sayaçları (`llm_calls`, `llm_failures`, `llm_skipped_by_breaker`).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from showis_contracts import Platform, Target, VariantSet
from showis_engine.llm_explainer import FindingExplainer
from showis_engine.planner import QueryPlanner
from showis_llm import LLMClient, LLMClientError, get_breaker, llm_stats, reset_breaker
from showis_llm.breaker import CircuitBreaker

BASE_URL = "https://api.openai.com/v1"


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch):
    monkeypatch.delenv("SHOWIS_LLM_COOLDOWN", raising=False)
    monkeypatch.delenv("SHOWIS_LLM_BREAKER_THRESHOLD", raising=False)
    reset_breaker()
    yield
    reset_breaker()


class _TimeoutClient(LLMClient):
    """Ağa çıkmadan her çağrıda ReadTimeout gibi davranan istemci."""

    def __init__(self, **kwargs):  # noqa: ANN003
        kwargs.setdefault("api_key", "sk-test")
        kwargs.setdefault("model", "test-model")
        kwargs.setdefault("session_id", "s")
        kwargs.setdefault("timeout_seconds", 0.05)
        kwargs.setdefault("max_retries", 1)
        super().__init__(base_url=BASE_URL, **kwargs)
        self.attempts = 0
        self.delay = 0.0

    async def complete(self, messages, schema=None, temperature=0.0, max_tokens=None):  # noqa: ANN001
        # Gerçek istemcinin davranışını taklit et: devre kesici + hata kaydı
        breaker = get_breaker()
        if not breaker.allow():
            raise LLMClientError("LLM devre dışı (cooldown)")
        breaker.record_call()
        self.attempts += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        breaker.record_failure()
        raise LLMClientError("LLM request failed after 2 attempts (ReadTimeout)")


class _OkClient(_TimeoutClient):
    async def complete(self, messages, schema=None, temperature=0.0, max_tokens=None):  # noqa: ANN001
        breaker = get_breaker()
        if not breaker.allow():
            raise LLMClientError("LLM devre dışı (cooldown)")
        breaker.record_call()
        self.attempts += 1
        breaker.record_success()
        if schema is not None:
            return schema.model_validate({"queries": []})
        return "LLM açıklaması"


# ---------------------------------------------------------------------------
# 1. İstemci varsayılanları
# ---------------------------------------------------------------------------


def test_istemci_varsayilan_timeout_ve_retry(monkeypatch):
    monkeypatch.delenv("SHOWIS_LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("SHOWIS_LLM_RETRIES", raising=False)
    client = LLMClient(base_url=BASE_URL, api_key="k", model="m", session_id="s")
    assert client.timeout == 8.0
    assert client.max_retries == 1


def test_istemci_env_ayarlari(monkeypatch):
    monkeypatch.setenv("SHOWIS_LLM_TIMEOUT", "3.5")
    monkeypatch.setenv("SHOWIS_LLM_RETRIES", "2")
    client = LLMClient(base_url=BASE_URL, api_key="k", model="m", session_id="s")
    assert client.timeout == 3.5
    assert client.max_retries == 2


# ---------------------------------------------------------------------------
# 2. Devre kesici
# ---------------------------------------------------------------------------


def test_esik_altinda_devre_kapali():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    for _ in range(2):
        assert breaker.allow()
        breaker.record_failure()
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_uc_ardisik_hata_devreyi_acar():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is False
    assert breaker.snapshot()["llm_skipped_by_breaker"] >= 1


def test_cooldown_sonrasi_yarim_acik_tek_deneme():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.05)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.allow() is False
    time.sleep(0.06)
    assert breaker.state == "half_open"
    assert breaker.allow() is True, "cooldown sonunda tek deneme izni"
    assert breaker.allow() is False, "ikinci deneme verilmez"


def test_yarim_acik_basari_devreyi_kapatir():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.05)
    for _ in range(3):
        breaker.record_failure()
    time.sleep(0.06)
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_basarili_cagri_sayaci_sifirlar():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed", "ardışık olmayan hatalar devreyi açmamalı"


def test_istemci_devre_acikken_istek_atmaz():
    breaker = get_breaker()
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    client = LLMClient(base_url=BASE_URL, api_key="k", model="m", session_id="s")
    with pytest.raises(LLMClientError, match="devre dışı"):
        asyncio.run(client.complete([{"role": "user", "content": "ping"}]))
    stats = llm_stats()
    assert stats["llm_skipped_by_breaker"] >= 1


# ---------------------------------------------------------------------------
# 3. Planner — hızlı fallback + ölçüm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_timeout_ile_hizli_fallback(monkeypatch):
    """LLM asılı kalsa bile planlama, planner timeout'u kadar bekleyip düşer."""
    monkeypatch.setenv("SHOWIS_LLM_PLANNER_TIMEOUT", "1")
    client = _TimeoutClient()
    client.delay = 5.0  # LLM erişilemiyor
    planner = QueryPlanner(llm_client=client)  # type: ignore[arg-type]

    target = Target(first_name="Kerem", last_name="Boz", hints={})
    started = time.perf_counter()
    plan = await planner.plan(target, VariantSet(variants=[]))
    elapsed = time.perf_counter() - started

    assert plan.queries, "şablon planı üretilmeli"
    assert elapsed < 2.0, f"planlama {elapsed:.2f} sn sürdü (beklenen <2 sn)"
    assert planner.llm_disabled is True


@pytest.mark.asyncio
async def test_planner_ayni_taramada_ikinci_kez_denemez():
    client = _TimeoutClient()
    planner = QueryPlanner(llm_client=client)  # type: ignore[arg-type]
    target = Target(first_name="Kerem", last_name="Boz", hints={})

    await planner.plan(target, VariantSet(variants=[]))
    attempts_after_first = client.attempts
    plan = await planner.plan(target, VariantSet(variants=[]))

    assert client.attempts == attempts_after_first, "ikinci plan çağrısı LLM'e gitmemeli"
    assert plan.queries


@pytest.mark.asyncio
async def test_planner_mutlu_yol_degismedi():
    client = _OkClient()
    planner = QueryPlanner(llm_client=client)  # type: ignore[arg-type]
    plan = await planner.plan(
        Target(first_name="Kerem", last_name="Boz", hints={}), VariantSet(variants=[])
    )
    assert client.attempts == 1
    assert planner.llm_disabled is False
    assert isinstance(plan.queries, list)


# ---------------------------------------------------------------------------
# 4. Explainer bütçesi
# ---------------------------------------------------------------------------


def _finding():  # noqa: ANN202
    from showis_contracts import ConfidenceBreakdown, Finding, FindingStatus

    return Finding(
        id="finding:github:kboz",
        case_id="c",
        scan_id="s",
        entity_id="github:kboz",
        platform=Platform.GITHUB,
        label="kboz",
        status=FindingStatus.CANDIDATE,
        score=0.55,
        breakdown=ConfidenceBreakdown(total=0.55),
    )


@pytest.mark.asyncio
async def test_explainer_butcesi_dolunca_yerel_uretilir():
    client = _TimeoutClient()
    explainer = FindingExplainer(llm_client=client, budget_seconds=0.0)  # type: ignore[arg-type]
    target = Target(first_name="Kerem", last_name="Boz")

    explanation = await explainer.explain(_finding(), target)
    assert client.attempts == 0, "bütçe yoksa LLM çağrılmamalı"
    assert explanation
    assert explainer.budget_exhausted is True


@pytest.mark.asyncio
async def test_explainer_butce_icinde_llm_kullanir():
    client = _OkClient()
    explainer = FindingExplainer(llm_client=client, budget_seconds=25.0)  # type: ignore[arg-type]
    explanation = await explainer.explain(
        _finding(), Target(first_name="Kerem", last_name="Boz")
    )
    assert client.attempts == 1
    assert "LLM açıklaması" in explanation


@pytest.mark.asyncio
async def test_explainer_hatasinda_yerel_aciklamaya_duser():
    client = _TimeoutClient()
    explainer = FindingExplainer(llm_client=client, budget_seconds=25.0)  # type: ignore[arg-type]
    explanation = await explainer.explain(
        _finding(), Target(first_name="Kerem", last_name="Boz")
    )
    assert explanation
    assert explainer.llm_disabled is True
    # ikinci bulgu LLM'e gitmez
    attempts = client.attempts
    await explainer.explain(_finding(), Target(first_name="Kerem", last_name="Boz"))
    assert client.attempts == attempts


# ---------------------------------------------------------------------------
# 5. scan.stats aktarımı
# ---------------------------------------------------------------------------


def test_stats_anahtarlari_mevcut():
    stats = llm_stats()
    assert {"llm_calls", "llm_failures", "llm_skipped_by_breaker"} <= set(stats)


@pytest.mark.asyncio
async def test_devre_acikken_planlama_aninda():
    """3 hatadan sonra devre açık: yeni planlama LLM'e hiç gitmez (<0.1 sn)."""
    breaker = get_breaker()
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    client = _TimeoutClient()
    planner = QueryPlanner(llm_client=client)  # type: ignore[arg-type]
    started = time.perf_counter()
    plan = await planner.plan(
        Target(first_name="Kerem", last_name="Boz"), VariantSet(variants=[])
    )
    elapsed = time.perf_counter() - started

    assert plan.queries
    assert client.attempts == 0, "devre açıkken LLM çağrılmamalı"
    assert elapsed < 0.1, f"planlama {elapsed:.3f} sn (beklenen <0.1)"

"""Tarama yasam dongusu — uctan uca entegrasyon testleri (T-201).

Gercek route'lar, gercek ``ScannerService``, gercek DB semasi; **yalnizca
connector'lar sahte** (ag istegi yok). Amac, T-101/T-108 ile degisen tarama
sozlesmesini API seviyesinde kilitlemek:

1. mutlu yol       : case -> scan -> completed -> bulgular -> tutarli stats
2. butce           : istemci ``budget`` alani mod profilinin ustune cikamaz
                     (``min()`` kirpma) ve kalan gorevler ``skipped`` olur (SEC-05)
3. iptal           : ``POST /scans/{id}/cancel`` -> durum ``cancelled`` kalir,
                     ``scan.completed`` audit kaydi **yazilmaz**; ucustaki
                     gorevler yeni bir statu degil ``skipped`` + ``error=cancelled``
                     olur (SEC-06, Atlas karari T-115)
4. gorev hatasi    : connector exception -> tarama devam eder, gorev ``failed``
5. yeniden tarama  : kullanici karari (``confirmed``) korunur (SEC-15)
6. drain loop      : ``run()`` sirasinda eklenen gorev de calistirilir
7. idempotans      : ayni case'te 2. tarama bulgulari cogaltmaz

Kurulum (sys.path, MASTER_KEY, sqlite engine, `get_db` override, auth token)
`tests/integration/conftest.py` icindedir (T-115). Bu dosya yalnizca connector
enjeksiyonunu ve tarama sozlesmesini sahiplenir.

Regresyon kapsami (gece yakalanan kusurlar — T-115'te kalici kilit):
* Q-01 ``_elapsed`` naive/aware: tarama `started_at`'i DB'den okurken sqlite
  naive doner; butce kontrolu `max_wall_seconds` ile bu yolu her taramada calistirir.
* Q-02 iptal yolunda eksik import: iptal + uçustaki gorev senaryosu (test 3).
* Q-03 ``_completion_key`` naive/aware: farkli oturumdan gelen `created_at` ile
  ayni turda biten gorevlerin siralanmasi (test 6, drain loop).

Auth notu (T-108): istemci sunucunun bekledigi token'i iki destekli baslikla
yollar (`conftest.auth_headers`), yani auth acik veya kapali — iki durumda da yesil.

DB notu: iptal senaryosu tarama surerken **ikinci bir oturumdan** yazar; bu
yuzden conftest dosya tabanli sqlite (WAL + busy_timeout) kullanir.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
import pytest_asyncio
import showis.api.v1.cases as cases_module
from showis.db.models import AuditLog
from showis.db.models import Scan as DbScan
from showis.db.models import Task as DbTask
from showis_contracts import (
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Sahte connector
# ---------------------------------------------------------------------------


class FakeConnector:
    """Sabit gecikmeli, tamamen yerel sahte connector (ag istegi yok).

    Gorev kimlikleri planlama turu basina benzersizdir (``fake-1-0``); gercek
    connector'lar da ``uuid4`` kullanir, yani ayni case'in yeniden taramasi
    ayni gorev kimligini uretmez. Hata enjeksiyonu gorev *sirasi* ile yapilir.
    """

    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A

    def __init__(
        self,
        name: str = "fake",
        tasks: int = 5,
        delay: float = 0.0,
        fail_indexes: set[int] | None = None,
        on_run: Any | None = None,
    ):
        self.name = name
        self.task_count = tasks
        self.delay = delay
        self.fail_indexes = fail_indexes or set()
        self.on_run = on_run
        self.planned = 0
        self.task_ids: list[str] = []

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        self.planned += 1
        self.task_ids = [f"{self.name}-{self.planned}-{i}" for i in range(self.task_count)]
        return [
            TaskSpec(
                id=task_id,
                connector=self.name,
                platform=self.platform,
                kind="probe",
                params={"i": i},
            )
            for i, task_id in enumerate(self.task_ids)
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        if self.delay:
            await asyncio.sleep(self.delay)
        if int(task.params.get("i", 0)) in self.fail_indexes and not task.params.get(
            "injected"
        ):
            raise RuntimeError("fake connector boom")
        if self.on_run is not None:
            await self.on_run(task, ctx)
        index = int(task.params.get("i", 0))
        subject = f"user{index}"
        return [
            Observation(
                id=f"obs-{task.id}",
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.PROFILE,
                subject=subject,
                tier=self.tier,
                data={"login": subject},
            )
        ]

    def normalize(self, obs: Observation) -> tuple[list[Entity], list]:
        return (
            [
                Entity(
                    id=f"{self.platform.value}:{obs.subject}",
                    type=EntityType.ACCOUNT,
                    platform=self.platform,
                    label=obs.subject,
                    attributes={"username": obs.subject},
                )
            ],
            [],
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class ApiHarness:
    """Testin API istemcisi + DB oturum fabrikasi + connector enjeksiyonu."""

    def __init__(
        self,
        client,
        factory: async_sessionmaker[AsyncSession],
    ):
        self.client = client
        self.factory = factory
        self.connectors: list[Any] = []

    # --- case / scan yardimcilari -----------------------------------------

    async def create_case(
        self,
        *,
        name: str = "Lifecycle Test",
        purpose: str = "SMOKE entegrasyon testi (otomatik)",
        first_name: str = "Test",
        last_name: str = "Smoke",
        retention_days: int = 7,
    ) -> str:
        res = await self.client.post(
            "/api/v1/cases",
            json={
                "name": name,
                "purpose": purpose,
                "legal_basis": "self_check",
                "retention_days": retention_days,
                "target": {"first_name": first_name, "last_name": last_name, "hints": {}},
                "seeds": [{"platform": "github", "username": "test_smoke"}],
            },
        )
        assert res.status_code == 201, res.text
        return res.json()["id"]

    async def start_scan(self, case_id: str, *, mode: str = "O1", budget: dict | None = None):
        payload: dict[str, Any] = {"mode": mode}
        if budget is not None:
            payload["budget"] = budget
        res = await self.client.post(f"/api/v1/cases/{case_id}/scan", json=payload)
        assert res.status_code in (200, 202), res.text
        return res.json()["scan_id"]

    async def scan_row(self, scan_id: str) -> DbScan | None:
        async with self.factory() as session:
            return (
                await session.execute(select(DbScan).where(DbScan.id == scan_id))
            ).scalar_one_or_none()

    async def scan_task_rows(self, scan_id: str) -> list[DbTask]:
        async with self.factory() as session:
            return list(
                (
                    await session.execute(
                        select(DbTask).where(DbTask.scan_id == scan_id).order_by(DbTask.id)
                    )
                )
                .scalars()
                .all()
            )

    async def audits(self, case_id: str) -> list[AuditLog]:
        async with self.factory() as session:
            return list(
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.case_id == case_id)
                        .order_by(AuditLog.id)
                    )
                )
                .scalars()
                .all()
            )

    async def wait_for_scan_row(self, case_id: str, *, timeout: float = 15.0) -> str:
        """Arka plan taramasi baslarken olusan ``scans`` satirini bekler."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async with self.factory() as session:
                row = (
                    await session.execute(
                        select(DbScan)
                        .where(DbScan.case_id == case_id)
                        .order_by(DbScan.created_at)
                    )
                ).scalars().first()
            if row is not None:
                return row.id
            await asyncio.sleep(0.02)
        raise AssertionError(f"scan satiri {timeout}s icinde olusmadi (case={case_id})")

    async def wait_for_status(
        self, scan_id: str, statuses: set[str], *, timeout: float = 15.0
    ) -> DbScan:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = await self.scan_row(scan_id)
            if row is not None and row.status in statuses:
                return row
            await asyncio.sleep(0.02)
        row = await self.scan_row(scan_id)
        raise AssertionError(
            f"scan {scan_id} {statuses} durumuna {timeout}s icinde gelmedi "
            f"(son durum: {getattr(row, 'status', None)})"
        )

    async def wait_for_task_status(
        self, scan_id: str, statuses: set[str], *, timeout: float = 15.0
    ) -> DbTask:
        """En az bir gorev verilen duruma gelene kadar bekler."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = await self.scan_task_rows(scan_id)
            for row in rows:
                if row.status in statuses:
                    return row
            await asyncio.sleep(0.02)
        raise AssertionError(
            f"scan {scan_id} icinde {statuses} durumunda gorev {timeout}s icinde olusmadi"
        )

    async def findings(self, case_id: str) -> list[dict]:
        res = await self.client.get(f"/api/v1/cases/{case_id}/findings")
        assert res.status_code == 200, res.text
        return res.json()

    async def scan_status(self, scan_id: str) -> dict:
        res = await self.client.get(f"/api/v1/scans/{scan_id}")
        assert res.status_code == 200, res.text
        return res.json()


def _same_instant(left: str | None, right: str | None) -> bool:
    """Iki ISO-8601 damgasinin ayni ani gosterdigini dogrular.

    sqlite timezone bilgisini saklamaz (naive doner); Postgres'te aware gelir.
    Bu yuzden karsilastirma naive degeri UTC kabul ederek yapilir — degerin
    kendisi (an) hala birebir karsilastirilir.
    """
    if not left or not right:
        return left == right
    from datetime import datetime, timezone

    def _norm(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    return _norm(left) == _norm(right)


@pytest_asyncio.fixture
async def api(
    client, db_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> ApiHarness:
    """Ortak `client`/DB fixture'lari + sahte connector enjeksiyonu."""
    harness = ApiHarness(client=client, factory=db_factory)

    # `run_scan_in_background` connector'lari kendi oturumuyla kurar: testte
    # sahte connector listesine bagla, LLM'i kapat (ag cagrisi olmasin).
    async def _fake_build_connectors(_session):
        return list(harness.connectors)

    async def _fake_build_llm_client(_session):
        return None

    monkeypatch.setattr(cases_module, "build_connectors", _fake_build_connectors)
    monkeypatch.setattr(cases_module, "build_llm_client", _fake_build_llm_client)
    yield harness


# ---------------------------------------------------------------------------
# 1. Mutlu yol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutlu_yol_tarama_tamamlanir_ve_bulgular_yazilir(api: ApiHarness):
    api.connectors = [FakeConnector(name="fake", tasks=5)]
    case_id = await api.create_case()
    scan_id = await api.start_scan(case_id, mode="O1")

    body = await api.scan_status(scan_id)
    assert body["status"] == "completed", body
    assert body["mode"] == "O1"
    stats = body["stats"]
    assert stats["executed_tasks"] == 5, stats
    assert stats["skipped_tasks"] == 0, stats
    assert stats["total_entities"] == 5, stats
    assert stats["total_observations"] == 5, stats
    assert body["finished_at"] is not None

    tasks = await api.scan_task_rows(scan_id)
    assert len(tasks) == 5
    assert {t.status for t in tasks} == {"done"}
    assert all(t.observation_count == 1 for t in tasks)
    assert all(t.started_at is not None and t.finished_at is not None for t in tasks)

    findings = await api.findings(case_id)
    assert len(findings) == 5, findings
    assert {f["platform"] for f in findings} == {"github"}
    assert all(f["evidence_ids"] is not None for f in findings)

    actions = [a.action for a in await api.audits(case_id)]
    assert "scan.start" in actions
    assert "scan.completed" in actions
    assert actions.count("connector.task") == 5


# ---------------------------------------------------------------------------
# 2. Butce: kucuk max_tasks -> kalan gorevler skipped, tarama completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butce_mod_profiline_kirpilir_ve_kalan_gorevler_skipped_olur(api: ApiHarness):
    """SEC-05 (Atlas karari T-115): istemci butcesi gelir ama `min()` ile kirpilir.

    Iki sey birden dogrulanir:
    * kucuk deger (`max_tasks=2`) korunur -> kalan gorevler `skipped`,
    * buyuk deger (`max_http_requests=10**6`) mod profilinin ustune cikamaz.
    """
    from showis_engine.scan_modes import budget_for_mode

    api.connectors = [FakeConnector(name="fake", tasks=8, delay=0.01)]
    case_id = await api.create_case()
    scan_id = await api.start_scan(
        case_id,
        mode="O1",
        budget={
            "max_tasks": 2,
            "max_http_requests": 1_000_000,
            "max_wall_seconds": 999_999,
            "max_llm_calls": 0,
        },
    )

    body = await api.scan_status(scan_id)
    assert body["status"] == "completed", body
    stats = body["stats"]
    assert stats["executed_tasks"] == 2, stats
    assert stats["skipped_tasks"] == 6, stats

    tasks = await api.scan_task_rows(scan_id)
    assert len(tasks) == 8
    assert sum(1 for t in tasks if t.status == "done") == 2
    skipped = [t for t in tasks if t.status == "skipped"]
    assert len(skipped) == 6
    assert all(t.error == "budget_or_timeout" for t in skipped)

    # DB'ye yazilan butce kirpilmis olmali (istemci sinirsiz istek atamaz)
    row = await api.scan_row(scan_id)
    assert row is not None
    profile = budget_for_mode("O1")
    assert row.budget["max_tasks"] == 2, row.budget
    assert row.budget["max_http_requests"] == profile["max_http_requests"], row.budget
    assert row.budget["max_wall_seconds"] == profile["max_wall_seconds"], row.budget
    assert row.budget["max_cost_usd"] == profile["max_cost_usd"], row.budget


# ---------------------------------------------------------------------------
# 3. Iptal: durum cancelled kalir, scan.completed audit yazilmaz (SEC-06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iptal_cancelled_kalir_ve_completed_audit_yazilmaz(api: ApiHarness):
    api.connectors = [FakeConnector(name="fake", tasks=8, delay=0.25)]
    case_id = await api.create_case(name="Iptal Test")

    started = time.monotonic()
    scan_request = asyncio.create_task(
        api.client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O1"})
    )
    try:
        scan_id = await api.wait_for_scan_row(case_id)
        await api.wait_for_status(scan_id, {"running"}, timeout=10.0)
        # Iptal, gorevler gercekten ucustayken gelsin: aksi halde "ucustaki
        # gorev" yolu (Q-02 regresyonu) hic calismaz.
        await api.wait_for_task_status(scan_id, {"running"}, timeout=10.0)

        cancel = await api.client.post(f"/api/v1/scans/{scan_id}/cancel")
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "cancelled"

        # Tarama 10 sn icinde durmali (T-108/SEC-06 kabul kriteri; T-312: sinirli
        # bekleme — uretimde deadlock olursa test asilmaz, FAIL eder).
        response = await asyncio.wait_for(scan_request, timeout=10.0)
        assert response.status_code == 200, response.text
        elapsed = time.monotonic() - started
    finally:
        if not scan_request.done():
            scan_request.cancel()
        await asyncio.gather(scan_request, return_exceptions=True)

    assert elapsed < 10.0, f"iptal {elapsed:.1f}s surdu (gorevler kesilmedi)"

    # Durum iptal edilen olarak KALIR: `completed` onu ezmez
    row = await api.scan_row(scan_id)
    assert row is not None
    assert row.status == "cancelled", row.status

    body = await api.scan_status(scan_id)
    assert body["status"] == "cancelled", body
    # Iptal gercekten is gordu: tum gorevler calistirilmadi
    assert body["stats"]["executed_tasks"] < 8, body["stats"]

    # Atlas karari (T-115): yeni bir "cancelled" gorev statusu YOK. Iptal
    # aninda ucusta olan gorevler `skipped` + `error="cancelled"` olur; hicbir
    # gorev `pending`/`running` kalmaz.
    tasks = await api.scan_task_rows(scan_id)
    assert tasks, "gorevler planlanmis olmali"
    statuses = {t.status for t in tasks}
    assert "pending" not in statuses and "running" not in statuses, (
        f"iptal sonrasi askida gorev kalmamali (durumlar: {sorted(statuses)})"
    )
    assert statuses <= {"done", "failed", "skipped"}, sorted(statuses)
    assert "cancelled" not in statuses, "gorev statusu olarak 'cancelled' kullanilmamali"
    in_flight = [t for t in tasks if t.status == "skipped" and t.error == "cancelled"]
    assert in_flight, (
        "ucustaki gorevler skipped + error='cancelled' olarak isaretlenmeli "
        f"(durumlar: {[(t.id, t.status, t.error) for t in tasks]})"
    )
    assert body["stats"]["cancelled_tasks"] >= len(in_flight), body["stats"]

    actions = [a.action for a in await api.audits(case_id)]
    assert "scan.cancel" in actions
    assert "scan.completed" not in actions, (
        "iptal edilen tarama icin scan.completed audit kaydi yazilmamali (SEC-06)"
    )


# ---------------------------------------------------------------------------
# 4. Gorev hatasi taramayi durdurmaz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gorev_hatasi_taramayi_durdurmaz(api: ApiHarness):
    api.connectors = [FakeConnector(name="fake", tasks=6, fail_indexes={2})]
    case_id = await api.create_case(name="Hata Test")
    scan_id = await api.start_scan(case_id, mode="O1")

    body = await api.scan_status(scan_id)
    assert body["status"] == "completed", body
    assert body["stats"]["executed_tasks"] == 6, body["stats"]

    tasks = await api.scan_task_rows(scan_id)
    by_index = {int(t.params["i"]): t for t in tasks}
    assert by_index[2].status == "failed"
    assert "fake connector boom" in (by_index[2].error or "")
    assert by_index[2].finished_at is not None
    assert sum(1 for t in tasks if t.status == "done") == 5

    # Hatali gorev bulgu uretmez, digerleri uretir
    findings = await api.findings(case_id)
    assert len(findings) == 5


# ---------------------------------------------------------------------------
# 5. Yeniden tarama kullanici kararini ezmez (SEC-15)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yeniden_tarama_kullanici_kararini_korur(api: ApiHarness):
    api.connectors = [FakeConnector(name="fake", tasks=4)]
    case_id = await api.create_case(name=" Yeniden Tarama")

    first_scan = await api.start_scan(case_id, mode="O1")
    assert (await api.scan_status(first_scan))["status"] == "completed"

    findings = await api.findings(case_id)
    assert findings, "ilk tarama bulgu uretmeli"
    target = findings[0]
    finding_id = target["id"]

    patched = await api.client.patch(
        f"/api/v1/findings/{finding_id}", json={"status": "confirmed"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "confirmed"
    verified_at = patched.json()["verified_at"]
    assert verified_at is not None

    second_scan = await api.start_scan(case_id, mode="O1")
    assert (await api.scan_status(second_scan))["status"] == "completed"

    after = {f["id"]: f for f in await api.findings(case_id)}
    assert finding_id in after, "yeniden tarama ayni bulgu kimligini korumali"
    assert after[finding_id]["status"] == "confirmed", (
        "kullanici karari (confirmed) yeniden taramada korunmali (SEC-15)"
    )
    # sqlite timezone bilgisini saklamaz: ayni *ani* gosterdiklerini dogrula.
    assert _same_instant(after[finding_id]["verified_at"], verified_at), (
        f"verified_at korunmali: {after[finding_id]['verified_at']!r} != {verified_at!r}"
    )


# ---------------------------------------------------------------------------
# 6. Drain loop: run() sirasinda eklenen gorev de calisir
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_loop_calisma_aninda_eklenen_gorevi_yurutur(api: ApiHarness):
    injected: dict[str, str] = {}

    async def _add_task(task: TaskSpec, ctx: ScanContext) -> None:
        """Gercek connector'larin 'yeni kullanici adi bulundu' akisini taklit eder.

        Gorev **baska bir oturumdan** yazilir: drain loop'un `_refill_pending`
        ile gormesi ve `created_at`'i DB'den (sqlite'ta naive) okunurken ayni
        turda biten orneklerle birlikte siralanmasi gerekir — Q-03 regresyonu
        tam olarak bu naive/aware karisiminda patliyordu.
        """
        if int(task.params.get("i", 0)) != 0 or injected:
            return
        new_id = "fake-injected"
        async with api.factory() as session:
            session.add(
                DbTask(
                    id=new_id,
                    scan_id=ctx.scan_id,
                    connector="fake",
                    platform=Platform.GITHUB.value,
                    kind="probe",
                    params={"i": 99, "injected": True},
                    status="pending",
                )
            )
            await session.commit()
        injected["id"] = new_id

    api.connectors = [FakeConnector(name="fake", tasks=5, delay=0.05, on_run=_add_task)]
    case_id = await api.create_case(name="Drain Test")
    scan_id = await api.start_scan(case_id, mode="O1")

    body = await api.scan_status(scan_id)
    assert injected, "sahte connector run() sirasinda gorev eklemedi"
    assert body["status"] == "completed", body
    assert body["stats"]["executed_tasks"] == 6, body["stats"]

    tasks = {t.id: t for t in await api.scan_task_rows(scan_id)}
    assert "fake-injected" in tasks, "run() sirasinda eklenen gorev DB'de yok"
    assert tasks["fake-injected"].status == "done", (
        "drain loop calisma aninda eklenen gorevi calistirmali"
    )
    assert tasks["fake-injected"].observation_count == 1


# ---------------------------------------------------------------------------
# 7. Regresyon: ayni case'te ikinci tarama stats'i sifirlar, veriyi cogaltmaz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ikinci_tarama_bulgulari_cogaltmaz(api: ApiHarness):
    api.connectors = [FakeConnector(name="fake", tasks=3)]
    case_id = await api.create_case(name="Idempotans Test")

    first = await api.start_scan(case_id, mode="O1")
    assert (await api.scan_status(first))["status"] == "completed"
    first_findings = await api.findings(case_id)

    second = await api.start_scan(case_id, mode="O1")
    assert (await api.scan_status(second))["status"] == "completed"
    second_findings = await api.findings(case_id)

    assert len(first_findings) == 3
    assert len(second_findings) == 3, "entity/finding kimlikleri deterministik olmali"
    assert {f["id"] for f in first_findings} == {f["id"] for f in second_findings}

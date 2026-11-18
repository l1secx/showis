"""A2b — WMN verisinin motora baglanmasi: istek butcesi, plan, rotasyon.

Ag yok: motor testleri sahte HTTP istemcisiyle, veri testleri yerel
``data/wmn-sites.json`` ile calisir.
"""

from __future__ import annotations

import httpx
import pytest
from showis_connectors.sitecheck import budget, wmn
from showis_connectors.sitecheck.connector import (
    MAX_PLANNED_USERNAMES,
    SiteCheckConnector,
)
from showis_connectors.sitecheck.engine import UsernameCheckEngine
from showis_connectors.sitecheck.sites import SITES, SiteSpec
from showis_contracts import Platform, ScanContext, Seed, Target
from showis_engine.scan_modes import MODE_PROFILES, SITECHECK_MAX_USERNAMES

BUDGET_ENV = budget.REQUEST_BUDGET_ENV
NSFW_ENV = wmn.ALLOW_NSFW_ENV


@pytest.fixture(autouse=True)
def _temiz_politika(monkeypatch):
    """Her test temiz env ve onbellekle baslar."""
    monkeypatch.delenv(BUDGET_ENV, raising=False)
    monkeypatch.delenv(NSFW_ENV, raising=False)
    wmn.clear_cache()
    yield
    wmn.clear_cache()


# ---------------------------------------------------------------------------
# Sahte HTTP istemcisi
# ---------------------------------------------------------------------------


class FakeHttp:
    """``HttpClient.get`` yerine gecen, cagrilari kaydeden sahte istemci."""

    def __init__(self, status: int = 404, text: str = "", user_agent: str = "test-ua"):
        self.status = status
        self.text = text
        self.user_agent = user_agent
        self.calls: list[dict] = []

    async def get(self, url: str, **kwargs) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return httpx.Response(
            self.status,
            text=self.text,
            request=httpx.Request("GET", url),
        )


def _ctx(mode: str | None = None) -> ScanContext:
    ctx = ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(kind="handle", nickname="alice"),
        seeds=[Seed(platform=Platform.GITHUB, username="alice")],
    )
    if mode is not None:
        object.__setattr__(ctx, "mode", mode)  # ScanContext semasinda yok; getattr yolu
    return ctx


# ---------------------------------------------------------------------------
# Yukleme
# ---------------------------------------------------------------------------


def test_veri_dosyasi_var():
    assert wmn.WMN_PATH.exists(), f"WMN verisi yok: {wmn.WMN_PATH} (A1 ciktisi beklenir)"


def test_load_wmn_specs_alanlari():
    specs = wmn.load_wmn_specs()
    assert len(specs) >= 300
    for spec in specs:
        assert isinstance(spec, SiteSpec)
        assert spec.platform is Platform.WEB
        assert spec.no_match_is_absent is False
        assert spec.url_template.startswith("https://")
        assert "{u}" in spec.url_template
        assert wmn.is_wmn(spec)
        assert spec.category, "WMN kategorisi SiteSpec.category alanina yazilmali"


def test_kategori_alani_kullanilir():
    specs = {s.key: s for s in wmn.load_wmn_specs()}
    assert specs["247sports"].category == "hobby"
    assert wmn.spec_category(specs["247sports"]) == "hobby"
    # elle yazilmis spec'lerde kategori bos
    assert all(spec.category == "" for spec in SITES)


def test_load_wmn_specs_headers_allowlist():
    """Yukleyici savunmaci allowlist uygular: yalniz Accept / Accept-Language."""
    specs = {s.key: s for s in wmn.load_wmn_specs()}
    for spec in specs.values():
        assert {name.lower() for name in spec.headers} <= wmn.ALLOWED_HEADERS
    with_headers = [s for s in specs.values() if s.headers]
    assert with_headers, "en az bir kayitta allowlist basligi olmali"
    assert specs["apex-legends"].headers.get("Accept-Language")


def test_load_wmn_specs_savunmaci_allowlist_uygular(tmp_path, monkeypatch):
    """Dosyaya elle eklenmis bir Cookie/X-* basligi yuklenirken dusurulur."""
    data = tmp_path / "wmn.json"
    data.write_text(
        """{"sites": [{"key": "x", "name": "X", "url_template": "https://x.example/{u}",
        "expect_status": 200, "absent_statuses": [404], "category": "social",
        "headers": {"Cookie": "session=abc", "X-Api-Key": "k", "Accept": "text/html",
                    "Referer": "https://x.example"}}]}""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wmn, "WMN_PATH", data)
    wmn.clear_cache()

    spec = wmn.load_wmn_specs()[0]
    assert spec.headers == {"Accept": "text/html"}


def test_load_wmn_specs_onbellekli():
    assert wmn.load_wmn_specs() is wmn.load_wmn_specs()


def test_bozuk_kayit_atlanir(tmp_path, monkeypatch, caplog):
    bad = tmp_path / "wmn.json"
    bad.write_text(
        """{
          "_license": {"name": "CC BY-SA 4.0"},
          "sites": [
            {"key": "iyi", "name": "Iyi", "url_template": "https://iyi.example/{u}",
             "expect_status": 200, "absent_statuses": [404]},
            {"key": "bozuk", "name": "Bozuk", "url_template": "http://bozuk.example/{u}",
             "expect_status": 200, "absent_statuses": [404]},
            {"key": "iyi", "name": "Tekrar", "url_template": "https://tekrar.example/{u}",
             "expect_status": 200, "absent_statuses": [404]},
            {"key": "kotu-status", "name": "Kotu", "url_template": "https://k.example/{u}",
             "expect_status": "200", "absent_statuses": [404]}
          ]
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wmn, "WMN_PATH", bad)
    wmn.clear_cache()

    specs = wmn.load_wmn_specs()
    assert [s.key for s in specs] == ["iyi"]
    assert "WMN kaydi atlandi" in caplog.text


def test_dosya_yoksa_bos_liste(tmp_path, monkeypatch):
    monkeypatch.setattr(wmn, "WMN_PATH", tmp_path / "yok.json")
    wmn.clear_cache()
    assert wmn.load_wmn_specs() == ()


# ---------------------------------------------------------------------------
# Birlestirme
# ---------------------------------------------------------------------------


def test_merged_sites_kapsam_ve_tekillik():
    merged = wmn.merged_sites()
    assert len(merged) >= 300
    hosts = [wmn._host(spec.url_template) for spec in merged]
    assert len(hosts) == len(set(hosts)), "ayni host iki kez planlanmamali"
    keys = [spec.key for spec in merged]
    assert len(keys) == len(set(keys)), "key'ler tekil olmali"


def test_elle_yazilmis_ayni_hostta_kazanir():
    merged = {spec.key: spec for spec in wmn.merged_sites()}
    # Elle yazilmis bitbucket spec'i korunur; WMN'nin ayni host'lu kaydi elenir.
    assert merged["bitbucket"].platform is Platform.BITBUCKET
    assert merged["bitbucket"].url_template == "https://bitbucket.org/{u}/"
    manual_hosts = {wmn._host(s.url_template) for s in SITES}
    wmn_hosts = {wmn._host(s.url_template) for s in wmn.load_wmn_specs()}
    assert manual_hosts & wmn_hosts, "cakisan host olmali (test anlamli olsun)"


def test_merged_sites_nsfw_varsayilan_disarida():
    merged = wmn.merged_sites()
    assert not any(wmn.is_nsfw(s) for s in merged)
    assert all(wmn.spec_category(s) is None for s in SITES)


def test_merged_sites_nsfw_env_ile_dahil(monkeypatch):
    taban = len(wmn.merged_sites())
    monkeypatch.setenv(NSFW_ENV, "1")
    dahil = wmn.merged_sites()
    assert any(wmn.is_nsfw(s) for s in dahil)
    assert len(dahil) > taban
    assert len(dahil) >= taban + 30


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_allow_nsfw_truthy(monkeypatch, value):
    monkeypatch.setenv(NSFW_ENV, value)
    assert wmn.allow_nsfw() is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_allow_nsfw_falsey(monkeypatch, value):
    monkeypatch.setenv(NSFW_ENV, value)
    assert wmn.allow_nsfw() is False


# ---------------------------------------------------------------------------
# Aile istek butcesi (D10)
# ---------------------------------------------------------------------------


def test_aile_butcesi_sabitleri():
    assert budget.SITECHECK_FAMILY_BUDGET == {"O1": 0, "O2": 600, "O3": 1200}
    assert budget.PRIMARY_SHARE == 0.80
    assert budget.SEARCH_SHARE == 0.20


def test_aile_butcesi_mod_normalize():
    assert budget.family_budget("derin") == 1200
    assert budget.family_budget("hizli") == 0
    assert budget.family_budget(None) == 600
    assert budget.family_budget("bilinmeyen") == 600


def test_aile_paylari():
    # O2: 600 -> 480 + 120 ; O3: 1200 -> 960 + 240
    assert budget.share_budget("O2", "primary") == 480
    assert budget.share_budget("O2", "search") == 120
    assert budget.share_budget("O3", "primary") == 960
    assert budget.share_budget("O3", "search") == 240
    assert budget.share_budget("O1", "primary") == 0
    assert budget.share_budget("O2", "primary") + budget.share_budget(
        "O2", "search"
    ) == budget.family_budget("O2")


def test_aile_butcesi_env_override(monkeypatch):
    monkeypatch.setenv(BUDGET_ENV, "250")
    assert budget.family_budget("O3") == 250
    assert budget.share_budget("O3", "primary") == 200
    assert budget.share_budget("O3", "search") == 50


def test_aile_butcesi_env_tarama_butcesine_kirpilir(monkeypatch):
    """M1: env, mod profilinin max_http_requests'ini asamaz."""
    monkeypatch.setenv(BUDGET_ENV, "999999")
    assert budget.family_budget("O2") == MODE_PROFILES["O2"]["max_http_requests"]
    assert budget.family_budget("O3") == MODE_PROFILES["O3"]["max_http_requests"]


@pytest.mark.parametrize("value", ["abc", "-7", "  "])
def test_aile_butcesi_gecersiz_env_varsayilana_doner(monkeypatch, value):
    monkeypatch.setenv(BUDGET_ENV, value)
    assert budget.family_budget("O2") == 600


def test_site_limit_bolme_degismezi():
    assert budget.site_limit_for(480, 10) == 48
    assert budget.site_limit_for(480, 6) == 80
    assert budget.site_limit_for(960, 10) == 96
    assert budget.site_limit_for(600, 1) == 600
    assert budget.site_limit_for(600, 0) == 600  # sifira bolme yok
    # pay < n -> 0 (n*limit <= pay degismezi korunur, Z3 M1)
    assert budget.site_limit_for(5, 10) == 0
    assert budget.site_limit_for(0, 10) == 0


def test_butce_0_istek_yok():
    assert wmn.sites_for_username("alice", "O1") == []


# ---------------------------------------------------------------------------
# Butce dilimi (limit)
# ---------------------------------------------------------------------------


def test_wmn_payi_ve_toplam_limit():
    """E1: limit, elle yazilmis ile WMN arasinda paylasilir (WMN >= 20)."""
    _, extra = wmn.split_sites()
    for limit in (40, 60, 100, 200):
        sites = wmn.sites_for_username("alice", "O2", limit)
        manual = [s for s in sites if not s.category]
        wmn_part = [s for s in sites if s.category]
        assert len(sites) == limit, (limit, len(sites))
        assert len(wmn_part) >= min(20, len(extra)), (limit, len(wmn_part))
        assert len(manual) > 0
        assert manual == [s for s in SITES if s in manual][: len(manual)]


def test_wmn_payi_ondan_fazla_kullanici_adinda_da_var():
    """A2b'de n=10 -> site_limit=60 idi ve WMN payi 0'a dusuyordu (E1 duzeltmesi)."""
    limit = 60
    sites = wmn.sites_for_username("alice", "O2", limit)
    manual = [s for s in sites if not s.category]
    wmn_part = [s for s in sites if s.category]

    assert len(sites) == limit
    assert len(wmn_part) == 20
    assert len(manual) == 40
    assert len(manual) + len(wmn_part) == limit


def test_limit_manual_ustunde_wmn_ekler():
    limit = len(SITES) + 30
    sites = wmn.sites_for_username("alice", "O2", limit)
    manual = [s for s in sites if not s.category]
    assert len(sites) == limit
    # elle yazilmis spec'ler basta ve korunur (limit yettigi kadar)
    assert [s.key for s in sites[: len(manual)]] == [s.key for s in SITES][: len(manual)]
    assert all(s.category for s in sites[len(manual) :])


def test_limit_mevcut_sayidan_buyukse_tumunu_dondurur():
    _, extra = wmn.split_sites()
    sites = wmn.sites_for_username("alice", "O2", 10_000)
    assert len(sites) == len(SITES) + len(extra)


def test_limit_verilmezse_birincil_pay_varsayimi():
    """Limit verilmezse tek kullanici varsayimiyla **birincil pay** kullanilir."""
    _, extra = wmn.split_sites()
    available = len(SITES) + len(extra)
    o2 = wmn.sites_for_username("alice", "O2")
    assert len(o2) == min(budget.share_budget("O2", "primary"), available)
    o3 = wmn.sites_for_username("alice", "O3")
    assert len(o3) == min(budget.share_budget("O3", "primary"), available)


# ---------------------------------------------------------------------------
# Rotasyon
# ---------------------------------------------------------------------------


def test_rotasyon_deterministik():
    a = [s.key for s in wmn.sites_for_username("alice", "O2", 100)]
    b = [s.key for s in wmn.sites_for_username("alice", "O2", 100)]
    assert a == b


def test_rotasyon_farkli_kullanicilar_farkli_dilim():
    alice = {s.key for s in wmn.sites_for_username("alice", "O2", 100)}
    bob = {s.key for s in wmn.sites_for_username("bob", "O2", 100)}
    assert alice != bob
    assert len(alice) == len(bob) == 100


def test_rotasyon_sarmal_ve_tekil():
    _, extra = wmn.split_sites()
    sites = wmn.sites_for_username("alice", "O3", len(SITES) + 400)
    wmn_keys = [s.key for s in sites if wmn.is_wmn(s)]
    assert len(wmn_keys) == 400
    assert len(set(wmn_keys)) == 400
    assert set(wmn_keys) <= {s.key for s in extra}


def test_rotasyon_elle_yazilanlar_once():
    sites = wmn.sites_for_username("alice", "O2", 100)
    assert [s.key for s in sites[: len(SITES)]] == [s.key for s in SITES]


# ---------------------------------------------------------------------------
# Plan: kullanici adi tavani + site_limit (D2)
# ---------------------------------------------------------------------------


def _plan_ctx(usernames: int, mode: str | None = "O2", scan_id: str = "s1") -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id=scan_id,
        target=Target(first_name="Test", last_name="Smoke"),
        seeds=[
            Seed(platform=Platform.GITHUB, username=f"user{i}") for i in range(usernames)
        ],
    )


async def test_plan_kullanici_adi_tavani():
    connector = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))
    connector.scan_mode = "O2"

    tasks = await connector.plan(_plan_ctx(25, scan_id="cap-test"))

    assert len(tasks) == MAX_PLANNED_USERNAMES == 10


async def test_plan_site_limit_ve_mod_yazar():
    connector = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))
    connector.scan_mode = "O2"

    tasks = await connector.plan(_plan_ctx(10, scan_id="plan-o2"))

    assert len(tasks) == 10
    assert {t.params["mode"] for t in tasks} == {"O2"}
    # D10: birincil connector aile butcesinin %80'ini kullanir
    assert {t.params["site_limit"] for t in tasks} == {
        budget.primary_site_limit("O2", len(tasks))
    }


@pytest.mark.parametrize("mode", ["O1", "O2", "O3"])
async def test_plan_birincil_payi_asmaz(mode):
    connector = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))
    connector.scan_mode = mode

    tasks = await connector.plan(_plan_ctx(10, scan_id=f"plan-{mode}"))

    share = budget.share_budget(mode, "primary")
    if share == 0:
        assert {t.params["site_limit"] for t in tasks} == {0}
        return
    total = sum(t.params["site_limit"] for t in tasks)
    assert total <= share
    assert total >= share - len(tasks)  # bolme kalani toleransi
    assert max(t.params["site_limit"] for t in tasks) >= 40


async def test_plan_ctx_modu_kullanilir():
    """``scan_mode`` verilmezse ``ctx.mode``, o da yoksa O2."""
    connector = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))

    tasks = await connector.plan(_ctx("O3"))
    assert tasks and tasks[0].params["mode"] == "O3"
    assert tasks[0].params["site_limit"] == budget.primary_site_limit(
        "O3", len(tasks)
    )

    connector2 = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))
    tasks2 = await connector2.plan(_plan_ctx(1, scan_id="varsayilan"))
    assert tasks2[0].params["mode"] == "O2"


def test_connector_ve_engine_kullanici_adi_tavani_hizali():
    assert MAX_PLANNED_USERNAMES == SITECHECK_MAX_USERNAMES


# ---------------------------------------------------------------------------
# Motor: headers gercekten geciyor mu
# ---------------------------------------------------------------------------


async def test_engine_headers_gecirir():
    fake = FakeHttp(status=200, text="ok")
    spec = SiteSpec(
        key="headerli",
        name="Headerli",
        platform=Platform.WEB,
        url_template="https://header.example/{u}",
        absent_statuses=(404,),
        expect_status=200,
        headers={"Accept": "application/json", "Accept-Language": "en-US"},
        skip_robots=True,
    )
    engine = UsernameCheckEngine(http_client=fake, sites=[spec], concurrency=1)

    results = await engine.check_username("alice")

    assert [r.status for r in results] == ["exists"]
    assert fake.calls[0]["url"] == "https://header.example/alice"
    assert fake.calls[0]["headers"] == {
        "Accept": "application/json",
        "Accept-Language": "en-US",
    }


async def test_engine_headers_yoksa_none():
    fake = FakeHttp(status=404)
    spec = SiteSpec(
        key="basliksiz",
        name="Basliksiz",
        platform=Platform.WEB,
        url_template="https://plain.example/{u}",
        skip_robots=True,
    )
    engine = UsernameCheckEngine(http_client=fake, sites=[spec], concurrency=1)

    await engine.check_username("alice")

    assert fake.calls[0]["headers"] is None


# ---------------------------------------------------------------------------
# Connector.run: butce payi kullanimi
# ---------------------------------------------------------------------------


async def test_connector_explicit_sites_korunur():
    """Acik `sites=` verilince mod/WMN devreye girmez (mevcut testler boyle)."""
    fake = FakeHttp(status=404)
    sites = [SITES[0], SITES[1]]
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, sites=sites, concurrency=2),
        sites=sites,
    )
    task = connector._make_task("alice")

    obs = await connector.run(task, _ctx("O3"))

    assert obs[0].data["total_sites"] == len(sites)
    assert obs[0].data["sites_planned"] == len(sites)


async def test_connector_run_site_limit_uygular():
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=8)
    )
    task = connector._make_task("alice")
    task.params["mode"] = "O2"
    task.params["site_limit"] = 70

    obs = await connector.run(task, _ctx())

    assert obs[0].data["site_limit"] == 70
    assert obs[0].data["sites_planned"] == 70
    assert obs[0].data["total_sites"] == 70
    assert len(fake.calls) == 70


async def test_connector_run_limit_yoksa_fail_safe_tavan():
    """Z3 C1: limitsiz (eski) gorev tam butceyi degil, fail-safe tavani kullanir."""
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=8)
    )
    task = connector._make_task("alice")  # site_limit yok
    task.params["mode"] = "O2"

    obs = await connector.run(task, _ctx())

    assert obs[0].data["site_limit"] == budget.primary_site_limit(
        "O2", MAX_PLANNED_USERNAMES
    )
    assert obs[0].data["sites_planned"] == obs[0].data["site_limit"]


async def test_connector_run_limit_yoksa_o1_istek_atmaz():
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=8)
    )
    task = connector._make_task("alice")
    task.params["mode"] = "O1"

    obs = await connector.run(task, _ctx())

    assert obs[0].data["sites_planned"] == 0
    assert fake.calls == []


async def test_connector_uctan_uca_butce_asilmaz():
    """Plan + run: 10 kullanici adi, O2 butcesi icinde kalinir."""
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=16)
    )
    connector.scan_mode = "O2"
    ctx = _plan_ctx(10, scan_id="uctan-uca")

    tasks = await connector.plan(ctx)
    planned = sum(t.params["site_limit"] for t in tasks)
    for task in tasks:
        await connector.run(task, ctx)

    assert planned <= budget.share_budget("O2", "primary") + len(tasks)
    assert len(fake.calls) == planned
    assert len(fake.calls) <= MODE_PROFILES["O2"]["max_http_requests"]


# ---------------------------------------------------------------------------
# Z4-1 (D10): tarama seviyesinde aile butcesi — iki connector birlikte
# ---------------------------------------------------------------------------


class _StubSearchProvider:
    """Arama saglayicisi yerine gecen, 30 sentetik URL donduren stub."""

    async def search(self, query: str, limit: int = 10, site=None):  # noqa: ANN001
        class _Hit:
            def __init__(self, i: int) -> None:
                self.url = f"https://ornek{i}.example/user"

        return [_Hit(i) for i in range(30)]


@pytest.mark.parametrize("mode", ["O1", "O2", "O3"])
async def test_aile_butcesi_tarama_seviyesinde_asmaz(mode):
    """Z4 C1: sitecheck + sitecheck_search toplam planlanan istek <= aile butcesi."""
    from showis_connectors.sitecheck.search_connector import SiteCheckSearchConnector

    ctx = ScanContext(
        case_id="c1",
        scan_id=f"family-{mode}",
        target=Target(first_name="Test", last_name="Smoke"),
        seeds=[
            Seed(platform=Platform.GITHUB, username=f"user{i}") for i in range(10)
        ],
    )
    primary = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))
    primary.scan_mode = mode
    search = SiteCheckSearchConnector(
        engine=UsernameCheckEngine(http_client=FakeHttp()),
        search_provider=_StubSearchProvider(),
    )
    search.scan_mode = mode

    primary_tasks = await primary.plan(ctx)
    search_tasks = await search.plan(ctx)
    all_tasks = primary_tasks + search_tasks

    assert all_tasks, "en az bir gorev planlanmali"
    # Tek bir limitsiz gorev kalmamali (Z3 C1'in kok nedeni)
    assert all("site_limit" in t.params for t in all_tasks)
    assert all(t.params["mode"] == mode for t in all_tasks)

    total = sum(int(t.params["site_limit"]) for t in all_tasks)
    assert total <= budget.family_budget(mode)
    if mode != "O1":
        # Aile butcesi tam olarak kullanilmali (paylar birbirini tamamlar)
        assert total == budget.family_budget(mode)

    # Iki connector da kendi payini kullanir (birincil %80 / arama %20)
    if mode != "O1":
        assert sum(t.params["site_limit"] for t in primary_tasks) == budget.share_budget(
            mode, "primary"
        )
        assert sum(t.params["site_limit"] for t in search_tasks) == budget.share_budget(
            mode, "search"
        )


async def test_aile_butcesi_uygulanan_site_sayilari():
    """Plan limitleri gercekten o kadar site tarar (limit <= gercek liste)."""
    from showis_connectors.sitecheck.search_connector import SiteCheckSearchConnector

    ctx = ScanContext(
        case_id="c1",
        scan_id="family-run",
        target=Target(first_name="Test", last_name="Smoke"),
        seeds=[Seed(platform=Platform.GITHUB, username=f"user{i}") for i in range(10)],
    )
    connector = SiteCheckConnector(engine=UsernameCheckEngine(http_client=FakeHttp()))
    connector.scan_mode = "O2"
    search = SiteCheckSearchConnector(
        engine=UsernameCheckEngine(http_client=FakeHttp()),
        search_provider=_StubSearchProvider(),
    )
    search.scan_mode = "O2"

    for conn in (connector, search):
        tasks = await conn.plan(ctx)
        limit = tasks[0].params["site_limit"]
        for task in tasks:
            sites = wmn.sites_for_username(
                str(task.params["username"]), "O2", limit
            )
            assert len(sites) <= limit

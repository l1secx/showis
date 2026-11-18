"""A2 — WMN verisinin motora baglanmasi: yukleyici, mod tavani, rotasyon.

Ag yok: motor testleri sahte HTTP istemcisiyle, veri testleri yerel
``data/wmn-sites.json`` ile calisir.
"""

from __future__ import annotations

import httpx
import pytest
from showis_connectors.sitecheck import wmn
from showis_connectors.sitecheck.connector import SiteCheckConnector
from showis_connectors.sitecheck.engine import UsernameCheckEngine
from showis_connectors.sitecheck.sites import SITES, SiteSpec
from showis_contracts import Platform, ScanContext, Seed, Target
from showis_engine.scan_modes import MODE_PROFILES

NSFW_KEYS_ENV = wmn.ALLOW_NSFW_ENV
CAP_ENV = wmn.MAX_SITES_ENV


@pytest.fixture(autouse=True)
def _temiz_politika(monkeypatch):
    """Her test temiz env ve onbellekle baslar."""
    monkeypatch.delenv(CAP_ENV, raising=False)
    monkeypatch.delenv(NSFW_KEYS_ENV, raising=False)
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
        assert spec.notes.startswith(wmn.WMN_NOTES_PREFIX)


def test_load_wmn_specs_headers_tasinir():
    specs = {s.key: s for s in wmn.load_wmn_specs()}
    apex = specs["apex-legends"]
    assert apex.headers
    assert "User-Agent" in apex.headers or "Accept-Language" in apex.headers
    # headers'i olmayan kayitlar bos sozlukle gelir (None degil)
    assert specs["247sports"].headers == {}


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
    manual_specs = {s.url_template for s in SITES}
    assert merged["bitbucket"].url_template in manual_specs


def test_merged_sites_nsfw_varsayilan_disarida():
    merged = wmn.merged_sites()
    assert not any(wmn.is_nsfw(s) for s in merged)
    assert all(wmn.spec_category(s) is None for s in SITES)


def test_merged_sites_nsfw_env_ile_dahil(monkeypatch):
    taban = len(wmn.merged_sites())
    monkeypatch.setenv(NSFW_KEYS_ENV, "1")
    dahil = wmn.merged_sites()
    assert any(wmn.is_nsfw(s) for s in dahil)
    assert len(dahil) > taban
    assert len(dahil) >= taban + 30


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_allow_nsfw_truthy(monkeypatch, value):
    monkeypatch.setenv(NSFW_KEYS_ENV, value)
    assert wmn.allow_nsfw() is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_allow_nsfw_falsey(monkeypatch, value):
    monkeypatch.setenv(NSFW_KEYS_ENV, value)
    assert wmn.allow_nsfw() is False


# ---------------------------------------------------------------------------
# Mod tavani / butce
# ---------------------------------------------------------------------------


def test_mod_tavanlari():
    assert wmn.SITE_CAP_BY_MODE == {"O1": 0, "O2": 120, "O3": 150}


def test_o1_wmn_kapali():
    sites = wmn.sites_for_username("alice", "O1")
    assert [s.key for s in sites] == [s.key for s in SITES]
    assert all(not s.notes.startswith(wmn.WMN_NOTES_PREFIX) for s in sites)


@pytest.mark.parametrize(
    "mode,cap", [("O2", wmn.SITE_CAP_BY_MODE["O2"]), ("O3", wmn.SITE_CAP_BY_MODE["O3"])]
)
def test_mod_dilimi_beklenen_uzunluk(mode, cap):
    _, extra = wmn.split_sites()
    sites = wmn.sites_for_username("alice", mode)
    assert len(sites) - len(SITES) == min(cap, len(extra))


def test_mod_normalize_edilir():
    assert wmn.sites_for_username("alice", "derin") == wmn.sites_for_username("alice", "O3")
    assert wmn.sites_for_username("alice", None) == wmn.sites_for_username("alice", "O2")
    assert wmn.sites_for_username("alice", "bilinmeyen") == wmn.sites_for_username("alice", "O2")


def test_env_tavani_ezilir(monkeypatch):
    monkeypatch.setenv(CAP_ENV, "5")
    assert len(wmn.sites_for_username("alice", "O2")) - len(SITES) == 5

    monkeypatch.setenv(CAP_ENV, "0")
    assert wmn.sites_for_username("alice", "O2") == list(SITES)


@pytest.mark.parametrize("value", ["abc", "-3", "  "])
def test_env_gecersizse_varsayilan(monkeypatch, value):
    monkeypatch.setenv(CAP_ENV, value)
    _, extra = wmn.split_sites()
    sites = wmn.sites_for_username("alice", "O2")
    assert len(sites) - len(SITES) == min(wmn.SITE_CAP_BY_MODE["O2"], len(extra))


def test_butce_o2_alti_kullanici():
    """O2: 6 kullanici adi x 120 WMN sitesi = 720 istek (tavan)."""
    toplam_wmn = 0
    toplam = 0
    for username in ("alice", "bob", "carol", "dave", "erin", "frank"):
        sites = wmn.sites_for_username(username, "O2")
        toplam += len(sites)
        toplam_wmn += len(sites) - len(SITES)
    assert toplam_wmn <= 720
    # Gercek istek sayisi (elle yazilanlar dahil) O2 butcesini asmamali.
    assert toplam <= MODE_PROFILES["O2"]["max_http_requests"]


def test_butce_o3_on_kullanici():
    """O3: 10 kullanici adi x 150 WMN sitesi = 1500 istek (tavan)."""
    toplam_wmn = 0
    for i in range(10):
        sites = wmn.sites_for_username(f"kullanici{i}", "O3")
        toplam_wmn += len(sites) - len(SITES)
    assert toplam_wmn <= 1500


def test_o3_toplam_istek_butcesi_asimi_bilinen_risk():
    """BILINEN RISK (A2-R1): O3'te 10 x (59+150) istek mod butcesini asar.

    Tavan 150'ye sabitken O3'te toplam >= 2000 istek gerekir; tarama butcesi
    2000. Tavanlar dusurulurse (orn. O3=130) bu test guncellenmeli.
    """
    toplam = sum(len(wmn.sites_for_username(f"u{i}", "O3")) for i in range(10))
    assert toplam == 10 * (len(SITES) + min(150, len(wmn.split_sites()[1])))
    assert toplam > MODE_PROFILES["O3"]["max_http_requests"]


# ---------------------------------------------------------------------------
# Rotasyon
# ---------------------------------------------------------------------------


def test_rotasyon_deterministik():
    a = [s.key for s in wmn.sites_for_username("alice", "O2")]
    b = [s.key for s in wmn.sites_for_username("alice", "O2")]
    assert a == b


def test_rotasyon_farkli_kullanicilar_farkli_dilim():
    alice = {s.key for s in wmn.sites_for_username("alice", "O2")}
    bob = {s.key for s in wmn.sites_for_username("bob", "O2")}
    assert alice != bob
    # Iki dilim de tam boyutta, ama icerikleri farkli
    assert len(alice) == len(bob) == len(SITES) + 120


def test_rotasyon_sarmal():
    """Dilim, listenin sonunu asinca bastan devam eder (sarma)."""
    _, extra = wmn.split_sites()
    sites = wmn.sites_for_username("alice", "O3")
    wmn_keys = [s.key for s in sites if s.notes.startswith(wmn.WMN_NOTES_PREFIX)]
    assert len(wmn_keys) == 150
    assert len(set(wmn_keys)) == 150
    assert set(wmn_keys) <= {s.key for s in extra}


def test_rotasyon_elle_yazilanlar_her_zaman_dahil():
    for mode in ("O1", "O2", "O3"):
        keys = {s.key for s in wmn.sites_for_username("alice", mode)}
        assert {s.key for s in SITES} <= keys


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
        headers={"User-Agent": "wm-ua/1.0", "Accept": "application/json"},
        skip_robots=True,
    )
    engine = UsernameCheckEngine(http_client=fake, sites=[spec], concurrency=1)

    results = await engine.check_username("alice")

    assert [r.status for r in results] == ["exists"]
    assert fake.calls and fake.calls[0]["url"] == "https://header.example/alice"
    assert fake.calls[0]["headers"] == {
        "User-Agent": "wm-ua/1.0",
        "Accept": "application/json",
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
# Connector: mod bazli site secimi
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

    assert len(obs) == 1
    assert obs[0].data["total_sites"] == len(sites)
    assert obs[0].data["sites_planned"] == len(sites)


async def test_connector_varsayilan_mod_o2():
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=8)
    )
    task = connector._make_task("alice")

    obs = await connector.run(task, _ctx())

    beklenen = len(SITES) + min(wmn.SITE_CAP_BY_MODE["O2"], len(wmn.split_sites()[1]))
    assert obs[0].data["scan_mode"] == "O2"
    assert obs[0].data["sites_planned"] == beklenen
    assert obs[0].data["total_sites"] == beklenen


async def test_connector_task_params_modu_oncelikli():
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=8)
    )
    task = connector._make_task("alice")
    task.params["mode"] = "O1"

    obs = await connector.run(task, _ctx("O3"))

    assert obs[0].data["scan_mode"] == "O1"
    assert obs[0].data["sites_planned"] == len(SITES)


async def test_connector_wmn_headers_isteke_gecer():
    """WMN dilimindeki baslikli site motor uzerinden istege yansimali."""
    fake = FakeHttp(status=404)
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(http_client=fake, concurrency=8)
    )
    task = connector._make_task("alice")

    await connector.run(task, _ctx())

    ua_calls = [c for c in fake.calls if c["headers"] and "User-Agent" in c["headers"]]
    assert ua_calls, "baslikli en az bir WMN sitesi taranmis olmali"
    assert all(c["url"].startswith("https://") for c in fake.calls)

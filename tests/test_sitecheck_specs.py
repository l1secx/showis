"""T-357 — site-check spesifikasyonlarının statik ve sınıflandırma testleri.

Ağ yok: sınıflandırma testleri `respx` ile sahte yanıt kullanır.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest
import respx
from showis_connectors.sitecheck.engine import (
    ABSENT,
    EXISTS,
    UsernameCheckEngine,
)
from showis_connectors.sitecheck.sites import SITES, SITES_BY_KEY, SiteSpec
from showis_contracts import Platform, Target
from showis_engine.http import HttpClient
from showis_engine.scan_modes import estimate_scan, site_count

# ---------------------------------------------------------------------------
# Statik doğrulama (her spec için ayrı test → 52 test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", SITES, ids=[s.key for s in SITES])
def test_spec_semasi_gecerli(spec: SiteSpec):
    assert spec.key and spec.key.strip()
    assert spec.name and spec.name.strip()
    assert isinstance(spec.platform, Platform)
    assert spec.url_template.startswith(("http://", "https://"))
    assert "{u}" in spec.url_template
    parsed = urlparse(spec.url_template.replace("{u}", "ornek"))
    assert parsed.hostname


@pytest.mark.parametrize("spec", SITES, ids=[s.key for s in SITES])
def test_spec_absent_sinyali_var(spec: SiteSpec):
    """'Her 200 = var' tuzağı yok: mutlaka bir absent sinyali bulunmalı."""
    assert (
        spec.absent_statuses
        or spec.absent_regex is not None
        or spec.json_path is not None
        or spec.present_regex is not None
    ), f"{spec.key}: absent sinyali yok"


@pytest.mark.parametrize("spec", SITES, ids=[s.key for s in SITES])
def test_spec_regexleri_derlenir(spec: SiteSpec):
    for pattern in (spec.present_regex, spec.absent_regex):
        if pattern is not None:
            re.compile(pattern)


@pytest.mark.parametrize("spec", SITES, ids=[s.key for s in SITES])
def test_json_spec_accept_header_tasir(spec: SiteSpec):
    if spec.json_path is not None:
        assert spec.headers.get("Accept") == "application/json"
        assert spec.expect_status == 200


def test_anahtarlar_tekil():
    keys = [spec.key for spec in SITES]
    assert len(keys) == len(set(keys)), "yinelenen site anahtarı"
    assert set(SITES_BY_KEY) == set(keys)


def test_kapsam_hedefe_yakin():
    """ROADMAP 2.3: 100+ hedefi; bugünkü taban 50'nin altına düşmemeli."""
    assert len(SITES) >= 50
    assert site_count() == len(SITES)


def test_platformlar_cogunlukla_tekil():
    """Aynı platform iki site olabilir; entity kimliği ad alanıyla ayrılmalı."""
    seen: dict[str, str] = {}
    for spec in SITES:
        if spec.platform in seen:
            # WhatsMyName WEB kümesi gibi çoklu siteler id_namespace taşır.
            if spec.id_namespace and spec.id_namespace != spec.key:
                pytest.fail(f"id_namespace anahtarla eşleşmiyor: {spec.key}")
            if not spec.id_namespace:
                pytest.fail(
                    f"platform {spec.platform.value} iki spec'te: "
                    f"{seen[spec.platform]} / {spec.key}"
                )
        seen[spec.platform] = spec.key


def test_validate_sites_statik_temiz():
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import validate_sites

    assert validate_sites.static_issues() == []


def test_tahmin_site_sayisi_listeden_turetilir(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    target = Target(first_name="Kerem", last_name="Boz", hints={"school": "ODTÜ"})
    est = estimate_scan(target, "O2", calibration_path="/tmp/olmayan-kalibrasyon.json")
    sitecheck = next(step for step in est["steps"] if step["key"] == "sitecheck")
    from showis_engine.scan_modes import site_count_for_mode

    active_sites = site_count_for_mode("O2")
    assert str(active_sites) in sitecheck["label"]
    usernames = sitecheck["tasks"]
    assert est["requests"] >= usernames * active_sites


# ---------------------------------------------------------------------------
# Sınıflandırma testleri (mock yanıt)
# ---------------------------------------------------------------------------

_SAMPLE = [
    SiteSpec(
        key="mock-status",
        name="MockStatus",
        platform=Platform.WEB,
        url_template="https://mock-status.example/{u}",
        skip_robots=True,
    ),
    SiteSpec(
        key="mock-absent-regex",
        name="MockAbsentRegex",
        platform=Platform.TUMBLR,
        url_template="https://mock-regex.example/{u}",
        absent_regex=r"sayfa bulunamadı",
        skip_robots=True,
    ),
    SiteSpec(
        key="mock-present-regex",
        name="MockPresentRegex",
        platform=Platform.MEDIUM,
        url_template="https://mock-present.example/{u}",
        present_regex=r"profile-header",
        no_match_is_absent=True,
        skip_robots=True,
    ),
]


def _engine(sites: list[SiteSpec]) -> UsernameCheckEngine:
    return UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False), sites=sites, concurrency=1
    )


@pytest.mark.asyncio
async def test_404_absent_sayilir():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://mock-status.example/kboz").respond(404)
        results = await _engine([_SAMPLE[0]]).check_username("kboz")
    assert results[0].status == ABSENT


@pytest.mark.asyncio
async def test_200_ve_absent_regex_absent_sayilir():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://mock-regex.example/kboz").respond(200, text="Üzgünüz, sayfa bulunamadı")
        results = await _engine([_SAMPLE[1]]).check_username("kboz")
    assert results[0].status == ABSENT


@pytest.mark.asyncio
async def test_200_exists_sayilir():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://mock-status.example/kboz").respond(200, text="kboz profil")
        results = await _engine([_SAMPLE[0]]).check_username("kboz")
    assert results[0].status == EXISTS


@pytest.mark.asyncio
async def test_present_regex_eslesmezse_absent():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://mock-present.example/kboz").respond(200, text="jenerik sayfa")
        results = await _engine([_SAMPLE[2]]).check_username("kboz")
    assert results[0].status == ABSENT


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", [_SAMPLE[0], _SAMPLE[1], _SAMPLE[2]], ids=lambda s: s.key)
async def test_ornek_specler_gercek_url_sablonuyla_istek_atilabilir(spec: SiteSpec):
    """Her örnek spec'te {u} yerine konan ad URL'ye girer ve tek istek atılır."""
    username = "ornek_kullanici"
    url = spec.url_template.format(u=username)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url).respond(404)
        results = await _engine([spec]).check_username(username)
    assert route.call_count == 1
    assert results[0].url == url


def test_gercek_listeden_ornek_specler_http_cagrisi_uretir():
    """Rastgele 10 gerçek spec: url_template {u} ile formatlanabilir ve GET üretir."""
    sample = SITES[:: max(1, len(SITES) // 10)][:10]
    assert len(sample) == 10
    for spec in sample:
        url = spec.url_template.format(u="ornek123")
        assert "ornek123" in url
        assert url.startswith("https://")


# ---------------------------------------------------------------------------
# T-365: {u} yerleştirmesi güvenli olmalı (URL enjeksiyonu yok)
# ---------------------------------------------------------------------------

from showis_connectors.sitecheck.engine import (  # noqa: E402
    build_site_url,
    normalize_username_for_url,
)
from showis_connectors.sitecheck.search_connector import (  # noqa: E402
    SiteCheckSearchConnector,
)
from showis_contracts import ScanContext, Seed  # noqa: E402


@pytest.mark.parametrize(
    "value",
    ["../etc/passwd", "a?x=1", "a b", "a@evil.tld", "a#frag", "a%2f..%2f", "", "@"],
)
def test_guvensiz_kullanici_adi_reddedilir(value):
    assert normalize_username_for_url(value) is None


@pytest.mark.parametrize("value", ["kboz", "kerem.boz", "kerem-boz", "kerem_boz", "a1"])
def test_normal_kullanici_adi_degismez(value):
    assert normalize_username_for_url(value) == value


def test_basindaki_at_ve_bosluk_temizlenir():
    assert normalize_username_for_url("  @kboz  ") == "kboz"


def test_url_sablonu_enjeksiyona_kapali():
    spec = SiteSpec(
        key="inj",
        name="Injection",
        platform=Platform.WEB,
        url_template="https://ornek.example/u/{u}/profil?lang=tr",
        skip_robots=True,
    )
    url = build_site_url(spec, "kboz")
    assert url == "https://ornek.example/u/kboz/profil?lang=tr"

    for bad in ("../etc/passwd", "a?x=1", "a#y", "a@evil.tld", "a b"):
        assert build_site_url(spec, bad) is None


def test_spec_username_pattern_istisnasi():
    spec = SiteSpec(
        key="tilde",
        name="Tilde",
        platform=Platform.WEB,
        url_template="https://ornek.example/~{u}",
        skip_robots=True,
        # Platform yalniz kucuk harf kabul ediyor: istisna deseni daraltir
        username_pattern=r"^[a-z]{3,10}$",
    )
    assert build_site_url(spec, "KBOZ") is None  # istisna deseni reddeder
    assert build_site_url(spec, "kboz") == "https://ornek.example/~kboz"
    # Istisna deseninde de enjeksiyon karakteri gecmez
    assert build_site_url(spec, "kboz?x=1") is None


@pytest.mark.asyncio
async def test_guvensiz_kullanici_adi_istek_atmaz():
    spec = SiteSpec(
        key="nosafe",
        name="NoSafe",
        platform=Platform.WEB,
        url_template="https://ornek.example/{u}",
        skip_robots=True,
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.route(host="ornek.example").respond(200)
        results = await _engine([spec]).check_username("../etc/passwd")
    assert route.call_count == 0
    assert results[0].status == "skipped"
    assert results[0].detail == "güvenli olmayan kullanıcı adı"


@pytest.mark.asyncio
async def test_search_connector_guvensiz_kimligi_planlamaz():
    class _Hit:
        def __init__(self, url: str):
            self.url = url

    class _Provider:
        name = "fake"

        async def search(self, query, *, limit=20, site=None):
            return [
                _Hit("https://github.com/kboz"),
                _Hit("https://github.com/../etc/passwd"),
                _Hit("https://github.com/a?x=1"),
            ]

    connector = SiteCheckSearchConnector(
        search_provider=_Provider(), max_usernames=1, sites=[]
    )
    ctx = ScanContext(
        case_id="c",
        scan_id="s",
        target=Target(first_name="Kerem", last_name="Boz"),
        seeds=[Seed(platform=Platform.GITHUB, username="kboz")],
    )
    tasks = await connector.plan(ctx)
    planned = {str(t.params.get("username")) for t in tasks}
    assert "kboz" in planned
    assert not any("/" in name or "?" in name or ".." in name for name in planned)


def test_wmn_alt_kumesi_guvenli_ve_ad_alanli():
    """WhatsMyName WEB siteleri aynı entity id'ye çökmesin."""
    import re

    from showis_connectors.sitecheck.sites_wmn import WMN_SITES

    assert WMN_SITES, "en az bir WMN spec bekleniyor"
    for spec in WMN_SITES:
        assert spec.id_namespace == spec.key
        assert spec.present_regex, spec.key
        assert spec.require_username is False
        assert spec.skip_robots is False, f"{spec.key}: robots atlanmamalı"
        assert "{u}" in spec.url_template
        re.compile(spec.present_regex)
        if spec.absent_regex:
            re.compile(spec.absent_regex)


def test_wmn_genis_kumesi_yalniz_o3():
    from showis_connectors.sitecheck import SiteCheckConnector
    from showis_engine.scan_modes import site_count, site_count_for_mode

    conn = SiteCheckConnector()
    assert len(conn.sites_for_mode("O2")) == site_count_for_mode("O2")
    assert len(conn.sites_for_mode("O3")) == site_count_for_mode("O3")
    assert site_count_for_mode("O3") > site_count_for_mode("O2")
    assert len(conn.sites_for_mode("O3")) <= site_count()
    assert any(spec.id_namespace for spec in conn.sites_for_mode("O3"))
    assert not any(spec.id_namespace for spec in conn.sites_for_mode("O2"))

"""A1 — WMN -> SiteSpec donusum testleri.

Ag yok. Varsayilan olarak **repo ici kucuk fixture** (`tests/fixtures/
wmn-source.min.json`) kullanilir; testler hermetiktir (gecici /tmp dosyasina
bagimli degil). Tam gercek veriyle kosan testler `SHOWIS_WMN_REAL_DATA=1`
verildiginde calisir (opt-in); o durumda kaynak dosya yoksa test **fail**
eder — sessizce skip etmez (QA-Z3-07).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import import_wmn_sites as wmn  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
#: Hermetik kaynak: repo icinde, 6 kayitlik minimal WMN dokumani.
FIXTURE_SOURCE = REPO_ROOT / "tests" / "fixtures" / "wmn-source.min.json"
#: Tam veri yalnizca opt-in (SHOWIS_WMN_REAL_DATA=1) ve yerel dosyayla.
REAL_DATA_ENV = "SHOWIS_WMN_REAL_DATA"
REAL_SOURCE = Path("/tmp/opencode/wmn-source.json")
OUT_PATH = (
    REPO_ROOT / "packages/connectors/showis_connectors/sitecheck/data/wmn-sites.json"
)


def _real_source_or_fail() -> dict:
    """Opt-in gercek veri: env yoksa test atlanir, env var + dosya yoksa FAIL."""
    if os.getenv(REAL_DATA_ENV) != "1":
        pytest.skip(f"gercek veri testi opt-in: {REAL_DATA_ENV}=1")
    assert REAL_SOURCE.exists(), (
        f"{REAL_DATA_ENV}=1 ama kaynak yok: {REAL_SOURCE} "
        "(gercek veri testi sessizce atlanmaz)"
    )
    return json.loads(REAL_SOURCE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_source() -> dict:
    return json.loads(FIXTURE_SOURCE.read_text(encoding="utf-8"))

#: Allowlist disinda kalmasi gereken (kimlik/oturum tasiyabilecek) basliklar.
_SECRET_HEADERS = {
    "Authorization": "Bearer gizli",
    "proxy-authorization": "Basic gizli",
    "Cookie": "session=abc",
    "X-Api-Key": "anahtar",
}


def _record(**overrides):
    """Gecerli bir WMN kaydi; testler tek alani degistirir."""
    base = {
        "name": "Example Site",
        "uri_check": "https://example.com/user/{account}",
        "e_code": 200,
        "e_string": "profile-card",
        "m_string": "Not found",
        "m_code": 404,
        "known": ["someone"],
        "cat": "social",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def fixture_doc():
    """4 tutulan + dislanan kayitlar: duz GET, POST, cloudflare, invalid, regex."""
    return {
        "license": ["Copyright (C) 2015-2026 Micah Hoffman"],
        "categories": ["social"],
        "sites": [
            # 1) duz GET, regex ozel karakterli e_string/m_string
            _record(
                name="Plain Site",
                uri_check="https://plain.example/{account}",
                e_string="a.b (profile)",
                m_string="user not.found",
                m_code=404,
            ),
            # 2) ayni host, farkli path -> korunmali
            _record(
                name="Plain Site Blog",
                uri_check="https://plain.example/blog/{account}",
                e_string="blog-card",
                m_string="",
                m_code=404,
            ),
            # 3) POST govdeli -> dislanir
            _record(
                name="Post Site",
                uri_check="https://post.example/api",
                post_body='{"username": "{account}"}',
            ),
            # 4) cloudflare -> dislanir
            _record(name="Cf Site", protection=["cloudflare"]),
            # 5) valid: false -> dislanir
            _record(name="Invalid Site", valid=False),
            # 6) ayni normalize url -> duplicate
            _record(name="Plain Site Mirror", uri_check="https://PLAIN.example/{account}/"),
            # 7) m_code == e_code ve m_string bos -> "yok" sinyali yok
            _record(
                name="Silent Site",
                uri_check="https://silent.example/{account}",
                m_string="",
                m_code=200,
            ),
        ],
    }


def test_donusum_sayaclari(fixture_doc):
    result = wmn.convert(fixture_doc)
    assert result.excluded["post"] == 1
    assert result.excluded["protected"] == 1
    assert result.excluded["invalid"] == 1
    assert result.excluded["duplicate"] == 1
    assert result.excluded["ambiguous"] == 1
    # 7 kayit - 5 dislama = 2 tutulan (ayni host farkli path korunur)
    assert result.kept == 2
    assert result.sites_total == 7


def test_ayni_host_farkli_path_korunur(fixture_doc):
    result = wmn.convert(fixture_doc)
    templates = {s["url_template"] for s in result.sites}
    assert templates == {
        "https://plain.example/{u}",
        "https://plain.example/blog/{u}",
    }


def test_account_placeholder_u_olur(fixture_doc):
    result = wmn.convert(fixture_doc)
    for site in result.sites:
        assert "{account}" not in site["url_template"]
        assert "{u}" in site["url_template"]
        site["url_template"].format(u="ornek")  # format hatasi vermemeli


def test_regex_literal_kacisli(fixture_doc):
    result = wmn.convert(fixture_doc)
    site = next(s for s in result.sites if s["key"] == "plain-site")
    # WMN degeri duz metindir: '.' regex jokerine donusmemeli.
    assert site["present_regex"] == re.escape("a.b (profile)")
    assert site["absent_regex"] == re.escape("user not.found")
    assert "a\\.b" in site["present_regex"]
    assert "not\\.found" in site["absent_regex"]
    re.compile(site["present_regex"])
    re.compile(site["absent_regex"])


def test_alan_eslemesi(fixture_doc):
    site = next(s for s in wmn.convert(fixture_doc).sites if s["key"] == "plain-site")
    assert site["expect_status"] == 200
    assert site["absent_statuses"] == [404]
    assert site["category"] == "social"
    assert site["platform"] == "web"
    assert site["source"] == "wmn"
    assert site["name"] == "Plain Site"
    assert set(site) == {
        "key",
        "name",
        "url_template",
        "expect_status",
        "present_regex",
        "absent_regex",
        "absent_statuses",
        "category",
        "platform",
        "source",
    }


# ---------------------------------------------------------------------------
# A2b/D4: header allowlist
# ---------------------------------------------------------------------------


def test_headers_allowlist_tasinir():
    doc = {
        "sites": [
            _record(
                name="Header Site",
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "en-US,en;q=0.5",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
                    "Content-Type": "application/json",
                    "Origin": "https://header.example",
                    "Referer": "https://header.example/",
                    "X-Tool": "vt-ui-main",
                    "Cookie": "accept_rules=true;",
                },
            )
        ]
    }
    site = wmn.convert(doc).sites[0]
    assert site["headers"] == {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
    }


def test_headers_yoksa_alan_yazilmaz():
    doc = {"sites": [_record(name="No Header Site")]}
    site = wmn.convert(doc).sites[0]
    assert "headers" not in site

    # bos sozluk de alan uretmemeli
    doc = {"sites": [_record(name="Empty Header Site", headers={})]}
    assert "headers" not in wmn.convert(doc).sites[0]


def test_allowlist_disi_basliklar_sayilir():
    doc = {"sites": [_record(name="Secret Site", headers=_SECRET_HEADERS)]}
    result = wmn.convert(doc)

    assert "headers" not in result.sites[0]
    assert result.headers_carried == 0
    assert result.headers_dropped == 1
    assert result.headers_removed == len(_SECRET_HEADERS)


def test_headers_sayaclari(fixture_doc):
    fixture_doc["sites"][0]["headers"] = {"Accept": "text/html", "Cookie": "x=1"}
    doc, result = wmn.build_document(fixture_doc)

    generated = doc["_generated"]
    assert generated["headers_carried"] == 1
    assert generated["headers_dropped"] == 1
    assert generated["headers_removed"] == 1
    assert generated["header_allowlist"] == ["accept", "accept-language"]
    assert result.headers_carried == 1
    assert wmn.validate_sites(doc["sites"]) == []


def test_validate_headers_hatalari():
    base = {
        "key": "h",
        "url_template": "https://h.example/{u}",
        "expect_status": 200,
        "present_regex": None,
        "absent_regex": "x",
        "absent_statuses": [404],
    }
    assert any("headers" in i for i in wmn.validate_sites([{**base, "headers": {}}]))
    assert any(
        "headers" in i for i in wmn.validate_sites([{**base, "headers": {"A": 1}}])
    )
    assert any(
        "allowlist" in i
        for i in wmn.validate_sites([{**base, "headers": {"Cookie": "x"}}])
    )
    # allowlist ici basliklar sorun degil
    assert wmn.validate_sites([{**base, "headers": {"Accept": "text/html"}}]) == []



# ---------------------------------------------------------------------------
# A1b: kategori / NSFW korunumu
# ---------------------------------------------------------------------------


def test_nsfw_kategorisi_filtrelenmez():
    doc = {
        "sites": [
            _record(name="Nsfw Site", cat="xx NSFW xx"),
            _record(name="Safe Site", cat="social", uri_check="https://safe.example/{account}"),
        ]
    }
    sites = wmn.convert(doc).sites
    assert [s["category"] for s in sites] == ["xx NSFW xx", "social"]


def test_kategori_yoksa_misc():
    doc = {"sites": [_record(name="No Cat Site", cat="")]}
    assert wmn.convert(doc).sites[0]["category"] == "misc"


def test_m_code_e_code_ile_ayniysa_absent_statuses_bos():
    doc = {"sites": [_record(name="Same Code", m_code=200, m_string="missing page")]}
    site = wmn.convert(doc).sites[0]
    assert site["absent_statuses"] == []
    assert site["absent_regex"] == re.escape("missing page")


def test_http_sablonu_https_e_yukseltilir():
    doc = {"sites": [_record(name="Old Site", uri_check="http://old.example/{account}")]}
    result = wmn.convert(doc)
    assert result.sites[0]["url_template"] == "https://old.example/{u}"
    assert result.http_upgraded == 1


def test_key_cakismasi_sonek_alir():
    doc = {
        "sites": [
            _record(name="Foo Bar", uri_check="https://foo.example/{account}"),
            _record(name="Foo-Bar", uri_check="https://foo2.example/{account}"),
            _record(name="foo bar!", uri_check="https://foo3.example/{account}"),
        ]
    }
    keys = [s["key"] for s in wmn.convert(doc).sites]
    assert keys == ["foo-bar", "foo-bar-2", "foo-bar-3"]


def test_slugify_ascii_disi_ve_bos_ad():
    assert wmn.slugify("Buy Me a Coffee") == "buy-me-a-coffee"
    assert wmn.slugify("  ") == "site"
    assert wmn.slugify("Пример") == "site"


def test_bos_regexler_none(fixture_doc):
    site = next(s for s in wmn.convert(fixture_doc).sites if s["key"] == "plain-site-blog")
    assert site["absent_regex"] is None
    assert site["present_regex"] == re.escape("blog-card")


def test_validate_temiz_ve_hatali():
    ok = [
        {
            "key": "a",
            "url_template": "https://a.example/{u}",
            "expect_status": 200,
            "present_regex": None,
            "absent_regex": "x",
            "absent_statuses": [404],
        }
    ]
    assert wmn.validate_sites(ok) == []

    dup = ok + [dict(ok[0])]
    assert any("tekrarlaniyor" in i for i in wmn.validate_sites(dup))

    bad = [
        {
            "key": "b",
            "url_template": "http://b.example/{u}",
            "expect_status": "200",
            "present_regex": "(",
            "absent_regex": None,
            "absent_statuses": "404",
        }
    ]
    issues = wmn.validate_sites(bad)
    assert any("https" in i for i in issues)
    assert any("expect_status" in i for i in issues)
    assert any("present_regex" in i for i in issues)
    assert any("absent_statuses" in i for i in issues)


def test_dokuman_zarfini_tasir(fixture_doc):
    doc, result = wmn.build_document(fixture_doc)
    assert list(doc) == ["_license", "_generated", "sites"]
    assert doc["_license"]["name"] == "CC BY-SA 4.0"
    assert doc["_license"]["source"] == "https://github.com/WebBreacher/WhatsMyName"
    assert doc["_license"]["adapted"] is True
    assert doc["_generated"]["from"] == "wmn-data.json"
    assert doc["_generated"]["sites_total"] == 7
    assert doc["_generated"]["kept"] == result.kept == len(doc["sites"])
    assert doc["_generated"]["excluded"]["post"] == 1


def test_lisans_zarfi_cc_by_sa_zorunlu_alanlari(fixture_doc):
    """D5: lisans adi + URI + kaynak + telif + katkida bulunanlar."""
    doc, _ = wmn.build_document(fixture_doc)
    lic = doc["_license"]

    assert lic["name"] == "CC BY-SA 4.0"
    assert lic["uri"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert lic["source"] == "https://github.com/WebBreacher/WhatsMyName"
    assert lic["copyright"] == "Copyright (C) 2015-2026 Micah Hoffman"
    assert lic["adapted"] is True
    assert lic["authors"], "authors bos olmamali"
    # kaynakta authors yoksa telif satirina duser
    assert lic["authors"] == ["Copyright (C) 2015-2026 Micah Hoffman"]


def test_lisans_zarfi_kaynak_authors_kullanir():
    doc, _ = wmn.build_document({"authors": ["Bir", " Iki ", ""], "sites": []})
    assert doc["_license"]["authors"] == ["Bir", "Iki"]



def test_main_dosyaya_yazar_ve_dogrular(tmp_path, fixture_doc):
    source = tmp_path / "wmn-source.json"
    source.write_text(json.dumps(fixture_doc), encoding="utf-8")
    out = tmp_path / "out" / "wmn-sites.json"

    assert wmn.main(["--source", str(source), "--out", str(out)]) == 0

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["_generated"]["kept"] == 2
    assert wmn.validate_sites(written["sites"]) == []


def test_main_kaynak_yoksa_hata(tmp_path, capsys):
    code = wmn.main(["--source", str(tmp_path / "yok.json"), "--out", str(tmp_path / "o.json")])
    assert code == 1
    assert "kaynak bulunamadi" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Hermetik kaynak (tests/fixtures/wmn-source.min.json) — her zaman kosar
# ---------------------------------------------------------------------------


def test_fixture_hermetik_kaynak(fixture_source):
    """Fixture 6 kayit tasir: duz, header'li, NSFW, POST, invalid, cloudflare."""
    names = [s["name"] for s in fixture_source["sites"]]
    assert len(names) == 6
    assert "Fixture Nsfw" in names


def test_fixture_donusum_sayaclari(fixture_source):
    result = wmn.convert(fixture_source)

    assert result.sites_total == 6
    assert result.kept == 3  # duz + header'li + NSFW
    assert result.excluded["post"] == 1
    assert result.excluded["invalid"] == 1
    assert result.excluded["protected"] == 1
    assert result.headers_carried == 1
    assert result.headers_removed >= 2  # Cookie + User-Agent


def test_fixture_dokuman_ve_lisans(fixture_source):
    doc, _ = wmn.build_document(fixture_source)

    assert list(doc) == ["_license", "_generated", "sites"]
    license_block = doc["_license"]
    assert license_block["name"] == "CC BY-SA 4.0"
    assert license_block["uri"].startswith("https://creativecommons.org/licenses/by-sa/4.0")
    # Kaynaktaki authors kullanilir (fixture'da 2 isim)
    assert license_block["authors"] == ["Micah Hoffman", "Test Katkici"]
    # CC BY-SA 3(a)(1)(a)(iv)/(v): sorumluluk reddi + materyal URI'si
    assert license_block["disclaimer"]
    assert license_block["material_url"].startswith("https://")
    assert wmn.validate_sites(doc["sites"]) == []


def test_fixture_uretim_deterministik(fixture_source):
    first, _ = wmn.build_document(fixture_source)
    second, _ = wmn.build_document(fixture_source)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_fixture_nsfw_kategorisi_korunur(fixture_source):
    result = wmn.convert(fixture_source)
    nsfw = [s for s in result.sites if s["category"] == "xx NSFW xx"]
    assert len(nsfw) == 1
    assert nsfw[0]["key"] == "fixture-nsfw"


def test_fixture_headers_allowlist(fixture_source):
    result = wmn.convert(fixture_source)
    with_headers = [s for s in result.sites if "headers" in s]
    assert len(with_headers) == 1
    # Literal beklenti: kod sabiti bosaltilsa test duser
    assert {n.lower() for n in with_headers[0]["headers"]} == {
        "accept",
        "accept-language",
    }


# ---------------------------------------------------------------------------
# Depodaki uretilmis dosya (her zaman kosar — /tmp'ye bagimli degil)
# ---------------------------------------------------------------------------


def test_uretilen_dosya_yapisi_ve_lisansi():
    """Commit'li wmn-sites.json: sozlesme + lisans zarfı (kaynak gerekmez)."""
    assert OUT_PATH.exists(), f"wmn-sites.json yok: {OUT_PATH}"
    data = json.loads(OUT_PATH.read_text(encoding="utf-8"))

    license_block = data["_license"]
    assert license_block["adapted"] is True
    assert license_block["uri"].startswith("https://creativecommons.org/licenses/by-sa/4.0")
    assert license_block["authors"], "authors bos olamaz"
    assert license_block["disclaimer"]
    assert license_block["material_url"].startswith("https://")
    assert license_block["source"].startswith("https://github.com/")

    assert wmn.validate_sites(data["sites"]) == []
    assert data["_generated"]["kept"] == len(data["sites"])
    assert len(data["sites"]) >= 300
    # Allowlist literali: veri dosyasinda baska baslik olmamali
    for site in data["sites"]:
        if "headers" in site:
            assert {n.lower() for n in site["headers"]} <= {"accept", "accept-language"}


# ---------------------------------------------------------------------------
# Gercek veri — opt-in (SHOWIS_WMN_REAL_DATA=1); env yoksa skip, dosya yoksa FAIL
# ---------------------------------------------------------------------------


def test_gercek_veri_kept_esigi():
    data = _real_source_or_fail()
    result = wmn.convert(data)

    assert result.kept >= 300
    assert result.sites_total == len(data["sites"])
    assert wmn.validate_sites(result.sites) == []
    # dislama sayaclari toplami toplam kayit sayisini asmamali
    assert result.kept + sum(result.excluded.values()) == result.sites_total


def test_gercek_veri_commit_li_dosyayla_ayni():
    """Depodaki dosya, gercek kaynaktan yeniden uretimle birebir ayni olmali."""
    data = _real_source_or_fail()
    shipped = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    expected, _ = wmn.build_document(data)
    assert shipped == expected


def test_gercek_veri_headers_allowlist():
    data = _real_source_or_fail()
    result = wmn.convert(data)

    with_headers = [s for s in result.sites if "headers" in s]
    assert result.headers_carried == len(with_headers)
    assert with_headers, "en az bir kayitta allowlist basligi beklenir"
    for site in with_headers:
        assert {n.lower() for n in site["headers"]} <= {"accept", "accept-language"}
    assert result.headers_dropped > 0


def test_gercek_veri_nsfw_korunur():
    data = _real_source_or_fail()
    result = wmn.convert(data)

    nsfw = [s for s in result.sites if s["category"] == "xx NSFW xx"]
    assert nsfw, "NSFW kayitlari cikarilmamali"
    nsfw_names = {
        (r.get("name") or "").strip()
        for r in data["sites"]
        if (r.get("cat") or "").strip() == "xx NSFW xx"
    }
    allowed = {"invalid", "post", "protected", "ambiguous"}
    for name, reason in result.dropped:
        if name in nsfw_names:
            assert reason in allowed or reason.startswith("duplicate:"), (
                f"{name}: beklenmeyen dislama nedeni {reason}"
            )

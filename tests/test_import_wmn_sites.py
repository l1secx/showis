"""A1 — WMN -> SiteSpec donusum testleri.

Ag yok: donusum testleri kucuk bir sahte WMN dokumani kullanir. Gercek veriyle
calisan test yalnizca yerel dosya varsa kosar, yoksa ``skip`` olur.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import import_wmn_sites as wmn  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCE = Path("/tmp/opencode/wmn-source.json")
OUT_PATH = (
    REPO_ROOT / "packages/connectors/showis_connectors/sitecheck/data/wmn-sites.json"
)


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
# Gercek veri (yerel dosya varsa)
# ---------------------------------------------------------------------------


def test_uretilen_dosya_tutarlidir():
    """Depodaki wmn-sites.json kaynaktan yeniden uretilebilir olmali."""
    if not OUT_PATH.exists():
        pytest.skip("wmn-sites.json uretilmemis")
    data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    assert data["_license"]["adapted"] is True
    assert wmn.validate_sites(data["sites"]) == []
    assert data["_generated"]["kept"] == len(data["sites"])
    assert len(data["sites"]) >= 300


def test_gercek_veri_kept_esigi():
    if not REAL_SOURCE.exists():
        pytest.skip(f"gercek WMN kaynagi yok: {REAL_SOURCE}")
    data = json.loads(REAL_SOURCE.read_text(encoding="utf-8"))
    result = wmn.convert(data)
    assert result.kept >= 300
    assert result.sites_total == len(data["sites"])
    assert wmn.validate_sites(result.sites) == []
    # dislama sayaclari toplami toplam kayit sayisini asmamali
    assert result.kept + sum(result.excluded.values()) == result.sites_total

"""htmlutil.meta_content `name=` desteği (P-013)."""

from __future__ import annotations

from showis_engine.htmlutil import meta_content, og_title


def test_meta_content_name_destegi():
    html = '<meta name="description" content="Aciklama metni">'
    assert meta_content(html, name="description") == "Aciklama metni"


def test_meta_content_attr_sirasi_tersten():
    html = '<meta content="x" name="description">'
    assert meta_content(html, name="description") == "x"


def test_meta_content_prop_oncelikli():
    html = (
        '<meta property="og:title" content="OG">'
        '<meta name="description" content="NORMAL">'
    )
    assert meta_content(html, prop="og:title", name="description") == "OG"
    assert og_title(html) == "OG"


def test_meta_content_eksik_none():
    assert meta_content("<html></html>", name="description") is None

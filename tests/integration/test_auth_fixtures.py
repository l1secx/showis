"""Ortak auth fixture'larinin sozlesmesi (T-115).

Neden bu dosya var?
    `tests/integration/conftest.py` iki sey vaat ediyor:
    1. Istemci sunucunun bekledigi token'i yollar -> auth **acikken de** yesil.
    2. `auth_bypass` yalnizca kullanan testte gecerlidir -> **surec geneli env
       sizintisi yok** (eski `os.environ.setdefault(..., "1")` hack'inin yerine).
    Bu vaatler burada dogrulanir; birisi bozulursa entegrasyon testleri sessizce
    auth'suz kosmaya baslamaz.
"""

from __future__ import annotations

import os

import pytest
from showis.core.security import auth_disabled

#: Testler kosmaya baslamadan onceki gercek ortam degeri (sizinti kontrolu icin).
_OUTER_AUTH_DISABLED = os.environ.get("SHOWIS_AUTH_DISABLED")


@pytest.mark.asyncio
async def test_token_basligi_ile_erisim_ve_basliksiz_red(client, auth_headers):
    """Token yolu: fixture'in gonderdigi basliklar gecerli, bos baslik gecersiz."""
    res = await client.get("/api/v1/cases")
    assert res.status_code == 200, res.text

    # Basliklari bosalt: bypass yoksa 401, bypass acik ortamda 200.
    blank = {"X-API-Token": "", "Authorization": ""}
    res = await client.get("/api/v1/cases", headers=blank)
    if auth_disabled():
        assert res.status_code == 200, res.text
    else:
        assert res.status_code == 401, res.text
        assert res.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_auth_bypass_fixture_yalniz_bu_testte_gecerli(auth_bypass, client):
    """`auth_bypass` verildiginde token'siz istek gecer; fixture testi kapsar."""
    assert auth_disabled() is True
    res = await client.get("/api/v1/cases", headers={"X-API-Token": "", "Authorization": ""})
    assert res.status_code == 200, res.text


def test_auth_bypass_ortami_sizdirmaz():
    """Onceki testin `auth_bypass`'i teardown'da geri alinmis olmali."""
    assert os.environ.get("SHOWIS_AUTH_DISABLED") == _OUTER_AUTH_DISABLED, (
        "SHOWIS_AUTH_DISABLED test sonrasinda sizmamali (monkeypatch ile geri alinmali)"
    )
    assert not auth_disabled() or _OUTER_AUTH_DISABLED is not None

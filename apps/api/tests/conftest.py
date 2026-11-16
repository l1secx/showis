"""API test yapilandirmasi.

Tum API testleri kimlik dogrulama bypass'i ile calisir
(`SHOWIS_AUTH_DISABLED=1`). Kimlik dogrulamayi **test eden** testler bu
degiskeni kendileri siler (bkz. `test_security_hardening.py::auth_enabled`).
"""

import pytest


@pytest.fixture(autouse=True)
def showis_api_auth_bypass(monkeypatch):
    monkeypatch.setenv("SHOWIS_AUTH_DISABLED", "1")
    # Testlerde gercek token kullanilmaz; deger sir degildir.
    monkeypatch.setenv("SHOWIS_API_TOKEN", "test-token-not-a-secret")
    yield
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)

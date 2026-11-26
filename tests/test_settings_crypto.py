"""showis_settings.crypto güvenlik davranışı (S-001 + mask)."""

from __future__ import annotations

import pytest
from showis_settings.crypto import (
    INSECURE_ENV,
    decrypt_secret,
    encrypt_secret,
    get_fernet,
    mask_secret,
)


def test_mask_kisa_sirlari_tamamen_gizler():
    assert mask_secret("") == ""
    assert mask_secret("secret") == "******"
    assert mask_secret("1234567") == "*******"
    assert mask_secret("12345678") == "********"
    assert mask_secret("123456789") == "1****9"
    assert mask_secret("12345678901") == "1****1"


def test_mask_uzun_sirde_orta_kismi_gizli():
    assert mask_secret("ghp_1234567890abcdef") == "ghp_****cdef"


def test_master_key_yoksa_fail_closed(monkeypatch):
    monkeypatch.delenv("MASTER_KEY", raising=False)
    monkeypatch.delenv(INSECURE_ENV, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        get_fernet()


def test_pytest_ortaminda_rastgele_anahtar_ve_gidis_donus(monkeypatch):
    monkeypatch.delenv("MASTER_KEY", raising=False)
    monkeypatch.delenv(INSECURE_ENV, raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    token = encrypt_secret("sk-test-secret")
    assert token and decrypt_secret(token) == "sk-test-secret"


def test_insecure_env_acik_opt_in(monkeypatch):
    monkeypatch.delenv("MASTER_KEY", raising=False)
    monkeypatch.setenv(INSECURE_ENV, "1")
    token = encrypt_secret("deger")
    assert decrypt_secret(token) == "deger"

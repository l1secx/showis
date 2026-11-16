"""T-332: log sızıntısı testleri — `api_key`/token'lar log'a düşmemeli.

Kanıt (bu gece): `httpx` INFO seviyesinde tam URL logluyordu →
`GET https://serpapi.com/search?...&api_key=<GERCEK>`. Bu testler merkezi
redaksiyonu, `redact_url`'i ve `httpx` logger seviyesini kilitler.
"""

from __future__ import annotations

import logging

import pytest
from showis.core.logging import (
    SecretRedactionFilter,
    install_redaction_filter,
    redact_secrets,
    redact_url,
)
from showis_engine.http import redact_url as engine_redact_url

SERPAPI_URL = (
    "https://serpapi.com/search?q=%22E2E%20Test%22&api_key=SUPERSECRETSERPAPIKEY123"
    "&num=20&engine=google"
)


def _record(msg: str, args: tuple = ()) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


# ---------------------------------------------------------------------------
# 1. Filtre: msg + args birlikte redakte edilir (SEC-11)
# ---------------------------------------------------------------------------


def test_filtre_msg_ve_args_birlikte_redakte_eder():
    record = _record("HTTP Request: GET %s", (SERPAPI_URL,))
    SecretRedactionFilter().filter(record)

    rendered = record.getMessage()
    assert "SUPERSECRETSERPAPIKEY123" not in rendered
    assert "api_key=****" in rendered
    # SEC-11: args temizlenir, handler sırları yeniden birleştiremez
    assert record.args == ()


def test_filtre_parcali_sirri_yakalar():
    """Sır format argümanında parçalıysa da (ör. 'ghp_%s') maskelenir."""
    record = _record("token: ghp_%s", ("abcdefghijklmnopqrstuvwxyz",))
    SecretRedactionFilter().filter(record)
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in record.getMessage()


def test_filtre_temiz_mesaji_degistirmez():
    record = _record("scan %s tamamlandi", ("scan-1",))
    SecretRedactionFilter().filter(record)
    # Sır yoksa %-format korunur (args silinmez)
    assert record.args == ("scan-1",)
    assert record.getMessage() == "scan scan-1 tamamlandi"


# ---------------------------------------------------------------------------
# 2. `redact_url` (engine + api) query parametrelerini maskeler
# ---------------------------------------------------------------------------


def test_redact_url_api_key_maskelenir():
    for redact in (redact_url, engine_redact_url):
        safe = redact(SERPAPI_URL)
        assert "SUPERSECRETSERPAPIKEY123" not in safe
        assert "api_key=****" in safe
        # Teşhis için host/yol ve diğer parametreler korunur
        assert "serpapi.com" in safe
        assert "num=20" in safe


def test_redact_url_google_cse_key_ve_cx_maskelenir():
    url = "https://www.googleapis.com/customsearch/v1?key=AIzaSECRETKEY1234567890&cx=4a0d1234567890abcdef1234567890ab&q=ada"
    safe = redact_url(url)
    assert "AIzaSECRETKEY1234567890" not in safe
    assert "4a0d1234567890abcdef1234567890ab" not in safe
    assert "key=****" in safe and "cx=****" in safe
    assert "q=ada" in safe


def test_redact_url_token_ve_bearer_maskelenir():
    assert "abc123" not in redact_url("https://api.example/v1?access_token=abc123")
    assert "abcdefghijklmnop" not in redact_secrets(
        "Authorization: Bearer abcdefghijklmnop"
    )


def test_redact_url_url_degilse_metin_redaksiyonuna_duser():
    assert "SUPERSECRETSERPAPIKEY123" not in redact_url("api_key=SUPERSECRETSERPAPIKEY123")
    assert redact_url("") == ""


# ---------------------------------------------------------------------------
# 3. Bilinen anahtar önekleri ve hex token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "xai-abcdefghijklmnopqrstuvwx",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    ],
)
def test_bilinen_anahtar_onekleri_maskelenir(secret):
    assert secret not in redact_secrets(f"istek atildi ({secret})")


def test_uzun_hex_token_maskelenir_ama_kanit_hasheri_korunur():
    hex_token = "4a0d1234567890abcdef1234567890ab"
    masked = redact_secrets(f"cse key={hex_token}")
    assert hex_token not in masked

    # Kanıt hash'i (content_hash) bağlamı maskelenmez
    sha = "a" * 64
    kept = redact_secrets(f"content_hash: {sha}")
    assert sha in kept


# ---------------------------------------------------------------------------
# 4. httpx logger seviyesi
# ---------------------------------------------------------------------------


def test_install_redaction_filter_httpx_loggerini_susturur():
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    install_redaction_filter()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_install_idempotent():
    install_redaction_filter()
    install_redaction_filter()
    root = logging.getLogger()
    filters = [f for f in root.filters if isinstance(f, SecretRedactionFilter)]
    assert len(filters) == 1


# ---------------------------------------------------------------------------
# 5. Uçtan uca: gerçek log akışında anahtar görünmüyor
# ---------------------------------------------------------------------------


def test_log_akisi_anahtar_sizdirmaz(caplog):
    install_redaction_filter()
    logger = logging.getLogger("showis.http.test")

    with caplog.at_level(logging.INFO):
        # httpx'in eski davranışını taklit et: tam URL INFO seviyesinde
        logger.info("HTTP Request: GET %s 200 OK", SERPAPI_URL)
        logger.warning("Hız limiti aşıldı (%s), 2 saniye bekleniyor...", SERPAPI_URL)

    text = caplog.text
    assert "SUPERSECRETSERPAPIKEY123" not in text
    assert "api_key=****" in text


# ---------------------------------------------------------------------------
# 6. Handler sonradan eklenirse de (Celery/uvicorn) maskelenir — record factory
# ---------------------------------------------------------------------------


def test_sonradan_eklenen_handler_de_maske_alir():
    import io

    install_redaction_filter()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)  # filtre YOK (Celery/uvicorn deseni)
    logger = logging.getLogger("showis.test.late_handler")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("HTTP Request: GET %s", SERPAPI_URL)
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "SUPERSECRETSERPAPIKEY123" not in output
    assert "api_key=****" in output


def test_worker_modulu_redaksiyonu_kurar():
    import showis.services.worker  # noqa: F401 - import yan etkisi test ediliyor

    assert logging.getLogger("httpx").level >= logging.WARNING
    record = logging.getLogger("showis.worker").makeRecord(
        "showis.worker", logging.INFO, __file__, 1, "url=%s", (SERPAPI_URL,), None
    )
    assert "SUPERSECRETSERPAPIKEY123" not in record.getMessage()

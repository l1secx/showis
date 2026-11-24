import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import DataError, DBAPIError

import showis.db.session as db_session_module
from showis.api.v1.ai_providers import router as ai_providers_router
from showis.api.v1.auth import router as auth_router
from showis.api.v1.breach import router as breach_router
from showis.api.v1.cases import router as cases_router
from showis.api.v1.findings import router as findings_router
from showis.api.v1.ops import public_router as ops_public_router
from showis.api.v1.ops import router as ops_router
from showis.api.v1.pool import router as pool_router
from showis.api.v1.reasoning import router as reasoning_router
from showis.api.v1.reports import router as reports_router
from showis.api.v1.research import router as research_router
from showis.api.v1.scans import router as scans_router
from showis.api.v1.settings import router as settings_router
from showis.api.v1.snapshots import router as snapshots_router
from showis.api.v1.supply_chain import router as supply_chain_router
from showis.core.logging import install_redaction_filter
from showis.core.security import (
    require_admin_access,
    require_token,
    require_write_access,
)
from showis.services.audit import (
    client_ip,
    client_ip_from_headers,
    record_throttled_event,
    user_agent,
    user_agent_from_headers,
)

logger = logging.getLogger("showis.main")

install_redaction_filter()

#: CORS: yalnizca bilinen arayuz origin'leri (SEC-04).
#: Virgulle ayrilmis liste `SHOWIS_ALLOWED_ORIGINS` ile override edilir.
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:8083",
    "http://127.0.0.1:8083",
    # CODE-01 notu: :8005 girdileri tarihsel (port T-111 ile publish edilmiyor).
    # test_security_hardening.py bunlara dayali CORS testleri icerdigi icin
    # simdilik tutuluyor; SHOWIS_ALLOWED_ORIGINS ile override edilebilir.
    "http://localhost:8005",
    "http://127.0.0.1:8005",
)

#: Acik API dokumantasyonu varsayilan olarak KAPALIDIR (SEC-01):
#: `/openapi.json` tum uclari, semalari ve alan adlarini ifsa eder.
#: Dev/test'te `SHOWIS_DOCS_ENABLED=1` ile acilir — acikken de auth arkasindadir.
DOCS_ENABLED_ENV = "SHOWIS_DOCS_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def allowed_origins() -> list[str]:
    raw = os.getenv("SHOWIS_ALLOWED_ORIGINS")
    if raw is None:
        return list(DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def docs_enabled() -> bool:
    """`SHOWIS_DOCS_ENABLED` (varsayilan kapali) — her istekte okunur."""
    return (os.getenv(DOCS_ENABLED_ENV) or "").strip().lower() in _TRUTHY


#: Documentasyon yollari (kapaliyken 404, acikken auth arkasinda)
_DOCS_DISABLED_DETAIL = "API dokümantasyonu kapalı (SHOWIS_DOCS_ENABLED)"


def _require_docs() -> None:
    if not docs_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_DOCS_DISABLED_DETAIL
        )


app = FastAPI(
    title="Showis API",
    description="Showis OSINT Engine & Management API",
    version="0.1.0",
    # Dokumantasyon rotalari elle (auth'lu) tanimlanir
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    # Token header ile tasinir (cerez yok) -> credentials kapali (CSRF yuzeyi yok).
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Token", "Authorization", "Content-Type"],
)


#: D-04: govde boyutu ust siniri (bayt). `SHOWIS_MAX_BODY_BYTES` ile degistirilir.
DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_BODY_ENV = "SHOWIS_MAX_BODY_BYTES"
#: 413 — Starlette sürümüne göre isim değiştiği için sabit literal kullanılıyor
_HTTP_413 = 413
_BODY_TOO_LARGE_DETAIL = b'{"detail":"\\u0130stek g\\u00f6vdesi \\u00e7ok b\\u00fcy\\u00fck"}'


def max_body_bytes() -> int:
    """Istek govdesi siniri (her istekte env'den okunur — test/dev ayari)."""
    raw = os.getenv(_MAX_BODY_ENV)
    if not raw:
        return DEFAULT_MAX_BODY_BYTES
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan kullanilir", _MAX_BODY_ENV, raw)
        return DEFAULT_MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    """D-04: asiri buyuk istek govdelerini **413** ile reddeder.

    Iki katman:
    1. `Content-Length` basligi varsa istek govdesi okunmadan reddedilir
       (bellek/CPU harcanmaz),
    2. `Content-Length` yoksa (chunked) `receive` sarmalanir ve okunan bayt
       sayilir; sinir asildigi anda akis kesilir.

    Hata govdesi sabittir ve ic bilgi (limit degeri, ic yol) sizdirmaz.
    Reddetme olayi `request.rejected` olarak audit'e yazilir — govde **içeriği**
    asla kaydedilmez, yalniz `content_length`/`status_code`.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = max_body_bytes()
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
        header_map = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in raw_headers
        }

        declared: int | None = None
        raw_length = header_map.get("content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length.strip())
            except ValueError:
                declared = None

        # 1) Content-Length bildirilmisse govde okunmadan karar verilir
        if declared is not None:
            if declared > limit:
                await self._reject(scope, send, declared)
                return
            await self.app(scope, receive, send)
            return

        # 2) Govde beklenmeyen metotlar (GET/HEAD/OPTIONS) hic sarilmaz:
        #    SSE gibi uzun omurlu akislar `receive` semantigine dokunulmadan gecer.
        if str(scope.get("method", "GET")).upper() in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        # 3) Content-Length yok (chunked): govde sinirli olarak tamponlanir,
        #    sinir asilirsa akis kesilip 413 doner. Bellek en fazla `limit`+parca.
        body = b""
        while True:
            message: dict[str, Any] = await receive()
            if message.get("type") == "http.disconnect":  # istemci koptu
                return
            body += message.get("body", b"")
            if len(body) > limit:
                await self._reject(scope, send, len(body))
                return
            if not message.get("more_body"):
                break

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Govde bitti; sonraki okumalar orijinal akisa devredilir
            message: dict[str, Any] = await receive()
            return message

        await self.app(scope, replay_receive, send)

    async def _reject(self, scope: dict[str, Any], send: Any, observed: int) -> None:
        """413 dondurur ve reddi audit'e yazar (govde icerigi yazilmaz)."""
        logger.warning(
            "Istek reddedildi (413): path=%s observed_bytes=%d",
            scope.get("path"),
            observed,
        )
        await send(
            {
                "type": "http.response.start",
                "status": _HTTP_413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_BODY_TOO_LARGE_DETAIL)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _BODY_TOO_LARGE_DETAIL})

        header_map = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in (scope.get("headers") or [])
        }
        client = scope.get("client") or (None, None)
        try:
            async with db_session_module.async_session_factory() as session:
                # T-343: 413 seli de hiz sinirli (peer IP anahtarda → XFF atlatamaz)
                await record_throttled_event(
                    db=session,
                    action="request.rejected",
                    peer_ip=client[0],
                    claimed_ip=client_ip_from_headers(header_map, client[0]),
                    status_code=_HTTP_413,
                    method=str(scope.get("method", "?")),
                    path=str(scope.get("path", "?")),
                    user_agent_header=user_agent_from_headers(header_map),
                    extra_details={"observed_bytes": observed},
                )
        except Exception:  # noqa: BLE001 - audit yazimi reddi engellemez
            logger.warning("request.rejected audit yazilamadi", exc_info=True)


app.add_middleware(BodySizeLimitMiddleware)

#: Tum API yuzeyi tek noktadan korunur; `/health` bilincli olarak disarida.
auth_required = [Depends(require_token)]
#: Yazma işlemleri: viewer engeli + analyst için vaka sahipliği.
write_required = [Depends(require_write_access)]
#: Dokumantasyon: once "kapali mi" (404), sonra token (401).
docs_guarded = [Depends(_require_docs), Depends(require_token)]

# ---------------------------------------------------------------------------
# T-376: DB katmanından gelen "girdi kaynaklı" hatalar 500/502 yerine 422
# ---------------------------------------------------------------------------

#: SQLSTATE class 22 = data exception (geçersiz metin kodlaması, NUL baytı, aralık vb.)
_DATA_ERROR_SQLSTATES = ("22",)


@app.exception_handler(DBAPIError)
async def dbapi_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    """`DBAPIError` → girdi hatasıysa **422**, gerçek arıza ise 500.

    Örnek: `q=a%00b` asyncpg'de `invalid byte sequence for encoding "UTF8": 0x00`
    üretiyordu → 500 (ve keep-alive bağlantısında 502). Kullanıcı hatası sınıfı
    (SQLSTATE 22) artık sızsız `422 {"detail": "Geçersiz girdi"}` döner.
    Bağlantı havuzu/DB arızası (class 08/57 vb.) 422'ye **çevrilmez**; 500 + log.

    Log: traceback yok, yalnız sınıf adı/SQLSTATE — sır/PII sızmaz.
    """
    sqlstate = str(getattr(getattr(exc, "orig", None), "sqlstate", "") or "")
    is_input_error = isinstance(exc, DataError) or sqlstate.startswith(_DATA_ERROR_SQLSTATES)
    if is_input_error:
        logger.warning(
            "Gecersiz girdi (DB): %s %s sqlstate=%s",
            request.method,
            request.url.path,
            sqlstate or "n/a",
        )
        return JSONResponse({"detail": "Geçersiz girdi"}, status_code=422)

    logger.error(
        "Veritabani hatasi: %s %s (%s)",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse({"detail": "Sunucu hatası"}, status_code=500)

#: Kimlik dogrulama basarisizliginda audit yazilan yollar (healthcheck haric)
_AUTH_AUDITED_STATUSES = {status.HTTP_401_UNAUTHORIZED, status.HTTP_503_SERVICE_UNAVAILABLE}


@app.middleware("http")
async def audit_auth_failures(request: Request, call_next):
    """SEC-13: 401/503 ureten istekleri `auth.failed` olarak audit'e yazar.

    **Sira (T-343):** `call_next()` once token dogrulamasini calistirir, audit
    ancak cevap uretildikten sonra yazilir — yani `auth → audit`. Token degeri
    **asla** loglanmaz; yalniz yol, yontem, IP ve durum kodu.

    **Hiz siniri (T-340 #4):** ayni `(peer|claimed, action, status)` icin
    dakikada `SHOWIS_AUTH_AUDIT_PER_MINUTE` (varsayilan 5) kayit; 503 icin 1.
    Fazlasi bastirilir, pencere donunce tek "suppressed: N" ozeti yazilir —
    boylece 401 seli `audit_logs`'u ve baglanti havuzunu tuketemez.
    """
    response = await call_next(request)
    if (
        not getattr(request.state, "auth_audited", False)
        and response.status_code in _AUTH_AUDITED_STATUSES
        and request.url.path not in ("/health", "/healthz")
    ):
        client = request.client
        try:
            async with db_session_module.async_session_factory() as session:
                await record_throttled_event(
                    db=session,
                    action="auth.failed",
                    peer_ip=client.host if client else None,
                    claimed_ip=client_ip(request),
                    status_code=response.status_code,
                    method=request.method,
                    path=request.url.path,
                    user_agent_header=user_agent(request),
                )
        except Exception:  # noqa: BLE001 - audit yazimi istegi engellemez
            logger.warning("auth.failed audit yazilamadi", exc_info=True)
    return response

app.include_router(auth_router, prefix="/api/v1")
app.include_router(
    cases_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    scans_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    settings_router,
    prefix="/api/v1",
    dependencies=[*auth_required, Depends(require_admin_access)],
)
app.include_router(
    ai_providers_router,
    prefix="/api/v1",
    dependencies=[*auth_required],
)
app.include_router(
    pool_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    breach_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    reasoning_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    research_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    snapshots_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    supply_chain_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    findings_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
app.include_router(
    reports_router, prefix="/api/v1", dependencies=[*auth_required, *write_required]
)
# T-329: gozlemlenebilirlik (/metrics, /healthz) — token korumali.
app.include_router(ops_router, prefix="/api/v1", dependencies=auth_required)
# T-330/F-04: derin saglik ucu kimliksiz (yalniz ok|degraded + check adlari).
app.include_router(ops_public_router)


# ---------------------------------------------------------------------------
# Dokumantasyon (varsayilan kapali; acikken de token ister)
# ---------------------------------------------------------------------------


@app.get("/openapi.json", include_in_schema=False, dependencies=docs_guarded)
async def openapi_schema() -> JSONResponse:
    _require_docs()
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=docs_guarded)
async def swagger_docs() -> HTMLResponse:
    _require_docs()
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Showis API")


@app.get("/redoc", include_in_schema=False, dependencies=docs_guarded)
async def redoc_docs() -> HTMLResponse:
    _require_docs()
    return get_redoc_html(openapi_url="/openapi.json", title="Showis API")


@app.get("/health")
async def health_check():
    """Container healthcheck: auth'tan MUAF, token tanimsizken de calisir."""
    return {
        "status": "ok",
        "service": "showis-api",
        "version": "0.1.0",
    }

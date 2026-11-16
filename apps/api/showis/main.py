import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

import showis.db.session as db_session_module
from showis.api.v1.cases import router as cases_router
from showis.api.v1.findings import router as findings_router
from showis.api.v1.reports import router as reports_router
from showis.api.v1.scans import router as scans_router
from showis.api.v1.settings import router as settings_router
from showis.core.logging import install_redaction_filter
from showis.core.security import require_token
from showis.services.audit import client_ip, record_audit, user_agent

logger = logging.getLogger("showis.main")

install_redaction_filter()

#: CORS: yalnizca bilinen arayuz origin'leri (SEC-04).
#: Virgulle ayrilmis liste `SHOWIS_ALLOWED_ORIGINS` ile override edilir.
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:8083",
    "http://127.0.0.1:8083",
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

#: Tum API yuzeyi tek noktadan korunur; `/health` bilincli olarak disarida.
auth_required = [Depends(require_token)]
#: Dokumantasyon: once "kapali mi" (404), sonra token (401).
docs_guarded = [Depends(_require_docs), Depends(require_token)]

#: Kimlik dogrulama basarisizliginda audit yazilan yollar (healthcheck haric)
_AUTH_AUDITED_STATUSES = {status.HTTP_401_UNAUTHORIZED, status.HTTP_503_SERVICE_UNAVAILABLE}


@app.middleware("http")
async def audit_auth_failures(request: Request, call_next):
    """SEC-13: 401/503 ureten istekleri `auth.failed` olarak audit'e yazar.

    `core/security.py` degismez; audit burada, cevap uretildikten sonra
    best-effort yazilir (DB hatasi istegi bozmaz). Token degeri **asla**
    loglanmaz — yalniz yol, yontem, IP ve durum kodu.
    """
    response = await call_next(request)
    if response.status_code in _AUTH_AUDITED_STATUSES and request.url.path not in (
        "/health",
    ):
        try:
            async with db_session_module.async_session_factory() as session:
                await record_audit(
                    db=session,
                    action="auth.failed",
                    case_id=None,
                    target=f"{request.method} {request.url.path}"[:200],
                    details={"status_code": response.status_code},
                    actor_ip=client_ip(request),
                    user_agent_header=user_agent(request),
                )
        except Exception:  # noqa: BLE001 - audit yazimi istegi engellemez
            logger.warning("auth.failed audit yazilamadi", exc_info=True)
    return response

app.include_router(cases_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(scans_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(settings_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(findings_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(reports_router, prefix="/api/v1", dependencies=auth_required)


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

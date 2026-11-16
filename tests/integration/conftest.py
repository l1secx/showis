"""`tests/integration` icin ortak fixture'lar (T-115).

Neden var?
    Entegrasyon testleri ayni kurulumu (sys.path, MASTER_KEY, sqlite engine,
    `get_db` override, auth token) tekrar tekrar yaziyordu. `test_api_cases.py`
    ise auth'u **modul import edilirken** `os.environ.setdefault(...)` ile
    kapatarak butun pytest surecini etkiliyordu (testler arasi gizli bagimlilik).
    Bu dosya kurulumu tek yere tasir; ortam degiskenleri `monkeypatch` ile
    **test basina** ayarlanir ve test bitince otomatik geri alinir.

Auth sozlesmesi (T-108):
    Sunucu token'i `SHOWIS_API_TOKEN`'dan okur, istemci ayni token'i iki destekli
    baslikla (`X-API-Token`, `Authorization: Bearer`) yollar. Bu yuzden testler
    auth **acikken de kapaliyken de** yesil kalir:
        * `SHOWIS_AUTH_DISABLED` disaridan set edilmisse -> bypass devrede,
        * set edilmemisse -> token eslesir.
    Auth'u gercekten test eden dosyalar `auth_bypass` fixture'ini **kullanmaz**
    (ör. `test_security_hardening.py`); kullanmayan entegrasyon testleri ise
    gercek token yolunu da dolayli olarak dogrular.

DB secimi:
    Bellek-ici sqlite her baglantiya ayri DB verir; iptal senaryosu tarama
    surerken **ikinci oturumdan** yazdigi icin dosya tabanli sqlite (WAL +
    `busy_timeout`) kullanilir.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

ROOT = Path(__file__).resolve().parents[2]
for _path in (
    ROOT / "apps/api",
    ROOT / "packages/contracts",
    ROOT / "packages/settings",
    ROOT / "packages/llm",
    ROOT / "packages/engine",
    ROOT / "packages/connectors",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# Uygulama import edilmeden once gerekli (sir cozme / rapor dizini).
os.environ.setdefault("MASTER_KEY", "test-master-key-for-pytest-only")
os.environ.setdefault("SHOWIS_REPORT_DIR", "/tmp/showis-test-reports")

import showis.db.session as db_session_module  # noqa: E402
from showis.db.base import Base  # noqa: E402
from showis.db.session import get_db  # noqa: E402
from showis.main import app  # noqa: E402

#: Testlerin kullandigi token — sir degil, yalnizca test degeri.
TEST_API_TOKEN = "test-token-integration-not-a-secret"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Sunucunun kabul ettigi iki basligi birlikte dondurur."""
    return {
        "X-API-Token": TEST_API_TOKEN,
        "Authorization": f"Bearer {TEST_API_TOKEN}",
    }


@pytest.fixture
def auth_bypass(monkeypatch):
    """Auth'u yalnizca bu test icin kapatir (eski global env hack'inin yerine).

    Kullanmak isteyen dosya `pytestmark = pytest.mark.usefixtures("auth_bypass")`
    yazar. Token zaten gonderildigi icin bu fixture **zorunlu degildir**;
    yalnizca token yolunu test etmek istemeyen dosyalar icin kestirmedir.
    """
    monkeypatch.setenv("SHOWIS_AUTH_DISABLED", "1")


# ---------------------------------------------------------------------------
# Ortam (test basina, otomatik geri alinir)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def integration_env(monkeypatch, tmp_path) -> dict[str, str]:
    """Tum entegrasyon testleri icin guvenli, izole ortam."""
    monkeypatch.setenv("SHOWIS_API_TOKEN", TEST_API_TOKEN)
    # Tarama senkron kosmali: Celery broker'i testte yok.
    monkeypatch.setenv("USE_CELERY", "0")
    # Paralel yurutme sozlesmesi de kapsansin (T-101).
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "3")
    monkeypatch.setenv("SHOWIS_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SHOWIS_EVIDENCE_DIR", str(tmp_path / "evidence"))
    return {"token": TEST_API_TOKEN}


# ---------------------------------------------------------------------------
# Veritabani
# ---------------------------------------------------------------------------


def _enable_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Dosya tabanli sqlite: es zamanli yazim icin WAL + busy_timeout."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


@pytest_asyncio.fixture
async def db_engine(tmp_path) -> AsyncEngine:
    """Test basina izole, dosya tabanli sqlite engine (sema hazir)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'integration.db'}",
        connect_args={"timeout": 30},
    )
    _enable_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Uygulamanin kullandigi oturum fabrikasinin test karsiligi."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# API istemcisi
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db_factory: async_sessionmaker[AsyncSession],
    auth_headers: dict[str, str],
    monkeypatch,
) -> AsyncClient:
    """Tum istekleri test DB'sine giden, auth basliklarini tasiyan istemci."""
    async def override_get_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.test_session_factory = db_factory
    # Arka plan taramasi kendi oturumunu process-global fabrikadan acar.
    monkeypatch.setattr(db_session_module, "async_session_factory", db_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()

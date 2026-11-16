"""T-320 (D-04): istek gövdesi boyut sınırı (413)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import AuditLog
from showis.db.session import get_db
from showis.main import app, max_body_bytes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MB = 1024 * 1024


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_varsayilan_limit_8mb(monkeypatch):
    monkeypatch.delenv("SHOWIS_MAX_BODY_BYTES", raising=False)
    assert max_body_bytes() == 8 * MB


def test_limit_env_ile_ayarlanir(monkeypatch):
    monkeypatch.setenv("SHOWIS_MAX_BODY_BYTES", "1234")
    assert max_body_bytes() == 1234
    monkeypatch.setenv("SHOWIS_MAX_BODY_BYTES", "bozuk")
    assert max_body_bytes() == 8 * MB


async def test_9mb_govde_413(client, db_factory):
    payload = b"x" * (9 * MB)
    res = await client.post(
        "/api/v1/cases",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    assert res.headers["content-type"].startswith("application/json")
    body = res.json()
    assert "detail" in body
    # Hata gövdesi iç bilgi sızdırmaz (limit, iç yol, token yok)
    text = str(body)
    assert "SHOWIS" not in text
    assert "/app" not in text
    assert str(9 * MB) not in text


async def test_7mb_govde_limiti_gormez(client, db_factory, monkeypatch):
    """Limitin altındaki gövde normal işlenir (auth/doğrulama hatası döner)."""
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SHOWIS_API_TOKEN", "dogru-token")
    payload = b'{"name":"' + b"x" * (7 * MB) + b'"}'
    res = await client.post(
        "/api/v1/cases",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 401, res.text  # token yok -> normal hata, 413 değil


async def test_chunked_govdede_sayac_ile_kesilir(client, db_factory, monkeypatch):
    """Content-Length yoksa (chunked) akan gövde sayılır ve sınırda kesilir."""
    monkeypatch.setenv("SHOWIS_MAX_BODY_BYTES", str(1 * MB))

    async def _chunks():
        for _ in range(4):
            yield b"y" * (512 * 1024)

    res = await client.post(
        "/api/v1/cases",
        content=_chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    assert res.json()["detail"]


async def test_413_audit_yazilir_ama_govde_icerigi_sizmaz(client, db_factory, monkeypatch):
    import showis.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "async_session_factory", db_factory)
    marker = "GIZLI-GOVDE-VERISI"
    payload = marker.encode() + b"x" * (9 * MB)

    res = await client.post(
        "/api/v1/cases",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Real-IP": "198.51.100.9",
        },
    )
    assert res.status_code == 413

    async with db_factory() as session:
        entry = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "request.rejected")
            )
        ).scalars().first()
    assert entry is not None
    assert entry.details["status_code"] == 413
    assert entry.details["observed_bytes"] == len(payload)
    assert entry.details["actor_ip"] == "198.51.100.9"
    # Gövde içeriği audit'e yazılmaz
    assert marker not in str(entry.details)
    assert marker not in str(entry.target)


async def test_limit_alti_get_etkilenmez(client, db_factory):
    res = await client.get("/health")
    assert res.status_code == 200

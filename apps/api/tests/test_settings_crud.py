import pytest
import respx
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.session import get_db
from showis.main import app
from showis_settings.crypto import decrypt_secret, encrypt_secret, mask_secret
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    await engine.dispose()


def test_crypto_masking_and_encryption():
    raw_secret = "ghp_1234567890abcdef"
    masked = mask_secret(raw_secret)
    assert masked == "ghp_****cdef"

    short = "secret"
    assert mask_secret(short) == "******"

    encrypted = encrypt_secret(raw_secret)
    assert encrypted != raw_secret.encode()
    decrypted = decrypt_secret(encrypted)
    assert decrypted == raw_secret


@pytest.mark.asyncio
async def test_settings_crud_endpoints(test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a provider
        create_res = await client.post(
            "/api/v1/settings/providers",
            json={
                "provider": "github",
                "label": "My GitHub",
                "config": {"note": "test"},
                "secret": "ghp_secrettoken12345",
            },
        )
        assert create_res.status_code == 200
        created = create_res.json()
        assert created["provider"] == "github"
        assert created["secret_masked"] == "ghp_****2345"
        setting_id = created["id"]

        # 2. List providers (key is masked, never exposed raw)
        list_res = await client.get("/api/v1/settings/providers")
        assert list_res.status_code == 200
        items = list_res.json()
        assert len(items) == 1
        assert items[0]["secret_masked"] == "ghp_****2345"

        # 3. Update provider without passing secret (keeps existing secret)
        update_res = await client.post(
            "/api/v1/settings/providers",
            json={
                "provider": "github",
                "label": "Updated GitHub Label",
                "config": {"updated": True},
            },
        )
        assert update_res.status_code == 200
        updated = update_res.json()
        assert updated["label"] == "Updated GitHub Label"
        assert updated["secret_masked"] == "ghp_****2345"

        # 4. Test provider connection with respx mock
        with respx.mock:
            respx.get("https://api.github.com/user").respond(200, json={"login": "octocat"})
            test_resp = await client.post(f"/api/v1/settings/providers/{setting_id}/test")
            assert test_resp.status_code == 200
            assert test_resp.json()["ok"] is True
            assert test_resp.json()["user"] == "octocat"

        # 5. Test provider failure case
        with respx.mock:
            respx.get("https://api.github.com/user").respond(
                401, json={"message": "Bad credentials"}
            )
            test_resp_fail = await client.post(f"/api/v1/settings/providers/{setting_id}/test")
            assert test_resp_fail.status_code == 200
            assert test_resp_fail.json()["ok"] is False

        # 6. Delete provider
        del_res = await client.delete(f"/api/v1/settings/providers/{setting_id}")
        assert del_res.status_code == 204

        # List should now be empty
        list_after = await client.get("/api/v1/settings/providers")
        assert len(list_after.json()) == 0

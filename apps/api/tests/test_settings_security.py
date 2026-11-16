"""SEC-02 / SEC-03 / SEC-08 — BYOK sır sızması ve SSRF regresyon testleri.

Senaryo kaynağı: `.orchestrator/outbox/appsec-engineer.md` (T-104).
"""

import pytest
import respx
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import Setting
from showis.db.session import get_db
from showis.main import app
from showis_settings.crypto import encrypt_secret, mask_secret
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def client(session_factory, monkeypatch):
    # T-108 (SEC-01): API artik token ister; testler gercek akisi kullanir.
    monkeypatch.setenv("SHOWIS_API_TOKEN", "test-token")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Token": "test-token"},
    ) as http_client:
        yield http_client


async def _insert_llm_setting(session_factory, base_url: str, secret: str) -> str:
    async with session_factory() as session:
        item = Setting(
            provider="llm",
            label="LLM",
            config={"base_url": base_url, "model": "gpt-4o-mini"},
            secret_encrypted=encrypt_secret(secret),
            secret_masked=mask_secret(secret),
            enabled=True,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item.id


@pytest.mark.asyncio
async def test_base_url_allowlist_disarida_birakir(client, monkeypatch):
    monkeypatch.delenv("SHOWIS_ALLOW_INSECURE_BASE_URL", raising=False)
    disallowed = [
        "https://evil.example/v1",
        "https://attacker.tld",
        "http://127.0.0.1:9/v1",  # http + dev istisnası kapalı
        "http://api.openai.com/v1",  # https zorunlu
        "https://api.openai.com/v1?token=x",  # query yasak
        "https://user:pass@api.openai.com/v1",  # userinfo yasak
        "ftp://api.openai.com/v1",
    ]
    for url in disallowed:
        res = await client.post(
            "/api/v1/settings/providers",
            json={"provider": "llm", "config": {"base_url": url, "model": "x"}},
        )
        assert res.status_code == 422, url

    ok = await client.post(
        "/api/v1/settings/providers",
        json={"provider": "llm", "config": {"base_url": "https://api.openai.com/v1"}},
    )
    assert ok.status_code == 200
    assert ok.json()["config"]["base_url"] == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_llm_config_bilinmeyen_alan_reddedilir(client):
    res = await client.post(
        "/api/v1/settings/providers",
        json={
            "provider": "llm",
            "config": {"base_url": "https://api.openai.com/v1", "injected": "x"},
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_base_url_degisince_sir_gecersiz_kilinir(client):
    create = await client.post(
        "/api/v1/settings/providers",
        json={
            "provider": "llm",
            "config": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
            "secret": "sk-test-secret-value-123456",
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["secret_required"] is False
    assert created["secret_masked"]

    # base_url değişiyor, sır gönderilmiyor -> sır silinir (SEC-02)
    update = await client.post(
        "/api/v1/settings/providers",
        json={
            "provider": "llm",
            "config": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.1-8b"},
        },
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["secret_required"] is True
    assert updated["secret_masked"] is None
    assert updated["config"]["base_url"] == "https://api.groq.com/openai/v1"

    # sır gönderilirse yeniden kurulur
    again = await client.post(
        "/api/v1/settings/providers",
        json={
            "provider": "llm",
            "config": {"base_url": "https://api.groq.com/openai/v1"},
            "secret": "gsk_new-secret-value-abcdef",
        },
    )
    assert again.json()["secret_required"] is False
    assert again.json()["secret_masked"]


@pytest.mark.asyncio
async def test_sir_allowlist_disi_hosta_gonderilmez(client, session_factory):
    """DB'ye doğrudan yazılmış (eski/elle) kötü base_url sır sızdırmamalı."""
    setting_id = await _insert_llm_setting(
        session_factory, "https://evil.example/v1", "sk-leaked-key-1234567890"
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://evil.example/v1/chat/completions").respond(
            200, json={"choices": []}
        )
        res = await client.post(f"/api/v1/settings/providers/{setting_id}/test")

    assert res.status_code == 200
    assert res.json() == {"ok": False, "error": "blocked by policy"}
    assert route.call_count == 0, "politika dışı hosta hiç istek gitmemeli"


@pytest.mark.asyncio
async def test_ssrf_ic_servis_engellenir(client, session_factory, monkeypatch):
    """Allowlist'i testhost'a genişletsek bile SSRF guard iç adresi keser."""
    monkeypatch.setenv("SHOWIS_LLM_BASE_URL_ALLOWLIST", "showis-postgres")
    setting_id = await _insert_llm_setting(
        session_factory, "http://showis-postgres:5432", "sk-ssrf-key-1234567890"
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.post("http://showis-postgres:5432/chat/completions").respond(200)
        res = await client.post(f"/api/v1/settings/providers/{setting_id}/test")

    assert res.json() == {"ok": False, "error": "blocked by policy"}
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_izinli_hosta_istek_gider_ve_token_headerda(client, session_factory):
    setting_id = await _insert_llm_setting(
        session_factory, "https://api.openai.com/v1", "sk-allowed-key-1234567890"
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://api.openai.com/v1/chat/completions").respond(
            200, json={"choices": []}
        )
        res = await client.post(f"/api/v1/settings/providers/{setting_id}/test")

    assert res.json()["ok"] is True
    assert route.call_count == 1
    sent = route.calls[0].request.headers.get("authorization", "")
    assert sent.startswith("Bearer sk-")


@pytest.mark.asyncio
async def test_hata_metni_saglayici_govdesini_sizdirmaz(client):
    create = await client.post(
        "/api/v1/settings/providers",
        json={"provider": "github", "secret": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"},
    )
    setting_id = create.json()["id"]

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/user").respond(
            401,
            text="Bad credentials for ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
            "at https://api.github.com/user?token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )
        res = await client.post(f"/api/v1/settings/providers/{setting_id}/test")

    body = res.json()
    assert body["ok"] is False
    assert "ghp_" not in str(body)
    assert body["error"] == "GitHub kimlik doğrulaması başarısız (HTTP 401)"


@pytest.mark.asyncio
async def test_ayar_yazimi_audit_ve_sirsiz(client, session_factory):
    await client.post(
        "/api/v1/settings/providers",
        json={"provider": "brave", "secret": "brave-secret-value-123456"},
    )
    async with session_factory() as session:
        from showis.db.models import AuditLog
        from sqlalchemy import select

        logs = (await session.execute(select(AuditLog))).scalars().all()
    actions = [log.action for log in logs]
    assert "settings.provider.update" in actions
    for log in logs:
        assert "brave-secret-value-123456" not in str(log.details)

    update_log = next(log for log in logs if log.action == "settings.provider.update")
    assert update_log.details["provider"] == "brave"
    assert update_log.details["secret_rotated"] is True
    assert "created" in update_log.details["changed_fields"]


@pytest.mark.asyncio
async def test_base_url_degisimi_auditte_alan_adiyla_gorunur(client, session_factory):
    await client.post(
        "/api/v1/settings/providers",
        json={
            "provider": "llm",
            "config": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
            "secret": "sk-rotation-test-1234567890",
        },
    )
    await client.post(
        "/api/v1/settings/providers",
        json={
            "provider": "llm",
            "config": {"base_url": "https://api.groq.com/openai/v1", "model": "llama"},
            "secret": "gsk-rotation-test-1234567890",
        },
    )

    async with session_factory() as session:
        from showis.db.models import AuditLog
        from sqlalchemy import select

        logs = (await session.execute(select(AuditLog))).scalars().all()

    updates = [log for log in logs if log.action == "settings.provider.update"]
    assert len(updates) == 2
    last = updates[-1].details
    assert last["provider"] == "llm"
    assert set(last["changed_fields"]) >= {"base_url", "model"}
    assert last["secret_rotated"] is True
    # sır değeri audit'e yazılmaz
    assert "gsk-rotation-test" not in str(last)
    assert "sk-rotation-test" not in str(logs)


def test_llm_client_allowlist_disi_base_url_reddeder():
    from showis_llm import LLMClient
    from showis_llm.base_url import BaseUrlError

    with pytest.raises(BaseUrlError):
        LLMClient(
            base_url="https://evil.example/v1",
            api_key="sk-x",
            model="m",
            session_id="s",
        )


@pytest.mark.asyncio
async def test_llm_client_cagri_aninda_yeniden_dogrular(monkeypatch):
    """Kurulumda izinli olan host, sonradan allowlist'ten çıkarılırsa istek gitmez."""
    from showis_llm import LLMClient, LLMClientError

    monkeypatch.setenv("SHOWIS_LLM_BASE_URL_ALLOWLIST", "allowed.example")
    client = LLMClient(
        base_url="https://allowed.example/v1",
        api_key="sk-secret",
        model="m",
        session_id="s",
    )
    monkeypatch.delenv("SHOWIS_LLM_BASE_URL_ALLOWLIST")

    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://allowed.example/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
        with pytest.raises(LLMClientError):
            await client.complete([{"role": "user", "content": "ping"}])
        assert route.call_count == 0, "politika disi base_url'e istek gitmemeli"


@pytest.mark.asyncio
async def test_llm_client_hata_metni_govde_sizdirmaz(monkeypatch):
    """401 yanıtı gövdesi/URL query'si LLMClientError içinde görünmemeli."""
    from showis_llm import LLMClient, LLMClientError

    client = LLMClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-secret-value-1234567890",
        model="m",
        session_id="s",
    )
    with respx.mock(assert_all_called=False) as router:
        router.post("https://api.openai.com/v1/chat/completions").respond(
            401, text="invalid key sk-secret-value-1234567890 for org xyz"
        )
        with pytest.raises(LLMClientError) as excinfo:
            await client.complete([{"role": "user", "content": "ping"}])

    message = str(excinfo.value)
    assert "sk-secret-value" not in message
    assert "org xyz" not in message
    assert "401" in message


@pytest.mark.asyncio
async def test_arama_saglayici_hatasi_anahtar_sizdirmaz():
    """SEC-08: SerpAPI/CSE anahtarı URL'de; hata metni anahtarsız olmalı."""
    import httpx
    from showis.services.search_guard import SearchProviderError, SearchProviderSanitizer

    class _Failing:
        name = "serpapi"

        async def search(self, query, *, limit=20, site=None):
            request = httpx.Request(
                "GET",
                "https://serpapi.com/search?q=x&api_key=TOP-SECRET-KEY&num=20",
            )
            raise httpx.HTTPStatusError(
                f"Client error '401 Unauthorized' for url '{request.url}'",
                request=request,
                response=httpx.Response(401, request=request),
            )

    sanitizer = SearchProviderSanitizer(_Failing())  # type: ignore[arg-type]
    with pytest.raises(SearchProviderError) as excinfo:
        await sanitizer.search("kerem")

    message = str(excinfo.value)
    assert "TOP-SECRET-KEY" not in message
    assert "serpapi.com" not in message
    assert "401" in message


def test_factory_arama_saglayicisini_sarmalar():
    from showis.services.connectors_factory import _pick_search_provider
    from showis.services.search_guard import SearchProviderSanitizer

    provider = _pick_search_provider({})
    assert isinstance(provider, SearchProviderSanitizer)
    assert "TOP-SECRET" not in repr(provider)

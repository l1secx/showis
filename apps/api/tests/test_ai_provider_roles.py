"""Anthropic preset, model rolleri ve rol bazlı istemci kurulumu."""

import httpx
import pytest
import respx
import test_research_workspace as _workspace
from showis.db.models import AIProvider
from showis.services.connectors_factory import build_llm_client
from sqlalchemy import select

# Ortak fixture'lar (SQLite in-memory + ASGI client) yeniden kullanılır.
client = _workspace.client
db_factory = _workspace.db_factory


async def _create(client, **extra):
    body = {
        "name": "Claude",
        "preset": "anthropic",
        "config": {
            "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
        },
        "secret": "sk-ant-secret-1234",
        "activate": True,
        **extra,
    }
    return await client.post("/api/v1/ai/providers", json=body)


@pytest.mark.asyncio
async def test_anthropic_preset_defaults_roles_and_capabilities(client):
    presets = (await client.get("/api/v1/ai/providers/presets")).json()
    assert presets["anthropic"]["default_model"] == "claude-opus-5-5"
    assert presets["anthropic"]["suggested_roles"]["extractor"] == "claude-haiku-5-5"

    response = await _create(
        client, roles={"planner": "claude-opus-5-5", "extractor": "claude-haiku-5-5"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["config"]["base_url"] == "https://api.anthropic.com/v1"
    assert body["roles"]["extractor"] == "claude-haiku-5-5"
    assert body["capabilities"]["claude-opus-5-5"]["thinking"] is True
    assert "sk-ant-secret-1234" not in str(body)


@pytest.mark.asyncio
async def test_roles_must_use_allowed_models_and_names(client):
    bad_model = await _create(client, roles={"planner": "gpt-9"})
    assert bad_model.status_code == 422
    bad_role = await _create(client, roles={"chef": "claude-opus-5-5"})
    assert bad_role.status_code == 422


@pytest.mark.asyncio
async def test_discover_models_uses_anthropic_headers(client):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.anthropic.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "claude-haiku-5-5"}]})
        )
        response = await client.post(
            "/api/v1/ai/providers/discover",
            json={"base_url": "https://api.anthropic.com/v1", "secret": "sk-ant-x"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["claude-haiku-5-5"]
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "sk-ant-x"
    assert "authorization" not in request.headers


@pytest.mark.asyncio
async def test_build_llm_client_applies_role_model_and_timeout(client, db_factory):
    created = await _create(
        client, roles={"reasoner": "claude-opus-5-5", "extractor": "claude-haiku-5-5"}
    )
    assert created.status_code == 201, created.text
    async with db_factory() as session:
        extractor = await build_llm_client(session, role="extractor")
        reasoner = await build_llm_client(session, role="reasoner")
        override = await build_llm_client(
            session, model_override="claude-sonnet-5-5", role="extractor"
        )
        plain = await build_llm_client(session)
        provider = (await session.execute(select(AIProvider))).scalar_one()
    assert extractor.model == "claude-haiku-5-5" and extractor.timeout <= 30.0
    assert reasoner.model == "claude-opus-5-5" and reasoner.timeout >= 180.0
    assert reasoner.thinking_budget and reasoner.api_style == "anthropic"
    assert override.model == "claude-sonnet-5-5"
    assert plain.model == "claude-opus-5-5" and plain.thinking_budget is None
    assert provider.roles["extractor"] == "claude-haiku-5-5"


@pytest.mark.asyncio
async def test_bilinmeyen_preset_422(client):
    """P-018: bilinmeyen preset sessizce opencode-go'ya düşmez."""
    bad = await _create(client, preset="openai-typo")
    assert bad.status_code == 422, bad.text
    assert "preset" in bad.text.lower()

    bad_config = await _create(client, config={"preset": "yanlis-preset"})
    assert bad_config.status_code == 422, bad_config.text

import json

import pytest
import respx
from pydantic import BaseModel
from showis_contracts import Platform, QueryPlan
from showis_llm import LLMClient, get_preset, redact_text, render_prompt


def test_redact_sensitive_info():
    text = "İletişim: ahmet.yilmaz@example.com ve +90 532 123 45 67 veya 05321234567"
    redacted = redact_text(text)
    assert "ahmet.yilmaz@example.com" not in redacted
    assert "a***@example.com" in redacted
    assert "+90 532 123 45 67" not in redacted
    assert "[TELEFON REDACTED]" in redacted


def test_render_prompts():
    rendered = render_prompt(
        "query_planner",
        first_name="Ahmet",
        last_name="Yılmaz",
        hints={"city": "Ankara"},
        variants=["ayilmaz", "ahmetyilmaz"],
    )
    assert "Ahmet" in rendered
    assert "Yılmaz" in rendered
    assert "Türkçe" in rendered


@pytest.mark.asyncio
async def test_llm_client_headers_and_opencode_go_preset():
    preset = get_preset("opencode-go")
    client = LLMClient(
        base_url=preset.base_url,
        api_key="test-opencode-key",
        model=preset.default_model,
        session_id="session-uuid-1234",
    )

    with respx.mock(assert_all_called=True) as mock:
        endpoint = mock.post(f"{preset.base_url}/chat/completions").respond(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "queries": [
                                        {
                                            "query": '"Ahmet Yılmaz" site:github.com',
                                            "platform": "github",
                                            "rationale": "Ad soyad github araması",
                                            "priority": 1,
                                        }
                                    ]
                                }
                            ),
                        }
                    }
                ]
            },
        )

        res = await client.complete(
            messages=[{"role": "user", "content": "Ahmet Yılmaz için arama planla"}],
            schema=QueryPlan,
        )

        assert isinstance(res, QueryPlan)
        assert len(res.queries) == 1
        assert res.queries[0].platform == Platform.GITHUB

        # Verify mandatory headers
        request = endpoint.calls[0].request
        assert request.headers["Authorization"] == "Bearer test-opencode-key"
        assert request.headers["x-opencode-session"] == "session-uuid-1234"
        assert request.headers["User-Agent"] == "Showis/0.1 (+https://github.com/showis-osint)"
        assert request.headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_llm_client_schema_repair_retry():
    preset = get_preset("opencode-go")
    client = LLMClient(
        base_url=preset.base_url,
        api_key="test-key",
        model="test-model",
        session_id="session-123",
    )

    class SimpleResult(BaseModel):
        name: str

    with respx.mock as mock:
        # First attempt returns invalid JSON, second attempt returns valid JSON
        mock.post(f"{preset.base_url}/chat/completions").side_effect = [
            httpx_respond_json(
                {"choices": [{"message": {"role": "assistant", "content": "not json"}}]}
            ),
            httpx_respond_json(
                {"choices": [{"message": {"role": "assistant", "content": '{"name": "valid"}'}}]}
            ),
        ]

        res = await client.complete(
            messages=[{"role": "user", "content": "isim ver"}],
            schema=SimpleResult,
        )
        assert res.name == "valid"


def httpx_respond_json(data: dict):
    import httpx

    return httpx.Response(200, json=data)

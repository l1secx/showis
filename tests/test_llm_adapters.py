"""LLM adapter'ları: native araç çağrısı, akış, rol ve politika (ağ yok; respx)."""

import json

import httpx
import pytest
import respx
from showis_llm import LLMClient, LLMClientError, reset_breaker
from showis_llm.adapters import anthropic as anth
from showis_llm.presets import detect_api_style, model_capabilities

TOOLS = [
    {
        "name": "web_search",
        "description": "Web'de ara",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


@pytest.fixture(autouse=True)
def _breaker():
    reset_breaker()
    yield
    reset_breaker()


def _openai_client(**kw) -> LLMClient:
    return LLMClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-test-openai-key",
        model="gpt-x",
        session_id="s1",
        **kw,
    )


def _anthropic_client(**kw) -> LLMClient:
    return LLMClient(
        base_url="https://api.anthropic.com/v1",
        api_key="sk-ant-test-key-123",
        model="claude-opus-5-5",
        session_id="s1",
        **kw,
    )


def test_api_style_and_capabilities():
    assert detect_api_style("https://api.anthropic.com/v1") == "anthropic"
    assert detect_api_style("https://api.openai.com/v1") == "openai"
    assert model_capabilities("claude-opus-5-5", "anthropic")["thinking"] is True


@pytest.mark.asyncio
async def test_openai_native_tool_call_and_redaction():
    body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": '{"query": "kerem"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    with respx.mock() as mock:
        route = mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await _openai_client().complete_with_tools(
            [{"role": "user", "content": "Mail: kerem@example.com ara"}], TOOLS
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"][0]["function"]["name"] == "web_search"
    assert "kerem@example.com" not in sent["messages"][0]["content"]
    assert result.tool_calls[0].name == "web_search"
    assert result.tool_calls[0].arguments == {"query": "kerem"}
    assert result.usage == {"input_tokens": 10, "output_tokens": 3}


@pytest.mark.asyncio
async def test_anthropic_tool_loop_headers_cache_and_thinking():
    body = {
        "content": [
            {"type": "thinking", "thinking": "düşünüyorum", "signature": "sig"},
            {"type": "text", "text": "Arıyorum"},
            {"type": "tool_use", "id": "tu_1", "name": "web_search", "input": {"query": "x"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 20, "output_tokens": 5, "cache_read_input_tokens": 12},
    }
    client = _anthropic_client(thinking_budget=2048)
    with respx.mock() as mock:
        route = mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await client.complete_with_tools(
            [
                {"role": "system", "content": "Sistem"},
                {"role": "user", "content": "Ara"},
            ],
            TOOLS,
        )
        request = route.calls.last.request
        assert request.headers["x-api-key"] == "sk-ant-test-key-123"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        sent = json.loads(request.content)
        assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert sent["thinking"] == {"type": "enabled", "budget_tokens": 2048}
        assert "temperature" not in sent
        assert sent["max_tokens"] > 2048

        assert result.thinking == "düşünüyorum"
        assert result.tool_calls[0].arguments == {"query": "x"}
        assert result.usage["cache_read_input_tokens"] == 12

        # Araç sonucu turu: düşünme blokları aynen geri gönderilir.
        follow = [
            {"role": "user", "content": "Ara"},
            result.assistant_message(),
            {"role": "tool", "tool_call_id": "tu_1", "content": "sonuç"},
        ]
        await client.complete_with_tools(follow, TOOLS)
        sent = json.loads(route.calls.last.request.content)
        assert sent["messages"][1]["content"][0]["type"] == "thinking"
        assert sent["messages"][2]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": "sonuç",
        }


def test_anthropic_split_merges_consecutive_tool_results():
    _, msgs = anth.split_messages(
        [
            {"role": "user", "content": "a"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "1", "name": "t", "arguments": {}},
                    {"id": "2", "name": "t", "arguments": {}},
                ],
            },
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
            {"role": "tool", "tool_call_id": "2", "content": "r2"},
        ]
    )
    assert len(msgs) == 3
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["1", "2"]


@pytest.mark.asyncio
async def test_anthropic_complete_with_schema_still_works():
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool

    with respx.mock() as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": '{"ok": true}'}]}
            )
        )
        parsed = await _anthropic_client().complete(
            [{"role": "user", "content": "x"}], schema=Out
        )
    assert parsed.ok is True


def _sse(*pairs) -> bytes:
    out = ""
    for event, data in pairs:
        if event:
            out += f"event: {event}\n"
        out += f"data: {json.dumps(data)}\n\n"
    return out.encode()


@pytest.mark.asyncio
async def test_openai_stream_parses_text_and_tool_deltas():
    raw = _sse(
        (None, {"choices": [{"delta": {"content": "Mer"}}]}),
        (None, {"choices": [{"delta": {"content": "haba"}}]}),
        (
            None,
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "web_search", "arguments": '{"que'},
                                }
                            ]
                        }
                    }
                ]
            },
        ),
        (
            None,
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": 'ry":"z"}'}}]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ),
        (None, {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}),
    ) + b"data: [DONE]\n\n"
    with respx.mock() as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=raw)
        )
        client = _openai_client()
        events = [e async for e in client.stream([{"role": "user", "content": "x"}], TOOLS)]
    assert "".join(e.text for e in events if e.type == "text_delta") == "Merhaba"
    call = next(e.tool_call for e in events if e.type == "tool_call")
    assert call.arguments == {"query": "z"}
    done = events[-1]
    assert done.type == "done" and done.result.text == "Merhaba"
    assert done.result.usage == {"input_tokens": 4, "output_tokens": 2}


@pytest.mark.asyncio
async def test_anthropic_stream_parses_thinking_text_and_tool_input():
    raw = _sse(
        ("message_start", {"message": {"usage": {"input_tokens": 9, "output_tokens": 1}}}),
        ("content_block_start", {"index": 0, "content_block": {"type": "thinking"}}),
        (
            "content_block_delta",
            {"index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
        ),
        ("content_block_start", {"index": 1, "content_block": {"type": "text"}}),
        ("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "Selam"}}),
        (
            "content_block_start",
            {"index": 2, "content_block": {"type": "tool_use", "id": "tu", "name": "web_search"}},
        ),
        (
            "content_block_delta",
            {"index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"query":'}},
        ),
        (
            "content_block_delta",
            {"index": 2, "delta": {"type": "input_json_delta", "partial_json": '"q"}'}},
        ),
        ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}),
        ("message_stop", {}),
    )
    with respx.mock() as mock:
        mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content=raw)
        )
        events = [
            e async for e in _anthropic_client().stream([{"role": "user", "content": "x"}], TOOLS)
        ]
    types = [e.type for e in events]
    assert "thinking_summary" in types and "text_delta" in types
    result = events[-1].result
    assert result.text == "Selam" and result.thinking == "hmm"
    assert result.tool_calls[0].arguments == {"query": "q"}
    assert result.usage == {"input_tokens": 9, "output_tokens": 7}
    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_http_error_hides_body_and_sets_status():
    with respx.mock() as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(400, text="secret-body-with-key")
        )
        with pytest.raises(LLMClientError) as exc:
            await _openai_client().complete_with_tools([{"role": "user", "content": "x"}], TOOLS)
    assert exc.value.status_code == 400
    assert "secret-body" not in str(exc.value)


@pytest.mark.asyncio
async def test_policy_blocks_disallowed_host_before_any_request():
    client = LLMClient(
        base_url="https://api.openai.com/v1", api_key="k", model="m", session_id="s"
    )
    client.base_url = "https://evil.example.com/v1"
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(LLMClientError):
            await client.complete_with_tools([{"role": "user", "content": "x"}], TOOLS)
        with pytest.raises(LLMClientError):
            _ = [e async for e in client.stream([{"role": "user", "content": "x"}])]
    assert not route.called

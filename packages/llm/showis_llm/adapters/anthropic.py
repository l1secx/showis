"""Anthropic Messages API (`/v1/messages`) dönüştürücüsü.

- `x-api-key` + `anthropic-version` başlıkları (çağıran ekler).
- Sistem istemi ve büyük bağlam blokları `cache_control` ile önbelleğe alınır.
- Genişletilmiş düşünme (`thinking`) açıksa düşünme blokları sonraki turda
  aynen geri gönderilir (`provider_content`).
"""

from __future__ import annotations

import json
from typing import Any

from .types import LLMEvent, LLMResult, ToolCall, ToolSpec

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
MIN_THINKING_BUDGET = 1024


def _text_block(text: str, *, cache: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def split_messages(
    messages: list[dict[str, Any]], *, cache: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(system blokları, messages) döndürür; araç sonuçları user turuna birleşir."""
    system_texts: list[str] = []
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = str(msg.get("content") or "")
        if role == "system":
            if content:
                system_texts.append(content)
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id") or ""),
                "content": content,
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            provider = msg.get("provider_content")
            if provider:
                out.append({"role": "assistant", "content": provider})
                continue
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments") or {},
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "."}]})
            continue
        # user
        if out and out[-1]["role"] == "user":
            previous = out[-1]["content"]
            if isinstance(previous, list):
                previous.append({"type": "text", "text": content})
            else:
                out[-1]["content"] = [{"type": "text", "text": previous}, _text_block(content)]
        else:
            out.append({"role": "user", "content": content})
    system_blocks = [_text_block(text) for text in system_texts]
    if cache and system_blocks:
        system_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return system_blocks, out


def build_tools(tools: list[ToolSpec], *, cache: bool = True) -> list[dict[str, Any]]:
    out = [
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema") or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]
    if cache and out:
        out[-1]["cache_control"] = {"type": "ephemeral"}
    return out


def build_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec] | None,
    temperature: float,
    max_tokens: int | None,
    stream: bool,
    thinking_budget: int | None = None,
    cache: bool = True,
) -> dict[str, Any]:
    system_blocks, converted = split_messages(messages, cache=cache)
    limit = max_tokens or DEFAULT_MAX_TOKENS
    payload: dict[str, Any] = {"model": model, "messages": converted, "max_tokens": limit}
    if system_blocks:
        payload["system"] = system_blocks
    if tools:
        payload["tools"] = build_tools(tools, cache=cache)
    if thinking_budget and thinking_budget >= MIN_THINKING_BUDGET:
        # Düşünme açıkken sıcaklık ayarlanamaz ve max_tokens bütçeyi aşmalıdır.
        payload["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        payload["max_tokens"] = max(limit, thinking_budget + 1024)
    else:
        payload["temperature"] = temperature
    if stream:
        payload["stream"] = True
    return payload


def _usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage = {
        "input_tokens": int(raw.get("input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
    }
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        if raw.get(key):
            usage[key] = int(raw[key])
    return usage


def parse_response(data: dict[str, Any]) -> LLMResult:
    blocks = data.get("content") or []
    texts: list[str] = []
    thinking: list[str] = []
    calls: list[ToolCall] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "thinking":
            thinking.append(str(block.get("thinking") or ""))
        elif kind == "tool_use":
            arguments = block.get("input")
            calls.append(
                ToolCall(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
    return LLMResult(
        text="".join(texts),
        tool_calls=calls,
        thinking="\n".join(thinking),
        stop_reason=str(data.get("stop_reason") or ""),
        usage=_usage(data.get("usage")),
        provider_content=list(blocks) if blocks else None,
    )


class StreamParser:
    """Anthropic SSE olaylarını (event adı + JSON) LLMEvent'e çevirir."""

    def __init__(self) -> None:
        self.blocks: dict[int, dict[str, Any]] = {}
        self.usage: dict[str, int] = {}
        self.stop_reason = ""

    def _merge(self, usage: dict[str, int]) -> None:
        self.usage.update({key: value for key, value in usage.items() if value})

    def feed(self, event: str, data: dict[str, Any]) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        if event == "message_start":
            self._merge(_usage((data.get("message") or {}).get("usage")))
        elif event == "content_block_start":
            block = dict(data.get("content_block") or {})
            kind = block.get("type")
            if kind == "text":
                block.setdefault("text", "")
            elif kind == "thinking":
                block.setdefault("thinking", "")
            elif kind == "tool_use":
                block["_json"] = ""
            self.blocks[int(data.get("index") or 0)] = block
        elif event == "content_block_delta":
            block = self.blocks.get(int(data.get("index") or 0))
            delta = data.get("delta") or {}
            if block is None:
                return events
            dtype = delta.get("type")
            if dtype == "text_delta":
                block["text"] = block.get("text", "") + str(delta.get("text") or "")
                events.append(LLMEvent("text_delta", text=str(delta.get("text") or "")))
            elif dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + str(delta.get("thinking") or "")
                events.append(LLMEvent("thinking_summary", text=str(delta.get("thinking") or "")))
            elif dtype == "signature_delta":
                block["signature"] = str(delta.get("signature") or "")
            elif dtype == "input_json_delta":
                block["_json"] = block.get("_json", "") + str(delta.get("partial_json") or "")
        elif event == "message_delta":
            delta = data.get("delta") or {}
            if delta.get("stop_reason"):
                self.stop_reason = str(delta["stop_reason"])
            self._merge(_usage(data.get("usage")))
        return events

    def finish(self) -> LLMResult:
        content: list[dict[str, Any]] = []
        for index in sorted(self.blocks):
            block = dict(self.blocks[index])
            if block.get("type") == "tool_use":
                raw = block.pop("_json", "") or "{}"
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = {}
                block["input"] = parsed if isinstance(parsed, dict) else {}
            content.append(block)
        result = parse_response(
            {"content": content, "stop_reason": self.stop_reason, "usage": self.usage}
        )
        return result

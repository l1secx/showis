"""OpenAI uyumlu `chat/completions` dönüştürücüsü (tools + stream)."""

from __future__ import annotations

import json
from typing import Any

from .types import LLMEvent, LLMResult, ToolCall, ToolSpec


def build_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(msg.get("tool_call_id") or ""),
                    "content": str(msg.get("content") or ""),
                }
            )
            continue
        item: dict[str, Any] = {"role": role, "content": msg.get("content") or ""}
        calls = msg.get("tool_calls")
        if role == "assistant" and calls:
            item["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
            if not item["content"]:
                item["content"] = None
        out.append(item)
    return out


def build_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def build_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec] | None,
    temperature: float,
    max_tokens: int | None,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": build_messages(messages),
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = build_tools(tools)
        payload["tool_choice"] = "auto"
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    return payload


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {
        "input_tokens": int(raw.get("prompt_tokens") or 0),
        "output_tokens": int(raw.get("completion_tokens") or 0),
    }


def parse_response(data: dict[str, Any]) -> LLMResult:
    choices = data.get("choices") or []
    if not choices:
        return LLMResult(usage=_usage(data.get("usage")))
    choice = choices[0]
    message = choice.get("message") or {}
    calls = [
        ToolCall(
            id=str(call.get("id") or f"call_{index}"),
            name=str((call.get("function") or {}).get("name") or ""),
            arguments=_args((call.get("function") or {}).get("arguments")),
        )
        for index, call in enumerate(message.get("tool_calls") or [])
    ]
    thinking = str(message.get("reasoning_content") or message.get("reasoning") or "")
    return LLMResult(
        text=str(message.get("content") or ""),
        tool_calls=[call for call in calls if call.name],
        thinking=thinking,
        stop_reason=str(choice.get("finish_reason") or ""),
        usage=_usage(data.get("usage")),
    )


class StreamParser:
    """SSE `data:` JSON parçalarını olaylara çevirir."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.calls: dict[int, dict[str, str]] = {}
        self.usage: dict[str, int] = {}
        self.stop_reason = ""

    def feed(self, chunk: dict[str, Any]) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        if chunk.get("usage"):
            self.usage = _usage(chunk["usage"])
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                self.text.append(piece)
                events.append(LLMEvent("text_delta", text=piece))
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                self.thinking.append(reasoning)
                events.append(LLMEvent("thinking_summary", text=reasoning))
            for call in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(
                    int(call.get("index") or 0), {"id": "", "name": "", "arguments": ""}
                )
                if call.get("id"):
                    slot["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    slot["name"] += function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]
            if choice.get("finish_reason"):
                self.stop_reason = str(choice["finish_reason"])
        return events

    def finish(self) -> LLMResult:
        calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=_args(slot["arguments"]),
            )
            for index, slot in sorted(self.calls.items())
            if slot["name"]
        ]
        return LLMResult(
            text="".join(self.text),
            tool_calls=calls,
            thinking="".join(self.thinking),
            stop_reason=self.stop_reason,
            usage=self.usage,
        )

"""Sağlayıcıdan bağımsız mesaj/araç tipleri.

Mesaj biçimi (nötr):

- ``{"role": "system"|"user"|"assistant", "content": str}``
- asistan araç çağırdıysa ``"tool_calls": [{"id", "name", "arguments": dict}]``
- araç sonucu ``{"role": "tool", "tool_call_id": str, "content": str}``
- Anthropic düşünme blokları ``"provider_content"`` altında geri taşınır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Nötr araç tanımı: {"name", "description", "input_schema": JSON-Schema}
ToolSpec = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    #: Anthropic: sonraki turda aynen geri gönderilecek içerik blokları.
    provider_content: list[dict[str, Any]] | None = None

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ]
        if self.provider_content is not None:
            message["provider_content"] = self.provider_content
        return message


@dataclass
class LLMEvent:
    """Akış olayı: text_delta | thinking_summary | tool_call | usage | done."""

    type: str
    text: str = ""
    tool_call: ToolCall | None = None
    usage: dict[str, int] | None = None
    result: LLMResult | None = None

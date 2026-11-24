import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .adapters import anthropic as anthropic_adapter
from .adapters import openai_compat
from .adapters.types import LLMEvent, LLMResult, ToolSpec
from .base_url import BaseUrlError, validate_base_url
from .breaker import get_breaker
from .presets import detect_api_style
from .redact import redact_text

logger = logging.getLogger("showis.llm")
T = TypeVar("T", bound=BaseModel)

DEFAULT_USER_AGENT = "Showis/0.1 (+https://github.com/showis-osint)"

#: T-333: hızlı başarısız ol — erişilemeyen LLM taramayı ~90 sn bekletmesin.
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_RETRIES = 1
TIMEOUT_ENV = "SHOWIS_LLM_TIMEOUT"
RETRIES_ENV = "SHOWIS_LLM_RETRIES"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0.5, float(str(raw).strip()))
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan %s kullanilir", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan %s kullanilir", name, raw, default)
        return default


class LLMClientError(Exception):
    def __init__(self, message: str = "", *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        session_id: str,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        strict_base_url: bool = True,
        role: str | None = None,
        thinking_budget: int | None = None,
    ):
        # SEC-02/03: sır yalnız allowlist'teki bir host'a gider. Kurulumda ve
        # her çağrıda doğrulanır (DB'de eski değer kalmış olabilir).
        if strict_base_url:
            base_url = validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.session_id = session_id
        self.role = role
        self.thinking_budget = thinking_budget
        #: Sağlayıcı araç çağrısını reddederse True; çağıran JSON-şema yoluna düşer.
        self.native_tools_disabled = False
        self.api_style = detect_api_style(self.base_url)
        self.user_agent = user_agent
        # T-333: env > argüman > varsayılan (8 sn / 1 deneme)
        self.timeout = (
            _env_float(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
            if timeout_seconds is None
            else timeout_seconds
        )
        self.max_retries = (
            _env_int(RETRIES_ENV, DEFAULT_MAX_RETRIES)
            if max_retries is None
            else max(1, int(max_retries))
        )

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Her mesajın metni redakte edilir; araç/düşünme alanları korunur."""
        redacted_messages: list[dict[str, Any]] = []
        for msg in messages:
            item = dict(msg)
            item["role"] = msg.get("role", "user")
            item["content"] = redact_text(str(msg.get("content") or ""))
            redacted_messages.append(item)
        return redacted_messages

    def _check_policy(self) -> None:
        # Her çağrıda politika kontrolü (defense in depth; sır gitmeden önce).
        try:
            validate_base_url(self.base_url)
        except BaseUrlError as exc:
            raise LLMClientError("LLM base_url blocked by policy") from exc

    def _headers(self) -> dict[str, str]:
        if self.api_style == "anthropic":
            return {
                "x-api-key": self.api_key,
                "anthropic-version": anthropic_adapter.API_VERSION,
                "User-Agent": self.user_agent,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-opencode-session": self.session_id,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
        }

    def _url(self) -> str:
        suffix = "messages" if self.api_style == "anthropic" else "chat/completions"
        return f"{self.base_url}/{suffix}"

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        if self.api_style == "anthropic":
            return anthropic_adapter.build_payload(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                thinking_budget=self.thinking_budget,
                tool_choice=tool_choice,
            )
        return openai_compat.build_payload(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            tool_choice=tool_choice,
        )

    def _parse(self, data: dict[str, Any]) -> LLMResult:
        if self.api_style == "anthropic":
            return anthropic_adapter.parse_response(data)
        return openai_compat.parse_response(data)

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        tool_choice: str | None = None,
    ) -> LLMResult:
        """Native araç çağrısı (OpenAI `tools` / Anthropic `tool_use`)."""
        self._check_policy()
        payload = self._build_payload(
            self._prepare_messages(messages), tools, temperature, max_tokens, False, tool_choice
        )
        data = await self._post_json(payload)
        result = self._parse(data)
        if not result.text and not result.tool_calls and not data.get("choices", True):
            raise LLMClientError("LLM returned no choices")
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """SSE akışı: text_delta / thinking_summary / usage / done (+ tool_call).

        `timeout` akışta boşta bekleme (idle) süresi olarak uygulanır.
        """
        self._check_policy()
        payload = self._build_payload(
            self._prepare_messages(messages), tools, temperature, max_tokens, True
        )
        breaker = get_breaker()
        if not breaker.allow():
            raise LLMClientError("LLM devre dışı (cooldown)")
        breaker.record_call()
        timeout = httpx.Timeout(self.timeout, connect=min(self.timeout, 15.0))
        anthropic_parser = anthropic_adapter.StreamParser()
        openai_parser = openai_compat.StreamParser()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", self._url(), headers=self._headers(), json=payload
                ) as resp:
                    if resp.status_code != 200:
                        breaker.record_failure()
                        raise LLMClientError(
                            f"LLM HTTP error {resp.status_code}", status_code=resp.status_code
                        )
                    event_name = ""
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        if self.api_style == "anthropic":
                            kind = event_name or str(chunk.get("type") or "")
                            if kind == "error":
                                raise LLMClientError("LLM stream error")
                            for event in anthropic_parser.feed(kind, chunk):
                                yield event
                        else:
                            for event in openai_parser.feed(chunk):
                                yield event
        except LLMClientError:
            raise
        except Exception as exc:
            breaker.record_failure()
            raise LLMClientError(f"LLM stream failed ({type(exc).__name__})") from None
        result = (
            anthropic_parser.finish() if self.api_style == "anthropic" else openai_parser.finish()
        )
        breaker.record_success()
        for call in result.tool_calls:
            yield LLMEvent("tool_call", tool_call=call)
        if result.usage:
            yield LLMEvent("usage", usage=result.usage)
        yield LLMEvent("done", result=result)

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retry + breaker ile tek JSON POST (araç yolu)."""
        breaker = get_breaker()
        if not breaker.allow():
            raise LLMClientError("LLM devre dışı (cooldown)")
        breaker.record_call()
        last_status: int | None = None
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self._url(), headers=self._headers(), json=payload
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_status = resp.status_code
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                if resp.status_code >= 400:
                    breaker.record_failure()
                    # SEC-08: sağlayıcı gövdesi sızdırılmaz.
                    raise LLMClientError(
                        f"LLM HTTP error {resp.status_code}", status_code=resp.status_code
                    )
                data = resp.json()
                breaker.record_success()
                return data if isinstance(data, dict) else {}
            except LLMClientError:
                raise
            except Exception as exc:
                last_error = exc
                logger.error("LLM cagrisi basarisiz (deneme %d): %s", attempt, type(exc).__name__)
        breaker.record_failure()
        raise LLMClientError(
            f"LLM request failed after {self.max_retries + 1} attempts "
            f"({last_status or type(last_error).__name__})",
            status_code=last_status,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        schema: type[T] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Any:
        prepared_msgs = self._prepare_messages(messages)

        self._check_policy()

        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            system_instruction = (
                f"\nYanıtını YALNIZCA geçerli bir JSON formatında ver. "
                f"Aşağıdaki JSON şemasına kesinlikle uy:\n{schema_json}"
            )
            prepared_msgs[-1]["content"] += system_instruction

        headers = self._headers()
        payload = self._build_payload(prepared_msgs, None, temperature, max_tokens, False)
        if schema is not None and self.api_style == "openai":
            payload["response_format"] = {"type": "json_object"}

        breaker = get_breaker()
        if not breaker.allow():
            # Devre açık: hiç istek atılmaz, çağıran anında fallback'e düşer.
            raise LLMClientError("LLM devre dışı (cooldown)")
        breaker.record_call()

        max_retries = self.max_retries
        last_error: Exception | None = None

        # `max_retries` = ek deneme sayisi (1 -> toplam 2 girisim)
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self._url(),
                        headers=headers,
                        json=payload,
                    )

                    if resp.status_code == 429:
                        wait = 2**attempt
                        logger.warning("LLM Rate limited (429). Retrying in %ds...", wait)
                        await asyncio.sleep(wait)
                        continue

                    if resp.status_code >= 500:
                        logger.warning("LLM Server error (%d). Retrying...", resp.status_code)
                        await asyncio.sleep(1)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    if self.api_style == "anthropic":
                        raw_content = self._parse(data).text
                        if not raw_content:
                            raise LLMClientError("LLM returned no content")
                    else:
                        choices = data.get("choices", [])
                        if not choices:
                            raise LLMClientError("LLM returned no choices")
                        raw_content = choices[0]["message"]["content"]

                    if schema is None:
                        breaker.record_success()
                        return raw_content

                    # Validate against schema
                    try:
                        # Extract JSON snippet if markdown code block returned
                        clean_content = raw_content.strip()
                        if clean_content.startswith("```json"):
                            clean_content = clean_content[7:]
                        if clean_content.startswith("```"):
                            clean_content = clean_content[3:]
                        if clean_content.endswith("```"):
                            clean_content = clean_content[:-3]
                        clean_content = clean_content.strip()

                        parsed = schema.model_validate_json(clean_content)
                        breaker.record_success()
                        return parsed
                    except (ValidationError, json.JSONDecodeError) as val_err:
                        # 1 schema repair retry
                        if attempt == 0:
                            logger.info("Schema parse error, attempting repair retry...")
                            prepared_msgs.append({"role": "assistant", "content": raw_content})
                            prepared_msgs.append(
                                {
                                    "role": "user",
                                    "content": (
                                        f"Verdiğin JSON geçersiz: {val_err}. "
                                        "Lütfen sadece geçerli JSON ile düzelt."
                                    ),
                                }
                            )
                            payload = self._build_payload(
                                prepared_msgs, None, temperature, max_tokens, False
                            )
                            if self.api_style == "openai":
                                payload["response_format"] = {"type": "json_object"}
                            continue
                        raise LLMClientError(
                            "LLM output failed schema validation "
                            f"({type(val_err).__name__})"
                        ) from None

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    continue
                # SEC-08: sağlayıcı gövdesi ve URL query'si sızdırılmaz.
                breaker.record_failure()
                raise LLMClientError(
                    f"LLM HTTP error {e.response.status_code}"
                ) from None
            except LLMClientError:
                raise
            except Exception as e:
                last_error = e
                logger.error(
                    "LLM cagrisi basarisiz (deneme %d): %s", attempt, type(e).__name__
                )

        breaker.record_failure()
        raise LLMClientError(
            f"LLM request failed after {max_retries + 1} attempts "
            f"({type(last_error).__name__})"
        )

import asyncio
import json
import logging
import os
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .base_url import BaseUrlError, validate_base_url
from .breaker import get_breaker
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
    pass


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
    ):
        # SEC-02/03: sır yalnız allowlist'teki bir host'a gider. Kurulumda ve
        # her çağrıda doğrulanır (DB'de eski değer kalmış olabilir).
        if strict_base_url:
            base_url = validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.session_id = session_id
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

    def _prepare_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        redacted_messages = []
        for msg in messages:
            content = msg.get("content", "")
            redacted_messages.append(
                {
                    "role": msg.get("role", "user"),
                    "content": redact_text(content),
                }
            )
        return redacted_messages

    async def complete(
        self,
        messages: list[dict[str, str]],
        schema: type[T] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Any:
        prepared_msgs = self._prepare_messages(messages)

        # Her çağrıda politika kontrolü (defense in depth; sır gitmeden önce).
        try:
            validate_base_url(self.base_url)
        except BaseUrlError as exc:
            raise LLMClientError("LLM base_url blocked by policy") from exc

        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            system_instruction = (
                f"\nYanıtını YALNIZCA geçerli bir JSON formatında ver. "
                f"Aşağıdaki JSON şemasına kesinlikle uy:\n{schema_json}"
            )
            prepared_msgs[-1]["content"] += system_instruction

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "x-opencode-session": self.session_id,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": prepared_msgs,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if schema is not None:
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
                        f"{self.base_url}/chat/completions",
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
                            payload["messages"] = prepared_msgs
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

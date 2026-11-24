"""Araştırma ajanının interaktif katmanı.

- Oturum başına süreç-içi kontrol nesnesi: yönlendirme (steer) kuyruğu,
  durdurma (interrupt) bayrağı ve ajanın kullanıcıya sorusu (`ask_user`).
- SSE yayıncısı ve toplu token yayını.
- Native araç çağrısı yardımcıları (`llm_structured`); araç desteklenmiyorsa
  eski JSON-şema yoluna düşülür.

Değişmez: bu katman havuza yazmaz; AI çıktıları `proposed/candidate` kalır.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from showis.services.orchestrator import ScanEventBroadcaster
from showis_llm import LLMClientError

logger = logging.getLogger("showis.research.interactive")
T = TypeVar("T", bound=BaseModel)

research_broadcaster = ScanEventBroadcaster()

#: Kullanıcı soruya bu kadar sürede yanıt vermezse ajan kendi kararıyla sürer.
QUESTION_TIMEOUT_SECONDS = 900.0
MAX_QUESTIONS_PER_SESSION = 4
MAX_STEERING_PENDING = 50
MAX_STEER_CHARS = 2000
TOKEN_FLUSH_CHARS = 160


@dataclass
class PendingQuestion:
    id: str
    question: str
    options: list[str]
    future: asyncio.Future[str]


QuestionHook = Callable[[PendingQuestion], Awaitable[None]]
AnsweredHook = Callable[[PendingQuestion, str], Awaitable[None]]


@dataclass
class ResearchControl:
    session_id: str
    interrupt: bool = False
    steering: list[str] = field(default_factory=list)
    pending: PendingQuestion | None = None
    asked: int = 0
    on_question: QuestionHook | None = None
    on_answered: AnsweredHook | None = None
    #: Soru sorulmadan hemen önce çağrılır: süreç ölürse oturum kaldığı yerden sürsün.
    on_checkpoint: Callable[[], Awaitable[None]] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # -- yönlendirme ---------------------------------------------------
    def push_steer(self, text: str) -> bool:
        """Yönlendirmeyi kuyruğa alır (ekleme-yalnız liste; tüketiciler kendi imlecini tutar)."""
        text = " ".join(str(text or "").split())[:MAX_STEER_CHARS]
        if not text or len(self.steering) >= MAX_STEERING_PENDING:
            return False
        self.steering.append(text)
        return True

    def since(self, cursor: int) -> list[str]:
        """İmleçten sonra gelen yönlendirmeler; çağıran imleci `len(steering)` yapar."""
        return list(self.steering[cursor:])

    # -- soru ---------------------------------------------------------
    async def ask(self, question: str, options: list[str] | None = None) -> str:
        """Kullanıcıya soru sorar ve yanıtı bekler (aynı anda tek soru)."""
        question = " ".join(str(question or "").split())[:600]
        if not question:
            return "(boş soru)"
        clean_options = [" ".join(str(o).split())[:120] for o in (options or []) if str(o).strip()]
        async with self._lock:
            if self.interrupt:
                return "(oturum durduruldu)"
            if self.asked >= MAX_QUESTIONS_PER_SESSION:
                return "(soru sınırı doldu; kendi muhakemenle devam et)"
            self.asked += 1
            loop = asyncio.get_running_loop()
            pending = PendingQuestion(
                id=uuid.uuid4().hex[:12],
                question=question,
                options=clean_options[:6],
                future=loop.create_future(),
            )
            self.pending = pending
            try:
                if self.on_checkpoint is not None:
                    try:
                        await self.on_checkpoint()
                    except Exception:  # noqa: BLE001 - kontrol noktası soruyu engellemesin
                        logger.warning("Kontrol noktasi yazilamadi", exc_info=True)
                if self.on_question is not None:
                    await self.on_question(pending)
                try:
                    answer = await asyncio.wait_for(
                        asyncio.shield(pending.future), timeout=QUESTION_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    answer = "(yanıt gelmedi; kendi muhakemenle devam et)"
                    if not pending.future.done():
                        pending.future.set_result(answer)
            finally:
                self.pending = None
            if self.on_answered is not None:
                await self.on_answered(pending, answer)
            return answer

    def answer(self, question_id: str, answer: str) -> bool:
        pending = self.pending
        if pending is None or pending.id != question_id or pending.future.done():
            return False
        pending.future.set_result(" ".join(str(answer or "").split())[:2000] or "(boş yanıt)")
        return True

    def request_interrupt(self) -> None:
        self.interrupt = True
        pending = self.pending
        if pending is not None and not pending.future.done():
            pending.future.set_result("(oturum durduruldu)")


_CONTROLS: dict[str, ResearchControl] = {}


def register_control(session_id: str) -> ResearchControl:
    control = ResearchControl(session_id=session_id)
    _CONTROLS[session_id] = control
    return control


def get_control(session_id: str) -> ResearchControl | None:
    return _CONTROLS.get(session_id)


def release_control(session_id: str) -> None:
    _CONTROLS.pop(session_id, None)


async def emit(session_id: str, event: str, data: dict[str, Any]) -> None:
    """Başarısız yayın araştırmayı düşürmesin."""
    try:
        await research_broadcaster.broadcast(session_id, f"research.{event}", data)
    except Exception:  # noqa: BLE001
        logger.debug("SSE yayini basarisiz (%s)", event, exc_info=True)


class TokenBatcher:
    """Token parçalarını toplayıp seyrek SSE olayı olarak yayınlar."""

    def __init__(self, session_id: str, agent: int | None = None, kind: str = "token"):
        self.session_id = session_id
        self.agent = agent
        self.kind = kind
        self._buf: list[str] = []
        self._size = 0

    async def add(self, text: str) -> None:
        if not text:
            return
        self._buf.append(text)
        self._size += len(text)
        if self._size >= TOKEN_FLUSH_CHARS:
            await self.flush()

    async def flush(self) -> None:
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf.clear()
        self._size = 0
        await emit(
            self.session_id,
            self.kind,
            {"session_id": self.session_id, "agent": self.agent, "text": text},
        )


# ----------------------------------------------------------------------
# Native araç yardımcıları
# ----------------------------------------------------------------------
def native_tools_available(client: Any) -> bool:
    """İstemci native araç çağrısını destekliyor ve devre dışı bırakılmamış mı?"""
    return callable(getattr(client, "complete_with_tools", None)) and not getattr(
        client, "native_tools_disabled", False
    )


def mark_native_tools_unsupported(client: Any, exc: BaseException) -> bool:
    """Sağlayıcı araç şemasını reddettiyse (400/404/422) native yolu kapatır."""
    status = getattr(exc, "status_code", None)
    if status in (400, 404, 422):
        try:
            client.native_tools_disabled = True
        except Exception:  # noqa: BLE001
            return False
        logger.info("Model arac cagrisini desteklemiyor; JSON-sema yoluna dusuluyor")
        return True
    return False


def schema_tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {"name": name, "description": description, "input_schema": model.model_json_schema()}


async def llm_structured(
    client: Any,
    messages: list[dict[str, Any]],
    schema: type[T],
    *,
    tool_name: str,
    description: str,
    temperature: float = 0.2,
    max_tokens: int = 2600,
) -> T | None:
    """Şemalı çıktı: native araç (zorlanmış) → olmazsa `complete(schema=...)`."""
    if native_tools_available(client):
        try:
            result = await client.complete_with_tools(
                messages,
                [schema_tool(tool_name, description, schema)],
                temperature=temperature,
                max_tokens=max_tokens,
                tool_choice=tool_name,
            )
            for call in result.tool_calls:
                if call.name == tool_name:
                    return schema.model_validate(call.arguments)
        except ValidationError:
            pass
        except LLMClientError as exc:
            if not mark_native_tools_unsupported(client, exc):
                raise
    parsed = await client.complete(
        messages=messages, schema=schema, temperature=temperature, max_tokens=max_tokens
    )
    return parsed if isinstance(parsed, schema) else None

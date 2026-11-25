"""Nihai rapor/biyografi akışı: token olayları, sonda şema doğrulaması, complete() yedeği."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
import respx
import test_research_interactive as _interactive
import test_research_workspace as _workspace
from showis.db.models import Case as DbCase
from showis.db.models import ResearchSession
from showis.services.research import (
    BioDraft,
    ResearchReport,
    ResearchRuntime,
    _synthesize_bio,
    _synthesize_report,
)
from showis.services.research_interactive import (
    JsonStringFieldExtractor,
    research_broadcaster,
    structured_with_stream,
)
from showis.services.research_playbook import build_identity_profile
from showis_llm import LLMClient, reset_breaker
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory

URL = "https://api.openai.com/v1/chat/completions"


@pytest.fixture(autouse=True)
def _breaker():
    reset_breaker()
    yield
    reset_breaker()


def _llm() -> LLMClient:
    return LLMClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-test-openai-key",
        model="gpt-x",
        session_id="s1",
        max_retries=0,
    )


def _sse(parts: list[str]) -> bytes:
    out = "".join(
        f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}\n\n" for p in parts
    )
    return (out + "data: [DONE]\n\n").encode()


def _drain(queue) -> list[dict[str, Any]]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def test_extractor_decodes_escapes_across_chunks():
    ex = JsonStringFieldExtractor("answer")
    text = '{"answer": "Satır 1\\nTırnak \\"x\\" \\u00fc", "citations": []}'
    out = "".join(ex.feed(text[i : i + 3]) for i in range(0, len(text), 3))
    assert out == 'Satır 1\nTırnak "x" ü'
    assert ex.done


@pytest.mark.asyncio
async def test_stream_emits_tokens_and_validates_at_end():
    payload = json.dumps({"answer": "## Özet\nKerem Boz bulundu.", "citations": []})
    chunks = [payload[i : i + 7] for i in range(0, len(payload), 7)]
    queue = research_broadcaster.subscribe("sess-stream")
    with respx.mock() as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200, content=_sse(chunks)))
        report = await structured_with_stream(
            _llm(),
            [{"role": "user", "content": "yaz"}],
            ResearchReport,
            session_id="sess-stream",
            stream_field="answer",
        )
    assert route.call_count == 1
    assert report is not None and report.answer.startswith("## Özet")
    request_body = json.loads(route.calls[0].request.content)
    assert request_body["stream"] is True
    events = _drain(queue)
    streamed = "".join(e["data"]["text"] for e in events if e["type"] == "research.token")
    assert streamed == "## Özet\nKerem Boz bulundu."
    assert "{" not in streamed  # JSON gürültüsü yok


@pytest.mark.asyncio
async def test_invalid_stream_json_falls_back_to_complete():
    good = json.dumps({"answer": "yedek yol", "citations": []})
    with respx.mock() as mock:
        route = mock.post(URL).mock(
            side_effect=[
                httpx.Response(200, content=_sse(["bu json değil"])),
                httpx.Response(200, json={"choices": [{"message": {"content": good}}]}),
            ]
        )
        report = await structured_with_stream(
            _llm(),
            [{"role": "user", "content": "yaz"}],
            ResearchReport,
            session_id="sess-fb",
            stream_field="answer",
        )
    assert route.call_count == 2
    assert report is not None and report.answer == "yedek yol"


@pytest.mark.asyncio
async def test_stream_http_error_falls_back_to_complete():
    good = json.dumps({"answer": "yedek", "citations": []})
    with respx.mock() as mock:
        mock.post(URL).mock(
            side_effect=[
                httpx.Response(400, json={"error": "stream yok"}),
                httpx.Response(200, json={"choices": [{"message": {"content": good}}]}),
            ]
        )
        report = await structured_with_stream(
            _llm(),
            [{"role": "user", "content": "yaz"}],
            ResearchReport,
            session_id="sess-400",
            stream_field="answer",
        )
    assert report is not None and report.answer == "yedek"


@pytest.mark.asyncio
async def test_client_without_stream_uses_complete():
    class NoStream:
        async def complete(self, messages, schema=None, **kwargs):
            return ResearchReport(answer="akışsız")

    report = await structured_with_stream(
        NoStream(),
        [{"role": "user", "content": "yaz"}],
        ResearchReport,
        session_id="sess-none",
        stream_field="answer",
    )
    assert report is not None and report.answer == "akışsız"


async def _runtime(db, client_, db_factory_, llm):
    case_id, session_id = await _interactive._start_session(client_, db_factory_)
    case = (await db.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
    row = (
        await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
    ).scalar_one()
    runtime = ResearchRuntime(
        db=db,
        session=row,
        case=case,
        target=None,
        client=llm,
        identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
        pool_items=[],
        stats={"depth": "quick"},
        started=time.monotonic(),
    )
    return runtime, session_id


@pytest.mark.asyncio
async def test_synthesize_report_streams_tokens(db_factory, client):
    payload = json.dumps(
        {
            "answer": "## Rapor\nKaynaklı sonuç.",
            "citations": [{"label": "GH", "url": "https://github.com/keremboz"}],
        }
    )
    async with db_factory() as db:
        runtime, session_id = await _runtime(db, client, db_factory, _llm())
        queue = research_broadcaster.subscribe(session_id)
        with respx.mock() as mock:
            mock.post(URL).mock(
                return_value=httpx.Response(
                    200, content=_sse([payload[i : i + 9] for i in range(0, len(payload), 9)])
                )
            )
            answer, citations = await _synthesize_report(
                runtime, pool_rows=[], pool_text="", bio_markdown="", stop_reason="test"
            )
    assert answer.startswith("## Rapor")
    assert citations and citations[0]["url"] == "https://github.com/keremboz"
    tokens = "".join(e["data"]["text"] for e in _drain(queue) if e["type"] == "research.token")
    assert tokens == "## Rapor\nKaynaklı sonuç."


@pytest.mark.asyncio
async def test_synthesize_bio_streams_summary(db_factory, client):
    payload = json.dumps(
        {"headline": "Kerem Boz", "summary": "Ankara'da yazılım geliştirici.", "confidence": 0.6}
    )
    async with db_factory() as db:
        runtime, session_id = await _runtime(db, client, db_factory, _llm())
        queue = research_broadcaster.subscribe(session_id)
        with respx.mock() as mock:
            mock.post(URL).mock(
                return_value=httpx.Response(
                    200, content=_sse([payload[i : i + 11] for i in range(0, len(payload), 11)])
                )
            )
            markdown, structured = await _synthesize_bio(runtime, pool_rows=[], pool_text="x")
    assert structured["mode"] == "llm" and "Ankara" in markdown
    tokens = "".join(e["data"]["text"] for e in _drain(queue) if e["type"] == "research.token")
    assert tokens == "Ankara'da yazılım geliştirici."
    assert BioDraft.model_validate(json.loads(payload)).headline == "Kerem Boz"


class _BrokenClient:
    async def stream(self, *args, **kwargs):
        for event in ():
            yield event
        raise RuntimeError("llm yok")

    async def complete(self, *args, **kwargs):
        raise RuntimeError("llm yok")


@pytest.mark.asyncio
async def test_synthesize_bio_does_not_rewrite_user_confirmed_row(db_factory, client):
    """AI, kullanıcının onayladığı biyografiyi ezmez; yeni öneri kaydı açar."""
    from showis.db.models import PoolItem
    from showis.services.pool import create_item

    async with db_factory() as db:
        runtime, _ = await _runtime(db, client, db_factory, _llm())
        confirmed, _ = await create_item(
            db,
            case_id=runtime.case.id,
            kind="bio",
            label="Onaylı biyografi",
            value="ONAYLI BIO",
            source="user",
            status="confirmed",
        )
        await db.commit()
        runtime.reasoner_client = _BrokenClient()
        markdown, _ = await _synthesize_bio(runtime, pool_rows=[], pool_text="")
        assert markdown
        bios = (
            (await db.execute(select(PoolItem).where(PoolItem.kind == "bio"))).scalars().all()
        )
        kept = next(row for row in bios if row.id == confirmed.id)
        assert kept.value == "ONAYLI BIO" and kept.status == "confirmed"
        assert any(
            row.id != confirmed.id and row.status in {"proposed", "candidate"} for row in bios
        )

"""Araştırma oturumu SSE olay yükleri (interaktif AI modu)."""

from pydantic import BaseModel, Field


class ResearchToolEvent(BaseModel):
    """`research.tool_start` / `research.tool_end` yükü."""

    session_id: str
    agent: int | None = None
    role: str = ""
    tool: str
    summary: str = ""
    ok: bool | None = None  # tool_start'ta None
    error: str | None = None


class ResearchToken(BaseModel):
    """`research.token`: ajanın canlı anlatım parçası (toplu gönderilir)."""

    session_id: str
    agent: int | None = None
    text: str


class ResearchThinking(BaseModel):
    """`research.thinking_summary`: model düşünme özeti."""

    session_id: str
    agent: int | None = None
    text: str


class ResearchQuestion(BaseModel):
    """`research.question`: ajanın kullanıcıya sorusu (oturum awaiting_user)."""

    session_id: str
    question_id: str
    question: str
    options: list[str] = Field(default_factory=list)


class ResearchProposal(BaseModel):
    """`research.proposal`: onay bekleyen havuz önerisi (proposed/candidate)."""

    session_id: str
    item_id: str
    kind: str
    label: str
    value: str
    status: str
    confidence: float = 0.0
    source_url: str | None = None


class ResearchSteer(BaseModel):
    """`research.steer`: analistin çalışırken verdiği yönlendirme."""

    session_id: str
    text: str
    queued: bool = True

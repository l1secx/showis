from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .enums import ObservationKind, Platform, RiskTier


class Evidence(BaseModel):
    """Kanıt: kaynak URL, ham içerik referansı, hash, zaman"""

    id: str
    source_url: str
    kind: str  # "html" | "json" | "text" | "screenshot"
    content_hash: str  # sha256 hex
    storage_key: str | None = None  # MinIO key
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    http_status: int | None = None
    user_agent: str | None = None
    truncated: bool = False
    notes: str | None = None


class Observation(BaseModel):
    """Connector'ın ham çıktısı — normalize edilmeden önce"""

    id: str
    connector: str
    platform: Platform
    kind: ObservationKind
    subject: str  # aranan anahtar (username, query, email)
    url: str | None = None
    tier: RiskTier
    data: dict[str, Any] = Field(default_factory=dict)  # connector'a özgü ham alanlar
    evidence: list[Evidence] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None
    blocked: bool = False  # bot koruması/engelleme durumu

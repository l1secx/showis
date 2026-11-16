from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .enums import EdgeType, EntityType, Platform


class Entity(BaseModel):
    id: str
    type: EntityType
    platform: Platform
    label: str  # @username veya isim
    url: str | None = None
    # name, bio, location, company, rank, avatar_url...
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Edge(BaseModel):
    id: str
    type: EdgeType
    source_entity_id: str
    target_entity_id: str
    confidence: float = 0.0
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

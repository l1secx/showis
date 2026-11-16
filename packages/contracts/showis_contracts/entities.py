import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .enums import EdgeType, EntityType, Platform

#: DB şeması (migration 0005): `entities.id`, `edges.*_entity_id`, `findings.*` VARCHAR(96)
IDENTITY_ID_MAX_LENGTH = 96
#: `findings.id = "finding:" + entity_id` türetmesi
FINDING_ID_PREFIX = "finding:"


def _short_hash(value: str, length: int) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def make_entity_id(
    platform: str, identifier: str, *, max_length: int = IDENTITY_ID_MAX_LENGTH
) -> str:
    """`<platform>:<identifier>` — DB sınırını aşarsa deterministik hash.

    Uzun kimlikler (ör. HackTheBox UUID'si + prefix) `entities.id` kolonunu
    taşırıp taramayı `unexpected_failure` ile düşürebiliyordu (T-346). Sınırı
    aşan kimlikler için `sha1(identifier)[:16]` kullanılır: deterministik
    (aynı girdi → aynı id, yeniden taramada karar korunur) ve prefix + 16
    karakter her zaman sınırın altında kalır.
    """
    raw = f"{platform}:{identifier}"
    if len(raw) <= max_length:
        return raw
    return f"{platform}:{_short_hash(str(identifier), 16)}"


def make_finding_id(entity_id: str, *, max_length: int = IDENTITY_ID_MAX_LENGTH) -> str:
    """`finding:<entity_id>` — sınırı aşarsa sabit uzunluklu hash.

    Kısa id'ler **değişmez** (geriye uyum: mevcut `finding:` kayıtları ve SEC-15
    kullanıcı kararları korunur); yalnız `finding:` + entity_id sınırı aşarsa
    `finding:` + `sha1(entity_id)[:24]` üretilir (deterministik).
    """
    raw = f"{FINDING_ID_PREFIX}{entity_id}"
    if len(raw) <= max_length:
        return raw
    return f"{FINDING_ID_PREFIX}{_short_hash(str(entity_id), 24)}"


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

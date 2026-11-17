from datetime import datetime

from pydantic import BaseModel, Field

from .enums import FindingStatus, Platform


class ConfidenceBreakdown(BaseModel):
    username_exact_match: float = 0.0
    #: Tek platformda birebir handle eşleşmesi (handle-only hedef); çok platformlu
    #: eşleşme `username_exact_match`'e aittir, ikisi aynı kanıt için verilmez.
    handle_exact: float = 0.0
    name_similarity: float = 0.0
    cross_link: float = 0.0
    bio_location_company: float = 0.0
    commit_email_or_pgp: float = 0.0
    avatar_exact_hash: float = 0.0
    contradiction: float = 0.0
    total: float = 0.0


class Finding(BaseModel):
    id: str
    case_id: str
    scan_id: str
    entity_id: str
    platform: Platform
    url: str | None = None
    label: str
    status: FindingStatus = FindingStatus.CANDIDATE
    score: float = 0.0
    breakdown: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    explanation: str = ""  # LLM açıklaması (kanıtsız iddia içermez)
    evidence_ids: list[str] = Field(default_factory=list)
    verified_by: str | None = None
    verified_at: datetime | None = None
    #: Sahte/atıl hesap sinyali (0..1; 1 = güvenilir/canlı profil).
    #: Güven skoruna (`score`) DAHİL EDİLMEZ; ayrı bir sinyaldir (ROADMAP 4.1).
    authenticity: float | None = None
    #: Authenticity skorunun deterministik gerekçeleri (Türkçe, LLM'siz)
    authenticity_reasons: list[str] = Field(default_factory=list)

from pydantic import BaseModel, Field

from .enums import Platform


class SearchQuery(BaseModel):
    query: str
    platform: Platform
    rationale: str = ""
    priority: int = 5


class QueryPlan(BaseModel):
    queries: list[SearchQuery] = Field(default_factory=list)


class UsernameVariant(BaseModel):
    username: str
    pattern: str  # "first.last" | "firstlast" | "nickname" | "manual"
    confidence: float = 0.0


class VariantSet(BaseModel):
    variants: list[UsernameVariant] = Field(default_factory=list)
    # "Ahmet Yılmaz", "ahmet.yilmaz", "A. Yılmaz"
    name_forms: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    display_name: str | None = None
    username: str | None = None
    bio: str | None = None
    location: str | None = None
    company: str | None = None
    links: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)  # çıkarılan ham ipuçları

from typing import Any, Protocol

from pydantic import BaseModel, Field
from showis_contracts import Platform

from .normalize import extract_platform_from_url, normalize_url


class SearchHit(BaseModel):
    url: str
    title: str
    snippet: str
    rank: int
    provider: str
    raw: dict[str, Any] = Field(default_factory=dict)
    platform: Platform = Platform.WEB
    subject_id: str | None = None

    def model_post_init(self, __context: Any) -> None:
        self.url = normalize_url(self.url)
        plat, subj = extract_platform_from_url(self.url)
        self.platform = plat
        self.subject_id = subj


class SearchProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        site: str | None = None,
    ) -> list[SearchHit]:
        """Arama motorunda arama yapar ve SearchHit listesi döner."""
        ...

import pytest
from showis_connectors.websearch import HybridSearchProvider, SearchHit, SearchProvider
from showis_contracts import Platform


class DummyProvider(SearchProvider):
    def __init__(self, name: str, hits: list[SearchHit], should_fail: bool = False):
        self.name = name
        self.hits = hits
        self.should_fail = should_fail

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        site: str | None = None,
    ) -> list[SearchHit]:
        if self.should_fail:
            raise RuntimeError("Provider down")
        return self.hits[:limit]


@pytest.mark.asyncio
async def test_hybrid_search_dedup_and_fallback():
    p1 = DummyProvider(
        name="brave",
        hits=[
            SearchHit(
                url="https://github.com/johndoe",
                title="John Doe - GitHub",
                snippet="Software engineer",
                rank=1,
                provider="brave",
            ),
        ],
    )
    p2 = DummyProvider(
        name="ddg",
        hits=[
            # Duplicate URL with trailing slash
            SearchHit(
                url="https://github.com/johndoe/",
                title="John Doe (GitHub Profile)",
                snippet="Engineer",
                rank=1,
                provider="ddg",
            ),
            # New hit
            SearchHit(
                url="https://tryhackme.com/p/johndoe",
                title="John Doe - TryHackMe",
                snippet="Cybersecurity rank",
                rank=2,
                provider="ddg",
            ),
        ],
    )

    hybrid = HybridSearchProvider(providers=[p1, p2], min_results_before_fallback=2)
    results = await hybrid.search("johndoe")

    assert len(results) == 2
    urls = [r.url for r in results]
    assert "https://github.com/johndoe" in urls
    assert "https://tryhackme.com/p/johndoe" in urls


@pytest.mark.asyncio
async def test_hybrid_search_graceful_failover():
    failing_provider = DummyProvider(name="broken", hits=[], should_fail=True)
    backup_provider = DummyProvider(
        name="backup",
        hits=[
            SearchHit(
                url="https://gitlab.com/johndoe",
                title="John Doe - GitLab",
                snippet="DevOps",
                rank=1,
                provider="backup",
            )
        ],
    )

    hybrid = HybridSearchProvider(providers=[failing_provider, backup_provider])
    results = await hybrid.search("johndoe")

    assert len(results) == 1
    assert results[0].platform == Platform.GITLAB

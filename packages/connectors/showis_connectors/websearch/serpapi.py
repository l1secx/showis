from showis_engine.http import HttpClient

from .base import SearchHit, SearchProvider


class SerpApiSearchProvider(SearchProvider):
    name: str = "serpapi"

    def __init__(self, api_key: str, http_client: HttpClient | None = None):
        self.api_key = api_key
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        site: str | None = None,
    ) -> list[SearchHit]:
        full_query = f"site:{site} {query}" if site else query
        url = "https://serpapi.com/search"
        params = {
            "q": full_query,
            "api_key": self.api_key,
            "num": str(limit),
            "engine": "google",
        }

        resp = await self.http_client.get(url, params=params, check_robots=False)
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchHit] = []
        organic_results = data.get("organic_results", [])
        for rank, item in enumerate(organic_results, start=1):
            results.append(
                SearchHit(
                    url=item.get("link", ""),
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    rank=rank,
                    provider=self.name,
                    raw=item,
                )
            )

        return results

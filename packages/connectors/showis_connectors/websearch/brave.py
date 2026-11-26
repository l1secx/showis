from showis_engine.http import DEFAULT_UA, HttpClient

from .base import SearchHit, SearchProvider


class BraveSearchProvider(SearchProvider):
    name: str = "brave"

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
        url = "https://api.search.brave.com/res/v1/web/search"
        params = {"q": full_query, "count": str(limit)}
        headers = {
            "X-Subscription-Token": self.api_key,
            "Accept": "application/json",
            "User-Agent": DEFAULT_UA,
        }

        resp = await self.http_client.get(
            url, params=params, headers=headers, check_robots=False
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchHit] = []
        web_results = data.get("web", {}).get("results", [])
        for rank, item in enumerate(web_results, start=1):
            results.append(
                SearchHit(
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    snippet=item.get("description", ""),
                    rank=rank,
                    provider=self.name,
                    raw=item,
                )
            )

        return results

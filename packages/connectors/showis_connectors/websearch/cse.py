from showis_engine.http import HttpClient

from .base import SearchHit, SearchProvider


class GoogleCseSearchProvider(SearchProvider):
    name: str = "cse"

    def __init__(self, api_key: str, cx: str, http_client: HttpClient | None = None):
        self.api_key = api_key
        self.cx = cx
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        site: str | None = None,
    ) -> list[SearchHit]:
        full_query = f"site:{site} {query}" if site else query
        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?q={full_query}&key={self.api_key}&cx={self.cx}&num={min(limit, 10)}"
        )

        resp = await self.http_client.get(url, check_robots=False)
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchHit] = []
        items = data.get("items", [])
        for rank, item in enumerate(items, start=1):
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

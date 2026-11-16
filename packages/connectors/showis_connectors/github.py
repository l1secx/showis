import uuid
from typing import Any

from showis_contracts import (
    Edge,
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
)
from showis_engine.http import EvidenceStore, HttpClient

GITHUB_API_BASE = "https://api.github.com"


class GitHubConnector:
    name: str = "github"
    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A
    supported_kinds: set[str] = {
        "search_users",
        "get_user",
        "list_repos",
        "list_events",
        "list_orgs",
        "list_gists",
        "list_gpg",
        "list_followers",
        "search_commits",
        "search_repos_by_user",
    }

    def __init__(
        self,
        token: str | None = None,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
    ):
        self.token = token
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)
        self.evidence_store = evidence_store or EvidenceStore()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        target = ctx.target

        # 1. Direct seed searches
        for seed in ctx.seeds:
            if seed.platform == Platform.GITHUB and seed.username:
                tasks.append(
                    TaskSpec(
                        id=str(uuid.uuid4()),
                        connector=self.name,
                        platform=self.platform,
                        kind="get_user",
                        params={"username": seed.username},
                        priority=1,
                    )
                )
            if seed.email:
                tasks.append(
                    TaskSpec(
                        id=str(uuid.uuid4()),
                        connector=self.name,
                        platform=self.platform,
                        kind="search_commits",
                        params={"email": seed.email},
                        priority=2,
                    )
                )

        # 2. Name query search
        fullname = f"{target.first_name} {target.last_name}".strip()
        q_parts = [f'"{fullname}" in:name']
        if target.hints.get("city"):
            q_parts.append(f'location:"{target.hints["city"]}"')
        if target.hints.get("company"):
            q_parts.append(f'company:"{target.hints["company"]}"')

        query_str = " ".join(q_parts)
        tasks.append(
            TaskSpec(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind="search_users",
                params={"query": query_str},
                priority=3,
            )
        )

        return tasks

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        observations: list[Observation] = []
        obs_id = str(uuid.uuid4())
        kind = task.kind
        params = task.params
        headers = self._headers()

        try:
            if kind == "search_users":
                query = params.get("query", "")
                url = f"{GITHUB_API_BASE}/search/users?q={query}&per_page=30"
                resp = await self.http_client.get(url, headers=headers, check_robots=False)

                if resp.status_code == 200:
                    data = resp.json()
                    ev = self.evidence_store.store_bytes(
                        resp.content,
                        source_url=url,
                        kind="json",
                        http_status=resp.status_code,
                    )
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.SEARCH_RESULT,
                            subject=query,
                            url=url,
                            tier=self.tier,
                            data=data,
                            evidence=[ev],
                        )
                    )
                else:
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.SEARCH_RESULT,
                            subject=query,
                            url=url,
                            tier=self.tier,
                            data={},
                            error=f"GitHub API Error {resp.status_code}: {resp.text}",
                            blocked=resp.status_code == 403,
                        )
                    )

            elif kind == "get_user":
                username = params.get("username", "")
                url = f"{GITHUB_API_BASE}/users/{username}"
                resp = await self.http_client.get(url, headers=headers, check_robots=False)

                if resp.status_code == 200:
                    data = resp.json()
                    ev = self.evidence_store.store_bytes(
                        resp.content,
                        source_url=url,
                        kind="json",
                        http_status=resp.status_code,
                    )
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.PROFILE,
                            subject=username,
                            url=url,
                            tier=self.tier,
                            data=data,
                            evidence=[ev],
                        )
                    )
                else:
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.PROFILE,
                            subject=username,
                            url=url,
                            tier=self.tier,
                            data={},
                            error=f"User {username} fetch error {resp.status_code}",
                            blocked=resp.status_code == 403,
                        )
                    )

            elif kind == "list_repos":
                username = params.get("username", "")
                url = f"{GITHUB_API_BASE}/users/{username}/repos?per_page=100&type=owner"
                resp = await self.http_client.get(url, headers=headers, check_robots=False)
                if resp.status_code == 200:
                    data = resp.json()
                    ev = self.evidence_store.store_bytes(
                        resp.content, source_url=url, kind="json", http_status=resp.status_code
                    )
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.REPOSITORY,
                            subject=username,
                            url=url,
                            tier=self.tier,
                            data={"repos": data},
                            evidence=[ev],
                        )
                    )

            elif kind == "search_commits":
                email = params.get("email", "")
                url = f"{GITHUB_API_BASE}/search/commits?q=author-email:{email}"
                resp = await self.http_client.get(url, headers=headers, check_robots=False)
                if resp.status_code == 200:
                    data = resp.json()
                    ev = self.evidence_store.store_bytes(
                        resp.content, source_url=url, kind="json", http_status=resp.status_code
                    )
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.COMMIT,
                            subject=email,
                            url=url,
                            tier=self.tier,
                            data=data,
                            evidence=[ev],
                        )
                    )

            else:
                # Other kinds (events, orgs, gists, gpg, followers) follow sub-resources
                username = params.get("username", "")
                url = f"{GITHUB_API_BASE}/users/{username}/{kind.replace('list_', '')}"
                resp = await self.http_client.get(url, headers=headers, check_robots=False)
                if resp.status_code == 200:
                    ev = self.evidence_store.store_bytes(
                        resp.content, source_url=url, kind="json", http_status=resp.status_code
                    )
                    observations.append(
                        Observation(
                            id=obs_id,
                            connector=self.name,
                            platform=self.platform,
                            kind=ObservationKind.EVENT,
                            subject=username,
                            url=url,
                            tier=self.tier,
                            data={"items": resp.json()},
                            evidence=[ev],
                        )
                    )

        except Exception as e:
            observations.append(
                Observation(
                    id=obs_id,
                    connector=self.name,
                    platform=self.platform,
                    kind=ObservationKind.PROFILE,
                    subject=task.params.get("username") or task.params.get("query", ""),
                    tier=self.tier,
                    data={},
                    error=str(e),
                )
            )

        return observations

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        entities: list[Entity] = []
        edges: list[Edge] = []

        if obs.error or obs.blocked or not obs.data:
            return entities, edges

        ev_ids = [ev.id for ev in obs.evidence]

        if obs.kind == ObservationKind.PROFILE:
            data = obs.data
            login = data.get("login")
            if not login:
                return entities, edges

            entity_id = f"github:{login}"
            attributes: dict[str, Any] = {
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "location": data.get("location"),
                "blog": data.get("blog"),
                "twitter_username": data.get("twitter_username"),
                "public_email": data.get("email"),
                "avatar_url": data.get("avatar_url"),
                "public_repos": data.get("public_repos"),
                "followers": data.get("followers"),
            }
            entities.append(
                Entity(
                    id=entity_id,
                    type=EntityType.ACCOUNT,
                    platform=Platform.GITHUB,
                    label=login,
                    url=data.get("html_url") or f"https://github.com/{login}",
                    attributes=attributes,
                    evidence_ids=ev_ids,
                )
            )

        elif obs.kind == ObservationKind.SEARCH_RESULT:
            items = obs.data.get("items", [])
            for item in items:
                login = item.get("login")
                if not login:
                    continue
                entity_id = f"github:{login}"
                entities.append(
                    Entity(
                        id=entity_id,
                        type=EntityType.ACCOUNT,
                        platform=Platform.GITHUB,
                        label=login,
                        url=item.get("html_url") or f"https://github.com/{login}",
                        attributes={"avatar_url": item.get("avatar_url")},
                        evidence_ids=ev_ids,
                    )
                )

        return entities, edges

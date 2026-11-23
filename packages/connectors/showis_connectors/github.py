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

        # Hedef ipucundaki e-posta da kesin bir kimlik anahtarıdır: commit
        # aramasında kullanılır (seed e-postasıyla aynı uç).
        hint_email = str(
            target.email or (target.hints or {}).get("email") or ""
        ).strip().lower()
        if hint_email and all(
            str((task.params or {}).get("email") or "").lower() != hint_email
            for task in tasks
        ):
            tasks.append(
                TaskSpec(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=self.platform,
                    kind="search_commits",
                    params={"email": hint_email},
                    priority=2,
                )
            )

        # 2. Identity query search (ad-soyad veya nick)
        # T-377 (SEC-10): ipucu metni guvenilmez; tirnak/operator enjeksiyonu
        # `sanitize_hint` ile notralize edilir (tek kaynak: showis_engine.planner).
        from showis_engine.planner import sanitize_hint

        fullname = f"{target.first_name} {target.last_name}".strip()
        # Nick de kullanici girdisidir: tirnak/operator enjeksiyonu ayni
        # `sanitize_hint` ile notralize edilir (SEC-10).
        nick = sanitize_hint((target.nickname or "").strip().lstrip("@"))
        q_parts: list[str] = []
        if fullname:
            q_parts.append(f'"{fullname}" in:name')
        elif nick:
            # Handle hedefi: ad/soyad yok, nick ile kimlik aramasi (`in:login`).
            q_parts.append(f"{nick} in:login")
        # Ne ad ne nick varsa `"" in:name` gibi bos sorgu uretilmez.
        if q_parts:
            city = sanitize_hint(target.hints.get("city"))
            if city:
                q_parts.append(f'location:"{city}"')
            company = sanitize_hint(target.hints.get("company"))
            if company:
                q_parts.append(f'company:"{company}"')

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

    async def _archived_user(self, username: str) -> Observation | None:
        """404 GitHub profilini Wayback CDX ile tarihsel ipucu olarak kurtarır."""
        import json as _json
        from urllib.parse import quote

        cdx_url = (
            "https://web.archive.org/cdx/search/cdx"
            f"?url=github.com/{quote(username, safe='')}"
            "&output=json&filter=statuscode:200&limit=1"
        )
        try:
            resp = await self.http_client.get(
                cdx_url,
                headers={"Accept": "application/json"},
                check_robots=False,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return None
            rows = _json.loads(resp.content or b"[]")
        except Exception:
            return None
        if not isinstance(rows, list) or len(rows) < 2:
            return None
        try:
            timestamp, original = str(rows[1][0]), str(rows[1][1])
        except (IndexError, TypeError):
            return None
        archive_url = f"https://web.archive.org/web/{timestamp}/{original}"
        ev = self.evidence_store.store_bytes(
            resp.content,
            source_url=cdx_url,
            kind="json",
            http_status=resp.status_code,
        )
        return Observation(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind=ObservationKind.PROFILE,
            subject=username,
            url=archive_url,
            tier=self.tier,
            data={
                "login": username,
                "name": username,
                "html_url": archive_url,
                "archived": True,
                "archive_timestamp": timestamp,
                "confidence_hint": 0.65,
                "exists": True,
            },
            evidence=[ev],
        )

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
                    archived = (
                        await self._archived_user(username)
                        if resp.status_code == 404
                        else None
                    )
                    if archived is not None:
                        observations.append(archived)
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
                "username": login,
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
                "archived": data.get("archived"),
                "archive_timestamp": data.get("archive_timestamp"),
                "confidence_hint": data.get("confidence_hint"),
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

"""GitHub commit aramasi — **e-posta pivotu** (B2).

E-posta seed'i bugune kadar yalniz Gravatar hash'ine giriyordu. Bu connector,
yapilandirilmis bir GitHub token'i varsa `search/commits?q=author-email:"..."` ile
o e-postayla commit atmis hesaplarin **login**'lerini bulur ve her birini
profil entity'sine cevirir. Zincir: `e-posta -> commit -> login -> (Keybase
proof | diger platformlar)`.

Ilkeler:

* **Fail-closed:** token yoksa gorev uretilmez (anonim arama hiz limiti cok
  dusuk ve sonuc kalitesi belirsiz); `logger.info` ile nedeni bir kez yazilir.
* **PII:** e-posta adresi log'a yazilmaz; yalnizca kisaltilmis sha256 etiketi
  (`email_tag`) kullanilir. Kanit/observation URL'sinde de adres maskelenir.
* **Rate limit:** 403/429 istisna firlatmaz; `error="github_email_rate_limited"`
  observation'i uretir (tarama devam eder, diger connector'lar etkilenmez).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any
from urllib.parse import quote

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
    make_entity_id,
)
from showis_engine.http import EvidenceStore, HttpClient

logger = logging.getLogger("showis.publicapi")

GITHUB_SEARCH_COMMITS = "https://api.github.com/search/commits"
#: Tek sayfada istenen commit sayisi (token limitini korumak icin kucuk).
PER_PAGE = 10
#: Rate limit / yetki hatalari: istisna degil, nazik hata observation'i.
RATE_LIMIT_STATUSES = frozenset({403, 429})
ERROR_RATE_LIMITED = "github_email_rate_limited"


def email_tag(email: str) -> str:
    """E-postanin log/URL icin kisa, geri donusturulemez etiketi."""
    digest = hashlib.sha256(str(email or "").strip().lower().encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


class GitHubEmailConnector:
    """E-posta -> GitHub commit -> yazar login pivotu (token zorunlu)."""

    name: str = "github_email"
    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A
    supported_kinds: set[str] = {"commits_by_email"}

    def __init__(
        self,
        token: str | None = None,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
        per_page: int = PER_PAGE,
    ):
        self.token = (token or "").strip() or None
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)
        self.evidence_store = evidence_store or EvidenceStore()
        self.per_page = max(1, int(per_page))

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _task_for(self, email: str, priority: int = 2) -> TaskSpec:
        return TaskSpec(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind="commits_by_email",
            #: `email` sorgu icin gerekli; `email_tag` log/UI icin PII'siz etiket.
            params={"email": email, "email_tag": email_tag(email)},
            priority=priority,
        )

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        """Her benzersiz e-posta seed'i icin tek gorev; token yoksa bos liste."""
        if not self.token:
            logger.info(
                "github_email: GitHub token yok — e-posta commit aramasi planlanmadi "
                "(fail-closed)"
            )
            return []

        tasks: list[TaskSpec] = []
        seen: set[str] = set()
        for seed in ctx.seeds:
            email = str(seed.email or "").strip()
            key = email.lower()
            if not email or key in seen:
                continue
            # Minimal dogrulama: sorguya girecek deger tek satir/@ icermeli.
            if "@" not in email or any(ch in email for ch in "\r\n\" "):
                logger.info(
                    "github_email: gecersiz e-posta seed'i atlandi (%s)", email_tag(email)
                )
                continue
            seen.add(key)
            tasks.append(self._task_for(email))
        return tasks

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _search_url(self, email: str) -> str:
        return (
            f"{GITHUB_SEARCH_COMMITS}"
            f'?q=author-email:"{quote(email, safe="@")}"&per_page={self.per_page}'
        )

    @staticmethod
    def _redacted_url(email: str, per_page: int = PER_PAGE) -> str:
        """Kanit/observation URL'si: adres yerine etiket (PII sizmasin)."""
        return (
            f"{GITHUB_SEARCH_COMMITS}"
            f"?q=author-email:<{email_tag(email)}>&per_page={per_page}"
        )

    def _obs(
        self,
        subject: str,
        *,
        url: str | None = None,
        data: dict[str, Any] | None = None,
        evidence: list[Any] | None = None,
        error: str | None = None,
    ) -> Observation:
        return Observation(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind=ObservationKind.PROFILE,
            subject=subject,
            url=url,
            tier=self.tier,
            data=data or {},
            evidence=evidence or [],
            error=error,
        )

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        email = str(task.params.get("email") or "").strip()
        tag = str(task.params.get("email_tag") or email_tag(email))
        if not email:
            return [self._obs(tag, error="email parametresi eksik")]

        url = self._search_url(email)
        redacted = self._redacted_url(email, self.per_page)
        try:
            resp = await self.http_client.get(
                url,
                headers=self._headers(),
                check_robots=False,
                follow_redirects=True,
                # Rate limit kisa vadede gecmez: tekrar denemek kotayi bosa
                # harcar; hata nazikce raporlanir (fail-closed).
                max_retries=1,
            )
        except Exception as exc:  # ag hatasi: tarama devam etsin
            logger.warning("github_email: istek hatasi (%s): %s", tag, type(exc).__name__)
            return [
                self._obs(
                    tag,
                    url=redacted,
                    error=f"github_email_request_failed:{type(exc).__name__}",
                )
            ]

        if resp.status_code in RATE_LIMIT_STATUSES:
            logger.warning(
                "github_email: GitHub %s dondurdu (%s) — rate limit/yetki",
                resp.status_code,
                tag,
            )
            return [self._obs(tag, url=redacted, error=ERROR_RATE_LIMITED)]

        if resp.status_code != 200:
            return [
                self._obs(tag, url=redacted, error=f"github_email_http_{resp.status_code}")
            ]

        try:
            payload = resp.json()
        except Exception:
            return [self._obs(tag, url=redacted, error="github_email_json_error")]

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return [self._obs(tag, url=redacted, error="github_email_bad_payload")]

        evidence: list[Any] = []
        if resp.content:
            evidence.append(
                self.evidence_store.store_bytes(
                    resp.content,
                    # URL'de e-posta yok: adres yerine etiket saklanir.
                    source_url=redacted,
                    kind="json",
                    http_status=resp.status_code,
                )
            )

        observations: list[Observation] = []
        seen_logins: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            author = item.get("author") or {}
            login = str(author.get("login") or "").strip()
            if not login or login.lower() in seen_logins:
                continue
            seen_logins.add(login.lower())
            commit_author = ((item.get("commit") or {}).get("author")) or {}
            observations.append(
                self._obs(
                    login,
                    url=redacted,
                    data={
                        "username": login,
                        "url": item.get("html_url"),
                        "name": commit_author.get("name"),
                        # Commit'in yazari bu e-postayi kullanmis (kanit).
                        "public_email": commit_author.get("email"),
                        "source": "github_commits",
                        "email_tag": tag,
                    },
                    evidence=evidence,
                )
            )

        if not observations:
            # Arama basarili ama eslesme yok: "bulunamadi" observation'i.
            observations.append(
                self._obs(tag, url=redacted, data={"exists": False, "email_tag": tag})
            )
        return observations

    # ------------------------------------------------------------------
    # Normalize
    # ------------------------------------------------------------------

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        if obs.error or obs.blocked or not obs.data:
            return [], []
        login = str(obs.data.get("username") or "").strip().lstrip("@")
        if not login:
            return [], []

        attributes: dict[str, Any] = {
            "username": login,
            "source": "github_commits",
        }
        for key in ("name", "url", "public_email"):
            if obs.data.get(key):
                attributes[key] = obs.data[key]
        if obs.data.get("url"):
            # Diger platformlarin (orn. Keybase proof) bu profile baglanmasi icin
            attributes["github_link"] = obs.data["url"]

        entity = Entity(
            id=make_entity_id(self.platform.value, login),
            type=EntityType.ACCOUNT,
            platform=self.platform,
            label=login,
            url=obs.data.get("url"),
            attributes=attributes,
            evidence_ids=[ev.id for ev in obs.evidence],
        )
        return [entity], []

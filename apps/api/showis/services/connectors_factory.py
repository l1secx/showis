"""Varsayilan connector setini BYOK ayarlarindan olusturur.

Ayarlar `settings` tablosundan okunur; anahtarlar Fernet ile sifreli saklanir
ve hicbir zaman log'a yazilmaz. Saglayici yoksa connector yine de kurulur —
ilgili gorevler calisma zamaninda nazikce `error` observation uretir
(uygulama anahtarsiz da acilir).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from showis.db.models import Setting
from showis.services.search_guard import SearchProviderSanitizer
from showis_connectors import (
    BitbucketConnector,
    BlueskyConnector,
    ChessConnector,
    CodebergConnector,
    CodeforcesConnector,
    DevtoConnector,
    DockerHubConnector,
    DomainReconConnector,
    GitHubConnector,
    GitLabConnector,
    GravatarConnector,
    HackerNewsConnector,
    HackTheBoxConnector,
    HuggingFaceConnector,
    IpReconConnector,
    KeybaseConnector,
    KitsuConnector,
    LeetCodeConnector,
    MastodonConnector,
    MediumConnector,
    NpmConnector,
    OpenAlexConnector,
    PypiConnector,
    RedditConnector,
    SiteCheckConnector,
    SiteCheckSearchConnector,
    SoundCloudConnector,
    StackOverflowConnector,
    SteamConnector,
    TryHackMeConnector,
    WordPressConnector,
)
from showis_connectors.websearch import (
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    GoogleCseSearchProvider,
    HybridSearchProvider,
    SearchProvider,
    SerpApiSearchProvider,
    WebSearchConnector,
)
from showis_engine.browser import BrowserClient
from showis_engine.policy import SSRFViolationError, guard_url
from showis_llm import LLMClient
from showis_llm.base_url import BaseUrlError, validate_base_url
from showis_settings.crypto import decrypt_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.connectors_factory")

#: LLM varsayilan adresi (politika dogrulamasindan gecer)
DEFAULT_LLM_BASE_URL = "https://opencode.ai/inference/go/openai/v1"


def _secret(setting: Setting | None) -> str:
    if not setting or not setting.secret_encrypted:
        return ""
    try:
        return str(decrypt_secret(setting.secret_encrypted))
    except Exception as exc:
        logger.warning("Saglayici sir cozulemedi (%s): %s", setting.provider, exc)
        return ""


def _pick_search_provider(settings: dict[str, Setting]) -> SearchProviderSanitizer:
    """Yapilandirilmis arama saglayicilarini hibrit fallback zinciriyle dondurur."""
    active_providers: list[SearchProvider] = []

    brave = settings.get("brave")
    if brave and brave.enabled and _secret(brave):
        active_providers.append(BraveSearchProvider(api_key=_secret(brave)))

    serpapi = settings.get("serpapi")
    if serpapi and serpapi.enabled and _secret(serpapi):
        active_providers.append(SerpApiSearchProvider(api_key=_secret(serpapi)))

    cse = settings.get("cse")
    if cse and cse.enabled:
        key = _secret(cse)
        cx = (cse.config or {}).get("cx")
        if key and cx:
            active_providers.append(GoogleCseSearchProvider(api_key=key, cx=str(cx)))

    # Anahtarsiz son care: DDG Lite
    ddg = DuckDuckGoSearchProvider()
    if active_providers:
        active_providers.append(ddg)
        return SearchProviderSanitizer(HybridSearchProvider(active_providers), "hybrid")
    # SEC-08: saglayici hatalari sir/URL sizdirmadan yeniden uretilir.
    return SearchProviderSanitizer(ddg, "duckduckgo")


def build_llm_client_from_settings(settings: dict[str, Setting]) -> LLMClient | None:
    """BYOK ayarlarından veya ortam değişkenlerinden LLM istemcisi oluşturur.

    Güvenlik (SEC-02/03): `base_url` allowlist + SSRF kontrolünden geçmeden
    istemci kurulmaz; sır, doğrulanmamış bir host'a gönderilmez.
    """
    llm_setting = settings.get("llm")
    api_key = ""
    config: dict[str, Any] = {}
    if llm_setting and llm_setting.enabled:
        api_key = _secret(llm_setting)
        config = llm_setting.config or {}

    # Ortam degiskeni fallback
    if not api_key:
        api_key = (
            os.getenv("OPENCODE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("LLM_API_KEY")
            or ""
        )

    if not api_key:
        return None

    raw_base_url = (
        config.get("base_url")
        or os.getenv("LLM_BASE_URL")
        or DEFAULT_LLM_BASE_URL
    )
    try:
        base_url = validate_base_url(raw_base_url)
        guard_url(base_url)
    except (BaseUrlError, SSRFViolationError) as exc:
        logger.warning(
            "LLM base_url politika disi (%s); LLM devre disi — saglayiciyi yeniden yapilandirin",
            type(exc).__name__,
        )
        return None

    model = str(config.get("model") or os.getenv("LLM_MODEL") or "minimax-m3.1-flash")
    if len(model) > 128:
        logger.warning("LLM model adi cok uzun; varsayilana donuluyor")
        model = "minimax-m3.1-flash"
    try:
        timeout = float(config.get("timeout_seconds", 30.0))
    except (TypeError, ValueError):
        timeout = 30.0
    timeout = min(max(timeout, 1.0), 120.0)

    return LLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        session_id=str(uuid.uuid4()),
        timeout_seconds=timeout,
    )


async def build_llm_client(db: AsyncSession) -> LLMClient | None:
    res = await db.execute(select(Setting))
    settings = {s.provider: s for s in res.scalars().all()}
    return build_llm_client_from_settings(settings)


async def build_connectors(db: AsyncSession) -> list[Any]:
    res = await db.execute(select(Setting))
    settings = {s.provider: s for s in res.scalars().all()}

    github_setting = settings.get("github")
    github_token = _secret(github_setting) if github_setting and github_setting.enabled else ""

    search_provider = _pick_search_provider(settings)

    browser_client = BrowserClient(
        os.getenv("BROWSER_SERVICE_URL", "http://browser:8081")
    )

    connectors: list[Any] = [
        # Isim/okul aramasi once calisir: kullanici sadece ad-soyad verse de
        # Bulgular'a ipucu (lead) sonuclari duser.
        WebSearchConnector(search_provider=search_provider),
        GitHubConnector(token=github_token or None),
        TryHackMeConnector(
            browser_client=browser_client,
            search_provider=search_provider,
        ),
        HackTheBoxConnector(
            browser_client=browser_client,
            search_provider=search_provider,
        ),
        # Herkese acik profil connector'lari (anahtarsiz, Tier A/B)
        GitLabConnector(),
        KeybaseConnector(),
        RedditConnector(),
        MediumConnector(),
        LeetCodeConnector(),
        CodeforcesConnector(),
        SoundCloudConnector(),
        DevtoConnector(),
        DockerHubConnector(),
        HackerNewsConnector(),
        # T-308: anahtarsiz public API connector'lari (hizli, O1'de de kosar)
        StackOverflowConnector(),
        BitbucketConnector(),
        MastodonConnector(),
        CodebergConnector(),
        NpmConnector(),
        PypiConnector(),
        OpenAlexConnector(),
        DomainReconConnector(),
        IpReconConnector(),
        ChessConnector(),
        SteamConnector(),
        GravatarConnector(),
        BlueskyConnector(),
        HuggingFaceConnector(),
        KitsuConnector(),
        WordPressConnector(),
        # Username-enumeration motoru (cok-platform varlik kontrolu)
        SiteCheckConnector(),
        # Site-check + web arama connector'i (isim/soyisim bazli tarama)
        SiteCheckSearchConnector(search_provider=search_provider),
    ]
    return connectors

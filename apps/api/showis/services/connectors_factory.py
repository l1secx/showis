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

from showis.db.models import AIProvider, Setting
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
    LinkFollowConnector,
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
    JinaDuckDuckGoSearchProvider,
    SearchProvider,
    SerpApiSearchProvider,
    WebSearchConnector,
)
from showis_engine.browser import BrowserClient
from showis_engine.http import HttpClient
from showis_engine.policy import SSRFViolationError, guard_url
from showis_llm import LLMClient
from showis_llm.base_url import BaseUrlError, validate_base_url
from showis_llm.presets import ROLE_TIMEOUTS, detect_api_style
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

    # Anahtarsiz son care: Jina Reader üzerinden DDG Lite, sonra doğrudan DDG.
    # Doğrudan DDG bazı ağlarda bot/ISP engeline takılır; Jina HTML proxy'si
    # aynı sonuç sayfasını okur.
    active_providers.extend(
        [
            JinaDuckDuckGoSearchProvider(),
            # Doğrudan DDG bazı ağlarda timeout'a düşer; hızlı başarısız ol.
            DuckDuckGoSearchProvider(
                http_client=HttpClient(timeout=8.0, enable_ssrf_guard=True)
            ),
        ]
    )
    # SEC-08: saglayici hatalari sir/URL sizdirmadan yeniden uretilir.
    return SearchProviderSanitizer(HybridSearchProvider(active_providers), "hybrid")


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


def _build_llm_client(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    role: str | None = None,
    thinking_budget: int | None = None,
) -> LLMClient | None:
    try:
        validated = validate_base_url(base_url)
        guard_url(validated)
    except (BaseUrlError, SSRFViolationError) as exc:
        logger.warning(
            "LLM base_url politika disi (%s); LLM devre disi — saglayiciyi yeniden yapilandirin",
            type(exc).__name__,
        )
        return None
    if len(model) > 128:
        logger.warning("LLM model adi cok uzun; varsayilana donuluyor")
        model = "minimax-m3.1-flash"
    timeout = min(max(float(timeout), 1.0), 180.0)
    return LLMClient(
        base_url=validated,
        api_key=api_key,
        model=model,
        session_id=str(uuid.uuid4()),
        timeout_seconds=timeout,
        role=role,
        thinking_budget=thinking_budget,
    )


def build_llm_client_from_ai_provider(
    provider: AIProvider, model_override: str | None = None, role: str | None = None
) -> LLMClient | None:
    """Aktif çoklu profilden istemci kurar; sır çözülemezse None."""
    if not provider.enabled or not provider.secret_encrypted:
        return None
    try:
        api_key = str(decrypt_secret(provider.secret_encrypted) or "")
    except Exception as exc:  # noqa: BLE001 - bozuk sır LLM'i bloklar
        logger.warning("AI provider siri cozulemedi (%s): %s", provider.id, exc)
        return None
    if not api_key:
        return None
    config = provider.config or {}
    base_url = str(config.get("base_url") or DEFAULT_LLM_BASE_URL)
    model = str(config.get("model") or "minimax-m3.1-flash")
    allowed = config.get("models")
    if model_override:
        if not isinstance(allowed, list) or model_override in allowed:
            model = model_override
        else:
            logger.warning(
                "Secilen model profil listesinde yok; varsayilan kullanilir (%s)",
                provider.id,
            )
    # Rol modeli: açık model seçimi (override) rolü ezer.
    roles = provider.roles if isinstance(provider.roles, dict) else {}
    role_model = str(roles.get(role) or "").strip() if role else ""
    if role_model and not model_override:
        model = role_model
    try:
        timeout = float(config.get("timeout_seconds", 30.0))
    except (TypeError, ValueError):
        timeout = 30.0
    thinking_budget: int | None = None
    if role:
        # Rol zaman aşımı: reasoner 180 sn, extractor 30 sn (profil süresinden küçükse yükselt).
        role_timeout = ROLE_TIMEOUTS.get(role, timeout)
        timeout = max(timeout, role_timeout) if role in ("planner", "reasoner") else role_timeout
        if role == "reasoner" and detect_api_style(base_url) == "anthropic":
            thinking_budget = 4096
    return _build_llm_client(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        role=role,
        thinking_budget=thinking_budget,
    )


async def build_llm_client(
    db: AsyncSession,
    provider_id: str | None = None,
    model_override: str | None = None,
    role: str | None = None,
) -> LLMClient | None:
    """Aktif çoklu profili, yoksa eski tek `llm` ayarını kullanır."""
    try:
        query = select(AIProvider).where(AIProvider.enabled.is_(True))
        if provider_id:
            query = query.where(AIProvider.id == provider_id)
        else:
            query = query.where(AIProvider.is_active.is_(True))
        provider = (await db.execute(query)).scalars().first()
        if provider is not None:
            # Seçili/aktif profil bozuksa başka bir sağlayıcıya sessizce düşme.
            return build_llm_client_from_ai_provider(
                provider, model_override=model_override, role=role
            )
        if provider_id:
            return None
    except Exception as exc:  # noqa: BLE001 - migration öncesi/bozuk tablo taramayı bloklamasın
        logger.warning(
            "AI provider tablosu okunamadi (%s); eski ayara dusuluyor",
            type(exc).__name__,
        )

    res = await db.execute(select(Setting))
    settings = {s.provider: s for s in res.scalars().all()}
    return build_llm_client_from_settings(settings)


async def build_search_provider(db: AsyncSession) -> SearchProviderSanitizer:
    """Araştırma ajanı için hibrit arama zinciri (BYOK + DDG fallback)."""
    res = await db.execute(select(Setting))
    settings = {s.provider: s for s in res.scalars().all()}
    return _pick_search_provider(settings)


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
        # Linktree/about.me/beacons gibi bağlantı toplayıcılarını izler.
        LinkFollowConnector(),
    ]
    return connectors

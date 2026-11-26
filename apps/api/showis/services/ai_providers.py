"""Çoklu AI sağlayıcı profili — doğrulama, sır yönetimi ve bağlantı testi.

Güvenlik kuralları eski `settings` BYOK yolundan devralınır:

- `base_url` yalnız allowlist'teki host'lara gider (`validate_base_url`).
- `base_url` değişirse o profilin sırrı geçersiz kılınır; sır yeni
  istemci kontrollü host'a taşınmaz.
- Hata yanıtlarında sağlayıcı gövdesi/URL query'si dönmez.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from showis.core.redact import provider_error_message, sanitize_error
from showis.db.models import AIProvider
from showis_engine.policy import SSRFViolationError, guard_url
from showis_llm.base_url import BaseUrlError, validate_base_url
from showis_llm.presets import (
    ANTHROPIC_DEFAULT_ROLES,
    PRESETS,
    ROLES,
    detect_api_style,
    get_preset,
    model_capabilities,
)
from showis_settings.crypto import decrypt_secret, encrypt_secret, mask_secret
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.ai_providers")

MAX_NAME = 64
MAX_MODEL = 128
MAX_SECRET = 512
MAX_CONFIG_KEYS = 16
MAX_CONFIG_VALUE = 512
#: `preset`, eski `settings(provider="llm")` kaydından taşınan config'de kalabilir;
#: kolonla aynı değeri taşır ve zararsızdır (geriye dönük uyumluluk).
ALLOWED_CONFIG_KEYS = {
    "base_url",
    "model",
    "models",
    "preset",
    "timeout_seconds",
    "temperature",
    "max_tokens",
}
MAX_MODELS = 200
DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
DEFAULT_MODEL = "minimax-m3.1-flash"
MAX_TIMEOUT = 120.0
MIN_TIMEOUT = 1.0

USER_AGENT = "Showis/0.1 (+https://github.com/showis-osint)"


class AIProviderValidationError(ValueError):
    pass


def available_presets() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "name": preset.name,
            "base_url": preset.base_url,
            "default_model": preset.default_model,
            "description": preset.description,
        }
        for key, preset in PRESETS.items()
    }


def _normalize_preset(value: Any) -> str:
    """Preset adını doğrular; bilinmeyen ad 422'ye dönüşür (P-018).

    `showis_llm.get_preset` geriye uyumluluk için bilinmeyende `opencode-go`
    döndürür; API katmanı bu sessiz yönlendirmeye izin vermez (sır yanlış
    sağlayıcıya gitmesin).
    """
    key = str(value or "custom").strip().lower()
    if key not in PRESETS and key != "custom":
        raise AIProviderValidationError(f"bilinmeyen preset: {value}")
    return key


def validate_config(config: dict[str, Any] | None, *, preset: str) -> dict[str, Any]:
    preset_key = _normalize_preset(preset)
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise AIProviderValidationError("config nesne olmali")
    if len(config) > MAX_CONFIG_KEYS:
        raise AIProviderValidationError("config cok fazla alan iceriyor")
    unknown = sorted(set(config) - ALLOWED_CONFIG_KEYS)
    if unknown:
        raise AIProviderValidationError(
            f"izinli alanlar: {sorted(ALLOWED_CONFIG_KEYS)} (gelen: {unknown})"
        )

    cleaned: dict[str, Any] = {}
    for key, value in config.items():
        if key == "models":
            if not isinstance(value, list) or len(value) > MAX_MODELS:
                raise AIProviderValidationError("models bir liste olmali (en fazla 200)")
            models: list[str] = []
            for model in value:
                if not isinstance(model, str) or not model.strip() or len(model.strip()) > 128:
                    raise AIProviderValidationError("models icinde gecersiz model adi")
                if model.strip() not in models:
                    models.append(model.strip())
            cleaned["models"] = models
            continue
        if isinstance(value, (dict, list, tuple, set)):
            raise AIProviderValidationError(f"config.{key} ic ice olamaz")
        if isinstance(value, str) and len(value) > MAX_CONFIG_VALUE:
            raise AIProviderValidationError(f"config.{key} cok uzun")
        cleaned[key] = value

    if "preset" in cleaned:
        # config içindeki preset de doğrulanır; sessiz opencode-go yönlendirmesi yok.
        cleaned["preset"] = _normalize_preset(cleaned["preset"])

    if preset_key == "custom":
        if not cleaned.get("base_url") or not cleaned.get("model"):
            raise AIProviderValidationError(
                "ozel profil icin base_url ve model zorunlu"
            )
        fallback = get_preset("opencode-go")
    else:
        fallback = get_preset(preset_key)
    raw_base_url = cleaned.get("base_url") or fallback.base_url
    try:
        cleaned["base_url"] = validate_base_url(raw_base_url)
    except BaseUrlError as exc:
        raise AIProviderValidationError(str(exc)) from exc

    model = cleaned.get("model") or fallback.default_model
    if not isinstance(model, str) or not model.strip() or len(model.strip()) > MAX_MODEL:
        raise AIProviderValidationError("model gecersiz")
    cleaned["model"] = model.strip()
    model_list = cleaned.get("models")
    if model_list is None:
        model_list = [cleaned["model"]]
    elif cleaned["model"] not in model_list:
        model_list.insert(0, cleaned["model"])
    cleaned["models"] = model_list

    timeout = cleaned.get("timeout_seconds", 30.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise AIProviderValidationError("timeout_seconds sayisal olmali")
    if not MIN_TIMEOUT <= float(timeout) <= MAX_TIMEOUT:
        raise AIProviderValidationError(
            f"timeout_seconds {int(MIN_TIMEOUT)}..{int(MAX_TIMEOUT)} olmali"
        )
    cleaned["timeout_seconds"] = float(timeout)

    temperature = cleaned.get("temperature", 0.2)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise AIProviderValidationError("temperature sayisal olmali")
    if not 0.0 <= float(temperature) <= 2.0:
        raise AIProviderValidationError("temperature 0..2 olmali")
    cleaned["temperature"] = float(temperature)

    max_tokens = cleaned.get("max_tokens", 2000)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise AIProviderValidationError("max_tokens tamsayi olmali")
    if not 64 <= max_tokens <= 32000:
        raise AIProviderValidationError("max_tokens 64..32000 olmali")
    cleaned["max_tokens"] = max_tokens
    return cleaned


def allowed_models(provider: AIProvider) -> list[str]:
    config = provider.config or {}
    models = config.get("models")
    if isinstance(models, list):
        clean = [str(model) for model in models if str(model).strip()]
        if clean:
            return clean[:MAX_MODELS]
    model = str(config.get("model") or "").strip()
    return [model] if model else []


def validate_roles(roles: dict[str, Any] | None, *, models: list[str]) -> dict[str, str]:
    """Rol → model eşlemesi; modeller profilin izinli listesinde olmalı."""
    if roles is None:
        return {}
    if not isinstance(roles, dict):
        raise AIProviderValidationError("roles nesne olmali")
    unknown = sorted(set(roles) - set(ROLES))
    if unknown:
        raise AIProviderValidationError(f"izinli roller: {list(ROLES)} (gelen: {unknown})")
    cleaned: dict[str, str] = {}
    for role, model in roles.items():
        text = str(model or "").strip()
        if not text:
            continue
        if len(text) > MAX_MODEL:
            raise AIProviderValidationError(f"roles.{role} cok uzun")
        if models and text not in models:
            raise AIProviderValidationError(f"roles.{role} izinli model listesinde degil")
        cleaned[role] = text
    return cleaned


def suggested_roles(preset: str) -> dict[str, str]:
    return dict(ANTHROPIC_DEFAULT_ROLES) if preset == "anthropic" else {}


def provider_capabilities(provider: AIProvider) -> dict[str, dict[str, bool]]:
    """İzinli her model için tahmini yetenekler (tools/stream/thinking)."""
    style = detect_api_style(str((provider.config or {}).get("base_url") or DEFAULT_BASE_URL))
    return {model: model_capabilities(model, style) for model in allowed_models(provider)}


def validate_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned or len(cleaned) > MAX_NAME:
        raise AIProviderValidationError("profil adi 1..64 karakter olmali")
    return cleaned


async def list_providers(db: AsyncSession) -> list[AIProvider]:
    rows = (
        (await db.execute(select(AIProvider).order_by(AIProvider.created_at.asc()))).scalars().all()
    )
    return list(rows)


async def get_provider(db: AsyncSession, provider_id: str) -> AIProvider | None:
    return (
        await db.execute(select(AIProvider).where(AIProvider.id == provider_id))
    ).scalar_one_or_none()


async def active_provider(db: AsyncSession) -> AIProvider | None:
    return (
        await db.execute(
            select(AIProvider).where(
                AIProvider.is_active.is_(True),
                AIProvider.enabled.is_(True),
            )
        )
    ).scalars().first()


async def _validate_secret(secret: str | None) -> None:
    if secret is not None and len(secret) > MAX_SECRET:
        raise AIProviderValidationError("secret cok uzun")


async def _guard_base_url(base_url: str) -> None:
    try:
        validate_base_url(base_url)
        await asyncio.to_thread(guard_url, base_url)
    except (BaseUrlError, SSRFViolationError) as exc:
        raise AIProviderValidationError("base_url politika disi") from exc


async def create_provider(
    db: AsyncSession,
    *,
    name: str,
    preset: str,
    config: dict[str, Any] | None,
    secret: str | None,
    enabled: bool = True,
    activate: bool = False,
    roles: dict[str, Any] | None = None,
) -> AIProvider:
    clean_name = validate_name(name)
    preset_key = _normalize_preset(preset)
    cleaned = validate_config(config, preset=preset_key)
    await _validate_secret(secret)
    if activate and not secret:
        raise AIProviderValidationError("aktif profil icin API anahtari gerekli")
    await _guard_base_url(cleaned["base_url"])

    existing = (
        await db.execute(select(AIProvider).where(AIProvider.name == clean_name))
    ).scalar_one_or_none()
    if existing is not None:
        raise AIProviderValidationError("bu adla bir profil zaten var")

    provider = AIProvider(
        name=clean_name,
        preset=preset_key,
        config=cleaned,
        roles=validate_roles(roles, models=cleaned["models"]),
        secret_encrypted=encrypt_secret(secret) if secret else None,
        secret_masked=mask_secret(secret) if secret else None,
        enabled=enabled,
        is_active=False,
    )
    db.add(provider)
    await db.flush()
    if activate:
        await activate_provider(db, provider.id)
    await db.commit()
    await db.refresh(provider)
    return provider


async def update_provider(
    db: AsyncSession,
    provider: AIProvider,
    *,
    name: str | None = None,
    preset: str | None = None,
    config: dict[str, Any] | None = None,
    secret: str | None = None,
    enabled: bool | None = None,
    roles: dict[str, Any] | None = None,
) -> tuple[AIProvider, bool]:
    """Profili günceller; sır geçersiz kılındıysa `(provider, True)` döner."""
    changed: list[str] = []
    if name is not None:
        clean_name = validate_name(name)
        if clean_name != provider.name:
            clash = (
                await db.execute(
                    select(AIProvider).where(
                        AIProvider.name == clean_name, AIProvider.id != provider.id
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise AIProviderValidationError("bu adla bir profil zaten var")
            provider.name = clean_name
            changed.append("name")

    if preset is not None:
        preset_key = _normalize_preset(preset)
        provider.preset = preset_key
        changed.append("preset")

    previous_url = str((provider.config or {}).get("base_url") or DEFAULT_BASE_URL)
    if config is not None:
        # Kolondaki preset legacy/bozuksa sessizce opencode-go'ya düşme.
        cleaned = validate_config(config, preset=_normalize_preset(provider.preset))
        await _guard_base_url(cleaned["base_url"])
        provider.config = cleaned
        changed.append("config")
    new_url = str((provider.config or {}).get("base_url") or DEFAULT_BASE_URL)
    if roles is not None:
        provider.roles = validate_roles(roles, models=allowed_models(provider))
        changed.append("roles")

    secret_invalidated = False
    await _validate_secret(secret)
    if secret:
        provider.secret_encrypted = encrypt_secret(secret)
        provider.secret_masked = mask_secret(secret)
        changed.append("secret_rotated")
    elif previous_url != new_url:
        # SEC-02: sır yeni (istemci kontrollü) host'a taşınmaz.
        provider.secret_encrypted = None
        provider.secret_masked = None
        secret_invalidated = True
        changed.append("secret_invalidated")

    if enabled is not None and enabled != provider.enabled:
        provider.enabled = enabled
        changed.append("enabled")
        if not enabled and provider.is_active:
            provider.is_active = False
            changed.append("deactivated")

    await db.commit()
    await db.refresh(provider)
    logger.info("AI provider guncellendi (id=%s, alanlar=%s)", provider.id, changed)
    return provider, secret_invalidated


async def activate_provider(db: AsyncSession, provider_id: str) -> AIProvider:
    provider = await get_provider(db, provider_id)
    if provider is None:
        raise AIProviderValidationError("profil bulunamadi")
    if not provider.enabled:
        raise AIProviderValidationError("pasif profil aktiflestirilemez")
    if not provider.secret_encrypted:
        raise AIProviderValidationError("anahtarsiz profil aktiflestirilemez")
    # Önce tüm aktifleri kapat: kısmi unique index aynı transaction'da çakışmasın.
    await db.execute(
        update(AIProvider)
        .where(AIProvider.is_active.is_(True), AIProvider.id != provider.id)
        .values(is_active=False)
    )
    await db.flush()
    provider.is_active = True
    await db.commit()
    await db.refresh(provider)
    return provider


async def delete_provider(db: AsyncSession, provider: AIProvider) -> None:
    await db.delete(provider)
    await db.commit()


def _auth_headers(base_url: str, api_key: str) -> dict[str, str]:
    if detect_api_style(base_url) == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": USER_AGENT,
        }
    return {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}


async def discover_models(
    *, base_url: str, api_key: str, timeout: float = 20.0
) -> list[str]:
    """OpenAI uyumlu `/models` ucundan erişilebilir model kimliklerini okur."""
    try:
        base = validate_base_url(base_url)
        await asyncio.to_thread(guard_url, base)
    except (BaseUrlError, SSRFViolationError) as exc:
        raise AIProviderValidationError("base_url politika disi") from exc
    headers = _auth_headers(base, api_key)
    headers["Accept"] = "application/json"
    try:
        async with httpx.AsyncClient(timeout=min(max(timeout, 5.0), 60.0)) as client:
            resp = await client.get(f"{base.rstrip('/')}/models", headers=headers)
    except Exception as exc:  # noqa: BLE001 - bağlantı/timeout
        logger.warning("Model listesi alinamadi: %s", sanitize_error(exc))
        raise AIProviderValidationError(
            f"Model listesi alinamadi ({type(exc).__name__})"
        ) from exc
    if resp.status_code == 401:
        raise AIProviderValidationError("API anahtari reddedildi")
    if resp.status_code == 429:
        raise AIProviderValidationError("Saglayici hiz siniri; sonra tekrar deneyin")
    if resp.status_code != 200:
        raise AIProviderValidationError(
            provider_error_message("LLM", resp.status_code)
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise AIProviderValidationError("Model listesi gecersiz JSON") from exc
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise AIProviderValidationError("Model listesi beklenen bicimde degil")
    models: list[str] = []
    for row in rows:
        model = row.get("id") if isinstance(row, dict) else row
        if not isinstance(model, str):
            continue
        name = model.strip()
        if name and len(name) <= 128 and name not in models:
            models.append(name)
    if not models:
        raise AIProviderValidationError("Saglayici model listesi bos")
    return sorted(models)[:MAX_MODELS]


async def test_provider(provider: AIProvider) -> dict[str, Any]:
    base_url = str((provider.config or {}).get("base_url") or DEFAULT_BASE_URL)
    try:
        validate_base_url(base_url)
        await asyncio.to_thread(guard_url, base_url)
    except (BaseUrlError, SSRFViolationError):
        return {"ok": False, "error": "blocked by policy"}

    try:
        token = decrypt_secret(provider.secret_encrypted)
    except Exception:  # noqa: BLE001 - bozuk sır/anahtar testi 500'e düşmesin
        logger.warning("AI provider siri cozulemedi (test, id=%s)", provider.id)
        return {"ok": False, "error": "API key could not be decrypted"}
    if not token:
        return {"ok": False, "error": "API key is missing"}
    model = str((provider.config or {}).get("model") or DEFAULT_MODEL)
    timeout = float((provider.config or {}).get("timeout_seconds") or 15.0)
    anthropic = detect_api_style(base_url) == "anthropic"
    headers = {**_auth_headers(base_url, token), "Content-Type": "application/json"}
    if not anthropic:
        headers["x-opencode-session"] = "provider-test"
    try:
        async with httpx.AsyncClient(timeout=min(timeout, 30.0)) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/{'messages' if anthropic else 'chat/completions'}",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
            )
        if resp.status_code == 200:
            return {"ok": True, "message": "LLM baglantisi dogrulandi", "model": model}
        return {"ok": False, "error": provider_error_message("LLM", resp.status_code)}
    except Exception as exc:  # noqa: BLE001 - bağlantı/timeout
        logger.warning("AI provider test hatasi (%s): %s", provider.id, sanitize_error(exc))
        return {"ok": False, "error": f"Connection test failed: {type(exc).__name__}"}

"""BYOK sağlayıcı ayarları (SEC-02, SEC-03, SEC-08).

Güvenlik kuralları:

- `config` istemciden gelir; `base_url` **allowlist + şema** doğrulamasından geçer
  (bkz. `showis_llm.base_url`), ayrıca dış çağrıdan önce `guard_url` (SSRF) çalışır.
- Sır, istemci kontrollü bir host'a **hiçbir koşulda** gönderilmez: `base_url`
  değiştirilirse o sağlayıcının sırrı geçersiz kılınır (`secret_required: true`).
- Hata yanıtlarında sağlayıcı gövdesi (`resp.text`) dönmez; yalnız durum kodu +
  sabit kategori metni.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from showis.core.redact import provider_error_message, sanitize_error
from showis.db.models import Setting
from showis.db.session import get_db
from showis.services.audit import record_request_audit
from showis_engine.policy import SSRFViolationError, guard_url
from showis_llm.base_url import BaseUrlError, validate_base_url
from showis_settings.crypto import decrypt_secret, encrypt_secret, mask_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.api.settings")

router = APIRouter(prefix="/settings/providers", tags=["settings"])

#: `base_url` kullanan sağlayıcılar
_URL_PROVIDERS = {"llm"}
#: `llm` config için izinli anahtarlar (başka alan sır sızdırma yüzeyidir)
_LLM_CONFIG_KEYS = {"base_url", "model", "timeout_seconds", "preset"}
_MAX_CONFIG_KEYS = 20
_MAX_CONFIG_VALUE = 512
_MAX_SECRET_LENGTH = 512
_MAX_MODEL_LENGTH = 128

PROVIDER_NAMES = {"github", "brave", "serpapi", "cse", "llm", "hibp"}
HTTP_FALLBACK_URL = "https://opencode.ai/inference/go/openai/v1"


class ProviderSettingCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    enabled: bool = True


class ProviderSettingResponse(BaseModel):
    id: str
    provider: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret_masked: str | None = None
    enabled: bool
    created_at: str
    updated_at: str
    #: True ise sır silindi/yok: istemci yeniden girmeli (base_url değişti)
    secret_required: bool = False


def _validate_config(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    """Anahtar/değer sınırları + sağlayıcıya özel base_url doğrulaması."""
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config nesne olmali")
    if len(config) > _MAX_CONFIG_KEYS:
        raise HTTPException(status_code=422, detail="config cok fazla alan iceriyor")

    cleaned: dict[str, Any] = {}
    for key, value in config.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            raise HTTPException(status_code=422, detail="config anahtari gecersiz")
        key = key.strip()
        if isinstance(value, str) and len(value) > _MAX_CONFIG_VALUE:
            raise HTTPException(status_code=422, detail=f"config.{key} cok uzun")
        if isinstance(value, (dict, list)):
            raise HTTPException(status_code=422, detail=f"config.{key} ic ice olamaz")
        cleaned[key] = value

    if provider in _URL_PROVIDERS:
        unknown = set(cleaned) - _LLM_CONFIG_KEYS
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"llm config icin izinli alanlar: {sorted(_LLM_CONFIG_KEYS)}",
            )
        if "base_url" in cleaned:
            try:
                cleaned["base_url"] = validate_base_url(cleaned["base_url"])
            except BaseUrlError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        model = cleaned.get("model")
        if model is not None and (
            not isinstance(model, str) or not model.strip() or len(model) > _MAX_MODEL_LENGTH
        ):
            raise HTTPException(status_code=422, detail="model gecersiz")
        timeout = cleaned.get("timeout_seconds")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise HTTPException(status_code=422, detail="timeout_seconds sayisal olmali")
            if not 1 <= float(timeout) <= 120:
                raise HTTPException(status_code=422, detail="timeout_seconds 1..120 olmali")
    return cleaned


def _base_url_of(config: dict[str, Any]) -> str:
    return str(config.get("base_url") or HTTP_FALLBACK_URL)


def _response(item: Setting, *, secret_required: bool = False) -> ProviderSettingResponse:
    return ProviderSettingResponse(
        id=item.id,
        provider=item.provider,
        label=item.label,
        config=item.config or {},
        secret_masked=item.secret_masked,
        enabled=item.enabled,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
        secret_required=secret_required,
    )


@router.get("", response_model=list[ProviderSettingResponse])
async def list_providers(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Setting).order_by(Setting.provider))
    items = res.scalars().all()
    return [_response(item) for item in items]


@router.post("", response_model=ProviderSettingResponse)
async def upsert_provider(
    data: ProviderSettingCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    provider = data.provider.strip().lower()
    if provider not in PROVIDER_NAMES:
        raise HTTPException(
            status_code=422, detail=f"bilinmeyen provider: {provider}"
        )
    if data.secret is not None and len(data.secret) > _MAX_SECRET_LENGTH:
        raise HTTPException(status_code=422, detail="secret cok uzun")
    config = _validate_config(provider, data.config)

    res = await db.execute(select(Setting).where(Setting.provider == provider))
    existing = res.scalar_one_or_none()

    secret_required = False
    changed: list[str] = []
    if existing:
        previous_config = dict(existing.config or {})
        previous_url = _base_url_of(previous_config)
        new_url = _base_url_of(config)
        url_changed = provider in _URL_PROVIDERS and previous_url != new_url

        if (data.label or existing.label) != existing.label:
            changed.append("label")
        if existing.enabled != data.enabled:
            changed.append("enabled")
        changed.extend(
            sorted(
                key
                for key in set(previous_config) | set(config)
                if previous_config.get(key) != config.get(key)
            )
        )

        existing.label = data.label or existing.label
        existing.config = config
        existing.enabled = data.enabled
        if data.secret:
            existing.secret_encrypted = encrypt_secret(data.secret)
            existing.secret_masked = mask_secret(data.secret)
        elif url_changed:
            # SEC-02: sır, yeni (istemci kontrollü) host'a asla taşınmaz.
            logger.warning(
                "Provider %s base_url degisti; saklanan sir gecersiz kilindi", provider
            )
            existing.secret_encrypted = None
            existing.secret_masked = None
            secret_required = True
            changed.append("secret_invalidated")
        item = existing
    else:
        encrypted = encrypt_secret(data.secret) if data.secret else None
        masked = mask_secret(data.secret) if data.secret else None
        item = Setting(
            provider=provider,
            label=data.label or provider.capitalize(),
            config=config,
            secret_encrypted=encrypted,
            secret_masked=masked,
            enabled=data.enabled,
        )
        db.add(item)
        secret_required = encrypted is None
        changed.append("created")

    await db.commit()
    await db.refresh(item)

    # T-112/3 (SEC-13): sır rotasyonu / base_url değişimi iz bırakır.
    # Sır değeri ASLA yazılmaz — yalnız provider + değişen alan adları.
    await record_request_audit(
        db=db,
        request=request,
        action="settings.provider.update",
        target=f"Setting: {item.provider}",
        details={
            "provider": item.provider,
            "changed_fields": changed,
            "secret_rotated": bool(data.secret),
            "secret_invalidated": secret_required,
            "config_keys": sorted(config),
        },
    )
    return _response(item, secret_required=secret_required)


@router.delete("/{setting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    setting_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Setting).where(Setting.id == setting_id))
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Provider setting not found")
    provider = item.provider
    await db.delete(item)
    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="settings.provider.delete",
        target=f"Setting: {provider}",
        details={"provider": provider},
    )
    return None


async def _guard_llm_base_url(base_url: str) -> dict[str, Any] | None:
    """Allowlist + SSRF kontrolü; ihlalde istemciye dönecek yanıtı üretir."""
    try:
        validate_base_url(base_url)
    except BaseUrlError:
        return {"ok": False, "error": "blocked by policy"}
    try:
        await asyncio.to_thread(guard_url, base_url)
    except SSRFViolationError:
        return {"ok": False, "error": "blocked by policy"}
    return None


@router.post("/{setting_id}/test")
async def test_provider_connection(setting_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Setting).where(Setting.id == setting_id))
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Provider setting not found")

    headers = {"User-Agent": "Showis/0.1 (+https://github.com/showis-osint)"}
    provider = item.provider

    # SEC-03: sır çözülmeden önce hedef URL politikası doğrulanır.
    if provider == "llm":
        base_url = _base_url_of(item.config or {})
        blocked = await _guard_llm_base_url(base_url)
        if blocked is not None:
            logger.warning("LLM test engellendi: base_url politika disi")
            return blocked

    token = decrypt_secret(item.secret_encrypted)

    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            if provider == "github":
                if not token:
                    return {"ok": False, "error": "GitHub PAT is missing"}
                resp = await client.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {token}", **headers},
                )
                if resp.status_code == 200:
                    return {
                        "ok": True,
                        "message": "GitHub connection verified",
                        "user": resp.json().get("login"),
                    }
                return {
                    "ok": False,
                    "error": provider_error_message("GitHub", resp.status_code),
                }

            if provider == "brave":
                if not token:
                    return {"ok": False, "error": "Brave API key is missing"}
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search?q=test&count=1",
                    headers={"X-Subscription-Token": token, **headers},
                )
                if resp.status_code == 200:
                    return {"ok": True, "message": "Brave Search connection verified"}
                return {
                    "ok": False,
                    "error": provider_error_message("Brave", resp.status_code),
                }

            if provider == "serpapi":
                if not token:
                    return {"ok": False, "error": "SerpAPI key is missing"}
                # Anahtar URL query'sinde (sağlayıcı gereği); yanıt gövdesi ve
                # istisna metni istemciye dönmez (SEC-08).
                resp = await client.get(
                    "https://serpapi.com/search",
                    params={"q": "test", "api_key": token, "num": 1},
                )
                if resp.status_code == 200:
                    return {"ok": True, "message": "SerpAPI connection verified"}
                return {
                    "ok": False,
                    "error": provider_error_message("SerpAPI", resp.status_code),
                }

            if provider == "cse":
                if not token:
                    return {"ok": False, "error": "Google CSE API key is missing"}
                cx = str((item.config or {}).get("cx") or "")
                if not cx:
                    return {"ok": False, "error": "Google CSE cx is missing"}
                resp = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"q": "test", "key": token, "cx": cx, "num": 1},
                )
                if resp.status_code == 200:
                    return {"ok": True, "message": "Google CSE connection verified"}
                return {
                    "ok": False,
                    "error": provider_error_message("Google CSE", resp.status_code),
                }

            if provider == "hibp":
                if not token:
                    return {"ok": False, "error": "HIBP API key is missing"}
                resp = await client.get(
                    "https://haveibeenpwned.com/api/v3/breachedaccount/test@example.com",
                    headers={"hibp-api-key": token, **headers},
                    params={"truncateResponse": "false"},
                )
                if resp.status_code in (200, 404):
                    return {"ok": True, "message": "HIBP connection verified"}
                return {
                    "ok": False,
                    "error": provider_error_message("HIBP", resp.status_code),
                }

            if provider == "llm":
                base_url = _base_url_of(item.config or {})
                model = str((item.config or {}).get("model") or "minimax-m3.1-flash")
                llm_headers = {
                    **headers,
                    "Authorization": f"Bearer {token}",
                    "x-opencode-session": "test-session",
                }
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=llm_headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5,
                    },
                )
                if resp.status_code == 200:
                    return {"ok": True, "message": "LLM connection verified"}
                return {"ok": False, "error": provider_error_message("LLM", resp.status_code)}

            return {
                "ok": True,
                "message": f"Provider {provider} saved (generic check passed)",
            }

    except Exception as e:  # bağlantı/timeout/SSRF-yeniden çözümleme
        logger.warning("Provider test hatasi (%s): %s", provider, sanitize_error(e))
        return {"ok": False, "error": f"Connection test failed: {type(e).__name__}"}

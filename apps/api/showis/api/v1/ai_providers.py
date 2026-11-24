"""Çoklu LLM sağlayıcı profilleri — yalnız yönetici yazar."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from showis.core.security import require_admin_access
from showis.db.models import AIProvider
from showis.db.session import get_db
from showis.services import ai_providers as service
from showis.services.audit import record_request_audit
from showis_settings.crypto import decrypt_secret
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/ai/providers", tags=["ai-providers"])


class AIProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    preset: str = Field(default="custom", max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    enabled: bool = True
    activate: bool = False


class AIProviderDiscover(BaseModel):
    provider_id: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    secret: str | None = Field(default=None, max_length=512)


class AIProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    preset: str | None = Field(default=None, max_length=32)
    config: dict[str, Any] | None = None
    secret: str | None = None
    enabled: bool | None = None


class AIProviderResponse(BaseModel):
    id: str
    name: str
    preset: str
    config: dict[str, Any]
    secret_masked: str | None = None
    secret_required: bool = False
    enabled: bool
    is_active: bool
    created_at: str
    updated_at: str


def _response(provider: AIProvider, *, secret_required: bool = False) -> AIProviderResponse:
    return AIProviderResponse(
        id=provider.id,
        name=provider.name,
        preset=provider.preset,
        config=provider.config or {},
        secret_masked=provider.secret_masked,
        secret_required=secret_required or not provider.secret_encrypted,
        enabled=provider.enabled,
        is_active=provider.is_active,
        created_at=provider.created_at.isoformat(),
        updated_at=provider.updated_at.isoformat(),
    )


@router.get("/presets")
async def list_presets():
    return service.available_presets()


@router.get("", response_model=list[AIProviderResponse])
async def list_ai_providers(db: AsyncSession = Depends(get_db)):
    providers = await service.list_providers(db)
    return [_response(provider) for provider in providers]


@router.post("", response_model=AIProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_ai_provider(
    data: AIProviderCreate,
    request: Request,
    _: None = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        provider = await service.create_provider(
            db,
            name=data.name,
            preset=data.preset,
            config=data.config,
            secret=data.secret,
            enabled=data.enabled,
            activate=data.activate,
        )
    except service.AIProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Bu adla bir profil zaten var") from exc
    await record_request_audit(
        db=db,
        request=request,
        action="settings.ai_provider.create",
        target=f"AIProvider: {provider.name}",
        details={
            "provider_id": provider.id,
            "preset": provider.preset,
            "model": (provider.config or {}).get("model"),
            "base_url": (provider.config or {}).get("base_url"),
            "activated": provider.is_active,
        },
    )
    return _response(provider)


@router.patch("/{provider_id}", response_model=AIProviderResponse)
async def update_ai_provider(
    provider_id: str,
    data: AIProviderPatch,
    request: Request,
    _: None = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
):
    provider = await service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    try:
        provider, secret_invalidated = await service.update_provider(
            db,
            provider,
            name=data.name,
            preset=data.preset,
            config=data.config,
            secret=data.secret,
            enabled=data.enabled,
        )
    except service.AIProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Bu adla bir profil zaten var") from exc
    await record_request_audit(
        db=db,
        request=request,
        action="settings.ai_provider.update",
        target=f"AIProvider: {provider.name}",
        details={
            "provider_id": provider.id,
            "secret_rotated": bool(data.secret),
            "secret_invalidated": secret_invalidated,
            "config_keys": sorted((data.config or {}).keys()),
        },
    )
    return _response(provider, secret_required=secret_invalidated)


@router.post("/discover")
async def discover_ai_provider_models(
    data: AIProviderDiscover,
    request: Request,
    _: None = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
):
    """Kaydedilmiş profil veya formdaki geçici anahtarla erişilebilir modelleri listeler."""
    provider = None
    explicit_base = str(data.base_url or "").strip()
    explicit_secret = str(data.secret or "").strip()
    if explicit_base and explicit_secret:
        # Formdaki yeni anahtarla kaydetmeden önce model listesi alınabilir.
        base_url = explicit_base
        api_key = explicit_secret
    elif data.provider_id:
        provider = await service.get_provider(db, data.provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="AI provider not found")
        base_url = str((provider.config or {}).get("base_url") or service.DEFAULT_BASE_URL)
        try:
            api_key = str(decrypt_secret(provider.secret_encrypted) or "")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="API anahtarı çözülemedi") from exc
    else:
        base_url = explicit_base
        api_key = explicit_secret
    if not base_url or not api_key:
        raise HTTPException(
            status_code=422, detail="base_url ve API anahtarı zorunlu"
        )
    try:
        models = await service.discover_models(base_url=base_url, api_key=api_key)
    except service.AIProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await record_request_audit(
        db=db,
        request=request,
        action="settings.ai_provider.discover_models",
        target=f"AIProvider: {provider.name if provider else 'form'}",
        details={
            "provider_id": provider.id if provider else None,
            "model_count": len(models),
        },
    )
    return {"models": models}


@router.post("/{provider_id}/activate", response_model=AIProviderResponse)
async def activate_ai_provider(
    provider_id: str,
    request: Request,
    _: None = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        provider = await service.activate_provider(db, provider_id)
    except service.AIProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Profil aktive edilemedi") from exc
    await record_request_audit(
        db=db,
        request=request,
        action="settings.ai_provider.activate",
        target=f"AIProvider: {provider.name}",
        details={"provider_id": provider.id, "preset": provider.preset},
    )
    return _response(provider)


@router.post("/{provider_id}/test")
async def test_ai_provider(
    provider_id: str,
    request: Request,
    _: None = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
):
    provider = await service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    result = await service.test_provider(provider)
    await record_request_audit(
        db=db,
        request=request,
        action="settings.ai_provider.test",
        target=f"AIProvider: {provider.name}",
        details={"provider_id": provider.id, "ok": bool(result.get("ok"))},
    )
    return result


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_provider(
    provider_id: str,
    request: Request,
    _: None = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
):
    provider = await service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    name = provider.name
    await service.delete_provider(db, provider)
    await record_request_audit(
        db=db,
        request=request,
        action="settings.ai_provider.delete",
        target=f"AIProvider: {name}",
        details={"provider_id": provider_id},
    )
    return None

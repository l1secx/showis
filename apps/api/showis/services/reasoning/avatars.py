"""Avatar özetlerinin (SHA-256 + dHash) havuz geneline yayılması.

Taramalar yalnız kendi varlıklarının avatarını özetler. Havuz öğeleri (gravatar,
profil URL'leri, AI bulguları) `attributes.avatar_url` taşıyorsa burada aynı
SSRF korumalı indirici ile özetlenir; korelasyon `same_avatar` kuralı bu özetleri okur.
"""

from __future__ import annotations

import asyncio
from typing import Any

from showis.db.models import PoolItem
from showis_engine.links import fetch_avatar_hashes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_BATCH = 20
CONCURRENCY = 5
FAILED_KEY = "avatar_hash_failed"


async def hash_item_avatar(item: PoolItem) -> bool:
    """Öğenin `avatar_url` görselini özetler; özetler `attributes`'a yazılır."""
    attributes = dict(item.attributes or {})
    url = str(attributes.get("avatar_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    sha, dhash = await fetch_avatar_hashes(url)
    if not (sha or dhash):
        attributes[FAILED_KEY] = True
        item.attributes = attributes
        return False
    attributes.pop(FAILED_KEY, None)
    if sha:
        attributes["avatar_sha256"] = sha
    if dhash:
        attributes["avatar_dhash"] = dhash
    item.attributes = attributes
    return True


async def hash_pool_avatars(
    db: AsyncSession, case_id: str, *, limit: int = MAX_BATCH
) -> dict[str, Any]:
    """Özeti çıkarılmamış `avatar_url` öğelerini (en çok `limit`) özetler."""
    items = list(
        (
            await db.execute(
                select(PoolItem)
                .where(PoolItem.case_id == case_id, PoolItem.status != "rejected")
                .order_by(PoolItem.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    pending = [
        item
        for item in items
        if (item.attributes or {}).get("avatar_url")
        and not (item.attributes or {}).get("avatar_dhash")
        and not (item.attributes or {}).get(FAILED_KEY)
    ][: max(1, min(limit, MAX_BATCH))]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(item: PoolItem) -> bool:
        async with semaphore:
            return await hash_item_avatar(item)

    results = await asyncio.gather(*(one(item) for item in pending))
    await db.commit()
    return {"candidates": len(pending), "hashed": sum(results)}

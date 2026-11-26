"""Edge kimlikleri için kısa, deterministik üreticiler.

Neden ayrı modül: hem `identity_edges` hem `links`/`linkfollow` aynı yardımcıyı
kullanır. Daha önce `links` kendi uzun `edge:cross_link:...` kimliklerini
üretiyordu ve DB `edges.id` VARCHAR(96) sınırını aşabiliyordu; `short_edge_id`
çıktısı her zaman 27 karakterdir (`prefix:sha1[:24]`).
"""

from __future__ import annotations

import hashlib


def short_edge_id(prefix: str, *parts: str) -> str:
    """Deterministik ve DB'ye sığan edge id'si (prefix + 24 hex ≤ 27 karakter)."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"

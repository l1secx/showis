"""Kimlik grafiği: deterministik SAME_PERSON / CROSS_LINK kenarları (T-309).

Sorun: taramalar 100+ entity üretiyor ama `edges` tablosu boş kalıyordu. Bio
linkleri (`CROSS_LINK`) ve avatar karması dışında hiçbir ilişki üretilmiyordu;
oysa OSINT'te en güçlü sinyal **aynı kullanıcı adının birden çok platformda
görülmesi**dir (ör. `kboz` 24 platformda).

Bu modül kenarları tek yerden, deterministik olarak üretir:

1. ``username`` — aynı normalize kullanıcı adı, ≥2 farklı platform → **yıldız
   (star) topolojisi**: grup içinde bir "hub" seçilir, kalanlar hub'a bağlanır.
   Tüm çiftler üretilmez: 24 platformluk grupta 276 yerine 23 kenar (O(n²) yok).
   ``confidence = min(0.35 + 0.05·(n−1), 0.85)``.
2. ``avatar_sha256`` — birebir aynı avatar dosyası → 0.90.
3. ``avatar_dhash`` — algısal Hamming mesafesi ≤ 6 → 0.70 (yardımcı:
   :func:`showis_engine.links.hamming_distance`).
4. ``email``/``pgp`` — aynı public/commit e-posta veya PGP parmak izi → 0.85.
5. ``CROSS_LINK`` — bio/link alanındaki platform profilleri (mevcut
   :func:`showis_engine.links.build_cross_link_edges` korunur).

Kurallar:

- Yalnız **platformlar arası** kenar (aynı platformdaki iki kayıt bağlanmaz).
- Dedupe: ``(küçük_id, büyük_id, type)`` — simetrik çift bir kez üretilir.
- Entity başına en fazla ``max_edges_per_entity`` (varsayılan 20) kenar; en
  yüksek confidence önce.
- ``edge.id`` deterministik ve **≤ 36 karakter** (DB `edges.id` VARCHAR(36)):
  ``e:<sha1[:24]>``. Uzun id'ler kırpılmaz, hash'lenir.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from showis_contracts import Edge, EdgeType, Entity, Platform

from .links import build_cross_link_edges, hamming_distance

logger = logging.getLogger("showis.identity_edges")

#: Yıldız topolojisinde hub seçim önceliği (küçük index = daha güçlü merkez)
HUB_PRIORITY: tuple[str, ...] = (
    Platform.GITHUB.value,
    Platform.GITLAB.value,
    Platform.WEB.value,
    Platform.KEYBASE.value,
)

#: Ortak kullanıcı adı kenarının taban/artış/üst sınırı
USERNAME_BASE_CONFIDENCE = 0.35
USERNAME_CONFIDENCE_STEP = 0.05
USERNAME_MAX_CONFIDENCE = 0.85

#: Diğer sinyallerin sabit güvenleri
AVATAR_SHA_CONFIDENCE = 0.90
AVATAR_DHASH_CONFIDENCE = 0.70
DHASH_MAX_DISTANCE = 6
EMAIL_OR_PGP_CONFIDENCE = 0.85

MAX_EDGES_PER_ENTITY = 20
#: DB şeması: `edges.id` VARCHAR(36)
EDGE_ID_MAX_LENGTH = 96  # migration 0005: edges.id VARCHAR(96)

_GENERIC_DHASHES = {"0000000000000000", "ffffffffffffffff"}


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def short_edge_id(prefix: str, *parts: str) -> str:
    """Deterministik ve DB'ye sığan edge id'si (prefix + 24 hex ≤ 27 karakter)."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def normalize_username(value: Any) -> str:
    """Kullanıcı adını karşılaştırma biçimine indirger (küçük harf, ``@`` yok)."""
    if value is None:
        return ""
    text = str(value).strip().lstrip("@").strip().lower()
    return text


def entity_username(entity: Entity) -> str:
    """Entity'nin kullanıcı adı: username/login → güvenli label → id kuyruğu."""
    attrs = entity.attributes or {}
    for key in ("username", "login", "handle"):
        value = normalize_username(attrs.get(key))
        if value:
            return value
    label = normalize_username(entity.label)
    # Arama sonucu etiketleri ("Kerem Boz") kullanıcı adı sayılmaz.
    if label and " " not in label and len(label) <= 64:
        return label
    ident = str(entity.id).split(":", 1)[-1]
    return normalize_username(ident)


def _entity_emails(entity: Entity) -> set[str]:
    attrs = entity.attributes or {}
    values: set[str] = set()
    for key in ("public_email", "email"):
        text = str(attrs.get(key) or "").strip().lower()
        if text and "@" in text:
            values.add(text)
    for item in attrs.get("commit_emails") or []:
        text = str(item).strip().lower()
        if text and "@" in text:
            values.add(text)
    return values


def _entity_pgp_keys(entity: Entity) -> set[str]:
    attrs = entity.attributes or {}
    values: set[str] = set()
    for key in ("pgp_fingerprint", "pgp_key", "pgp", "gpg_key", "gpg_fingerprint"):
        text = str(attrs.get(key) or "").strip().upper().replace(" ", "")
        if len(text) >= 16:
            values.add(text)
    for item in attrs.get("pgp_keys") or []:
        text = str(item).strip().upper().replace(" ", "")
        if len(text) >= 16:
            values.add(text)
    return values


def _hub_sort_key(entity: Entity) -> tuple[int, str, str]:
    """Hub seçimi: platform önceliği → username → id (deterministik)."""
    platform = entity.platform.value
    try:
        priority = HUB_PRIORITY.index(platform)
    except ValueError:
        priority = len(HUB_PRIORITY)
    return (priority, entity_username(entity), entity.id)


def _pair_key(source_id: str, target_id: str, edge_type: EdgeType) -> tuple[str, str, str]:
    first, second = sorted((source_id, target_id))
    return (first, second, edge_type.value)


def _merge_evidence(*entities: Entity) -> list[str]:
    ids: list[str] = []
    for entity in entities:
        for ev_id in entity.evidence_ids or []:
            if ev_id not in ids:
                ids.append(ev_id)
    return ids


def _star_edges(
    group: list[Entity],
    *,
    edge_type: EdgeType,
    confidence: float,
    reason_builder: Any,
    prefix: str,
) -> list[Edge]:
    """Grup içinde hub seçip yıldız topolojisinde kenarlar üretir."""
    ordered = sorted(group, key=_hub_sort_key)
    hub = ordered[0]
    edges: list[Edge] = []
    for member in ordered[1:]:
        if member.platform == hub.platform:
            continue  # yalnız platformlar arası
        edges.append(
            Edge(
                id=short_edge_id(prefix, edge_type.value, hub.id, member.id),
                type=edge_type,
                source_entity_id=hub.id,
                target_entity_id=member.id,
                confidence=confidence,
                reason=reason_builder(hub, member),
                evidence_ids=_merge_evidence(hub, member),
            )
        )
    return edges


# ---------------------------------------------------------------------------
# Genel API
# ---------------------------------------------------------------------------


def build_identity_edges(
    entities: list[Entity],
    *,
    max_edges_per_entity: int = MAX_EDGES_PER_ENTITY,
    max_dhash_distance: int = DHASH_MAX_DISTANCE,
    include_cross_link: bool = True,
) -> list[Edge]:
    """Entity'lerden deterministik kimlik kenarlarını üretir.

    Args:
        entities: Tarama sonucu entity listesi.
        max_edges_per_entity: Bir entity'ye bağlanabilecek azami kenar (en
            yüksek confidence önce tutulur).
        max_dhash_distance: Avatar dHash eşleşme eşiği (varsayılan 6).
        include_cross_link: Bio/link kaynaklı CROSS_LINK kenarlarını da ekle.
    """
    if not entities:
        return []

    candidates: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()
    #: Ortak kullanıcı adı yıldızı birincil kanıt zinciridir; per-entity sınırdan muaf
    #: (24 platformluk gerçek örüntü 23 kenar üretmeli). Sınır zayıf sinyalleri
    #: (avatar dHash çiftleri, e-posta grupları) budar.
    uncapped: set[str] = set()

    def add(edge: Edge) -> None:
        key = _pair_key(edge.source_entity_id, edge.target_entity_id, edge.type)
        if edge.source_entity_id == edge.target_entity_id or key in seen:
            return
        seen.add(key)
        candidates.append(edge)

    # 1) Ortak kullanıcı adı — platform bazında tekilleştir, yıldız topolojisi
    by_username: dict[str, dict[str, Entity]] = {}
    for entity in entities:
        username = entity_username(entity)
        if not username:
            continue
        group = by_username.setdefault(username, {})
        current = group.get(entity.platform.value)
        if current is None or entity.id < current.id:
            group[entity.platform.value] = entity

    for username, platform_map in sorted(by_username.items()):
        if len(platform_map) < 2:
            continue
        members = list(platform_map.values())
        confidence = min(
            USERNAME_MAX_CONFIDENCE,
            round(USERNAME_BASE_CONFIDENCE + USERNAME_CONFIDENCE_STEP * (len(members) - 1), 2),
        )

        def _reason(hub: Entity, member: Entity, _u: str = username, _n: int = len(members)) -> str:
            return f"ortak kullanıcı adı '{_u}' ({_n} platform)"

        for edge in _star_edges(
            members,
            edge_type=EdgeType.SAME_PERSON,
            confidence=confidence,
            reason_builder=_reason,
            prefix="e:u",
        ):
            uncapped.add(edge.id)
            add(edge)

    # 2) Aynı avatar SHA-256 (birebir dosya)
    by_sha: dict[str, list[Entity]] = {}
    for entity in entities:
        sha = str((entity.attributes or {}).get("avatar_sha256") or "").strip()
        if sha:
            by_sha.setdefault(sha, []).append(entity)
    for sha, sha_group in sorted(by_sha.items()):
        if len({e.platform.value for e in sha_group}) < 2:
            continue

        def _sha_reason(hub: Entity, member: Entity, _sha: str = sha) -> str:
            return f"avatar SHA-256 eşleşmesi: {_sha[:16]}…"

        for edge in _star_edges(
            sha_group,
            edge_type=EdgeType.SAME_PERSON,
            confidence=AVATAR_SHA_CONFIDENCE,
            reason_builder=_sha_reason,
            prefix="e:a",
        ):
            add(edge)

    # 3) Algısal avatar benzerliği (dHash Hamming <= 6)
    dhash_entities = sorted(
        (
            entity
            for entity in entities
            if (entity.attributes or {}).get("avatar_dhash")
            and str((entity.attributes or {}).get("avatar_dhash")) not in _GENERIC_DHASHES
        ),
        key=_hub_sort_key,
    )
    # Çift-karşılaştırma O(n²) kenar üretir; bunun yerine ayrık yıldızlar kur:
    # her hub kendisine ≤ eşik mesafedeki (kullanılmamış) üyeleri bağlar.
    assigned: set[str] = set()
    for hub in dhash_entities:
        if hub.id in assigned:
            continue
        assigned.add(hub.id)
        for member in dhash_entities:
            if member.id == hub.id or member.id in assigned:
                continue
            if member.platform == hub.platform:
                continue
            distance = hamming_distance(
                str(hub.attributes.get("avatar_dhash")),
                str(member.attributes.get("avatar_dhash")),
            )
            if distance > max_dhash_distance:
                continue
            assigned.add(member.id)
            add(
                Edge(
                    id=short_edge_id("e:d", EdgeType.SAME_PERSON.value, hub.id, member.id),
                    type=EdgeType.SAME_PERSON,
                    source_entity_id=hub.id,
                    target_entity_id=member.id,
                    confidence=AVATAR_DHASH_CONFIDENCE,
                    reason=f"avatar görsel benzerliği (dHash mesafe={distance})",
                    evidence_ids=_merge_evidence(hub, member),
                )
            )

    # 4) Aynı e-posta / PGP parmak izi
    for kind, extractor, label in (
        ("email", _entity_emails, "e-posta"),
        ("pgp", _entity_pgp_keys, "PGP parmak izi"),
    ):
        by_value: dict[str, list[Entity]] = {}
        for entity in entities:
            for value in extractor(entity):
                by_value.setdefault(value, []).append(entity)
        for value, value_group in sorted(by_value.items()):
            if len({e.platform.value for e in value_group}) < 2:
                continue

            def _mail_reason(hub: Entity, member: Entity, _v: str = value, _l: str = label) -> str:
                if _l == "e-posta":
                    # PII minimizasyonu: alan adı görünür, yerel kısım maskeli
                    local, _, domain = _v.partition("@")
                    shown = f"{local[:1]}***@{domain}" if domain else "***"
                    return f"aynı {_l}: {shown}"
                return f"aynı {_l}: {_v[:16]}…"

            for edge in _star_edges(
                value_group,
                edge_type=EdgeType.SAME_PERSON,
                confidence=EMAIL_OR_PGP_CONFIDENCE,
                reason_builder=_mail_reason,
                prefix=f"e:{kind}",
            ):
                add(edge)

    # 5) Bio/link kaynaklı CROSS_LINK kenarları korunur
    if include_cross_link:
        for entity in entities:
            for edge in build_cross_link_edges(entity, entities):
                add(edge)

    # Entity başına üst sınır: en yüksek confidence önce
    candidates.sort(key=lambda e: (-e.confidence, e.id))
    per_entity: dict[str, int] = {}
    selected: list[Edge] = []
    for edge in candidates:
        endpoints = (edge.source_entity_id, edge.target_entity_id)
        if edge.id not in uncapped and any(
            per_entity.get(endpoint, 0) >= max_edges_per_entity for endpoint in endpoints
        ):
            continue
        for endpoint in endpoints:
            per_entity[endpoint] = per_entity.get(endpoint, 0) + 1
        selected.append(edge)

    selected.sort(key=lambda e: (e.type.value, e.source_entity_id, e.target_entity_id))
    return selected


def edges_by_type(edges: list[Edge]) -> dict[str, int]:
    """Kenarları türe göre sayar (``scan.stats.edges_by_type``, BÜYÜK harf)."""
    counts: dict[str, int] = {}
    for edge in edges:
        key = edge.type.value.upper()
        counts[key] = counts.get(key, 0) + 1
    return counts


def db_safe_edge_id(edge_id: str) -> str:
    """`edges.id` sınırına (96) sığdırır; aşarsa hash'ler (deterministik)."""
    text = str(edge_id)
    if len(text) <= EDGE_ID_MAX_LENGTH:
        return text
    return short_edge_id("e:x", text)


def pair_key(edge: Edge) -> tuple[str, str, str]:
    """Simetrik dedupe anahtarı: (küçük_id, büyük_id, type)."""
    return _pair_key(edge.source_entity_id, edge.target_entity_id, edge.type)

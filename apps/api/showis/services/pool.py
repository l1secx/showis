"""Vaka araştırma havuzu — normalizasyon, tekilleştirme, arama ve tohumlama.

Havuz, vaka içindeki tüm kullanıcı girdilerini ve AI/tarama kaynaklı
önerileri tek yerde tutar. Bu modül DB yazımını yönetir; commit çağırana
aittir (API uçları ve tarama servisi kendi transaction'ını yönetir).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from showis.db.models import Case, CaseComment, PoolItem, Seed, Target
from showis.services.phone import parse_phone, phone_attributes
from showis_engine.variants import normalize_turkish
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

POOL_KINDS: frozenset[str] = frozenset(
    {
        "name",
        "username",
        "email",
        "phone",
        "domain",
        "ip",
        "url",
        "person",
        "organization",
        "product",
        "location",
        "event",
        "note",
        "bio",
        "identifier",
        "other",
    }
)
POOL_SOURCES: frozenset[str] = frozenset({"user", "scan", "ai", "import"})
POOL_STATUSES: frozenset[str] = frozenset({"proposed", "candidate", "confirmed", "rejected"})

#: Toplu durum ucunda kullanıcının verebileceği kararlar (AI için değil).
BULK_STATUSES: frozenset[str] = frozenset({"confirmed", "rejected"})
#: Tek istekte işlenebilecek en fazla öğe kimliği.
MAX_BULK_ITEMS = 200

MAX_LABEL = 256
MAX_VALUE = 20000
MAX_QUERY = 200


class PoolValidationError(ValueError):
    pass


def _fold(value: str) -> str:
    """Türkçe karakterleri ASCII'ye katlar, boşlukları korur."""
    text = normalize_turkish(value)
    # `normalize_turkish` boşlukları siler; kimlik değerlerinde kelime sınırı
    # anlamlıdır (ör. "Acme Lojistik" != "Acmelojistik").
    if " " in value or "\t" in value:
        text = " ".join(
            normalize_turkish(part) for part in value.replace("\t", " ").split() if part
        )
    return text.strip()


def _fit_index(text: str) -> str:
    """Uzun değeri btree indeks sınırına sığdırır; çakışma olasılığını korur.

    PostgreSQL btree bir satırda ~2704 bayt sınırı koyar; 4000 karakterlik bir
    not doğrudan indekslenirse insert hata verir. İlk 300 karakter + SHA-256
    kısa özeti hem aranabilir kalır hem de farklı uzun metinleri ayırır.
    """
    if len(text) <= 400:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{text[:300]}#{digest}"


def normalize_value(kind: str, value: str) -> str:
    """Tekilleştirme/arama anahtarı. Görünen değer değişmez."""
    raw = " ".join(str(value or "").split())
    if not raw:
        raise PoolValidationError("deger bos olamaz")
    if kind == "email":
        return _fit_index(raw.casefold())
    if kind == "url":
        # URL parçası (#start-of-content) tekilleştirmeyi bölmesin.
        return _fit_index(raw.casefold().split("#")[0].rstrip("/."))
    if kind == "domain":
        return _fit_index(raw.casefold().rstrip("/."))
    if kind == "username":
        return _fit_index(raw.casefold().lstrip("@"))
    return _fit_index(_fold(raw))


def _clean_label(label: str | None, fallback: str) -> str:
    text = " ".join(str(label or fallback).split())
    if not text:
        text = fallback
    return text[:MAX_LABEL]


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PoolValidationError("deger bos olamaz")
    if _CONTROL_RE.search(text):
        text = _CONTROL_RE.sub(" ", text)
    if len(text) > MAX_VALUE:
        raise PoolValidationError(f"deger en fazla {MAX_VALUE} karakter olabilir")
    return " ".join(text.split()) if "\n" not in text else text


def validate_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value not in POOL_KINDS:
        raise PoolValidationError(f"gecersiz tur: {kind}")
    return value


def _merge_json(current: Any, incoming: Any) -> dict[str, Any]:
    result = dict(current or {})
    if isinstance(incoming, dict):
        result.update(incoming)
    return result


async def create_item(
    db: AsyncSession,
    *,
    case_id: str,
    kind: str,
    label: str | None,
    value: str,
    source: str = "user",
    status: str | None = None,
    confidence: float = 0.0,
    attributes: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> tuple[PoolItem, bool]:
    """Havuza kayıt ekler; aynı `(kind, normalized)` varsa onu döndürür.

    Dönüş: `(item, created)`. Kullanıcı aynı değeri yeniden eklerse mevcut
    kayıt `confirmed`/`user` olarak canlanır; AI önerisi kullanıcı girdisini
    asla gölgelemez.
    """
    normalized_kind = validate_kind(kind)
    clean_value = _clean_value(value)
    normalized = normalize_value(normalized_kind, clean_value)
    if normalized_kind == "phone":
        phone = parse_phone(clean_value)
        if phone is not None:
            # Korelasyon (same_phone) normalize numarayı bu öznitelikten okur.
            attributes = {**(attributes or {}), **phone_attributes(phone)}
    if normalized_kind == "event" and attributes:
        context = " ".join(
            str(attributes.get(key) or "").strip()
            for key in ("date", "place", "organization")
            if str(attributes.get(key) or "").strip()
        )
        if context:
            # Arama ve tekilleştirme yer/kurum bağlamını da görsün.
            normalized = _fit_index(f"{normalized} {normalize_value('event', context)}")
    source = str(source or "user").strip().lower()
    if source not in POOL_SOURCES:
        raise PoolValidationError(f"gecersiz kaynak: {source}")
    status = str(status or ("confirmed" if source == "user" else "candidate")).strip().lower()
    if status not in POOL_STATUSES:
        raise PoolValidationError(f"gecersiz durum: {status}")
    if source != "user" and status == "confirmed":
        # Değişmez: AI/tarama/pivot yalnız proposed/candidate yazar.
        status = "proposed"
    confidence = max(0.0, min(1.0, float(confidence or 0.0)))

    existing = (
        await db.execute(
            select(PoolItem).where(
                PoolItem.case_id == case_id,
                PoolItem.kind == normalized_kind,
                PoolItem.normalized == normalized,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        changed = False
        if source == "user" and existing.source != "user":
            existing.source = "user"
            existing.status = "confirmed"
            changed = True
        if existing.status == "rejected" and source == "user":
            existing.status = "confirmed"
            changed = True
        existing.attributes = _merge_json(existing.attributes, attributes)
        existing.provenance = _merge_json(existing.provenance, provenance)
        # AI, kullanıcının girdiği etiketi ezemez; yalnız kendi kaydını günceller.
        if label and (source == "user" or existing.source != "user"):
            existing.label = _clean_label(label, existing.label)
            changed = True
        if changed:
            await db.flush()
        return existing, False

    item = PoolItem(
        case_id=case_id,
        kind=normalized_kind,
        label=_clean_label(label, clean_value[:MAX_LABEL]),
        value=clean_value,
        normalized=normalized,
        source=source,
        status=status,
        confidence=confidence,
        attributes=dict(attributes or {}),
        provenance=dict(provenance or {}),
        created_by=created_by,
    )
    db.add(item)
    await db.flush()
    return item, True


async def seed_pool_for_case(
    db: AsyncSession,
    *,
    case: Case,
    target: Target | None,
    seeds: Sequence[Seed] = (),
    created_by: str | None = None,
) -> int:
    """Vaka girdilerini havuza aktarır. Idempotenttir; eklenen yeni kayıt sayısını döner."""
    created = 0

    async def add(
        kind: str,
        label: str,
        value: str | None,
        *,
        source: str = "user",
        status: str = "confirmed",
        attributes: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        nonlocal created
        if not value or not str(value).strip():
            return
        try:
            _, was_created = await create_item(
                db,
                case_id=case.id,
                kind=kind,
                label=label,
                value=str(value),
                source=source,
                status=status,
                attributes=attributes,
                provenance=provenance,
                created_by=created_by,
            )
        except PoolValidationError:
            return
        if was_created:
            created += 1

    if target is not None:
        full_name = f"{target.first_name or ''} {target.last_name or ''}".strip()
        await add("name", "Ad Soyad", full_name)
        await add("username", "Kullanıcı adı", target.nickname)
        await add("email", "E-posta", target.email)
        await add("phone", "Telefon", target.phone)
        await add("domain", "Alan adı", target.domain)
        await add("ip", "IP adresi", target.ip)
        hints = target.hints or {}
        await add("location", "Şehir", hints.get("city"))
        await add(
            "organization",
            "Kurum",
            hints.get("company"),
            attributes={"role": "company"},
        )
        await add(
            "organization",
            "Okul",
            hints.get("school"),
            attributes={"role": "school"},
        )
        extra = hints.get("extra")
        await add("note", "İpucu", extra if isinstance(extra, str) else None)

    for seed in seeds:
        platform = seed.platform or "platform"
        await add(
            "username",
            f"{platform} kullanıcı adı",
            seed.username,
            provenance={"platform": seed.platform, "seed_id": seed.id},
        )
        await add(
            "url",
            f"{platform} profili",
            seed.url,
            provenance={"platform": seed.platform, "seed_id": seed.id},
        )
        await add(
            "email",
            f"{platform} e-postası",
            seed.email,
            provenance={"platform": seed.platform, "seed_id": seed.id},
        )

    await add("note", "Vaka amacı", case.purpose, attributes={"field": "purpose"})
    await add("note", "Vaka notu", case.notes, attributes={"field": "notes"})

    await db.flush()
    return created


async def ensure_case_pool(db: AsyncSession, case_id: str) -> int:
    """Var olan vaka için havuzu tembel oluşturur (eski kayıtlar için)."""
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        return 0
    existing = (
        await db.execute(
            select(func.count()).select_from(PoolItem).where(PoolItem.case_id == case_id)
        )
    ).scalar_one()
    if existing:
        return 0
    target = (
        await db.execute(select(Target).where(Target.case_id == case_id))
    ).scalar_one_or_none()
    seeds = (
        (await db.execute(select(Seed).where(Seed.case_id == case_id))).scalars().all()
    )
    try:
        created = await seed_pool_for_case(
            db, case=case, target=target, seeds=seeds, created_by="system"
        )
        await db.commit()
        return created
    except IntegrityError:
        # Eşzamanlı iki GET aynı havuzu oluşturmaya çalıştı; diğeri kazandı.
        await db.rollback()
        return 0


async def add_comment_item(
    db: AsyncSession, *, comment: CaseComment, created_by: str | None = None
) -> PoolItem | None:
    """Vaka yorumunu havuzda not olarak görünür kılar."""
    if not comment.body or not comment.body.strip():
        return None
    item, _ = await create_item(
        db,
        case_id=comment.case_id,
        kind="note",
        label=f"Yorum — {comment.author}",
        value=comment.body,
        source="user",
        status="confirmed",
        provenance={"comment_id": comment.id, "author": comment.author},
        created_by=created_by or comment.author,
    )
    return item


def _query_conditions(q: str | None) -> list[Any]:
    text = " ".join(str(q or "").split())[:MAX_QUERY]
    if not text:
        return []
    tokens = text.split()
    # Havuzdaki `normalized` alanı ASCII katlanmış hâlidir; arama da katlanır.
    folded_tokens = [
        folded for token in tokens if (folded := _fold(token))
    ]
    conditions = []
    for token in folded_tokens[:8]:
        escaped = (
            token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        conditions.append(
            or_(
                PoolItem.normalized.like(pattern, escape="\\"),
                func.lower(PoolItem.label).like(pattern, escape="\\"),
            )
        )
    return conditions


async def search_items(
    db: AsyncSession,
    *,
    case_id: str,
    q: str | None = None,
    kinds: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
    statuses: Iterable[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PoolItem], int]:
    """Havuz araması: kelimeler AND, filtreler OR."""

    conditions: list[Any] = [PoolItem.case_id == case_id]
    conditions.extend(_query_conditions(q))

    kind_list = [k for k in (kinds or []) if k in POOL_KINDS]
    if kind_list:
        conditions.append(PoolItem.kind.in_(kind_list))
    source_list = [s for s in (sources or []) if s in POOL_SOURCES]
    if source_list:
        conditions.append(PoolItem.source.in_(source_list))
    status_list = [s for s in (statuses or []) if s in POOL_STATUSES]
    if status_list:
        conditions.append(PoolItem.status.in_(status_list))

    where = and_(*conditions)
    total = (
        await db.execute(select(func.count()).select_from(PoolItem).where(where))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(PoolItem)
                .where(where)
                .order_by(PoolItem.created_at.desc(), PoolItem.id.asc())
                .limit(max(1, min(200, int(limit))))
                .offset(max(0, int(offset)))
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def context_items(
    db: AsyncSession,
    *,
    case_id: str,
    limit: int = 80,
    statuses: Sequence[str] = ("confirmed", "candidate"),
) -> list[PoolItem]:
    """Ajan/planlayıcı için en yüksek değerli havuz kayıtları."""
    rows = (
        (
            await db.execute(
                select(PoolItem)
                .where(
                    PoolItem.case_id == case_id,
                    PoolItem.status.in_(statuses),
                )
                .order_by(
                    func.coalesce(PoolItem.confidence, 0).desc(),
                    PoolItem.created_at.desc(),
                )
                .limit(max(1, min(200, int(limit))))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def format_context(items: Sequence[PoolItem], limit: int = 60) -> str:
    """LLM/planlayıcı istemine giden kısa, satır bazlı havuz özeti."""
    lines: list[str] = []
    for item in items[:limit]:
        value = " ".join(str(item.value).split())
        if len(value) > 240:
            value = value[:237] + "..."
        extra = ""
        if item.kind == "event" and isinstance(item.attributes, dict):
            parts = [
                str(item.attributes.get(key) or "").strip()
                for key in ("date", "place", "organization")
            ]
            extra = " | ".join(part for part in parts if part)
            if extra:
                extra = f" ({extra})"
        if item.kind == "email" and isinstance(item.attributes, dict):
            check = item.attributes.get("breach_check")
            if isinstance(check, dict):
                try:
                    count = int(check.get("count") or 0)
                except (TypeError, ValueError):
                    count = 0
                extra = f" (sızıntı: {count} ihlal)" if count else " (sızıntı: ihlal yok)"
        lines.append(
            f"- [{item.kind}] {item.label}: {value}{extra} (kaynak={item.source})"
        )
    return "\n".join(lines)


def mark_reasoning_stale(case: Case, *, now: datetime | None = None) -> None:
    """Kullanıcı havuz kararı/düzenlemesi sonrası muhakeme çıktılarını bayatlatır.

    `reasoning_stale_at` yalnız ilk bayatlamada yazılır; art arda kararlar aynı
    zaman damgasını korur ki istemci "ne zamandan beri bayat" bilgisini tutarlı görsün.
    Commit çağırana aittir.
    """
    if case.reasoning_stale_at is None:
        case.reasoning_stale_at = now or datetime.now(timezone.utc)


async def clear_reasoning_stale(db: AsyncSession, case_id: str) -> None:
    """Korelasyon/hipotez üretimi muhakemeyi tazeler; bayat damgasını temizler.

    Çağıranın transaction'ında çalışır; ayrı commit açmaz.
    """
    await db.execute(
        update(Case)
        .where(Case.id == case_id, Case.reasoning_stale_at.is_not(None))
        .values(reasoning_stale_at=None)
    )


async def bulk_set_status(
    db: AsyncSession,
    *,
    case_id: str,
    item_ids: Sequence[str],
    status: str,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Yalnız bu vakanın öğelerine toplu durum yazar; tek transaction çağıranın.

    Dönüş: `(updated, skipped)`. Tekrarlanan kimlik bir kez sayılır; bu vakanın
    dışındaki veya var olmayan kimlikler `skipped` olur. `status` yalnız
    `confirmed|rejected` olabilir (kullanıcı kararı; AI yolu bu işlevi çağırmaz).
    """
    if status not in BULK_STATUSES:
        raise PoolValidationError(f"gecersiz toplu durum: {status}")
    unique_ids = list(dict.fromkeys(str(item_id) for item_id in item_ids))
    if not unique_ids:
        raise PoolValidationError("item_ids bos olamaz")
    if len(unique_ids) > MAX_BULK_ITEMS:
        raise PoolValidationError(f"item_ids en fazla {MAX_BULK_ITEMS} olabilir")

    owned = (
        (
            await db.execute(
                select(PoolItem.id).where(
                    PoolItem.case_id == case_id, PoolItem.id.in_(unique_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    owned_ids = list(owned)
    if owned_ids:
        await db.execute(
            update(PoolItem)
            .where(PoolItem.case_id == case_id, PoolItem.id.in_(owned_ids))
            .values(status=status, updated_at=now or datetime.now(timezone.utc))
        )
    await db.flush()
    return len(owned_ids), len(unique_ids) - len(owned_ids)

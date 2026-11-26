import ipaddress
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import showis.db.session as db_session_module
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from showis.core.security import ROLE_ANALYST, Actor, require_token
from showis.db.models import (
    AIProvider,
    Case,
    CaseComment,
    Edge,
    Entity,
    Evidence,
    Finding,
    PoolItem,
    PoolRelation,
    Report,
    ResearchMessage,
    ResearchSession,
    Scan,
    SupplyChainEdge,
    SupplyChainNode,
)
from showis.db.models import Seed as DbSeed
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.services.ai_providers import allowed_models
from showis.services.audit import list_audit_logs, record_request_audit
from showis.services.connectors_factory import build_connectors, build_llm_client
from showis.services.identity_graph import (
    compute_identity_clusters,
    corroboration_signals,
)
from showis.services.maltego import build_maltego_xml
from showis.services.misp import build_misp_event
from showis.services.pool import add_comment_item, seed_pool_for_case
from showis.services.report_service import create_report, purge_case_reports
from showis.services.retention import _evidence_file_path
from showis.services.scanner import ScannerService
from showis.services.stix import build_stix_bundle
from showis.services.targets import db_target_to_contract as _db_target_to_contract
from showis_contracts import FindingStatus, Platform, Target
from showis_engine.policy import guard_target
from showis_engine.scan_modes import budget_for_mode, estimate_scan, norm_mode
from showis_engine.variants import normalize_turkish
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger("showis.api.cases")

router = APIRouter(prefix="/cases", tags=["cases"])

#: Girdi doğrulama sınırları (SEC-14)
MAX_NAME = 256
MAX_PURPOSE = 4000
MAX_NOTES = 4000
MAX_HINT_VALUE = 200
MAX_HINTS = 10
MAX_UID_FIELD = 512
MAX_NICKNAME = 128
#: Nick tabanı: `variants.normalize_turkish` sonrası en az bu kadar karakter (spec §6.1)
MIN_NICK_BASE = 3
HINT_KEYS = {"school", "city", "company", "username", "email", "extra"}
USERNAME_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^\+?[1-9][0-9]{6,14}$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)

#: `budget` anahtarları — mod profilinin üstüne çıkılamaz (SEC-05)
_BUDGET_KEYS = ("max_tasks", "max_http_requests", "max_llm_calls", "max_wall_seconds")


def _clean_hints(hints: dict[str, Any]) -> dict[str, Any]:
    if not hints:
        return {}
    if len(hints) > MAX_HINTS:
        raise ValueError(f"hints en fazla {MAX_HINTS} anahtar içerebilir")
    unknown = sorted(set(hints) - HINT_KEYS)
    if unknown:
        raise ValueError(f"izinli hint anahtarları: {sorted(HINT_KEYS)} (gelen: {unknown})")
    cleaned: dict[str, Any] = {}
    for key, value in hints.items():
        if isinstance(value, (dict, list, tuple, set)):
            raise ValueError(f"hints.{key} iç içe yapı olamaz")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"hints.{key} metin veya sayı olmalı")
        if isinstance(value, str):
            value = " ".join(value.split())
            if len(value) > MAX_HINT_VALUE:
                raise ValueError(f"hints.{key} en fazla {MAX_HINT_VALUE} karakter")
        cleaned[key] = value
    return cleaned


class SeedInput(BaseModel):
    platform: Platform
    url: str | None = Field(default=None, max_length=MAX_UID_FIELD)
    username: str | None = Field(default=None, pattern=USERNAME_PATTERN)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            return None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("url http(s) ile başlamalı")
        return value.strip()

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        candidate = value.strip()
        if not _EMAIL_RE.match(candidate):
            raise ValueError("email biçimi geçersiz")
        return candidate


class TargetInput(BaseModel):
    """Hedef kimliği — ad-soyad **veya** nick (spec §6.1).

    Sıra: alan doğrulayıcıları (ad temizleme, `_clean_hints`, nick `@` temizliği)
    çalışır; ardından `_check_identity` deprecated `hints["username"]`'i nick'e
    taşır ve kimlik kurallarını uygular. `kind` varsayılanı `"name"` olduğundan
    `kind` göndermeyen eski istemciler aynı davranır.

    | Mod | Zorunlu | Opsiyonel |
    |---|---|---|
    | `name` | `first_name` (≥2) | `last_name`, `nickname` |
    | `handle` | `nickname` (normalize sonrası ≥3 + ≥1 alfanümerik) | `first_name`, `last_name` |
    """

    kind: Literal["name", "handle", "email", "phone", "domain", "ip"] = "name"
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)
    nickname: str = Field(default="", max_length=MAX_NICKNAME)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=32)
    domain: str = Field(default="", max_length=253)
    ip: str = Field(default="", max_length=64)
    hints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("first_name", "last_name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return " ".join(str(value).split())

    @field_validator("nickname")
    @classmethod
    def _strip_at(cls, value: str) -> str:
        # Ham değer saklanır (görüntüleme için); yalnız baştaki `@` ve boşluklar düşer.
        return str(value).strip().lstrip("@").strip()

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, value: str) -> str:
        raw = re.sub(r"[\s().-]+", "", str(value))
        return raw.strip()

    @field_validator("domain")
    @classmethod
    def _normalize_domain(cls, value: str) -> str:
        return str(value).strip().lower().rstrip(".")

    @field_validator("ip")
    @classmethod
    def _normalize_ip(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("hints")
    @classmethod
    def _check_hints(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_hints(value)

    @staticmethod
    def _check_nickname(value: str) -> None:
        """Yalnız **açıkça verilen** `nickname` için geçerli kural (R14).

        `hints["username"]`'den taşınan değer bu denetimden geçmez: eski payload'lar
        422'ye dönmesin diye legacy girdi toleransı vardır; taşınan değer sadece `@`
        temizliği ve `MAX_NICKNAME` kırpmasından geçer.
        """
        # R14: ≥3/alnum kuralı explicit nick'e uygulanır; `hints.username` legacy tolerans.
        # Bedeli: DB'de 2 karakterlik legacy handle kalabilir; `variants.py` exact-handle
        # varyantını `<3` kuralıyla elemediği için böyle değerler yine aranabilir.
        normalized = normalize_turkish(value)
        if len(normalized) < MIN_NICK_BASE or not any(ch.isalnum() for ch in normalized):
            raise ValueError(
                f"nickname normalize edildikten sonra en az {MIN_NICK_BASE} karakter "
                "olmalı ve en az bir alfanümerik karakter içermeli"
            )

    @model_validator(mode="after")
    def _check_identity(self) -> "TargetInput":
        supplied = self.nickname
        #: `hints.username` deprecated: varsa tek kimlik kaynağına (`nickname`) taşınır
        #: ve hints'ten düşer. Taşınan değer **yeniden doğrulanmaz** (R14): legacy
        #: payload'lar 422'ye dönmesin; nick kuralı yalnız açıkça verilen `nickname` için.
        #: Yine de `VARCHAR(128)` sınırı korunur — 129–200 karakterlik eski bir hint
        #: DB'ye sığmadığı için insert 500'e düşmemeli; değer `MAX_NICKNAME`'e kırpılır.
        migrated = self.hints.pop("username", None)
        if migrated is not None and not self.nickname:
            self.nickname = str(migrated).strip().lstrip("@").strip()[:MAX_NICKNAME]

        if self.kind == "name" and len(self.first_name) < 2:
            raise ValueError("ad en az 2 karakter olmalı (soyad opsiyonel)")
        if supplied:
            self._check_nickname(supplied)
        if self.kind == "handle" and not self.nickname:
            raise ValueError(
                "handle modunda nickname zorunlu (normalize sonrası en az "
                f"{MIN_NICK_BASE} karakter)"
            )
        if self.kind == "email":
            if not self.email or not _EMAIL_RE.match(self.email):
                raise ValueError("geçerli bir e-posta adresi gerekli")
        if self.kind == "phone":
            if not self.phone or not _PHONE_RE.match(self.phone):
                raise ValueError("telefon E.164 biçiminde olmalı (ör. +905551112233)")
        if self.kind == "domain":
            if not self.domain or not _DOMAIN_RE.match(self.domain):
                raise ValueError("geçerli bir alan adı gerekli (ör. example.com)")
        if self.kind == "ip":
            try:
                ipaddress.ip_address(self.ip)
            except ValueError as exc:
                raise ValueError("geçerli bir IPv4/IPv6 adresi gerekli") from exc
        return self


class CaseCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=MAX_NAME)
    purpose: str = Field(min_length=10, max_length=MAX_PURPOSE)
    #: consent | legitimate_interest | self_check
    legal_basis: Literal["consent", "legitimate_interest", "self_check"]
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    retention_days: int = Field(default=30, ge=1, le=365)
    #: Hukuki dayanak + robots/ToS operatör onayı. Eski API istemcileri için
    #: varsayılan `True`; yeni arayüz açık onay gönderir. `False` reddedilir.
    authorization_confirmed: bool = True
    target: TargetInput
    seeds: list[SeedInput] = Field(default_factory=list, max_length=20)


class MonitorPatch(BaseModel):
    enabled: bool
    interval_hours: int = Field(default=24, ge=1, le=168)
    mode: Literal["O1", "O2", "O3", "o1", "o2", "o3"] = "O2"


class OwnerPatch(BaseModel):
    owner: str | None = Field(default=None, max_length=128)


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="operator", min_length=1, max_length=128)


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, max_length=2000)


class ScanStartRequest(BaseModel):
    mode: Literal["O1", "O2", "O3", "o1", "o2", "o3"] = "O2"
    #: Geriye dönük uyumlu alan: değerler mod profilinin üstüne çıkamaz,
    #: bilinmeyen anahtarlar yok sayılır (SEC-05).
    budget: dict[str, Any] | None = None
    #: Aktif AI profilinin izinli modellerinden biri; boşsa profil varsayılanı.
    llm_model: str | None = Field(default=None, max_length=128)



@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(
    data: CaseCreateRequest,
    request: Request,
    actor: Actor = Depends(require_token),
    db: AsyncSession = Depends(get_db),
):
    # SEC-07: reşit-olmayan / uygun olmayan hedef sinyali burada durdurulur.
    # Spec §10: `guard_target` ad + soyad + nick + hints birleşimini tarar, bu yüzden
    # kontrat hedefi gerçek alanlarla kurulur (nick ayrıca `first_name`'e paketlenmez).
    if not data.authorization_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Hukuki dayanak ve robots.txt/kullanım koşulları onayı gerekli "
                "(authorization_confirmed=true)."
            ),
        )

    contract_target = Target(
        kind=data.target.kind,
        first_name=data.target.first_name,
        last_name=data.target.last_name,
        nickname=data.target.nickname,
        email=data.target.email,
        phone=data.target.phone,
        domain=data.target.domain,
        ip=data.target.ip,
        hints=data.target.hints,
    )
    guard_target(contract_target)
    if contract_target.review_required:
        await record_request_audit(
            db=db,
            request=request,
            action="target.blocked",
            case_id=None,
            target="Target: redacted",
            details={
                "reason": "review_required",
                "hints_keys": sorted(data.target.hints),
                "stage": "case.create",
            },
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "Hedef inceleme gerektiriyor: reşit olmayan kişi sinyali tespit edildi "
                "(AUP md.7). Bu hedef için tarama başlatılamaz."
            ),
        )

    case = Case(
        name=data.name,
        purpose=data.purpose,
        legal_basis=data.legal_basis,
        notes=data.notes,
        retention_days=data.retention_days,
        owner=actor.username if actor.role == ROLE_ANALYST else None,
    )
    db.add(case)
    await db.flush()

    target = DbTarget(
        case_id=case.id,
        first_name=data.target.first_name,
        last_name=data.target.last_name,
        nickname=data.target.nickname,
        email=data.target.email or None,
        phone=data.target.phone or None,
        domain=data.target.domain or None,
        ip=data.target.ip or None,
        kind=data.target.kind,
        hints=data.target.hints,
        review_required=False,
    )
    db.add(target)

    seed_rows: list[DbSeed] = []
    for s in data.seeds:
        seed = DbSeed(
            case_id=case.id,
            platform=s.platform,
            url=s.url,
            username=s.username,
            email=s.email,
        )
        db.add(seed)
        seed_rows.append(seed)

    # Havuz, vaka girdilerinin tek görünür kaynağıdır; AI araştırması ve
    # planlayıcı buradan beslenir.
    await db.flush()
    pool_created = await seed_pool_for_case(
        db, case=case, target=target, seeds=seed_rows, created_by=actor.username
    )

    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="case.create",
        case_id=case.id,
        target=f"Case: {case.name}",
        details={
            "legal_basis": case.legal_basis,
            "purpose": case.purpose,
            "authorization_confirmed": data.authorization_confirmed,
            "robots_policy": "honor",
            "public_sources_only": True,
            "pool_items_created": pool_created,
        },
    )

    return {
        "id": case.id,
        "name": case.name,
        "status": case.status,
        "created_at": case.created_at.isoformat(),
    }


#: T-358: vaka listesi filtre/sıralama sözleşmesi
CASE_STATUSES: frozenset[str] = frozenset({"active", "review_pending", "deleted"})
CASE_MODES: frozenset[str] = frozenset({"O1", "O2", "O3"})
CASE_SORTS: frozenset[str] = frozenset(
    {"created_desc", "created_asc", "name_asc", "updated_desc"}
)
DEFAULT_CASE_LIMIT = 50
MAX_CASE_LIMIT = 200
#: T-366: arama metni ve derin offset sınırları (kötüye kullanım/derin sayfalama)
MAX_CASE_Q_LENGTH = 128
MAX_CASE_OFFSET = 10_000
#: `legacy=1` tam dökümü: dizi sözleşmesi bozulmadan en fazla bu kadar vaka
LEGACY_MAX_ROWS = 500

#: T-376: kontrol karakterleri (C0 + DEL) — DB/encoding katmanına ulaşmadan reddedilir.
#: `q=a%00b` asyncpg'de `invalid byte sequence for encoding "UTF8": 0x00` → 500/502 idi.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def reject_control_chars(value: str | None, *, param: str) -> str | None:
    """Metin parametrelerinde C0/DEL karakterlerini reddeder (422).

    `%00` gibi percent-encoded girdiler decode edildikten sonra buraya düşer.
    """
    if value is None:
        return None
    if _CONTROL_CHARS_RE.search(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Geçersiz girdi: '{param}' kontrol karakteri içeriyor",
        )
    return value


def _escape_literal(value: str) -> str:
    r"""`LIKE` deseni için `%`, `_` ve `\` kaçışlanır (kullanıcı joker üretemez)."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _escape_like(value: str) -> str:
    """Alt dizgi deseni: literal kaçışı + iki yanına `%`."""
    return f"%{_escape_literal(value)}%"


#: Türkçe/aksanlı harfleri ASCII karşılığına indirger. `İ/I/ı` aynı harfe
#: katlanır: `İnceleme`, `inceleme` ve `INCELEME` aynı aramada buluşur.
_TR_SEARCH_MAP = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
        "Â": "a",
        "â": "a",
        "Î": "i",
        "î": "i",
        "Û": "u",
        "û": "u",
    }
)


def _fold_search_text(value: str) -> str:
    """Arama metnini katlar; kelime sınırlarını (boşluk) **korur**."""
    folded = str(value or "").translate(_TR_SEARCH_MAP).lower()
    return " ".join(folded.split())


def _fold_sql(column: Any) -> Any:
    """DB tarafında Türkçe harf katlaması (SQLite ve Postgres uyumlu).

    `lower()` SQLite'ta yalnız ASCII'yi katlar; bu yüzden önce Türkçe harfler
    `replace()` ile ASCII'ye çevrilir. Böylece `İnceleme` ile `inceleme`
    aynı ifadeye dönüşür ve ILIKE/LIKE eşleşmesi DB'den bağımsız çalışır.
    """
    expression = column
    for source, target in (
        ("İ", "i"),
        ("I", "i"),
        ("ı", "i"),
        ("Ş", "s"),
        ("ş", "s"),
        ("Ğ", "g"),
        ("ğ", "g"),
        ("Ü", "u"),
        ("ü", "u"),
        ("Ö", "o"),
        ("ö", "o"),
        ("Ç", "c"),
        ("ç", "c"),
        ("Â", "a"),
        ("â", "a"),
        ("Î", "i"),
        ("î", "i"),
        ("Û", "u"),
        ("û", "u"),
    ):
        expression = func.replace(expression, source, target)
    return func.lower(expression)


def _search_tokens(text: str, *, limit: int = 8) -> list[str]:
    """Arama metnini kelimelere ayırır (en fazla `limit`; aşırı uzun sorgu yok)."""
    return [token for token in text.split() if token][:limit]


def _token_match(token: str) -> Any:
    """Tek kelimenin vaka adı/amacı veya hedef kimliğinde geçmesi."""
    folded = _fold_search_text(token)
    if not folded:
        return None
    pattern = _escape_like(folded)
    target_fields = (
        DbTarget.first_name,
        DbTarget.last_name,
        DbTarget.nickname,
    )
    target_conds = [
        _fold_sql(field).like(pattern, escape="\\")
        for field in target_fields
    ]
    case_conds = [
        _fold_sql(Case.name).like(pattern, escape="\\"),
        _fold_sql(Case.purpose).like(pattern, escape="\\"),
    ]
    target_hit = select(DbTarget.case_id).where(or_(*target_conds))
    return or_(*case_conds, Case.id.in_(target_hit))


def _relevance_score(token: str) -> Any:
    """Tek kelime için 0..~100 arası ilgi puanı (SQL ifadesi)."""
    folded = _fold_search_text(token)
    if not folded:
        return 0
    contains = _escape_like(folded)
    prefix = f"{_escape_literal(folded)}%"
    name = _fold_sql(Case.name)
    purpose = _fold_sql(Case.purpose)
    first = _fold_sql(DbTarget.first_name)
    last = _fold_sql(DbTarget.last_name)
    nickname = _fold_sql(DbTarget.nickname)
    full = func.trim(
        func.coalesce(_fold_sql(DbTarget.first_name), "")
        + " "
        + func.coalesce(_fold_sql(DbTarget.last_name), "")
    )
    target_score = (
        select(
            func.max(
                case(
                    (full == folded, 100),
                    (full.like(prefix, escape="\\"), 90),
                    (nickname == folded, 95),
                    (nickname.like(prefix, escape="\\"), 85),
                    (first == folded, 70),
                    (last == folded, 70),
                    (full.like(contains, escape="\\"), 55),
                    (nickname.like(contains, escape="\\"), 50),
                    (first.like(contains, escape="\\"), 35),
                    (last.like(contains, escape="\\"), 35),
                    else_=0,
                )
            )
        )
        .where(DbTarget.case_id == Case.id)
        .correlate(Case)
        .scalar_subquery()
    )
    return (
        case(
            (name == folded, 100),
            (name.like(prefix, escape="\\"), 80),
            (name.like(contains, escape="\\"), 60),
            else_=0,
        )
        + func.coalesce(target_score, 0)
        + case((purpose.like(contains, escape="\\"), 20), else_=0)
    )


def _relevance_order(text: str) -> list[Any]:
    """Kelime bazlı ilgi sırası: her kelimenin en iyi eşleşmesi toplanır."""
    tokens = _search_tokens(text)
    if not tokens:
        return []
    score: Any = 0
    for token in tokens:
        score = score + _relevance_score(token)
    return [score.desc(), Case.created_at.desc(), Case.id.asc()]


def _case_filters(q: str | None, case_status: str | None) -> list[Any]:
    """Arama + durum filtrelerini SQL koşullarına çevirir (geçersiz değer → 422).

    Çok kelimeli aramada **her kelime** ayrı ayrı aranır ve hepsi eşleşmelidir
    (AND). Böylece `kerem boz` sorgusu `first_name=Kerem`, `last_name=Boz`
    olan hedefi bulur; tek alanda birleşik yazılması gerekmez.
    """
    conditions: list[Any] = []

    text = (q or "").strip()
    if text:
        token_conditions = [
            condition
            for token in _search_tokens(text)
            if (condition := _token_match(token)) is not None
        ]
        if token_conditions:
            conditions.append(and_(*token_conditions))

    if case_status is not None and case_status.strip():
        value = case_status.strip().lower()
        if value not in CASE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Geçersiz status: "
                    f"{value} (izinli: {', '.join(sorted(CASE_STATUSES))})"
                ),
            )
        conditions.append(Case.status == value)

    return conditions


@router.get("", response_model=None)
async def list_cases(
    q: str | None = Query(None, max_length=MAX_CASE_Q_LENGTH),
    status_filter: str | None = Query(None, alias="status"),
    mode: str | None = None,
    limit: int = Query(DEFAULT_CASE_LIMIT, ge=1, le=MAX_CASE_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_CASE_OFFSET),
    sort: Literal[
        "created_desc", "created_asc", "name_asc", "updated_desc", "relevance"
    ] = "created_desc",
    legacy: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Vaka listesi — arama, filtre, sıralama ve sayfalama (T-358).

    Parametreler:
    * `q` — vaka adı, amaç ve hedef ad/soyad içinde büyük/küçük harf duyarsız arama
      (`%`/`_` kaçışlanır).
    * `status` — vaka durumu (`active|review_pending|deleted`); başka değer → 422.
    * `mode` — **son taramanın** modu (`O1|O2|O3`); başka değer → 422.
    * `limit` (varsayılan 50, 1..200), `offset` (>=0), `sort`
      (`created_desc|created_asc|name_asc|updated_desc`).
    * `legacy=1` — eski düz liste yanıtı (web uyumu); sayfalama uygulanmaz, ancak
      döküm **en fazla `LEGACY_MAX_ROWS` (500)** vaka ile sınırlıdır (aşılırsa log uyarısı).

    N+1 yok: son tarama bilgisi korelasyonlu alt sorgularla tek sorguda gelir,
    hedef/seed ilişkileri `selectinload` ile toplu yüklenir.
    """
    # T-376: kontrol karakterleri (NUL vb.) DB katmanına ulaşmadan 422
    q = reject_control_chars(q, param="q")
    status_filter = reject_control_chars(status_filter, param="status")
    reject_control_chars(mode, param="mode")

    if mode is not None and mode.strip():
        normalized_mode = norm_mode(mode)
        if mode.strip().upper() not in CASE_MODES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Geçersiz mode: {mode} (izinli: {', '.join(sorted(CASE_MODES))})"
                ),
            )
    else:
        normalized_mode = None

    conditions = _case_filters(q, status_filter)

    # Son tarama: mod / durum / zaman (korelasyonlu alt sorgular → N+1 yok)
    def _latest(column: Any) -> Any:
        return (
            select(column)
            .where(Scan.case_id == Case.id)
            .order_by(Scan.created_at.desc(), Scan.id.desc())
            .limit(1)
            .correlate(Case)
            .scalar_subquery()
        )

    last_scan_mode = _latest(Scan.mode)
    last_scan_at = _latest(Scan.created_at)
    updated_at = func.coalesce(last_scan_at, Case.created_at)

    if normalized_mode is not None:
        conditions.append(last_scan_mode == normalized_mode)

    base = select(Case).where(*conditions)

    if sort == "name_asc":
        ordered = base.order_by(Case.name.asc(), Case.id.asc())
    elif sort == "created_asc":
        ordered = base.order_by(Case.created_at.asc(), Case.id.asc())
    elif sort == "updated_desc":
        ordered = base.order_by(updated_at.desc(), Case.id.asc())
    elif sort == "relevance" and (q or "").strip():
        # q yokken "relevance" anlamsızdır: en yeni kayıt sırasına düş.
        ordered = base.order_by(*_relevance_order((q or "").strip()))
    else:  # created_desc (varsayılan) veya q'suz relevance
        ordered = base.order_by(Case.created_at.desc(), Case.id.asc())

    if legacy:
        # Geriye uyum: eski düz liste — web hâlâ bunu bekliyor. Dizi sözleşmesi korunur,
        # ancak tam döküm sınırsız büyümesin (T-366): ilk `LEGACY_MAX_ROWS` vaka.
        legacy_total = (
            await db.execute(select(func.count(Case.id)).where(*conditions))
        ).scalar_one()
        if legacy_total > LEGACY_MAX_ROWS:
            logger.warning(
                "legacy dump truncated: total=%d limit=%d", legacy_total, LEGACY_MAX_ROWS
            )
        rows = (
            await db.execute(ordered.limit(LEGACY_MAX_ROWS))
        ).scalars().all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "purpose": c.purpose,
                "legal_basis": c.legal_basis,
                "status": c.status,
                "owner": c.owner,
                "retention_days": c.retention_days,
                "created_at": c.created_at.isoformat(),
            }
            for c in rows
        ]

    total = (
        await db.execute(select(func.count(Case.id)).where(*conditions))
    ).scalar_one()

    page = (
        await db.execute(
            ordered.options(selectinload(Case.targets), selectinload(Case.seeds))
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    # Son tarama bilgisi sayfa için tek sorguda (id listesiyle)
    scan_info: dict[str, dict[str, Any]] = {}
    if page:
        page_ids = [c.id for c in page]
        scan_rows = (
            await db.execute(
                select(Scan.case_id, Scan.mode, Scan.status, Scan.created_at).where(
                    Scan.case_id.in_(page_ids)
                )
            )
        ).all()
        for case_id, scan_mode, scan_status, scan_created in scan_rows:
            current = scan_info.get(case_id)
            if current is None or (
                scan_created is not None
                and (current.get("_at") is None or scan_created > current["_at"])
            ):
                scan_info[case_id] = {
                    "mode": scan_mode,
                    "status": scan_status,
                    "_at": scan_created,
                }

    items = []
    for c in page:
        target = c.targets[0] if c.targets else None
        contract = _db_target_to_contract(target) if target else None
        scan = scan_info.get(c.id) or {}
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "purpose": c.purpose,
                "legal_basis": c.legal_basis,
                "status": c.status,
                "owner": c.owner,
                "retention_days": c.retention_days,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "target": (
                    {
                        "kind": contract.kind,
                        "first_name": contract.first_name,
                        "last_name": contract.last_name,
                        "nickname": contract.nickname,
                        "email": contract.email,
                        "phone": contract.phone,
                        "domain": contract.domain,
                        "ip": contract.ip,
                    }
                    if contract
                    else None
                ),
                "seeds_count": len(c.seeds),
                "last_scan": (
                    {
                        "mode": scan.get("mode"),
                        "status": scan.get("status"),
                        "created_at": scan["_at"].isoformat() if scan.get("_at") else None,
                    }
                    if scan
                    else None
                ),
            }
        )

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{case_id}")
async def get_case(case_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Case)
        .options(selectinload(Case.targets), selectinload(Case.seeds))
        .where(Case.id == case_id)
    )
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    target = case.targets[0] if case.targets else None
    contract = _db_target_to_contract(target) if target else None
    return {
        "id": case.id,
        "name": case.name,
        "purpose": case.purpose,
        "legal_basis": case.legal_basis,
        "notes": case.notes,
        "status": case.status,
        "owner": case.owner,
        "retention_days": case.retention_days,
        "created_at": case.created_at.isoformat(),
        "target": {
            "kind": contract.kind,
            "first_name": contract.first_name,
            "last_name": contract.last_name,
            "nickname": contract.nickname,
            "email": contract.email,
            "phone": contract.phone,
            "domain": contract.domain,
            "ip": contract.ip,
            "hints": contract.hints,
        }
        if contract
        else None,
        "seeds": [
            {
                "id": s.id,
                "platform": s.platform,
                "url": s.url,
                "username": s.username,
                "email": s.email,
            }
            for s in case.seeds
        ],
    }


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Case).where(Case.id == case_id))
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    await record_request_audit(
        db=db,
        request=request,
        action="case.delete",
        case_id=case.id,
        target=f"Case: {case.name}",
    )
    # Kanit dosyalari DB cascade'i ile silinmez: satirlardan once diskteki
    # dosyalari kaldir (KVKK md.7 — silme talebi aninda karsilanir).
    storage_keys = (
        await db.execute(select(Evidence.storage_key).where(Evidence.case_id == case_id))
    ).scalars()
    for storage_key in storage_keys:
        path = _evidence_file_path(storage_key)
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                logger.warning("Kanit dosyasi silinemedi: %s", path)

    # T-112/4 (SEC-12): rapor dosyalari DB cascade'i ile silinmez; diskte
    # kalan PDF/JSON (tam PII) kalirsa KVKK md.7 fiilen ihlal edilir.
    failed_paths, removed = await _cleanup_report_files(db, case_id)
    if failed_paths:
        await record_request_audit(
            db=db,
            request=request,
            action="report.cleanup_failed",
            case_id=case.id,
            target=f"Case: {case.name}",
            details={"failed_count": len(failed_paths), "removed_count": removed},
        )
    logger.info(
        "Case silindi: %s (rapor dosyasi silinen=%d, basarisiz=%d)",
        case_id,
        removed,
        len(failed_paths),
    )

    # Toplu silme: ORM iliskileri async'te lazy-load edemez; DB seviyesi
    # ondelete=CASCADE tum cocuk kayitlari temizler. AuditLog'da case_id
    # FK'siz oldugundan silme kaydi korunur.
    await db.execute(delete(Case).where(Case.id == case_id))
    await db.commit()
    return None


def _report_dir() -> Path:
    """Rapor dizini (import aninda degil, cagri aninda env'den okunur)."""
    return Path(os.getenv("SHOWIS_REPORT_DIR", "./data/reports"))


async def _cleanup_report_files(
    db: AsyncSession, case_id: str
) -> tuple[list[str], int]:
    """Vakaya ait rapor dosyalarini siler.

    Iki kaynak birlikte kullanilir: `reports.storage_key` (gercek yol) ve
    `SHOWIS_REPORT_DIR` altinda adinda case_id gecen dosyalar (yetim kalanlar).

    Returns:
        (silinemeyen_yollar, silinen_sayi)
    """
    candidates: set[Path] = set()
    stored = (
        await db.execute(select(Report.storage_key).where(Report.case_id == case_id))
    ).scalars()
    for storage_key in stored:
        if storage_key:
            candidates.add(Path(str(storage_key)))

    report_dir = _report_dir()
    if report_dir.is_dir():
        try:
            candidates.update(report_dir.glob(f"*{case_id}*"))
        except OSError as exc:  # izin vb.
            logger.warning("Rapor dizini taranamadi (%s): %s", report_dir, type(exc).__name__)

    failed: list[str] = []
    removed = 0
    for path in sorted(candidates):
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError as exc:
            # Sessiz yutma yok: log + audit (cagiran taraf kaydi yazar)
            logger.warning(
                "Rapor dosyasi silinemedi (%s): %s", path, type(exc).__name__
            )
            failed.append(str(path))

    # T112-R3: vaka bazli rapor klasoru (`REPORT_DIR/<case>/`) — dosyalar + klasor
    dir_failed, dir_removed = purge_case_reports(case_id)
    failed.extend(dir_failed)
    removed += dir_removed
    return failed, removed


def _use_celery() -> bool:
    return os.getenv("USE_CELERY", "0") == "1"


def _dispatch_celery_task(scan: Scan) -> bool:
    """``showis.scan.run`` görevini kuyruğa atar ve id'yi ``scan.stats``e yazar.

    T-112/1: scan satırı **önce commit edilir** (worker satırı görebilsin), sonra
    görev kuyruğa atılır ve dönen id `scan.stats["celery_task_id"]` olarak hemen
    kalıcılaştırılır. `POST /scans/{id}/cancel` bu alanı okuyup
    `celery_app.control.revoke(task_id, terminate=True)` çağırır.
    Broker erişilemezse sessiz yutma yok: uyarı loglanır ve `False` döner (çağıran
    süreç içi yolu seçer).
    """
    from showis.services.worker import celery_app

    try:
        result = celery_app.send_task("showis.scan.run", args=[scan.id], queue="osint")
    except Exception as exc:  # noqa: BLE001 - broker hatasi tarama baslatmayi bloklamasin
        logger.warning(
            "Celery dispatch basarisiz (scan=%s): %s", scan.id, type(exc).__name__
        )
        return False

    task_id = getattr(result, "id", None)
    if not task_id:
        logger.warning("Celery send_task id dondurmedi (scan=%s)", scan.id)
        return False

    stats = dict(scan.stats or {})
    stats["celery_task_id"] = str(task_id)
    scan.stats = stats
    return True


async def _run_scan_inline(scan_id: str) -> None:
    """Süreç içi tarama (USE_CELERY=0 veya broker erişilemezse)."""
    async with db_session_module.async_session_factory() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == scan_id))
        ).scalar_one_or_none()
        selected_model = (
            (scan.policy_snapshot or {}).get("llm_model") if scan is not None else None
        )
        connectors = await build_connectors(session)
        if selected_model:
            llm_client = await build_llm_client(
                session, model_override=str(selected_model)
            )
        else:
            llm_client = await build_llm_client(session)
        scanner = ScannerService(connectors=connectors, llm_client=llm_client)
        await scanner.execute_scan(scan_id, session)


async def run_scan_in_background(scan_id: str):
    """Geriye dönük giriş noktası (testler ve eski çağrılar bunu yamalar)."""
    if _use_celery():
        from showis.services.worker import celery_app

        celery_app.send_task("showis.scan.run", args=[scan_id], queue="osint")
        return
    await _run_scan_inline(scan_id)


@router.get("/{case_id}/scan/estimate")
async def estimate_case_scan(
    case_id: str, mode: str = "O2", db: AsyncSession = Depends(get_db)
):
    """Tarama baslamadan once tahmini sure/gorev sayisini doner."""
    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Case target not found")
    contract_target = _db_target_to_contract(target)
    return estimate_scan(contract_target, mode)


def _clamp_budget(requested: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """İstemci bütçesini mod profilinin üstüne çıkaramaz (SEC-05).

    Yalnız bilinen anahtarlar dikkate alınır; her değer ``min()`` ile profile
    kırpılır. Sonuç her zaman güvenli mod profilidir.
    """
    # Not: `budget_for_mode` dönüşü mypy'de `Any`'ye düşüyor (engine tarafı
    # import çözümlemesi); yerel anotasyon `no-any-return` uyarısını kapatır.
    profile: dict[str, Any] = budget_for_mode(mode)
    if not requested:
        return profile
    clamped = dict(profile)
    for key in _BUDGET_KEYS:
        if key not in requested:
            continue
        raw = requested[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = int(raw)
        if value <= 0:
            continue
        clamped[key] = min(value, int(profile[key]))
    return clamped


async def _find_active_scan(db: AsyncSession, case_id: str) -> Scan | None:
    """Vakadaki `queued`/`running` taramayi doner (SEC-05 on kontrolu).

    DB seviyesindeki garanti `uq_scans_case_active` kismi unique index'idir;
    bu fonksiyon yalnizca hizli 409 uretir.
    """
    return (
        await db.execute(
            select(Scan)
            .where(Scan.case_id == case_id, Scan.status.in_(("queued", "running")))
            .order_by(Scan.created_at.desc())
        )
    ).scalars().first()


async def _load_contract_target(db: AsyncSession, case_id: str) -> Target | None:
    db_target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    if not db_target:
        return None
    return _db_target_to_contract(db_target)


@router.post("/{case_id}/scan")
async def start_scan(
    case_id: str,
    payload: ScanStartRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Case).where(Case.id == case_id))
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # SEC-07 (defense in depth): planlama öncesi hedef yeniden denetlenir.
    contract_target = await _load_contract_target(db, case_id)
    if contract_target is None:
        raise HTTPException(status_code=404, detail="Case target not found")
    guard_target(contract_target)
    if contract_target.review_required or case.status == "review_pending":
        await record_request_audit(
            db=db,
            request=request,
            action="target.blocked",
            case_id=case_id,
            target="Target: redacted",
            details={"reason": "review_required", "stage": "scan.start"},
        )
        raise HTTPException(
            status_code=422,
            detail="Hedef inceleme gerektiriyor: tarama başlatılamaz (AUP md.7).",
        )

    # SEC-05: aynı vakada tek aktif tarama.
    active = await _find_active_scan(db, case_id)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "scan_already_active",
                "scan_id": active.id,
                "status": active.status,
            },
        )

    mode = norm_mode(payload.mode)
    selected_model = (payload.llm_model or "").strip() or None
    if selected_model:
        provider = (
            await db.execute(
                select(AIProvider).where(
                    AIProvider.enabled.is_(True), AIProvider.is_active.is_(True)
                )
            )
        ).scalars().first()
        allowed = allowed_models(provider) if provider else []
        if allowed and selected_model not in allowed:
            raise HTTPException(
                status_code=422,
                detail="Seçilen model aktif sağlayıcının izinli modellerinde yok",
            )
    policy_snapshot: dict[str, Any] = {"tier": "B", "safe_mode": True}
    if selected_model:
        policy_snapshot["llm_model"] = selected_model
    scan = Scan(
        case_id=case.id,
        status="queued",
        mode=mode,
        budget=_clamp_budget(payload.budget, mode),
        policy_snapshot=policy_snapshot,
        stats={},
    )
    db.add(scan)
    dispatch_failed = False
    try:
        # id'yi flush ile üret (PK sunucu tarafında da oluşsun) ve satırı commit et:
        # Celery worker'ı taramayı ancak satır görünür olduktan sonra koşmalı.
        await db.flush()
        await db.commit()
    except IntegrityError:
        # T-112/2: 0003_active_scan_unique kismi index'i — yaris kaybedildi.
        await db.rollback()
        active = await _find_active_scan(db, case_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "scan_already_active",
                "scan_id": active.id if active else None,
                "status": active.status if active else None,
            },
        ) from None
    await db.refresh(scan)

    if _use_celery():
        # T-112/1: commit sonrası dispatch; task id `scan.stats`a yazılıp
        # hemen kalıcılaştırılır — iptal ucu (scans.py) bu alanı okur.
        dispatch_failed = not _dispatch_celery_task(scan)
        if not dispatch_failed:
            try:
                await db.commit()
            except Exception:  # noqa: BLE001 - stats yazımı taramayı bloklamaz
                logger.warning("celery_task_id yazilamadi (scan=%s)", scan.id)
                await db.rollback()
    await db.refresh(scan)

    await record_request_audit(
        db=db,
        request=request,
        action="scan.start",
        case_id=case.id,
        target=f"Scan: {scan.id}",
        details={
            "mode": scan.mode,
            "budget": scan.budget,
            "budget_clamped": bool(payload.budget),
            "llm_model": (scan.policy_snapshot or {}).get("llm_model"),
            "celery_task_id": (scan.stats or {}).get("celery_task_id"),
            "celery_dispatch_failed": dispatch_failed,
        },
    )

    if dispatch_failed:
        # Broker yok/erişilemez: tarama sessizce kuyrukta kalmasın, süreç içinde koş.
        background_tasks.add_task(_run_scan_inline, scan.id)
    elif not _use_celery():
        background_tasks.add_task(run_scan_in_background, scan.id)

    return {
        "scan_id": scan.id,
        "case_id": case.id,
        "status": scan.status,
        "mode": scan.mode,
    }


#: T-327: bulgu listeleme filtreleri
_FINDING_STATUSES = {status.value for status in FindingStatus}


def _parse_status_filter(raw: str | None) -> list[str]:
    """`?status=candidate,confirmed` → doğrulanmış liste (geçersizde 422)."""
    if raw is None:
        return []
    values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not values:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="status filtresi boş olamaz",
        )
    invalid = sorted({value for value in values if value not in _FINDING_STATUSES})
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Geçersiz status: {', '.join(invalid)} "
                f"(izinli: {', '.join(sorted(_FINDING_STATUSES))})"
            ),
        )
    return list(dict.fromkeys(values))


def _parse_min_authenticity(raw: float | None) -> float | None:
    """`?min_authenticity=0.35` → 0..1 aralık doğrulaması (aksi halde 422)."""
    if raw is None:
        return None
    if not math.isfinite(raw) or raw < 0 or raw > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="min_authenticity 0..1 aralığında olmalı",
        )
    return raw


_FINDING_SORTS = frozenset(
    {"score_desc", "score_asc", "authenticity_desc", "corroboration_desc"}
)


def _parse_finding_platforms(raw: str | None) -> set[str]:
    """`?platform=github,gitlab` → doğrulanmış platform kümesi (boşsa filtre yok)."""
    if raw is None or not raw.strip():
        return set()
    values = {part.strip().lower() for part in raw.split(",") if part.strip()}
    valid = {platform.value for platform in Platform}
    invalid = sorted(values - valid)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Geçersiz platform: {', '.join(invalid)} "
                f"(izinli: {', '.join(sorted(valid))})"
            ),
        )
    return values


def _monitor_payload(case: Case) -> dict[str, Any]:
    return {
        "enabled": bool(case.monitor_enabled),
        "interval_hours": int(case.monitor_interval_hours or 24),
        "mode": case.monitor_mode or "O2",
        "webhook_url": case.webhook_url,
        "last_scan_at": (
            case.monitor_last_scan_at.isoformat() if case.monitor_last_scan_at else None
        ),
    }


@router.get("/{case_id}/monitor")
async def get_case_monitor(case_id: str, db: AsyncSession = Depends(get_db)):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return _monitor_payload(case)


@router.patch("/{case_id}/monitor")
async def update_case_monitor(
    case_id: str,
    payload: MonitorPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    case.monitor_enabled = payload.enabled
    case.monitor_interval_hours = payload.interval_hours
    case.monitor_mode = norm_mode(payload.mode)
    await db.commit()
    await db.refresh(case)
    await record_request_audit(
        db=db,
        request=request,
        action="monitor.update",
        case_id=case.id,
        target=f"Case: {case.name}",
        details={
            "enabled": bool(case.monitor_enabled),
            "interval_hours": case.monitor_interval_hours,
            "mode": case.monitor_mode,
        },
    )
    return _monitor_payload(case)


@router.patch("/{case_id}/webhook")
async def update_case_webhook(
    case_id: str,
    payload: WebhookPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import urlparse

    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    value = (payload.url or "").strip() or None
    if value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=422, detail="Webhook http(s) URL olmalı")
        if parsed.username or parsed.password:
            raise HTTPException(status_code=422, detail="Webhook URL'de kimlik bilgisi olamaz")
    case.webhook_url = value
    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="case.webhook.update",
        case_id=case.id,
        target=f"Case: {case.name}",
        details={"configured": bool(value)},
    )
    return {"id": case.id, "webhook_url": case.webhook_url}


@router.get("/{case_id}/diff")
async def get_case_diff(
    case_id: str,
    from_scan: str | None = Query(None),
    to_scan: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """İki taramanın anlık görüntülerini karşılaştırır (yeni/çıkan/değişen)."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    async def _load(scan_id: str | None) -> Scan | None:
        if scan_id:
            return (
                await db.execute(
                    select(Scan).where(Scan.id == scan_id, Scan.case_id == case_id)
                )
            ).scalar_one_or_none()
        rows = (
            await db.execute(
                select(Scan)
                .where(Scan.case_id == case_id, Scan.status == "completed")
                .order_by(Scan.created_at.desc())
                .limit(1)
            )
        ).scalars().all()
        return rows[0] if rows else None

    to_row = await _load(to_scan)
    if to_row is None:
        return {"available": False, "reason": "no_completed_scan"}
    if from_scan:
        from_row = await _load(from_scan)
    else:
        rows = (
            await db.execute(
                select(Scan)
                .where(
                    Scan.case_id == case_id,
                    Scan.status == "completed",
                    Scan.id != to_row.id,
                )
                .order_by(Scan.created_at.desc())
                .limit(1)
            )
        ).scalars().all()
        from_row = rows[0] if rows else None

    old = (from_row.stats or {}).get("snapshot") if from_row else None
    new = (to_row.stats or {}).get("snapshot")
    if not new:
        return {"available": False, "reason": "snapshot_missing", "to_scan": to_row.id}

    old_findings = {item["id"]: item for item in (old or {}).get("findings", [])}
    new_findings = {item["id"]: item for item in new.get("findings", [])}
    changed = []
    for finding_id in sorted(set(old_findings) & set(new_findings)):
        before, after = old_findings[finding_id], new_findings[finding_id]
        if before.get("status") != after.get("status") or before.get("score") != after.get("score"):
            changed.append({"id": finding_id, "before": before, "after": after})

    old_entities = set((old or {}).get("entity_ids", []))
    new_entities = set(new.get("entity_ids", []))
    old_edges = set((old or {}).get("edge_ids", []))
    new_edges = set(new.get("edge_ids", []))

    return {
        "available": True,
        "case_id": case_id,
        "from_scan": from_row.id if from_row else None,
        "to_scan": to_row.id,
        "findings": {
            "new": [new_findings[i] for i in sorted(set(new_findings) - set(old_findings))],
            "removed": [old_findings[i] for i in sorted(set(old_findings) - set(new_findings))],
            "changed": changed,
        },
        "entities": {
            "added": sorted(new_entities - old_entities),
            "removed": sorted(old_entities - new_entities),
        },
        "edges": {
            "added": sorted(new_edges - old_edges),
            "removed": sorted(old_edges - new_edges),
        },
        "counts": {
            "findings_new": len(set(new_findings) - set(old_findings)),
            "findings_removed": len(set(old_findings) - set(new_findings)),
            "findings_changed": len(changed),
            "entities_added": len(new_entities - old_entities),
            "edges_added": len(new_edges - old_edges),
        },
    }


@router.patch("/{case_id}/owner")
async def update_case_owner(
    case_id: str,
    payload: OwnerPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    owner = (payload.owner or "").strip() or None
    case.owner = owner[:128] if owner else None
    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="case.owner.update",
        case_id=case.id,
        target=f"Case: {case.name}",
        details={"owner": case.owner},
    )
    return {"id": case.id, "owner": case.owner}


@router.get("/{case_id}/comments")
async def list_case_comments(case_id: str, db: AsyncSession = Depends(get_db)):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    rows = (
        await db.execute(
            select(CaseComment)
            .where(CaseComment.case_id == case_id)
            .order_by(CaseComment.created_at.asc())
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "author": row.author,
            "body": row.body,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post("/{case_id}/comments", status_code=201)
async def add_case_comment(
    case_id: str,
    payload: CommentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    body = reject_control_chars(payload.body.strip(), param="body") or ""
    if not body:
        raise HTTPException(status_code=422, detail="Yorum boş olamaz")
    author = reject_control_chars(
        (request.headers.get("X-Actor") or payload.author).strip(), param="author"
    ) or "operator"
    row = CaseComment(
        case_id=case.id,
        author=author[:128],
        body=body[:4000],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    # Kullanıcı yorumu da havuzun parçasıdır; AI araştırması bu notu görür.
    # Havuz yazımı başarısız olsa bile yorum kaydı korunur.
    try:
        await add_comment_item(db, comment=row, created_by=author)
        await db.commit()
    except Exception:  # noqa: BLE001 - yorum kaydı havuz hatasına kurban gitmesin
        await db.rollback()
        logger.warning("Yorum havuza eklenemedi (comment=%s)", row.id, exc_info=True)
    await record_request_audit(
        db=db,
        request=request,
        action="case.comment.create",
        case_id=case.id,
        target=f"Case: {case.name}",
        details={"comment_id": row.id, "author": row.author},
    )
    return {
        "id": row.id,
        "author": row.author,
        "body": row.body,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{case_id}/findings")
async def get_case_findings(
    case_id: str,
    status_query: str | None = Query(None, alias="status"),
    platform: str | None = None,
    min_authenticity: float | None = None,
    sort: Literal[
        "score_desc", "score_asc", "authenticity_desc", "corroboration_desc"
    ] = "score_desc",
    db: AsyncSession = Depends(get_db),
):
    """Vaka bulguları — kimlik kümesi ve bağımsız sinyal özetiyle.

    Opsiyonel filtreler (T-327):
    * `status=candidate,confirmed` — virgülle çoklu; her değer
      `candidate|confirmed|lead|rejected` olmalı (aksi halde 422).
    * `platform=github,gitlab` — virgülle çoklu platform filtresi.
    * `min_authenticity=0.35` — 0..1 aralığında; authenticity skoru bu değerin
      altında olan (veya `authenticity` boş olan) bulgular döner **değil**.
    * `sort` — `score_desc` (varsayılan), `score_asc`, `authenticity_desc`,
      `corroboration_desc`.

    Her bulgu, bağlı olduğu kimlik kümesini (`cluster`) ve güven skoruna
    katkı veren bağımsız sinyalleri (`signals`, `corroboration`) taşır.
    """
    if sort not in _FINDING_SORTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Geçersiz sort: {sort} (izinli: {', '.join(sorted(_FINDING_SORTS))})",
        )
    statuses = _parse_status_filter(status_query)
    platforms = _parse_finding_platforms(platform)
    min_auth = _parse_min_authenticity(min_authenticity)

    stmt = select(Finding).where(Finding.case_id == case_id)
    if statuses:
        stmt = stmt.where(Finding.status.in_(statuses))
    if platforms:
        stmt = stmt.where(Finding.platform.in_(platforms))
    if min_auth is not None:
        stmt = stmt.where(Finding.authenticity.is_not(None)).where(
            Finding.authenticity >= min_auth
        )
    findings = (await db.execute(stmt.order_by(Finding.score.desc()))).scalars().all()

    entities = (
        await db.execute(select(Entity).where(Entity.case_id == case_id))
    ).scalars().all()
    edges = (
        await db.execute(select(Edge).where(Edge.case_id == case_id))
    ).scalars().all()
    clusters_by_entity, _ = compute_identity_clusters(entities, edges)

    rows: list[dict[str, Any]] = []
    for finding in findings:
        signals = corroboration_signals(finding.breakdown)
        cluster = clusters_by_entity.get(finding.entity_id)
        rows.append(
            {
                "id": finding.id,
                "entity_id": finding.entity_id,
                "platform": finding.platform,
                "label": finding.label,
                "url": finding.url,
                "status": finding.status,
                "score": finding.score,
                "breakdown": finding.breakdown,
                "explanation": finding.explanation,
                "evidence_ids": finding.evidence_ids,
                "verified_at": (
                    finding.verified_at.isoformat() if finding.verified_at else None
                ),
                # T-327: sahte/spam (authenticity) sinyali — eski istemciler icin ek alan
                "authenticity": finding.authenticity,
                "authenticity_reasons": finding.authenticity_reasons,
                # Yeni: kaç bağımsız sinyal bu skoru destekliyor?
                "corroboration": len(signals),
                "signals": signals,
                "evidence_count": len(finding.evidence_ids or []),
                # Yeni: aynı kişiye bağlı hesaplar (tek başına kalan da döner).
                "cluster": cluster.as_dict() if cluster else None,
                "cluster_id": cluster.id if cluster else None,
            }
        )

    def _authenticity(row: dict[str, Any]) -> float | None:
        value = row.get("authenticity")
        return None if value is None else float(value)

    if sort == "score_asc":
        rows.sort(key=lambda row: (float(row["score"]), str(row["id"])))
    elif sort == "authenticity_desc":
        rows.sort(
            key=lambda row: (
                _authenticity(row) is None,
                -(_authenticity(row) or 0.0),
                -float(row["score"]),
                str(row["id"]),
            )
        )
    elif sort == "corroboration_desc":
        rows.sort(
            key=lambda row: (
                -int(row.get("corroboration") or 0),
                -float(row["score"]),
                _authenticity(row) is None,
                -(_authenticity(row) or 0.0),
                str(row["id"]),
            )
        )
    else:  # score_desc
        rows.sort(key=lambda row: (-float(row["score"]), str(row["id"])))
    return rows


@router.get("/{case_id}/stix")
async def export_case_stix(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Vakayı STIX 2.1 bundle olarak dışa aktarır (MISP/OpenCTI uyumu)."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    entities = (
        await db.execute(select(Entity).where(Entity.case_id == case_id))
    ).scalars().all()
    findings = (
        await db.execute(select(Finding).where(Finding.case_id == case_id))
    ).scalars().all()
    evidence = (
        await db.execute(select(Evidence).where(Evidence.case_id == case_id))
    ).scalars().all()
    contract_target = _db_target_to_contract(target) if target else None
    bundle = build_stix_bundle(
        case=case,
        target=contract_target,
        entities=list(entities),
        findings=list(findings),
        evidence=list(evidence),
    )
    await record_request_audit(
        db=db,
        request=request,
        action="case.export.stix",
        case_id=case_id,
        target=f"Case: {case.name}",
        details={"objects": len(bundle["objects"])},
    )
    return JSONResponse(
        content=bundle,
        media_type="application/stix+json;version=2.1",
        headers={"Content-Disposition": f'attachment; filename="showis-{case_id}.stix.json"'},
    )


@router.get("/{case_id}/misp")
async def export_case_misp(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """MISP core formatında Event JSON'u döndürür."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    entities = (
        await db.execute(select(Entity).where(Entity.case_id == case_id))
    ).scalars().all()
    findings = (
        await db.execute(select(Finding).where(Finding.case_id == case_id))
    ).scalars().all()
    event = build_misp_event(
        case=case,
        target=_db_target_to_contract(target) if target else None,
        entities=list(entities),
        findings=list(findings),
    )
    await record_request_audit(
        db=db,
        request=request,
        action="case.export.misp",
        case_id=case_id,
        target=f"Case: {case.name}",
        details={"attributes": len(event["Event"]["Attribute"])},
    )
    return JSONResponse(
        content=event,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="showis-{case_id}.misp.json"'},
    )


@router.get("/{case_id}/maltego")
async def export_case_maltego(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Maltego local transform için XML entity listesi."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    entities = (
        await db.execute(select(Entity).where(Entity.case_id == case_id))
    ).scalars().all()
    findings = (
        await db.execute(select(Finding).where(Finding.case_id == case_id))
    ).scalars().all()
    xml = build_maltego_xml(
        case=case,
        target=_db_target_to_contract(target) if target else None,
        entities=list(entities),
        findings=list(findings),
    )
    await record_request_audit(
        db=db,
        request=request,
        action="case.export.maltego",
        case_id=case_id,
        target=f"Case: {case.name}",
        details={"entities": len(entities)},
    )
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="showis-{case_id}.maltego.xml"'},
    )


@router.post("/{case_id}/export")
@router.get("/{case_id}/export", deprecated=True)
async def export_case(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """KVKK veri sahibi talebi: vakanin tum verisini JSON olarak disar aktarir."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    seeds = (await db.execute(select(DbSeed).where(DbSeed.case_id == case_id))).scalars().all()
    entities = (await db.execute(select(Entity).where(Entity.case_id == case_id))).scalars().all()
    edges = (await db.execute(select(Edge).where(Edge.case_id == case_id))).scalars().all()
    findings = (await db.execute(select(Finding).where(Finding.case_id == case_id))).scalars().all()
    evidence = (
        (await db.execute(select(Evidence).where(Evidence.case_id == case_id))).scalars().all()
    )
    pool_items = (
        (await db.execute(select(PoolItem).where(PoolItem.case_id == case_id))).scalars().all()
    )
    research_sessions = (
        (await db.execute(select(ResearchSession).where(ResearchSession.case_id == case_id)))
        .scalars()
        .all()
    )
    research_messages = (
        (
            await db.execute(
                select(ResearchMessage)
                .join(ResearchSession, ResearchMessage.session_id == ResearchSession.id)
                .where(ResearchSession.case_id == case_id)
                .order_by(ResearchMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    supply_nodes = (
        (
            await db.execute(
                select(SupplyChainNode).where(SupplyChainNode.case_id == case_id)
            )
        )
        .scalars()
        .all()
    )
    supply_edges = (
        (
            await db.execute(
                select(SupplyChainEdge).where(SupplyChainEdge.case_id == case_id)
            )
        )
        .scalars()
        .all()
    )
    clusters_by_entity, clusters = compute_identity_clusters(entities, edges)

    await record_request_audit(
        db=db,
        request=request,
        action="case.export",
        case_id=case_id,
        target=f"Case: {case.name}",
    )

    contract = _db_target_to_contract(target) if target else None

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "case": {
            "id": case.id,
            "name": case.name,
            "purpose": case.purpose,
            "legal_basis": case.legal_basis,
            "notes": case.notes,
            "status": case.status,
            "retention_days": case.retention_days,
            "created_at": case.created_at.isoformat(),
        },
        "target": {
            "kind": contract.kind,
            "first_name": contract.first_name,
            "last_name": contract.last_name,
            "nickname": contract.nickname,
            "email": contract.email,
            "phone": contract.phone,
            "domain": contract.domain,
            "ip": contract.ip,
            "name_forms": contract.name_forms,
            "hints": contract.hints,
            "review_required": contract.review_required,
        }
        if contract
        else None,
        "seeds": [
            {
                "platform": s.platform,
                "url": s.url,
                "username": s.username,
                "email": s.email,
                "source": s.source,
            }
            for s in seeds
        ],
        "entities": [
            {
                "id": e.id,
                "type": e.type,
                "platform": e.platform,
                "label": e.label,
                "url": e.url,
                "attributes": e.attributes,
                "score": e.score,
            }
            for e in entities
        ],
        "edges": [
            {
                "id": ed.id,
                "type": ed.type,
                "source": ed.source_entity_id,
                "target": ed.target_entity_id,
                "confidence": ed.confidence,
                "reason": ed.reason,
            }
            for ed in edges
        ],
        "findings": [
            {
                "id": f.id,
                "entity_id": f.entity_id,
                "platform": f.platform,
                "label": f.label,
                "url": f.url,
                "status": f.status,
                "score": f.score,
                "breakdown": f.breakdown,
                "explanation": f.explanation,
                "evidence_ids": f.evidence_ids,
                # T-327: authenticity alanlari export'ta da yer alir
                "authenticity": f.authenticity,
                "authenticity_reasons": f.authenticity_reasons,
                # Yeni: küme kimliği + bağımsız sinyal özeti
                "corroboration": len(corroboration_signals(f.breakdown)),
                "cluster_id": (
                    clusters_by_entity[f.entity_id].id
                    if f.entity_id in clusters_by_entity
                    else None
                ),
            }
            for f in findings
        ],
        "clusters": [cluster.as_dict() for cluster in clusters],
        "evidence": [
            {
                "id": ev.id,
                "source_url": ev.source_url,
                "kind": ev.kind,
                "content_hash": ev.content_hash,
                "http_status": ev.http_status,
                "fetched_at": ev.fetched_at.isoformat() if ev.fetched_at else None,
            }
            for ev in evidence
        ],
        "pool": [
            {
                "id": item.id,
                "kind": item.kind,
                "label": item.label,
                "value": item.value,
                "source": item.source,
                "status": item.status,
                "confidence": item.confidence,
                "attributes": item.attributes,
                "provenance": item.provenance,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in pool_items
        ],
        "research": [
            {
                "id": session.id,
                "title": session.title,
                "question": session.question,
                "mode": session.mode,
                "status": session.status,
                "error": session.error,
                "final_answer": session.final_answer,
                "citations": session.citations,
                "stats": session.stats,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "tool_name": message.tool_name,
                        "tool_args": message.tool_args,
                        "tool_result": message.tool_result,
                        "citations": message.citations,
                        "created_at": message.created_at.isoformat()
                        if message.created_at
                        else None,
                    }
                    for message in research_messages
                    if message.session_id == session.id
                ],
            }
            for session in research_sessions
        ],
        "supply_chain": {
            "nodes": [
                {
                    "id": node.id,
                    "kind": node.kind,
                    "name": node.name,
                    "country": node.country,
                    "website": node.website,
                    "identifiers": node.identifiers,
                    "attributes": node.attributes,
                    "source": node.source,
                    "status": node.status,
                    "confidence": node.confidence,
                }
                for node in supply_nodes
            ],
            "edges": [
                {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "relation": edge.relation,
                    "label": edge.label,
                    "confidence": edge.confidence,
                    "status": edge.status,
                    "source": edge.source,
                    "evidence": edge.evidence,
                }
                for edge in supply_edges
            ],
        },
    }


@router.get("/{case_id}/graph")
async def get_case_graph(case_id: str, db: AsyncSession = Depends(get_db)):
    """Cytoscape icin {nodes, edges} dondurur."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    entities = (await db.execute(select(Entity).where(Entity.case_id == case_id))).scalars().all()
    edges = (await db.execute(select(Edge).where(Edge.case_id == case_id))).scalars().all()
    findings = (await db.execute(select(Finding).where(Finding.case_id == case_id))).scalars().all()
    score_by_entity = {f.entity_id: f.score for f in findings}
    clusters_by_entity, clusters = compute_identity_clusters(entities, edges)

    nodes = [
        {
            "data": {
                "id": e.id,
                "label": e.label,
                "type": e.type,
                "platform": e.platform,
                "url": e.url,
                "score": score_by_entity.get(e.id, e.score or 0.0),
                "attributes": e.attributes or {},
                "cluster_id": (
                    clusters_by_entity[e.id].id if e.id in clusters_by_entity else None
                ),
                "cluster_size": (
                    clusters_by_entity[e.id].size if e.id in clusters_by_entity else 1
                ),
                "cluster_confidence": (
                    clusters_by_entity[e.id].max_confidence
                    if e.id in clusters_by_entity
                    else 0.0
                ),
                "cluster_platforms": (
                    list(clusters_by_entity[e.id].platforms)
                    if e.id in clusters_by_entity
                    else []
                ),
            }
        }
        for e in entities
    ]
    graph_edges = [
        {
            "data": {
                "id": ed.id,
                "source": ed.source_entity_id,
                "target": ed.target_entity_id,
                "label": ed.type,
                "type": ed.type,
                "confidence": ed.confidence,
                "reason": ed.reason,
            }
        }
        for ed in edges
    ]
    for node in nodes:
        node["data"]["origin"] = "scan"
    pool_nodes, pool_edges = await _pool_graph(db, case_id, entities)
    return {
        "nodes": nodes + pool_nodes,
        "edges": graph_edges + pool_edges,
        "clusters": [cluster.as_dict() for cluster in clusters],
    }


#: Grafikte düğüm olarak gösterilen havuz türleri (not/biyografi metindir, düğüm değil).
_GRAPH_POOL_KINDS = frozenset(
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
        "location",
        "identifier",
        "product",
    }
)
_MAX_GRAPH_POOL_ITEMS = 600


def _url_key(value: str) -> str:
    text = str(value or "").strip().lower().split("#", 1)[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", text)


async def _pool_graph(
    db: AsyncSession, case_id: str, entities: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Havuz kayıtları + tipli ilişkiler; tarama varlığıyla aynı URL "same_as" bağlanır.

    Böylece araştırma/zincir bulguları ve analist onayları tarama grafiğiyle
    tek tuvalde görünür. Reddedilen kayıt ve ilişkiler gösterilmez.
    """
    items = (
        (
            await db.execute(
                select(PoolItem)
                .where(
                    PoolItem.case_id == case_id,
                    PoolItem.status != "rejected",
                    PoolItem.kind.in_(_GRAPH_POOL_KINDS),
                )
                .order_by(PoolItem.confidence.desc(), PoolItem.created_at.asc())
                .limit(_MAX_GRAPH_POOL_ITEMS)
            )
        )
        .scalars()
        .all()
    )
    if not items:
        return [], []
    ids = {item.id for item in items}
    relations = (
        (
            await db.execute(
                select(PoolRelation).where(
                    PoolRelation.case_id == case_id, PoolRelation.status != "rejected"
                )
            )
        )
        .scalars()
        .all()
    )
    nodes: list[dict[str, Any]] = []
    for item in items:
        attrs = item.attributes if isinstance(item.attributes, dict) else {}
        url = attrs.get("profile_url") or (
            item.value if str(item.value).startswith(("http://", "https://")) else None
        )
        score = 1.0 if item.status == "confirmed" else float(item.confidence or 0.0)
        nodes.append(
            {
                "data": {
                    "id": item.id,
                    "label": item.label[:120],
                    "type": item.kind,
                    "platform": str(attrs.get("platform") or "havuz"),
                    "url": url,
                    "score": round(score, 4),
                    "attributes": {
                        "value": item.value[:500],
                        "status": item.status,
                        "source": item.source,
                        "chain": (item.provenance or {}).get("chain")
                        if isinstance(item.provenance, dict)
                        else None,
                    },
                    "origin": "pool",
                    "status": item.status,
                    "cluster_id": None,
                    "cluster_size": 1,
                    "cluster_confidence": 0.0,
                    "cluster_platforms": [],
                }
            }
        )
    edges: list[dict[str, Any]] = []
    for rel in relations:
        if rel.src_item_id not in ids or rel.dst_item_id not in ids:
            continue
        notes = [str(e.get("note") or "") for e in (rel.evidence or []) if isinstance(e, dict)]
        edges.append(
            {
                "data": {
                    "id": f"rel:{rel.id}",
                    "source": rel.src_item_id,
                    "target": rel.dst_item_id,
                    "label": rel.type,
                    "type": rel.type,
                    "confidence": float(rel.confidence or 0.0),
                    "reason": "; ".join(note for note in notes if note)[:300],
                    "status": rel.status,
                }
            }
        )
    by_url: dict[str, str] = {}
    for item in items:
        attrs = item.attributes if isinstance(item.attributes, dict) else {}
        for candidate in (item.value, attrs.get("profile_url")):
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                by_url.setdefault(_url_key(candidate), item.id)
    for entity in entities:
        if not entity.url:
            continue
        item_id = by_url.get(_url_key(entity.url))
        if item_id is None:
            continue
        edges.append(
            {
                "data": {
                    "id": f"same:{entity.id}:{item_id}",
                    "source": entity.id,
                    "target": item_id,
                    "label": "same_as",
                    "type": "same_as",
                    "confidence": 0.9,
                    "reason": "tarama varlığı ile havuz kaydı aynı adres",
                }
            }
        )
    return nodes, edges


@router.get("/{case_id}/audit")
async def get_case_audit(case_id: str, db: AsyncSession = Depends(get_db)):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    logs = await list_audit_logs(db, case_id=case_id, limit=500)
    return [
        {
            "id": log.id,
            "actor": log.actor,
            "action": log.action,
            "target": log.target,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


class ReportCreateRequest(BaseModel):
    format: str = "pdf"  # pdf | json | html


@router.post("/{case_id}/report")
async def create_case_report(
    case_id: str,
    payload: ReportCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        report, _content = await create_report(db, case_id, payload.format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await record_request_audit(
        db=db,
        request=request,
        action="report.create",
        case_id=case_id,
        target=f"Report: {report.id} ({report.format})",
    )
    return {
        "id": report.id,
        "case_id": report.case_id,
        "format": report.format,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "download_url": f"/api/v1/reports/{report.id}/download",
    }

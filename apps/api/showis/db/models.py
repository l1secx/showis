import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def gen_uuid_str() -> str:
    return str(uuid.uuid4())


# Migration'lar `sa.JSON()` kullanir (0001); JSONB varyanti sema drift'i uretir.
JSON_TYPE = JSON()


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    provider: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    secret_masked: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIProvider(Base):
    """Çoklu LLM sağlayıcı profili (OpenAI uyumlu).

    `Setting(provider="llm")` tek sağlayıcılı eski yoldur; bu tablo aynı anda
    birden fazla profil tutar. Sır yalnız `secret_encrypted` içinde yaşar ve
    istemciye hiçbir zaman dönmez. Tek aktif profil kısmi unique index ile
    garanti edilir (uygulama katmanı da aynı kuralı uygular).
    """

    __tablename__ = "ai_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    preset: Mapped[str] = mapped_column(String(32), default="custom", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    #: Model rolleri: {planner, reasoner, extractor, summarizer} -> model kimliği.
    roles: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, server_default="{}")
    secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    secret_masked: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    legal_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    #: Otomatik yeniden tarama (monitoring). Kapalı varsayılan.
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    monitor_interval_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    monitor_mode: Mapped[str] = mapped_column(String(16), default="O2", server_default="O2")
    monitor_last_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Operasyonel sahiplik etiketi (yetkilendirme sınırı DEĞİL).
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Tarama tamamlandığında POST edilecek webhook (SSRF guard ile).
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Havuzda kullanıcı kararı/düzenlemesi sonrası muhakeme çıktıları bayat.
    #: Korelasyon veya hipotez üretimi çalışınca temizlenir.
    reasoning_stale_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    targets: Mapped[list["Target"]] = relationship(
        "Target", back_populates="case", cascade="all, delete-orphan"
    )
    seeds: Mapped[list["Seed"]] = relationship(
        "Seed", back_populates="case", cascade="all, delete-orphan"
    )
    scans: Mapped[list["Scan"]] = relationship(
        "Scan", back_populates="case", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="case", cascade="all, delete-orphan"
    )
    entities: Mapped[list["Entity"]] = relationship(
        "Entity", back_populates="case", cascade="all, delete-orphan"
    )
    findings: Mapped[list["Finding"]] = relationship(
        "Finding", back_populates="case", cascade="all, delete-orphan"
    )
    comments: Mapped[list["CaseComment"]] = relationship(
        "CaseComment", back_populates="case", cascade="all, delete-orphan"
    )


class ApiUser(Base):
    """Panel/API kullanıcısı — token hash'i ve rolü."""

    __tablename__ = "api_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="analyst")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaseComment(Base):
    __tablename__ = "case_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    author: Mapped[str] = mapped_column(String(128), nullable=False, default="operator")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="comments")


class PoolItem(Base):
    """Vaka araştırma havuzu — kullanıcı girdisi, tarama çıktısı ve AI önerisi.

    `normalized` alanı aynı vaka içinde tekrarı engeller (unique constraint).
    `value` kullanıcıya gösterilen özgün değerdir; `normalized` yalnız
    karşılaştırma/arama içindir. `provenance` kaynağın URL'si, oturum kimliği
    veya kanıt kimliği gibi iz bilgilerini taşır.
    """

    __tablename__ = "pool_items"
    __table_args__ = (
        UniqueConstraint("case_id", "kind", "normalized", name="uq_pool_items_case_kind_value"),
        Index("ix_pool_items_case_status", "case_id", "status"),
        Index("ix_pool_items_case_source", "case_id", "source"),
        Index(
            "ix_pool_items_value_trgm",
            "value",
            postgresql_using="gin",
            postgresql_ops={"value": "gin_trgm_ops"},
        ),
        Index(
            "ix_pool_items_label_trgm",
            "label",
            postgresql_using="gin",
            postgresql_ops={"label": "gin_trgm_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PoolRelation(Base):
    """Havuz öğeleri arası tipli ilişki (Maltego tarzı graf kenarı).

    AI/kural çıktısı `proposed` veya `candidate` olur; `confirmed` yalnız kullanıcı
    eylemiyle yazılır. `evidence` bağımsız kaynak/kural kanıtlarının listesidir.
    """

    __tablename__ = "pool_relations"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "src_item_id", "dst_item_id", "type", name="uq_pool_relations_edge"
        ),
        Index("ix_pool_relations_case_status", "case_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    src_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pool_items.id", ondelete="CASCADE"), nullable=False
    )
    dst_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pool_items.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Hypothesis(Base):
    """Kanıt temelli hipotez. Skor kanıtlardan hesaplanır; LLM yalnız gerekçe yazar."""

    __tablename__ = "hypotheses"
    __table_args__ = (Index("ix_hypotheses_case_status", "case_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: Log-odds'tan türetilen olasılık (0..1); elle düzenlenmez.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    supporting: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    contradicting: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    next_checks: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Hipotezin dayandığı havuz öğeleri (kümenin üyeleri).
    item_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ResearchSession(Base):
    """AI araştırma oturumu — soru, mod, sağlayıcı ve nihai sentez."""

    __tablename__ = "research_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: "research" = araştır/öner, "agent" = tam ajan (yazma + tarama başlatma).
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="research")
    provider_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchControlState(Base):
    """Araştırma oturumunun kalıcı kontrol durumu (steer kuyruğu, soru/yanıt, durdurma).

    Süreç yeniden başlasa da `awaiting_user` oturumu sorusunu korur ve yanıtla sürer.
    """

    __tablename__ = "research_control_state"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    interrupt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Ekleme-yalnız yönlendirme metinleri.
    steering: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    #: {"id","question","options","asked_at"} veya None.
    pending_question: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    #: Süreç kapalıyken verilen yanıt: {"question_id","question","answer","answered_at"}.
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    asked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ResearchMessage(Base):
    """Araştırma akışındaki tek adım: kullanıcı, düşünce, araç veya nihai yanıt."""

    __tablename__ = "research_messages"
    __table_args__ = (Index("ix_research_messages_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_args: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    tool_result: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupplyChainNode(Base):
    """Tedarik zinciri düğümü — şirket, tedarikçi, ürün, kişi veya yer."""

    __tablename__ = "supply_chain_nodes"
    __table_args__ = (
        UniqueConstraint("case_id", "kind", "normalized", name="uq_supply_chain_node"),
        Index("ix_supply_chain_nodes_case_kind", "case_id", "kind"),
        Index("ix_supply_chain_nodes_case_status", "case_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="company")
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized: Mapped[str] = mapped_column(String(256), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SupplyChainEdge(Base):
    """Tedarik zinciri ilişkisi — yönlü kenar ve kanıt/iz bilgisi."""

    __tablename__ = "supply_chain_edges"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "source_id", "target_id", "relation", name="uq_supply_chain_edge"
        ),
        Index("ix_supply_chain_edges_case", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("supply_chain_nodes.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("supply_chain_nodes.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="related_to")
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    #: Kimlik bicimi: "name" (ad + soyad) veya "handle" (yalnizca kullanici adi).
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="name")
    #: Handle hedeflerinde ad/soyad bilinmez (bkz. migration 0007).
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Kullanici adi (handle). Yalnizca handle hedeflerinde dolu olur.
    nickname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Kimlik turu email/phone/domain/ip oldugunda ilgili alan dolu olur.
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name_forms: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    hints: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="targets")


class Seed(Base):
    __tablename__ = "seeds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="seeds")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    mode: Mapped[str] = mapped_column(String(16), default="O2")
    budget: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="scans")
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="scan", cascade="all, delete-orphan"
    )
    observations: Mapped[list["Observation"]] = relationship(
        "Observation", back_populates="scan", cascade="all, delete-orphan"
    )
    findings: Mapped[list["Finding"]] = relationship(
        "Finding", back_populates="scan", cascade="all, delete-orphan"
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    #: Yurutme onceligi (kucuk once). Planner gorevleri 1..5 uretir; connector
    #: gorevleri varsayilan 5'tir. `_refill_pending` bu kolonla siralar.
    priority: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Gorevin gercekten calistirilmaya basladigi an. `created_at` planlama
    #: anidir; aradaki fark kuyruk beklemesidir, yurutme suresi degildir.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scan: Mapped["Scan"] = relationship("Scan", back_populates="tasks")


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tier: Mapped[str] = mapped_column(String(8), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scan: Mapped["Scan"] = relationship("Scan", back_populates="observations")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped["Case"] = relationship("Case", back_populates="evidence")


class EvidenceSnapshot(Base):
    """Okunan herkese açık sayfanın arşivlenmiş metin kopyası (kanıt zinciri).

    `sha256` ham içerik özetidir; `text_sha256` saklanan (kırpılmış) metnin özetidir.
    Aynı (vaka, url, sha256) tekrar yazılmaz.
    """

    __tablename__ = "evidence_snapshots"
    __table_args__ = (
        UniqueConstraint("case_id", "url", "sha256", name="uq_evidence_snapshots_content"),
        Index("ix_evidence_snapshots_case_fetched", "case_id", "fetched_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("research_sessions.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: research | subagent | pivot | citation
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="research")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WatchAlert(Base):
    """Kayıtlı izleme bildirimi: yeni sızıntı veya yeni hesap bulgusu.

    Aynı (vaka, dedupe_key) bir kez yazılır; analist `seen_at` ile okundu işaretler.
    """

    __tablename__ = "watch_alerts"
    __table_args__ = (
        UniqueConstraint("case_id", "dedupe_key", name="uq_watch_alerts_dedupe"),
        Index("ix_watch_alerts_case_created", "case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    #: breach | account
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("pool_items.id", ondelete="SET NULL"), nullable=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(96), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="entities")


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[str] = mapped_column(String(96), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(96), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    #: Sahte/atil hesap sinyali (0..1). Guven skorundan bagimsizdir (T-301).
    authenticity: Mapped[float | None] = mapped_column(Float, nullable=True)
    authenticity_reasons: Mapped[list[str] | None] = mapped_column(JSON_TYPE, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="findings")
    scan: Mapped["Scan"] = relationship("Scan", back_populates="findings")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid_str)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        Integer().with_variant(BigInteger, "postgresql"),
        primary_key=True,
        autoincrement=True,
    )
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), default="local-user")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_audit_logs_case_id_created_at", AuditLog.case_id, AuditLog.created_at)
Index("ix_audit_logs_created_at", AuditLog.created_at)
#: `_refill_pending` sirasi: once dusuk `priority`, sonra ekleme sirasi (0008).
Index("ix_tasks_scan_priority", Task.scan_id, Task.priority)

# 0016: tek aktif AI sağlayıcı profili ve vaka başına tek çalışan araştırma.
Index(
    "uq_ai_providers_active",
    AIProvider.is_active,
    unique=True,
    postgresql_where=text("is_active"),
    sqlite_where=text("is_active"),
)
Index(
    "uq_research_sessions_case_active",
    ResearchSession.case_id,
    unique=True,
    postgresql_where=text("status IN ('queued', 'running', 'awaiting_user')"),
    sqlite_where=text("status IN ('queued', 'running', 'awaiting_user')"),
)
Index("ix_research_sessions_case_created", ResearchSession.case_id, ResearchSession.created_at)

# 0006: sorgu indeksleri (FK ve sik filtrelenen alanlar).
Index("ix_cases_created_at", Case.created_at)
Index("ix_targets_case_id", Target.case_id)
Index("ix_scans_case_created", Scan.case_id, Scan.created_at)
Index("ix_findings_case_status", Finding.case_id, Finding.status)
Index("ix_findings_scan_id", Finding.scan_id)
Index("ix_entities_case_id", Entity.case_id)
Index("ix_edges_case_id", Edge.case_id)
Index("ix_observations_scan_id", Observation.scan_id)
Index("ix_tasks_scan_status", Task.scan_id, Task.status)
Index("ix_evidence_case_id", Evidence.case_id)

# 0003: vaka basina tek aktif (queued/running) tarama.
Index(
    "uq_scans_case_active",
    Scan.case_id,
    unique=True,
    postgresql_where=text("status IN ('queued', 'running')"),
    sqlite_where=text("status IN ('queued', 'running')"),
)

# 0009/0010/0011/0012: arama, kimlik, izleme ve sahiplik indeksleri.
Index(
    "ix_cases_name_trgm",
    Case.name,
    postgresql_using="gin",
    postgresql_ops={"name": "gin_trgm_ops"},
)
Index(
    "ix_targets_firstname_trgm",
    Target.first_name,
    postgresql_using="gin",
    postgresql_ops={"first_name": "gin_trgm_ops"},
)
Index(
    "ix_targets_lastname_trgm",
    Target.last_name,
    postgresql_using="gin",
    postgresql_ops={"last_name": "gin_trgm_ops"},
)
Index(
    "ix_targets_nickname_trgm",
    Target.nickname,
    postgresql_using="gin",
    postgresql_ops={"nickname": "gin_trgm_ops"},
)
Index("ix_targets_email", Target.email)
Index("ix_targets_domain", Target.domain)
Index("ix_cases_monitor_due", Case.monitor_enabled, Case.monitor_last_scan_at)
Index("ix_cases_owner", Case.owner)
Index("ix_case_comments_case_created", CaseComment.case_id, CaseComment.created_at)

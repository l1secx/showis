"""case kapsamlı entity/edge/finding kimlikleri (P-005 / DEC-010)

Revision ID: 0026_case_scoped_ids
Revises: 0025_heartbeat_at
Create Date: 2026-11-26 13:30:00.000000

Eski kimlikler globaldi: aynı hedef iki case'te taranınca entity satırı ikinci
case'e taşınıyor, finding ilk case'te kalıyordu. Yeni scanner kimlikleri
`case_id` ile üretir (`make_entity_id(..., case_id=...)`, `short_edge_id("ec", ...)`).
Bu migration mevcut satırları aynı algoritmayla kapsar:

* her case için referans verilen entity'lerin kapsamlı kopyası oluşturulur,
* edge/finding satırları kapsamlı kimliklere taşınır,
* artık referans verilmeyen eski (kapsamsız) entity satırları silinir.

Veri dönüşümü geri alınamaz; `downgrade()` bilinçli olarak no-op'tur.
Postgres dışı (SQLite dev) veritabanlarında çalışmaz.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_case_scoped_ids"
down_revision: Union[str, None] = "0025_heartbeat_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ID_MAX = 96
_SCOPED_SUFFIX_RE = re.compile(r"~c[0-9a-f]{8}$")


def _sha1(value: str, length: int) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def _scoped_entity(raw_id: str, case_id: str) -> str:
    platform, separator, identifier = str(raw_id).partition(":")
    if not separator or not identifier:
        return str(raw_id)
    # T17-01: gelen sonek hangi case'e ait olduğuna güvenilmez; at ve yeniden üret.
    identifier = _SCOPED_SUFFIX_RE.sub("", identifier)
    if not identifier:
        return str(raw_id)
    scoped = f"{platform}:{identifier}~c{_sha1(str(case_id), 8)}"
    if len(scoped) <= _ID_MAX:
        return scoped
    return f"{platform}:{_sha1(f'{case_id}|{identifier}', 16)}"


def _scoped_finding(entity_id: str, case_id: str) -> str:
    # `entity_id` migration içinde zaten case kapsamlı üretildi; ikinci kez
    # case hash'i eklenirse kimlik çiftleşir (scanner ile aynı kural).
    raw = f"finding:{entity_id}"
    if len(raw) <= _ID_MAX:
        return raw
    return f"finding:{_sha1(str(entity_id), 24)}"


def _scoped_edge(edge_id: str, case_id: str) -> str:
    return f"ec:{_sha1(f'{case_id}|{edge_id}', 24)}"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (test/dev) şeması create_all ile kurulur; veri göçü gerekmez.
        return

    meta = sa.MetaData()
    entities = sa.Table("entities", meta, autoload_with=bind)
    edges = sa.Table("edges", meta, autoload_with=bind)
    findings = sa.Table("findings", meta, autoload_with=bind)

    entity_rows = list(bind.execute(sa.select(entities)).mappings())
    edge_rows = list(bind.execute(sa.select(edges)).mappings())
    finding_rows = list(bind.execute(sa.select(findings)).mappings())
    by_id = {str(row["id"]): row for row in entity_rows}

    # Case -> o case'te referans verilen ham entity id'leri.
    refs: dict[str, set[str]] = {}

    def _ref(case_id: Any, entity_id: Any) -> None:
        case_key = str(case_id)
        entity_key = str(entity_id)
        refs.setdefault(case_key, set()).add(entity_key)

    for row in entity_rows:
        _ref(row["case_id"], row["id"])
    for row in edge_rows:
        _ref(row["case_id"], row["source_entity_id"])
        _ref(row["case_id"], row["target_entity_id"])
    for row in finding_rows:
        _ref(row["case_id"], row["entity_id"])

    # 1) Her case için kapsamlı entity kopyaları (şablon varsa kopyala).
    for case_id, raw_ids in refs.items():
        for raw_id in raw_ids:
            scoped = _scoped_entity(raw_id, case_id)
            if scoped == raw_id:
                continue
            exists = bind.execute(
                sa.select(entities.c.id).where(entities.c.id == scoped)
            ).first()
            if exists:
                continue
            template = by_id.get(raw_id)
            if template is not None:
                values = dict(template)
            else:
                platform = raw_id.split(":", 1)[0] if ":" in raw_id else "unknown"
                label = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
                values = {
                    "id": raw_id,
                    "case_id": case_id,
                    "type": "account",
                    "platform": platform[:64],
                    "label": label[:256],
                    "url": None,
                    "attributes": {"unverified_ref": True},
                    "score": 0.0,
                    "first_seen": sa.func.now(),
                }
            values["id"] = scoped
            values["case_id"] = case_id
            bind.execute(sa.insert(entities).values(**values))

    # 2) Edge kimlikleri ve uçları.
    for row in edge_rows:
        case_id = str(row["case_id"])
        new_id = _scoped_edge(str(row["id"]), case_id)
        new_source = _scoped_entity(str(row["source_entity_id"]), case_id)
        new_target = _scoped_entity(str(row["target_entity_id"]), case_id)
        if (
            new_id == row["id"]
            and new_source == row["source_entity_id"]
            and new_target == row["target_entity_id"]
        ):
            continue
        bind.execute(
            sa.update(edges)
            .where(edges.c.id == row["id"])
            .values(
                id=new_id,
                source_entity_id=new_source,
                target_entity_id=new_target,
            )
        )

    # 3) Finding kimlikleri ve entity bağları.
    for row in finding_rows:
        case_id = str(row["case_id"])
        new_entity = _scoped_entity(str(row["entity_id"]), case_id)
        new_id = _scoped_finding(new_entity, case_id)
        if new_id == row["id"] and new_entity == row["entity_id"]:
            continue
        bind.execute(
            sa.update(findings)
            .where(findings.c.id == row["id"])
            .values(id=new_id, entity_id=new_entity)
        )

    # 4) Artık referans verilmeyen eski (kapsamsız) entity'leri sil.
    live_entity_ids: set[str] = set()
    for row in bind.execute(
        sa.select(edges.c.source_entity_id, edges.c.target_entity_id)
    ):
        live_entity_ids.add(str(row[0]))
        live_entity_ids.add(str(row[1]))
    for (entity_id,) in bind.execute(sa.select(findings.c.entity_id)):
        live_entity_ids.add(str(entity_id))

    for row in entity_rows:
        raw_id = str(row["id"])
        scoped = _scoped_entity(raw_id, str(row["case_id"]))
        if scoped == raw_id or raw_id in live_entity_ids:
            continue
        bind.execute(sa.delete(entities).where(entities.c.id == raw_id))


def downgrade() -> None:
    # Veri dönüşümü geri alınamaz (case bilgisi kapsamsız kimlikte yoktu).
    # Şema değişikliği yok; geri alma gerekmez.
    pass

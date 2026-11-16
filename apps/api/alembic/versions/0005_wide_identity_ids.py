"""entity/edge/finding kimlik kolonlarini genislet (VARCHAR 36 -> 96)

Revision ID: 0005_wide_identity_ids
Revises: 0004_finding_authenticity
Create Date: 2026-11-17 01:30:00.000000

Neden: entity id'leri ``{platform}:{identifier}`` bicimindedir. Bazi platformlar
(HackTheBox vb.) UUID tabanli kimlik kullanir: ``hackthebox:<36 karakter>`` = 46
karakter -> `entities.id VARCHAR(36)` tasar, tarama `unexpected_failure` ile duser.
Turev kimlikler de ayni sinirdan etkilenir: ``finding:{entity.id}`` ve edge id'leri.

Genisletme (Postgres) FK'leri bozmadan yapilabilir; her iki taraf da ayni genislige
cikariliyor. Uygulama katmani 96 karakteri asan kimlikleri hash'ler (T-346).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_wide_identity_ids"
down_revision: Union[str, None] = "0004_finding_authenticity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WIDE = sa.String(length=96)
_WIDE_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entities", ("id",)),
    ("edges", ("id", "source_entity_id", "target_entity_id")),
    ("findings", ("id", "entity_id")),
)


def upgrade() -> None:
    for table, columns in _WIDE_TABLES:
        for column in columns:
            op.alter_column(
                table,
                column,
                existing_type=sa.String(length=36),
                type_=_WIDE,
                existing_nullable=False,
            )


def downgrade() -> None:
    # Geri alma yalnizca 36 karakteri asan satir yoksa mumkundur; varsa hata verir
    # (veri kaybini onlemek icin bilincli tercih).
    for table, columns in _WIDE_TABLES:
        for column in columns:
            op.alter_column(
                table,
                column,
                existing_type=_WIDE,
                type_=sa.String(length=36),
                existing_nullable=False,
            )

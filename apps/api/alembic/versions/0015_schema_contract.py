"""şema sözleşmesi — model/migration drift'ini kapat

Revision ID: 0015_schema_contract
Revises: 0014_api_users
Create Date: 2026-11-23 13:10:00.000000

0001 ve sonraki migration'lar bazı kolonları nullable bıraktı; modeller ise
non-null ve Python default'lu. Canlıda NULL yok (doğrulandı). Bu migration
NULL değerleri güvenli varsayılanlarla doldurur ve NOT NULL kısıtını uygular.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_schema_contract"
down_revision: Union[str, None] = "0014_api_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (tablo, kolon, yedek değer SQL'i, alembic tipi)
_COLUMNS: tuple[tuple[str, str, str, sa.types.TypeEngine], ...] = (
    ("audit_logs", "details", "'{}'::json", sa.JSON()),
    ("audit_logs", "created_at", "now()", sa.DateTime(timezone=True)),
    ("cases", "created_at", "now()", sa.DateTime(timezone=True)),
    ("edges", "confidence", "0.0", sa.Float()),
    ("edges", "evidence_ids", "'[]'::json", sa.JSON()),
    ("entities", "score", "0.0", sa.Float()),
    ("entities", "first_seen", "now()", sa.DateTime(timezone=True)),
    ("evidence", "fetched_at", "now()", sa.DateTime(timezone=True)),
    ("findings", "score", "0.0", sa.Float()),
    ("findings", "evidence_ids", "'[]'::json", sa.JSON()),
    ("findings", "created_at", "now()", sa.DateTime(timezone=True)),
    ("observations", "blocked", "false", sa.Boolean()),
    ("observations", "collected_at", "now()", sa.DateTime(timezone=True)),
    ("reports", "created_at", "now()", sa.DateTime(timezone=True)),
    ("scans", "created_at", "now()", sa.DateTime(timezone=True)),
    ("seeds", "created_at", "now()", sa.DateTime(timezone=True)),
    ("settings", "created_at", "now()", sa.DateTime(timezone=True)),
    ("settings", "updated_at", "now()", sa.DateTime(timezone=True)),
    ("targets", "created_at", "now()", sa.DateTime(timezone=True)),
    ("tasks", "attempts", "0", sa.Integer()),
    ("tasks", "observation_count", "0", sa.Integer()),
    ("tasks", "created_at", "now()", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for table, column, fallback, column_type in _COLUMNS:
        op.execute(
            sa.text(f"UPDATE {table} SET {column} = {fallback} WHERE {column} IS NULL")
        )
        op.alter_column(
            table,
            column,
            existing_type=column_type,
            nullable=False,
        )


def downgrade() -> None:
    for table, column, _fallback, column_type in reversed(_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=column_type,
            nullable=True,
        )

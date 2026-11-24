"""kayıtlı izleme bildirimleri

Revision ID: 0023_watch_alerts
Revises: 0022_evidence_snapshots
Create Date: 2026-11-24 21:00:00.000000

- `watch_alerts`: izlenen vakada yeni sızıntı/hesap bulgusu bildirimleri;
  (case_id, dedupe_key) tekildir.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_watch_alerts"
down_revision: Union[str, None] = "0022_evidence_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watch_alerts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("pool_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("case_id", "dedupe_key", name="uq_watch_alerts_dedupe"),
    )
    op.create_index("ix_watch_alerts_case_created", "watch_alerts", ["case_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_watch_alerts_case_created", table_name="watch_alerts")
    op.drop_table("watch_alerts")

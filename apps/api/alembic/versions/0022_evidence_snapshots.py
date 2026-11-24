"""kanıt arşivi

Revision ID: 0022_evidence_snapshots
Revises: 0021_research_control_state
Create Date: 2026-11-24 19:00:00.000000

- `evidence_snapshots`: okunan/alıntılanan herkese açık sayfaların kırpılmış metin
  kopyası + sha256; araştırma atıfları snapshot id'sine bağlanır.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_evidence_snapshots"
down_revision: Union[str, None] = "0021_research_control_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("research_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="research"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("case_id", "url", "sha256", name="uq_evidence_snapshots_content"),
    )
    op.create_index(
        "ix_evidence_snapshots_case_fetched", "evidence_snapshots", ["case_id", "fetched_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_snapshots_case_fetched", table_name="evidence_snapshots")
    op.drop_table("evidence_snapshots")

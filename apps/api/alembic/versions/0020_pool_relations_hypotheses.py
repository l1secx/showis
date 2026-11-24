"""havuz ilişkileri ve hipotezler

Revision ID: 0020_pool_relations_hypotheses
Revises: 0019_research_awaiting_user
Create Date: 2026-11-24 14:00:00.000000

- `pool_relations`: havuz öğeleri arası tipli ilişki (same_person, owns_account,
  uses_email, linked_from, located_in, works_at, alias_of, contradicts).
- `hypotheses`: log-odds ile skorlanan, kanıt zincirli hipotezler.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_pool_relations_hypotheses"
down_revision: Union[str, None] = "0019_research_awaiting_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pool_relations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "src_item_id",
            sa.String(length=36),
            sa.ForeignKey("pool_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_item_id",
            sa.String(length=36),
            sa.ForeignKey("pool_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="rule"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "case_id", "src_item_id", "dst_item_id", "type", name="uq_pool_relations_edge"
        ),
    )
    op.create_index(
        "ix_pool_relations_case_status", "pool_relations", ["case_id", "status"]
    )
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("supporting", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column(
            "contradicting", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column("next_checks", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("item_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="rule"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hypotheses_case_status", "hypotheses", ["case_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_hypotheses_case_status", table_name="hypotheses")
    op.drop_table("hypotheses")
    op.drop_index("ix_pool_relations_case_status", table_name="pool_relations")
    op.drop_table("pool_relations")

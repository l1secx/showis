"""vaka iş birliği — sahip etiketi ve yorumlar

Revision ID: 0012_case_collaboration
Revises: 0011_case_monitoring
Create Date: 2026-11-23 11:40:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_case_collaboration"
down_revision: Union[str, None] = "0011_case_monitoring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("owner", sa.String(length=128), nullable=True))
    op.create_index("ix_cases_owner", "cases", ["owner"], unique=False)
    op.create_table(
        "case_comments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author", sa.String(length=128), nullable=False, server_default="operator"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_case_comments_case_created", "case_comments", ["case_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_case_comments_case_created", table_name="case_comments")
    op.drop_table("case_comments")
    op.drop_index("ix_cases_owner", table_name="cases")
    op.drop_column("cases", "owner")

"""araştırma kontrol durumu

Revision ID: 0021_research_control_state
Revises: 0020_pool_relations_hypotheses
Create Date: 2026-11-24 18:00:00.000000

- `research_control_state`: steer kuyruğu, ask_user sorusu/yanıtı ve durdurma
  bayrağı kalıcıdır; API yeniden başlasa da `awaiting_user` oturumu sürer.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_research_control_state"
down_revision: Union[str, None] = "0020_pool_relations_hypotheses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_control_state",
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("research_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("interrupt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("steering", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("pending_question", sa.JSON(), nullable=True),
        sa.Column("answer", sa.JSON(), nullable=True),
        sa.Column("asked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("research_control_state")

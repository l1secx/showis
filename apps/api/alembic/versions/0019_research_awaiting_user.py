"""araştırma oturumu: awaiting_user durumu aktif sayılır

Revision ID: 0019_research_awaiting_user
Revises: 0018_ai_provider_roles
Create Date: 2026-11-24 12:00:00.000000

Ajan kullanıcıya soru sorarken oturum `awaiting_user` olur; vaka başına tek
aktif oturum kuralı bu durumu da kapsamalı (aksi halde ikinci oturum açılabilir).
`interrupted` bilinçli olarak dışarıdadır: durdurulmuş oturum yer tutmaz.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_research_awaiting_user"
down_revision: Union[str, None] = "0018_ai_provider_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_research_sessions_case_active"


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="research_sessions")
    op.create_index(
        _INDEX,
        "research_sessions",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'awaiting_user')"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="research_sessions")
    op.create_index(
        _INDEX,
        "research_sessions",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

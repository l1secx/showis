"""AI sağlayıcı profiline model rolleri

Revision ID: 0018_ai_provider_roles
Revises: 0017_legacy_llm_profile
Create Date: 2026-11-24 10:00:00.000000

`roles` JSON: {planner, reasoner, extractor, summarizer} -> model kimliği.
Boş/eksik rol profilin varsayılan modeline düşer.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_ai_provider_roles"
down_revision: Union[str, None] = "0017_legacy_llm_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_providers",
        sa.Column("roles", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )


def downgrade() -> None:
    op.drop_column("ai_providers", "roles")

"""vaka webhook URL'i — scan.completed bildirimi

Revision ID: 0013_case_webhook
Revises: 0012_case_collaboration
Create Date: 2026-11-23 12:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_case_webhook"
down_revision: Union[str, None] = "0012_case_collaboration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("webhook_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "webhook_url")

"""vaka muhakeme bayat damgası

Revision ID: 0024_case_reasoning_stale
Revises: 0023_watch_alerts
Create Date: 2026-11-25 14:00:00.000000

- `cases.reasoning_stale_at`: havuzda kullanıcı onay/ret/düzenlemesi sonrası
  hipotez ve ilişkilerin bayatladığını gösteren zaman damgası. Korelasyon veya
  hipotez üretimi çalışınca NULL'a döner.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_case_reasoning_stale"
down_revision: Union[str, None] = "0023_watch_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column("reasoning_stale_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cases", "reasoning_stale_at")

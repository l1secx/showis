"""findings.authenticity — sahte/atil hesap sinyali

Revision ID: 0004_finding_authenticity
Revises: 0003_active_scan_unique
Create Date: 2026-11-17 00:40:00.000000

T-301 authenticity skorunu `Finding` kontratina ekledi ama DB'de kolon yoktu;
skor hesaplaniyor, kalici hale gelmiyordu (API/UI gosteremiyordu).

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_finding_authenticity"
down_revision: Union[str, None] = "0003_active_scan_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("authenticity", sa.Float(), nullable=True),
    )
    op.add_column(
        "findings",
        sa.Column("authenticity_reasons", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("findings", "authenticity_reasons")
    op.drop_column("findings", "authenticity")

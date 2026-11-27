"""findings.confidence_basis — CONFIRMED gerekçesinin kalıcı hali

Revision ID: 0027_finding_confidence_basis
Revises: 0026_case_scoped_ids
Create Date: 2026-11-27 12:00:00.000000

Kolon `NOT NULL` + `server_default=""` ile eklenir; mevcut satırlar boş dize alır
("alan eklenmeden önce üretilmiş kayıt"). Backfill gerekmez.
Geri dönüş kolonu düşürür; gerekçe bilgisi kaybolur (yeniden tarama gerekir).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_finding_confidence_basis"
down_revision: Union[str, None] = "0026_case_scoped_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("confidence_basis", sa.String(length=16), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("findings", "confidence_basis")

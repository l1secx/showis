"""target kimlik türleri — email/phone/domain/ip kolonları

Revision ID: 0010_target_identifiers
Revises: 0009_trgm_search
Create Date: 2026-11-23 10:30:00.000000

Hedef modeli yalnız ad-soyad ve kullanıcı adı biliyordu. OSINT kapsamını
e-posta, telefon, domain ve IP'ye genişletmek için her tür ayrı nullable
kolonda saklanır; mevcut `name`/`handle` satırları değişmez.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_target_identifiers"
down_revision: Union[str, None] = "0009_trgm_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("targets", sa.Column("email", sa.String(length=256), nullable=True))
    op.add_column("targets", sa.Column("phone", sa.String(length=32), nullable=True))
    op.add_column("targets", sa.Column("domain", sa.String(length=253), nullable=True))
    op.add_column("targets", sa.Column("ip", sa.String(length=64), nullable=True))
    op.create_index("ix_targets_email", "targets", ["email"], unique=False)
    op.create_index("ix_targets_domain", "targets", ["domain"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_targets_domain", table_name="targets")
    op.drop_index("ix_targets_email", table_name="targets")
    op.drop_column("targets", "ip")
    op.drop_column("targets", "domain")
    op.drop_column("targets", "phone")
    op.drop_column("targets", "email")

"""tasks.started_at — gercek yurutme suresi icin

Revision ID: 0002_task_started_at
Revises: 0001_initial
Create Date: 2026-11-16 22:50:00.000000

`finished_at - created_at` kuyruk beklemesini de kapsiyordu; kalibrasyon ve
performans olcumu icin gorevin gercekten basladigi an gerekli.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_task_started_at"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Gecmis kayitlarda kuyruk beklemesi bilinmedigi icin geriye donuk deger
    # yazilmaz (NULL kalir). Kalibrasyon yalnizca started_at dolu satirlari
    # kullanir; eski veriler yanlis maliyet uretmesin.


def downgrade() -> None:
    op.drop_column("tasks", "started_at")

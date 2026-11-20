"""tasks.priority — oncelik gercekten uygulanabilsin

Revision ID: 0008_task_priority
Revises: 0007_target_identity
Create Date: 2026-11-18 00:00:00.000000

`TaskSpec.priority` planlama aninda uretiliyordu ama DB'ye yazilmiyordu; bu yuzden
yurutme sirasi her zaman `created_at` idi ve planner gorevleri (kuyrugun sonuna
eklendikleri icin) butce/duvar saati kesintisinde ilk atlanan gorevler oluyordu.
Kolon eklendikten sonra `_refill_pending` `priority, created_at, id` ile siralar.
Eski satirlar 5 (varsayilan) ile doldurulur; davranis oncekiyle aynidir.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_task_priority"
down_revision: Union[str, None] = "0007_target_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `server_default` eski satirlari tek UPDATE ile doldurur; modelin varsayilani
    # da 5 oldugu icin uygulama tarafinda ek bir yol gerekmez.
    op.add_column(
        "tasks",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
    )
    op.create_index("ix_tasks_scan_priority", "tasks", ["scan_id", "priority"])


def downgrade() -> None:
    op.drop_index("ix_tasks_scan_priority", table_name="tasks")
    op.drop_column("tasks", "priority")

"""aktif tarama tekilligi — case basina en fazla bir queued/running scan

Revision ID: 0003_active_scan_unique
Revises: 0002_task_started_at
Create Date: 2026-11-16 23:22:00.000000

Uygulama katmani `POST /cases/{id}/scan` icin 409 donduruyor (SEC-05), ancak iki
es zamanli istek kontrolu atlatabilir. Bu kismi (partial) unique index yarisi
DB seviyesinde kapatir.

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_active_scan_unique"
down_revision: Union[str, None] = "0002_task_started_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_scans_case_active",
        "scans",
        ["case_id"],
        unique=True,
        postgresql_where="status IN ('queued', 'running')",
    )


def downgrade() -> None:
    op.drop_index("uq_scans_case_active", table_name="scans")

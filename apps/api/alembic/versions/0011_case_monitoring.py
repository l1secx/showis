"""vaka izleme — otomatik yeniden tarama alanları

Revision ID: 0011_case_monitoring
Revises: 0010_target_identifiers
Create Date: 2026-11-23 11:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_case_monitoring"
down_revision: Union[str, None] = "0010_target_identifiers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column(
            "monitor_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "cases",
        sa.Column(
            "monitor_interval_hours",
            sa.Integer(),
            nullable=False,
            server_default="24",
        ),
    )
    op.add_column(
        "cases",
        sa.Column(
            "monitor_mode",
            sa.String(length=16),
            nullable=False,
            server_default="O2",
        ),
    )
    op.add_column(
        "cases",
        sa.Column("monitor_last_scan_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_cases_monitor_due",
        "cases",
        ["monitor_enabled", "monitor_last_scan_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_cases_monitor_due", table_name="cases")
    op.drop_column("cases", "monitor_last_scan_at")
    op.drop_column("cases", "monitor_mode")
    op.drop_column("cases", "monitor_interval_hours")
    op.drop_column("cases", "monitor_enabled")

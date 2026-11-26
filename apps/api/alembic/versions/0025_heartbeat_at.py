"""tarama/araştırma canlılık damgası (stale recovery)

Revision ID: 0025_heartbeat_at
Revises: 0024_case_reasoning_stale
Create Date: 2026-11-26 12:00:00.000000

- `scans.heartbeat_at`: çalışan tarama döngüsü periyodik günceller. Stale
  recovery canlı O3 taramasını (duvar bütçesi = eski eşik) ikinci kez
  başlatmasın diye "son canlılık" olarak kullanılır.
- `research_sessions.heartbeat_at`: API süreci çalışırken periyodik günceller;
  süreç çökerse bayat oturum `interrupted` işaretlenip "Devam et" ile sürülür.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_heartbeat_at"
down_revision: Union[str, None] = "0024_case_reasoning_stale"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "research_sessions",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_scans_status_heartbeat", "scans", ["status", "heartbeat_at"]
    )
    op.create_index(
        "ix_research_sessions_status_heartbeat",
        "research_sessions",
        ["status", "heartbeat_at"],
    )
    #: Retention purge sorguları (B-026).
    op.create_index("ix_evidence_expires_at", "evidence", ["expires_at"])
    op.create_index(
        "ix_evidence_snapshots_expires_at", "evidence_snapshots", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_snapshots_expires_at", table_name="evidence_snapshots")
    op.drop_index("ix_evidence_expires_at", table_name="evidence")
    op.drop_index(
        "ix_research_sessions_status_heartbeat", table_name="research_sessions"
    )
    op.drop_index("ix_scans_status_heartbeat", table_name="scans")
    op.drop_column("research_sessions", "heartbeat_at")
    op.drop_column("scans", "heartbeat_at")

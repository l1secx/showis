"""sorgu indeksleri — FK kolonlari ve sik filtrelenen alanlar

Revision ID: 0006_query_indexes
Revises: 0005_wide_identity_ids
Create Date: 2026-11-17 02:40:00.000000

Postgres FK kolonlarina otomatik indeks koymaz; `findings`/`scans`/`tasks`
sorgulari `case_id`/`scan_id` ile filtrelendigi icin 10k+ satirda seq scan'e
duser. Bu migration okuma yollarini indeksler (yazma maliyeti ihmal edilebilir).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006_query_indexes"
down_revision: Union[str, None] = "0005_wide_identity_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (index adi, tablo, kolonlar)
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_cases_created_at", "cases", ("created_at",)),
    ("ix_targets_case_id", "targets", ("case_id",)),
    ("ix_scans_case_created", "scans", ("case_id", "created_at")),
    ("ix_findings_case_status", "findings", ("case_id", "status")),
    ("ix_findings_scan_id", "findings", ("scan_id",)),
    ("ix_entities_case_id", "entities", ("case_id",)),
    ("ix_edges_case_id", "edges", ("case_id",)),
    ("ix_observations_scan_id", "observations", ("scan_id",)),
    ("ix_tasks_scan_status", "tasks", ("scan_id", "status")),
    ("ix_evidence_case_id", "evidence", ("case_id",)),
    ("ix_audit_logs_created_at", "audit_logs", ("created_at",)),
    # NOT: `audit_logs(case_id)` icin ayri indeks EKLENMEZ — 0001'de olusturulan
    # `ix_audit_logs_case_id_created_at` bilesik indeksinin tam onekidir
    # (T-364 olcumu: DROP sonrasi iki sorgu da bilesik indeksle karsilandi).
)


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, list(columns), unique=False)


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)

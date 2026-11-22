"""pg_trgm arama indeksleri — hizli ILIKE / metin arama

Revision ID: 0009_trgm_search
Revises: 0008_task_priority
Create Date: 2026-11-22 17:50:00.000000

Vaka arama (`GET /cases?q=...`) sorgularinda `ILIKE '%q%'` olcekte seq scan'e
dusmesini onler. Postgres ortaminda `pg_trgm` eklentisi ve GIN indeksleri
olusturulur; test/SQLite ortaminda sessizce atlanir.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009_trgm_search"
down_revision: Union[str, None] = "0008_task_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_cases_name_trgm "
            "ON cases USING gin (name gin_trgm_ops);"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_targets_firstname_trgm "
            "ON targets USING gin (first_name gin_trgm_ops);"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_targets_lastname_trgm "
            "ON targets USING gin (last_name gin_trgm_ops);"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_targets_nickname_trgm "
            "ON targets USING gin (nickname gin_trgm_ops);"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_targets_nickname_trgm;")
        op.execute("DROP INDEX IF EXISTS ix_targets_lastname_trgm;")
        op.execute("DROP INDEX IF EXISTS ix_targets_firstname_trgm;")
        op.execute("DROP INDEX IF EXISTS ix_cases_name_trgm;")

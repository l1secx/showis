"""araştırma havuzu, AI ajan oturumları, çoklu sağlayıcı ve tedarik zinciri

Revision ID: 0016_research_workspace
Revises: 0015_schema_contract
Create Date: 2026-11-23 15:45:00.000000

Yeni yüzeyler:

- `ai_providers`: OpenAI uyumlu çoklu LLM profili; tek aktif profil kısmi
  unique index ile garanti edilir. Eski `settings(provider="llm")` kaydı
  geriye dönük fallback olarak korunur.
- `pool_items`: vaka içi araştırma havuzu; kullanıcı girdisi, tarama ve AI
  önerileri tek yerde. `(case_id, kind, normalized)` tekilliği tekrarı önler.
- `research_sessions` / `research_messages`: AI araştırma oturumu ve adım
  geçmişi. Vaka başına tek çalışan oturum kısmi unique index ile sınırlanır.
- `supply_chain_nodes` / `supply_chain_edges`: kişi kimlik grafiğinden ayrı
  tedarik zinciri modeli.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_research_workspace"
down_revision: Union[str, None] = "0015_schema_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("preset", sa.String(length=32), nullable=False, server_default="custom"),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("secret_masked", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "uq_ai_providers_active",
        "ai_providers",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "pool_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("case_id", "kind", "normalized", name="uq_pool_items_case_kind_value"),
    )
    op.create_index("ix_pool_items_case_status", "pool_items", ["case_id", "status"])
    op.create_index("ix_pool_items_case_source", "pool_items", ["case_id", "source"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pool_items_value_trgm "
        "ON pool_items USING gin (value gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pool_items_label_trgm "
        "ON pool_items USING gin (label gin_trgm_ops);"
    )

    op.create_table(
        "research_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="research"),
        sa.Column(
            "provider_id",
            sa.String(length=36),
            sa.ForeignKey("ai_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_research_sessions_case_active",
        "research_sessions",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_research_sessions_case_created",
        "research_sessions",
        ["case_id", "created_at"],
    )

    op.create_table(
        "research_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("research_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("tool_args", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("tool_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("citations", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_research_messages_session_created",
        "research_messages",
        ["session_id", "created_at"],
    )

    op.create_table(
        "supply_chain_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="company"),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("normalized", sa.String(length=256), nullable=False),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("identifiers", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("case_id", "kind", "normalized", name="uq_supply_chain_node"),
    )
    op.create_index("ix_supply_chain_nodes_case_kind", "supply_chain_nodes", ["case_id", "kind"])
    op.create_index(
        "ix_supply_chain_nodes_case_status", "supply_chain_nodes", ["case_id", "status"]
    )

    op.create_table(
        "supply_chain_edges",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(length=36),
            sa.ForeignKey("supply_chain_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.String(length=36),
            sa.ForeignKey("supply_chain_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(length=32), nullable=False, server_default="related_to"),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "case_id",
            "source_id",
            "target_id",
            "relation",
            name="uq_supply_chain_edge",
        ),
    )
    op.create_index("ix_supply_chain_edges_case", "supply_chain_edges", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_supply_chain_edges_case", table_name="supply_chain_edges")
    op.drop_table("supply_chain_edges")
    op.drop_index("ix_supply_chain_nodes_case_status", table_name="supply_chain_nodes")
    op.drop_index("ix_supply_chain_nodes_case_kind", table_name="supply_chain_nodes")
    op.drop_table("supply_chain_nodes")

    op.drop_index("ix_research_messages_session_created", table_name="research_messages")
    op.drop_table("research_messages")
    op.drop_index("ix_research_sessions_case_created", table_name="research_sessions")
    op.drop_index("uq_research_sessions_case_active", table_name="research_sessions")
    op.drop_table("research_sessions")

    op.execute("DROP INDEX IF EXISTS ix_pool_items_label_trgm;")
    op.execute("DROP INDEX IF EXISTS ix_pool_items_value_trgm;")
    op.drop_index("ix_pool_items_case_source", table_name="pool_items")
    op.drop_index("ix_pool_items_case_status", table_name="pool_items")
    op.drop_table("pool_items")

    op.drop_index("uq_ai_providers_active", table_name="ai_providers")
    op.drop_table("ai_providers")

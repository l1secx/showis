"""eski tek `llm` ayarını çoklu profil tablosuna taşı

Revision ID: 0017_legacy_llm_profile
Revises: 0016_research_workspace
Create Date: 2026-11-23 17:10:00.000000

Kullanıcıların mevcut OpenCode/OpenAI anahtarı `settings(provider="llm")`
altında duruyor. Yeni arayüz profil listesi gösterdiği için bu kayıt
görünmez kalıyordu; anahtarı yeniden girmeye gerek kalmasın diye tek
seferlik veri taşıması yapılır. Ad çakışırsa veya zaten aktif bir profil
varsa satır eklenmez/aktifleştirilmez.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0017_legacy_llm_profile"
down_revision: Union[str, None] = "0016_research_workspace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO ai_providers
            (id, name, preset, config, secret_encrypted, secret_masked,
             enabled, is_active, created_at, updated_at)
        SELECT
            md5(random()::text || clock_timestamp()::text),
            'Eski LLM ayarı',
            COALESCE(NULLIF(config->>'preset', ''), 'custom'),
            COALESCE(config, '{}'::json),
            secret_encrypted,
            secret_masked,
            enabled,
            CASE
                WHEN enabled
                     AND NOT EXISTS (SELECT 1 FROM ai_providers WHERE is_active)
                THEN true
                ELSE false
            END,
            created_at,
            updated_at
        FROM settings
        WHERE provider = 'llm'
          AND secret_encrypted IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM ai_providers WHERE name = 'Eski LLM ayarı'
          );
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_providers WHERE name = 'Eski LLM ayarı';")

"""findings.confidence_basis — CONFIRMED gerekcesinin kalici hali

Revision ID: 0008_finding_confidence_basis
Revises: 0007_target_identity
Create Date: 2026-11-18 15:05:00.000000

D8/C1: `Finding.confidence_basis` kontratta vardi (cross_link | commit_email |
avatar | weak) ama DB'de kolon yoktu; tarama bitince gerekce kayboluyor,
`GET /cases/{id}/findings` "neden CONFIRMED" sorusunu yanitlayamiyordu.

Veri gocu YOK: kolon `NOT NULL` + `server_default=""` ile eklenir; mevcut
satirlar bos dize alir ("alan eklenmeden once uretilmis kayit" anlami kontratta
belgelenmistir). Backfill gerekmez.

Geri donus: kolon dusurulur -> gerekce bilgisi kalici olarak kaybolur (yeniden
tarama gerekir). Yalnizca gelistirme ortaminda anlamlidir.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_finding_confidence_basis"
down_revision: Union[str, None] = "0007_target_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: `Finding.confidence_basis` icin izinli degerler (kontrat `findings.py`).
#: DB'de CHECK kisiti yok: kontrat dogrulamasi Pydantic katmaninda; kolon
#: genisligi (16) en uzun degeri ("commit_email") kapsar.
MAX_LENGTH = 16


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column(
            "confidence_basis",
            sa.String(length=MAX_LENGTH),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("findings", "confidence_basis")

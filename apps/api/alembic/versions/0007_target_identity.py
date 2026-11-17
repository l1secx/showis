"""hedef kimligi — nickname + kind, ad/soyad nullable

Revision ID: 0007_target_identity
Revises: 0006_query_indexes
Create Date: 2026-11-17 10:20:00.000000

Neden: hedefler artik yalnizca kullanici adiyla (handle) tanimlanabiliyor. Bu
durumda `first_name`/`last_name` bilinmez; ikisini NOT NULL tutmak handle-only
hedefi imkansiz kilar. Handle `nickname` kolonunda saklanir, `kind` ise kimligin
"name" mi "handle" mi oldugunu soyler (mevcut satirlar "name" varsayilani alir).

Backfill: eski istemciler kullanici adini `hints` icine yaziyordu
(`hints.nickname` / `hints.username`); bu degerler yeni `nickname` kolonuna tasinir.
Ayni deger `hints` icinde birakilir — okuma yollari geriye donuk uyumlu kalir.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_target_identity"
down_revision: Union[str, None] = "0006_query_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Handle-only hedeflerde kullanici adi `hints` icinde bu anahtarlarla geliyordu
#: (`nickname` once, sonra `username`).
#:
#: NOT: `hints` kolonu Postgres `json`'dur (0001_initial_schema `sa.JSON()`), `jsonb`
#: degil. `?` (anahtar var mi) operatoru yalnizca jsonb'de bulunur; bu yuzden
#: anahtar varligi `->>` uzerinden test edilir (hem `json` hem `jsonb` ile calisir).
_NICKNAME_BACKFILL = """
UPDATE targets
   SET nickname = COALESCE(hints->>'nickname', hints->>'username')
 WHERE nickname IS NULL
   AND COALESCE(hints->>'nickname', hints->>'username') IS NOT NULL
"""


def upgrade() -> None:
    op.add_column("targets", sa.Column("nickname", sa.String(length=128), nullable=True))
    op.add_column(
        "targets",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="name"),
    )
    for column in ("first_name", "last_name"):
        op.alter_column(
            "targets",
            column,
            existing_type=sa.String(length=128),
            existing_nullable=False,
            nullable=True,
        )
    op.execute(sa.text(_NICKNAME_BACKFILL))


def downgrade() -> None:
    # VERI KAYBI: handle-only hedeflerin `nickname` degeri ve `kind` bilgisi
    # tamamen kaybolur (kolonlar dusurulur). Ayrica ad/soyad NOT NULL'a donerken
    # bos satirlar '' ile doldurulur — "ad bilinmiyor" ile "ad bos string"
    # ayrimi geri alinamaz. Geri donus yalnizca gelistirme ortaminda anlamlidir.
    # asyncpg tek `execute` icinde birden fazla komut kabul etmez: ayri ayri calis.
    for column in ("first_name", "last_name"):
        op.execute(sa.text(f"UPDATE targets SET {column} = '' WHERE {column} IS NULL"))
    for column in ("first_name", "last_name"):
        op.alter_column(
            "targets",
            column,
            existing_type=sa.String(length=128),
            existing_nullable=True,
            nullable=False,
        )
    op.drop_column("targets", "kind")
    op.drop_column("targets", "nickname")

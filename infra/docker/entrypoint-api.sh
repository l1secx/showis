#!/bin/sh
# API konteyner girisi: sema migrasyonlari + API.
#
# UMASK (T-314 / SEC-19): API'nin olusturdugu rapor/kanit dosyalari kisisel veri
# icerir. `umask 077` ile yeni dosyalar 0600, yeni klasorler 0700 olur; varsayilan
# 0022 (0644/0755) ile ayni makinedeki diger kullanicilar okuyabiliyordu.
set -e
umask 077

cd /app/apps/api
alembic upgrade head
exec "$@"

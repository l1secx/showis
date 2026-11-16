#!/bin/sh
# Once sema migrasyonlarini calistir, sonra API'yi baslat
set -e
cd /app/apps/api
alembic upgrade head
exec "$@"

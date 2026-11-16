#!/usr/bin/env bash
# Showis geri yukleme: backups/<timestamp>/showis-db.dump -> PostgreSQL
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-backups}"

if [ $# -lt 1 ]; then
  echo "Kullanim: $0 <backup-dizini|dump-dosyasi>" >&2
  echo "Mevcut yedekler:" >&2
  ls -1 "${BACKUP_DIR}" 2>/dev/null | tail -10 >&2 || true
  exit 1
fi

SRC="$1"
if [ -d "${SRC}" ]; then
  DUMP="${SRC}/showis-db.dump"
else
  DUMP="${SRC}"
fi

if [ ! -f "${DUMP}" ]; then
  echo "Hata: dump dosyasi bulunamadi: ${DUMP}" >&2
  exit 1
fi

echo "[restore] ${DUMP} geri yukleniyor..."
echo "DIKKAT: mevcut veritabani uzerine yazilacak!"
read -r -p "Devam? [e/H] " ans
if [ "${ans}" != "e" ] && [ "${ans}" != "E" ]; then
  echo "Iptal edildi."
  exit 0
fi

docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  sh -c 'pg_restore -U "'"${POSTGRES_USER:-showis}"'" -d "'"${POSTGRES_DB:-showis}"'" --clean --if-exists' \
  < "${DUMP}"

echo "[restore] Tamamlandi. Uygulamayi yeniden baslatin."

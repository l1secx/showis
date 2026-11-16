#!/usr/bin/env bash
# Showis yedekleme: PostgreSQL dump + MinIO veri senkronu (opsiyonel)
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${BACKUP_DIR}/${TIMESTAMP}"

mkdir -p "${OUT_DIR}"

echo "[backup] PostgreSQL dump aliniyor..."
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-showis}" -d "${POSTGRES_DB:-showis}" -Fc \
  > "${OUT_DIR}/showis-db.dump"

echo "[backup] MinIO evidence verisi senkronlaniyor (varsa)..."
if docker compose -f "${COMPOSE_FILE}" ps --status running minio | grep -q minio; then
  docker run --rm --network host \
    -v "${PWD}/minio-backup:/data" \
    minio/mc:latest sh -c "
      mc alias set local http://localhost:9000 '${MINIO_ACCESS_KEY:-minioadmin}' '${MINIO_SECRET_KEY:-minioadmin}' &&
      mc mirror --overwrite local/showis-evidence /data/showis-evidence || true
    " || echo "[backup] MinIO mirror atlandi (mc erisimi yok)"
fi

echo "${TIMESTAMP}" > "${OUT_DIR}/manifest.txt"
echo "[backup] Tamamlandi: ${OUT_DIR}"

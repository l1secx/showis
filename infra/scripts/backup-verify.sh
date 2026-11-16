#!/usr/bin/env bash
# Yedek dogrulama tatbikati (T-315 / ROADMAP 6.3)
#
# Ne yapar:
#   1) En yeni (veya verilen) dump'i sha256 ile dogrular
#   2) Ayni Postgres konteynerinde `showis_restore_test` veritabanini olusturur
#   3) Dump'i oraya restore eder (canli DB'ye DOKUNMAZ)
#   4) Tablo satir sayilarini canli DB ile karsilastirir (cases, scans, tasks, findings, entities)
#   5) Test veritabanini siler
#   6) Fark varsa != 0 ile cikar
#
# Kullanim:
#   bash infra/scripts/backup-verify.sh [DUMP]
#
# Garantiler:
#   * Hedef DB adi sabittir (showis_restore_test) ve canli DB adiyla esitlenirse
#     script hemen durur.
#   * Restore `--exit-on-error --single-transaction`.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/data/backups}"
PG_CONTAINER="${PG_CONTAINER:-showis-postgres}"
PG_USER="${POSTGRES_USER:-showis}"
LIVE_DB="${POSTGRES_DB:-showis}"
TEST_DB="${SHOWIS_RESTORE_TEST_DB:-showis_restore_test}"
TABLES=(cases targets scans tasks findings entities evidence)

log() { printf '[verify] %s\n' "$*"; }
die() { printf '[verify] HATA: %s\n' "$*" >&2; exit 1; }

[ "$TEST_DB" != "$LIVE_DB" ] || die "test DB adi canli DB ile ayni olamaz ($TEST_DB)"
docker inspect -f '{{.State.Running}}' "$PG_CONTAINER" 2>/dev/null | grep -q true \
  || die "postgres konteyneri calismiyor: $PG_CONTAINER"

SRC="${1:-}"
if [ -z "$SRC" ]; then
  SRC="$(ls -1t "$BACKUP_DIR"/showis-*.dump 2>/dev/null | head -1 || true)"
  [ -n "$SRC" ] || die "$BACKUP_DIR icinde dump yok — once backup.sh calistirin"
fi
[ -f "$SRC" ] || die "dump bulunamadi: $SRC"

log "dump: $(basename "$SRC") ($(du -h "$SRC" | cut -f1))"

# --- 1) Butunluk -----------------------------------------------------------
if [ -f "${SRC}.sha256" ]; then
  ( cd "$(dirname "$SRC")" && sha256sum -c --status "$(basename "${SRC}").sha256" ) \
    || die "sha256 uyusmuyor"
  log "sha256 OK"
else
  log "UYARI: sha256 dosyasi yok"
fi
docker exec -i "$PG_CONTAINER" pg_restore --list < "$SRC" >/dev/null || die "dump okunamadi (pg_restore --list)"
log "arsiv formati OK (pg_restore --list)"

count() { # <db> <tablo-ifadesi>  -> sayi (bos ise "ERR")
  docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$1" -tAc "SELECT count(*) FROM $2" 2>/dev/null \
    | tr -d '[:space:]'
}

# --- 2) Temiz test DB ------------------------------------------------------
log "test veritabani hazirlaniyor: $TEST_DB"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -q -c "DROP DATABASE IF EXISTS \"$TEST_DB\";" >/dev/null 2>&1
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -q -c "CREATE DATABASE \"$TEST_DB\";" >/dev/null

cleanup() {
  log "test veritabani siliniyor: $TEST_DB"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -q -c "DROP DATABASE IF EXISTS \"$TEST_DB\";" >/dev/null 2>&1 2>&1 || true
}
trap cleanup EXIT

# --- 3) Restore ------------------------------------------------------------
log "restore -> $TEST_DB"
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$TEST_DB" \
  --no-owner --no-acl --exit-on-error --single-transaction < "$SRC" \
  || die "pg_restore basarisiz (tatbikat BASARISIZ)"

# --- 4) Karsilastirma ------------------------------------------------------
echo
printf '%-12s %10s %10s %8s\n' "tablo" "canli" "restore" "durum"
printf '%-12s %10s %10s %8s\n' "------------" "----------" "----------" "--------"
mismatch=0
for t in "${TABLES[@]}"; do
  live="$(count "$LIVE_DB" "$t")"
  test="$(count "$TEST_DB" "$t")"
  if [ -z "$live" ] || [ -z "$test" ]; then
    live="${live:-ERR}"; test="${test:-ERR}"
    status="HATA"
    mismatch=$((mismatch + 1))
  elif [ "$live" = "$test" ]; then
    status="OK"
  else
    status="FARK"
    mismatch=$((mismatch + 1))
  fi
  printf '%-12s %10s %10s %8s\n' "$t" "$live" "$test" "$status"
done

# --- 5) Semaya dair hizli kontrol -----------------------------------------
schema_live="$(count "$LIVE_DB" "information_schema.tables WHERE table_schema='public'")"
schema_test="$(count "$TEST_DB" "information_schema.tables WHERE table_schema='public'")"
printf '%-12s %10s %10s %8s\n' "tablo_sayisi" "$schema_live" "$schema_test" \
  "$([ "$schema_live" = "$schema_test" ] && echo OK || echo FARK)"
[ "$schema_live" = "$schema_test" ] || mismatch=$((mismatch + 1))

# --- 6) Dosya yedegi (varsa) ----------------------------------------------
files_tgz="${SRC%.dump}.files.tar.gz"
if [ -f "$files_tgz" ]; then
  if [ -f "${files_tgz}.sha256" ] && ( cd "$(dirname "$files_tgz")" && sha256sum -c --status "$(basename "${files_tgz}").sha256" ); then
    n="$(tar -tzf "$files_tgz" 2>/dev/null | wc -l)"
    echo
    log "dosya yedegi OK: $(basename "$files_tgz") (${n} girdi)"
  else
    log "UYARI: dosya yedegi bozuk veya sha256 yok: $(basename "$files_tgz")"
    mismatch=$((mismatch + 1))
  fi
fi

echo
if [ "$mismatch" -eq 0 ]; then
  log "TATBIKAT BASARILI — yedek geri yuklenebilir ve satir sayilari birebir"
  exit 0
fi
# T-353: canli sistemde aradan gecen surede veri degisebilir (vaka silme/ekleme, retention).
# Bu durum yedegin bozuk oldugunu GOSTERMEZ; taze yedekle tekrar deneyin.
log "TATBIKAT BASARISIZ — $mismatch uyusmazlik"
log "IPUCU: dump alindiktan sonra canli veri degismis olabilir (vaka silme/ekleme, retention)."
log "       Teyit icin: bash infra/scripts/backup.sh && bash infra/scripts/backup-verify.sh"
exit 1

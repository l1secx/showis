#!/usr/bin/env bash
# Showis geri yukleme (T-315 / ROADMAP 6.3)
#
# Kullanim:
#   restore.sh --to-test [DUMP]        # showis_restore_test veritabanina (guvenli, varsayilan)
#   restore.sh --db <DB> [DUMP]        # belirtilen veritabanina ('showis' REDDEDILIR)
#   restore.sh --force-live [DUMP]     # FELAKET kurtarma: canli 'showis' DB'ye yazar (onay ister)
#
#   DUMP verilmezse data/backups/ icindeki EN YENI dump kullanilir.
#
# Guvenlik:
#   * Varsayilan hedef canli DB DEGIL: `showis_restore_test`.
#   * Canliya yazmak icin `--force-live` + ekranda DB adini yazma onayi gerekir.
#   * Varsa `.sha256` dosyasi dogrulanir; pg_restore `--exit-on-error --single-transaction`.
#
# Cikis kodu: 0 basarili, != 0 hata.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/data/backups}"
PG_CONTAINER="${PG_CONTAINER:-showis-postgres}"
PG_USER="${POSTGRES_USER:-showis}"
LIVE_DB="${POSTGRES_DB:-showis}"
TEST_DB="${SHOWIS_RESTORE_TEST_DB:-showis_restore_test}"

MODE="test"
TARGET_DB="$TEST_DB"
SRC=""

while [ $# -gt 0 ]; do
  case "$1" in
    --to-test) MODE="test"; TARGET_DB="$TEST_DB"; shift ;;
    --db) MODE="named"; TARGET_DB="${2:-}"; shift 2 ;;
    --force-live) MODE="live"; TARGET_DB="$LIVE_DB"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "Bilinmeyen arguman: $1" >&2; exit 2 ;;
    *) SRC="$1"; shift ;;
  esac
done

log() { printf '[restore] %s\n' "$*"; }
die() { printf '[restore] HATA: %s\n' "$*" >&2; exit 1; }

[ -n "$TARGET_DB" ] || die "hedef veritabani bos"
# T-010/D-023: DB adi SQL/komut satirina giriyor; yalniz guvenli kimlik kabul et.
for _db in "$TARGET_DB" "$LIVE_DB" "$TEST_DB"; do
  printf '%s' "$_db" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' \
    || die "gecersiz veritabani adi (harf/rakam/alt cizgi): $_db"
done
unset _db
[ "$TARGET_DB" != "$TEST_DB" ] || MODE="test"

# Canli DB korumasi
if [ "$TARGET_DB" = "$LIVE_DB" ] && [ "$MODE" != "live" ]; then
  die "hedef canli veritabani ($LIVE_DB); bunun icin --force-live kullanin"
fi
if [ "$MODE" = "live" ]; then
  echo "DIKKAT: CANLI '$LIVE_DB' veritabaninin uzerine yazilacak (--clean)."
  printf "Onay icin veritabani adini yazin (%s): " "$LIVE_DB"
  read -r ans
  [ "$ans" = "$LIVE_DB" ] || die "onay alinamadi, iptal"
fi

# Kaynak dump
if [ -z "$SRC" ]; then
  SRC="$(ls -1t "$BACKUP_DIR"/showis-*.dump 2>/dev/null | head -1 || true)"
  [ -n "$SRC" ] || die "$BACKUP_DIR icinde dump yok"
fi
[ -f "$SRC" ] || die "dump bulunamadi: $SRC"

# sha256 dogrulama
if [ -f "${SRC}.sha256" ]; then
  log "sha256 dogrulaniyor..."
  ( cd "$(dirname "$SRC")" && sha256sum -c --status "$(basename "${SRC}").sha256" ) \
    || die "sha256 uyusmuyor: $SRC"
  log "sha256 OK"
else
  log "UYARI: ${SRC}.sha256 yok — butunluk dogrulanamadi"
fi

docker inspect -f '{{.State.Running}}' "$PG_CONTAINER" 2>/dev/null | grep -q true \
  || die "postgres konteyneri calismiyor: $PG_CONTAINER"

psql_db() { # <db> <sql>
  docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$1" -tAc "$2"
}

log "hedef: $TARGET_DB (konteyner: $PG_CONTAINER)"

if [ "$TARGET_DB" = "$TEST_DB" ]; then
  log "test veritabani sifirlaniyor..."
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c \
    "DROP DATABASE IF EXISTS \"$TEST_DB\";" >/dev/null
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c \
    "CREATE DATABASE \"$TEST_DB\";" >/dev/null
elif [ "$MODE" = "named" ]; then
  if [ "$(psql_db postgres "SELECT 1 FROM pg_database WHERE datname='$TARGET_DB'")" != "1" ]; then
    log "veritabani yok, olusturuluyor: $TARGET_DB"
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c \
      "CREATE DATABASE \"$TARGET_DB\";" >/dev/null
  fi
fi

log "pg_restore basliyor: $(basename "$SRC")"
RESTORE_ARGS=(-U "$PG_USER" -d "$TARGET_DB" --no-owner --no-acl --exit-on-error --single-transaction)
if [ "$MODE" = "live" ]; then
  RESTORE_ARGS+=(--clean --if-exists)
fi
docker exec -i "$PG_CONTAINER" pg_restore "${RESTORE_ARGS[@]}" < "$SRC" || die "pg_restore basarisiz"

log "geri yukleme tamamlandi. Satir sayilari:"
for t in cases targets scans tasks findings entities evidence; do
  n="$(psql_db "$TARGET_DB" "SELECT count(*) FROM $t" 2>/dev/null || echo "-")"
  printf '   %-12s %s\n' "$t" "$n"
done

if [ "$MODE" = "live" ]; then
  echo
  log "SONRAKI ADIM: uygulama konteynerlerini yeniden baslatin (Atlas onayi ile):"
  log "  docker compose -f infra/docker-compose.yml up -d --no-deps api worker beat"
fi

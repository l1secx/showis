#!/usr/bin/env bash
# Showis yedekleme (T-315 / ROADMAP 6.3)
#
# Neleri yedekler:
#   1) PostgreSQL  -> pg_dump -Fc (custom format, sikistirmali)  showis-YYYYmmdd-HHMM.dump
#   2) Rapor + kanit dosyalari (named volume `infra_report_data:/app/data`)
#                  -> tar.gz  showis-YYYYmmdd-HHMM.files.tar.gz
#   3) Her ikisi icin sha256: <ad>.sha256
#
# Tasarim notlari:
#   * Konum: `data/backups/` (varsayilan), izin 700; dosyalar 600 (T-314 standardi).
#   * Atomik yazim: once `.tmp` dosyasina yazilir, dogrulanir, sonra rename edilir.
#   * Dogrulama: dump `pg_restore --list` ile okunabilir mi, sha256 tutuyor mu.
#   * Retention: son 7 gunun her gun icin en yeni yedek + son 4 haftanin pazartesileri.
#     Silme YALNIZ `data/backups/` icinde ve `showis-*` desenine uyan dosyalarda.
#   * Toplam boyut 2 GB'i gecerse en eski yedekler (en yenisi korunur) budanir.
#   * MinIO: kodda kullanilmiyor (evidence yerel diskte / volume'da), bu yuzden
#     MinIO mirror'u kaldirildi — yaniltici "basarili" gorunuyordu.
#
# Cikis kodu: 0 basarili, != 0 hata (systemd journal'a duser).
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/data/backups}"
PG_CONTAINER="${PG_CONTAINER:-showis-postgres}"
DATA_VOLUME="${SHOWIS_DATA_VOLUME:-infra_report_data}"
PG_USER="${POSTGRES_USER:-showis}"
PG_DB="${POSTGRES_DB:-showis}"
# Digest pinli yardimci imaj (T-304 standardi: `latest` yok).
TOOL_IMAGE="${SHOWIS_TOOL_IMAGE:-alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc}"
RETENTION_DAILY_DAYS=7
RETENTION_WEEKLY_WEEKS=4
MAX_TOTAL_BYTES=$((2 * 1024 * 1024 * 1024))   # 2 GB

STAMP="$(date -u +%Y%m%d-%H%M)"
NAME="showis-${STAMP}"

log() { printf '[backup] %s\n' "$*"; }
die() { printf '[backup] HATA: %s\n' "$*" >&2; exit 1; }

trap 'die "beklenmeyen hata (satir $LINENO)"' ERR

# --- on kosullar -----------------------------------------------------------
command -v docker >/dev/null || die "docker bulunamadi"
docker inspect -f '{{.State.Running}}' "$PG_CONTAINER" 2>/dev/null | grep -q true \
  || die "postgres konteyneri calismiyor: $PG_CONTAINER"
docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1 \
  || log "UYARI: $DATA_VOLUME volume'u yok — dosya yedegi atlanacak"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true
# Yedek dizini simetrigi: eski (gevsek) izinler varsa sik.
find "$BACKUP_DIR" -maxdepth 1 -type f -exec chmod 600 {} + 2>/dev/null || true

log "hedef: $BACKUP_DIR"
log "veritabani: $PG_DB @ $PG_CONTAINER"

# --- 1) PostgreSQL dump ----------------------------------------------------
DUMP="$BACKUP_DIR/${NAME}.dump"
TMP_DUMP="$BACKUP_DIR/.${NAME}.dump.tmp"
log "pg_dump -Fc basliyor..."
if ! docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
      -Fc --no-owner --no-acl > "$TMP_DUMP"; then
  rm -f "$TMP_DUMP"
  die "pg_dump basarisiz"
fi
[ -s "$TMP_DUMP" ] || { rm -f "$TMP_DUMP"; die "dump bos"; }
# Butunluk: custom-format arsiv okunabiliyor mu?
docker exec -i "$PG_CONTAINER" pg_restore --list < "$TMP_DUMP" >/dev/null \
  || { rm -f "$TMP_DUMP"; die "dump dogrulanamadi (pg_restore --list)"; }
chmod 600 "$TMP_DUMP"
mv -f "$TMP_DUMP" "$DUMP"
sha256sum "$DUMP" | awk -v f="$(basename "$DUMP")" '{print $1"  "f}' > "${DUMP}.sha256"
chmod 600 "${DUMP}.sha256"
log "dump OK: $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1))"

# --- 2) Rapor + kanit dosyalari (volume) ----------------------------------
FILES_TGZ="$BACKUP_DIR/${NAME}.files.tar.gz"
if docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
  if ! docker run --rm --user 0 -v "${DATA_VOLUME}:/src:ro" "$TOOL_IMAGE" \
        sh -c '[ -d /src/reports ] || [ -d /src/evidence ]'; then
    log "UYARI: volume'da reports/evidence yok — dosya yedegi atlandi"
  else
    log "dosya yedegi (reports/evidence) basliyor..."
    # Root olarak okur (volume 10001:10001/700), cikti dosyasini kerem'e devreder.
    docker run --rm --user 0 \
      -e "OUT_NAME=${NAME}" -e "OUT_UID=$(id -u)" -e "OUT_GID=$(id -g)" \
      -v "${DATA_VOLUME}:/src:ro" \
      -v "${BACKUP_DIR}:/out" \
      "$TOOL_IMAGE" sh -c '
        set -e
        cd /src
        dirs=""
        for d in reports evidence; do [ -d "$d" ] && dirs="$dirs $d"; done
        [ -n "$dirs" ] || exit 0
        tar -czf "/out/${OUT_NAME}.files.tar.gz" $dirs
        chown "${OUT_UID}:${OUT_GID}" "/out/${OUT_NAME}.files.tar.gz"
        chmod 600 "/out/${OUT_NAME}.files.tar.gz"
      '
    [ -s "$FILES_TGZ" ] || die "dosya yedegi olusmadi"
    sha256sum "$FILES_TGZ" | awk -v f="$(basename "$FILES_TGZ")" '{print $1"  "f}' > "${FILES_TGZ}.sha256"
    chmod 600 "${FILES_TGZ}" "${FILES_TGZ}.sha256"
    log "dosya yedegi OK: $(basename "$FILES_TGZ") ($(du -h "$FILES_TGZ" | cut -f1))"
  fi
else
  log "dosya yedegi atlandi (volume yok)"
fi

# --- 3) Retention ----------------------------------------------------------
# Kural: son 7 gunun her gunu icin EN YENI yedek + son 4 haftanin pazartesileri.
prune() {
  local today_s age_s dow keep_day_n=0 keep_week_n=0
  local -A keep_day=() keep_week=()
  today_s="$(date -d "$(date +%Y%m%d)" +%s)"

  while IFS= read -r f; do
    local base ts day
    base="$(basename "$f" .dump)"
    ts="${base#showis-}"
    day="${ts%%-*}"
    [ -n "$day" ] || continue
    age_s=$(( ( $(date -d "$day" +%s 2>/dev/null || echo "$today_s") - today_s ) / 86400 ))
    dow="$(date -d "$day" +%u 2>/dev/null || echo 0)"

    # 1) gunluk: son 7 gun, her gun icin en yeni (liste yeniden eskiye sirali)
    if [ "$age_s" -ge "-$((RETENTION_DAILY_DAYS - 1))" ] && [ -z "${keep_day[$day]:-}" ] && [ "$keep_day_n" -lt "$RETENTION_DAILY_DAYS" ]; then
      keep_day[$day]=1; keep_day_n=$((keep_day_n + 1))
      # Pazartesi ise ayni dosya haftalik slotu da doldurur (tek kopya yeter).
      if [ "$dow" = "1" ] && [ -z "${keep_week[$day]:-}" ] && [ "$keep_week_n" -lt "$RETENTION_WEEKLY_WEEKS" ]; then
        keep_week[$day]=1; keep_week_n=$((keep_week_n + 1))
      fi
      continue
    fi
    # 2) haftalik: pazartesi ve son 4 hafta icinde (o gun zaten gunluk olarak korunmadiysa)
    if [ "$dow" = "1" ] && [ "$age_s" -ge "-$((RETENTION_WEEKLY_WEEKS * 7))" ] && [ -z "${keep_week[$day]:-}" ] && [ "$keep_week_n" -lt "$RETENTION_WEEKLY_WEEKS" ]; then
      keep_week[$day]=1; keep_week_n=$((keep_week_n + 1)); continue
    fi
    # 3) digerleri: sil (yalniz bu dizinde, showis-* deseni)
    log "retention: siliniyor $(basename "$f") (+ .sha256 / .files.tar.gz)"
    rm -f "$f" "$f.sha256" "${f%.dump}.files.tar.gz" "${f%.dump}.files.tar.gz.sha256"
  done < <(ls -1 "$BACKUP_DIR"/showis-*.dump 2>/dev/null | sort -r)

  log "retention: gunluk=$keep_day_n haftalik=$keep_week_n"
}
prune

# Yetim .sha256 / .files.tar.gz temizligi (dump'i olmayan)
for extra in "$BACKUP_DIR"/showis-*.dump.sha256 "$BACKUP_DIR"/showis-*.files.tar.gz "$BACKUP_DIR"/showis-*.files.tar.gz.sha256; do
  [ -e "$extra" ] || continue
  case "$extra" in
    *.dump.sha256) dumpfile="${extra%.sha256}" ;;
    *.files.tar.gz.sha256) dumpfile="${extra%.files.tar.gz.sha256}.dump" ;;
    *.files.tar.gz) dumpfile="${extra%.files.tar.gz}.dump" ;;
    *) continue ;;
  esac
  if [ ! -e "$dumpfile" ]; then
    log "yetim dosya siliniyor: $(basename "$extra")"
    rm -f "$extra"
  fi
done

# --- 4) Boyut siniri (2 GB) ----------------------------------------------
total="$(du -sb "$BACKUP_DIR" 2>/dev/null | cut -f1)"
total="${total:-0}"
if [ "$total" -gt "$MAX_TOTAL_BYTES" ]; then
  log "UYARI: toplam boyut $((total / 1024 / 1024))MB > 2GB — en eski yedekler budaniyor"
  while [ "$(du -sb "$BACKUP_DIR" | cut -f1)" -gt "$MAX_TOTAL_BYTES" ]; do
    oldest="$(ls -1 "$BACKUP_DIR"/showis-*.dump 2>/dev/null | sort | head -1)"
    [ -n "$oldest" ] || break
    # En yeni yedegi asla silme
    newest="$(ls -1 "$BACKUP_DIR"/showis-*.dump 2>/dev/null | sort | tail -1)"
    [ "$oldest" = "$newest" ] && break
    log "boyut budama: $(basename "$oldest")"
    rm -f "$oldest" "$oldest.sha256" "${oldest%.dump}.files.tar.gz" "${oldest%.dump}.files.tar.gz.sha256"
  done
fi

log "OZET: $(ls -1 "$BACKUP_DIR"/showis-*.dump 2>/dev/null | wc -l) dump, toplam $(( $(du -sb "$BACKUP_DIR" | cut -f1) / 1024 / 1024 ))MB"
log "tamamlandi: $(basename "$DUMP")"

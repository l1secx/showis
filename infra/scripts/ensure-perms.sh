#!/usr/bin/env bash
# Hassas dizin izinlerini duzeltir (T-314 / SEC-19).
#
#   raporlar ve kanitlar kisisel veri icerir (PDF/JSON, HTTP govdesi).
#   Hedef: klasorler 0700, dosyalar 0600.
#
# Kullanim:
#   bash infra/scripts/ensure-perms.sh              # ikisi de (host + docker volume)
#   bash infra/scripts/ensure-perms.sh --host       # yalniz host ./data
#   bash infra/scripts/ensure-perms.sh --volume     # yalniz docker named volume
#   bash infra/scripts/ensure-perms.sh --dry-run    # yalniz rapor, degisiklik yapma
#
# Notlar:
#   * Idempotent: tekrar calistirmak zarar vermez (yalniz daha gevsek izinleri sikar).
#   * Host tarafi `kerem:kerem`, container volume tarafi 10001:10001 (showis) sahipli;
#     sahiplik DEGISTIRILMEZ, yalniz izinler.
#   * Silme yapmaz (T-314 kisiti).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="${SHOWIS_HOST_DATA_DIR:-$REPO_ROOT/data}"
VOLUME="${SHOWIS_DATA_VOLUME:-infra_report_data}"

DO_HOST=0
DO_VOLUME=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --host) DO_HOST=1 ;;
    --volume) DO_VOLUME=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Bilinmeyen arguman: $arg" >&2; exit 2 ;;
  esac
done
if [ "$DO_HOST" -eq 0 ] && [ "$DO_VOLUME" -eq 0 ]; then
  DO_HOST=1
  DO_VOLUME=1
fi

echo "== ensure-perms (dry_run=$DRY_RUN)"

host_perm() { # <path>
  stat -c '%a %U:%G %n' "$1" 2>/dev/null || echo "- $1"
}

if [ "$DO_HOST" -eq 1 ]; then
  echo
  echo "-- host: $DATA_DIR"
  if [ ! -d "$DATA_DIR" ]; then
    echo "   (dizin yok, atlandi)"
  else
    echo "   ONCE:"
    host_perm "$DATA_DIR"
    find "$DATA_DIR" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort | while read -r d; do
      host_perm "$d"
    done
    sample="$(find "$DATA_DIR" -type f 2>/dev/null | head -1 || true)"
    [ -n "$sample" ] && echo "   ornek dosya: $(host_perm "$sample")"

    if [ "$DRY_RUN" -eq 0 ]; then
      # Dizinler 700 (sahip: kerem). Alt dizinler dahil.
      find "$DATA_DIR" -type d -exec chmod 700 {} +
      # Dosyalar 600.
      find "$DATA_DIR" -type f -exec chmod 600 {} +
      echo "   SONRA:"
      host_perm "$DATA_DIR"
      find "$DATA_DIR" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort | while read -r d; do
        host_perm "$d"
      done
      [ -n "$sample" ] && echo "   ornek dosya: $(host_perm "$sample")"
    else
      echo "   (dry-run: degisiklik yapilmadi)"
    fi
  fi
fi

if [ "$DO_VOLUME" -eq 1 ]; then
  echo
  echo "-- docker volume: $VOLUME (/app/data)"
  if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    echo "   (volume yok, atlandi)"
  else
    # Root olarak calisir; dosya sahipligine dokunmaz, yalniz chmod.
    run() { docker run --rm -v "$VOLUME:/v" "alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc" sh -c "$1"; }
    echo "   ONCE:"
    run 'stat -c "%a %u:%g %n" /v /v/reports /v/evidence 2>/dev/null; f=$(find /v -type f | head -1); [ -n "$f" ] && stat -c "   ornek dosya: %a %u:%g %n" "$f"'
    if [ "$DRY_RUN" -eq 0 ]; then
      run 'find /v -type d -exec chmod 700 {} + ; find /v -type f -exec chmod 600 {} +'
      echo "   SONRA:"
      run 'stat -c "%a %u:%g %n" /v /v/reports /v/evidence 2>/dev/null; f=$(find /v -type f | head -1); [ -n "$f" ] && stat -c "   ornek dosya: %a %u:%g %n" "$f"'
    else
      echo "   (dry-run: degisiklik yapilmadi)"
    fi
  fi
fi

echo
echo "Bitti."

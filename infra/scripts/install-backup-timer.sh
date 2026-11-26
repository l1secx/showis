#!/usr/bin/env bash
# Showis saatlik yedekleme systemd user timer'ini kurar (T-010 / D-004).
#
# Ne yapar:
#   1) infra/systemd/showis-backup.{service,timer} sablonlarini repo kokune gore render eder
#   2) ~/.config/systemd/user/ altina yazar
#   3) systemctl --user daemon-reload + enable --now showis-backup.timer
#
# Kullanim:
#   bash infra/scripts/install-backup-timer.sh            # kur + baslat
#   bash infra/scripts/install-backup-timer.sh --print     # yalniz render edilmis unit'leri bas
#   bash infra/scripts/install-backup-timer.sh --uninstall # timer'i durdur + unit'leri sil
#
# Notlar:
#   * Root GEREKMEZ; systemd user manager kullanilir.
#   * Kullanici `docker` grubunda olmali (backup.sh `docker exec` calistirir).
#   * Kurulumdan sonra elle deneme: `systemctl --user start showis-backup.service`
#     ve `journalctl --user -u showis-backup.service -n 30 --no-pager`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_TEMPLATE="$REPO_ROOT/infra/systemd/showis-backup.service"
TIMER_TEMPLATE="$REPO_ROOT/infra/systemd/showis-backup.timer"

MODE="install"
for arg in "$@"; do
  case "$arg" in
    --print) MODE="print" ;;
    --uninstall) MODE="uninstall" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Bilinmeyen arguman: $arg" >&2; exit 2 ;;
  esac
done

[ -r "$SERVICE_TEMPLATE" ] || { echo "HATA: sablon yok: $SERVICE_TEMPLATE" >&2; exit 1; }
[ -r "$TIMER_TEMPLATE" ] || { echo "HATA: sablon yok: $TIMER_TEMPLATE" >&2; exit 1; }

render() { sed "s|@REPO_ROOT@|$REPO_ROOT|g" "$1"; }

if [ "$MODE" = "print" ]; then
  echo "### $TARGET_DIR/showis-backup.service"
  render "$SERVICE_TEMPLATE"
  echo "### $TARGET_DIR/showis-backup.timer"
  cat "$TIMER_TEMPLATE"
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "HATA: systemctl bulunamadi; systemd user manager olmadan kurulum yapilamaz" >&2
  exit 1
fi

if [ "$MODE" = "uninstall" ]; then
  systemctl --user disable --now showis-backup.timer 2>/dev/null || true
  rm -f "$TARGET_DIR/showis-backup.service" "$TARGET_DIR/showis-backup.timer"
  systemctl --user daemon-reload || true
  echo "Kaldirildi: showis-backup.timer/service"
  exit 0
fi

mkdir -p "$TARGET_DIR"
umask 022
render "$SERVICE_TEMPLATE" > "$TARGET_DIR/showis-backup.service"
cp "$TIMER_TEMPLATE" "$TARGET_DIR/showis-backup.timer"
chmod 644 "$TARGET_DIR/showis-backup.service" "$TARGET_DIR/showis-backup.timer"

systemctl --user daemon-reload
systemctl --user enable --now showis-backup.timer
systemctl --user list-timers showis-backup.timer --no-pager || true

echo
echo "Kuruldu: $TARGET_DIR/showis-backup.{service,timer}"
echo "Elle deneme : systemctl --user start showis-backup.service"
echo "Log         : journalctl --user -u showis-backup.service -n 30 --no-pager"
echo "Not         : kullanici 'docker' grubunda olmali (backup.sh docker exec kullanir)."

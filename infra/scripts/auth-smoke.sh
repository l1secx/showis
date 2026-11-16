#!/usr/bin/env bash
# T-111 / T-113 kimlik dogrulama + tek giris noktasi smoke testi.
#
# Kullanim:
#   bash infra/scripts/auth-smoke.sh [BASE] [LAN_IP]
#     BASE    : varsayilan http://127.0.0.1:8083 (Caddy tek giris noktasi)
#     LAN_IP  : varsayilan `hostname -I | awk '{print $1}'` (8005 kapali olmali)
#
# Kimlik bilgileri (ekrana YAZILMAZ):
#   SHOWIS_CADDY_USER          : varsayilan `showis`
#   SHOWIS_CADDY_PASSWORD      : varsa dogrudan kullanilir
#   SHOWIS_CADDY_PASSWORD_FILE : varsayilan infra/.secrets/caddy-basic-auth.txt
#
# Erisim modeli (T-113): basic auth /health DISINDA her yolda (UI + /api/*);
# /api/* Next proxy'sine gider ve proxy X-API-Token'i sunucu tarafinda ekler.
#
# Notlar: salt okunur (GET + gecersiz govdeli POST -> 422, kayit olusmaz).
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BASE="${1:-http://127.0.0.1:8083}"
LAN_IP="${2:-$(hostname -I 2>/dev/null | awk '{print $1}')}"

CADDY_USER="${SHOWIS_CADDY_USER:-showis}"
CADDY_PASS="${SHOWIS_CADDY_PASSWORD:-}"
if [ -z "$CADDY_PASS" ]; then
  PASS_FILE="${SHOWIS_CADDY_PASSWORD_FILE:-$REPO_ROOT/infra/.secrets/caddy-basic-auth.txt}"
  if [ -r "$PASS_FILE" ]; then
    CADDY_PASS="$(tr -d '\r\n' < "$PASS_FILE")"
  fi
fi

TOKEN="${SHOWIS_API_TOKEN:-}"
fail=0

code() { curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$@"; }
code_auth() { code -u "$CADDY_USER:$CADDY_PASS" "$@"; }

report() { # ad, beklenen, gelen
  if [ "$2" = "$3" ]; then
    printf '  [OK]   %-52s beklenen=%-3s gelen=%s\n' "$1" "$2" "$3"
  else
    printf '  [FAIL] %-52s beklenen=%-3s gelen=%s\n' "$1" "$2" "$3"
    fail=$((fail + 1))
  fi
}

echo "== Showis auth smoke — base=$BASE lan=${LAN_IP:-yok}"
if [ -n "$CADDY_PASS" ]; then
  echo "  basic auth: kullanici=$CADDY_USER (parola yuklendi, ${#CADDY_PASS} karakter)"
else
  echo "  UYARI: basic auth parolasi bulunamadi (env veya dosya yok) — kimlikli adimlar atlanacak"
fi
[ -n "$TOKEN" ] && echo "  api token: tanimli (${#TOKEN} karakter)"

# --- Edge kimlik dogrulamasi: /health MUAF ---------------------------------
report "GET /health (muaf, kimlik yok)" 200 "$(code "$BASE/health")"

# --- Kimliksiz istekler 401 almali ----------------------------------------
report "GET / (kimlik yok)" 401 "$(code "$BASE/")"
report "GET /api/v1/cases (kimlik yok)" 401 "$(code "$BASE/api/v1/cases")"
if [ -n "$CADDY_PASS" ]; then
  report "GET /api/v1/cases (YANLIS parola)" 401 \
    "$(code -u "$CADDY_USER:yanlis-parola" "$BASE/api/v1/cases")"
fi

# --- Kimlikli istekler -----------------------------------------------------
if [ -n "$CADDY_PASS" ]; then
  report "GET / (basic auth)" 200 "$(code_auth "$BASE/")"
  report "GET /api/v1/cases (basic auth -> proxy token ekler)" 200 \
    "$(code_auth "$BASE/api/v1/cases")"
  report "POST /api/v1/cases gecersiz govde (basic auth)" 422 \
    "$(code_auth -X POST "$BASE/api/v1/cases" -H 'Content-Type: application/json' -d '{"gecersiz":true}')"

  # --- SSE (proxy + basic auth) -------------------------------------------
  SCAN_ID="${SCAN_ID:-}"
  if [ -z "$SCAN_ID" ] && command -v docker >/dev/null 2>&1; then
    SCAN_ID="$(docker exec showis-postgres psql -U showis -d showis -tAc \
      'select id from scans order by created_at desc limit 1' 2>/dev/null | tr -d '[:space:]')"
  fi
  if [ -n "$SCAN_ID" ]; then
    sse_hdr="$(mktemp)"
    # NOT: API'nin SSE keepalive araligi 20 sn (event_broadcaster/sse_event_stream).
    # Caddy/Next ilk govde baytina kadar baslik gondermedigi icin --max-time > 21 olmali.
    sse_code="$(curl -s -N -D "$sse_hdr" -o /dev/null -w "%{http_code}" --max-time 24 \
      -u "$CADDY_USER:$CADDY_PASS" -H 'Accept: text/event-stream' \
      "$BASE/api/v1/scans/$SCAN_ID/events" || true)"
    if grep -qi '^content-type: *text/event-stream' "$sse_hdr"; then
      report "GET /api/v1/scans/<id>/events (SSE)" 200 "$sse_code"
    else
      printf '  [FAIL] %-52s content-type text/event-stream yok (HTTP %s)\n' "GET /api/v1/scans/<id>/events (SSE)" "$sse_code"
      fail=$((fail + 1))
    fi
    rm -f "$sse_hdr"
  else
    echo "  [SKIP] SSE: scan id bulunamadi"
  fi
fi

# --- API token katmani (konteyner ici, proxy'yi atlar) --------------------
if command -v docker >/dev/null 2>&1; then
  report "API dogrudan (container ici), tokensiz" 401 \
    "$(docker exec showis-api sh -lc 'curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/cases' 2>/dev/null)"
fi

# --- Eski dogrudan web portu kapatilmis olmali ---------------------------
if [ -n "$LAN_IP" ]; then
  lan8005=$(code "http://$LAN_IP:8005/")
  if [ "$lan8005" = "000" ]; then
    printf '  [OK]   %-52s baglanti reddedildi (000)\n' "LAN :8005 (kapatilmis olmali)"
  else
    printf '  [FAIL] %-52s hala acik (HTTP %s)\n' "LAN :8005 (kapatilmis olmali)" "$lan8005"
    fail=$((fail + 1))
  fi
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "SONUC: tum kontroller gecti"
  exit 0
fi
echo "SONUC: $fail kontrol basarisiz"
exit 1

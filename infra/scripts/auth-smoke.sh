#!/usr/bin/env bash
# T-111 / T-113 kimlik dogrulama + tek giris noktasi smoke testi.
#
# Kullanim:
#   bash infra/scripts/auth-smoke.sh [BASE] [LAN_IP]
#     BASE    : varsayilan http://127.0.0.1:8083 (Caddy tek giris noktasi)
#     LAN_IP  : varsayilan `hostname -I | awk '{print $1}'` (8005 kapali olmali)
#
# Kimlik bilgileri (ekrana YAZILMAZ):
#   SHOWIS_CADDY_USER          : varsayilan `analyst` (infra/.env ile ayni)
#   SHOWIS_CADDY_PASSWORD      : varsa dogrudan kullanilir
#   SHOWIS_CADDY_PASSWORD_FILE : varsayilan infra/.secrets/caddy-basic-auth.txt
#
# Parola curl'e argv ile DEGIL, 0600 gecici `--config` dosyasiyla verilir;
# `ps`/argv'de gorunmez (T-010/D-009).
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

CADDY_USER="${SHOWIS_CADDY_USER:-analyst}"
CADDY_PASS="${SHOWIS_CADDY_PASSWORD:-}"
if [ -z "$CADDY_PASS" ]; then
  PASS_FILE="${SHOWIS_CADDY_PASSWORD_FILE:-$REPO_ROOT/infra/.secrets/caddy-basic-auth.txt}"
  if [ -r "$PASS_FILE" ]; then
    CADDY_PASS="$(tr -d '\r\n' < "$PASS_FILE")"
  fi
fi

TOKEN="${SHOWIS_API_TOKEN:-}"
fail=0

# curl --config icin tirnak/ters-bolu kacisi (parola argv'ye girmesin).
conf_escape() { printf '%s' "$1" | sed 's/[\\"]/\\&/g'; }

CREDS_DIR=""
CONF_BASIC=()
CONF_WRONG=()
cleanup() { if [ -n "$CREDS_DIR" ]; then rm -rf "$CREDS_DIR"; fi; }
trap cleanup EXIT INT TERM

if [ -n "$CADDY_PASS" ]; then
  old_umask="$(umask)"; umask 077
  CREDS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/showis-auth-smoke.XXXXXX")"
  umask "$old_umask"
  printf 'user = "%s:%s"\n' \
    "$(conf_escape "$CADDY_USER")" "$(conf_escape "$CADDY_PASS")" \
    > "$CREDS_DIR/basic.conf"
  printf 'user = "%s:%s"\n' \
    "$(conf_escape "$CADDY_USER")" "$(conf_escape "yanlis-parola")" \
    > "$CREDS_DIR/wrong.conf"
  CONF_BASIC=(--config "$CREDS_DIR/basic.conf")
  CONF_WRONG=(--config "$CREDS_DIR/wrong.conf")
fi

code() { curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$@"; }
code_auth() { code "${CONF_BASIC[@]}" "$@"; }

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
    "$(code "${CONF_WRONG[@]}" "$BASE/api/v1/cases")"
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
      "${CONF_BASIC[@]}" -H 'Accept: text/event-stream' \
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

# --- T-319: guvenlik basliklari ------------------------------------------
check_header() { # <ad> <url> <baslik> <beklenen-deger|-> <auth?>
  local name="$1" url="$2" hdr="$3" want="$4" auth="${5:-yes}"
  local out val
  if [ "$auth" = "yes" ]; then out="$(curl -sI --max-time 10 "${CONF_BASIC[@]}" "$url")"
  else out="$(curl -sI --max-time 10 "$url")"; fi
  if [ "$want" = "-" ]; then
    if printf '%s' "$out" | grep -qi "^${hdr}:"; then
      printf '  [FAIL] %-52s %s basligi OLMAMALI\n' "$name" "$hdr"; fail=$((fail + 1))
    else
      printf '  [OK]   %-52s %s yok\n' "$name" "$hdr"
    fi
    return
  fi
  val="$(printf '%s' "$out" | grep -i "^${hdr}:" | head -1 | cut -d: -f2- | sed 's/^ //; s/\r$//')"
  case "$val" in
    *"$want"*) printf '  [OK]   %-52s %s: %s\n' "$name" "$hdr" "$val" ;;
    *) printf '  [FAIL] %-52s %s beklenen "%s", gelen "%s"\n' "$name" "$hdr" "$want" "$val"; fail=$((fail + 1)) ;;
  esac
}

if [ -n "$CADDY_PASS" ]; then
  echo
  echo "-- guvenlik basliklari (T-319)"
  check_header "UI: X-Content-Type-Options" "$BASE/" "X-Content-Type-Options" "nosniff"
  check_header "UI: X-Frame-Options" "$BASE/" "X-Frame-Options" "DENY"
  check_header "UI: Referrer-Policy" "$BASE/" "Referrer-Policy" "no-referrer"
  check_header "UI: Permissions-Policy" "$BASE/" "Permissions-Policy" "geolocation=()"
  check_header "UI: Content-Security-Policy" "$BASE/" "Content-Security-Policy" "default-src 'self'"
  check_header "UI: Server gizli" "$BASE/" "Server" "-"
  check_header "API: X-Content-Type-Options" "$BASE/api/v1/cases" "X-Content-Type-Options" "nosniff"
  check_header "API: X-Frame-Options" "$BASE/api/v1/cases" "X-Frame-Options" "DENY"
  check_header "API: CSP yok (JSON)" "$BASE/api/v1/cases" "Content-Security-Policy" "-"
  check_header "API: Server gizli" "$BASE/api/v1/cases" "Server" "-"

  # --- govde siniri (T-319): 11MB > 10MB -> 413 ---------------------------
  big="$(mktemp)"; head -c 11500000 /dev/zero | tr '\0' 'a' > "$big"
  report "POST /api/v1/cases 11MB govde -> 413" 413 \
    "$(code_auth -X POST -H 'Content-Type: application/json' --data-binary @"$big" "$BASE/api/v1/cases")"
  rm -f "$big"
fi

# --- Host dogrulamasi (T-319, env-gated) ---------------------------------
ALLOWED_HOSTS="${SHOWIS_ALLOWED_HOSTS:-}"
if [ -z "$ALLOWED_HOSTS" ] && command -v docker >/dev/null 2>&1; then
  ALLOWED_HOSTS="$(docker inspect showis-caddy --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | grep '^SHOWIS_ALLOWED_HOSTS=' | cut -d= -f2- || true)"
fi
if [ -n "$ALLOWED_HOSTS" ] && [ -n "$CADDY_PASS" ]; then
  report "Host: evil.example -> 421" 421 \
    "$(code_auth -H 'Host: evil.example' "$BASE/")"
else
  echo "  [SKIP] Host allowlist devre disi (SHOWIS_ALLOWED_HOSTS bos) — 421 kontrolu atlandi"
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

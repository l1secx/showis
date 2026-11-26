#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# e2e_acceptance.sh — Showis canli kullanici akisi kabul testi (T-323)
#
# Tek komutla, canli sistemde uctan uca akisi dogrular:
#   vaka olustur -> tahmin -> tarama (O1) -> idempotans/409 -> bulgular ->
#   bulgu dogrulama -> rapor -> diske yazim/izin -> export -> silme
#
# Kullanim:
#   export SHOWIS_API_TOKEN=...                                       # API (T-108)
#   export SHOWIS_BASIC_AUTH_USER=... SHOWIS_BASIC_AUTH_PASSWORD=...   # edge (T-113)
#   bash scripts/e2e_acceptance.sh
#   bash scripts/e2e_acceptance.sh --keep          # vakayi silme (inceleme)
#   bash scripts/e2e_acceptance.sh --pre-deploy    # deploy oncesi: yeni kabiliyetler BEKLENIYOR
#   bash scripts/e2e_acceptance.sh --strict        # BEKLENIYOR'lar da FAIL sayilsin
#
# Adimlar (T-330 ile genisletildi):
#   1 health · 2 kimlik katmani · 2c eski E2E temizligi · 3 vaka · 4 tahmin
#   (+4b/4c O2 tutarlilik & sitecheck kapsami) · 5 tarama (409 → 1 retry) ·
#   6 idempotans (+6c iptal terminali)
#   7 bulgular (+7b authenticity) · 8 bulgu dogrulama · 9 rapor (+9b disk/izin)
#   10 export (+10b-10f vaka listesi: zarf, arama, siralama, limit, legacy)
#   11 grafik (+11b kenar bilgisi) · 12 JSON rapor bolumleri
#   13 HTML rapor + CSP · 14 min_authenticity filtresi · 15 status filtresi
#   16 /healthz · 17 /metrics · 18 guvenlik basliklari · 19 edge backfill (dry-run)
#   20 vaka silme + kalinti kontrolu
#
# Durumlar: PASS / FAIL / BEKLENIYOR (deploy bekleyen kabiliyet) / UYARI (bilgi,
# cikis kodunu etkilemez) / ATLANDI. `--strict` iken hicbir adim BEKLENIYOR kalmaz.
#
# Kimlik: `--basic-auth user:parola` > SHOWIS_BASIC_AUTH_* > `--base-url` userinfo.
# Kimlik bilgileri curl'e 0600 gecici `--config` dosyasiyla verilir; argv/ps'te
# gorunmez. `Authorization` cakismasi: basic auth varken token YALNIZ
# `X-API-Token` basligiyla gonderilir (Bearer Basic'i ezerdi — T-305).
#
# Cikis kodlari: 0 hepsi PASS (veya strict degilken yalniz BEKLENIYOR),
#                1 FAIL var, 2 kullanim hatasi, 3 on kosul (health/auth) basarisiz
# ---------------------------------------------------------------------------
set -euo pipefail
set +x   # kimlik bilgileri trace'e dusmesin

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BASE_URL="${SHOWIS_BASE_URL:-http://127.0.0.1:8083}"
TOKEN="${SHOWIS_API_TOKEN:-}"
BASIC_ARG=""
BASIC_USER="${SHOWIS_BASIC_AUTH_USER:-}"
BASIC_PASSWORD="${SHOWIS_BASIC_AUTH_PASSWORD:-}"
URL_USERINFO=""
KEEP=0
STRICT=0
PRE_DEPLOY=0
SCAN_TIMEOUT=600     # O1 poll ust siniri (sn)
POLL_INTERVAL=5
API_CONTAINER="${SHOWIS_API_CONTAINER:-showis-api}"
REPORT_DIR_IN_CONTAINER="${SHOWIS_REPORT_DIR_IN_CONTAINER:-/app/data/reports}"
CURL_MAX_TIME=60

usage() {
  cat <<'USAGE'
e2e_acceptance.sh — Showis canli uctan uca kabul testi

Kullanim:
  scripts/e2e_acceptance.sh [secenekler]

Secenekler:
  --base-url URL          API tabani (varsayilan: http://127.0.0.1:8083)
  --token TOKEN           API token (varsayilan: $SHOWIS_API_TOKEN)
  --basic-auth USER:PASS  Edge (Caddy) HTTP Basic kimligi
  --keep                  Vakayi silmeden birak (inceleme icin)
  --pre-deploy            Deploy oncesi kosu: eksik yeni kabiliyetler BEKLENIYOR
  --strict                BEKLENIYOR adimlarini da FAIL say (deploy sonrasi)
  --timeout SEC           Tarama poll ust siniri (varsayilan: 600)
  --interval SEC          Poll araligi (varsayilan: 5)
  -h, --help              Bu yardimi basar

Ortam:
  SHOWIS_API_TOKEN, SHOWIS_BASIC_AUTH_USER, SHOWIS_BASIC_AUTH_PASSWORD,
  SHOWIS_BASE_URL, SHOWIS_API_CONTAINER (varsayilan: showis-api)

Ornek:
  export SHOWIS_API_TOKEN=***
  export SHOWIS_BASIC_AUTH_USER=showis
  read -rs SHOWIS_BASIC_AUTH_PASSWORD && export SHOWIS_BASIC_AUTH_PASSWORD
  bash scripts/e2e_acceptance.sh --keep
USAGE
}

die() { echo "HATA: $*" >&2; exit 1; }
usage_die() { echo "HATA: $*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="${2:?--base-url deger ister}"; shift 2 ;;
    --token) TOKEN="${2:?--token deger ister}"; shift 2 ;;
    --basic-auth) BASIC_ARG="${2:?--basic-auth user:parola ister}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --strict) STRICT=1; shift ;;
    --pre-deploy) PRE_DEPLOY=1; shift ;;
    --timeout) SCAN_TIMEOUT="${2:?--timeout deger ister}"; shift 2 ;;
    --interval) POLL_INTERVAL="${2:?--interval deger ister}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Bilinmeyen arguman: $1" >&2; usage >&2; exit 2 ;;
  esac
done

BASE_URL="${BASE_URL%/}"
if [[ "$STRICT" -eq 1 && "$PRE_DEPLOY" -eq 1 ]]; then
  usage_die "--strict ve --pre-deploy birlikte kullanilamaz (strict: hicbir adim BEKLENIYOR kalmaz)"
fi
[[ "$SCAN_TIMEOUT" =~ ^[0-9]+$ ]] || usage_die "--timeout sayi olmali"
[[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || usage_die "--interval sayi olmali"

command -v curl >/dev/null 2>&1 || die "curl gerekli"
command -v jq >/dev/null 2>&1 || die "jq gerekli"

# --- kimlik ----------------------------------------------------------------
if [[ "$BASE_URL" =~ ^([A-Za-z][A-Za-z0-9+.-]*)://([^/@]+)@(.+)$ ]]; then
  URL_USERINFO="${BASH_REMATCH[2]}"
  BASE_URL="${BASH_REMATCH[1]}://${BASH_REMATCH[3]}"
fi

urldecode() {
  local value="${1//+/ }"
  printf '%b' "${value//%/\\x}"
}

if [[ -n "$BASIC_ARG" ]]; then
  [[ "$BASIC_ARG" == *:* ]] || usage_die "--basic-auth user:parola formatinda olmali"
  BASIC_USER="${BASIC_ARG%%:*}"
  BASIC_PASSWORD="${BASIC_ARG#*:}"
elif [[ -n "$BASIC_USER" || -n "$BASIC_PASSWORD" ]]; then
  [[ -n "$BASIC_USER" && -n "$BASIC_PASSWORD" ]] \
    || die "SHOWIS_BASIC_AUTH_USER ve SHOWIS_BASIC_AUTH_PASSWORD birlikte verilmeli"
elif [[ -n "$URL_USERINFO" ]]; then
  BASIC_USER="$(urldecode "${URL_USERINFO%%:*}")"
  [[ "$URL_USERINFO" == *:* ]] && BASIC_PASSWORD="$(urldecode "${URL_USERINFO#*:}")"
fi
BASIC_AUTH_ENABLED=0
if [[ -n "$BASIC_USER" ]]; then
  [[ -n "$BASIC_PASSWORD" ]] || die "edge basic auth: parola bos"
  BASIC_AUTH_ENABLED=1
fi

CLEANUP_PATHS=()
CONF_AUTH=()        # token + basic (tam yetki)
CONF_BASIC=()       # yalniz basic (proxy token'i ekler)
#: T-378: script hangi noktada biterse bitsin kendi olusturdugu vakayi birakma.
CASE_ID=""
CASE_DELETED=0
cleanup() {
  local path
  for path in ${CLEANUP_PATHS[@]+"${CLEANUP_PATHS[@]}"}; do
    [[ -n "$path" ]] && rm -rf "$path"
  done
  if [[ -n "$CASE_ID" && "$CASE_DELETED" -eq 0 && "$KEEP" -eq 0 ]]; then
    http_request auth DELETE "/api/v1/cases/${CASE_ID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

CREDS_DIR=""
if [[ "$BASIC_AUTH_ENABLED" -eq 1 || -n "$TOKEN" ]]; then
  old_umask="$(umask)"; umask 077
  CREDS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/showis-e2e.XXXXXX")"
  umask "$old_umask"
  CLEANUP_PATHS+=("$CREDS_DIR")
  if [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
    basic_file="$CREDS_DIR/basic.conf"
    printf 'user = "%s:%s"\n' \
      "$(printf '%s' "$BASIC_USER" | sed 's/[\\"]/\\&/g')" \
      "$(printf '%s' "$BASIC_PASSWORD" | sed 's/[\\"]/\\&/g')" > "$basic_file"
    CONF_BASIC=(--config "$basic_file")
    if [[ -n "$TOKEN" ]]; then
      auth_file="$CREDS_DIR/auth.conf"
      printf 'user = "%s:%s"\nheader = "X-API-Token: %s"\n' \
        "$(printf '%s' "$BASIC_USER" | sed 's/[\\"]/\\&/g')" \
        "$(printf '%s' "$BASIC_PASSWORD" | sed 's/[\\"]/\\&/g')" \
        "$(printf '%s' "$TOKEN" | sed 's/[\\"]/\\&/g')" > "$auth_file"
      CONF_AUTH=(--config "$auth_file")
    else
      CONF_AUTH=("${CONF_BASIC[@]}")
    fi
  else
    auth_file="$CREDS_DIR/auth.conf"
    printf 'header = "X-API-Token: %s"\nheader = "Authorization: Bearer %s"\n' \
      "$(printf '%s' "$TOKEN" | sed 's/[\\"]/\\&/g')" \
      "$(printf '%s' "$TOKEN" | sed 's/[\\"]/\\&/g')" > "$auth_file"
    CONF_AUTH=(--config "$auth_file")
  fi
fi

# --- renkler ---------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
  C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_CYAN=""; C_DIM=""; C_BOLD=""
fi

# --- adim muhasebesi -------------------------------------------------------
PASS_N=0; FAIL_N=0; PENDING_N=0; SKIP_N=0; WARN_N=0
RESULTS=()

record() {  # record <no> <ad> <durum> <ayrinti>
  local no="$1" name="$2" state="$3" detail="${4:-}"
  RESULTS+=("$no|$name|$state|$detail")
  case "$state" in
    PASS) PASS_N=$((PASS_N + 1)) ;;
    FAIL) FAIL_N=$((FAIL_N + 1)) ;;
    BEKLENIYOR) PENDING_N=$((PENDING_N + 1)) ;;
    ATLANDI) SKIP_N=$((SKIP_N + 1)) ;;
    UYARI) WARN_N=$((WARN_N + 1)) ;;
  esac
}

step_pass() { printf '%s[PASS]%s %-2s %-46s %s\n' "$C_GREEN" "$C_RESET" "$1" "$2" "${3:-}"; record "$1" "$2" PASS "${3:-}"; }
step_fail() { printf '%s[FAIL]%s %-2s %-46s %s\n' "$C_RED" "$C_RESET" "$1" "$2" "${3:-}" >&2; record "$1" "$2" FAIL "${3:-}"; }
step_pending() {
  if [[ "$STRICT" -eq 1 ]]; then
    printf '%s[FAIL]%s %-2s %-46s %s (strict)\n' "$C_RED" "$C_RESET" "$1" "$2" "${3:-}" >&2
    record "$1" "$2" FAIL "${3:-} (strict)"
  else
    printf '%s[BEKLENIYOR]%s %-2s %-40s %s\n' "$C_YELLOW" "$C_RESET" "$1" "$2" "${3:-}"
    record "$1" "$2" BEKLENIYOR "${3:-}"
  fi
}
step_skip() { printf '%s[ATLANDI]%s %-2s %-44s %s\n' "$C_DIM" "$C_RESET" "$1" "$2" "${3:-}"; record "$1" "$2" ATLANDI "${3:-}"; }
# UYARI: bilgi niteliginde (cikis kodunu etkilemez; --strict'te de PASS/FAIL'e donusmez)
step_warn() { printf '%s[UYARI]%s %-4s %-44s %s\n' "$C_CYAN" "$C_RESET" "$1" "$2" "${3:-}"; record "$1" "$2" UYARI "${3:-}"; }

# capability <no> <ad> <var:1|0> <var-detay> <eksik-detay>
# Deploy ile gelen yeni kabiliyetler: eksikse --pre-deploy'da BEKLENIYOR,
# aksi halde (deploy sonrasi kabul kosusu) FAIL.
capability() {
  local no="$1" name="$2" present="$3" ok_detail="${4:-}" missing_detail="${5:-kabiliyet yok}"
  if [[ "$present" == "1" ]]; then
    step_pass "$no" "$name" "$ok_detail"
  elif [[ "$PRE_DEPLOY" -eq 1 ]]; then
    step_pending "$no" "$name" "${missing_detail} (--pre-deploy)"
  else
    step_fail "$no" "$name" "$missing_detail"
  fi
}

# --- HTTP yardimcilari -----------------------------------------------------
HTTP_CODE=""; HTTP_BODY=""

# http_request <CONF|-> <METHOD> <PATH> [BODY]
# Donus: 0 = yanit alindi (4xx/5xx dahil), 1 = baglanti hatasi
http_request() {
  local conf="$1" method="$2" path="$3" body="${4:-}" tmp code
  local args=(-sS --max-time "$CURL_MAX_TIME" -X "$method" -w '%{http_code}')
  tmp="$(mktemp)"
  args+=(-o "$tmp")
  if [[ "$conf" == "-" ]]; then
    : # kimliksiz
  elif [[ "$conf" == "auth" ]]; then
    [[ ${#CONF_AUTH[@]} -gt 0 ]] && args+=("${CONF_AUTH[@]}")
  elif [[ "$conf" == "basic" ]]; then
    [[ ${#CONF_BASIC[@]} -gt 0 ]] && args+=("${CONF_BASIC[@]}")
  fi
  if [[ -n "$body" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary "$body")
  fi
  if ! code="$(curl "${args[@]}" "${BASE_URL}${path}")"; then
    rm -f "$tmp"
    HTTP_CODE="000"; HTTP_BODY=""
    return 1
  fi
  HTTP_CODE="$code"
  HTTP_BODY="$(cat "$tmp")"
  rm -f "$tmp"
  return 0
}

json_field() { printf '%s' "$HTTP_BODY" | jq -r "$1" 2>/dev/null || true; }

# Dagitim iki rapor yerlesimi kullanabilir (final deploy oncesi/sonrasi):
#   yeni: <REPORT_DIR>/<case_id>/<hex>.json     eski: <REPORT_DIR>/<case_id>_<hex>.json
# Bu yuzden her ikisi de taranir; cikti: "<izin> <yol>" satirlari.
REPORT_SH_SCAN='cd "$1" 2>/dev/null || exit 0
for p in "$2" "$2"_*; do
  [ -e "$p" ] || continue
  if [ -d "$p" ]; then stat -c "%a %n" "$p"/* 2>/dev/null; else stat -c "%a %n" "$p"; fi
done'

report_artifacts() {
  if command -v docker >/dev/null 2>&1 && docker inspect "$API_CONTAINER" >/dev/null 2>&1; then
    docker exec "$API_CONTAINER" sh -c "$REPORT_SH_SCAN" sh \
      "$REPORT_DIR_IN_CONTAINER" "$CASE_ID" 2>/dev/null || true
  else
    sh -c "$REPORT_SH_SCAN" sh "${REPO_ROOT}/data/reports" "$CASE_ID" 2>/dev/null || true
  fi
}

report_location() {
  if command -v docker >/dev/null 2>&1 && docker inspect "$API_CONTAINER" >/dev/null 2>&1; then
    echo "konteyner ${REPORT_DIR_IN_CONTAINER}"
  else
    echo "host data/reports"
  fi
}

echo "${C_BOLD}== Showis E2E kabul testi (T-323) ==${C_RESET}"
echo "taban url : ${BASE_URL}"
echo "kimlik    : $(
  if [[ "$BASIC_AUTH_ENABLED" -eq 1 && -n "$TOKEN" ]]; then echo "edge basic ('${BASIC_USER}', parola ***) + X-API-Token";
  elif [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then echo "edge basic ('${BASIC_USER}', parola ***)";
  elif [[ -n "$TOKEN" ]]; then echo "X-API-Token + Bearer (edge basic yok)";
  else echo "YOK"; fi
)"
echo

# ---------------------------------------------------------------- 1) health
if http_request - GET /health; then
  health_status="$(json_field '.status')"
  if [[ "$HTTP_CODE" == "200" && "$health_status" == "ok" ]]; then
    step_pass 1 "/health (kimliksiz)" "200 ok"
  else
    step_fail 1 "/health (kimliksiz)" "HTTP $HTTP_CODE status=$health_status"
  fi
else
  step_fail 1 "/health (kimliksiz)" "baglanti yok: $BASE_URL"
  echo "${C_RED}API'ye ulasilamiyor — kalan adimlar atlandi.${C_RESET}" >&2
  FAIL_N=$((FAIL_N + 1))
  printf '\n%sSONUÇ: KALDI%s\n' "$C_RED" "$C_RESET"
  exit 3
fi

# ------------------------------------------------- 2) kimlik dogrulama katmani
if http_request - GET /api/v1/cases; then
  if [[ "$HTTP_CODE" == "401" ]]; then
    step_pass 2 "kimliksiz /cases -> 401" "401"
  elif [[ "$HTTP_CODE" == "200" ]]; then
    step_pending 2 "kimliksiz /cases -> 401" "200 (auth kapali olabilir: SHOWIS_AUTH_DISABLED?)"
  else
    step_fail 2 "kimliksiz /cases -> 401" "HTTP $HTTP_CODE"
  fi
else
  step_fail 2 "kimliksiz /cases -> 401" "baglanti hatasi"
fi

if [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
  if http_request basic GET /api/v1/cases; then
    if [[ "$HTTP_CODE" == "200" ]]; then
      step_pass 2b "yalniz basic auth /cases -> 200" "200 (proxy token ekliyor)"
    else
      step_fail 2b "yalniz basic auth /cases -> 200" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 120)"
    fi
  else
    step_fail 2b "yalniz basic auth /cases -> 200" "baglanti hatasi"
  fi
else
  step_skip 2b "yalniz basic auth /cases -> 200" "basic auth verilmedi"
fi

# --------------------------- 2c) eski E2E vakalarini temizle (T-378 / DEC-004)
# Art arda kosularda onceki kosunun (yarim kalmis) vakasi/aktif taramasi
# yenisiyle cakismasin. SAHIPLIK: yalniz bu script'in kendi olusturdugu vakalar
# silinir — birebir ad "E2E kabul testi" VE notes alaninda script isareti.
# Amacinda/metninde "E2E" gecen kullanici vakalari SILINMEZ (DEC-004).
E2E_CASE_NAME="E2E kabul testi"
E2E_MARKER="script=e2e_acceptance.sh"
cleanup_deadline=$(( $(date +%s) + 20 ))
stale_ids=""
if http_request auth GET "/api/v1/cases?q=E2E%20kabul&limit=200"; then
  if [[ "$HTTP_CODE" == "200" ]]; then
    stale_ids="$(printf '%s' "$HTTP_BODY" | jq -r \
      --arg name "$E2E_CASE_NAME" --arg marker "$E2E_MARKER" '
      (if type == "object" then (.items // []) else . end)
      | [ .[] | select((.name // "") == $name)
              | select(((.notes // "") | contains($marker))) | .id ] | .[]' 2>/dev/null || true)"
  fi
fi
stale_count=0
stale_deleted=0
while IFS= read -r old_case; do
  [[ -n "$old_case" ]] || continue
  stale_count=$((stale_count + 1))
  if [[ "$(date +%s)" -ge "$cleanup_deadline" ]]; then continue; fi
  if http_request auth DELETE "/api/v1/cases/${old_case}"; then
    [[ "$HTTP_CODE" == "204" ]] && stale_deleted=$((stale_deleted + 1))
  fi
done <<<"$stale_ids"
if [[ "$stale_count" -eq 0 ]]; then
  step_pass 2c "eski E2E temizligi" "kalinti yok"
else
  step_pass 2c "eski E2E temizligi" "${stale_deleted}/${stale_count} yarim vaka silindi"
fi

# ------------------------------------------------------------- 3) vaka acma
case_payload="$(jq -n \
  --arg marker "$E2E_MARKER" \
  --arg run "run=$(date -u +%Y%m%dT%H%M%SZ)-$$" '{
  name: "E2E kabul testi",
  purpose: "E2E — otomatik kabul testi (T-323); hedef anonim",
  legal_basis: "self_check",
  notes: ($marker + "; " + $run + "; hedef anonim"),
  retention_days: 7,
  target: {first_name: "E2E", last_name: "Test", hints: {}},
  seeds: []
}')"
if http_request auth POST /api/v1/cases "$case_payload"; then
  CASE_ID="$(json_field '.id')"
  if [[ "$HTTP_CODE" == "201" && "$CASE_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
    step_pass 3 "vaka olustur (201)" "id=${CASE_ID:0:8}…"
  else
    step_fail 3 "vaka olustur (201)" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 160)"
    CASE_ID=""
  fi
else
  step_fail 3 "vaka olustur (201)" "baglanti hatasi"
fi

if [[ -z "$CASE_ID" ]]; then
  for n in 4 4b 4c 5 6a 6b 6c 7 7b 7c 8 9 9b 10 10b 10c 10d 10e 10f 10g 10h 10i \
           11 11b 12 13 14 14b 14c 15 15b 19; do
    step_skip "$n" "(vaka yok)" "3. adim basarisiz"
  done
  for n in 16 17 17b 18 18b; do step_skip "$n" "(vaka yok)" "vaka olusturulamadi — genel kontroller kosulmadi"; done
else

# ------------------------------------------------------------- 4) tahmin
if http_request auth GET "/api/v1/cases/${CASE_ID}/scan/estimate?mode=O1"; then
  est_seconds="$(json_field '.seconds')"
  est_tasks="$(json_field '.tasks')"
  est_conf="$(json_field '.confidence')"
  if [[ "$HTTP_CODE" == "200" ]] && [[ "$est_seconds" =~ ^[0-9]+$ ]] && (( est_seconds > 0 )) \
     && [[ "$est_tasks" =~ ^[0-9]+$ ]] && (( est_tasks > 0 )) && [[ -n "$est_conf" && "$est_conf" != "null" ]]; then
    step_pass 4 "O1 tahmini" "${est_seconds} sn / ${est_tasks} gorev (${est_conf})"
  else
    step_fail 4 "O1 tahmini" "HTTP $HTTP_CODE seconds=$est_seconds tasks=$est_tasks confidence=$est_conf"
  fi
else
  step_fail 4 "O1 tahmini" "baglanti hatasi"
fi

# ------------------------------- 4b/4c) O2 tahmin tutarliligi + sitecheck
# T-361/6: kesin site sayisi beklenmez; iki sey dogrulanir:
#   (a) tutarlilik: `requests >= tasks` (site-check istekleri tahmine girer),
#   (b) T-357 kapsami: sitecheck adimindaki site sayisi 48'in uzerinde mi.
if http_request auth GET "/api/v1/cases/${CASE_ID}/scan/estimate?mode=O2"; then
  if [[ "$HTTP_CODE" != "200" ]]; then
    step_fail 4b "O2 tahmin tutarliligi" "HTTP $HTTP_CODE"
    step_skip 4c "sitecheck kapsami (T-357)" "tahmin okunamadi"
  else
    o2_tasks="$(json_field '.tasks')"
    o2_requests="$(json_field '.requests')"
    if [[ "$o2_tasks" =~ ^[0-9]+$ && "$o2_requests" =~ ^[0-9]+$ ]] \
       && (( o2_tasks > 0 )) && (( o2_requests >= o2_tasks )); then
      step_pass 4b "O2 tahmin tutarliligi (requests>=tasks)" \
        "requests=${o2_requests} >= tasks=${o2_tasks}"
    else
      step_fail 4b "O2 tahmin tutarliligi (requests>=tasks)" \
        "requests=${o2_requests:-?} tasks=${o2_tasks:-?}"
    fi
    site_count="$(printf '%s' "$HTTP_BODY" | jq -r '
      [ .steps[]? | select(.key == "sitecheck")
        | (.label | try capture("× (?<n>[0-9]+) site").n catch empty) ] | .[0] // empty' 2>/dev/null || true)"
    if [[ "$site_count" =~ ^[0-9]+$ ]] && (( site_count > 48 )); then
      capability 4c "sitecheck kapsami (T-357)" 1 "${site_count} site (>48)" ""
    else
      capability 4c "sitecheck kapsami (T-357)" 0 "" \
        "site sayisi ${site_count:-bulunamadi} (T-357 oncesi 48)"
    fi
  fi
else
  step_fail 4b "O2 tahmin tutarliligi (requests>=tasks)" "baglanti hatasi"
  step_skip 4c "sitecheck kapsami (T-357)" "tahmin okunamadi"
fi

# --------------------------------------------------- 5) tarama + 6) idempotans
SCAN_ID=""
scan_finished=0
# T-378: onceki kosudan kalan aktif tarama 409 verebilir → 3 sn backoff ile 1 retry.
start_detail=""
for attempt in 1 2; do
  if ! http_request auth POST "/api/v1/cases/${CASE_ID}/scan" '{"mode":"O1"}'; then
    step_fail 5a "O1 taramasi baslatildi" "baglanti hatasi"
    SCAN_ID=""
    break
  fi
  if [[ "$HTTP_CODE" == "409" && "$attempt" -eq 1 ]]; then
    start_detail="409 → 3 sn backoff sonrasi tekrar"
    sleep 3
    continue
  fi
  SCAN_ID="$(json_field '.scan_id')"
  if [[ ( "$HTTP_CODE" == "200" || "$HTTP_CODE" == "202" ) && -n "$SCAN_ID" && "$SCAN_ID" != "null" ]]; then
    step_pass 5a "O1 taramasi baslatildi" "scan=${SCAN_ID:0:8}… HTTP $HTTP_CODE${start_detail:+ ($start_detail)}"
  elif [[ "$HTTP_CODE" == "409" ]]; then
    step_fail 5a "O1 taramasi baslatildi" "409 tekrar (gercek cakisma): $(printf '%s' "$HTTP_BODY" | head -c 140)"
    SCAN_ID=""
  else
    step_fail 5a "O1 taramasi baslatildi" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 180)"
    SCAN_ID=""
  fi
  break
done

# 6a) aktif tarama varken ikinci POST -> 409 (tarama henuz bitmedi)
if [[ -n "$SCAN_ID" ]]; then
  if http_request auth POST "/api/v1/cases/${CASE_ID}/scan" '{"mode":"O1"}'; then
    if [[ "$HTTP_CODE" == "409" ]]; then
      step_pass 6a "aktifken 2. POST /scan -> 409" "409 (tek aktif tarama)"
    else
      step_fail 6a "aktifken 2. POST /scan -> 409" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 160)"
    fi
  else
    step_fail 6a "aktifken 2. POST /scan -> 409" "baglanti hatasi"
  fi

  # 5b) terminal duruma kadar poll
  deadline=$(( $(date +%s) + SCAN_TIMEOUT ))
  SCAN_STATUS="queued"; SCAN_STATS='{}'
  while :; do
    if http_request auth GET "/api/v1/scans/${SCAN_ID}"; then
      SCAN_STATUS="$(json_field '.status')"
      SCAN_STATS="$(printf '%s' "$HTTP_BODY" | jq -c '.stats // {}' 2>/dev/null || echo '{}')"
      case "$SCAN_STATUS" in completed|failed|cancelled) break ;; esac
    fi
    if [[ "$(date +%s)" -ge "$deadline" ]]; then break; fi
    sleep "$POLL_INTERVAL"
  done
  executed="$(printf '%s' "$SCAN_STATS" | jq -r '.executed_tasks // 0' 2>/dev/null || echo 0)"
  requests="$(printf '%s' "$SCAN_STATS" | jq -r '.http_requests // 0' 2>/dev/null || echo 0)"
  if [[ "$SCAN_STATUS" == "completed" ]] && [[ "$executed" =~ ^[0-9]+$ ]] && (( executed > 0 )); then
    step_pass 5b "tarama tamamlandi" "completed, ${executed} gorev / ${requests} istek"
    scan_finished=1
  else
    step_fail 5b "tarama tamamlandi" "status=$SCAN_STATUS executed=$executed (sinir ${SCAN_TIMEOUT}s)"
  fi

  # 6b) bitmisken ikinci baslatma: davranis raporlanir, tarama hemen iptal edilir
  if [[ "$scan_finished" -eq 1 ]]; then
    if http_request auth POST "/api/v1/cases/${CASE_ID}/scan" '{"mode":"O1"}'; then
      if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "202" ]]; then
        rerun_scan="$(json_field '.scan_id')"
        step_pass 6b "bitmisken 2. POST /scan" "HTTP $HTTP_CODE (yeni tarama: ${rerun_scan:0:8}…) — iptal ediliyor"
        if [[ -n "$rerun_scan" && "$rerun_scan" != "null" ]]; then
          http_request auth POST "/api/v1/scans/${rerun_scan}/cancel" >/dev/null 2>&1 || true
          # T-378: iptal ASENKRON — terminal duruma (cancelled|completed) gelmeden
          # devam etmek sonraki kosuda "409 aktif tarama" yarisi yaratiyordu.
          CancelDeadline=$(( $(date +%s) + 20 ))
          rerun_status="?"
          while [[ "$(date +%s)" -lt "$CancelDeadline" ]]; do
            rerun_status="$(json_field '.status')"
            http_request auth GET "/api/v1/scans/${rerun_scan}" >/dev/null 2>&1 || true
            rerun_status="$(json_field '.status')"
            case "$rerun_status" in cancelled|completed|failed) break ;; esac
            sleep 1
          done
          if [[ "$rerun_status" == "cancelled" || "$rerun_status" == "completed" ]]; then
            step_pass 6c "2. tarama terminal durumda" "status=${rerun_status} (<=20 sn)"
          else
            step_warn 6c "2. tarama terminal durumda" \
              "status=${rerun_status:-?} — 20 sn icinde durmadi (baslangic temizligi telafi eder)"
          fi
        fi
      elif [[ "$HTTP_CODE" == "409" ]]; then
        step_pass 6b "bitmisken 2. POST /scan" "409 (hala aktif tarama var)"
      else
        step_fail 6b "bitmisken 2. POST /scan" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 160)"
      fi
    else
      step_fail 6b "bitmisken 2. POST /scan" "baglanti hatasi"
    fi
  else
    step_skip 6b "bitmisken 2. POST /scan" "tarama tamamlanmadi"
  fi
else
  step_skip 6a "aktifken 2. POST /scan -> 409" "tarama baslamadi"
  step_skip 6b "bitmisken 2. POST /scan" "tarama baslamadi"
fi

# ------------------------------------------------------------- 7) bulgular
FINDING_ID=""
if http_request auth GET "/api/v1/cases/${CASE_ID}/findings"; then
  if [[ "$HTTP_CODE" != "200" ]]; then
    step_fail 7 "bulgular" "HTTP $HTTP_CODE"
  else
    fcount="$(printf '%s' "$HTTP_BODY" | jq 'length' 2>/dev/null || echo 0)"
    dist="$(printf '%s' "$HTTP_BODY" | jq -r 'group_by(.status)|map("\(.[0].status)=\(length)")|join(", ")' 2>/dev/null || echo "?")"
    has_auth="$(printf '%s' "$HTTP_BODY" | jq 'if length == 0 then false else (map(has("authenticity")) | all) end' 2>/dev/null || echo false)"
    FINDING_ID="$(printf '%s' "$HTTP_BODY" | jq -r 'map(select(.id != null)) | (sort_by(-(.score // 0)) | .[0].id) // empty' 2>/dev/null || true)"
    if (( fcount > 0 )); then
      step_pass 7 "bulgular listelendi" "${fcount} bulgu [${dist}]"
    else
      step_fail 7 "bulgular listelendi" "bulgu yok (executed_tasks=$executed)"
    fi
    if [[ "$has_auth" == "true" ]]; then
      bad_auth="$(printf '%s' "$HTTP_BODY" | jq '[.[] | select(.authenticity != null and ((.authenticity < 0) or (.authenticity > 1)))] | length' 2>/dev/null || echo 0)"
      has_reasons="$(printf '%s' "$HTTP_BODY" | jq 'map(has("authenticity_reasons")) | all' 2>/dev/null || echo false)"
      if [[ "$bad_auth" == "0" ]]; then
        capability 7b "bulgu.authenticity (0-1)" 1 \
          "ornek=$(printf '%s' "$HTTP_BODY" | jq -r 'map(select(.authenticity != null)) | .[0].authenticity // "null"' 2>/dev/null)" ""
      else
        step_fail 7b "bulgu.authenticity (0-1)" "${bad_auth} bulguda aralik disi"
      fi
      capability 7c "bulgu.authenticity_reasons" "$( [[ "$has_reasons" == "true" ]] && echo 1 || echo 0 )" \
        "$(printf '%s' "$HTTP_BODY" | jq -c 'map(select(.authenticity_reasons != null)) | .[0].authenticity_reasons // []' 2>/dev/null)" \
        "authenticity_reasons alani yok"
    else
      capability 7b "bulgu.authenticity (0-1)" 0 "" "authenticity alani yok"
      capability 7c "bulgu.authenticity_reasons" 0 "" "authenticity alani yok"
    fi
  fi
else
  step_fail 7 "bulgular listelendi" "baglanti hatasi"
  step_skip 7b "bulgu.authenticity (0-1)" "bulgular okunamadi"
  step_skip 7c "bulgu.authenticity_reasons" "bulgular okunamadi"
fi

# --------------------------------------------------- 8) bulgu dogrulama
if [[ -n "$FINDING_ID" ]]; then
  if http_request auth PATCH "/api/v1/findings/${FINDING_ID}" '{"status":"confirmed"}'; then
    if [[ "$HTTP_CODE" == "200" ]] && [[ "$(json_field '.status')" == "confirmed" ]]; then
      if http_request auth GET "/api/v1/cases/${CASE_ID}/findings"; then
        kept="$(printf '%s' "$HTTP_BODY" | jq -r --arg id "$FINDING_ID" 'map(select(.id == $id)) | .[0].status // "yok"' 2>/dev/null || echo yok)"
        verified="$(printf '%s' "$HTTP_BODY" | jq -r --arg id "$FINDING_ID" 'map(select(.id == $id)) | .[0].verified_at // "yok"' 2>/dev/null || echo yok)"
        if [[ "$kept" == "confirmed" && "$verified" != "yok" && -n "$verified" ]]; then
          step_pass 8 "bulgu confirmed + kalici" "status=confirmed verified_at dolu"
        else
          step_fail 8 "bulgu confirmed + kalici" "status=$kept verified_at=$verified"
        fi
      else
        step_fail 8 "bulgu confirmed + kalici" "yeniden okuma basarisiz"
      fi
    else
      step_fail 8 "bulgu confirmed + kalici" "HTTP $HTTP_CODE status=$(json_field '.status')"
    fi
  else
    step_fail 8 "bulgu confirmed + kalici" "baglanti hatasi"
  fi
else
  step_skip 8 "bulgu confirmed + kalici" "dogrulanacak bulgu yok"
fi

# --------------------------------------------------------------- 9) rapor
REPORT_OK=0
report_code=""
REPORT_JSON_URL=""
if http_request auth POST "/api/v1/cases/${CASE_ID}/report" '{"format":"json"}'; then
  report_code="$HTTP_CODE"
else
  report_code="000"
fi
if [[ "$report_code" != "200" && "$report_code" != "201" && "$report_code" != "000" ]]; then
  # geriye donuk: cogul yol denenir (sozlesme farki raporlanir)
  if http_request auth POST "/api/v1/cases/${CASE_ID}/reports" '{"format":"json"}'; then
    report_code="$HTTP_CODE"
  fi
fi
if [[ "$report_code" == "200" || "$report_code" == "201" ]]; then
  report_id="$(json_field '.id')"
  REPORT_JSON_URL="$(json_field '.download_url')"
  [[ "$REPORT_JSON_URL" == "null" ]] && REPORT_JSON_URL=""
  step_pass 9 "rapor olustur (JSON)" "HTTP ${report_code} report=${report_id:0:8}…"
  # disk: konteynerde ara, yoksa host'ta
  files=""
  if command -v docker >/dev/null 2>&1 && docker inspect "$API_CONTAINER" >/dev/null 2>&1; then
    files="$(docker exec "$API_CONTAINER" sh -c "ls -1 ${REPORT_DIR_IN_CONTAINER}/${CASE_ID}/*.json 2>/dev/null" || true)"
    perms="$(docker exec "$API_CONTAINER" sh -c "stat -c '%a %n' ${REPORT_DIR_IN_CONTAINER}/${CASE_ID}/* 2>/dev/null" || true)"
    where="konteyner ${REPORT_DIR_IN_CONTAINER}/${CASE_ID}"
  else
    files="$(ls -1 "${REPO_ROOT}/data/reports/${CASE_ID}"/*.json 2>/dev/null || true)"
    perms="$(stat -c '%a %n' "${REPO_ROOT}/data/reports/${CASE_ID}"/* 2>/dev/null || true)"
    where="host data/reports/${CASE_ID}"
  fi
  artifacts="$(report_artifacts)"
  if [[ -n "$artifacts" ]]; then
    n_files="$(printf '%s\n' "$artifacts" | wc -l | tr -d ' ')"
    bad="$(printf '%s\n' "$artifacts" | awk '$1 != "600"')"
    if [[ -z "$bad" ]]; then
      step_pass 9b "rapor dosyasi diskte (600)" "${n_files} dosya, izin 600 [$(report_location)]"
      REPORT_OK=1
    else
      # Dagitilan imaj eski yerlesim/izinlerle yaziyor olabilir (T-314 henuz deploy edilmedi)
      step_pending 9b "rapor dosyasi izni 600" \
        "diskte var ama izin $(printf '%s' "$bad" | awk '{print $1}' | sort -u | tr '\n' ',' | sed 's/,$//') — T-314 final deploy sonrasi"
    fi
  else
    step_fail 9b "rapor dosyasi diskte" "$(report_location)/<case_id> altinda .json yok"
  fi
elif [[ "$report_code" == "000" ]]; then
  step_fail 9 "rapor olustur (JSON)" "baglanti hatasi"
else
  step_fail 9 "rapor olustur (JSON)" "HTTP ${report_code}: $(printf '%s' "$HTTP_BODY" | head -c 180)"
fi

# -------------------------------------------------------------- 10) export
if http_request auth GET "/api/v1/cases/${CASE_ID}/export"; then
  if [[ "$HTTP_CODE" != "200" ]]; then
    step_fail 10 "export (JSON)" "HTTP $HTTP_CODE"
  else
    exp_case="$(json_field '.case.id')"
    exp_cases="$(json_field '.cases | length')"
    exp_findings="$(json_field '.findings | length')"
    if [[ "$exp_case" == "$CASE_ID" ]]; then
      step_pass 10 "export (JSON)" "case.id eslesti, findings=${exp_findings}"
    elif [[ "$exp_cases" =~ ^[0-9]+$ ]] && (( exp_cases > 0 )); then
      step_pass 10 "export (JSON)" "cases[${exp_cases}] dolu"
    else
      step_fail 10 "export (JSON)" "case/cases alani bos"
    fi
  fi
else
  step_fail 10 "export (JSON)" "baglanti hatasi"
fi

# --------------------------------- 10b-10f) vaka listesi (T-358 sozlesmesi)
# Zarf: {items,total,limit,offset}; ?legacy=1 eski duz dizi. Tum alt adimlar
# "kabiliyet" olarak siniflandirilir: deploy oncesi BEKLENIYOR, sonrasi FAIL/PASS.
cases_envelope=""
if http_request auth GET "/api/v1/cases?limit=5"; then
  cases_envelope="$(printf '%s' "$HTTP_BODY" | jq 'type == "object" and has("items") and has("total") and has("limit")' 2>/dev/null || echo false)"
  if [[ "$HTTP_CODE" == "200" && "$cases_envelope" == "true" ]]; then
    n_items="$(printf '%s' "$HTTP_BODY" | jq '.items | length')"
    c_total="$(printf '%s' "$HTTP_BODY" | jq '.total')"
    c_limit="$(printf '%s' "$HTTP_BODY" | jq '.limit')"
    if [[ "$n_items" =~ ^[0-9]+$ ]] && (( n_items <= 5 )) \
       && [[ "$c_total" =~ ^[0-9]+$ ]] && (( c_total >= n_items )); then
      step_pass 10b "cases?limit=5 zarfi" "items=${n_items}, total=${c_total}, limit=${c_limit}"
    else
      step_fail 10b "cases?limit=5 zarfi" "items=${n_items} total=${c_total} limit=${c_limit}"
    fi

    # --- arama: kendi vakasini buluyor
    if http_request auth GET "/api/v1/cases?q=E2E%20kabul&limit=200"; then
      found_self="$(printf '%s' "$HTTP_BODY" | jq -r --arg id "$CASE_ID" '[.items[]?.id] | index($id) != null' 2>/dev/null || echo false)"
      if [[ "$HTTP_CODE" == "200" && "$found_self" == "true" ]]; then
        step_pass 10c "cases?q=… kendi vakasini buluyor" "q='E2E kabul' → vaka listede"
      else
        capability 10c "cases?q=… kendi vakasini buluyor" 0 "" "HTTP $HTTP_CODE, eşleşme=${found_self}"
      fi
    else
      step_fail 10c "cases?q=… kendi vakasini buluyor" "baglanti hatasi"
    fi
    # --- ozel karakter: cokme yok (200 veya 422; 5xx yasak)
    if http_request auth GET "/api/v1/cases?q=%25&limit=5"; then
      if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "422" ]]; then
        step_pass 10d "cases?q=%25 ozel karakter" "HTTP $HTTP_CODE (5xx yok)"
      else
        step_fail 10d "cases?q=%25 ozel karakter" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 100)"
      fi
    else
      step_fail 10d "cases?q=%25 ozel karakter" "baglanti hatasi"
    fi

    # --- siralama
    if http_request auth GET "/api/v1/cases?sort=name_asc&limit=50"; then
      if [[ "$HTTP_CODE" == "200" ]]; then
        sorted_ok="$(printf '%s' "$HTTP_BODY" | jq '[.items[]?.name] as $n | ($n == ($n | sort))' 2>/dev/null || echo false)"
        first_names="$(printf '%s' "$HTTP_BODY" | jq -r '[.items[]?.name][0:3] | join(" | ")' 2>/dev/null || echo "?")"
        if [[ "$sorted_ok" == "true" ]]; then
          step_pass 10e "cases?sort=name_asc artan" "ilk: ${first_names}"
        else
          step_fail 10e "cases?sort=name_asc artan" "siralama artan degil: ${first_names}"
        fi
      else
        capability 10e "cases?sort=name_asc artan" 0 "" "HTTP $HTTP_CODE"
      fi
    else
      step_fail 10e "cases?sort=name_asc artan" "baglanti hatasi"
    fi
    if http_request auth GET "/api/v1/cases?sort=bozuk"; then
      if [[ "$HTTP_CODE" == "422" ]]; then
        step_pass 10f "cases?sort=bozuk -> 422" "422"
      else
        capability 10f "cases?sort=bozuk -> 422" 0 "" "HTTP $HTTP_CODE (beklenen 422)"
      fi
    else
      step_fail 10f "cases?sort=bozuk -> 422" "baglanti hatasi"
    fi

    # --- limit sinirlari: 0 -> 422 ; 999 -> kirpilmis (<=200) veya 422
    if http_request auth GET "/api/v1/cases?limit=0"; then
      if [[ "$HTTP_CODE" == "422" ]]; then
        step_pass 10g "cases?limit=0 -> 422" "422"
      else
        capability 10g "cases?limit=0 -> 422" 0 "" "HTTP $HTTP_CODE (beklenen 422)"
      fi
    else
      step_fail 10g "cases?limit=0 -> 422" "baglanti hatasi"
    fi
    # Sozlesme (Atlas T-361 karari): limit <= 200; asimi 422 ile ACIKCA reddedilir.
    if http_request auth GET "/api/v1/cases?limit=999"; then
      if [[ "$HTTP_CODE" == "422" ]]; then
        step_pass 10h "cases?limit=999 -> 422" "422 (sozlesme: limit <= 200, asimi 422)"
      elif [[ "$HTTP_CODE" == "200" ]]; then
        big_items="$(printf '%s' "$HTTP_BODY" | jq '.items | length')"
        big_limit="$(printf '%s' "$HTTP_BODY" | jq '.limit')"
        if [[ "$big_items" =~ ^[0-9]+$ ]] && (( big_items <= 200 )); then
          step_pass 10h "cases?limit=999 kirpildi (<=200)" \
            "donen=${big_items}, limit=${big_limit} (sozlesme: limit <= 200, asimi 422)"
        else
          step_fail 10h "cases?limit=999 kirpildi (<=200)" "donen=${big_items} (>200)"
        fi
      else
        step_fail 10h "cases?limit=999 -> 422" \
          "HTTP $HTTP_CODE (beklenen 422 veya kirpilmis 200)"
      fi
    else
      step_fail 10h "cases?limit=999 -> 422" "baglanti hatasi"
    fi

    # --- legacy: duz dizi
    if http_request auth GET "/api/v1/cases?legacy=1"; then
      legacy_type="$(printf '%s' "$HTTP_BODY" | jq -r 'type' 2>/dev/null || echo "?")"
      if [[ "$HTTP_CODE" == "200" && "$legacy_type" == "array" ]]; then
        step_pass 10i "cases?legacy=1 duz dizi" "dizi, $(printf '%s' "$HTTP_BODY" | jq 'length') kayit"
      else
        capability 10i "cases?legacy=1 duz dizi" 0 "" "HTTP $HTTP_CODE, govde tipi=${legacy_type}"
      fi
    else
      step_fail 10i "cases?legacy=1 duz dizi" "baglanti hatasi"
    fi
  else
    # Zarf yok: T-358 oncesi imaj → yeni adimlarin tamami BEKLENIYOR/FAIL
    reason="HTTP $HTTP_CODE, zarf=${cases_envelope}"
    capability 10b "cases?limit=5 zarfi" 0 "" "$reason"
    for n in 10c 10d 10e 10f 10g 10h 10i; do
      capability "$n" "vaka listesi (T-358)" 0 "" "$reason"
    done
  fi
else
  step_fail 10b "cases?limit=5 zarfi" "baglanti hatasi"
  for n in 10c 10d 10e 10f 10g 10h 10i; do
    step_skip "$n" "vaka listesi (T-358)" "baglanti hatasi"
  done
fi

# ------------------------------------------------------- 11) kimlik grafigi
if http_request auth GET "/api/v1/cases/${CASE_ID}/graph"; then
  graph_ok="$(printf '%s' "$HTTP_BODY" | jq 'has("nodes") and has("edges")' 2>/dev/null || echo false)"
  if [[ "$HTTP_CODE" == "200" && "$graph_ok" == "true" ]]; then
    n_nodes="$(printf '%s' "$HTTP_BODY" | jq '.nodes | length')"
    n_edges="$(printf '%s' "$HTTP_BODY" | jq '.edges | length')"
    capability 11 "graph {nodes,edges} sozlesmesi" 1 "nodes=${n_nodes} edges=${n_edges}" ""
    if [[ "$n_edges" =~ ^[0-9]+$ ]] && (( n_edges > 0 )); then
      step_pass 11b "kimlik grafigi kenarlari" "edges=${n_edges}"
    else
      step_warn 11b "kimlik grafigi kenarlari" \
        "edges=0 — anonim hedefte ayni kullanici adi >=2 platformda cikmadi (bilgi, FAIL degil)"
    fi
  else
    capability 11 "graph {nodes,edges} sozlesmesi" 0 "" \
      "HTTP $HTTP_CODE veya nodes/edges alanlari eksik"
    step_skip 11b "kimlik grafigi kenarlari" "graph okunamadi"
  fi
else
  step_fail 11 "graph {nodes,edges} sozlesmesi" "baglanti hatasi"
  step_skip 11b "kimlik grafigi kenarlari" "graph okunamadi"
fi

# -------------------------------------------------- 12) JSON rapor bolumleri
if [[ -n "$REPORT_JSON_URL" && "$REPORT_JSON_URL" != "null" ]]; then
  if http_request auth GET "$REPORT_JSON_URL"; then
    if [[ "$HTTP_CODE" != "200" ]]; then
      step_fail 12 "JSON rapor bolumleri" "indirme HTTP $HTTP_CODE"
    else
      missing="$(printf '%s' "$HTTP_BODY" | jq -r '
        [ (if has("executive_summary") then empty else "executive_summary" end),
          (if (has("platform_breakdown") or has("platform_distribution")) then empty else "platform_breakdown|platform_distribution" end),
          (if (has("cross_links") or has("identity_links")) then empty else "cross_links|identity_links" end),
          (if has("timeline") then empty else "timeline" end),
          (if has("evidence_summary") then empty else "evidence_summary" end)
        ] | join(",")' 2>/dev/null || echo "parse-hatasi")"
      found="$(printf '%s' "$HTTP_BODY" | jq -r '
        ["executive_summary",
         (if has("platform_breakdown") then "platform_breakdown" elif has("platform_distribution") then "platform_distribution" else empty end),
         (if has("cross_links") then "cross_links" elif has("identity_links") then "identity_links" else empty end),
         "timeline", "evidence_summary"] | map(select(. != null and . != "")) | join(",")' 2>/dev/null || echo "?")"
      if [[ -z "$missing" ]]; then
        step_pass 12 "JSON rapor bolumleri (5/5)" "$found"
      else
        capability 12 "JSON rapor bolumleri" 0 "" "eksik: ${missing}"
      fi
    fi
  else
    step_fail 12 "JSON rapor bolumleri" "baglanti hatasi"
  fi
else
  step_skip 12 "JSON rapor bolumleri" "rapor olusturulamadi"
fi

# --------------------------------------------------- 13) HTML rapor + CSP
html_url=""
if http_request auth POST "/api/v1/cases/${CASE_ID}/report" '{"format":"html"}'; then
  [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "201" ]] && html_url="$(json_field '.download_url')"
fi
if [[ -n "$html_url" && "$html_url" != "null" ]]; then
  tmp_html="$(mktemp)"
  hdr_file="$(mktemp)"
  if curl -sS --max-time "$CURL_MAX_TIME" ${CONF_AUTH[@]+"${CONF_AUTH[@]}"} \
       -D "$hdr_file" -o "$tmp_html" "${BASE_URL}${html_url}"; then
    ctype="$(grep -i '^content-type:' "$hdr_file" | head -1 | tr -d '\r' | cut -d' ' -f2-)"
    csp="$(grep -i '^content-security-policy:' "$hdr_file" | head -1 | tr -d '\r' | cut -d' ' -f2-)"
    size="$(wc -c <"$tmp_html" | tr -d ' ')"
    if [[ "$ctype" == text/html* && -n "$csp" && "$size" -gt 0 ]]; then
      step_pass 13 "HTML rapor (text/html + CSP)" "${size} bayt, CSP: $(printf '%s' "$csp" | head -c 40)…"
    else
      capability 13 "HTML rapor (text/html + CSP)" 0 "" \
        "content-type='${ctype:-yok}' csp='$( [[ -n "$csp" ]] && echo var || echo yok )'"
    fi
  else
    step_fail 13 "HTML rapor (text/html + CSP)" "indirme baglantisi kurulamadi"
  fi
  rm -f "$tmp_html" "$hdr_file"
else
  capability 13 "HTML rapor (text/html + CSP)" 0 "" \
    "POST /report {format:html} -> HTTP ${HTTP_CODE} (bicim desteklenmiyor olabilir)"
fi

# ---------------------------------------- 14) min_authenticity filtresi
if http_request auth GET "/api/v1/cases/${CASE_ID}/findings?min_authenticity=0.35"; then
  if [[ "$HTTP_CODE" == "200" ]]; then
    fa_count="$(printf '%s' "$HTTP_BODY" | jq 'length' 2>/dev/null || echo 0)"
    fa_below="$(printf '%s' "$HTTP_BODY" | jq '[.[] | select(.authenticity != null and .authenticity < 0.35)] | length' 2>/dev/null || echo 0)"
    fa_null="$(printf '%s' "$HTTP_BODY" | jq '[.[] | select(.authenticity == null)] | length' 2>/dev/null || echo 0)"
    if [[ "$fa_null" =~ ^[0-9]+$ && "$fa_count" =~ ^[0-9]+$ ]] && (( fa_count > 0 && fa_null == fa_count )); then
      capability 14 "min_authenticity=0.35 filtresi" 0 "" \
        "yanitta authenticity alani yok — filtre dogrulanamaz"
    elif [[ "$fa_below" == "0" ]]; then
      step_pass 14 "min_authenticity=0.35 filtresi" "${fa_count} bulgu (filtresiz ${fcount:-?}), esik alti yok"
      if [[ "$fa_count" =~ ^[0-9]+$ && "${fcount:-0}" =~ ^[0-9]+$ ]] && (( fa_count > fcount )); then
        step_fail 14b "filtre sonucu daraltiyor" "filtreli=${fa_count} > filtresiz=${fcount}"
      else
        step_pass 14b "filtre sonucu daraltiyor" "filtreli=${fa_count} <= filtresiz=${fcount:-?}"
      fi
    else
      capability 14 "min_authenticity=0.35 filtresi" 0 "" "yanitta esik alti ${fa_below} bulgu dondu"
    fi
  elif [[ "$HTTP_CODE" == "422" ]]; then
    capability 14 "min_authenticity=0.35 filtresi" 0 "" "HTTP 422 (filtre parametresi taninmiyor)"
  else
    step_fail 14 "min_authenticity=0.35 filtresi" "HTTP $HTTP_CODE"
  fi
else
  step_fail 14 "min_authenticity=0.35 filtresi" "baglanti hatasi"
fi
# gecersiz deger -> 422
if http_request auth GET "/api/v1/cases/${CASE_ID}/findings?min_authenticity=abc"; then
  if [[ "$HTTP_CODE" == "422" ]]; then
    step_pass 14c "gecersiz min_authenticity -> 422" "422"
  else
    capability 14c "gecersiz min_authenticity -> 422" 0 "" "HTTP $HTTP_CODE (beklenen 422)"
  fi
else
  step_fail 14c "gecersiz min_authenticity -> 422" "baglanti hatasi"
fi

# ------------------------------------------------- 15) status filtresi
if http_request auth GET "/api/v1/cases/${CASE_ID}/findings?status=candidate,confirmed"; then
  if [[ "$HTTP_CODE" == "200" ]]; then
    st_count="$(printf '%s' "$HTTP_BODY" | jq 'length' 2>/dev/null || echo 0)"
    st_bad="$(printf '%s' "$HTTP_BODY" | jq '[.[] | select(.status != "candidate" and .status != "confirmed")] | length' 2>/dev/null || echo 0)"
    if [[ "$st_bad" == "0" ]]; then
      step_pass 15 "status=candidate,confirmed filtresi" "${st_count} bulgu, izinsiz durum yok"
    else
      capability 15 "status=candidate,confirmed filtresi" 0 "" "${st_bad} bulgu filtre disi durumda"
    fi
  elif [[ "$HTTP_CODE" == "422" ]]; then
    capability 15 "status=candidate,confirmed filtresi" 0 "" "HTTP 422 (filtre parametresi taninmiyor)"
  else
    step_fail 15 "status=candidate,confirmed filtresi" "HTTP $HTTP_CODE"
  fi
else
  step_fail 15 "status=candidate,confirmed filtresi" "baglanti hatasi"
fi
if http_request auth GET "/api/v1/cases/${CASE_ID}/findings?status=bogus"; then
  if [[ "$HTTP_CODE" == "422" ]]; then
    step_pass 15b "gecersiz status -> 422" "422"
  else
    capability 15b "gecersiz status -> 422" 0 "" "HTTP $HTTP_CODE (beklenen 422)"
  fi
else
  step_fail 15b "gecersiz status -> 422" "baglanti hatasi"
fi

# ----------------------------------------------------------- 16) /healthz
if http_request - GET /healthz; then
  checks_ok="$(printf '%s' "$HTTP_BODY" | jq 'has("checks") and (.checks | type == "object")' 2>/dev/null || echo false)"
  if [[ "$HTTP_CODE" == "200" && "$checks_ok" == "true" ]]; then
    n_checks="$(printf '%s' "$HTTP_BODY" | jq '.checks | length')"
    step_pass 16 "/healthz (derin saglik)" "200, ${n_checks} kontrol: $(printf '%s' "$HTTP_BODY" | jq -r '.checks | keys | join(",")' 2>/dev/null | head -c 60)"
  else
    capability 16 "/healthz (derin saglik)" 0 "" "HTTP $HTTP_CODE / checks nesnesi yok"
  fi
else
  step_fail 16 "/healthz (derin saglik)" "baglanti hatasi"
fi

# ----------------------------------------------- 17) metrik ucu (T-329)
# Dagitilan sozlesme: `/api/v1/metrics` (ops.py router'i `/api/v1` altinda).
# Kok `/metrics` edge'de web'e gider (404) — T-330 metni kok yolu yazmisti, bu
# yuzden iki yol da denenir ve HANGISININ yanit verdigi raporlanir.
METRICS_PATH=""
for candidate in /api/v1/metrics /metrics; do
  if http_request auth GET "$candidate"; then
    if [[ "$HTTP_CODE" == "200" ]]; then METRICS_PATH="$candidate"; break; fi
  fi
done
metrics_ok=0
if [[ -n "$METRICS_PATH" ]]; then
  n_showis="$(printf '%s' "$HTTP_BODY" | grep -c '^showis_' || true)"
  if (( n_showis >= 5 )); then
    metrics_ok=1
    step_pass 17 "metrik ucu (${METRICS_PATH})" "200, ${n_showis} showis_ satiri"
  else
    capability 17 "metrik ucu (${METRICS_PATH})" 0 "" "yalniz ${n_showis} showis_ satiri (>=5 bekleniyor)"
  fi
else
  capability 17 "metrik ucu (/api/v1/metrics)" 0 "" \
    "ne /api/v1/metrics ne /metrics 200 dondurdu (son HTTP $HTTP_CODE)"
fi
if [[ "$metrics_ok" -eq 1 ]]; then
  if http_request - GET "$METRICS_PATH"; then
    if [[ "$HTTP_CODE" == "401" ]]; then
      step_pass 17b "metrik ucu kimliksiz -> 401" "401 (${METRICS_PATH})"
    else
      step_fail 17b "metrik ucu kimliksiz -> 401" "HTTP $HTTP_CODE (auth'suz erisim)"
    fi
  else
    step_fail 17b "metrik ucu kimliksiz -> 401" "baglanti hatasi"
  fi
else
  step_skip 17b "/metrics kimliksiz -> 401" "metrik ucu yok"
fi

# ----------------------------------------------- 18) guvenlik basliklari
ui_hdrs="$(curl -sSI --max-time "$CURL_MAX_TIME" ${CONF_BASIC[@]+"${CONF_BASIC[@]}"} "${BASE_URL}/" 2>/dev/null || true)"
api_hdrs="$(curl -sS -D - -o /dev/null --max-time "$CURL_MAX_TIME" ${CONF_AUTH[@]+"${CONF_AUTH[@]}"} "${BASE_URL}/api/v1/cases" 2>/dev/null || true)"
hdrs_missing_ui=""
for h in x-content-type-options x-frame-options referrer-policy content-security-policy; do
  printf '%s' "$ui_hdrs" | grep -qi "^${h}:" || hdrs_missing_ui="${hdrs_missing_ui} ${h}"
done
if [[ -z "$hdrs_missing_ui" ]]; then
  step_pass 18 "UI guvenlik basliklari (nosniff/XFO/RP/CSP)" "hepsi var"
else
  capability 18 "UI guvenlik basliklari (nosniff/XFO/RP/CSP)" 0 "" "eksik:${hdrs_missing_ui}"
fi
hdrs_missing_api=""
for h in x-content-type-options x-frame-options referrer-policy; do
  printf '%s' "$api_hdrs" | grep -qi "^${h}:" || hdrs_missing_api="${hdrs_missing_api} ${h}"
done
if [[ -z "$hdrs_missing_api" ]]; then
  step_pass 18b "API guvenlik basliklari (nosniff/XFO/RP)" "hepsi var"
else
  capability 18b "API guvenlik basliklari (nosniff/XFO/RP)" 0 "" "eksik:${hdrs_missing_api}"
fi

# ------------------------------------ 19) edge backfill (dry-run)
backfill_py=""
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  backfill_py="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  backfill_py="$(command -v python3)"
fi
backfill_dsn="${SHOWIS_DATABASE_URL:-}"
if [[ -z "$backfill_dsn" && -f "${REPO_ROOT}/infra/.env" ]]; then
  pg_user="$(grep -m1 '^POSTGRES_USER=' "${REPO_ROOT}/infra/.env" | cut -d= -f2- || true)"
  pg_pass="$(grep -m1 '^POSTGRES_PASSWORD=' "${REPO_ROOT}/infra/.env" | cut -d= -f2- || true)"
  pg_db="$(grep -m1 '^POSTGRES_DB=' "${REPO_ROOT}/infra/.env" | cut -d= -f2- || true)"
  pg_host="${POSTGRES_HOST:-}"
  pg_port="${POSTGRES_PORT:-5432}"
  if [[ -z "$pg_host" ]] && command -v docker >/dev/null 2>&1; then
    pg_host="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' showis-postgres 2>/dev/null | awk '{print $NF}' || true)"
  fi
  if [[ -n "$pg_user" && -n "$pg_db" && -n "$pg_host" ]]; then
    backfill_dsn="postgresql://${pg_user}:${pg_pass}@${pg_host}:${pg_port}/${pg_db}"
  fi
fi
if [[ -n "$backfill_dsn" && -n "$backfill_py" && -f "${REPO_ROOT}/scripts/backfill_edges.py" ]]; then
  bf_out="$(cd "$REPO_ROOT" && SHOWIS_DATABASE_URL="$backfill_dsn" PYTHONPATH="${REPO_ROOT}/apps/api:${REPO_ROOT}/packages/contracts:${REPO_ROOT}/packages/settings:${REPO_ROOT}/packages/llm:${REPO_ROOT}/packages/engine:${REPO_ROOT}/packages/connectors" \
      "$backfill_py" scripts/backfill_edges.py --case-id "$CASE_ID" --dry-run 2>&1)" && bf_rc=0 || bf_rc=$?
  if [[ "$bf_rc" -eq 0 ]]; then
    bf_json="$(printf '%s' "$bf_out" | sed -n '/^{/,/^}/p')"
    if printf '%s' "$bf_json" | jq -e 'has("entities") and has("edges")' >/dev/null 2>&1; then
      bf_ent="$(printf '%s' "$bf_json" | jq -r '.entities')"
      bf_edges="$(printf '%s' "$bf_json" | jq -r '.edges')"
      bf_exist="$(printf '%s' "$bf_json" | jq -r '.existing_edges // "?"')"
      if printf '%s' "$bf_out" | grep -qi "dry-run"; then
        step_pass 19 "backfill_edges --dry-run" "entities=${bf_ent} yeni_edges=${bf_edges} mevcut=${bf_exist}, yazilmadi"
      else
        step_warn 19 "backfill_edges --dry-run" "JSON parse edildi ama dry-run notu yok"
      fi
    else
      capability 19 "backfill_edges --dry-run" 0 "" "cikti JSON olarak parse edilemedi"
    fi
  else
    capability 19 "backfill_edges --dry-run" 0 "" "exit=${bf_rc}: $(printf '%s' "$bf_out" | tail -1 | head -c 120)"
  fi
else
  capability 19 "backfill_edges --dry-run" 0 "" \
    "DSN/python bulunamadi (SHOWIS_DATABASE_URL veya docker showis-postgres gerekli)"
fi

# ------------------------------------------------------------- 20) silme
if [[ "$KEEP" -eq 1 ]]; then
  step_skip 20 "vaka silme (204 -> 404)" "--keep verildi; vaka birakildi: ${CASE_ID}"
else
  if http_request auth DELETE "/api/v1/cases/${CASE_ID}"; then
    if [[ "$HTTP_CODE" == "204" ]]; then
      ok_404=0
      if http_request auth GET "/api/v1/cases/${CASE_ID}"; then
        [[ "$HTTP_CODE" == "404" ]] && ok_404=1
      fi
      leftovers="$(report_artifacts | head -3)"
      if [[ "$ok_404" -eq 1 && -z "$leftovers" ]]; then
        CASE_DELETED=1
        step_pass 20 "vaka silindi (204/404, rapor kalintisi yok)" "204 -> 404, rapor dosyalari silindi"
      elif [[ "$ok_404" -eq 1 ]]; then
        CASE_DELETED=1   # DB satiri silindi; trap yalniz dosya kalintisi icin ugrasmasin
        step_fail 20 "vaka silindi" "FAIL: rapor kalintisi: ${leftovers}"
      else
        step_fail 20 "vaka silindi" "DELETE 204 ama GET $( [[ $ok_404 -eq 1 ]] && echo 404 || echo "$HTTP_CODE" )"
      fi
    else
      step_fail 20 "vaka silindi (204)" "HTTP $HTTP_CODE: $(printf '%s' "$HTTP_BODY" | head -c 160)"
    fi
  else
    step_fail 20 "vaka silindi (204)" "baglanti hatasi"
  fi
fi

fi  # CASE_ID

# ---------------------------------------------------------------- 21) ozet
echo
echo "${C_BOLD}== OZET ==${C_RESET}"
printf '%-4s %-46s %-12s %s\n' "NO" "ADIM" "DURUM" "AYRINTI"
printf '%-4s %-46s %-12s %s\n' "----" "----------------------------------------------" "------------" "-------"
for line in "${RESULTS[@]}"; do
  IFS='|' read -r no name state detail <<<"$line"
  color="$C_RESET"
  case "$state" in
    PASS) color="$C_GREEN" ;; FAIL) color="$C_RED" ;;
    BEKLENIYOR) color="$C_YELLOW" ;; ATLANDI) color="$C_DIM" ;;
    UYARI) color="$C_CYAN" ;;
  esac
  printf '%-4s %-46s %s%-12s%s %s\n' "$no" "$name" "$color" "$state" "$C_RESET" "$detail"
done
echo
echo "PASS=${PASS_N}  FAIL=${FAIL_N}  BEKLENIYOR=${PENDING_N}  UYARI=${WARN_N}  ATLANDI=${SKIP_N}  (strict=${STRICT}, pre-deploy=${PRE_DEPLOY})"
if [[ "$KEEP" -eq 1 && -n "$CASE_ID" ]]; then
  echo "${C_DIM}vaka birakildi: ${CASE_ID} (sil: curl ... -X DELETE ${BASE_URL}/api/v1/cases/${CASE_ID})${C_RESET}"
fi

if [[ "$FAIL_N" -gt 0 ]]; then
  printf '\n%sSONUÇ: KALDI%s\n' "$C_RED$C_BOLD" "$C_RESET"
  exit 1
fi
printf '\n%sSONUÇ: GEÇTİ%s' "$C_GREEN$C_BOLD" "$C_RESET"
if [[ "$PENDING_N" -gt 0 ]]; then
  printf ' %s(%s adim final deploy sonrasi bekleniyor)%s' "$C_YELLOW" "$PENDING_N" "$C_RESET"
fi
if [[ "$WARN_N" -gt 0 ]]; then
  printf ' %s(%s bilgi notu)%s' "$C_CYAN" "$WARN_N" "$C_RESET"
fi
echo
exit 0

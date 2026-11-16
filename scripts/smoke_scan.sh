#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# smoke_scan.sh — canli tarama suresi olcumu (T-201 / T-202 kabul kaniti)
#
# Ne yapar?
#   1. Gecici bir vaka olusturur (purpose icinde "SMOKE" isareti; hedef adi
#      anonim: "Test Smoke").
#   2. GET  /api/v1/cases/{id}/scan/estimate?mode=O2  -> tahmin (sn/gorev/istek)
#   3. POST /api/v1/cases/{id}/scan                  -> taramayi baslatir
#   4. GET  /api/v1/scans/{id}                       -> terminal duruma kadar poll
#      (varsayilan 5 sn aralik, ust sinir 30 dk)
#   5. Olculen sure + gorev/istek/bulgu sayilarini JSON olarak yazar ve
#      insan-okur ozet basar.
#
# Varsayilan olarak vakayi **silmez**: kanit DB'de kalir. `--delete` ile
# kaskad siler (KVKK silme yolu da boylece dogrulanmis olur).
#
# Kimlik dogrulama (iki katman, T-108 + T-113):
#   * Edge: Caddy `/health` disindaki her yolda **HTTP Basic** ister. Kimlik
#     `--basic-auth user:parola`, `SHOWIS_BASIC_AUTH_USER` +
#     `SHOWIS_BASIC_AUTH_PASSWORD` veya `--base-url http://user:parola@host`
#     userinfo'sinden alinir (oncelik bu sirada).
#   * API: `X-API-Token`. Basic auth **verildiginde** `Authorization` basligi
#     curl tarafindan Basic icin kullanilir; bu yuzden token yalniz
#     `X-API-Token` ile gonderilir (cakisma olmaz). Basic auth yoksa geriye
#     donuk uyum icin `Authorization: Bearer <token>` de gonderilir.
#   * Kimlik bilgileri **argv'de gorunmez**: curl `--config` dosyasi (0600,
#     gecici) ile verilir; parola ciktiya/rapora yazilmaz.
#
# Kullanim:
#   export SHOWIS_API_TOKEN=...                       # auth (T-108)
#   export SHOWIS_BASIC_AUTH_USER=... SHOWIS_BASIC_AUTH_PASSWORD=...  # edge (T-113)
#   bash scripts/smoke_scan.sh
#   bash scripts/smoke_scan.sh --mode O2 --reference 636
#   bash scripts/smoke_scan.sh --basic-auth user:parola --mode O2
#   bash scripts/smoke_scan.sh --allow-unauthenticated --dry-run
#
# Cikti: data/smoke/<UTC-timestamp>.json (data/ .gitignore kapsaminda)
# Cikis kodlari: 0 basarili tarama, 1 hata, 2 kullanim, 3 tarama tamamlanmadi
# ---------------------------------------------------------------------------
set -euo pipefail
set +x   # kimlik bilgileri izde/log'da gorunmesin (parola/token asla trace edilmez)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${REPO_ROOT}/data/smoke"

BASE_URL="${SHOWIS_BASE_URL:-http://127.0.0.1:8083}"
TOKEN="${SHOWIS_API_TOKEN:-}"
BASIC_ARG=""                              # --basic-auth user:parola
BASIC_USER="${SHOWIS_BASIC_AUTH_USER:-}"
BASIC_PASSWORD="${SHOWIS_BASIC_AUTH_PASSWORD:-}"
URL_USERINFO=""                           # --base-url icindeki user:parola
MODE="O2"
INTERVAL=5          # saniye
TIMEOUT=1800       # 30 dk
DELETE_CASE=0
DRY_RUN=0
ALLOW_UNAUTH=0
REFERENCE=""       # onceki olcum (sn) — verilirse fark yuzdesi basilir
CURL_MAX_TIME=60

usage() {
  cat <<'USAGE'
smoke_scan.sh — Showis canli tarama suresi olcumu

Kullanim:
  scripts/smoke_scan.sh [secenekler]

Secenekler:
  --base-url URL          API tabani (varsayilan: http://127.0.0.1:8083)
  --mode O1|O2|O3         Tarama modu (varsayilan: O2)
  --token TOKEN           API token (varsayilan: $SHOWIS_API_TOKEN)
  --basic-auth USER:PASS  Edge (Caddy) HTTP Basic kimligi; verilirse token
                          yalniz X-API-Token basligiyla gonderilir
  --interval SEC          Poll araligi (varsayilan: 5)
  --timeout SEC           Ust sinir (varsayilan: 1800 = 30 dk)
  --reference SEC         Onceki olcum; fark ve % degisim basilir
  --delete                Vakayi olcum sonunda kaskad siler (varsayilan: silmez)
  --allow-unauthenticated Token yoksa devam et (yalniz auth kapaliyken anlamli)
  --dry-run               Hic istek atmadan plani basar
  -h, --help              Bu yardimi basar

Ortam:
  SHOWIS_API_TOKEN                 API token (X-API-Token)
  SHOWIS_BASIC_AUTH_USER           Edge basic auth kullanicisi (Caddy)
  SHOWIS_BASIC_AUTH_PASSWORD       Edge basic auth parolasi
  SHOWIS_BASE_URL                  Alternatif taban URL
                                   (user:parola@host userinfo'si de kabul edilir)

Oncelik: --basic-auth > SHOWIS_BASIC_AUTH_* > --base-url userinfo.
Caddy (:8083) `/health` disinda basic auth istedigi icin edge uzerinden
olcum yaparken basic auth **zorunludur**; yalniz token gondermek 401 verir.
Kimlik bilgileri curl'e 0600 gecici `--config` dosyasiyla verilir; `ps`
ciktisinda ve raporlarda gorunmez.

Ornek:
  export SHOWIS_API_TOKEN=***
  export SHOWIS_BASIC_AUTH_USER=showis
  read -rs SHOWIS_BASIC_AUTH_PASSWORD   # parolayi argv'ye yazmadan
  export SHOWIS_BASIC_AUTH_PASSWORD
  bash scripts/smoke_scan.sh --reference 636
USAGE
}

die() {
  echo "HATA: $*" >&2
  exit 1
}

# Kullanim hatalari: exit 2 (calisma hatasi exit 1'den ayri tutulur)
usage_die() {
  echo "HATA: $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="${2:?--base-url deger ister}"; shift 2 ;;
    --mode) MODE="${2:?--mode deger ister}"; shift 2 ;;
    --token) TOKEN="${2:?--token deger ister}"; shift 2 ;;
    --basic-auth) BASIC_ARG="${2:?--basic-auth user:parola ister}"; shift 2 ;;
    --interval) INTERVAL="${2:?--interval deger ister}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?--timeout deger ister}"; shift 2 ;;
    --reference) REFERENCE="${2:?--reference deger ister}"; shift 2 ;;
    --delete) DELETE_CASE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --allow-unauthenticated) ALLOW_UNAUTH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Bilinmeyen arguman: $1" >&2; usage >&2; exit 2 ;;
  esac
done

BASE_URL="${BASE_URL%/}"
MODE="$(printf '%s' "$MODE" | tr '[:lower:]' '[:upper:]')"
case "$MODE" in
  O1|O2|O3) ;;
  *) usage_die "gecersiz mod: $MODE (O1|O2|O3)" ;;
esac
[[ "$INTERVAL" =~ ^[0-9]+$ ]] || usage_die "--interval sayi olmali"
[[ "$TIMEOUT" =~ ^[0-9]+$ ]] || usage_die "--timeout sayi olmali"
HAS_REFERENCE=0
if [[ -n "$REFERENCE" ]]; then
  [[ "$REFERENCE" =~ ^[0-9]+([.][0-9]+)?$ ]] || usage_die "--reference sayi olmali"
  HAS_REFERENCE=1
fi

# --- kimlik: edge basic auth (Caddy) + API token (T-108) -------------------
# 1) URL userinfo'sini ayikla: http://user:parola@host:port -> http://host:port
#    (parola ozet/log ciktisinda gorunmesin diye ayri tutulur)
if [[ "$BASE_URL" =~ ^([A-Za-z][A-Za-z0-9+.-]*)://([^/@]+)@(.+)$ ]]; then
  URL_USERINFO="${BASH_REMATCH[2]}"
  BASE_URL="${BASH_REMATCH[1]}://${BASH_REMATCH[3]}"
fi

# userinfo icindeki yuzde-kodlamayi cozer (%40 -> @)
urldecode() {
  local value="${1//+/ }"
  printf '%b' "${value//%/\\x}"
}

# 2) Oncelik: --basic-auth > SHOWIS_BASIC_AUTH_* > URL userinfo
if [[ -n "$BASIC_ARG" ]]; then
  [[ "$BASIC_ARG" == *:* ]] || usage_die "--basic-auth user:parola formatinda olmali"
  BASIC_USER="${BASIC_ARG%%:*}"
  BASIC_PASSWORD="${BASIC_ARG#*:}"
elif [[ -n "$BASIC_USER" || -n "$BASIC_PASSWORD" ]]; then
  if [[ -z "$BASIC_USER" || -z "$BASIC_PASSWORD" ]]; then
    die "edge basic auth eksik: SHOWIS_BASIC_AUTH_USER ve SHOWIS_BASIC_AUTH_PASSWORD birlikte verilmeli"
  fi
elif [[ -n "$URL_USERINFO" ]]; then
  BASIC_USER="$(urldecode "${URL_USERINFO%%:*}")"
  if [[ "$URL_USERINFO" == *:* ]]; then
    BASIC_PASSWORD="$(urldecode "${URL_USERINFO#*:}")"
  fi
fi

BASIC_AUTH_ENABLED=0
if [[ -n "$BASIC_USER" || -n "$BASIC_PASSWORD" ]]; then
  [[ -n "$BASIC_USER" ]] || die "edge basic auth: kullanici bos"
  [[ -n "$BASIC_PASSWORD" ]] || die "edge basic auth: parola bos"
  BASIC_AUTH_ENABLED=1
fi

# 3) Token politikasi
if [[ -z "$TOKEN" ]]; then
  if [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
    echo "UYARI: SHOWIS_API_TOKEN bos — edge basic auth devrede; /api/* proxy'si" >&2
    echo "       token'i sunucu tarafinda ekler (UI akisi). Dogrudan API cagrisi" >&2
    echo "       yapiliyorsa token gerekir." >&2
  elif [[ "$ALLOW_UNAUTH" -eq 1 ]]; then
    echo "UYARI: SHOWIS_API_TOKEN bos — kimlik dogrulamasiz devam ediliyor." >&2
    echo "       Bu yalniz auth kapali bir ortamda kabul edilebilir." >&2
  elif [[ "$DRY_RUN" -eq 1 ]]; then
    echo "UYARI: SHOWIS_API_TOKEN bos — --dry-run istek atmaz, token gerekmez." >&2
  else
    die "SHOWIS_API_TOKEN bos. Token verin, --basic-auth ile edge kimligi verin veya auth kapaliysa --allow-unauthenticated ekleyin."
  fi
fi

command -v curl >/dev/null 2>&1 || die "curl gerekli"
command -v jq >/dev/null 2>&1 || die "jq gerekli"

# 4) `Authorization` cakismasi:
#    Basic auth varsa curl Basic basligini kendisi uretir; token'i AYRICA
#    `Authorization: Bearer` ile gondermek Basic'i ezerdi (Caddy 401). Bu yuzden
#    token yalniz `X-API-Token` basligiyla gider. Basic auth yoksa geriye donuk
#    uyum icin Bearer basligi korunur.
HEADER_MODE="yok"
if [[ -n "$TOKEN" ]]; then
  if [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
    HEADER_MODE="X-API-Token (Bearer yok — edge Basic ile cakismasin)"
  else
    HEADER_MODE="X-API-Token + Authorization: Bearer"
  fi
elif [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
  HEADER_MODE="edge Basic (token yok)"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  cat <<PLAN
SMOKE PLANI (dry-run — hicbir istek atilmadi)
  taban url      : ${BASE_URL}
  mod            : ${MODE}
  api token      : $( [[ -n "$TOKEN" ]] && echo "var (redakte)" || echo "YOK" )
  edge basic auth: $( [[ "$BASIC_AUTH_ENABLED" -eq 1 ]] && echo "var — kullanici '${BASIC_USER}', parola ***" || echo "yok" )
  auth basliklari: ${HEADER_MODE}
  kimlik kaynagi : $( if [[ -n "$BASIC_ARG" ]]; then echo "--basic-auth"; elif [[ -n "${SHOWIS_BASIC_AUTH_USER:-}" ]]; then echo "SHOWIS_BASIC_AUTH_*"; elif [[ -n "$URL_USERINFO" ]]; then echo "--base-url userinfo"; else echo "yok"; fi )
  poll araligi   : ${INTERVAL} sn
  ust sinir      : ${TIMEOUT} sn
  cikti dizini   : ${OUT_DIR}
  case silinecek : $( [[ "$DELETE_CASE" -eq 1 ]] && echo "evet (--delete)" || echo "hayir (delil kalir)" )
  akis           : POST /api/v1/cases -> GET .../scan/estimate -> POST .../scan -> poll GET /api/v1/scans/{id}
  hedef          : Test Smoke (anonim, gercek kisi degil)
PLAN
  exit 0
fi

# --- kimlik bilgilerini curl'e dosyadan verme (argv/ps sizintisi yok) ------
CLEANUP_PATHS=()
cleanup() {
  local path
  for path in ${CLEANUP_PATHS[@]+"${CLEANUP_PATHS[@]}"}; do
    [[ -n "$path" ]] && rm -rf "$path"
  done
}
trap cleanup EXIT INT TERM

CURL_AUTH=()
if [[ -n "$TOKEN" || "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
  old_umask="$(umask)"
  umask 077
  creds_dir="$(mktemp -d "${TMPDIR:-/tmp}/showis-smoke.XXXXXX")"
  umask "$old_umask"
  CLEANUP_PATHS+=("$creds_dir")
  creds_file="${creds_dir}/curl.conf"
  : > "$creds_file"

  # Token: yalniz X-API-Token; Basic varken Authorization'a DOKUNMA.
  if [[ -n "$TOKEN" ]]; then
    printf 'header = "X-API-Token: %s"\n' "${TOKEN//[\"\\]/}" >> "$creds_file"
    if [[ "$BASIC_AUTH_ENABLED" -eq 0 ]]; then
      printf 'header = "Authorization: Bearer %s"\n' "${TOKEN//[\"\\]/}" >> "$creds_file"
    fi
  fi
  # Edge basic auth: curl `user = "u:p"` (varsayilan HTTP Basic semasi).
  if [[ "$BASIC_AUTH_ENABLED" -eq 1 ]]; then
    basic_user="${BASIC_USER//\\/\\\\}"
    basic_user="${basic_user//\"/\\\"}"
    basic_password="${BASIC_PASSWORD//\\/\\\\}"
    basic_password="${basic_password//\"/\\\"}"
    printf 'user = "%s:%s"\n' "$basic_user" "$basic_password" >> "$creds_file"
  fi
  CURL_AUTH=(--config "$creds_file")
fi

# --- HTTP yardimcilari -----------------------------------------------------
HTTP_CODE=""
HTTP_BODY=""

# http_request <METHOD> <PATH> [JSON_BODY] -> 0 if HTTP < 400
http_request() {
  local method="$1" path="$2" body="${3:-}" tmp code
  tmp="$(mktemp)"
  local args=(-sS --max-time "$CURL_MAX_TIME" -X "$method" -w '%{http_code}' -o "$tmp")
  if [[ ${#CURL_AUTH[@]} -gt 0 ]]; then
    args+=("${CURL_AUTH[@]}")
  fi
  if [[ -n "$body" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary "$body")
  fi
  if ! code="$(curl "${args[@]}" "${BASE_URL}${path}")"; then
    rm -f "$tmp"
    echo "HATA: ${method} ${path} — baglanti kurulamadi (${BASE_URL})" >&2
    return 1
  fi
  HTTP_CODE="$code"
  HTTP_BODY="$(cat "$tmp")"
  rm -f "$tmp"
  if [[ "$code" -ge 400 ]]; then
    echo "HATA: ${method} ${path} -> HTTP ${code}: $(printf '%s' "$HTTP_BODY" | head -c 400)" >&2
    return 1
  fi
  return 0
}

now_epoch() { date +%s.%N; }

echo "== Showis tarama smoke olcumu =="
echo "taban url: ${BASE_URL} | mod: ${MODE} | aralik: ${INTERVAL}sn | ust sinir: ${TIMEOUT}sn"

if ! http_request GET "/health"; then
  die "API saglik kontrolu basarisiz (${BASE_URL}/health). Servis ayakta mi?"
fi

if ! http_request GET "/api/v1/cases"; then
  die "GET /api/v1/cases basarisiz — kimlik dogrulama reddedildi.
       Edge (Caddy) basic auth istiyorsa: --basic-auth user:parola veya
       SHOWIS_BASIC_AUTH_USER + SHOWIS_BASIC_AUTH_PASSWORD verin.
       API token'i gerekiyorsa: --token / SHOWIS_API_TOKEN."
fi

# --- 1. gecici vaka --------------------------------------------------------
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CASE_PAYLOAD="$(jq -n --arg name "SMOKE ${STAMP}" --arg mode "$MODE" '{
  name: $name,
  purpose: ("SMOKE — otomatik tarama suresi olcumu (" + $mode + ")"),
  legal_basis: "self_check",
  notes: "QA smoke olcumu; hedef anonim (Test Smoke). Silmek icin --delete.",
  retention_days: 7,
  target: {first_name: "Test", last_name: "Smoke", hints: {}},
  seeds: []
}')"

http_request POST "/api/v1/cases" "$CASE_PAYLOAD" || die "vaka olusturulamadi"
CASE_ID="$(printf '%s' "$HTTP_BODY" | jq -r '.id')"
[[ -n "$CASE_ID" && "$CASE_ID" != "null" ]] || die "vaka id okunamadi"
echo "vaka      : ${CASE_ID}"

# --- 2. tahmin -------------------------------------------------------------
EST_SECONDS=""; EST_TASKS=""; EST_REQUESTS=""; EST_CONFIDENCE=""; EST_CONCURRENCY=""
if http_request GET "/api/v1/cases/${CASE_ID}/scan/estimate?mode=${MODE}"; then
  EST_SECONDS="$(printf '%s' "$HTTP_BODY" | jq -r '.seconds // empty')"
  EST_TASKS="$(printf '%s' "$HTTP_BODY" | jq -r '.tasks // empty')"
  EST_REQUESTS="$(printf '%s' "$HTTP_BODY" | jq -r '.requests // empty')"
  EST_CONFIDENCE="$(printf '%s' "$HTTP_BODY" | jq -r '.confidence // empty')"
  EST_CONCURRENCY="$(printf '%s' "$HTTP_BODY" | jq -r '.concurrency // empty')"
  echo "tahmin    : ${EST_SECONDS:-?} sn / ${EST_TASKS:-?} gorev / ${EST_REQUESTS:-?} istek (${EST_CONFIDENCE:-?})"
else
  echo "UYARI: tahmin ucu okunamadi — olcum yine yapilir." >&2
fi

# --- 3. taramayi baslat ----------------------------------------------------
STARTED_AT="$(now_epoch)"
http_request POST "/api/v1/cases/${CASE_ID}/scan" "$(jq -n --arg mode "$MODE" '{mode: $mode}')" \
  || die "tarama baslatilamadi (vaka silinmedi: ${CASE_ID})"
SCAN_ID="$(printf '%s' "$HTTP_BODY" | jq -r '.scan_id')"
[[ -n "$SCAN_ID" && "$SCAN_ID" != "null" ]] || die "scan_id okunamadi"
echo "tarama    : ${SCAN_ID} — poll ediliyor (${INTERVAL}sn)"

# --- 4. terminal duruma kadar poll ----------------------------------------
DEADLINE=$(( $(date +%s) + TIMEOUT ))
STATUS="queued"
STATS='{}'
TIMED_OUT=0
while :; do
  if ! http_request GET "/api/v1/scans/${SCAN_ID}"; then
    echo "UYARI: durum okunamadi, tekrar denenecek." >&2
  else
    STATUS="$(printf '%s' "$HTTP_BODY" | jq -r '.status // "unknown"')"
    STATS="$(printf '%s' "$HTTP_BODY" | jq -c '.stats // {}')"
    case "$STATUS" in
      completed|failed|cancelled) break ;;
    esac
  fi
  if [[ "$(date +%s)" -ge "$DEADLINE" ]]; then
    TIMED_OUT=1
    break
  fi
  sleep "$INTERVAL"
done
FINISHED_AT="$(now_epoch)"
ELAPSED="$(awk -v a="$STARTED_AT" -v b="$FINISHED_AT" 'BEGIN { printf "%.1f", b - a }')"

# --- 5. sayimlar -----------------------------------------------------------
FINDINGS_COUNT="null"
if http_request GET "/api/v1/cases/${CASE_ID}/findings"; then
  FINDINGS_COUNT="$(printf '%s' "$HTTP_BODY" | jq 'length')"
fi

EXECUTED="$(printf '%s' "$STATS" | jq -r '.executed_tasks // "null"')"
SKIPPED="$(printf '%s' "$STATS" | jq -r '.skipped_tasks // "null"')"
REQUESTS="$(printf '%s' "$STATS" | jq -r '.http_requests // "null"')"
OBSERVATIONS="$(printf '%s' "$STATS" | jq -r '.total_observations // "null"')"
ENTITIES="$(printf '%s' "$STATS" | jq -r '.total_entities // "null"')"

DELETED="false"
if [[ "$DELETE_CASE" -eq 1 ]]; then
  if http_request DELETE "/api/v1/cases/${CASE_ID}"; then
    DELETED="true"
  else
    echo "UYARI: vaka silinemedi: ${CASE_ID}" >&2
  fi
fi

# --- 6. JSON cikti --------------------------------------------------------
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR}/${STAMP}.json"
jq -n \
  --arg ts "$STAMP" \
  --arg base_url "$BASE_URL" \
  --arg mode "$MODE" \
  --arg case_id "$CASE_ID" \
  --arg scan_id "$SCAN_ID" \
  --arg status "$STATUS" \
  --argjson elapsed "$ELAPSED" \
  --argjson timeout "$TIMED_OUT" \
  --argjson stats "$STATS" \
  --arg est_seconds "${EST_SECONDS:-}" \
  --arg est_tasks "${EST_TASKS:-}" \
  --arg est_requests "${EST_REQUESTS:-}" \
  --arg est_confidence "${EST_CONFIDENCE:-}" \
  --arg est_concurrency "${EST_CONCURRENCY:-}" \
  --argjson executed "$EXECUTED" \
  --argjson skipped "$SKIPPED" \
  --argjson requests "$REQUESTS" \
  --argjson observations "$OBSERVATIONS" \
  --argjson entities "$ENTITIES" \
  --argjson findings "$FINDINGS_COUNT" \
  --argjson deleted "$DELETED" \
  --argjson has_reference "$HAS_REFERENCE" \
  --argjson reference "${REFERENCE:-0}" \
  '{
    timestamp_utc: $ts,
    base_url: $base_url,
    mode: $mode,
    case_id: $case_id,
    scan_id: $scan_id,
    status: $status,
    measured_seconds: $elapsed,
    timed_out: ($timeout == 1),
    estimate: {
      seconds: (if $est_seconds == "" then null else ($est_seconds | tonumber) end),
      tasks: (if $est_tasks == "" then null else ($est_tasks | tonumber) end),
      requests: (if $est_requests == "" then null else ($est_requests | tonumber) end),
      confidence: (if $est_confidence == "" then null else $est_confidence end),
      concurrency: (if $est_concurrency == "" then null else ($est_concurrency | tonumber) end)
    },
    stats: $stats,
    executed_tasks: $executed,
    skipped_tasks: $skipped,
    http_requests: $requests,
    observations: $observations,
    entities: $entities,
    findings: $findings,
    case_deleted: $deleted,
    # Oncesi/sonrasi kiyasi: --reference verilirse otomatik hesaplanir.
    reference_seconds: (if $has_reference == 1 then $reference else null end),
    delta_seconds: (
      if $has_reference == 1 then (($elapsed - $reference) | . * 10 | round / 10) else null end
    ),
    delta_percent: (
      if $has_reference == 1 and $reference > 0
      then ((($elapsed - $reference) / $reference * 100) | . * 10 | round / 10)
      else null end
    ),
    direction: (
      if $has_reference == 0 then null
      elif $elapsed < $reference then "faster"
      elif $elapsed > $reference then "slower"
      else "same" end
    )
  }' > "$OUT_FILE"

# --- 7. insan-okur ozet ---------------------------------------------------
pretty_duration() {
  awk -v s="$1" 'BEGIN {
    s = s + 0
    m = int(s / 60); r = s - m * 60
    if (m > 0) printf "%d dk %.1f sn", m, r; else printf "%.1f sn", s
  }'
}

echo
echo "== SONUC =="
echo "durum        : ${STATUS}$( [[ "$TIMED_OUT" -eq 1 ]] && echo " (UST SINIR ASILDI)" )"
echo "olculen sure : ${ELAPSED} sn ($(pretty_duration "$ELAPSED"))"
if [[ -n "$EST_SECONDS" ]]; then
  echo "tahmin       : ${EST_SECONDS} sn (${EST_CONFIDENCE:-?}, es zamanlilik ${EST_CONCURRENCY:-?})"
fi
if [[ -n "$REFERENCE" ]]; then
  awk -v now="$ELAPSED" -v ref="$REFERENCE" 'BEGIN {
    diff = now - ref
    pct = (ref > 0) ? (diff / ref) * 100 : 0
    dir = (diff < 0) ? "hizlandi" : (diff > 0 ? "yavasladi" : "ayni")
    printf "onceki/sonraki: %.1f sn -> %.1f sn (%+.1f sn, %+.1f%%, %s)\n", ref, now, diff, pct, dir
  }'
fi
echo "gorev        : ${EXECUTED} calisti${SKIPPED:+, ${SKIPPED} atlandi}"
echo "istek        : ${REQUESTS}"
echo "gozlem/varlik: ${OBSERVATIONS} / ${ENTITIES}"
echo "bulgu        : ${FINDINGS_COUNT}"
if [[ "$DELETED" == "true" ]]; then
  echo "vaka         : ${CASE_ID} (silindi — --delete)"
else
  echo "vaka         : ${CASE_ID} (silinmedi — delil)"
fi
echo "json         : ${OUT_FILE}"
printf '%s\n' "$(jq -c '.' "$OUT_FILE")"

if [[ "$TIMED_OUT" -eq 1 ]]; then
  echo "HATA: tarama ${TIMEOUT} sn icinde terminal duruma gelmedi." >&2
  exit 3
fi
if [[ "$STATUS" != "completed" ]]; then
  echo "HATA: tarama '${STATUS}' ile bitti — beklenen: completed" >&2
  exit 3
fi
exit 0

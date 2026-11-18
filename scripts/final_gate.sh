#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# final_gate.sh — "Final kapi": kod tabaninin saglikli olup olmadigini tek
# komutla soyler (T-335). CI'ya baglanabilir; cikis kodu 0 = HAZIR.
#
# Kullanim:
#   bash scripts/final_gate.sh              # statik + test + build (canliya dokunmaz)
#   bash scripts/final_gate.sh --live       # + uctan uca kabul testi (--strict)
#   bash scripts/final_gate.sh --quiet      # yalniz ozet tablosu (detay dokmez)
#
# Adimlar (sirasıyla, her biri ayri ayri olculur):
#   1 ruff check packages apps tests
#   2 mypy (brief.md'deki tam komut)
#   3 pytest -q (tam paket; faulthandler ayari pyproject'te)
#   4 apps/web: npx tsc --noEmit
#   5 apps/web: npm run build
#   6 make schemas + `git diff --exit-code apps/web/lib/types.ts` (jenerator el yazimi dosyayi bozmuyor mu)
#   7 docker compose config -q (ortam eksikse UYARI, FAIL degil)
#   8 infra/scripts/check-secrets.sh (sir taramasi; exit kodu politikasi)
#   9 (yalniz --live) scripts/e2e_acceptance.sh --strict
#
# Notlar:
#   * `set -uo pipefail` — `-e` YOK: tek hata script'i durdurmaz, karar sonda verilir.
#   * UYARI cikis kodunu etkilemez; yalnizca FAIL "HAZIR DEGIL" yapar.
#   * Cikis kodlari: 0 = HAZIR, 1 = HAZIR DEGIL (>=1 FAIL), 2 = kullanim hatasi.
# ---------------------------------------------------------------------------
set -uo pipefail
set +x   # sirlar trace'e dusmesin
# Yerel (tr_TR) ondalik ayirici printf/awk sayi ayristirmasini bozuyor:
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || { echo "HATA: proje kokune girilemedi: $ROOT" >&2; exit 2; }

PYTHON_BIN="${PYTHON:-.venv/bin/python}"
RUFF_BIN="${RUFF:-.venv/bin/ruff}"
MYPY_BIN="${MYPY:-.venv/bin/mypy}"

LIVE=0
QUIET=0

usage() {
  cat <<'USAGE'
final_gate.sh — Showis final kapi (go/no-go)

Kullanim:
  bash scripts/final_gate.sh [secenekler]

Secenekler:
  --live       Canli uctan uca kabul testini de kos (scripts/e2e_acceptance.sh --strict)
  --quiet      Yalniz ozet tablosu; hata detayi dokme
  -h, --help   Bu yardimi basar

Cikis kodlari:
  0 = HAZIR   1 = HAZIR DEGIL (en az bir FAIL)   2 = kullanim hatasi

Ortam:
  PYTHON, RUFF, MYPY   ikili yollarini degistirir (varsayilan: .venv/bin/...)
  GATE_SCAN_TIMEOUT    pytest icin ek sinir YOK (faulthandler pyproject'te)
  --live icin          SHOWIS_BASIC_AUTH_USER ve SHOWIS_BASIC_AUTH_PASSWORD
                       zorunlu; yoksa E2E 3. adimda 401 ile duser.
                       Yalnizca bu iki degiskeni verin. `infra/.env` dosyasini
                       `set -a; . ./.env` ile source ETMEYIN: USE_CELERY=1 ve
                       konteyner adresleri host pytest'ine sizar, Celery gercek
                       Redis'i arar ve paket dakikalarca retry'a duser.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live) LIVE=1; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Bilinmeyen arguman: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# --- renkler ---------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
  C_YELLOW=$'\033[33m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_DIM=""; C_BOLD=""
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/showis-gate.XXXXXX")"
cleanup() { rm -rf "$LOG_DIR"; }
trap cleanup EXIT INT TERM

STEP_N=0
PASS_N=0; FAIL_N=0; WARN_N=0; SKIP_N=0
declare -a R_NAME=() R_DUR=() R_STATE=() R_LOG=() R_NOTE=()

# Not: bicimlendirme awk icinde yapilir; bash printf'e sayi ayristirtmayiz
# (tr_TR ondalik ayirici "19,8" bekler ve "gecersiz sayi" hatasi verirdi).
fmt_secs() { awk -v ms="$1" 'BEGIN { printf "%.1f", ms / 1000 }'; }

# record <ad> <ms> <durum> <log-dosyasi|-> [not]
record() {
  local name="$1" ms="$2" state="$3" log="$4" note="${5:-}"
  R_NAME+=("$name"); R_DUR+=("$ms"); R_STATE+=("$state")
  R_LOG+=("$log"); R_NOTE+=("$note")
  case "$state" in
    PASS) PASS_N=$((PASS_N + 1)) ;;
    FAIL) FAIL_N=$((FAIL_N + 1)) ;;
    UYARI) WARN_N=$((WARN_N + 1)) ;;
    ATLANDI) SKIP_N=$((SKIP_N + 1)) ;;
  esac
  if [[ "$QUIET" -eq 0 ]]; then
    local color="$C_RESET"
    case "$state" in
      PASS) color="$C_GREEN" ;; FAIL) color="$C_RED" ;;
      UYARI) color="$C_YELLOW" ;; ATLANDI) color="$C_DIM" ;;
    esac
    printf '%-16s %s%-2s%s %-46s %6ss  %s\n' \
      "[$state]" "$color" "" "$C_RESET" "$name" "$(fmt_secs "$ms")" "$note"
  fi
}

# step <ad> <shell-komutu>
step() {
  local name="$1" cmd="$2" start end ms log rc
  STEP_N=$((STEP_N + 1))
  log="$LOG_DIR/step${STEP_N}.log"
  start=$(date +%s%N)
  bash -c "$cmd" >"$log" 2>&1
  rc=$?
  end=$(date +%s%N)
  ms=$(( (end - start) / 1000000 ))
  if [[ "$rc" -eq 0 ]]; then
    record "$name" "$ms" PASS "$log"
  else
    record "$name" "$ms" FAIL "$log" "exit=$rc"
  fi
  return 0
}

echo "${C_BOLD}== Showis final kapi (T-335) ==${C_RESET}"
echo "kok: ${ROOT}"
echo "mod: $([[ "$LIVE" -eq 1 ]] && echo 'statik + test + build + canli kabul' || echo 'statik + test + build (canli dokunulmaz)')"
echo
[[ "$QUIET" -eq 0 ]] && printf '%s%-16s %-46s %8s  %s%s\n' "$C_DIM" "DURUM" "ADIM" "SURE" "NOT" "$C_RESET"

TOTAL_START=$(date +%s%N)

# ---------------------------------------------------------------- 1) ruff
step "ruff check" \
  "$RUFF_BIN check packages apps tests"

# ---------------------------------------------------------------- 2) mypy
step "mypy (type check)" \
  "PYTHONPATH=apps/api:packages/contracts:packages/settings:packages/llm:packages/engine:packages/connectors \
   $MYPY_BIN packages/contracts/showis_contracts packages/settings packages/engine/showis_engine \
   packages/connectors/showis_connectors apps/api/showis"

# --------------------------------------------------------------- 3) pytest
step "pytest (tam paket)" \
  "PYTHONPATH=apps/api:packages/contracts:packages/settings:packages/llm:packages/engine:packages/connectors \
   .venv/bin/pytest"

# ------------------------------------------------------------ 4) web: tsc
step "web: tsc --noEmit" \
  "cd apps/web && npx --no-install tsc --noEmit"

# ---------------------------------------------------------- 5) web: build
step "web: npm run build" \
  "cd apps/web && npm run build"

# ------------------------------------- 6) schemas + types.ts drift kontrolu
# Iki farkli sebep ayirt edilir:
#   (a) jenerator el yazimi dosyayi YENIDEN YAZDI  -> T-334 ihlali
#   (b) dosya commit/index ile ayni degil           -> commit edilmemis degisiklik
# Her iki durumda da dosya calisma oncesi haline geri alinir (kapi agaci degistirmez).
STEP_N=$((STEP_N + 1))
schemas_log="$LOG_DIR/step${STEP_N}.log"
schemas_start=$(date +%s%N)
types_file="apps/web/lib/types.ts"
snapshot="$LOG_DIR/types.snapshot.ts"
before_hash=""
git diff --exit-code -- "$types_file" >/dev/null 2>&1
git_pre_rc=$?
[[ -f "$types_file" ]] && { cp "$types_file" "$snapshot"; before_hash="$(sha256sum "$types_file" | awk '{print $1}')"; }
{
  echo "== make schemas =="
  make schemas
} >"$schemas_log" 2>&1
make_rc=$?
after_hash=""
[[ -f "$types_file" ]] && after_hash="$(sha256sum "$types_file" | awk '{print $1}')"
git diff --exit-code -- "$types_file" >>"$schemas_log" 2>&1
git_rc=$?
schemas_ms=$(( ($(date +%s%N) - schemas_start) / 1000000 ))

if [[ "$make_rc" -ne 0 ]]; then
  [[ -f "$snapshot" ]] && cp "$snapshot" "$types_file"
  record "schemas + types.ts drift" "$schemas_ms" FAIL "$schemas_log" "make schemas exit=$make_rc"
elif [[ -n "$before_hash" && "$before_hash" != "$after_hash" ]]; then
  # Jenerator el yazimi dosyaya yazdi (T-334 sonrasi beklenen: yazmamali)
  [[ -f "$snapshot" ]] && cp "$snapshot" "$types_file"
  record "schemas + types.ts drift" "$schemas_ms" FAIL "$schemas_log" \
    "jenerator types.ts'i yeniden yazdi (geri alindi)"
elif [[ "$git_rc" -ne 0 ]]; then
  if [[ "$git_pre_rc" -ne 0 ]]; then
    # Dosya zaten commit/index'ten farkli (calisma agacinda degisiklik var) ama
    # jenerator ona DOKUNMADI: kapiyi bloklamaz, bilgi olarak raporlanir.
    record "schemas + types.ts drift" "$schemas_ms" UYARI "$schemas_log" \
      "types.ts'de commit edilmemis degisiklik (jenerator dokunmadi)"
  else
    record "schemas + types.ts drift" "$schemas_ms" FAIL "$schemas_log" \
      "make schemas sonrasi types.ts degisti"
  fi
else
  record "schemas + types.ts drift" "$schemas_ms" PASS "$schemas_log"
fi

# --------------------------------------------------- 7) compose config -q
STEP_N=$((STEP_N + 1))
compose_log="$LOG_DIR/step${STEP_N}.log"
compose_start=$(date +%s%N)
compose_note=""
if ! command -v docker >/dev/null 2>&1; then
  compose_ms=$(( ($(date +%s%N) - compose_start) / 1000000 ))
  record "docker compose config" "$compose_ms" ATLANDI "-" "docker yok"
else
  ( cd infra && docker compose config -q ) >"$compose_log" 2>&1
  compose_rc=$?
  compose_ms=$(( ($(date +%s%N) - compose_start) / 1000000 ))
  env_missing=0
  grep -qE 'variable is not set|required variable|is missing a value' "$compose_log" && env_missing=1
  if ! grep -q '^SHOWIS_API_TOKEN=' "$ROOT/infra/.env" 2>/dev/null && [[ -z "${SHOWIS_API_TOKEN:-}" ]]; then
    compose_note="ortam eksik: SHOWIS_API_TOKEN"
    env_missing=1
  fi
  if [[ "$compose_rc" -eq 0 ]]; then
    if [[ "$env_missing" -eq 1 ]]; then
      record "docker compose config" "$compose_ms" UYARI "$compose_log" "${compose_note:-ortam eksik (uyari)}"
    else
      record "docker compose config" "$compose_ms" PASS "$compose_log"
    fi
  elif [[ "$env_missing" -eq 1 ]]; then
    record "docker compose config" "$compose_ms" UYARI "$compose_log" "ortam eksik: $(grep -oE '[A-Z_]+' "$compose_log" | head -1)"
  else
    record "docker compose config" "$compose_ms" FAIL "$compose_log" "exit=$compose_rc"
  fi
fi

# ------------------------------------------------------- 8) secret tarama
step "secret tarama" \
  "bash infra/scripts/check-secrets.sh"

# ---------------------------------------------------- 9) canli kabul (opt)
if [[ "$LIVE" -eq 1 ]]; then
  step "canli E2E kabul (--strict)" \
    "bash scripts/e2e_acceptance.sh --strict"
else
  STEP_N=$((STEP_N + 1))
  record "canli E2E kabul (--strict)" 0 ATLANDI "-" "--live verilmedi"
fi

TOTAL_MS=$(( ($(date +%s%N) - TOTAL_START) / 1000000 ))

# ------------------------------------------------------------------ tablo
echo
echo "${C_BOLD}== ADIM TABLOSU ==${C_RESET}"
printf '%-4s %-46s %8s  %-10s %s\n' "NO" "ADIM" "SURE" "SONUC" "NOT"
printf '%-4s %-46s %8s  %-10s %s\n' "----" "----------------------------------------------" \
  "--------" "----------" "---"
for i in "${!R_NAME[@]}"; do
  color="$C_RESET"
  case "${R_STATE[$i]}" in
    PASS) color="$C_GREEN" ;; FAIL) color="$C_RED" ;;
    UYARI) color="$C_YELLOW" ;; ATLANDI) color="$C_DIM" ;;
  esac
  printf '%-4s %-46s %7ss  %s%-10s%s %s\n' \
    "$((i + 1))" "${R_NAME[$i]}" "$(fmt_secs "${R_DUR[$i]}")" \
    "$color" "${R_STATE[$i]}" "$C_RESET" "${R_NOTE[$i]}"
done
echo
printf 'toplam sure: %ss (butce: 180s%s)\n' "$(fmt_secs "$TOTAL_MS")" \
  "$( [[ "$LIVE" -eq 1 ]] && echo ' + canli kosu' )"
echo "PASS=${PASS_N}  FAIL=${FAIL_N}  UYARI=${WARN_N}  ATLANDI=${SKIP_N}"

# --------------------------------------------------- hata detaylari
if [[ "$FAIL_N" -gt 0 && "$QUIET" -eq 0 ]]; then
  echo
  echo "${C_BOLD}== HATA DETAYLARI (son 20 satir) ==${C_RESET}"
  for i in "${!R_NAME[@]}"; do
    [[ "${R_STATE[$i]}" == "FAIL" ]] || continue
    echo
    echo "${C_RED}--- ${R_NAME[$i]} (${R_LOG[$i]}) ---${C_RESET}"
    tail -20 "${R_LOG[$i]}" 2>/dev/null || echo "(log yok)"
  done
elif [[ "$FAIL_N" -gt 0 && "$QUIET" -eq 1 ]]; then
  echo
  echo "basarisiz adimlar: $(for i in "${!R_NAME[@]}"; do [[ "${R_STATE[$i]}" == "FAIL" ]] && printf '%s, ' "${R_NAME[$i]}"; done | sed 's/, $//')"
fi

echo
if [[ "$FAIL_N" -gt 0 ]]; then
  printf '%sSONUÇ: HAZIR DEĞİL (%s hata)%s\n' "$C_RED$C_BOLD" "$FAIL_N" "$C_RESET"
  exit 1
fi
printf '%sSONUÇ: HAZIR%s' "$C_GREEN$C_BOLD" "$C_RESET"
[[ "$WARN_N" -gt 0 ]] && printf ' %s(%s uyari)%s' "$C_YELLOW" "$WARN_N" "$C_RESET"
echo
exit 0

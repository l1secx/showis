#!/usr/bin/env bash
# Basit sir tarayicisi (T-304 / T-325). gitleaks/trufflehog yoksa devreye girer.
#
# Kullanim:
#   bash infra/scripts/check-secrets.sh [--strict] [KOK_DIZIN]
#
#   --strict : test yollarindaki bulgular da hata sayilir (varsayilan: uyari).
#
# Cikis kodu:
#   0 = bulgu yok VEYA yalnizca test yollarinda allowlist disi yer tutucu (uyari)
#   1 = (--strict disinda) test disi bir yolda bulgu  VEYA  --strict ile herhangi bir bulgu
#
# Nasil calisir:
#   * Dizin bir git deposuysa **yalniz izlenen (tracked) dosyalar** `git grep` ile taranir.
#   * Git deposu degilse dosya sistemi taranir; `.env`/`*.env` muaf tutulur ve uyarilir.
#   * Desen tabanlidir (entropi analizi yok) — gitleaks'in yerini tutmaz.
#   * Test yollarindaki bilinen yer tutucular `infra/secret-scan-allowlist.txt` ile muaf
#     tutulur; test dosyasindaki yuksek entropili token'lar yakalanmaya devam eder.
#   * Bulgular KISMEN MASKELENIR; sadece gorunum icin.
set -u

# LC_ALL=C (T-325): tr_TR.UTF-8 gibi yerellerde `[A-Za-z]` araligi bozuluyor ve
# token'lar SESSIZCE kaciyor (olculdu: ghp_ABCDEF... tr_TR'de eslesmiyor, C'de eslesiyor).
export LC_ALL=C

STRICT=0
ROOT=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) ROOT="$arg" ;;
  esac
done
# SCRIPT_DIR, cd'den ONCE hesaplanmali (goreli $0 ile baska dizinden cagrilabilir).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
cd "$ROOT" || exit 2

ALLOWLIST="${SECRET_SCAN_ALLOWLIST:-$SCRIPT_DIR/../secret-scan-allowlist.txt}"
SELF="$(basename "$0")"

# Test yolu mu? (allowlist yalnizca buralarda gecerli)
is_test_path() {
  printf '%s' "$1" | grep -qE '(^|/)tests?/' && return 0
  printf '%s' "$1" | grep -qE '(^|/)test_[^/]*\.py$' && return 0
  printf '%s' "$1" | grep -qE '(^|/)[^/]*_test\.py$' && return 0
  return 1
}

# Allowlist'teki desenler (yorumlar ve bos satirlar atilir)
ALLOW_PATTERNS=()
if [ -r "$ALLOWLIST" ]; then
  while IFS= read -r line; do
    pat="${line%%#*}"
    pat="$(printf '%s' "$pat" | sed 's/[[:space:]]*$//')"
    [ -n "$pat" ] && ALLOW_PATTERNS+=("$pat")
  done < "$ALLOWLIST"
fi

allowed_token() { # <token>
  local tok="$1" pat
  for pat in "${ALLOW_PATTERNS[@]:-}"; do
    [ -n "$pat" ] || continue
    printf '%s' "$tok" | grep -qE -- "$pat" && return 0
  done
  return 1
}

# ad<TAB>ERE
PATTERNS=(
  $'AWS access key\tAKIA[0-9A-Z]{16}'
  $'GitHub token\tgh[pousr]_[A-Za-z0-9]{20,}'
  $'Slack token\txox[baprs]-[A-Za-z0-9-]{10,}'
  $'Google API key\tAIza[0-9A-Za-z_-]{35}'
  $'Stripe live key\t[sr]k_live_[0-9A-Za-z]{20,}'
  $'Private key block\t-----BEGIN ([A-Z]+ )?PRIVATE KEY-----'
  $'JWT\t\\beyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.'
  $'Credential URL\t://[^/[:space:]@\"$]+:[^/[:space:]@\"]{6,}@'
  $'Showis API token degeri\tSHOWIS_API_TOKEN[[:space:]]*=[[:space:]]*[0-9a-zA-Z_-]{24,}'
  $'bcrypt hash\t\\$2[aby]\\$[0-9]{2}\\$[./A-Za-z0-9]{53}'
  $'Generic secret assignment\t([Aa][Pp][Ii][_-]?[Kk][Ee][Yy]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]|[Tt][Oo][Kk][Ee][Nn])[[:space:]]*[:=][[:space:]]*[\"'\'']?[A-Za-z0-9+/_-]{32,}[\"'\'']?'
)

mask() { awk '{ if (length($0) > 24) print substr($0, 1, 4) "***" substr($0, length($0) - 3); else print "***" }'; }

# Sablon / ornek degerler: gercek sir degil (yol fark etmeksizin).
is_placeholder() {
  case "$1" in
    *'${'*) return 0 ;;                  # compose/ortam degiskeni sablonu
    *'$VAR'*|*'$('*) return 0 ;;
    *change-me*|*changeme*|*CHANGE_ME*) return 0 ;;
    *example*|*Example*|*EXAMPLE*) return 0 ;;
    *dummy*|*Dummy*|*DUMMY*|*placeholder*|*PLACEHOLDER*) return 0 ;;
    *'{'*'}'*) return 0 ;;               # f-string / sablon ({password}, {user} …)
    *parola*|*Parola*|*PAROLA*) return 0 ;;  # repoda yaygin yer tutucu kelime (TR)
    *'***'*) return 0 ;;                 # zaten maskeli
    *'<...>'*|*'<'*'>'*) return 0 ;;
    *your_*|*YOUR_*|*xxxx*|*XXXX*) return 0 ;;
    *localhost*|*127.0.0.1*) return 0 ;;
    *showis:change-me*|*showis:password*|*showis:showis*) return 0 ;;
    *sk-secret-value*) return 0 ;;       # eski test fixture
    *'=***'*|*'=""'*|*"=''"*) return 0 ;;
  esac
  return 1
}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MODE="git (izlenen dosyalar)"
else
  MODE="dosya sistemi (.env muaf)"
fi

echo "check-secrets: kok=$ROOT mod=$MODE strict=$STRICT"
if [ "$MODE" = "dosya sistemi (.env muaf)" ]; then
  echo "  NOT: .env / *.env taranmadi. Bu dosyalar .gitignore'da olmali (gercek sirlar orada)."
fi
[ "${#ALLOW_PATTERNS[@]}" -gt 0 ] && echo "  allowlist: ${#ALLOW_PATTERNS[@]} desen ($ALLOWLIST)"

findings=0
warnings=0
allowed=0
for entry in "${PATTERNS[@]}"; do
  name="${entry%%$'\t'*}"
  ere="${entry#*$'\t'}"
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    file="${hit%%:*}"
    rest="${hit#*:}"
    line="${rest%%:*}"
    content="${rest#*:}"
    token="$(printf '%s' "$content" | grep -oE -- "$ere" | head -1)"
    [ -n "$token" ] || token="$content"

    # 1) Genel yer tutucu (yol fark etmeksizin)
    if is_placeholder "$content"; then
      continue
    fi
    # 2) Test yolundaki allowlist desenleri
    if is_test_path "$file" && allowed_token "$token"; then
      printf '[IZINLI] %-32s %s:%s  %s\n' "$name" "$file" "$line" "$(printf '%s' "$token" | mask)"
      allowed=$((allowed + 1))
      continue
    fi
    # 3) Test yolundaki allowlist disi bulgu: varsayilan uyari, --strict ile hata
    if is_test_path "$file"; then
      printf '[UYARI] %-33s %s:%s  %s (allowlist disi test degeri)\n' "$name" "$file" "$line" "$(printf '%s' "$token" | mask)"
      warnings=$((warnings + 1))
      continue
    fi
    printf '[BULGU] %-32s %s:%s  %s\n' "$name" "$file" "$line" "$(printf '%s' "$token" | mask)"
    findings=$((findings + 1))
  done < <(
    if [ "$MODE" = "git (izlenen dosyalar)" ]; then
      git grep -InE -e "$ere" -- . 2>/dev/null | sed 's|^\./||'
    else
      grep -rInE \
        --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.next \
        --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=.orchestrator \
        --exclude-dir=.secrets --exclude-dir=dist --exclude-dir=build \
        --exclude='*.lock' --exclude='package-lock.json' --exclude="$SELF" \
        --exclude='.env' --exclude='.env.*' --exclude='*.env' \
        -e "$ere" . 2>/dev/null | sed 's|^\./||'
    fi
  )
done

echo
echo "check-secrets: $findings bulgu, $warnings uyari, $allowed allowlist"
if [ "$findings" -gt 0 ]; then
  echo "SONUC: FAIL — gercek bulgu(lar) var"
  exit 1
fi
if [ "$STRICT" -eq 1 ] && [ "$warnings" -gt 0 ]; then
  echo "SONUC: FAIL (--strict) — test yollarinda allowlist disi deger"
  exit 1
fi
if [ "$warnings" -gt 0 ]; then
  echo "SONUC: PASS (uyarilar var) — test yollarindaki degerleri inceleyin"
  exit 0
fi
echo "SONUC: PASS"
exit 0

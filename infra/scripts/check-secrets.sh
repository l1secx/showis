#!/usr/bin/env bash
# Basit sir tarayicisi (T-304 / SEC-17). gitleaks/trufflehog yoksa devreye girer.
#
# Kullanim:
#   bash infra/scripts/check-secrets.sh [KOK_DIZIN]
#
# Cikis kodu: 0 = bulgu yok, 1 = bulgu var (CI'i dusurur).
#
# Nasil calisir:
#   * Dizin bir git deposuysa **yalniz izlenen (tracked) dosyalar** `git grep` ile
#     taranir. Boylece `.env` gibi git disi tutulan dosyalar taranmaz (dogru davranis:
#     risk, sirrin commit edilmesidir).
#   * Git deposu degilse dosya sistemi taranir; `.env`/`*.env` muaf tutulur ve bu
#     durum ekrana yazilir.
#   * Desen tabanlidir (entropi analizi yok) — gitleaks'in yerini tutmaz.
#   * Bulgular KISMEN MASKELENIR; sadece gorunum icin.
set -u

ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" || exit 2

SELF="$(basename "$0")"

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
  $'Generic secret assignment\t(api[_-]?key|apikey|secret|password|passwd|token)[[:space:]]*[:=][[:space:]]*[\"'\'']?[A-Za-z0-9+/_-]{32,}[\"'\'']?'
)

mask() { awk '{ if (length($0) > 24) print substr($0, 1, 4) "***" substr($0, length($0) - 3); else print "***" }'; }

# Sablon / ornek degerler: gercek sir degil.
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
    *sk-secret-value*) return 0 ;;       # test fixture
    *'=***'*|*'=""'*|*"=''"*) return 0 ;;
  esac
  return 1
}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MODE="git (izlenen dosyalar)"
else
  MODE="dosya sistemi (.env muaf)"
fi

echo "check-secrets: kok=$ROOT mod=$MODE"
if [ "$MODE" = "dosya sistemi (.env muaf)" ]; then
  echo "  NOT: .env / *.env taranmadi. Bu dosyalar .gitignore'da olmali (gercek sirlar orada)."
fi

findings=0
for entry in "${PATTERNS[@]}"; do
  name="${entry%%$'\t'*}"
  ere="${entry#*$'\t'}"
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    file="${hit%%:*}"
    rest="${hit#*:}"
    line="${rest%%:*}"
    content="${rest#*:}"
    if is_placeholder "$content"; then
      continue
    fi
    printf '[BULGU] %-32s %s:%s  %s\n' "$name" "$file" "$line" "$(printf '%s' "$content" | mask)"
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
if [ "$findings" -eq 0 ]; then
  echo "check-secrets: bulgu yok"
  exit 0
fi
echo "check-secrets: $findings potansiyel sir bulgusu — inceleyin"
exit 1

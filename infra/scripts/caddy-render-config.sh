#!/bin/sh
# Caddy yapilandirmasini ortam degiskenlerine gore render eder (T-111, T-113, T-319).
#
# Caddyfile'da kosullu blok yok; iki isaret satiri runtime'da degistirilir:
#
#   __BASIC_AUTH_PLACEHOLDER__  ->  SHOWIS_CADDY_USER + SHOWIS_CADDY_PASSWORD_HASH
#                                   doluysa basicauth blogu, degilse HICBIR SEY
#   __HOST_CHECK_PLACEHOLDER__  ->  SHOWIS_ALLOWED_HOSTS doluysa `@bad_host ... + respond 421`
#                                   blogu, degilse HICBIR SEY
#
# Erisim modeli: basic auth, Caddyfile'daki `@gate` matcher'i ile **/health disindaki
# tum yollara** uygulanir — UI ve /api/* dahil.
#   - /health : muaftir (container healthcheck; token/basic auth istemez)
#   - /api/*  : korumali. /api/* Next proxy'sine gider ve proxy X-API-Token'i sunucu
#               tarafinda ekler; bu yuzden token edge'de ayirt edici degildir.
#   - SSE (text/event-stream): ayni origin'de tarayici basic auth kimligini EventSource
#               istegine otomatik ekler (kimlik onbellegi).
#
# Kullanim: /bin/sh caddy-render-config.sh   (container command olarak)
set -eu

SRC="${CADDY_SOURCE:-/etc/caddy/Caddyfile}"
OUT="${CADDY_RENDERED:-/tmp/Caddyfile.rendered}"
AUTH_MARKER="__BASIC_AUTH_PLACEHOLDER__"
HOST_MARKER="__HOST_CHECK_PLACEHOLDER__"

if [ ! -r "$SRC" ]; then
    echo "[caddy-render] HATA: kaynak Caddyfile okunamadi: $SRC" >&2
    exit 1
fi

RENDER_AUTH=0
if [ -n "${SHOWIS_CADDY_USER:-}" ] && [ -n "${SHOWIS_CADDY_PASSWORD_HASH:-}" ]; then
    RENDER_AUTH=1
fi

RENDER_HOSTS=0
HOST_ARGS=""
if [ -n "${SHOWIS_ALLOWED_HOSTS:-}" ]; then
    # Virgulle ayrilmis liste -> "host a b c" matcher argumanlari
    HOST_ARGS="$(printf '%s' "$SHOWIS_ALLOWED_HOSTS" | tr ',' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')"
    if [ -n "$HOST_ARGS" ]; then
        RENDER_HOSTS=1
    fi
fi

: > "$OUT"
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        *"$AUTH_MARKER"*)
            if [ "$RENDER_AUTH" = "1" ]; then
                # Parola HASH olarak beklenir (bcrypt); duz parola kabul edilmez.
                # NOT: Caddy 2.7 bu direktifi `basicauth` adiyla sunar (2.8+: basic_auth).
                printf '\tbasicauth @gate {\n\t\t%s %s\n\t}\n' \
                    "$SHOWIS_CADDY_USER" "$SHOWIS_CADDY_PASSWORD_HASH" >> "$OUT"
            fi
            ;;
        *"$HOST_MARKER"*)
            if [ "$RENDER_HOSTS" = "1" ]; then
                # Yalniz beklenen Host'lara izin ver; digerleri 421 Misdirected Request.
                # NOT: basicauth direktif sirasinda once calisir; kimliksiz istek 401 alir,
                # kimlikli istekte yanlis Host 421 ile reddedilir.
                # handle blogu, diger handle'lardan ONCE ve ayri bir grupta calisir
                # (site seviyesinde `respond` handle'lardan sonra geldigi icin yetmiyordu).
                printf '\t@bad_host not host %s\n\thandle @bad_host {\n\t\trespond "Misdirected Request" 421\n\t}\n' \
                    "$HOST_ARGS" >> "$OUT"
            fi
            ;;
        *)
            printf '%s\n' "$line" >> "$OUT"
            ;;
    esac
done < "$SRC"

if [ -n "${SHOWIS_CADDY_PASSWORD_HASH:-}" ] && [ -z "${SHOWIS_CADDY_USER:-}" ]; then
    echo "[caddy-render] UYARI: SHOWIS_CADDY_PASSWORD_HASH var ama SHOWIS_CADDY_USER bos; basic auth DEVRE DISI" >&2
fi

if [ "$RENDER_AUTH" = "1" ]; then
    echo "[caddy-render] basic auth ETKIN (@gate: /health disindaki tum yollar — UI + /api/*), kullanici: ${SHOWIS_CADDY_USER}" >&2
else
    echo "[caddy-render] UYARI: SHOWIS_CADDY_USER/SHOWIS_CADDY_PASSWORD_HASH tanimli degil; basic auth DEVRE DISI (edge kimlik dogrulamasi yok — API yalniz X-API-Token katmanina bagli)" >&2
fi

if [ "$RENDER_HOSTS" = "1" ]; then
    echo "[caddy-render] Host allowlist ETKIN: ${HOST_ARGS} (digerleri 421)" >&2
else
    echo "[caddy-render] UYARI: SHOWIS_ALLOWED_HOSTS tanimli degil; Host dogrulamasi DEVRE DISI (421 kontrolu yok)" >&2
fi

exec caddy run --config "$OUT" --adapter caddyfile

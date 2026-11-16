#!/bin/sh
# Caddy yapilandirmasini ortam degiskenlerine gore render eder (T-111, T-113).
#
# Neden: Caddyfile'da kosullu blok yok. Basic auth'u "env yoksa tamamen devre disi"
# birakabilmek icin isaret satiri runtime'da degistirilir:
#
#   __BASIC_AUTH_PLACEHOLDER__  ->  SHOWIS_CADDY_USER + SHOWIS_CADDY_PASSWORD_HASH
#                                   doluysa basicauth blogu, degilse HICBIR SEY
#
# Kapsam (T-113): basic auth, Caddyfile'daki `@gate` matcher'i ile **/health disindaki
# tum yollara** uygulanir — UI ve /api/* dahil.
#   - /health : muaftir (container healthcheck; token/basic auth istemez)
#   - /api/*  : korumali. Gerekce: /api/* Next proxy'sine gider ve proxy, X-API-Token'i
#               her istege sunucu tarafinda ekler; bu yuzden token edge'de ayirt edici
#               degildir, tek gercek kapi edge kimlik dogrulamasidir.
#   - SSE (text/event-stream): ayni origin'de tarayici, basic auth kimlik bilgisini
#               EventSource istegine otomatik ekler (kimlik onbellegi).
#
# Kullanim: /bin/sh caddy-render-config.sh   (container command olarak)
set -eu

SRC="${CADDY_SOURCE:-/etc/caddy/Caddyfile}"
OUT="${CADDY_RENDERED:-/tmp/Caddyfile.rendered}"
MARKER="__BASIC_AUTH_PLACEHOLDER__"

if [ ! -r "$SRC" ]; then
    echo "[caddy-render] HATA: kaynak Caddyfile okunamadi: $SRC" >&2
    exit 1
fi

RENDER_AUTH=0
if [ -n "${SHOWIS_CADDY_USER:-}" ] && [ -n "${SHOWIS_CADDY_PASSWORD_HASH:-}" ]; then
    RENDER_AUTH=1
fi

: > "$OUT"
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        *"$MARKER"*)
            if [ "$RENDER_AUTH" = "1" ]; then
                # Parola HASH olarak beklenir (bcrypt); duz parola kabul edilmez.
                # NOT: Caddy 2.7 bu direktifi `basicauth` adiyla sunar (2.8+: basic_auth).
                printf '\tbasicauth @gate {\n\t\t%s %s\n\t}\n' \
                    "$SHOWIS_CADDY_USER" "$SHOWIS_CADDY_PASSWORD_HASH" >> "$OUT"
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

exec caddy run --config "$OUT" --adapter caddyfile

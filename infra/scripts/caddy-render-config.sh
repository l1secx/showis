#!/bin/sh
# Caddy yapilandirmasini ortam degiskenlerine gore render eder (T-111, T-113, T-319).
#
# Caddyfile'da kosullu blok yok; iki isaret satiri runtime'da degistirilir:
#
#   __BASIC_AUTH_PLACEHOLDER__  ->  SHOWIS_CADDY_USER + SHOWIS_CADDY_PASSWORD_HASH
#                                   doluysa basicauth blogu, degilse HICBIR SEY
#   __HOST_CHECK_PLACEHOLDER__  ->  SHOWIS_ALLOWED_HOSTS doluysa `@bad_host ... + respond 421`
#                                   blogu, degilse HICBIR SEY
#   __TLS_HOSTS_PLACEHOLDER__   ->  SHOWIS_TLS_HOSTS (virgulle) -> `https://host:8443, ...`
#                                   Varsayilan: localhost,127.0.0.1
#
# Erisim modeli: basic auth, Caddyfile'daki `@gate` matcher'i ile **statik
# Next.js parçaları ve /health dışındaki** yollara uygulanir — HTML/RSC ve /api/*.
#   - /_next/static/*, /icon.svg, /favicon.ico : muaftir (yalniz kod/görsel;
#     her chunk'ta bcrypt çağrısı sayfa geçişini kilitliyordu).
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
TLS_MARKER="__TLS_HOSTS_PLACEHOLDER__"
ACTOR_MARKER="__ACTOR_HEADER_PLACEHOLDER__"
TLS_HOSTS="${SHOWIS_TLS_HOSTS:-localhost,127.0.0.1}"
TLS_ADDRS="$(printf '%s\n' "$TLS_HOSTS" | tr ',' '\n' | sed 's/^ *//; s/ *$//' | while read -r host; do [ -n "$host" ] && printf 'https://%s:8443, ' "$host"; done | sed 's/, $//')"

if [ ! -r "$SRC" ]; then
    echo "[caddy-render] HATA: kaynak Caddyfile okunamadi: $SRC" >&2
    exit 1
fi

RENDER_AUTH=0
# SHOWIS_CADDY_USERS: newline/; ayrılmış "kullanici:bcrypt_hash" listesi.
# Geriye uyum: SHOWIS_CADDY_USER + SHOWIS_CADDY_PASSWORD_HASH tek kullanıcı.
if [ -n "${SHOWIS_CADDY_USERS:-}" ]; then
    RENDER_AUTH=1
elif [ -n "${SHOWIS_CADDY_USER:-}" ] && [ -n "${SHOWIS_CADDY_PASSWORD_HASH:-}" ]; then
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
                if [ "${SHOWIS_EDGE_FORWARD_AUTH:-0}" = "1" ]; then
                    # 401 audit + hız sınırı API'de; parola bcrypt doğrulaması da orada.
                    printf '\tforward_auth @gate api:8000 {\n\t\turi /api/v1/auth/edge\n\t\theader_up Authorization {http.request.header.Authorization}\n\t\tcopy_headers X-Actor\n\t}\n' >> "$OUT"
                else
                    printf '\tbasicauth @gate {\n' >> "$OUT"
                    if [ -n "${SHOWIS_CADDY_USERS:-}" ]; then
                        printf '%s\n' "$SHOWIS_CADDY_USERS" \
                            | tr ';' '\n' \
                            | while IFS=: read -r user hash; do
                                [ -n "$user" ] && [ -n "$hash" ] || continue
                                printf '\t\t%s %s\n' "$user" "$hash" >> "$OUT"
                            done
                    else
                        printf '\t\t%s %s\n' \
                            "$SHOWIS_CADDY_USER" "$SHOWIS_CADDY_PASSWORD_HASH" >> "$OUT"
                    fi
                    printf '\t}\n' >> "$OUT"
                fi
            fi
            ;;
        *"$ACTOR_MARKER"*)
            if [ "${SHOWIS_EDGE_FORWARD_AUTH:-0}" = "1" ]; then
                # forward_auth 2xx yanıtındaki X-Actor'u isteğe kopyalar.
                printf '\t\t\theader_up X-Actor {http.request.header.X-Actor}\n' >> "$OUT"
            else
                # basicauth modunda Caddy kullanıcı kimliği.
                printf '\t\t\theader_up X-Actor {http.auth.user.id}\n' >> "$OUT"
            fi
            ;;
        *"$TLS_MARKER"*)
            printf '%s {\n' "$TLS_ADDRS" >> "$OUT"
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

if [ -n "${SHOWIS_CADDY_PASSWORD_HASH:-}" ] && [ -z "${SHOWIS_CADDY_USER:-}" ] && [ -z "${SHOWIS_CADDY_USERS:-}" ]; then
    echo "[caddy-render] UYARI: SHOWIS_CADDY_PASSWORD_HASH var ama kullanici yok; basic auth DEVRE DISI" >&2
fi

if [ "$RENDER_AUTH" = "1" ]; then
    if [ "${SHOWIS_EDGE_FORWARD_AUTH:-0}" = "1" ]; then
        echo "[caddy-render] edge auth ETKIN (forward_auth -> api /api/v1/auth/edge)" >&2
    else
    echo "[caddy-render] basic auth ETKIN (@gate: HTML/RSC + /api/*; statik ve /health muaf)" >&2
    fi >&2
else
    echo "[caddy-render] UYARI: Caddy kullanıcıları tanimli degil; basic auth DEVRE DISI (edge kimlik dogrulamasi yok)" >&2
fi

echo "[caddy-render] TLS (8443): ${TLS_HOSTS}" >&2

if [ "$RENDER_HOSTS" = "1" ]; then
    echo "[caddy-render] Host allowlist ETKIN: ${HOST_ARGS} (digerleri 421)" >&2
else
    echo "[caddy-render] UYARI: SHOWIS_ALLOWED_HOSTS tanimli degil; Host dogrulamasi DEVRE DISI (421 kontrolu yok)" >&2
fi

exec caddy run --config "$OUT" --adapter caddyfile

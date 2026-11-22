#!/usr/bin/env bash
# Geliştirme ortamını başlatır: API (uvicorn --reload) + Web (next dev).
#
# Bu betik `make dev` tarafından çağrılır. Amacı, sessiz yarım kalmış bir
# başlangıç yerine **anlaşılır** bir başlangıç sağlamak:
#   1. Kök `.env` yüklenir (boş değerler atlanır — boş `DATABASE_URL`,
#      API'nin varsayılanını ezer ve bağlantıyı kırardı).
#   2. Postgres/Redis erişilebilirliği önceden kontrol edilir; kapalıysa
#      yığın izi yerine tek satır çözüm gösterilir.
#   3. İki süreçten biri ölürse diğeri de kapatılır (portta öksüz süreç kalmaz).
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

# --- 0. NixOS: native uzantılar için libstdc++ ------------------------------
# pip'in indirdiği greenlet (SQLAlchemy asyncio) sistem kütüphane yolunda
# olmayan `libstdc++.so.6` arar. Sistem derleyicisinin dizinini yalnızca
# gerçekten bir yol döndüğünde ekle (standart dağıtımlarda blok atlanır).
# Aynı koruma Makefile'da da var: `make test` gibi hedefler doğrudan
# `.venv/bin/python` çağırdığı için oradaki export gerekli.
_libstdcxx="$(cc -print-file-name=libstdc++.so.6 2>/dev/null || true)"
if [[ "$_libstdcxx" == /* ]]; then
  export LD_LIBRARY_PATH="$(dirname "$_libstdcxx")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# --- 1. .env yükle (yalnız dolu değerler) -----------------------------------
if [[ -f .env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ -z "$key" || "$key" == "$line" ]] && continue
    # Boş değer: mevcut ortam değişkenini/varsayılanı ezmesin.
    [[ -z "${value//[[:space:]]/}" ]] && continue
    export "$key=$value"
  done < .env
fi

# --- 1b. Docker kimlik bilgilerini yerel adreslere bağla --------------------
# `infra/.env` docker compose içindir: POSTGRES_* değerleri ve servis adları
# (`postgres`, `redis`) yalnızca compose ağında çözülür. Yerel geliştirmede
# aynı veritabanına `localhost` üzerinden bağlanılır.
if [[ -f infra/.env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ -z "$key" || "$key" == "$line" ]] && continue
    [[ -z "${value//[[:space:]]/}" ]] && continue
    # Yalnızca henüz tanımlı olmayanları al (kabuk/`.env` önceliklidir).
    [[ -n "${!key:-}" ]] && continue
    export "$key=$value"
  done < infra/.env
fi

# Boş/verilmemişse localhost adresini kur. Parola URL için kodlanır.
if [[ -z "${DATABASE_URL:-}" && -n "${POSTGRES_PASSWORD:-}" ]]; then
  _enc_pw="$(printf '%s' "$POSTGRES_PASSWORD" | sed -e 's/%/%25/g' -e 's/@/%40/g' -e 's/:/%3A/g' -e 's#/#%2F#g' -e 's/?/%3F/g' -e 's/#/%23/g')"
  DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-showis}:${_enc_pw}@localhost:5432/${POSTGRES_DB:-showis}"
  export DATABASE_URL
fi
# `redis` servis adı host'ta çözülmez; yerel karşılığına çevir.
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
REDIS_URL="${REDIS_URL//@redis:/@localhost:}"; REDIS_URL="${REDIS_URL//:\/\/redis:/://localhost:}"
export REDIS_URL

# Web sunucusu API token'ını sunucu tarafında okur (tarayıcıya verilmez).
if [[ -n "${SHOWIS_API_TOKEN:-}" ]]; then
  export SHOWIS_API_TOKEN
fi
export API_URL="${API_URL:-http://localhost:8000}"

# --- 2. Bağımlılık ön kontrolü ---------------------------------------------
port_open() {
  # bash'in /dev/tcp desteğiyle TCP yoklaması (nc'e bağımlı değil).
  (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

missing=()
port_open 5432 || missing+=("postgres:5432")
port_open 6379 || missing+=("redis:6379")

if (( ${#missing[@]} > 0 )); then
  cat >&2 <<MSG
HATA: Veritabanı servislerine ulaşılamıyor (${missing[*]}).

Yerel geliştirme için bu portların host'a açılması gerekir:

  docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d postgres redis

Alternatif: yalnızca Docker yığınını kullanın (make docker-up) ve
http://localhost:8083 adresine gidin — arayüz Caddy üzerinden çalışır.
MSG
  exit 1
fi

# --- 3. Süreçleri başlat ----------------------------------------------------
api_pid=""
web_pid=""

cleanup() {
  trap - EXIT INT TERM
  for pid in "$api_pid" "$web_pid"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  # Alt süreç gruplarını da kapat (next/uvicorn kendi worker'larını açar).
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

( cd "$ROOT/apps/api" && PYTHONPATH=. exec "$ROOT/.venv/bin/uvicorn" showis.main:app --reload --port 8000 ) &
api_pid=$!

( cd "$ROOT/apps/web" && exec npm run dev ) &
web_pid=$!

echo "API: http://localhost:8000  •  Web: http://localhost:3000"
echo "Durdurmak için Ctrl+C."

# İlk ölen süreçte çık: yarım kalmış ortam bırakma.
wait -n "$api_pid" "$web_pid" 2>/dev/null || wait -n

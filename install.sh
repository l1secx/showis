#!/usr/bin/env bash
# Showis installer — clones the repository and starts the Docker Compose stack.
#
#   curl -fsSL https://raw.githubusercontent.com/l1secx/showis/main/install.sh | bash
#   curl -fsSL ... | bash -s -- --no-start
#   npm install -g @l1secx/showis && showis install
#
# First run writes infra/.env with random secrets (POSTGRES_PASSWORD,
# MASTER_KEY, SHOWIS_API_TOKEN) and a Caddy basic-auth user, because the edge
# gate is mandatory and Caddy refuses to start without it. Re-running keeps the
# existing infra/.env and only fast-forwards the checkout.
set -eu

REPO_URL="${SHOWIS_REPO_URL:-https://github.com/l1secx/showis.git}"
BRANCH="${SHOWIS_BRANCH:-main}"
INSTALL_DIR="${SHOWIS_INSTALL_DIR:-$HOME/.local/share/showis}"
EDGE_USER="${SHOWIS_EDGE_USER:-admin}"
NO_START=0
DRY_RUN=0

info() { printf '[INFO] %s\n' "$*" >&2; }
die() { printf '[HATA] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Showis installer

  install.sh                 clone (or update) and start the Docker Compose stack
  install.sh --no-start      prepare infra/.env but do not start
  install.sh --dir PATH      install directory (default ~/.local/share/showis)
  install.sh --branch NAME   git branch to install (default main)
  install.sh --dry-run       print what would happen, change nothing

  curl -fsSL https://raw.githubusercontent.com/l1secx/showis/main/install.sh | bash

Environment: SHOWIS_INSTALL_DIR, SHOWIS_BRANCH, SHOWIS_REPO_URL, SHOWIS_EDGE_USER.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-start) NO_START=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --dir) INSTALL_DIR="${2:?--dir needs a path}"; shift 2 ;;
    --branch) BRANCH="${2:?--branch needs a name}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

have git || die "git is required"
have docker || die "Docker is required: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 \
  || die "Docker Compose v2 is required (the 'docker compose' plugin; v1 'docker-compose' is not supported)"

ENV_FILE="$INSTALL_DIR/infra/.env"

if [ "$DRY_RUN" = 1 ]; then
  info "repository : $REPO_URL ($BRANCH)"
  info "directory  : $INSTALL_DIR"
  if [ -f "$ENV_FILE" ]; then info "infra/.env : kept as is"; else info "infra/.env : generated with random secrets"; fi
  if [ "$NO_START" = 1 ]; then info "start      : skipped (--no-start)"; else info "start      : docker compose -f infra/docker-compose.yml up -d --build"; fi
  exit 0
fi

# --- source -----------------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only --quiet origin "$BRANCH" \
    || die "git pull failed in $INSTALL_DIR (local changes?). Resolve them and re-run."
elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  die "$INSTALL_DIR exists and is not a Showis checkout; choose another --dir"
else
  info "Cloning $REPO_URL into $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# --- infra/.env -------------------------------------------------------------
# urlsafe base64 without padding; MASTER_KEY keeps the padding Fernet expects.
rand() { head -c "$1" /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_'; }

# Replace the KEY= line in place (awk, so '/', '$' and '&' in values are safe).
set_key() {
  KEY="$1" VALUE="$2" awk 'BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"] }
    index($0, k "=") == 1 { print k "=" v; found = 1; next }
    { print }
    END { if (!found) print k "=" v }' "$ENV_FILE" > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
}

FIRST_INSTALL=0
EDGE_PASSWORD=""
if [ -f "$ENV_FILE" ]; then
  info "Keeping existing infra/.env"
else
  FIRST_INSTALL=1
  info "Generating infra/.env"
  # Pin the hash generator to the same Caddy image the stack runs.
  CADDY_IMAGE="$(awk '/^  caddy:/ { s = 1 } s && /image:/ { print $2; exit }' infra/docker-compose.yml)"
  [ -n "$CADDY_IMAGE" ] || die "could not read the caddy image from infra/docker-compose.yml"
  EDGE_PASSWORD="$(rand 18)"
  EDGE_HASH="$(docker run --rm "$CADDY_IMAGE" caddy hash-password --plaintext "$EDGE_PASSWORD")" \
    || die "could not hash the edge password with $CADDY_IMAGE"

  (umask 077 && cp infra/.env.example "$ENV_FILE")
  set_key POSTGRES_PASSWORD "$(rand 24)"
  set_key MASTER_KEY "$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')"
  set_key SHOWIS_API_TOKEN "$(rand 32)"
  set_key SHOWIS_CADDY_USER "$EDGE_USER"
  # Single quotes stop Compose from interpolating the '$' in the bcrypt hash.
  set_key SHOWIS_CADDY_PASSWORD_HASH "'$EDGE_HASH'"
  set_key SHOWIS_EDGE_FORWARD_AUTH 1
  chmod 600 "$ENV_FILE"

  mkdir -p infra/.secrets
  chmod 700 infra/.secrets
  (umask 077 && printf '%s\n' "$EDGE_PASSWORD" > infra/.secrets/caddy-basic-auth.txt)
fi

# --- start ------------------------------------------------------------------
if [ "$NO_START" = 1 ]; then
  info "Prepared $INSTALL_DIR (not started). Start with: showis up"
  exit 0
fi

info "Building and starting the stack (first build takes a few minutes)"
docker compose -f infra/docker-compose.yml up -d --build

cat <<EOF

Showis is running: https://localhost:8443
  (internal CA — trust it with: docker exec showis-caddy caddy trust)
EOF
if [ "$FIRST_INSTALL" = 1 ]; then
  cat <<EOF
  user     : $EDGE_USER
  password : $EDGE_PASSWORD
  (also in $INSTALL_DIR/infra/.secrets/caddy-basic-auth.txt)
EOF
else
  echo "  password : $INSTALL_DIR/infra/.secrets/caddy-basic-auth.txt"
fi

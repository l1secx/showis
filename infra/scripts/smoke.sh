#!/usr/bin/env bash
set -euo pipefail

echo "=== Showis Smoke Test ==="

BASE_URL="${SHOWIS_BASE_URL:-http://localhost:8083}"
API_URL="${SHOWIS_API_URL:-http://localhost:8000}"
BROWSER_URL="${SHOWIS_BROWSER_URL:-http://localhost:8081}"

echo "1. Checking API health directly..."
curl -fsS "${API_URL}/health" | grep -q "ok" && echo "-> API is healthy" || { echo "API health failed"; exit 1; }

echo "2. Checking Gateway / Caddy health proxy..."
curl -fsS "${BASE_URL}/health" | grep -q "ok" && echo "-> Gateway routing is healthy" || { echo "Gateway check failed (is caddy running?)"; }

echo "=== Smoke test passed! ==="

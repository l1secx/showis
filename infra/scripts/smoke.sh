#!/usr/bin/env bash
set -euo pipefail

echo "=== Showis Smoke Test ==="

BASE_URL="${SHOWIS_BASE_URL:-http://localhost:8083}"
API_URL="${SHOWIS_API_URL:-http://localhost:8000}"
BROWSER_URL="${SHOWIS_BROWSER_URL:-http://localhost:8081}"

echo "1. Checking API health directly..."
if ! curl -fsS "${API_URL}/health" | grep -q "ok"; then
  echo "API health failed (${API_URL}/health)"; exit 1
fi
echo "-> API is healthy"

echo "2. Checking Gateway / Caddy health proxy..."
if ! curl -fsS "${BASE_URL}/health" | grep -q "ok"; then
  echo "Gateway check failed (${BASE_URL}/health; is caddy running?)"
  exit 1
fi
echo "-> Gateway routing is healthy"

echo "=== Smoke test passed! ==="

#!/usr/bin/env bash
# Demo duman testi: API health + temel CRUD + rapor uretimi (dis servis gerektirmez)
set -euo pipefail

API="${API_URL:-http://localhost:8083/api/v1}"
FAIL=0

check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "[ok] ${desc}"
  else
    echo "[FAIL] ${desc}"
    FAIL=1
  fi
}

check "api health" curl -sf "${API%/api/v1}/health"
check "cases list" curl -sf "${API}/cases"
check "settings providers" curl -sf "${API}/settings/providers"

# Vaka olustur + sil
CASE_JSON=$(mktemp)
HTTP_CODE=$(curl -s -o "${CASE_JSON}" -w '%{http_code}' -X POST "${API}/cases" \
  -H 'Content-Type: application/json' \
  -d '{"name":"smoke","purpose":"smoke","legal_basis":"self_check","target":{"first_name":"Smoke","last_name":"Test"},"seeds":[]}')
if [ "${HTTP_CODE}" = "201" ]; then
  echo "[ok] case create"
  CASE_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "${CASE_JSON}")
  check "case get" curl -sf "${API}/cases/${CASE_ID}"
  check "case export" curl -sf "${API}/cases/${CASE_ID}/export"
  check "case graph" curl -sf "${API}/cases/${CASE_ID}/graph"
  check "case audit" curl -sf "${API}/cases/${CASE_ID}/audit"
  check "report json" curl -sf -X POST "${API}/cases/${CASE_ID}/report" -H 'Content-Type: application/json' -d '{"format":"json"}'
  check "case delete" curl -sf -X DELETE "${API}/cases/${CASE_ID}"
else
  echo "[FAIL] case create (HTTP ${HTTP_CODE})"
  FAIL=1
fi
rm -f "${CASE_JSON}"

exit ${FAIL}

#!/usr/bin/env bash
# Showis demo: sentetik bir vaka olusturur, tarama baslatir, bulgulari listeler.
# Gercek API anahtari gerektirmez — anahtarsiz connector'lar nazikce hata/blocked uretir.
set -euo pipefail

API="${API_URL:-http://localhost:8083/api/v1}"

echo "[demo] API health kontrolu: ${API}"
for i in $(seq 1 30); do
  if curl -sf "${API%/api/v1}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

DEMO_TARGET="Demo_${RANDOM}"
echo "[demo] Vaka olusturuluyor (hedef: ${DEMO_TARGET})..."
CASE_ID=$(curl -sf -X POST "${API}/cases" \
  -H 'Content-Type: application/json' \
  -d "{
    \"name\": \"Demo Vaka - ${DEMO_TARGET}\",
    \"purpose\": \"Uctan uca demo akisi\",
    \"legal_basis\": \"self_check\",
    \"retention_days\": 7,
    \"target\": {\"first_name\": \"${DEMO_TARGET}\", \"last_name\": \"Ornek\", \"hints\": {\"city\": \"Istanbul\"}},
    \"seeds\": [{\"platform\": \"github\", \"username\": \"octocat\"}]
  }" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

echo "[demo] Vaka ID: ${CASE_ID}"
echo "[demo] Tarama baslatiliyor..."
SCAN_ID=$(curl -sf -X POST "${API}/cases/${CASE_ID}/scan" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"O3","budget":{"max_tasks":50,"max_http_requests":200,"max_llm_calls":5,"max_wall_seconds":300,"max_cost_usd":1.0}}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["scan_id"])')

echo "[demo] Scan ID: ${SCAN_ID} — tamamlanmasi bekleniyor..."
for i in $(seq 1 60); do
  STATUS=$(curl -sf "${API}/scans/${SCAN_ID}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])')
  echo "  durum: ${STATUS}"
  if [ "${STATUS}" = "completed" ] || [ "${STATUS}" = "failed" ] || [ "${STATUS}" = "cancelled" ]; then
    break
  fi
  sleep 5
done

echo "[demo] Bulgular:"
curl -sf "${API}/cases/${CASE_ID}/findings" | python3 -m json.tool | sed -n '1,80p'

echo "[demo] Rapor uretiliyor (json)..."
REPORT_ID=$(curl -sf -X POST "${API}/cases/${CASE_ID}/report" \
  -H 'Content-Type: application/json' \
  -d '{"format":"json"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "[demo] Rapor ID: ${REPORT_ID} -> ${API}/reports/${REPORT_ID}/download"
echo "[demo] Tamamlandi. Vaka: ${API}/cases/${CASE_ID}"

#!/usr/bin/env bash
# Weekly query-log triage — summarize refused + low-confidence buckets.
#
# Run manually:
#   ./deploy/run_weekly_triage.sh
#
# Or install the systemd timer (see deploy/README.md):
#   sudo cp deploy/weekly-triage.{service,timer} /etc/systemd/system/
#   sudo systemctl daemon-reload && sudo systemctl enable --now weekly-triage.timer
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

VENV_DIR="${VENV_DIR:-${REPO_ROOT}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-${VENV_DIR}/bin/python}"
REPORT_DIR="${REPO_ROOT}/eval/triage-reports"
STAMP="$(date -u +%Y-%m-%d)"
REPORT_PATH="${REPORT_DIR}/weekly-${STAMP}.txt"
JSON_PATH="${REPORT_DIR}/weekly-${STAMP}.json"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[weekly_triage] virtualenv missing at ${VENV_DIR}; run deploy/run_web.sh once first" >&2
  exit 1
fi

mkdir -p "${REPORT_DIR}"

echo "[weekly_triage] reading ${REPO_ROOT}/eval/query_log.jsonl"
echo "[weekly_triage] writing ${REPORT_PATH}"

{
  echo "Ark onboarding bot — weekly query triage (${STAMP} UTC)"
  echo
  "${PYTHON_BIN}" eval/summarize_queries.py
} | tee "${REPORT_PATH}"

"${PYTHON_BIN}" eval/summarize_queries.py --json > "${JSON_PATH}"

echo "[weekly_triage] done — review gold-set candidates in ${REPORT_PATH}"

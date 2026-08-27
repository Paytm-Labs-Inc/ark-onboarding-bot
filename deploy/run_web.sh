#!/usr/bin/env bash
# Launch the Ark onboarding bot web UI on an Ark compute, internal-only.
#
# Binds to loopback (127.0.0.1) by default so the service is NEVER exposed on a
# public interface ("no open bind"). Teammates reach it over an SSH tunnel — see
# deploy/README.md. Override WEB_HOST/WEB_PORT only if you understand the
# network exposure implications.
set -euo pipefail

# Resolve repo root as the parent of this script's directory, so the service
# works no matter which directory systemd / the operator starts it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

VENV_DIR="${VENV_DIR:-${REPO_ROOT}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Internal-only defaults. Do not change WEB_HOST to 0.0.0.0 on a host reachable
# from outside the corp network.
export WEB_HOST="${WEB_HOST:-127.0.0.1}"
export WEB_PORT="${WEB_PORT:-8765}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "[run_web] creating virtualenv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/pip" install --upgrade pip
  "${VENV_DIR}/bin/pip" install -r "${REPO_ROOT}/requirements.txt"
fi

# The web app reads PI_API_KEY from the environment / .env (via python-dotenv)
# and refuses to start without the configured backend's credential.
if [[ -z "${PI_API_KEY:-}" && ! -f "${REPO_ROOT}/.env" ]]; then
  echo "[run_web] WARNING: PI_API_KEY is not set and no .env file found; the app will refuse to start." >&2
fi

echo "[run_web] serving on http://${WEB_HOST}:${WEB_PORT} (internal-only)"
exec "${VENV_DIR}/bin/python" -m src.web

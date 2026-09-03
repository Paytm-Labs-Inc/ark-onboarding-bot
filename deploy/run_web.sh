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

if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

# The app refuses to start without the configured backend's credential, which
# it reads from the environment / .env (via python-dotenv in src.web main).
if [[ ! -f "${REPO_ROOT}/.env" && -z "${PI_API_KEY:-}" && -z "${CURSOR_API_KEY:-}" ]]; then
  echo "[run_web] WARNING: no .env file and neither PI_API_KEY nor CURSOR_API_KEY is set; the app will refuse to start." >&2
fi

echo "[run_web] serving on http://${WEB_HOST}:${WEB_PORT} (internal-only)"
exec "${VENV_DIR}/bin/python" -m src.web

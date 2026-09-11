#!/usr/bin/env bash
# Cursor MCP launcher — cd to repo root so `python -m src.mcp_server` resolves.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m src.mcp_server

#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
LOG_DIR="$PROJECT_DIR/.runlogs"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python environment not found. Follow the local setup in README.md, or set PYTHON_BIN."
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import uvicorn, fastapi, pandas' >/dev/null 2>&1; then
  echo "Install backend dependencies: $PYTHON_BIN -m pip install -r backend/requirements.txt"
  exit 1
fi
if [[ ! -x "$PROJECT_DIR/frontend/node_modules/.bin/vite" ]]; then
  echo "Install frontend dependencies: cd frontend && npm ci"
  exit 1
fi
if ! command -v node >/dev/null || ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
  echo "Node.js 22.12 or newer is required. See the Docker quick start in README.md."
  exit 1
fi

mkdir -p "$LOG_DIR"
export VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://127.0.0.1:$API_PORT}"

cleanup() {
  for process_id in "${BACKEND_PID:-}" "${FRONTEND_PID:-}"; do
    if [[ -n "$process_id" ]] && kill -0 "$process_id" 2>/dev/null; then
      kill "$process_id" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backend_args=(app.main:app --app-dir "$PROJECT_DIR/backend" --host 127.0.0.1 --port "$API_PORT")
if [[ "${UVICORN_RELOAD:-0}" == "1" ]]; then
  backend_args+=(--reload --reload-dir "$PROJECT_DIR/backend")
fi
"$PYTHON_BIN" -m uvicorn "${backend_args[@]}" >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

(
  cd "$PROJECT_DIR/frontend"
  exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort
) >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo "Frontend: http://127.0.0.1:$FRONTEND_PORT"
echo "API docs: http://127.0.0.1:$API_PORT/docs"
echo "Logs: $LOG_DIR"
echo "Press Ctrl+C to stop both services."
wait -n "$BACKEND_PID" "$FRONTEND_PID"

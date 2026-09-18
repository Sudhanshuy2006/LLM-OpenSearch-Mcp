#!/usr/bin/env bash
# One-command launcher.
#
#   ./run.sh demo     UI with the in-memory engine  (no Docker, no API key)
#   ./run.sh up       start OpenSearch in Docker and load the sample data
#   ./run.sh ui       UI against the real cluster
#   ./run.sh cli      run the five demo questions in the terminal
#   ./run.sh down     stop the containers
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

ensure_venv() {
  if [ ! -d "$VENV" ]; then
    echo "creating virtualenv…"
    "$PY" -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r requirements.txt
  fi
}

case "${1:-demo}" in
  demo)
    ensure_venv
    echo "starting the UI in demo mode — open http://127.0.0.1:8000"
    DEMO_MODE=true MCP_SERVER_MODE=local LLM_PROVIDER="${LLM_PROVIDER:-rules}" \
      "$VENV/bin/python" poc/web/app.py
    ;;
  up)
    docker compose -f poc/docker-compose.yml up -d --wait
    ensure_venv
    echo "loading sample data…"
    "$VENV/bin/python" poc/load_sample_data.py
    echo "done. now run: ./run.sh ui"
    ;;
  ui)
    ensure_venv
    MCP_SERVER_MODE="${MCP_SERVER_MODE:-local}" "$VENV/bin/python" poc/web/app.py
    ;;
  cli)
    ensure_venv
    "$VENV/bin/python" poc/cli.py --demo
    ;;
  down)
    docker compose -f poc/docker-compose.yml down
    ;;
  *)
    echo "usage: ./run.sh [demo|up|ui|cli|down]"; exit 1 ;;
esac

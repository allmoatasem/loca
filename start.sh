#!/bin/bash
set -e
cd "$(dirname "$0")"

# Activate venv
source .venv/bin/activate

# Start the orchestrator proxy in the background
echo "Starting orchestrator proxy on :8000 ..."
python3 -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 &
PROXY_PID=$!

# Start Open WebUI in Docker
echo "Starting Open WebUI on :3000 ..."
docker compose up -d

# Wait a moment then open the browser
sleep 2
open http://localhost:3000

echo ""
echo "Running. Press Ctrl+C to stop."
echo "  Proxy PID: $PROXY_PID"
echo "  Logs:      docker compose logs -f open-webui"

# On exit, kill the proxy and stop Open WebUI
trap "echo 'Stopping...'; kill $PROXY_PID 2>/dev/null; docker compose stop; exit" INT TERM

wait $PROXY_PID

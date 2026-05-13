#!/bin/sh
# Resolve the Tailscale IPv4 address at startup and bind uvicorn to it.
# Falls back to 127.0.0.1 if the interface isn't up yet.

set -eu

TS_IP=$(ip -4 -br addr show tailscale0 2>/dev/null | awk '{print $3}' | cut -d/ -f1 | head -1)
BIND_ADDR="${TS_IP:-127.0.0.1}"

echo "bc-proxy binding to $BIND_ADDR:8500"
exec /srv/bc-proxy/.venv/bin/uvicorn proxy:app \
    --host "$BIND_ADDR" \
    --port 8500

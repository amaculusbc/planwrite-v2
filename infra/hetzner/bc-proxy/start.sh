#!/bin/sh
# Bind locally and let nginx front the service over HTTPS.

set -eu

BIND_ADDR="127.0.0.1"

echo "bc-proxy binding to $BIND_ADDR:8500"
exec /srv/bc-proxy/.venv/bin/uvicorn proxy:app \
    --host "$BIND_ADDR" \
    --port 8500

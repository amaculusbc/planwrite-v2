#!/bin/bash
# Railway container entrypoint.
#
# Brings up Tailscale in userspace-networking mode (no /dev/net/tun required),
# joins the tailnet with the auth key, exposes a local SOCKS5 proxy
# at 127.0.0.1:1055, then execs the main CMD.
#
# Required env:
#   TS_AUTHKEY                Reusable tagged auth key for the Railway node
# Optional:
#   TS_HOSTNAME               Override hostname (default: planwrite-railway)

set -euo pipefail

if [[ -n "${TS_AUTHKEY:-}" ]]; then
  echo "Starting Tailscale in userspace-networking mode..."

  /usr/sbin/tailscaled \
      --tun=userspace-networking \
      --socks5-server=localhost:1055 \
      --statedir=/var/cache/tailscale &

  for i in $(seq 1 30); do
    if /usr/bin/tailscale status >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  /usr/bin/tailscale up \
      --authkey="$TS_AUTHKEY" \
      --hostname="${TS_HOSTNAME:-planwrite-railway}" \
      --ssh=false

  echo "Tailscale up:"
  /usr/bin/tailscale status 2>/dev/null | head -5 || true
else
  echo "TS_AUTHKEY not set - skipping Tailscale tunnel bootstrap."
fi

exec "$@"

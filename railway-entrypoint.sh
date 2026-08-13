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

  # A failed/expired auth key must NOT take the whole app down. Before this guard, `set -e`
  # made a rejected key fatal: the script exited before `exec "$@"`, uvicorn never started,
  # and the container crashlooped (502). BC Core is only reachable through this tunnel, so
  # without it odds fall back to Charlotte and expertise/boosts go quiet — degraded, not dead.
  if /usr/bin/tailscale up \
      --authkey="$TS_AUTHKEY" \
      --hostname="${TS_HOSTNAME:-planwrite-railway}" \
      --ssh=false; then
    echo "Tailscale up:"
    /usr/bin/tailscale status 2>/dev/null | head -5 || true
  else
    echo "WARNING: Tailscale failed to come up (likely an invalid/expired TS_AUTHKEY)."
    echo "Continuing WITHOUT the tunnel — BC Core will be unreachable until the key is fixed."
  fi
else
  echo "TS_AUTHKEY not set - skipping Tailscale tunnel bootstrap."
fi

exec "$@"

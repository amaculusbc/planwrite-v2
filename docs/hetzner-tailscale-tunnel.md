# Hetzner / Tailscale Tunnel

This repo's Railway image can bootstrap a Tailscale userspace tunnel at startup.
The current production pattern is:

- Railway app container joins the tailnet with `TS_AUTHKEY`
- `tailscaled` exposes a local SOCKS5 proxy at `127.0.0.1:1055`
- BC Core traffic is routed to the Hetzner-hosted node via `BC_CORE_BASE_URL`
- Other external traffic remains on direct egress

## Required Runtime Variables

- `TS_AUTHKEY`
- `BC_CORE_BASE_URL`

## Optional Runtime Variables

- `TS_HOSTNAME`
- `BC_CORE_SOCKS_PROXY`

## Files In Repo

- `Dockerfile`
- `railway-entrypoint.sh`
- `.env.example`

## Notes

- Do not commit live auth keys or tailnet hostnames that should remain private.
- The entrypoint uses Tailscale userspace networking, so no `/dev/net/tun` device is required.
- The Railway container observed in production exposes the SOCKS5 listener on `127.0.0.1:1055`.

# BC Core proxy

A small FastAPI service that lives on the Hetzner box, sits behind the
OpenVPN tunnel, and exposes BC Core (`core-external-api.actionnetwork.com`) to
planwrite-v2 on Railway. It listens only on the Tailscale interface.

## Why

Railway cannot run the OpenVPN path used to reach BC Core directly. Hetzner can.
This proxy bridges the two:

- Railway joins the tailnet in userspace mode
- Hetzner joins the same tailnet
- Hetzner forwards requests through the BC OpenVPN tunnel

## Components

- `proxy.py` - FastAPI reverse proxy
- `start.sh` - binds uvicorn to the Tailscale IP only
- `install.sh` - idempotent installer
- `bc-proxy.service` - systemd unit
- `nginx-bc-proxy.conf` - optional nginx fronting example
- `env.example` - env template, not secrets

## Secrets not committed

- `/etc/bc-proxy/env`
- BC Core API key
- live Tailscale auth keys
- live BC OpenVPN config

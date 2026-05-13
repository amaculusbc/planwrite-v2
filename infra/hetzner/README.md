# Hetzner Infrastructure

This directory contains the non-secret server-side artifacts that back the
Railway BC Core tunnel path.

What belongs in Git:

- install scripts
- systemd unit files
- startup wrappers
- proxy application code
- env templates
- nginx examples
- operational docs

What does not belong in Git:

- live OpenVPN `.ovpn` files
- live BC Core API keys
- live Tailscale auth keys
- `/etc/bc-proxy/env`
- any private host-specific certs or keys

Current production shape:

- Railway runs `planwrite-v2` and boots a Tailscale userspace SOCKS5 proxy
- Hetzner runs a `bc-proxy` FastAPI service behind Tailscale
- Hetzner also runs an OpenVPN client so BC Core traffic can reach BC's network

Directories:

- `bc-proxy/` - the Hetzner-side proxy app and service files
- `setup-bc-vpn.sh` - the OpenVPN selective-routing installer

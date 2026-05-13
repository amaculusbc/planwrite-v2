#!/bin/bash
# Idempotent installer for the BC Core proxy on the Hetzner box.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/srv/bc-proxy"
APP_USER="bc-proxy"
TS_HOSTNAME="${TS_HOSTNAME:-bc-proxy}"
TS_TAGS="${TS_TAGS:-tag:bc-proxy}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled

if [[ -n "${TS_AUTHKEY:-}" ]]; then
  tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOSTNAME" --advertise-tags="$TS_TAGS" --ssh=false --reset
else
  if ! tailscale status >/dev/null 2>&1; then
    echo "Tailscale not authenticated and TS_AUTHKEY not provided." >&2
    exit 1
  fi
fi

TS_IP=$(tailscale ip -4 | head -1)
echo "Tailscale IP: $TS_IP"

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR"
install -m 0644 "$SCRIPT_DIR/proxy.py" "$APP_DIR/proxy.py"
install -m 0644 "$SCRIPT_DIR/requirements.txt" "$APP_DIR/requirements.txt"
install -m 0755 "$SCRIPT_DIR/start.sh" "$APP_DIR/start.sh"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [[ ! -d "$APP_DIR/.venv" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

mkdir -p /etc/bc-proxy
chmod 0700 /etc/bc-proxy
if [[ ! -f /etc/bc-proxy/env ]]; then
  install -m 0600 "$SCRIPT_DIR/env.example" /etc/bc-proxy/env
  echo "Created /etc/bc-proxy/env from env.example - edit it before starting the service." >&2
fi

install -m 0644 "$SCRIPT_DIR/bc-proxy.service" /etc/systemd/system/bc-proxy.service
systemctl daemon-reload
systemctl enable bc-proxy.service
systemctl restart bc-proxy.service

sleep 2
if systemctl is-active --quiet bc-proxy; then
  echo "bc-proxy is active, listening on $TS_IP:8500"
else
  echo "bc-proxy failed to start - check: journalctl -u bc-proxy -n 50"
  exit 1
fi

cat <<EOF

bc-proxy installed (tailnet-only).

Tailscale node: $TS_HOSTNAME ($TS_IP), tagged $TS_TAGS
Reachable from any tailnet peer at:
  http://$TS_HOSTNAME:8500/healthz
  http://$TS_HOSTNAME:8500/healthz/upstream
  http://$TS_HOSTNAME:8500/<any BC Core path>
EOF

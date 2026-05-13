#!/bin/bash
# Install and start the BC Core OpenVPN tunnel with selective routing.
# Run as root on the Hetzner host. Idempotent — safe to re-run.
#
# Inputs:
#   /etc/planwrite/bc.ovpn   the .ovpn file from BC IT (place this before running)
#
# What it does:
#   1. Installs the openvpn client (if missing)
#   2. Copies the .ovpn to /etc/openvpn/client/bc.conf
#   3. Appends selective-routing directives so ONLY core-external-api.actionnetwork.com
#      egresses via the tunnel; OpenAI/ESPN/BAM stay on the host's normal egress
#   4. Installs the route-up script
#   5. Enables openvpn-client@bc.service
#   6. Sanity-checks the tunnel by hitting /league-types

set -euo pipefail

OVPN_SRC="${OVPN_SRC:-/etc/planwrite/bc.ovpn}"
OVPN_DST="/etc/openvpn/client/bc.conf"
ROUTE_UP="/etc/openvpn/bc-route-up.sh"
BC_HOST="core-external-api.actionnetwork.com"

if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

if [[ ! -f "$OVPN_SRC" ]]; then
  echo "Missing $OVPN_SRC - place the .ovpn file from BC IT there first." >&2
  exit 1
fi

if ! command -v openvpn >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends openvpn iproute2 dnsutils curl ca-certificates
fi

install -m 0600 "$OVPN_SRC" "$OVPN_DST"

if ! grep -q "# planwrite selective routing" "$OVPN_DST"; then
  cat >> "$OVPN_DST" <<EOF

# planwrite selective routing - keep host egress direct, only BC Core via tunnel
pull-filter ignore "redirect-gateway"
pull-filter ignore "dhcp-option DNS"
script-security 2
route-up $ROUTE_UP
EOF
fi

cat > "$ROUTE_UP" <<EOF
#!/bin/sh
BC_IP=\$(getent hosts $BC_HOST | awk '{print \$1}' | head -1)
if [ -n "\$BC_IP" ]; then
  ip route replace "\$BC_IP/32" dev "\$dev" || true
fi
EOF
chmod +x "$ROUTE_UP"

systemctl daemon-reload
systemctl enable openvpn-client@bc.service
systemctl restart openvpn-client@bc.service

for i in $(seq 1 30); do
  if ip link show tun0 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! ip link show tun0 >/dev/null 2>&1; then
  echo "tun0 never came up - check: journalctl -u openvpn-client@bc -n 50" >&2
  exit 1
fi

echo
echo "tun0 is up. Verifying BC Core reachability..."
if [[ -n "${BC_CORE_API_KEY:-}" ]]; then
  if curl -fsS -H "X-Api-Key: $BC_CORE_API_KEY" "https://$BC_HOST/league-types" >/dev/null; then
    echo "BC Core reachable through tunnel."
  else
    echo "Tunnel up but BC Core call failed - check API key and tunnel route." >&2
    exit 1
  fi
else
  echo "(Skipping BC Core curl test - set BC_CORE_API_KEY in env to enable.)"
fi

echo "Done."

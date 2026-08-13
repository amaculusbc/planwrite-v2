#!/bin/bash
# Railway container entrypoint.
#
# BC Core is reached directly over its external HTTPS API (BC_CORE_BASE_URL), so there is
# no tunnel to bring up — this just execs the main CMD. (Tailscale was removed 2026-08-13;
# it was a dead migration remnant whose expired auth key once crashlooped the app.)

set -euo pipefail

exec "$@"

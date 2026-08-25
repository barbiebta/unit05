#!/bin/bash
set -eo pipefail

utils=/opt/supervisor-scripts/utils
. "${utils}/environment.sh"

exec /usr/bin/tailscale \
    --socket=/run/tailscale/tailscaled.sock \
    nc "${UNIT05_EDITOR_TAILSCALE_HOST:?UNIT05_EDITOR_TAILSCALE_HOST is required}" 22

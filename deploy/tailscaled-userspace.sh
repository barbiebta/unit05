#!/bin/bash
set -eo pipefail

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"

install -d -m 0700 /workspace/tailscale
install -d -m 0755 /run/tailscale

exec /usr/sbin/tailscaled \
    --tun=userspace-networking \
    --state=/workspace/tailscale/tailscaled.state \
    --socket=/run/tailscale/tailscaled.sock \
    --socks5-server=127.0.0.1:1055

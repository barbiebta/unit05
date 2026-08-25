#!/bin/bash
set -eo pipefail

exec /usr/bin/socat \
    TCP-LISTEN:10022,bind=127.0.0.1,reuseaddr,fork \
    EXEC:/opt/supervisor-scripts/unit05-tailscale-nc.sh

#!/usr/bin/env bash
set -eo pipefail

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"

exec /venv/main/bin/python /workspace/unit05/container/sync-models.py

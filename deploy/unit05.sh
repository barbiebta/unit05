#!/bin/bash
set -eo pipefail

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"

exec /workspace/unit05/.venv/bin/unit05 \
  --host "${UNIT05_HOST:-127.0.0.1}" \
  --port "${UNIT05_PORT:-18765}"

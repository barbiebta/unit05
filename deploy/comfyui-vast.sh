#!/usr/bin/env bash
set -eo pipefail

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"

cd /workspace/ComfyUI
exec /venv/main/bin/python main.py \
  --listen 127.0.0.1 \
  --port 18188 \
  --user-directory /workspace/ComfyUI/user \
  --input-directory /workspace/ComfyUI/input \
  --output-directory /workspace/ComfyUI/output \
  --temp-directory /workspace/ComfyUI/temp

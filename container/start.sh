#!/usr/bin/env bash
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
COMFYUI_HOST="${COMFYUI_HOST:-0.0.0.0}"
COMFYUI_PORT="${COMFYUI_PORT:-8188}"
UNIT05_HOST="${UNIT05_HOST:-0.0.0.0}"
UNIT05_PORT="${UNIT05_PORT:-18765}"

mkdir -p \
  "${COMFYUI_ROOT}/input" \
  "${COMFYUI_ROOT}/output" \
  "${COMFYUI_ROOT}/models" \
  "${COMFYUI_ROOT}/user/default/workflows" \
  "${UNIT05_ROOT:-/workspace/unit05-data}"

cd "${COMFYUI_ROOT}"
python main.py --listen "${COMFYUI_HOST}" --port "${COMFYUI_PORT}" &
comfy_pid=$!

unit05 --host "${UNIT05_HOST}" --port "${UNIT05_PORT}" &
unit05_pid=$!

shutdown() {
  kill -TERM "${unit05_pid}" "${comfy_pid}" 2>/dev/null || true
  wait "${unit05_pid}" "${comfy_pid}" 2>/dev/null || true
}
trap shutdown INT TERM EXIT

wait -n "${comfy_pid}" "${unit05_pid}"


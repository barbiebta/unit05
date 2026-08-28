#!/bin/bash
set -euo pipefail

source /opt/supervisor-scripts/utils/environment.sh
cd /workspace/ComfyUI
exec /usr/bin/python3 -u /workspace/unit05/tools/run_latent_mutation_cascade_server.py

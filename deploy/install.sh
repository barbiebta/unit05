#!/bin/bash
set -euo pipefail

project_root="${1:-/workspace/unit05}"
python_bin="${UNIT05_INSTALL_PYTHON:-/venv/main/bin/python}"
venv_dir="${project_root}/.venv"

test -f "${project_root}/pyproject.toml"
test -x "${python_bin}"
command -v uv >/dev/null
command -v supervisorctl >/dev/null

uv venv "${venv_dir}" --python "${python_bin}" --allow-existing
uv pip install --python "${venv_dir}/bin/python" -e "${project_root}"

install -m 0755 "${project_root}/deploy/unit05.sh" \
  /opt/supervisor-scripts/unit05.sh
install -m 0644 "${project_root}/deploy/unit05.conf" \
  /etc/supervisor/conf.d/unit05.conf

supervisorctl reread
supervisorctl update
supervisorctl restart unit05 || supervisorctl start unit05
supervisorctl status unit05

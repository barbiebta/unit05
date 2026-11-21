#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/workspace/unit05}"
COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
PYTHON="${PYTHON:-/venv/main/bin/python}"
UV="${UV:-$(command -v uv)}"
COMFYUI_COMMIT="${COMFYUI_COMMIT:-a1079ba16f2674734b065eb036fbfdddaa321a4d}"
TORCH_INDEX="https://download.pytorch.org/whl/cu130"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates ffmpeg git git-lfs jq libgl1 libglib2.0-0 rclone
rm -rf /var/lib/apt/lists/*

mkdir -p \
  "${COMFYUI_ROOT}/models" \
  "${COMFYUI_ROOT}/input" \
  "${COMFYUI_ROOT}/output" \
  "${COMFYUI_ROOT}/temp" \
  "${COMFYUI_ROOT}/user/default/workflows"

if [[ ! -d "${COMFYUI_ROOT}/.git" ]]; then
  if [[ -e "${COMFYUI_ROOT}" ]] && [[ -n "$(find "${COMFYUI_ROOT}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "${COMFYUI_ROOT} is nonempty and is not a Git checkout" >&2
    exit 3
  fi
  git clone --filter=blob:none https://github.com/Comfy-Org/ComfyUI.git "${COMFYUI_ROOT}"
fi
git -C "${COMFYUI_ROOT}" remote set-url origin https://github.com/Comfy-Org/ComfyUI.git
git -C "${COMFYUI_ROOT}" fetch -q origin "${COMFYUI_COMMIT}"
if [[ "$(git -C "${COMFYUI_ROOT}" rev-parse HEAD 2>/dev/null || true)" != "${COMFYUI_COMMIT}" ]]; then
  if ! git -C "${COMFYUI_ROOT}" diff --quiet || ! git -C "${COMFYUI_ROOT}" diff --cached --quiet; then
    echo "Tracked ComfyUI changes exist; refusing to overwrite them" >&2
    exit 3
  fi
  git -C "${COMFYUI_ROOT}" checkout -q --detach "${COMFYUI_COMMIT}"
fi

"${UV}" pip install --python "${PYTHON}" \
  torch==2.11.0+cu130 torchvision==0.26.0+cu130 torchaudio==2.11.0+cu130 \
  --index-url "${TORCH_INDEX}"
grep -Eiv '^[[:space:]]*(torch|torchvision|torchaudio)([<=>~![:space:]]|$)' \
  "${COMFYUI_ROOT}/requirements.txt" > /tmp/unit05-comfy-requirements.txt
"${UV}" pip install --python "${PYTHON}" -r /tmp/unit05-comfy-requirements.txt

install_node() {
  local name="$1" repository="$2" revision="$3" destination="${COMFYUI_ROOT}/custom_nodes/$1"
  if [[ ! -d "${destination}/.git" ]]; then
    if [[ -e "${destination}" ]]; then
      echo "${destination} exists but is not a Git checkout" >&2
      return 3
    fi
    git clone --filter=blob:none "${repository}" "${destination}"
  fi
  git -C "${destination}" remote set-url origin "${repository}"
  git -C "${destination}" fetch -q origin "${revision}"
  if [[ "$(git -C "${destination}" rev-parse HEAD 2>/dev/null || true)" != "${revision}" ]]; then
    if ! git -C "${destination}" diff --quiet || ! git -C "${destination}" diff --cached --quiet; then
      echo "Tracked changes exist in ${destination}; refusing to overwrite them" >&2
      return 3
    fi
    git -C "${destination}" checkout -q --detach "${revision}"
  fi
  if [[ -f "${destination}/requirements.txt" ]]; then
    grep -Eiv '^[[:space:]]*(torch|torchvision|torchaudio)([<=>~![:space:]]|$)' \
      "${destination}/requirements.txt" > "/tmp/${name}-requirements.txt"
    [[ ! -s "/tmp/${name}-requirements.txt" ]] || \
      "${UV}" pip install --python "${PYTHON}" -r "/tmp/${name}-requirements.txt"
  fi
}

while IFS=$'\t' read -r name repository revision; do
  install_node "${name}" "${repository}" "${revision}"
done < <(jq -r '.[] | [.name, .repository, .revision] | @tsv' "${PROJECT_ROOT}/container/custom-nodes.lock.json")

cp -a "${PROJECT_ROOT}/container/overlays/ComfyUI-H3-Motion-Context-MultiRef/." \
  "${COMFYUI_ROOT}/custom_nodes/ComfyUI-H3-Motion-Context-MultiRef/"
mkdir -p "${COMFYUI_ROOT}/custom_nodes/LBH_H3_Legacy_Compat"
cp -a "${PROJECT_ROOT}/container/overlays/LBH_H3_Legacy_Compat" \
  "${COMFYUI_ROOT}/custom_nodes/"
cp -a "${PROJECT_ROOT}/container/workflows/." "${COMFYUI_ROOT}/user/default/workflows/"

# Node dependencies may mention Torch. Restore the tested CUDA 13 wheel set last.
"${UV}" pip install --python "${PYTHON}" \
  torch==2.11.0+cu130 torchvision==0.26.0+cu130 torchaudio==2.11.0+cu130 \
  --index-url "${TORCH_INDEX}"
"${UV}" pip check --python "${PYTHON}"

install -m 0755 "${PROJECT_ROOT}/deploy/comfyui-vast.sh" /opt/supervisor-scripts/comfyui-unit05.sh
install -m 0644 "${PROJECT_ROOT}/deploy/comfyui-vast.conf" /etc/supervisor/conf.d/comfyui-unit05.conf

"${PROJECT_ROOT}/deploy/install.sh" "${PROJECT_ROOT}"
supervisorctl reread
supervisorctl update
supervisorctl restart comfyui-unit05 || supervisorctl start comfyui-unit05

"${PYTHON}" - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0), "capability", torch.cuda.get_device_capability(0))
PY
echo UNIT05_VAST_STACK_INSTALLED

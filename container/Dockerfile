FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG COMFYUI_COMMIT=a1079ba16f2674734b065eb036fbfdddaa321a4d

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/comfy-venv \
    PATH=/opt/comfy-venv/bin:${PATH} \
    COMFYUI_ROOT=/workspace/ComfyUI \
    UNIT05_ROOT=/workspace/unit05-data \
    UNIT05_COMFY_URL=http://127.0.0.1:8188 \
    UNIT05_COMFY_ROOT=/workspace/ComfyUI \
    UNIT05_COMFY_INPUT_DIR=/workspace/ComfyUI/input \
    UNIT05_COMFY_OUTPUT_DIR=/workspace/ComfyUI/output \
    UNIT05_TEMPLATE=/opt/unit05/templates/dasiwa_ref2va_api_template.json

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      ffmpeg \
      git \
      git-lfs \
      libgl1 \
      libglib2.0-0 \
      libsm6 \
      libxext6 \
      libxrender1 \
      python3 \
      python3-dev \
      python3-venv \
      tini \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv "${VIRTUAL_ENV}" \
    && python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu130 \
      torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0

RUN git clone --filter=blob:none https://github.com/Comfy-Org/ComfyUI.git "${COMFYUI_ROOT}" \
    && git -C "${COMFYUI_ROOT}" checkout --detach "${COMFYUI_COMMIT}" \
    && python -m pip install --no-cache-dir -r "${COMFYUI_ROOT}/requirements.txt"

COPY container/custom-nodes.lock.json container/install_custom_nodes.py /tmp/unit05-container/
RUN python /tmp/unit05-container/install_custom_nodes.py \
      /tmp/unit05-container/custom-nodes.lock.json \
      "${COMFYUI_ROOT}/custom_nodes"

# Preserve the live server's intentional local node work. Caches are not copied.
COPY container/overlays/ComfyUI-H3-Motion-Context-MultiRef/ \
  ${COMFYUI_ROOT}/custom_nodes/ComfyUI-H3-Motion-Context-MultiRef/
COPY container/overlays/LBH_H3_Legacy_Compat/ \
  ${COMFYUI_ROOT}/custom_nodes/LBH_H3_Legacy_Compat/

COPY container/workflows/ ${COMFYUI_ROOT}/user/default/workflows/
COPY . /opt/unit05/
RUN python -m pip install --no-cache-dir /opt/unit05 \
    && chmod +x /opt/unit05/container/start.sh

WORKDIR /workspace
VOLUME ["/workspace/ComfyUI/models", "/workspace/ComfyUI/input", "/workspace/ComfyUI/output", "/workspace/unit05-data"]
EXPOSE 8188 18765

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/unit05/container/start.sh"]


# model-free Unit05 server image

This captures the working `unit05-vast` software state from 2026-08-28 without
models, generated media, credentials, caches, or machine-specific Tailscale state.

The live stack being reproduced was:

- ComfyUI `a1079ba16f2674734b065eb036fbfdddaa321a4d`
- Python 3.12
- PyTorch `2.10.0+cu130`
- torchvision `0.25.0+cu130`
- torchaudio `2.10.0+cu130`
- Triton `3.6.0`
- ComfyUI frontend package `1.49.6`
- an underlying CUDA 12.8 toolkit with a CUDA 13.0 PyTorch wheel
- the custom-node revisions in `custom-nodes.lock.json`
- all GUI workflows present under `ComfyUI/user/default/workflows`
- the local LBH compatibility node and the modified Instrumentality bridge/node code

The container intentionally does not enable SageAttention. It was not installed in
the working Comfy environment, and there was no SageAttention startup argument.

## Build

Run this from the Unit05 repository root:

```bash
docker build -f container/Dockerfile -t unit05-rebuild02:2026-08-28 .
```

The build needs internet access for the CUDA base image, PyTorch wheels, ComfyUI,
and pinned custom-node repositories.

## Run

```bash
docker run --rm --gpus all \
  -p 8188:8188 -p 18765:18765 \
  -v unit05-models:/workspace/ComfyUI/models \
  -v unit05-input:/workspace/ComfyUI/input \
  -v unit05-output:/workspace/ComfyUI/output \
  -v unit05-data:/workspace/unit05-data \
  unit05-rebuild02:2026-08-28
```

ComfyUI listens on port 8188 and the ordinary Unit05 bundle executor listens on
18765. The mutation-cascade and Instrumentality scripts are included under
`/opt/unit05/tools`, but do not autostart because they are experiment-specific and
expect particular source media.

For delivery to an editor machine, mount the SSH key or other credential at run
time and provide the existing `UNIT05_OUTPUT_*` environment variables. Never bake
the key, Tailscale identity, API tokens, models, or outputs into the image.


# Vast deployment: `unit05-rebuild02`

This is a model-free, reproducible image for the Dummyplug v2 bundle producer,
Unit05 executor, and the known-good MiniMax H3 ComfyUI graph. Models, media,
credentials, and Tailscale identity remain on mounted storage or runtime secrets.

## Capacity

- GPU: one RTX PRO 6000 Blackwell WS (96 GB) is the known-good target.
- Volume: 250 GB minimum for the five required base files and working room;
  500 GB is the practical floor for experiments and retained outputs.
- Container disk: 60 GB minimum; 100 GB recommended for image layers, Python
  packages, custom nodes, and temporary package caches.
- RAM: 128 GB works for the normal single-worker graph; 192 GB or more gives
  safer headroom for loading, encoding, and large reference media.
- Ports: TCP 8188 for ComfyUI and TCP 18765 for Unit05. Keep both private.

## Build and publish

From the repository root:

```bash
docker build -f container/Dockerfile -t ghcr.io/barbiebta/unit05:rebuild02 .
docker push ghcr.io/barbiebta/unit05:rebuild02
```

Use the immutable digest shown by the push when creating the Vast template.

## Vast template

1. Use the published image digest.
2. Request one GPU and at least the capacity above.
3. Mount persistent storage at `/workspace`.
4. Expose 8188 and 18765 through Vast's authenticated proxy, or use SSH/Tailscale.
5. Copy the non-secret variables from `container/vast.env.example`.
6. Do not put SSH keys, API tokens, or a Tailscale auth key into the image.
7. Place the files listed by `container/models.required.json` at their exact
   destinations beneath `/workspace/ComfyUI` before the first render.

The editor already has an authenticated `r2:` rclone remote for the private
`gehirnmodels` bucket. Copy that rclone configuration into a runtime secret at
`/run/secrets/rclone.conf`, then restore the minimal API-worker model set with:

```bash
python /opt/unit05/container/sync-models.py
```

The four large private objects come from R2. `taeh3.safetensors` is fetched
from Kijai's public Hugging Face repository and verified by both size and
SHA-256. The script is idempotent and writes through `.part` files.

The container starts both services. Docker reports healthy only after Unit05's
worker thread is alive and ComfyUI answers `/system_stats`.

## First-boot acceptance test

```bash
curl --fail http://127.0.0.1:8188/system_stats
curl --fail http://127.0.0.1:18765/health
curl --fail http://127.0.0.1:18765/api/status
python /opt/unit05/container/preflight.py
```

Then upload the committed cross-repository fixture ZIP through the Unit05
dashboard or:

```bash
curl --fail --upload-file tests/fixtures/dummyplug-v2-smoke.zip \
  http://127.0.0.1:18765/api/inputs/dummyplug-v2-smoke.zip
```

The fixture verifies ingestion and schema compatibility. Its placeholder media
is not intended to complete an H3 render; use a real sealed Dummyplug bundle for
the final GPU smoke test.

## Shutdown rule

Before destroying a rental, confirm Unit05 has no active job, copy outbox and
diagnostics to the editor, push source changes, and snapshot or retain the
persistent model volume. Stop the rental only after those checks pass.

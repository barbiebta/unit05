# unit05

`unit05` watches a folder, executes sealed Dummyplug H3 job ZIPs through ComfyUI one at a time, exposes a health/progress dashboard, delivers completed outputs to a configured destination, and cleans each job after delivery.

The folder is the queue.

```text
data/
  inputs/    # upload finished .dummyjob.zip files here
  working/   # claimed bundles and extracted job workspaces
  outbox/    # completed results waiting for delivery
  archive/   # bundles whose results were delivered
  failed/    # rejected or failed bundles
  logs/
```

Upload with a temporary suffix and rename only after SFTP finishes:

```text
job.dummyjob.zip.partial -> job.dummyjob.zip
```

## Configuration

The service reads environment variables. Important defaults:

```text
UNIT05_ROOT=/workspace/unit05/data
UNIT05_COMFY_URL=http://127.0.0.1:18188
UNIT05_COMFY_ROOT=/workspace/ComfyUI
UNIT05_COMFY_INPUT_DIR=/workspace/input
UNIT05_COMFY_OUTPUT_DIR=/workspace/output
UNIT05_TEMPLATE=/workspace/unit05/templates/dasiwa_ref2va_api_template.json
UNIT05_HOST=127.0.0.1
UNIT05_PORT=18765
```

For a mounted/local destination:

```text
UNIT05_OUTPUT_LOCAL_DIR=/some/output/folder
```

For the default editor-desktop SFTP destination:

```text
UNIT05_OUTPUT_SFTP_HOST=<desktop Tailscale address>
UNIT05_OUTPUT_SFTP_PORT=22
UNIT05_OUTPUT_SFTP_USER=<restricted user>
UNIT05_OUTPUT_SFTP_KEY=/workspace/secrets/editor-output-key
UNIT05_OUTPUT_SFTP_DIR=<restricted output directory>
```

If delivery is not configured or the desktop is offline, results remain in `outbox` and are retried without blocking later renders.

For Vast containers without `/dev/net/tun`, `deploy/tailscaled-userspace.sh` runs Tailscale in userspace mode under Supervisor. `unit05-delivery-bridge` forwards local port `10022` through `tailscale nc`; run `deploy/configure-delivery.sh` to persist the corresponding SFTP settings. The Windows editor setup scripts in `tools/` install OpenSSH with a tailnet-only firewall rule and add the dedicated SFTP-only Unit05 public key.

## Install on the Vast template

With the project copied to `/workspace/unit05`:

```bash
chmod +x /workspace/unit05/deploy/install.sh
/workspace/unit05/deploy/install.sh
```

The installer is idempotent. Configuration examples are in `deploy/unit05.env.example`; the Vast service wrapper reads `/workspace/.env` through the template's environment helper.

## Dashboard

The dashboard is served on the configured host and port. It reports Executor, Comfy, GPU, Tailscale, destination-link, folder queue, current-node/progress, timings, pending delivery, and failures. Bind it through Tailscale Serve or an SSH tunnel; do not expose it publicly without authentication.

Once the node is logged into Tailscale, publish the localhost-only dashboard privately with:

```bash
tailscale --socket=/run/tailscale/tailscaled.sock serve --bg --yes 18765
```

## JoyCaption prompt fermentation

`unit05.joycaption` and `unit05.living_prompt` are reusable building blocks for video loops that
derive the next prompt from the last generated chunk. The current Instrumentality experiment
extracts a representative frame, asks JoyCaption to describe every character as an
anthropomorphic rabbit, atomizes the result, and combines those generated fragments with
user-written fragments that persist until explicitly edited or removed. Each generation keeps an
exact prompt snapshot in bounded history.

JoyCaption can remain localhost-only on the editor. Start a reconnecting reverse SSH bridge so it
appears only on Unit05 loopback:

```powershell
.\deploy\start-joycaption-bridge.ps1 `
  -Background `
  -Unit05Host root@UNIT05_HOST `
  -SshPort UNIT05_SSH_PORT `
  -IdentityFile "$env:USERPROFILE\.ssh\runpod_ed25519"
```

Copy `deploy/unit05-joycaption.env.example` to a root-readable configuration file on Unit05 and
set the API key. The default client endpoint is `http://127.0.0.1:18000/v1`; no provider address,
container hostname, or current rebuild number is embedded in the Python modules.

## Safety

- ZIP traversal and undeclared files are rejected.
- Every v1 bundle file is checksum-verified.
- Stable job IDs plus bundle hashes prevent duplicate execution.
- Comfy prompt IDs are journaled before waiting.
- Outputs remain remote until destination delivery succeeds.
- Cleanup never removes models, templates, shared assets, queued jobs, or unacknowledged results.

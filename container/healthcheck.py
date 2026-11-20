from __future__ import annotations

import json
import os
import sys
import urllib.request


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return payload


def main() -> int:
    unit05_port = os.environ.get("UNIT05_PORT", "18765")
    comfy_url = os.environ.get("UNIT05_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    executor = get_json(f"http://127.0.0.1:{unit05_port}/health")
    get_json(f"{comfy_url}/system_stats")
    if executor.get("healthy") is not True or executor.get("comfy") is not True:
        raise RuntimeError(f"Unit05 is not ready: {executor}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)

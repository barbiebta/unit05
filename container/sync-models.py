from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


COMFYUI_ROOT = Path(os.environ.get("COMFYUI_ROOT", "/workspace/ComfyUI"))
MANIFEST = Path(__file__).with_name("models.required.json")
RCLONE_CONFIG = os.environ.get("RCLONE_CONFIG", "/run/secrets/rclone.conf")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid(path: Path, item: dict[str, object]) -> bool:
    if not path.is_file() or path.stat().st_size != int(item["bytes"]):
        return False
    expected_hash = item.get("sha256")
    return expected_hash is None or sha256(path) == expected_hash


def download(item: dict[str, object]) -> None:
    destination = COMFYUI_ROOT / str(item["destination"]) / str(item["filename"])
    if valid(destination, item):
        print(f"verified existing: {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    source = str(item["source"])
    print(f"downloading {source} -> {destination}")
    if source.startswith("r2:"):
        if not shutil.which("rclone"):
            raise RuntimeError("rclone is required for R2 model restoration")
        subprocess.run(
            [
                "rclone",
                "copyto",
                source,
                str(partial),
                "--config",
                RCLONE_CONFIG,
                "--s3-no-check-bucket",
                "--multi-thread-cutoff",
                "256M",
                "--multi-thread-streams",
                "16",
                "--transfers",
                "8",
                "--checkers",
                "16",
                "--retries",
                "8",
                "--low-level-retries",
                "20",
                "--stats",
                "30s",
                "--stats-one-line",
            ],
            check=True,
        )
    else:
        request = urllib.request.Request(source, headers={"User-Agent": "unit05-model-sync/1"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=8 * 1024 * 1024)

    if not valid(partial, item):
        raise RuntimeError(f"Downloaded file failed verification: {partial}")
    partial.replace(destination)
    print(f"verified: {destination} ({destination.stat().st_size} bytes)")


def main() -> int:
    items = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise RuntimeError("models.required.json must contain a list")
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Every model manifest entry must be an object")
        download(item)
    print("UNIT05_MODEL_SYNC_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"model sync failed: {error}", file=sys.stderr)
        raise SystemExit(1)

from __future__ import annotations

import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seconds_between(start: str | None, end: str | None) -> float | None:
    start_value, end_value = parse_iso(start), parse_iso(end)
    if not start_value or not end_value:
        return None
    return round((end_value - start_value).total_seconds(), 3)


def collect_environment(comfy_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    commands = {
        "gpu": [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        "comfy_commit": ["git", "-C", str(comfy_root), "rev-parse", "HEAD"],
    }
    for key, command in commands.items():
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
            if process.returncode == 0:
                result[key] = process.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process = subprocess.run(
            [
                "/venv/main/bin/python",
                "-c",
                "import json,torch; print(json.dumps({'torch':torch.__version__,'cuda':torch.version.cuda}))",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if process.returncode == 0:
            result.update(json.loads(process.stdout.strip().splitlines()[-1]))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return result


def comfy_timestamps(history_record: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for event, data in history_record.get("status", {}).get("messages", []):
        if not isinstance(data, dict) or "timestamp" not in data:
            continue
        moment = datetime.fromtimestamp(float(data["timestamp"]) / 1000, tz=timezone.utc).isoformat()
        if event == "execution_start":
            found["execution_start"] = moment
        elif event == "execution_success":
            found["execution_success"] = moment
    return found


CSV_FIELDS = [
    "job_id",
    "title",
    "prompt_id",
    "created_at",
    "execution_start",
    "execution_success",
    "queue_wait_seconds",
    "comfy_execution_seconds",
    "remote_total_seconds",
    "width",
    "height",
    "duration",
    "fps",
    "steps",
    "sampler",
    "seed",
    "shift_video",
    "shift_audio",
    "input_scaling",
    "attention_backend",
    "gpu",
    "torch",
    "cuda",
    "comfy_commit",
]


def append_render_history(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})

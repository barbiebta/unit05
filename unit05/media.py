from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class MediaProbeError(RuntimeError):
    pass


def probe_media(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if process.returncode:
        raise MediaProbeError(process.stderr.strip() or f"ffprobe failed for {path.name}")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise MediaProbeError(f"ffprobe returned invalid JSON for {path.name}") from error
    video = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"), {})
    duration = float(payload.get("format", {}).get("duration") or 0)
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _fraction(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "has_video": bool(video),
        "has_audio": bool(audio),
    }


def _fraction(value: Any) -> float:
    if not value:
        return 0.0
    text = str(value)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else 0.0
    return float(text)

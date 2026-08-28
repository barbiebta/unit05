from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path


RABBIT_FRAME_PROMPT = (
    "Return only a JSON array containing 6 to 12 concise, independent visual prompt fragments. "
    "Describe every human or character as an anthropomorphic rabbit, as though "
    "they were already rabbits in the image. Preserve the visible action, pose, composition, "
    "camera, setting, lighting, materials, and mood. Treat inherited slogans, logos, "
    "watermarks, and garment lettering as low-salience visual material: usually describe "
    "their presence, texture, or partial legibility instead of faithfully transcribing "
    "the exact words, unless the lettering is visually dominant or structurally important. "
    "Do not mention transformation, "
    "reinterpretation, a source image, or these instructions. Use present tense."
)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class JoyCaptionConfig:
    base_url: str = "http://127.0.0.1:18000/v1"
    api_key: str = ""
    model: str = "fancyfeast/llama-joycaption-beta-one-hf-llava"
    timeout_seconds: float = 300.0

    @classmethod
    def from_env_file(cls, path: Path) -> "JoyCaptionConfig":
        values = _read_env(path)
        return cls(
            base_url=values.get(
                "UNIT05_JOYCAPTION_BASE_URL", "http://127.0.0.1:18000/v1"
            ).rstrip("/"),
            api_key=values.get("UNIT05_JOYCAPTION_API_KEY", values.get("JOY_API_KEY", "")),
            model=values.get(
                "UNIT05_JOYCAPTION_MODEL",
                values.get(
                    "JOY_MODEL_ID", "fancyfeast/llama-joycaption-beta-one-hf-llava"
                ),
            ),
            timeout_seconds=float(values.get("UNIT05_JOYCAPTION_TIMEOUT", "300")),
        )


def extract_representative_frame(
    video_path: Path, output_path: Path, *, seconds: float = 0.8
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(seconds), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(output_path),
        ],
        check=True,
    )
    return output_path


def caption_image(
    image_path: Path,
    config: JoyCaptionConfig,
    *,
    prompt: str = RABBIT_FRAME_PROMPT,
    seed: int | None = None,
    temperature: float = 0.65,
    max_tokens: int = 400,
) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + encoded},
                    },
                ],
            }
        ],
        "temperature": temperature,
        "top_p": 0.9,
        "max_tokens": max_tokens,
    }
    if seed is not None:
        payload["seed"] = int(seed)
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key
    request = urllib.request.Request(
        config.base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        result = json.load(response)
    return str(result["choices"][0]["message"]["content"]).strip()


def atomize_caption(caption: str, *, limit: int = 16) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", caption).strip()
    try:
        decoded = json.loads(normalized)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, list):
        parts = [str(part) for part in decoded]
    else:
        parts = re.split(r"\s*[;\n]+\s*", normalized)
    if len([part for part in parts if part.strip()]) < 2:
        parts = re.split(r"\s*,\s*", normalized)
    atoms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        atom = re.sub(r"\s+", " ", part).strip(" .;,\t")
        key = atom.casefold()
        if not atom or key in seen:
            continue
        seen.add(key)
        atoms.append(atom)
        if len(atoms) >= limit:
            break
    return atoms


def ferment_video_frame(
    video_path: Path,
    frame_path: Path,
    config: JoyCaptionConfig,
    *,
    seed: int | None = None,
) -> tuple[str, list[str]]:
    extract_representative_frame(video_path, frame_path)
    caption = caption_image(frame_path, config, seed=seed)
    return caption, atomize_caption(caption)

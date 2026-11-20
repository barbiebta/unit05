from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return payload


def main() -> int:
    comfy_root = Path(os.environ.get("COMFYUI_ROOT", "/workspace/ComfyUI"))
    comfy_url = os.environ.get("UNIT05_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    template_path = Path(
        os.environ.get("UNIT05_TEMPLATE", "/opt/unit05/templates/dasiwa_ref2va_api_template.json")
    )
    requirement_path = Path(__file__).with_name("models.required.json")

    template = read_json(template_path)
    if not isinstance(template, dict):
        raise RuntimeError("The API template must contain a JSON object")
    required_classes = {
        str(node.get("class_type"))
        for node in template.values()
        if isinstance(node, dict) and node.get("class_type")
    }
    available_classes = set(get_json(f"{comfy_url}/object_info"))
    missing_classes = sorted(required_classes - available_classes)

    requirements = read_json(requirement_path)
    if not isinstance(requirements, list):
        raise RuntimeError("models.required.json must contain a list")
    missing_models = []
    for item in requirements:
        if not isinstance(item, dict):
            raise RuntimeError("Every model requirement must be an object")
        destination = str(item["destination"])
        filename = str(item["filename"])
        candidate = comfy_root / destination / filename
        if not candidate.is_file():
            missing_models.append(str(candidate))

    report = {
        "comfy_url": comfy_url,
        "template": str(template_path),
        "required_node_classes": len(required_classes),
        "missing_node_classes": missing_classes,
        "missing_models": missing_models,
        "ready": not missing_classes and not missing_models,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"preflight failed: {error}", file=sys.stderr)
        raise SystemExit(1)

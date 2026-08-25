from __future__ import annotations

import copy
import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

try:
    import websocket
except ImportError:  # The executor can still monitor through Comfy's history API.
    websocket = None

from .bundle import ExtractedBundle
from .media import probe_media


class ComfyError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagedPrompt:
    graph: dict[str, Any]
    staged_inputs: list[Path]


def _safe_filename(value: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    cleaned = "".join(character if character in allowed else "_" for character in value)
    return cleaned[:160] or "asset"


def _node_by_class(graph: dict[str, Any], class_type: str) -> tuple[str, dict[str, Any]]:
    matches = [(node_id, node) for node_id, node in graph.items() if node.get("class_type") == class_type]
    if len(matches) != 1:
        raise ComfyError(f"Expected exactly one {class_type} node, found {len(matches)}")
    return matches[0]


def build_prompt_graph(
    *,
    template: dict[str, Any],
    bundle: ExtractedBundle,
    comfy_input_dir: Path,
) -> StagedPrompt:
    graph = copy.deepcopy(template)
    manifest = bundle.manifest
    generation = manifest["generation"]
    director_id, director = _node_by_class(graph, "MiniMaxH3Director")
    director_inputs = director["inputs"]
    director_inputs.update(
        {
            "mode": "REF2VA",
            "prompt": bundle.prompt,
            "width": int(generation["width"]),
            "height": int(generation["height"]),
            "duration": int(generation["duration"]),
            "frame_rate": float(generation["fps"]),
            "ref_image_size": str(generation["ref_image_size"]),
        }
    )

    job_prefix = _safe_filename(bundle.job_id)
    staged_inputs: list[Path] = []
    timeline_items: list[dict[str, Any]] = []
    references = sorted(manifest["references"], key=lambda item: int(item.get("order", 0)))
    for index, reference in enumerate(references):
        relative = PurePosixPath(reference["path"])
        source = bundle.root.joinpath(*relative.parts)
        staged_name = f"dp_{job_prefix}_{index:02d}_{_safe_filename(source.name)}"
        staged_path = comfy_input_dir / staged_name
        comfy_input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged_path)
        staged_inputs.append(staged_path)

        probed = probe_media(source)
        kind = str(reference["kind"])
        source_duration = float(reference.get("source_duration") or probed["duration"] or 0)
        trim_start = float(reference.get("trim_start", 0)) if kind == "video" else 0.0
        trim_end = float(reference.get("trim_end", source_duration)) if kind == "video" else 0.0
        effective_duration = max(0.0, trim_end - trim_start) if kind == "video" else 1.0
        timeline_item: dict[str, Any] = {
            "id": f"{kind}-{bundle.job_id}-{index}",
            "enabled": True,
            "order": index,
            "slot": index,
            "start": float(reference.get("timeline_start", index)),
            "duration": effective_duration,
            "type": kind,
            "value": staged_name,
            "source_width": int(reference.get("source_width") or probed["width"] or 0),
            "source_height": int(reference.get("source_height") or probed["height"] or 0),
        }
        if source_duration:
            timeline_item["source_duration"] = source_duration
        if kind == "video":
            timeline_item["trim_start"] = trim_start
            timeline_item["trim_end"] = trim_end
        timeline_items.append(timeline_item)

    timeline = {
        "version": 1,
        "items": timeline_items,
        "prompt_blocks": [],
        "resolution": {
            "input_scaling": str(generation["input_scaling"]),
            "custom_width": int(generation["width"]),
            "custom_height": int(generation["height"]),
        },
        "resolved_prompt": bundle.prompt,
        "field_heights": {},
    }
    director_inputs["timeline_data"] = json.dumps(timeline, separators=(",", ":"))
    director_inputs["builder_state"] = json.dumps(
        {
            "version": 1,
            "mode": "REF2VA",
            "duration": int(generation["duration"]),
            "prompt_mode": "structured",
            "resolution": {
                "input_scaling": str(generation["input_scaling"]),
                "custom_width": int(generation["width"]),
                "custom_height": int(generation["height"]),
            },
        },
        separators=(",", ":"),
    )

    for node in graph.values():
        inputs = node.get("inputs", {})
        class_type = node.get("class_type")
        if class_type == "RandomNoise":
            inputs["noise_seed"] = int(generation["seed"])
        elif class_type == "KSamplerSelect":
            inputs["sampler_name"] = str(generation["sampler"])
        elif class_type == "BasicScheduler":
            inputs["steps"] = int(generation["steps"])
        elif class_type == "MiniMaxH3SigmaShift":
            inputs["shift_video"] = float(generation["shift_video"])
            inputs["shift_audio"] = float(generation["shift_audio"])
        elif class_type == "DaSiWa_EnhancedVideoCombine":
            output = generation.get("output") or {}
            inputs["filename_prefix"] = f"unit05/{job_prefix}/render"
            inputs["container"] = str(output.get("container", "Auto"))
            inputs["codec"] = str(output.get("codec", "Auto"))
            inputs["quality"] = int(output.get("quality", 21))
            inputs["audio_codec"] = str(output.get("audio_codec", "Auto"))
            inputs["audio_bitrate"] = str(output.get("audio_bitrate", "192k"))
            inputs["save_output"] = True

    if director_id not in graph:
        raise ComfyError("Director node disappeared while preparing the graph")
    return StagedPrompt(graph=graph, staged_inputs=staged_inputs)


class ComfyClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _json(self, method: str, path: str, payload: Any | None = None, timeout: float = 15) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"content-type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as error:
            raise ComfyError(f"Comfy {method} {path} failed: {error}") from error

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/system_stats", timeout=3)

    def object_info(self) -> dict[str, Any]:
        return self._json("GET", "/object_info", timeout=20)

    def queue(self) -> dict[str, Any]:
        return self._json("GET", "/queue", timeout=5)

    def history(self, prompt_id: str) -> dict[str, Any]:
        return self._json("GET", f"/history/{urllib.parse.quote(prompt_id)}", timeout=10)

    def submit(self, graph: dict[str, Any], client_id: str) -> str:
        response = self._json("POST", "/prompt", {"prompt": graph, "client_id": client_id}, timeout=30)
        if not isinstance(response, dict) or not response.get("prompt_id"):
            raise ComfyError(f"Comfy rejected the prompt: {response}")
        return str(response["prompt_id"])

    def wait_for_completion(
        self,
        *,
        prompt_id: str,
        client_id: str,
        update: Callable[[dict[str, Any]], None],
        poll_seconds: float,
    ) -> dict[str, Any]:
        websocket_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        websocket_url = f"{websocket_url}/ws?clientId={urllib.parse.quote(client_id)}"
        connection: Any | None = None
        last_history_check = 0.0
        try:
            try:
                if websocket is None:
                    raise RuntimeError("websocket-client is not installed")
                connection = websocket.create_connection(websocket_url, timeout=2)
                connection.settimeout(1)
            except Exception as error:
                update({"event": "websocket_unavailable", "detail": str(error)})
                connection = None

            while True:
                if connection is not None:
                    try:
                        raw = connection.recv()
                        if isinstance(raw, str):
                            message = json.loads(raw)
                            data = message.get("data", {})
                            if data.get("prompt_id") in {None, prompt_id}:
                                update({"event": message.get("type"), **data})
                    except Exception as error:
                        if websocket is not None and isinstance(error, websocket.WebSocketTimeoutException):
                            continue
                        update({"event": "websocket_lost", "detail": str(error)})
                        connection.close()
                        connection = None
                now = time.monotonic()
                if now - last_history_check >= poll_seconds:
                    last_history_check = now
                    history = self.history(prompt_id)
                    if prompt_id in history:
                        record = history[prompt_id]
                        if record.get("status", {}).get("completed"):
                            if record.get("status", {}).get("status_str") != "success":
                                raise ComfyError(f"Comfy execution failed: {record.get('status')}")
                            return record
                time.sleep(0.1 if connection is not None else poll_seconds)
        finally:
            if connection is not None:
                connection.close()


def collect_output_files(history_record: dict[str, Any], comfy_output_dir: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for node_output in history_record.get("outputs", {}).values():
        for key in ("images", "gifs", "audio"):
            for item in node_output.get(key, []) if isinstance(node_output, dict) else []:
                if item.get("type") != "output" or not item.get("filename"):
                    continue
                subfolder = PurePosixPath(str(item.get("subfolder", "")))
                if subfolder.is_absolute() or ".." in subfolder.parts:
                    raise ComfyError("Comfy returned an unsafe output subfolder")
                path = comfy_output_dir.joinpath(*subfolder.parts, str(item["filename"]))
                resolved = path.resolve()
                if not resolved.is_relative_to(comfy_output_dir.resolve()):
                    raise ComfyError("Comfy returned an output outside its output directory")
                if resolved.is_file() and resolved not in seen:
                    seen.add(resolved)
                    found.append(resolved)
    if not found:
        raise ComfyError("Comfy completed successfully but returned no output files")
    return found

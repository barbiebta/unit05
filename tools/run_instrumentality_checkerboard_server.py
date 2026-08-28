"""Submit six deterministic bilateral H3 gap fills to local ComfyUI."""

import copy
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


COMFY = "http://127.0.0.1:8188"
BASE_PROMPT = Path("/workspace/ComfyUI/output/unit05_drift_ab/REF35.api_prompt.json")
OUT_DIR = Path("/workspace/ComfyUI/output/instrumentality_checkerboard_0312000")
SOURCE = "source_0312000_65s_h3.mp4"
FPS = 24.0
HANDLE_FRAMES = 39
GAP_FRAMES = 114
GAP_STARTS = [120, 354, 588, 822, 1056, 1290]
PROMPT = (
    "The two locked endpoint clips belong to one uninterrupted shot. "
    "Generate the natural continuous motion, action, camera movement, and sound "
    "that bridge their exact states. Arrive smoothly and precisely at the locked ending."
)


def post_json(path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        COMFY + path, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def get_json(path):
    with urllib.request.urlopen(COMFY + path, timeout=60) as response:
        return json.load(response)


def make_prompt(index, gap_start):
    prompt = copy.deepcopy(json.loads(BASE_PROMPT.read_text(encoding="utf-8")))
    # The saved continuation graph has a second OUTPUT_NODE that writes its old
    # source-latent chain. Remove that independent sink so Comfy executes only
    # the Instrumentality result rooted at DaSiWa_EnhancedVideoCombine.
    prompt.pop("1512:2742", None)
    gap_end = gap_start + GAP_FRAMES
    left_start = (gap_start - HANDLE_FRAMES) / FPS
    right_start = gap_end / FPS

    director = prompt["2730"]["inputs"]
    director.update({
        "mode": "T2VA",
        "prompt": PROMPT,
        "width": 1344,
        "height": 768,
        "duration": 8,
        "ref_image_size": "match",
        "timeline_data": '{"version":1,"items":[],"prompt_blocks":[]}',
        "builder_state": "",
        "frame_rate": 24.0,
    })

    prompt["1512:2600"]["inputs"]["noise_seed"] = 42069 + index
    prompt["1512:2679"]["inputs"]["steps"] = 25
    prompt["inst_left"] = {
        "class_type": "VHS_LoadVideoFFmpeg",
        "inputs": {
            "video": SOURCE,
            "force_rate": 24.0,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": HANDLE_FRAMES,
            "start_time": left_start,
            "format": "H3",
        },
        "_meta": {"title": "Instrumentality left 39-frame AV handle"},
    }
    prompt["inst_right"] = {
        "class_type": "VHS_LoadVideoFFmpeg",
        "inputs": {
            "video": SOURCE,
            "force_rate": 24.0,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": HANDLE_FRAMES,
            "start_time": right_start,
            "format": "H3",
        },
        "_meta": {"title": "Instrumentality right 39-frame AV handle"},
    }
    prompt["inst_bridge"] = {
        "class_type": "MiniMaxH3Instrumentality",
        "inputs": {
            "latent": ["1512:2700", 1],
            "vae": ["1512:2584", 0],
            "audio_vae": ["1512:2585", 0],
            "start_frames": ["inst_left", 0],
            "start_audio": ["inst_left", 2],
            "end_frames": ["inst_right", 0],
            "end_audio": ["inst_right", 2],
            "start_fps": 24.0,
            "end_fps": 24.0,
            "preserve_frames": HANDLE_FRAMES,
            "crop": "disabled",
        },
        "_meta": {"title": "Instrumentality bilateral AV mask"},
    }
    prompt["1512:2668"]["inputs"]["latent_image"] = ["inst_bridge", 0]
    prompt["inst_extract"] = {
        "class_type": "MiniMaxH3InstrumentalityExtractMiddle",
        "inputs": {
            "images": ["1512:2671", 0],
            "audio": ["1512:2669", 0],
            "left_context_frames": ["inst_bridge", 2],
            "right_context_frames": ["inst_bridge", 2],
            "frame_rate": 24.0,
        },
        "_meta": {"title": "Instrumentality exact 114-frame generated gap"},
    }
    output = prompt["2568"]["inputs"]
    output["images"] = ["inst_extract", 0]
    output["audio"] = ["inst_extract", 1]
    output["frame_rate"] = 24.0
    output["filename_prefix"] = f"instrumentality_checkerboard_0312000/gap_{index:02d}"
    output["crop_to_audio"] = False
    output["save_output"] = True

    metadata = {
        "index": index,
        "source_gap_start_frame": gap_start,
        "source_gap_end_frame_exclusive": gap_end,
        "source_gap_start_seconds": gap_start / FPS,
        "source_gap_end_seconds": gap_end / FPS,
        "left_handle_start_frame": gap_start - HANDLE_FRAMES,
        "right_handle_start_frame": gap_end,
        "seed": 42069 + index,
        "prompt": PROMPT,
        "steps": 25,
    }
    return prompt, metadata


def wait_for_job(prompt_id):
    started = time.monotonic()
    while True:
        history = get_json("/history/" + prompt_id)
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error" or status.get("completed") is False:
                raise RuntimeError(json.dumps(status, indent=2))
            return entry, time.monotonic() - started
        time.sleep(5)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": SOURCE,
        "source_absolute_start_seconds": 192.0,
        "output_window_frames": 1440,
        "output_window_seconds": 60.0,
        "fps": FPS,
        "gap_frames": GAP_FRAMES,
        "gap_seconds": GAP_FRAMES / FPS,
        "handle_frames_each_side": HANDLE_FRAMES,
        "jobs": [],
    }
    for index, gap_start in enumerate(GAP_STARTS, 1):
        prompt, metadata = make_prompt(index, gap_start)
        api_path = OUT_DIR / f"gap_{index:02d}.api_prompt.json"
        api_path.write_text(json.dumps(prompt, indent=2), encoding="utf-8")
        print(
            f"SUBMIT gap {index}: source {metadata['source_gap_start_seconds']:.2f}-"
            f"{metadata['source_gap_end_seconds']:.2f}s seed {metadata['seed']}",
            flush=True,
        )
        try:
            response = post_json("/prompt", {
                "prompt": prompt,
                "client_id": "instrumentality-checkerboard-0312000",
            })
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", "replace")) from exc
        if response.get("node_errors"):
            raise RuntimeError(json.dumps(response, indent=2))
        prompt_id = response["prompt_id"]
        print("RUNNING", prompt_id, flush=True)
        history, elapsed = wait_for_job(prompt_id)
        metadata.update({
            "prompt_id": prompt_id,
            "elapsed_seconds": elapsed,
            "outputs": history.get("outputs", {}),
        })
        (OUT_DIR / f"gap_{index:02d}.history.json").write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )
        manifest["jobs"].append(metadata)
        (OUT_DIR / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(f"DONE gap {index} in {elapsed:.2f}s", flush=True)
    print("ALL SIX GAPS COMPLETE", flush=True)


if __name__ == "__main__":
    main()

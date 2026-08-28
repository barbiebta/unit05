"""Replace every source-derived interval in the phase-one checkerboard.

The six existing generated islands remain untouched. Five interior 5-second
intervals are regenerated bilaterally. The 1.5-second tail and 5-second head
are treated as one circular interval, conditioned by the final generated
island on the left and the first generated island on the right.
"""

import copy
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


COMFY = "http://127.0.0.1:8188"
BASE_PROMPT = Path("/workspace/ComfyUI/output/unit05_drift_ab/REF35.api_prompt.json")
OUT_DIR = Path("/workspace/ComfyUI/output/instrumentality_checkerboard_phase2_text")
SOURCE = "instrumentality_checkerboard_phase1_60s.mp4"
FPS = 24.0
HANDLE_FRAMES = 39
PROMPT = (
    "The two locked endpoint clips belong to one uninterrupted shot. "
    "Generate the natural continuous motion, action, camera movement, and sound "
    "that bridge their exact states. Arrive smoothly and precisely at the locked ending. "
    "Huge Flashing text (in a bold sans-serif font) describing the situation appears "
    "in brief splashes in between the characters and the scenery."
)

# Native output lengths obey H3's joint AV lattice. The later local assembly
# normalizes 148 -> 156 frames for the circular bridge and 114 -> 120 for each
# interior fill, retaining both endpoint states and the exact 60-second timeline.
JOBS = [
    {
        "name": "bridge_loop",
        "duration": 9,
        "native_middle_frames": 148,
        "desired_frames": 156,
        "left_start_frame": 1365,
        "right_start_frame": 120,
        "description": "circular tail+head bridge: frames 1404..1439 then 0..119",
    },
    *[
        {
            "name": f"fill_{i:02d}",
            "duration": 8,
            "native_middle_frames": 114,
            "desired_frames": 120,
            "left_start_frame": gap_start - HANDLE_FRAMES,
            "right_start_frame": gap_start + 120,
            "description": f"replace phase-one source frames {gap_start}..{gap_start + 119}",
        }
        for i, gap_start in enumerate([234, 468, 702, 936, 1170], 2)
    ],
]


def post_json(path, payload):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        COMFY + path, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def get_json(path):
    with urllib.request.urlopen(COMFY + path, timeout=60) as response:
        return json.load(response)


def make_prompt(index, spec):
    prompt = copy.deepcopy(json.loads(BASE_PROMPT.read_text(encoding="utf-8")))
    prompt.pop("1512:2742", None)

    director = prompt["2730"]["inputs"]
    director.update({
        "mode": "T2VA",
        "prompt": PROMPT,
        "width": 1344,
        "height": 768,
        "duration": spec["duration"],
        "ref_image_size": "match",
        "timeline_data": '{"version":1,"items":[],"prompt_blocks":[]}',
        "builder_state": "",
        "frame_rate": FPS,
    })

    prompt["1512:2600"]["inputs"]["noise_seed"] = 42069 + index
    prompt["1512:2679"]["inputs"]["steps"] = 25
    for node_id, start_frame, side in (
        ("inst_left", spec["left_start_frame"], "left"),
        ("inst_right", spec["right_start_frame"], "right"),
    ):
        prompt[node_id] = {
            "class_type": "VHS_LoadVideoFFmpeg",
            "inputs": {
                "video": SOURCE,
                "force_rate": FPS,
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": HANDLE_FRAMES,
                "start_time": start_frame / FPS,
                "format": "H3",
            },
            "_meta": {"title": f"Inverse checkerboard {side} 39-frame AV handle"},
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
            "start_fps": FPS,
            "end_fps": FPS,
            "preserve_frames": HANDLE_FRAMES,
            "crop": "disabled",
        },
        "_meta": {"title": "Instrumentality inverse bilateral AV mask"},
    }
    prompt["1512:2668"]["inputs"]["latent_image"] = ["inst_bridge", 0]
    prompt["inst_extract"] = {
        "class_type": "MiniMaxH3InstrumentalityExtractMiddle",
        "inputs": {
            "images": ["1512:2671", 0],
            "audio": ["1512:2669", 0],
            "left_context_frames": ["inst_bridge", 2],
            "right_context_frames": ["inst_bridge", 2],
            "frame_rate": FPS,
        },
        "_meta": {"title": f"Extract native {spec['native_middle_frames']}-frame fill"},
    }

    output = prompt["2568"]["inputs"]
    output.update({
        "images": ["inst_extract", 0],
        "audio": ["inst_extract", 1],
        "frame_rate": FPS,
        "filename_prefix": f"instrumentality_checkerboard_phase2_text/{spec['name']}",
        "crop_to_audio": False,
        "save_output": True,
    })

    metadata = {
        **spec,
        "index": index,
        "left_start_seconds": spec["left_start_frame"] / FPS,
        "right_start_seconds": spec["right_start_frame"] / FPS,
        "seed": 42069 + index,
        "prompt": PROMPT,
        "steps": 25,
        "handle_frames_each_side": HANDLE_FRAMES,
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
        "phase_one_source": SOURCE,
        "purpose": "inverse checkerboard; no original source frames remain after assembly",
        "output_window_frames": 1440,
        "output_window_seconds": 60.0,
        "fps": FPS,
        "prompt": PROMPT,
        "jobs": [],
    }
    for index, spec in enumerate(JOBS, 1):
        prompt, metadata = make_prompt(index, spec)
        (OUT_DIR / f"{spec['name']}.api_prompt.json").write_text(
            json.dumps(prompt, indent=2), encoding="utf-8"
        )
        print(
            f"SUBMIT {spec['name']}: native {spec['native_middle_frames']} -> "
            f"timeline {spec['desired_frames']} frames; seed {metadata['seed']}",
            flush=True,
        )
        try:
            response = post_json("/prompt", {
                "prompt": prompt,
                "client_id": "instrumentality-checkerboard-phase2-text",
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
        (OUT_DIR / f"{spec['name']}.history.json").write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )
        manifest["jobs"].append(metadata)
        (OUT_DIR / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(f"DONE {spec['name']} in {elapsed:.2f}s", flush=True)
    print("ALL SIX INVERSE FILLS COMPLETE", flush=True)


if __name__ == "__main__":
    main()

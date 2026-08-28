"""Iteratively replace random, freely overlapping 39-frame holes.

Each hole is selected immediately before submission from the currently evolving
full-length video. Later holes may overlap earlier generations; arbitrary small
pieces of the original may survive. Three distinct character images are attached
as real Ref2VA references for every generation.
"""

import argparse
import copy
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UNIT05_SOURCE = Path(os.environ.get("UNIT05_SOURCE", "/workspace/unit05"))
if not UNIT05_SOURCE.exists():
    UNIT05_SOURCE = Path(__file__).resolve().parent / "unit05"
sys.path.insert(0, str(UNIT05_SOURCE))

from unit05.joycaption import JoyCaptionConfig, ferment_video_frame, sample_atoms
from unit05.living_prompt import (
    compiled_text,
    load_state as load_living_state,
    record_history,
    replace_fermented,
    save_state as save_living_state,
    state_lock as living_state_lock,
)
from unit05.temporal_steering import choose_temporal_steering


COMFY = "http://127.0.0.1:8188"
BASE_PROMPT = Path("/workspace/ComfyUI/output/unit05_drift_ab/REF35.api_prompt.json")
COMFY_ROOT = Path("/workspace/ComfyUI")
SOURCE_NAME = "lumina_hey_babes_1344x768_24fps.mp4"
SOURCE_PATH = COMFY_ROOT / "input" / SOURCE_NAME
OUT_DIR = COMFY_ROOT / "output" / "lumina_random_mutation_cascade"
CONTEXT_DIR = COMFY_ROOT / "input" / "lumina_random_mutation_contexts"
DOLL_REF_DIR = COMFY_ROOT / "input" / "lumina_doll_refs"
STATE_PATH = OUT_DIR / "dashboard_state.json"
LIVING_PROMPT_PATH = OUT_DIR / "living_prompt.json"
JOY_FRAME_DIR = OUT_DIR / "joycaption_frames"
JOY_CONFIG_PATH = Path(
    os.environ.get("UNIT05_JOYCAPTION_CONFIG", "/root/.config/unit05/joycaption.env")
)
FPS = 24
TOTAL_FRAMES = 5441
BLOCK_FRAMES = 39
BLOCK_COUNT = (TOTAL_FRAMES + BLOCK_FRAMES - 1) // BLOCK_FRAMES
CONTEXT_FRAMES = 90
ORDER_SEED = 54239090
TEMPORAL_MODES = {
    "normal": {
        "source_context_frames": 90,
        "edge_context_frames": 39,
        "director_duration": 5,
        "description": "compress 90 source frames into 39 latent frames",
    },
    "no_compression": {
        "source_context_frames": 39,
        "edge_context_frames": 39,
        "director_duration": 5,
        "description": "preserve 39 source frames as 39 latent frames",
    },
    "slow": {
        "source_context_frames": 39,
        "edge_context_frames": 90,
        "director_duration": 9,
        "description": "expand 39 source frames into 90 latent frames",
    },
    "double_slow": {
        "source_context_frames": 39,
        "edge_context_frames": 141,
        "director_duration": 13,
        "description": "expand 39 source frames into 141 latent frames",
    },
}
PROMPT = (
    "The two temporally compressed endpoint clips belong to one uninterrupted shot. "
    "Regenerate the missing moment as natural continuous motion, action, camera movement, "
    "and sound that bridge their states. Arrive smoothly and precisely at the locked ending. "
    "Use <Picture 1>, <Picture 2>, and <Picture 3> as mandatory character-design sources. "
    "The endpoint clips control timing, pose, composition, and continuity, but they do not "
    "control character identity. Unmistakably redesign every visible character from one of "
    "the three reference pictures, carrying over distinctive species, anatomy, face, hair, "
    "skin or fur, body shape, and costume traits instead of preserving the incoming performers. "
    "The characters' faces and species visibly derive from <Picture 1>. Their bodies and "
    "silhouettes also visibly derive from <Picture 1>. Inherited garment slogans and "
    "lettering are unstable memory: they tend to drift into partial, incorrect, "
    "nonalphabetic, or abstract marks rather than being faithfully repeated."
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def run(command):
    subprocess.run(command, check=True)


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


def probe_frames(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
            str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_state(**values):
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    state.update(values)
    state["updated_unix"] = time.time()
    atomic_json(STATE_PATH, state)


def available_character_references():
    references = sorted(
        path.name for path in DOLL_REF_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if len(references) < 3:
        raise RuntimeError(f"need at least three character references in {DOLL_REF_DIR}")
    return references


def build_frame_owners(jobs):
    """Map each output frame to the latest source that owns it."""
    owners = [(SOURCE_PATH, frame) for frame in range(TOTAL_FRAMES)]
    for job in jobs:
        start = int(job["hole_start_frame"])
        source = Path(job["output_path"])
        for offset in range(BLOCK_FRAMES):
            owners[start + offset] = (source, offset)
    return owners


def timeline_parts(start_frame, length, owners):
    """Collapse an evolving frame interval into contiguous source-file runs."""
    selected = owners[int(start_frame):int(start_frame) + int(length)]
    if len(selected) != int(length):
        raise RuntimeError("requested context falls outside the evolving timeline")
    parts = []
    for source, local_frame in selected:
        if parts and parts[-1][0] == source and parts[-1][2] == local_frame:
            parts[-1] = (source, parts[-1][1], local_frame + 1)
        else:
            parts.append((source, local_frame, local_frame + 1))
    return parts


def make_context(path, start_frame, owners, context_frames, reverse=False):
    context_frames = int(context_frames)
    parts = timeline_parts(start_frame, context_frames, owners)
    command = ["ffmpeg", "-y"]
    for source, _, _ in parts:
        command += ["-i", str(source)]
    filters = []
    sequence = []
    for i, (_, start, end) in enumerate(parts):
        filters.append(
            f"[{i}:v]trim=start_frame={start}:end_frame={end},"
            f"setpts=PTS-STARTPTS,setsar=1[v{i}]"
        )
        filters.append(
            f"[{i}:a]atrim=start={start / FPS:.12f}:end={end / FPS:.12f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
        sequence.append(f"[v{i}][a{i}]")
    filters.append("".join(sequence) + f"concat=n={len(parts)}:v=1:a=1[cv][ca]")
    if reverse:
        filters.append("[cv]reverse[outv]")
        filters.append(
            f"[ca]apad,atrim=start=0:end={context_frames / FPS},areverse[outa]"
        )
    else:
        filters.append("[cv]null[outv]")
        filters.append(f"[ca]apad,atrim=start=0:end={context_frames / FPS}[outa]")
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[outv]", "-map", "[outa]", "-r", str(FPS),
        "-frames:v", str(context_frames),
        "-c:v", "libx264", "-preset", "fast", "-crf", "10", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path),
    ]
    run(command)
    if probe_frames(path) != context_frames:
        raise RuntimeError(f"context {path} is not exactly {context_frames} frames")


def make_prompt(
    step, hole_start, left_name, right_name, character_refs, steering, temporal,
    living_text="",
):
    prompt = copy.deepcopy(json.loads(BASE_PROMPT.read_text(encoding="utf-8")))
    prompt.pop("1512:2742", None)
    full_prompt = PROMPT
    if living_text:
        full_prompt += "\n\nCurrent prompt fragments: " + living_text
    timeline = {
        "version": 1,
        "items": [
            {
                "id": f"character-reference-{slot + 1}-step-{step:04d}",
                "enabled": True,
                "order": slot,
                "slot": slot,
                "start": 0,
                "duration": 1,
                "type": "image",
                "value": f"lumina_doll_refs/{filename}",
                "thumbnail": None,
            }
            for slot, filename in enumerate(character_refs)
        ],
        "prompt_blocks": [],
        # Director's UI label is "Native (ShortEdge 2048px)"; its canonical
        # backend value is "Auto".
        "resolution": {"input_scaling": "Auto"},
    }
    prompt["2730"]["inputs"].update({
        "mode": "REF2VA",
        "prompt": full_prompt,
        "external_prompt_overwrite": full_prompt,
        "width": 1344,
        "height": 768,
        "duration": temporal["director_duration"],
        "ref_image_size": "max",
        "timeline_data": json.dumps(timeline, separators=(",", ":")),
        "builder_state": "",
        "frame_rate": float(FPS),
    })
    prompt["1512:2600"]["inputs"]["noise_seed"] = 61000 + step
    prompt["1512:2679"]["inputs"]["steps"] = 25

    for node_id, filename, side in (
        ("compressed_left", left_name, "left"),
        ("compressed_right", right_name, "right"),
    ):
        prompt[node_id] = {
            "class_type": "VHS_LoadVideoFFmpeg",
            "inputs": {
                "video": filename,
                "force_rate": float(FPS),
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": temporal["source_context_frames"],
                "start_time": 0.0,
                "format": "H3",
            },
            "_meta": {
                "title": (
                    f"Mutation {side} {temporal['source_context_frames']}-frame "
                    f"evolving AV context"
                )
            },
        }

    prompt["compressed_bridge"] = {
        "class_type": "MiniMaxH3CompressedContextBridge",
        "inputs": {
            "latent": ["1512:2700", 1],
            "vae": ["1512:2584", 0],
            "audio_vae": ["1512:2585", 0],
            "start_frames": ["compressed_left", 0],
            "start_audio": ["compressed_left", 2],
            "end_frames": ["compressed_right", 0],
            "end_audio": ["compressed_right", 2],
            "start_fps": float(FPS),
            "end_fps": float(FPS),
            "source_context_frames": temporal["source_context_frames"],
            "compressed_context_frames": temporal["edge_context_frames"],
            "crop": "disabled",
        },
        "_meta": {"title": temporal["description"]},
    }
    prompt["1512:2668"]["inputs"]["latent_image"] = ["compressed_bridge", 0]
    prompt["compressed_middle"] = {
        "class_type": "MiniMaxH3CompressGeneratedMiddleLatent",
        "inputs": {
            "latent": ["1512:2668", 0],
            "edge_context_frames": temporal["edge_context_frames"],
            "output_frames": BLOCK_FRAMES,
        },
        "_meta": {"title": "Compress native generated center to exact 39-frame latent"},
    }
    prompt["1512:2671"]["inputs"]["samples"] = ["compressed_middle", 0]
    prompt["1512:2669"]["inputs"]["samples"] = ["compressed_middle", 0]
    prompt["2568"]["inputs"].update({
        "images": ["1512:2671", 0],
        "audio": ["1512:2669", 0],
        "frame_rate": float(FPS),
        "filename_prefix": (
            f"lumina_random_mutation_cascade/step_{step:04d}_hole_{hole_start:05d}"
        ),
        "crop_to_audio": False,
        "save_output": True,
    })
    return prompt


def prepare_living_prompt(step):
    with living_state_lock(LIVING_PROMPT_PATH):
        state = load_living_state(LIVING_PROMPT_PATH)
        state["active_step"] = int(step)
        save_living_state(LIVING_PROMPT_PATH, state)
        return compiled_text(state), copy.deepcopy(state)


def record_living_prompt(step, full_prompt, snapshot):
    with living_state_lock(LIVING_PROMPT_PATH):
        current = load_living_state(LIVING_PROMPT_PATH)
        history_state = copy.deepcopy(snapshot)
        history_state["history"] = list(current.get("history") or [])
        record_history(
            history_state, step=step, compiled_prompt=full_prompt,
        )
        current["history"] = history_state["history"]
        current["active_step"] = int(step)
        save_living_state(LIVING_PROMPT_PATH, current)


def ferment_finished_chunk(step, output_path):
    frame_path = JOY_FRAME_DIR / f"step_{int(step):04d}.jpg"
    config = JoyCaptionConfig.from_env_file(JOY_CONFIG_PATH)
    caption, candidate_atoms = ferment_video_frame(
        Path(output_path), frame_path, config, seed=23000 + int(step)
    )
    atoms = sample_atoms(candidate_atoms, limit=3, seed=23000 + int(step))
    with living_state_lock(LIVING_PROMPT_PATH):
        state = load_living_state(LIVING_PROMPT_PATH)
        replace_fermented(
            state, atoms, step=int(step), caption=caption,
            frame_path=str(frame_path),
        )
        for entry in reversed(state.get("history") or []):
            if int(entry.get("step", -1)) == int(step):
                entry["result_caption"] = caption
                entry["result_atoms"] = candidate_atoms
                entry["injected_atoms"] = atoms
                break
        state["active_step"] = None
        save_living_state(LIVING_PROMPT_PATH, state)
    return caption, atoms, frame_path


def assemble_current(path, owners):
    parts = timeline_parts(0, TOTAL_FRAMES, owners)
    unique_sources = []
    for source, _, _ in parts:
        if source not in unique_sources:
            unique_sources.append(source)
    command = ["ffmpeg", "-y"]
    for source in unique_sources:
        # Dozens of independent AV1 decoders otherwise each create their own
        # thread pool and eventually exhaust RAM as the cascade grows.
        command += ["-threads", "1", "-i", str(source)]
    input_index = {source: index for index, source in enumerate(unique_sources)}
    filters = []
    sequence = []
    for part_index, (source, start, end) in enumerate(parts):
        source_index = input_index[source]
        filters.append(
            f"[{source_index}:v]trim=start_frame={start}:end_frame={end},"
            f"setpts=PTS-STARTPTS,setsar=1[v{part_index}]"
        )
        filters.append(
            f"[{source_index}:a]atrim=start={start / FPS:.12f}:end={end / FPS:.12f},"
            f"asetpts=PTS-STARTPTS[a{part_index}]"
        )
        sequence.append(f"[v{part_index}][a{part_index}]")
    filters.append("".join(sequence) + f"concat=n={len(parts)}:v=1:a=1[cv][ca]")
    filters.append(f"[ca]apad,atrim=start=0:end={TOTAL_FRAMES / FPS:.12f}[outa]")
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[cv]", "-map", "[outa]", "-r", str(FPS),
        "-frames:v", str(TOTAL_FRAMES),
        "-c:v", "libx264", "-threads", "0", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(path),
    ]
    run(command)
    if probe_frames(path) != TOTAL_FRAMES:
        raise RuntimeError(f"assembled current video is not exactly {TOTAL_FRAMES} frames")


def recover_orphaned_job(manifest, owners, manifest_path):
    """Finish bookkeeping if the controller restarted while Comfy kept rendering."""
    if not STATE_PATH.exists():
        return owners
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return owners
    step = int(state.get("active_step") or 0)
    hole_start = state.get("active_hole_start")
    prompt_id = state.get("active_prompt_id")
    expected_step = len(manifest["jobs"]) + 1
    if step != expected_step or hole_start is None or not prompt_id:
        return owners
    hole_start = int(hole_start)
    print(
        f"RECOVER active step {step:04d} hole {hole_start} prompt {prompt_id}",
        flush=True,
    )
    history, recovery_wait = wait_for_job(prompt_id)
    history_path = OUT_DIR / f"step_{step:04d}_hole_{hole_start:05d}.history.json"
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    prompt_path = OUT_DIR / f"step_{step:04d}_hole_{hole_start:05d}.api_prompt.json"
    prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    candidates = sorted(
        OUT_DIR.glob(f"step_{step:04d}_hole_{hole_start:05d}_*_audio.webm"),
        key=lambda item: item.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError("recovered Comfy job has no expected hole output")
    output_path = candidates[-1]
    frames = probe_frames(output_path)
    if frames != BLOCK_FRAMES:
        raise RuntimeError(
            f"recovered hole has {frames} frames, expected {BLOCK_FRAMES}"
        )
    elapsed = max(
        recovery_wait,
        output_path.stat().st_mtime - prompt_path.stat().st_mtime,
    )
    steering = state.get("active_steering") or {
        "text": "", "inverted_bookends": False,
        "temporal_mode": "normal", "version": 0,
    }
    temporal_mode = str(state.get("active_temporal_mode") or "normal")
    if temporal_mode not in TEMPORAL_MODES:
        temporal_mode = "normal"
    temporal = TEMPORAL_MODES[temporal_mode]
    inverted = bool(state.get("active_inverted_bookends", False))
    left_name = prompt["compressed_left"]["inputs"]["video"]
    right_name = prompt["compressed_right"]["inputs"]["video"]
    character_refs = list(state.get("active_character_references") or [])
    full_prompt = prompt["2730"]["inputs"]["external_prompt_overwrite"]
    fermentation = {}
    with living_state_lock(LIVING_PROMPT_PATH):
        living = load_living_state(LIVING_PROMPT_PATH)
        if not any(int(item.get("step", -1)) == step for item in living.get("history") or []):
            record_history(living, step=step, compiled_prompt=full_prompt)
        save_living_state(LIVING_PROMPT_PATH, living)
    try:
        caption, atoms, frame_path = ferment_finished_chunk(step, output_path)
        fermentation = {
            "caption": caption, "atoms": atoms, "frame_path": str(frame_path),
        }
    except Exception as exc:
        fermentation = {"error": str(exc)}
        print(f"RECOVERY FERMENTATION FAILED step {step:04d}: {exc}", flush=True)
    manifest["jobs"].append({
        "step": step,
        "hole_start_frame": hole_start,
        "hole_end_frame_exclusive": hole_start + BLOCK_FRAMES,
        "seed": int(state.get("active_seed") or (61000 + step)),
        "character_references": character_refs,
        "steering": steering,
        "inverted_bookends": inverted,
        "temporal_mode": temporal_mode,
        "source_context_frames": temporal["source_context_frames"],
        "latent_edge_context_frames": temporal["edge_context_frames"],
        "director_duration": temporal["director_duration"],
        "full_prompt": full_prompt,
        "prompt_atoms": state.get("active_prompt_atoms"),
        "fermentation": fermentation,
        "prompt_id": prompt_id,
        "elapsed_seconds": elapsed,
        "left_context": left_name,
        "right_context": right_name,
        "output_path": str(output_path),
        "output_frames": frames,
        "recovered_after_controller_restart": True,
    })
    current_pending = OUT_DIR / "CURRENT_MUTATION_CASCADE.pending.mp4"
    current_path = OUT_DIR / "CURRENT_MUTATION_CASCADE.mp4"
    owners = build_frame_owners(manifest["jobs"])
    print("ASSEMBLE recovered live current version", flush=True)
    assemble_current(current_pending, owners)
    current_pending.replace(current_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_state(
        status="ready",
        completed_steps=step,
        active_step=None,
        active_block=None,
        active_hole_start=None,
        active_prompt_id=None,
        active_queue_id=None,
        active_steering=None,
        active_full_prompt=None,
        active_prompt_atoms=None,
        current_video="/current.mp4",
        video_version=step,
        last_completed_hole_start=hole_start,
        last_elapsed_seconds=elapsed,
        last_chunk_path=str(output_path),
        last_character_references=character_refs,
        last_rabbit_reference=" · ".join(character_refs),
        joycaption_error=fermentation.get("error"),
    )
    print(f"RECOVERED step {step:04d} hole {hole_start}", flush=True)
    return owners


def main(limit=None):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "manifest.json"
    character_references = available_character_references()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source") != SOURCE_NAME or int(manifest.get("total_frames", 0)) != TOTAL_FRAMES:
            raise RuntimeError("saved cascade manifest belongs to a different source")
    else:
        manifest = {
            "source": SOURCE_NAME,
            "fps": FPS,
            "total_frames": TOTAL_FRAMES,
            "hole_frames": BLOCK_FRAMES,
            "continuous": True,
            "context_frames_each_side": CONTEXT_FRAMES,
            "temporal_modes": TEMPORAL_MODES,
            "random_seed": ORDER_SEED,
            "prompt": PROMPT,
            "character_reference_pool": character_references,
            "character_references_per_generation": 3,
            "jobs": [],
        }
    manifest.setdefault("temporal_modes", TEMPORAL_MODES)
    owners = build_frame_owners(manifest["jobs"])
    current_path = OUT_DIR / "CURRENT_MUTATION_CASCADE.mp4"
    if not manifest["jobs"] and not current_path.exists():
        shutil.copy2(SOURCE_PATH, current_path)
    owners = recover_orphaned_job(manifest, owners, manifest_path)
    start_step = len(manifest["jobs"]) + 1
    final_step = None if limit is None else start_step + limit - 1

    write_state(
        status="ready",
        completed_steps=len(manifest["jobs"]),
        total_steps=None,
        continuous=True,
        current_video=(
            "/current.mp4" if (OUT_DIR / "CURRENT_MUTATION_CASCADE.mp4").exists() else None
        ),
        video_version=len(manifest["jobs"]),
    )

    step = start_step
    while final_step is None or step <= final_step:
        living_text, living_snapshot = prepare_living_prompt(step)
        steering = choose_temporal_steering(living_text)
        inverted = steering["inverted_bookends"]
        temporal_mode = steering["temporal_mode"]
        temporal = TEMPORAL_MODES[temporal_mode]
        source_context_frames = temporal["source_context_frames"]
        step_random = random.Random(ORDER_SEED * 1_000_000 + step)
        minimum_start = source_context_frames
        maximum_start = TOTAL_FRAMES - BLOCK_FRAMES - source_context_frames
        hole_start = step_random.randint(minimum_start, maximum_start)
        selected_refs = step_random.sample(character_references, 3)
        suffix = f"_{temporal_mode}" + ("_inverted" if inverted else "")
        chronological_left = CONTEXT_DIR / f"step_{step:04d}_left{suffix}.mkv"
        chronological_right = CONTEXT_DIR / f"step_{step:04d}_right{suffix}.mkv"
        make_context(
            chronological_left,
            hole_start - source_context_frames,
            owners,
            source_context_frames,
            reverse=inverted,
        )
        make_context(
            chronological_right,
            hole_start + BLOCK_FRAMES,
            owners,
            source_context_frames,
            reverse=inverted,
        )
        if inverted:
            # The future/right window, reversed, becomes the starting bookend;
            # the past/left window, reversed, becomes the ending bookend.
            start_path, end_path = chronological_right, chronological_left
        else:
            start_path, end_path = chronological_left, chronological_right
        left_name = f"lumina_random_mutation_contexts/{start_path.name}"
        right_name = f"lumina_random_mutation_contexts/{end_path.name}"
        prompt = make_prompt(
            step, hole_start, left_name, right_name, selected_refs, steering, temporal,
            living_text=living_text,
        )
        full_prompt = prompt["2730"]["inputs"]["external_prompt_overwrite"]
        record_living_prompt(step, full_prompt, living_snapshot)
        (OUT_DIR / f"step_{step:04d}_hole_{hole_start:05d}.api_prompt.json").write_text(
            json.dumps(prompt, indent=2), encoding="utf-8"
        )
        write_state(
            status="submitting",
            active_step=step,
            active_block=hole_start,
            active_hole_start=hole_start,
            active_seed=61000 + step,
            active_character_references=selected_refs,
            active_rabbit_reference=" · ".join(selected_refs),
            active_steering=steering,
            active_queue_id=None,
            active_frantic_score=steering["frantic_score"],
            active_frantic_cues=steering["matched_cues"],
            active_inverted_bookends=inverted,
            active_temporal_mode=temporal_mode,
            active_full_prompt=full_prompt,
            active_prompt_atoms=living_snapshot,
            completed_steps=step - 1,
        )
        print(
            f"SUBMIT step {step:04d} hole {hole_start} seed {61000 + step} "
            f"refs={selected_refs!r} frantic_score={steering['frantic_score']} "
            f"temporal_mode={temporal_mode} inverted={inverted}",
            flush=True,
        )
        try:
            response = post_json("/prompt", {
                "prompt": prompt,
                "client_id": "instrumentality-mutation-cascade",
            })
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", "replace")) from exc
        if response.get("node_errors"):
            raise RuntimeError(json.dumps(response, indent=2))
        prompt_id = response["prompt_id"]
        write_state(status="rendering", active_prompt_id=prompt_id)
        print("RUNNING", prompt_id, flush=True)
        history, elapsed = wait_for_job(prompt_id)
        (OUT_DIR / f"step_{step:04d}_hole_{hole_start:05d}.history.json").write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )
        candidates = sorted(
            OUT_DIR.glob(f"step_{step:04d}_hole_{hole_start:05d}_*_audio.webm"),
            key=lambda item: item.stat().st_mtime,
        )
        if not candidates:
            raise RuntimeError("Comfy completed without writing the expected hole output")
        output_path = candidates[-1]
        frames = probe_frames(output_path)
        if frames != BLOCK_FRAMES:
            raise RuntimeError(
                f"generated hole has {frames} frames, expected {BLOCK_FRAMES}"
            )
        write_state(status="fermenting", last_chunk_path=str(output_path))
        fermentation = {}
        try:
            caption, atoms, frame_path = ferment_finished_chunk(step, output_path)
            fermentation = {
                "caption": caption,
                "atoms": atoms,
                "frame_path": str(frame_path),
            }
            print(
                f"FERMENTED step {step:04d}: {len(atoms)} atoms from {frame_path.name}",
                flush=True,
            )
        except Exception as exc:
            fermentation = {"error": str(exc)}
            write_state(joycaption_error=str(exc))
            print(f"FERMENTATION FAILED step {step:04d}: {exc}", flush=True)
        manifest["jobs"].append({
            "step": step,
            "hole_start_frame": hole_start,
            "hole_end_frame_exclusive": hole_start + BLOCK_FRAMES,
            "seed": 61000 + step,
            "character_references": selected_refs,
            "steering": steering,
            "inverted_bookends": inverted,
            "temporal_mode": temporal_mode,
            "source_context_frames": temporal["source_context_frames"],
            "latent_edge_context_frames": temporal["edge_context_frames"],
            "director_duration": temporal["director_duration"],
            "full_prompt": full_prompt,
            "prompt_atoms": living_snapshot,
            "fermentation": fermentation,
            "prompt_id": prompt_id,
            "elapsed_seconds": elapsed,
            "left_context": left_name,
            "right_context": right_name,
            "output_path": str(output_path),
            "output_frames": frames,
        })
        for offset in range(BLOCK_FRAMES):
            owners[hole_start + offset] = (output_path, offset)
        current_pending = OUT_DIR / "CURRENT_MUTATION_CASCADE.pending.mp4"
        print("ASSEMBLE live current version", flush=True)
        assemble_current(current_pending, owners)
        current_pending.replace(current_path)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        write_state(
            status="ready",
            completed_steps=step,
            active_step=None,
            active_block=None,
            active_hole_start=None,
            active_prompt_id=None,
            active_queue_id=None,
            active_steering=None,
            active_full_prompt=None,
            active_prompt_atoms=None,
            current_video="/current.mp4",
            video_version=step,
            last_completed_hole_start=hole_start,
            last_elapsed_seconds=elapsed,
            last_chunk_path=str(output_path),
            last_character_references=selected_refs,
            last_rabbit_reference=" · ".join(selected_refs),
            joycaption_error=fermentation.get("error"),
        )
        print(f"DONE step {step:04d} hole {hole_start} in {elapsed:.2f}s", flush=True)
        step += 1

    write_state(status="paused", completed_steps=len(manifest["jobs"]), total_steps=None)
    print(f"CASCADE PAUSED AT STEP {len(manifest['jobs'])}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(args.limit)

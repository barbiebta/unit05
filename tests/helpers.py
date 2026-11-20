from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


def manifest(job_id: str = "job-test-001", *, schema: str = "dummyplug.h3-job.v2") -> dict:
    return {
        "schema": schema,
        "job_id": job_id,
        "job_desc": "Reference trim test",
        "created_at": "2026-08-25T00:00:00Z",
        "title": "Reference trim test",
        "workflow": {"template": "dasiwa-ref2va", "version": 1},
        "prompt_policy": {
            "allCharactersTransgenderWomen": True,
            "allowedVocabulary": {"man": False, "woman": True, "trans": True, "futa": True},
        },
        "generation": {
            "width": 768,
            "height": 1344,
            "duration": 5,
            "fps": 24,
            "seed": 123456789,
            "steps": 25,
            "sampler": "res_multistep",
            "shift_video": 12,
            "shift_audio": 3,
            "ref_image_size": "match",
            "input_scaling": "Auto",
            "output": {
                "container": "mp4",
                "codec": "Auto",
                "quality": 21,
                "audio_codec": "aac",
                "audio_bitrate": "192k",
            },
        },
        "references": [
            {
                "asset_id": "performance",
                "path": "assets/video-1__performance.mov",
                "kind": "video",
                "order": 0,
                "label": "<Video 1>",
                "size": 11,
                "sha256": hashlib.sha256(b"video-bytes").hexdigest(),
                "mime_type": "video/quicktime",
                "source_width": 768,
                "source_height": 1344,
                "source_duration": 11.0,
                "trim_start": 2.0,
                "trim_end": 7.0,
            }
        ],
    }


def create_bundle(
    path: Path,
    *,
    job_id: str = "job-test-001",
    schema: str = "dummyplug.h3-job.v2",
    override: dict | None = None,
) -> Path:
    job_manifest = manifest(job_id, schema=schema)
    if override:
        job_manifest.update(override)
    members = {
        "manifest.json": json.dumps(job_manifest, indent=2).encode(),
        "prompt.txt": b"subject_definitions:\n<Subject 1> test\nsummary:\ntest\nretention_analysis:\ntest\ndetailed_description:\n[Shot 1] test\noverall_soundscape:\ntest\nnon_diegetic_music:\nN/A",
        "request.txt": b"test request",
        "reference-map.json": b"{}",
        "assets/video-1__performance.mov": b"video-bytes",
        "metadata/performance.json": b"{}",
    }
    checksums = {name: hashlib.sha256(payload).hexdigest() for name, payload in members.items()}
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
        archive.writestr("checksums.json", json.dumps(checksums).encode())
    return path

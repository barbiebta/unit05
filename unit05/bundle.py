from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


SUPPORTED_SCHEMA = "dummyplug.h3-job.v1"
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedBundle:
    job_id: str
    bundle_hash: str
    manifest: dict[str, Any]
    prompt: str
    root: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
        raise BundleError(f"Unsafe archive path: {name!r}")
    if normalized.parts[0].endswith(":"):
        raise BundleError(f"Unsafe archive path: {name!r}")
    return normalized


def _read_json(archive: zipfile.ZipFile, name: str) -> Any:
    try:
        with archive.open(name) as source:
            return json.load(source)
    except KeyError as error:
        raise BundleError(f"Missing required file: {name}") from error
    except json.JSONDecodeError as error:
        raise BundleError(f"Invalid JSON in {name}: {error}") from error


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SUPPORTED_SCHEMA:
        raise BundleError(f"Unsupported bundle schema: {manifest.get('schema')!r}")
    job_id = str(manifest.get("job_id", ""))
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise BundleError("manifest.job_id is missing or unsafe")
    workflow = manifest.get("workflow")
    if not isinstance(workflow, dict) or workflow.get("template") != "dasiwa-ref2va" or workflow.get("version") != 1:
        raise BundleError("Only workflow dasiwa-ref2va version 1 is currently supported")
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        raise BundleError("manifest.generation must be an object")
    required_generation = {
        "width",
        "height",
        "duration",
        "fps",
        "seed",
        "steps",
        "sampler",
        "shift_video",
        "shift_audio",
        "ref_image_size",
        "input_scaling",
    }
    missing = sorted(required_generation - set(generation))
    if missing:
        raise BundleError(f"Missing generation settings: {', '.join(missing)}")
    width, height = int(generation["width"]), int(generation["height"])
    if width < 16 or width > 8192 or width % 16 or height < 16 or height > 8192 or height % 16:
        raise BundleError("Width and height must be multiples of 16 between 16 and 8192")
    if int(generation["duration"]) < 1:
        raise BundleError("Duration must be at least one second")
    if not 0.1 <= float(generation["fps"]) <= 240:
        raise BundleError("FPS must be between 0.1 and 240")
    if int(generation["steps"]) < 1:
        raise BundleError("Steps must be positive")
    if generation["ref_image_size"] not in {"match", "max"}:
        raise BundleError("ref_image_size must be 'match' or 'max'")
    scaling_modes = {
        "Off",
        "Auto",
        "Target",
        "Fit",
        "Fill and crop",
        "Fit and pad",
        "Long side with divisible crop",
    }
    if generation["input_scaling"] not in scaling_modes:
        raise BundleError(f"input_scaling must be one of {sorted(scaling_modes)}")
    references = manifest.get("references")
    if not isinstance(references, list) or not references:
        raise BundleError("At least one reference is required")
    orders: set[int] = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise BundleError(f"Reference {index} must be an object")
        kind = reference.get("kind")
        if kind not in {"image", "video", "audio"}:
            raise BundleError(f"Reference {index} has unsupported kind {kind!r}")
        path = str(reference.get("path", ""))
        safe_path = _safe_member(path)
        if safe_path.parts[0] != "assets":
            raise BundleError(f"Reference {index} must point inside assets/")
        order = int(reference.get("order", index))
        if order in orders:
            raise BundleError(f"Duplicate reference order: {order}")
        orders.add(order)
        if kind == "video":
            trim_start = float(reference.get("trim_start", 0))
            source_duration = float(reference.get("source_duration", 0))
            trim_end = float(reference.get("trim_end", source_duration))
            if trim_start < 0 or trim_end <= trim_start:
                raise BundleError(f"Reference {index} has invalid video trim")
            if source_duration > 0 and trim_end > source_duration + 0.001:
                raise BundleError(f"Reference {index} trim exceeds source duration")


def extract_bundle(
    bundle_path: Path,
    destination: Path,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
) -> ExtractedBundle:
    bundle_hash = sha256_file(bundle_path)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if len(files) > max_files:
                raise BundleError(f"Archive contains {len(files)} files; limit is {max_files}")
            total_size = sum(item.file_size for item in files)
            if total_size > max_uncompressed_bytes:
                raise BundleError("Archive is larger than the configured uncompressed-size limit")
            normalized_names: dict[str, zipfile.ZipInfo] = {}
            for item in files:
                normalized = str(_safe_member(item.filename))
                if normalized in normalized_names:
                    raise BundleError(f"Duplicate archive path: {normalized}")
                normalized_names[normalized] = item

            manifest = _read_json(archive, "manifest.json")
            if not isinstance(manifest, dict):
                raise BundleError("manifest.json must contain an object")
            _validate_manifest(manifest)
            checksums = _read_json(archive, "checksums.json")
            if not isinstance(checksums, dict):
                raise BundleError("checksums.json must contain an object")
            try:
                with archive.open("prompt.txt") as source:
                    prompt = source.read().decode("utf-8").strip()
            except KeyError as error:
                raise BundleError("Missing required file: prompt.txt") from error
            if not prompt:
                raise BundleError("prompt.txt is empty")

            expected_checksum_paths = set(normalized_names) - {"checksums.json"}
            if set(checksums) != expected_checksum_paths:
                missing = sorted(expected_checksum_paths - set(checksums))
                extra = sorted(set(checksums) - expected_checksum_paths)
                raise BundleError(f"checksums.json path mismatch; missing={missing}, extra={extra}")

            for normalized, item in normalized_names.items():
                target = destination.joinpath(*PurePosixPath(normalized).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(item) as source, target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
                if normalized != "checksums.json":
                    expected = str(checksums.get(normalized, "")).lower()
                    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest.hexdigest() != expected:
                        raise BundleError(f"Checksum mismatch: {normalized}")

            for reference in manifest["references"]:
                asset_path = destination.joinpath(*PurePosixPath(reference["path"]).parts)
                if not asset_path.is_file():
                    raise BundleError(f"Missing referenced asset: {reference['path']}")
                declared = str(reference.get("sha256", "")).lower()
                actual = sha256_file(asset_path)
                if declared and declared != actual:
                    raise BundleError(f"Reference hash mismatch: {reference['path']}")
                if "size" in reference and int(reference["size"]) != asset_path.stat().st_size:
                    raise BundleError(f"Reference size mismatch: {reference['path']}")

        return ExtractedBundle(
            job_id=str(manifest["job_id"]),
            bundle_hash=bundle_hash,
            manifest=manifest,
            prompt=prompt,
            root=destination,
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

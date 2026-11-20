from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


CANONICAL_SCHEMA = "dummyplug.h3-job.v2"
LEGACY_SCHEMA = "dummyplug.h3-job.v1"
SUPPORTED_SCHEMAS = {CANONICAL_SCHEMA, LEGACY_SCHEMA}
MAX_SAFE_INTEGER = 9_007_199_254_740_991
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DIRECTOR_INPUT_SCALING_MODES = {
    "Off",
    "Auto",
    "Target",
    "Fit",
    "Fill and crop",
    "Fit and pad",
    "Long side with divisible crop",
}
LEGACY_INPUT_SCALING_ALIASES = {
    "match": "Auto",
    "max": "Auto",
    "native": "Auto",
    "fit": "Fit",
    "fill": "Fill and crop",
    "720": "Auto",
    "1024": "Auto",
    "Native (ShortEdge 2048px)": "Auto",
    "Target · Selected Aspect & Resolution": "Target",
}


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
    if (
        "\\" in name
        or re.match(r"^[A-Za-z]:", name)
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise BundleError(f"Unsafe archive path: {name!r}")
    normalized = PurePosixPath(name)
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


def _normalize_legacy_scaling(manifest: dict[str, Any]) -> None:
    """Translate Dummyplug's short-lived combined scaling options in memory.

    Legacy bundles used one ``input_scaling`` field for both the Director's
    reference-image-size and scaling controls. Leave the archive untouched so
    its declared checksum still verifies; only the manifest passed to the
    executor receives the modern split fields and canonical Director values.
    """
    if manifest.get("schema") != LEGACY_SCHEMA:
        return
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return
    legacy_value = generation.get("input_scaling")
    if "ref_image_size" not in generation:
        generation["ref_image_size"] = legacy_value if legacy_value in {"match", "max"} else "match"
    if legacy_value in LEGACY_INPUT_SCALING_ALIASES:
        generation["input_scaling"] = LEGACY_INPUT_SCALING_ALIASES[legacy_value]


def _exact_integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BundleError(f"{field} must be an integer from {minimum} to {maximum}")
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise BundleError(f"{field} must be an integer from {minimum} to {maximum}")
    result = int(numeric)
    if not minimum <= result <= maximum:
        raise BundleError(f"{field} must be an integer from {minimum} to {maximum}")
    return result


def _finite_number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BundleError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise BundleError(f"{field} must be a finite number")
    if minimum is not None and result < minimum:
        raise BundleError(f"{field} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise BundleError(f"{field} must be at most {maximum}")
    return result


def _validate_prompt_policy(manifest: dict[str, Any]) -> None:
    policy = manifest.get("prompt_policy")
    if not isinstance(policy, dict):
        raise BundleError("manifest.prompt_policy must be an object in v2 bundles")
    if not isinstance(policy.get("allCharactersTransgenderWomen"), bool):
        raise BundleError("manifest.prompt_policy.allCharactersTransgenderWomen must be boolean")
    vocabulary = policy.get("allowedVocabulary")
    if not isinstance(vocabulary, dict):
        raise BundleError("manifest.prompt_policy.allowedVocabulary must be an object")
    for word in ("man", "woman", "trans", "futa"):
        if not isinstance(vocabulary.get(word), bool):
            raise BundleError(f"manifest.prompt_policy.allowedVocabulary.{word} must be boolean")


def _validate_manifest(manifest: dict[str, Any]) -> None:
    schema = manifest.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise BundleError(f"Unsupported bundle schema: {manifest.get('schema')!r}")
    job_id = str(manifest.get("job_id", ""))
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise BundleError("manifest.job_id is missing or unsafe")
    workflow = manifest.get("workflow")
    if not isinstance(workflow, dict) or workflow.get("template") != "dasiwa-ref2va" or workflow.get("version") != 1:
        raise BundleError("Only workflow dasiwa-ref2va version 1 is currently supported")
    if schema == CANONICAL_SCHEMA:
        job_desc = manifest.get("job_desc")
        if not isinstance(job_desc, str) or not job_desc.strip() or len(job_desc) > 30:
            raise BundleError("manifest.job_desc must be a non-empty string of at most 30 characters")
        created_at = manifest.get("created_at")
        if not isinstance(created_at, str):
            raise BundleError("manifest.created_at must be an ISO-8601 timestamp")
        try:
            parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise BundleError("manifest.created_at must be an ISO-8601 timestamp") from error
        if parsed_created_at.tzinfo is None:
            raise BundleError("manifest.created_at must include a timezone")
        if "title" in manifest and (not isinstance(manifest["title"], str) or not manifest["title"].strip()):
            raise BundleError("manifest.title must be a non-empty string when present")
        _validate_prompt_policy(manifest)
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
    width = _exact_integer(generation["width"], "generation.width", minimum=64, maximum=8192)
    height = _exact_integer(generation["height"], "generation.height", minimum=64, maximum=8192)
    if width % 16 or height % 16:
        raise BundleError("Width and height must be multiples of 16")
    _exact_integer(generation["duration"], "generation.duration", minimum=1, maximum=20)
    _finite_number(generation["fps"], "generation.fps", minimum=1, maximum=120)
    _exact_integer(generation["seed"], "generation.seed", minimum=0, maximum=MAX_SAFE_INTEGER)
    _exact_integer(generation["steps"], "generation.steps", minimum=1, maximum=200)
    _finite_number(generation["shift_video"], "generation.shift_video")
    _finite_number(generation["shift_audio"], "generation.shift_audio")
    sampler = generation["sampler"]
    if not isinstance(sampler, str) or not sampler.strip():
        raise BundleError("generation.sampler must be a non-empty string")
    if generation["ref_image_size"] not in {"match", "max"}:
        raise BundleError("ref_image_size must be 'match' or 'max'")
    if generation["input_scaling"] not in DIRECTOR_INPUT_SCALING_MODES:
        raise BundleError(f"input_scaling must be one of {sorted(DIRECTOR_INPUT_SCALING_MODES)}")
    output = generation.get("output")
    if schema == CANONICAL_SCHEMA and output is None:
        raise BundleError("generation.output is required in v2 bundles")
    if output is not None:
        if not isinstance(output, dict):
            raise BundleError("generation.output must be an object")
        required_output = {"container", "codec", "quality", "audio_codec", "audio_bitrate"}
        if schema == CANONICAL_SCHEMA and not required_output.issubset(output):
            raise BundleError(f"Missing output settings: {', '.join(sorted(required_output - set(output)))}")
        for field in ("container", "codec", "audio_codec", "audio_bitrate"):
            if field in output and (not isinstance(output[field], str) or not output[field].strip()):
                raise BundleError(f"generation.output.{field} must be a non-empty string")
        if "quality" in output:
            _exact_integer(output["quality"], "generation.output.quality", minimum=0, maximum=51)
    references = manifest.get("references")
    if not isinstance(references, list) or not references:
        raise BundleError("At least one reference is required")
    orders: set[int] = set()
    asset_ids: set[str] = set()
    reference_paths: set[str] = set()
    label_counts = {"image": 0, "video": 0, "audio": 0}
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise BundleError(f"Reference {index} must be an object")
        kind = reference.get("kind")
        if kind not in {"image", "video", "audio"}:
            raise BundleError(f"Reference {index} has unsupported kind {kind!r}")
        path = str(reference.get("path", ""))
        safe_path = _safe_member(path)
        if safe_path.parts[0] != "assets" or (schema == CANONICAL_SCHEMA and len(safe_path.parts) != 2):
            raise BundleError(f"Reference {index} must point directly inside assets/")
        if schema == CANONICAL_SCHEMA and not re.fullmatch(r"[A-Za-z0-9._-]+", safe_path.name):
            raise BundleError(f"Reference {index} has an unsafe asset filename")
        if path in reference_paths:
            raise BundleError(f"Duplicate reference path: {path}")
        reference_paths.add(path)
        order = _exact_integer(reference.get("order", index), f"Reference {index} order", minimum=0, maximum=10_000)
        if order in orders:
            raise BundleError(f"Duplicate reference order: {order}")
        if schema == CANONICAL_SCHEMA and order != index:
            raise BundleError(f"Reference {index} order must equal its array position")
        orders.add(order)
        if schema == CANONICAL_SCHEMA:
            asset_id = reference.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id:
                raise BundleError(f"Reference {index} asset_id must be a non-empty string")
            if asset_id in asset_ids:
                raise BundleError(f"Duplicate reference asset_id: {asset_id}")
            asset_ids.add(asset_id)
            mime_type = reference.get("mime_type")
            if not isinstance(mime_type, str) or not mime_type.strip():
                raise BundleError(f"Reference {index} mime_type must be a non-empty string")
            declared_hash = reference.get("sha256")
            if not isinstance(declared_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", declared_hash):
                raise BundleError(f"Reference {index} sha256 must be lowercase hexadecimal")
            _exact_integer(reference.get("size"), f"Reference {index} size", minimum=0, maximum=MAX_SAFE_INTEGER)
            label_counts[kind] += 1
            label_kind = {"image": "Picture", "video": "Video", "audio": "Audio"}[kind]
            expected_label = f"<{label_kind} {label_counts[kind]}>"
            if reference.get("label") != expected_label:
                raise BundleError(f"Reference {index} label must be {expected_label}")
            if kind in {"image", "video"}:
                _exact_integer(reference.get("source_width"), f"Reference {index} source_width", minimum=1, maximum=MAX_SAFE_INTEGER)
                _exact_integer(reference.get("source_height"), f"Reference {index} source_height", minimum=1, maximum=MAX_SAFE_INTEGER)
        if kind == "video":
            if schema == CANONICAL_SCHEMA and not {"source_duration", "trim_start", "trim_end"}.issubset(reference):
                raise BundleError(f"Reference {index} video metadata is incomplete")
            trim_start = _finite_number(reference.get("trim_start", 0), f"Reference {index} trim_start", minimum=0)
            source_duration = _finite_number(
                reference.get("source_duration", 0), f"Reference {index} source_duration", minimum=0
            )
            trim_end = _finite_number(
                reference.get("trim_end", source_duration), f"Reference {index} trim_end", minimum=0
            )
            if schema == CANONICAL_SCHEMA and source_duration <= 0:
                raise BundleError(f"Reference {index} source_duration must be positive")
            if trim_start < 0 or trim_end <= trim_start:
                raise BundleError(f"Reference {index} has invalid video trim")
            if source_duration > 0 and trim_end > source_duration + 0.001:
                raise BundleError(f"Reference {index} trim exceeds source duration")
        elif schema == CANONICAL_SCHEMA and ("trim_start" in reference or "trim_end" in reference):
            raise BundleError(f"Reference {index} non-video must not declare trim metadata")


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
            _normalize_legacy_scaling(manifest)
            _validate_manifest(manifest)
            if manifest.get("schema") == CANONICAL_SCHEMA:
                required_members = {
                    "manifest.json",
                    "prompt.txt",
                    "request.txt",
                    "reference-map.json",
                    "checksums.json",
                }
                missing_members = sorted(required_members - set(normalized_names))
                if missing_members:
                    raise BundleError(f"Missing required files: {', '.join(missing_members)}")
                reference_map = _read_json(archive, "reference-map.json")
                if not isinstance(reference_map, dict):
                    raise BundleError("reference-map.json must contain an object")
                try:
                    with archive.open("request.txt") as source:
                        source.read().decode("utf-8")
                except UnicodeDecodeError as error:
                    raise BundleError("request.txt must be valid UTF-8") from error
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

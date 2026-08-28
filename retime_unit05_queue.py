from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from unit05.bundle import extract_bundle


TARGET_DURATION = 10
EXPECTED_SOURCE_DURATION = 15
SCALE = TARGET_DURATION / EXPECTED_SOURCE_DURATION
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def format_seconds(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def rescale_temporal_language(text: str) -> str:
    def numeric_range(match: re.Match[str]) -> str:
        first = format_seconds(float(match.group("first")) * SCALE)
        second = format_seconds(float(match.group("second")) * SCALE)
        return f"{first}{match.group('joiner')}{second} seconds"

    text = re.sub(
        r"(?P<first>\d+(?:\.\d+)?)(?P<joiner>\s*(?:-|–|—|to|and)\s*)(?P<second>\d+(?:\.\d+)?)\s*seconds\b",
        numeric_range,
        text,
        flags=re.IGNORECASE,
    )

    def numeric_seconds(match: re.Match[str]) -> str:
        value = format_seconds(float(match.group("value")) * SCALE)
        suffix = match.group("suffix")
        return f"{value}{suffix}"

    text = re.sub(
        r"(?P<value>\d+(?:\.\d+)?)(?P<suffix>\s*seconds?\b|-seconds?\b)",
        numeric_seconds,
        text,
        flags=re.IGNORECASE,
    )

    def timecode(match: re.Match[str]) -> str:
        seconds = int(match.group("seconds"))
        if seconds > EXPECTED_SOURCE_DURATION:
            return match.group(0)
        scaled = int(round(seconds * SCALE))
        return f"0:{scaled:02d}"

    text = re.sub(r"\b0:(?P<seconds>\d{2})\b", timecode, text)

    words = "|".join(NUMBER_WORDS)

    def word_seconds(match: re.Match[str]) -> str:
        value = format_seconds(NUMBER_WORDS[match.group("word").lower()] * SCALE)
        separator = match.group("separator")
        unit = match.group("unit")
        return f"{value}{separator}{unit}"

    text = re.sub(
        rf"\b(?P<word>{words})(?P<separator>-|\s+)(?P<unit>seconds?)\b",
        word_seconds,
        text,
        flags=re.IGNORECASE,
    )
    return text


def read_members(source: Path) -> tuple[list[zipfile.ZipInfo], dict[str, bytes], bytes]:
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        members = {info.filename: archive.read(info.filename) for info in infos if not info.is_dir()}
        return infos, members, archive.comment


def rebuild(source: Path, output_dir: Path) -> tuple[Path, int, int]:
    infos, members, comment = read_members(source)
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    generation = manifest["generation"]
    old_duration = int(generation["duration"])
    if old_duration not in {TARGET_DURATION, EXPECTED_SOURCE_DURATION}:
        raise ValueError(f"{source.name}: unexpected duration {old_duration}")

    changed = old_duration != TARGET_DURATION
    generation["duration"] = TARGET_DURATION
    if changed:
        for reference in manifest.get("references", []):
            timeline_start = reference.get("timeline_start")
            if timeline_start is not None and float(timeline_start) > TARGET_DURATION:
                reference["timeline_start"] = round(float(timeline_start) * SCALE, 6)
        members["prompt.txt"] = rescale_temporal_language(members["prompt.txt"].decode("utf-8")).encode("utf-8")
        if "request.txt" in members:
            members["request.txt"] = rescale_temporal_language(members["request.txt"].decode("utf-8")).encode("utf-8")

    members["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    checksums = {
        name: digest(data)
        for name, data in sorted(members.items())
        if name != "checksums.json"
    }
    members["checksums.json"] = (json.dumps(checksums, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / source.name
    partial = output_dir / f".{source.name}.partial"
    if target.exists() or partial.exists():
        raise FileExistsError(target)
    try:
        with zipfile.ZipFile(partial, "x") as archive:
            archive.comment = comment
            by_name = {info.filename: info for info in infos}
            for info in infos:
                if info.is_dir():
                    archive.writestr(info, b"")
                elif info.filename in members:
                    archive.writestr(info, members[info.filename])
            for name, data in members.items():
                if name not in by_name:
                    archive.writestr(name, data)
        with zipfile.ZipFile(partial, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"CRC failure at {bad}")
        with tempfile.TemporaryDirectory(prefix="unit05-retime-validate-") as temporary:
            extracted = extract_bundle(
                partial,
                Path(temporary) / "extracted",
                max_files=256,
                max_uncompressed_bytes=2 * 1024**3,
            )
            actual_duration = int(extracted.manifest["generation"]["duration"])
            if actual_duration != TARGET_DURATION:
                raise ValueError(f"validator read duration {actual_duration}")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target, old_duration, target.stat().st_size


def main() -> None:
    source_dir = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    bundles = sorted(source_dir.glob("*.zip"))
    if not bundles:
        raise RuntimeError("no queued bundles found")
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        for source in bundles:
            target, old_duration, size = rebuild(source, output_dir)
            print(f"VALID|{target.name}|{old_duration}->10|{size}")
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    print(f"COMPLETE|{len(bundles)}")


if __name__ == "__main__":
    main()

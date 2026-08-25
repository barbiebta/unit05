#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit05.bundle import extract_bundle


def repair(source: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / source.name
    partial = target.with_name(f".{target.name}.partial")
    if target.exists() or partial.exists():
        raise FileExistsError(f"repair output already exists: {target}")

    with zipfile.ZipFile(source, "r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        generation = manifest["generation"]
        legacy_value = generation.get("input_scaling")
        if "ref_image_size" in generation or legacy_value not in {"match", "max"}:
            raise ValueError(f"bundle does not match the legacy scaling defect: {source}")
        generation["ref_image_size"] = legacy_value
        generation["input_scaling"] = "Auto"
        manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

        checksums = json.loads(archive.read("checksums.json"))
        checksums["manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()
        checksum_bytes = (json.dumps(checksums, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

        try:
            with zipfile.ZipFile(partial, "x") as repaired:
                repaired.comment = archive.comment
                for item in archive.infolist():
                    if item.filename == "manifest.json":
                        payload = manifest_bytes
                    elif item.filename == "checksums.json":
                        payload = checksum_bytes
                    else:
                        payload = archive.read(item.filename)
                    repaired.writestr(item, payload)
            with zipfile.ZipFile(partial, "r") as repaired:
                bad_member = repaired.testzip()
                if bad_member:
                    raise ValueError(f"repaired archive failed CRC at {bad_member}")
            with tempfile.TemporaryDirectory(prefix="unit05-repair-check-") as temporary:
                extract_bundle(
                    partial,
                    Path(temporary) / "extracted",
                    max_files=256,
                    max_uncompressed_bytes=2 * 1024**3,
                )
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair legacy Dummyplug scaling fields")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    for source in args.sources:
        target = repair(source.resolve(), args.output_dir.resolve())
        print(json.dumps({"source": str(source), "repaired": str(target), "bytes": target.stat().st_size}))


if __name__ == "__main__":
    main()

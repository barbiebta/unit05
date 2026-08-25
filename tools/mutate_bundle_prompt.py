#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit05.bundle import extract_bundle


def mutate(source: Path, target: Path, prompt_path: Path, request_path: Path) -> dict[str, object]:
    if target.exists() or target.with_name(f".{target.name}.partial").exists():
        raise FileExistsError(target)
    prompt = prompt_path.read_text(encoding="utf-8").strip() + "\n"
    request = request_path.read_text(encoding="utf-8").strip() + "\n"
    partial = target.with_name(f".{target.name}.partial")
    with zipfile.ZipFile(source, "r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        manifest["job_id"] = str(uuid.uuid4())
        manifest["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        replacements = {
            "manifest.json": (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(),
            "prompt.txt": prompt.encode(),
            "request.txt": request.encode(),
        }
        checksums = json.loads(archive.read("checksums.json"))
        for name, payload in replacements.items():
            checksums[name] = hashlib.sha256(payload).hexdigest()
        replacements["checksums.json"] = (
            json.dumps(checksums, indent=2, ensure_ascii=False) + "\n"
        ).encode()
        try:
            with zipfile.ZipFile(partial, "x") as output:
                output.comment = archive.comment
                for item in archive.infolist():
                    output.writestr(item, replacements.get(item.filename, archive.read(item.filename)))
            with tempfile.TemporaryDirectory(prefix="unit05-mutation-check-") as temporary:
                extracted = extract_bundle(
                    partial,
                    Path(temporary) / "extracted",
                    max_files=256,
                    max_uncompressed_bytes=2 * 1024**3,
                )
                if extracted.job_id != manifest["job_id"] or extracted.prompt.strip() != prompt.strip():
                    raise ValueError("mutated bundle validation did not preserve the requested identity/prompt")
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return {"path": str(target), "job_id": manifest["job_id"], "bytes": target.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("prompt", type=Path)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    print(json.dumps(mutate(args.source, args.target, args.prompt, args.request)))


if __name__ == "__main__":
    main()

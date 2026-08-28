from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path


def run(*command: str, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_dependencies(node_dir: Path) -> None:
    requirements = node_dir / "requirements.txt"
    if requirements.is_file():
        run(sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(requirements))
        return

    pyproject = node_dir / "pyproject.toml"
    if not pyproject.is_file():
        return
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
    dependencies = [str(item) for item in project.get("dependencies") or []]
    if dependencies:
        run(sys.executable, "-m", "pip", "install", "--no-cache-dir", *dependencies)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)

    nodes = json.loads(args.lock.read_text(encoding="utf-8"))
    for node in nodes:
        target = args.destination / node["name"]
        run("git", "clone", "--filter=blob:none", node["repository"], str(target))
        run("git", "checkout", "--detach", node["revision"], cwd=target)
        install_dependencies(target)


if __name__ == "__main__":
    main()


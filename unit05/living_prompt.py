from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None


def split_atoms(text: str) -> list[str]:
    parts = re.split(r"\s*(?:\r?\n|;)+\s*", str(text or "").strip())
    return [re.sub(r"\s+", " ", part).strip(" .;") for part in parts if part.strip()]


def new_state(initial_text: str = "") -> dict[str, Any]:
    now = time.time()
    return {
        "version": 1,
        "next_id": 1,
        "anchor_atoms": [
            {
                "id": index + 1,
                "text": atom,
                "enabled": True,
                "locked": False,
                "created_unix": now,
            }
            for index, atom in enumerate(split_atoms(initial_text))
        ],
        "fermented_atoms": [],
        "history": [],
    }


def load_state(path: Path, initial_text: str = "") -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = new_state(initial_text)
    state.setdefault("version", 1)
    state.setdefault("anchor_atoms", [])
    state.setdefault("fermented_atoms", [])
    state.setdefault("history", [])
    state["next_id"] = max(
        int(state.get("next_id", 1)),
        1 + max((int(atom.get("id", 0)) for atom in state["anchor_atoms"]), default=0),
    )
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def state_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def compiled_atoms(state: dict[str, Any]) -> list[str]:
    atoms = list(state.get("anchor_atoms") or []) + list(state.get("fermented_atoms") or [])
    return [str(atom.get("text") or "").strip() for atom in atoms if atom.get("enabled", True) and str(atom.get("text") or "").strip()]


def compiled_text(state: dict[str, Any]) -> str:
    return "; ".join(compiled_atoms(state))


def add_anchor_atoms(state: dict[str, Any], text: str) -> list[dict[str, Any]]:
    existing = {
        str(atom.get("text") or "").strip().casefold()
        for atom in list(state.get("anchor_atoms") or [])
    }
    added: list[dict[str, Any]] = []
    for value in split_atoms(text):
        if value.casefold() in existing:
            continue
        atom = {
            "id": int(state.get("next_id", 1)),
            "text": value,
            "enabled": True,
            "locked": False,
            "source": "user",
            "created_unix": time.time(),
        }
        state["next_id"] = atom["id"] + 1
        state.setdefault("anchor_atoms", []).append(atom)
        added.append(atom)
        existing.add(value.casefold())
    if added:
        state["version"] = int(state.get("version", 0)) + 1
    return added


def remove_atom(state: dict[str, Any], atom_id: object) -> bool:
    target = str(atom_id)
    removed = False
    for key in ("anchor_atoms", "fermented_atoms"):
        before = list(state.get(key) or [])
        after = [atom for atom in before if str(atom.get("id")) != target]
        if len(after) != len(before):
            state[key] = after
            removed = True
    if removed:
        state["version"] = int(state.get("version", 0)) + 1
    return removed


def update_atom(state: dict[str, Any], atom_id: object, text: str) -> bool:
    target = str(atom_id)
    value = re.sub(r"\s+", " ", str(text or "")).strip(" .;")
    if not value:
        return False
    for key in ("anchor_atoms", "fermented_atoms"):
        for atom in state.get(key) or []:
            if str(atom.get("id")) == target:
                atom["text"] = value
                atom["updated_unix"] = time.time()
                state["version"] = int(state.get("version", 0)) + 1
                return True
    return False


def replace_fermented(
    state: dict[str, Any], atoms: list[str], *, step: int, caption: str, frame_path: str
) -> None:
    now = time.time()
    state["fermented_atoms"] = [
        {
            "id": f"f{step}-{index + 1}",
            "text": atom,
            "enabled": True,
            "locked": False,
            "source": "joycaption",
            "source_step": int(step),
            "created_unix": now,
        }
        for index, atom in enumerate(atoms)
    ]
    state["last_caption"] = caption
    state["last_frame_path"] = frame_path
    state["version"] = int(state.get("version", 0)) + 1


def record_history(
    state: dict[str, Any], *, step: int, compiled_prompt: str, caption: str = ""
) -> None:
    state.setdefault("history", []).append(
        {
            "step": int(step),
            "compiled_prompt": compiled_prompt,
            "anchor_atoms": list(state.get("anchor_atoms") or []),
            "fermented_atoms": list(state.get("fermented_atoms") or []),
            "caption": caption,
            "created_unix": time.time(),
        }
    )
    state["history"] = state["history"][-8:]

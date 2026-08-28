from pathlib import Path

from unit05.joycaption import (
    RABBIT_FRAME_PROMPT,
    JoyCaptionConfig,
    atomize_caption,
    sample_atoms,
)
from unit05.living_prompt import (
    add_anchor_atoms,
    compiled_text,
    load_state,
    record_history,
    remove_atom,
    replace_fermented,
    save_state,
    update_atom,
)


def test_atomize_caption_accepts_comma_list() -> None:
    assert atomize_caption("white rabbit, low camera, bright kitchen") == [
        "white rabbit", "low camera", "bright kitchen"
    ]


def test_atomize_caption_accepts_json_array() -> None:
    assert atomize_caption('["white rabbit", "low camera"]') == [
        "white rabbit", "low camera"
    ]


def test_caption_prompt_softly_deemphasizes_exact_lettering() -> None:
    assert "low-salience visual material" in RABBIT_FRAME_PROMPT
    assert "usually describe" in RABBIT_FRAME_PROMPT
    assert "unless the lettering is visually dominant" in RABBIT_FRAME_PROMPT


def test_sample_atoms_selects_three_reproducibly() -> None:
    candidates = [f"atom {index}" for index in range(12)]
    first = sample_atoms(candidates, seed=42)
    second = sample_atoms(candidates, seed=42)
    assert first == second
    assert len(first) == 3
    assert len(set(first)) == 3
    assert set(first) <= set(candidates)


def test_sample_atoms_keeps_short_lists_intact() -> None:
    assert sample_atoms(["one", "two"], seed=42) == ["one", "two"]


def test_config_reads_existing_joy_key(tmp_path: Path) -> None:
    env = tmp_path / "joy.env"
    env.write_text("JOY_API_KEY=secret\nUNIT05_JOYCAPTION_BASE_URL=http://127.0.0.1:19000/v1\n")
    config = JoyCaptionConfig.from_env_file(env)
    assert config.api_key == "secret"
    assert config.base_url == "http://127.0.0.1:19000/v1"


def test_living_prompt_replaces_only_fermented_atoms(tmp_path: Path) -> None:
    path = tmp_path / "prompt.json"
    state = load_state(path, "keep this; and this")
    replace_fermented(state, ["rabbit ears", "wet pavement"], step=4, caption="x", frame_path="x.jpg")
    assert compiled_text(state) == "keep this; and this; rabbit ears; wet pavement"
    record_history(state, step=4, compiled_prompt=compiled_text(state), caption="x")
    save_state(path, state)
    loaded = load_state(path)
    assert len(loaded["history"]) == 1
    assert loaded["fermented_atoms"][0]["source_step"] == 4


def test_living_prompt_atoms_are_independently_editable(tmp_path: Path) -> None:
    state = load_state(tmp_path / "prompt.json")
    added = add_anchor_atoms(state, "one; two")
    assert [atom["text"] for atom in added] == ["one", "two"]
    assert add_anchor_atoms(state, "ONE") == []
    assert update_atom(state, added[0]["id"], "one changed")
    assert remove_atom(state, added[1]["id"])
    assert compiled_text(state) == "one changed"

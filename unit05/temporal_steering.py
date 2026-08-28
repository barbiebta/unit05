from __future__ import annotations

import re
from typing import Any


ENERGY_CUES: tuple[tuple[str, int], ...] = (
    (r"\b(?:still|motionless|frozen)\b", -3),
    (r"\b(?:meditative|serene|tranquil|peaceful)\b", -3),
    (r"\b(?:slow|slowly|unhurried|lingering)\b", -2),
    (r"\b(?:calm|quiet|gentle|softly|subtle|restrained|ambient)\b", -1),
    (r"\b(?:gradual|gradually|thoughtful|rests?|sitting|gazing)\b", -1),
    (r"\b(?:transform|transforms|transforming|morph|morphs|melts?)\b", 1),
    (r"\b(?:intense|urgent|sudden|suddenly|fast|quickly|rapid|rapidly)\b", 2),
    (r"\b(?:flashing|flickering|pulsing|spinning|rushing|chasing)\b", 2),
    (r"\b(?:chaotic|frenetic|frantic|frenzied|manic)\b", 4),
    (r"\b(?:strobing|thrashing|convulsing|exploding|shattering|smashing)\b", 4),
    (r"\b(?:violently|screaming|tearing|rupturing)\b", 3),
)


def franticness(text: str) -> tuple[int, list[str]]:
    """Return a deterministic prompt-energy score and the cues that produced it."""
    value = str(text or "").casefold()
    score = 0
    matches: list[str] = []
    for pattern, weight in ENERGY_CUES:
        found = re.findall(pattern, value)
        if not found:
            continue
        contribution = weight * min(len(found), 3)
        score += contribution
        matches.append(f"{found[0]} {contribution:+d}")
    exclamations = min(value.count("!"), 3)
    if exclamations:
        score += exclamations
        matches.append(f"exclamation {exclamations:+d}")
    return max(-12, min(12, score)), matches


def choose_temporal_steering(text: str) -> dict[str, Any]:
    """Map prompt energy to temporal conditioning without randomness or queue state."""
    score, cues = franticness(text)
    if score <= -5:
        mode = "double_slow"
    elif score <= -2:
        mode = "slow"
    elif score <= 2:
        mode = "no_compression"
    else:
        mode = "normal"
    return {
        "id": None,
        "version": 1,
        "selection_source": "living_prompt_franticness",
        "frantic_score": score,
        "matched_cues": cues,
        "inverted_bookends": score >= 8,
        "temporal_mode": mode,
    }

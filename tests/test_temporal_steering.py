from unit05.temporal_steering import choose_temporal_steering


def test_neutral_prompt_uses_no_compression() -> None:
    result = choose_temporal_steering("A rabbit stands beside a pink wall")
    assert result["temporal_mode"] == "no_compression"
    assert result["inverted_bookends"] is False


def test_calm_prompt_slows_time() -> None:
    result = choose_temporal_steering(
        "A motionless rabbit rests quietly in a serene room, moving slowly"
    )
    assert result["temporal_mode"] == "double_slow"
    assert result["inverted_bookends"] is False


def test_frantic_prompt_compresses_and_inverts_time() -> None:
    result = choose_temporal_steering(
        "Frantic strobing bodies thrash violently while everything explodes!!!"
    )
    assert result["temporal_mode"] == "normal"
    assert result["inverted_bookends"] is True

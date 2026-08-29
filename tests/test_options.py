import pytest

from podcastclip.options import language_label, normalize_language_code, resolve_duration_minutes
from podcastclip.pipeline import script_length, script_length_budget


def test_duration_presets_and_custom_minutes() -> None:
    assert resolve_duration_minutes(preset="quick") == 2
    assert resolve_duration_minutes(preset="standard") == 5
    assert resolve_duration_minutes(preset="deep") == 8
    assert resolve_duration_minutes(preset="long") == 15
    assert resolve_duration_minutes(minutes=11) == 11


def test_duration_defaults_to_standard() -> None:
    assert resolve_duration_minutes() == 5


def test_duration_rejects_conflicts_and_invalid_values() -> None:
    with pytest.raises(ValueError):
        resolve_duration_minutes(preset="deep", minutes=8)
    with pytest.raises(ValueError):
        resolve_duration_minutes(minutes=0)


def test_language_codes_accept_supported_aliases() -> None:
    assert normalize_language_code("zh") == "zh"
    assert normalize_language_code("中文") == "zh"
    assert language_label("en") == "English"
    assert language_label("英文") == "English"


def test_language_budgets_use_language_specific_units() -> None:
    assert script_length_budget(5, "中文") == (1485, 1815)
    assert script_length_budget(5, "English") == (675, 825)
    assert script_length("你好，跑步简报。", "中文") == 8
    assert script_length("A short running brief.", "English") == 4

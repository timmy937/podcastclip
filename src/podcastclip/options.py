from __future__ import annotations


DURATION_PRESETS = {
    "quick": 2,
    "standard": 5,
    "deep": 8,
    "long": 15,
}
DEFAULT_DURATION_PRESET = "standard"

LANGUAGE_LABELS = {
    "zh": "中文",
    "en": "English",
}
DEFAULT_LANGUAGE_CODE = "zh"

_LANGUAGE_ALIASES = {
    "中文": "zh",
    "chinese": "zh",
    "english": "en",
    "英文": "en",
}


def resolve_duration_minutes(*, preset: str | None = None, minutes: int | None = None) -> int:
    if preset is not None and minutes is not None:
        raise ValueError("Choose either a duration preset or --minutes, not both.")
    if minutes is not None:
        if minutes <= 0:
            raise ValueError("Minutes must be greater than zero.")
        return minutes

    selected = preset or DEFAULT_DURATION_PRESET
    try:
        return DURATION_PRESETS[selected]
    except KeyError as exc:
        choices = ", ".join(DURATION_PRESETS)
        raise ValueError(f"Unknown duration preset {selected!r}. Choose one of: {choices}.") from exc


def normalize_language_code(value: str) -> str:
    normalized = value.strip().lower()
    normalized = _LANGUAGE_ALIASES.get(normalized, normalized)
    if normalized not in LANGUAGE_LABELS:
        choices = ", ".join(LANGUAGE_LABELS)
        raise ValueError(f"Unknown language {value!r}. Choose one of: {choices}.")
    return normalized


def language_label(value: str) -> str:
    return LANGUAGE_LABELS[normalize_language_code(value)]

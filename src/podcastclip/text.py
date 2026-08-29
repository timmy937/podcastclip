from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass


SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？.!?])\s+|(?<=[。！？])")
VTT_TIMESTAMP = re.compile(
    r"(?P<hours>\d{2,}):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)(?:[.,](?P<millis>\d{3}))?"
)


@dataclass(frozen=True)
class CaptionCue:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class CaptionSegment:
    start_seconds: float
    end_seconds: float
    text: str


def parse_vtt(vtt_text: str) -> str:
    return _join_caption_text(cue.text for cue in parse_vtt_cues(vtt_text))


def parse_vtt_cues(vtt_text: str) -> list[CaptionCue]:
    raw_cues: list[CaptionCue] = []
    blocks = re.split(r"\n\s*\n", vtt_text)
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timestamp_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timestamp_index is None:
            continue
        timestamp_line = lines[timestamp_index]
        start_raw, end_raw = [part.strip().split()[0] for part in timestamp_line.split("-->", 1)]
        start = _parse_vtt_timestamp(start_raw)
        end = _parse_vtt_timestamp(end_raw)
        text = _clean_caption_text(" ".join(lines[timestamp_index + 1 :]))
        if not text or end < start or end - start <= 0.05:
            continue

        raw_cues.append(CaptionCue(start, end, text))

    cues: list[CaptionCue] = []
    previous_display = ""
    for cue in raw_cues:
        delta = _caption_delta(previous_display, cue.text)
        previous_display = cue.text
        if delta:
            cues.append(CaptionCue(cue.start_seconds, cue.end_seconds, delta))
    return cues


def segment_caption_cues(
    cues: list[CaptionCue],
    *,
    min_chars: int = 60,
    max_chars: int = 320,
    max_seconds: float = 20.0,
) -> list[CaptionSegment]:
    segments: list[CaptionSegment] = []
    current: list[CaptionCue] = []
    for cue in cues:
        candidate = current + [cue]
        candidate_text = " ".join(item.text for item in candidate)
        too_long = len(candidate_text) > max_chars
        too_slow = bool(current) and cue.end_seconds - current[0].start_seconds > max_seconds
        if current and (too_long or too_slow):
            segments.append(_segment_from_cues(current))
            current = [cue]
        else:
            current = candidate
        if current and len(" ".join(item.text for item in current)) >= max_chars:
            segments.append(_segment_from_cues(current))
            current = []
    if current:
        segments.append(_segment_from_cues(current))

    if len(segments) >= 2 and len(segments[-1].text) < min_chars:
        previous = segments[-2]
        last = segments[-1]
        if len(previous.text) + len(last.text) + 1 <= max_chars:
            segments[-2:] = [CaptionSegment(previous.start_seconds, last.end_seconds, f"{previous.text} {last.text}")]
    return segments


def format_timestamped_segments(segments: list[CaptionSegment]) -> str:
    return "\n\n".join(f"[{format_timestamp(segment.start_seconds)}] {segment.text}" for segment in segments)


def parse_loose_json(text: str) -> object:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = min((index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0), default=-1)
    if start >= 0:
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end > start:
            cleaned = cleaned[start : end + 1]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError as exc:
        raise ValueError(f"Could not parse model JSON: {text[:300]}") from exc


def split_for_tts(text: str, max_chars: int = 900) -> list[str]:
    normalized = normalize_for_tts(text)
    if len(normalized) <= max_chars:
        return [normalized] if normalized else []

    sentences = [part.strip() for part in SENTENCE_BOUNDARY.split(normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_text(sentence, max_chars))
            continue
        candidate = f"{current}{sentence}" if not current else f"{current} {sentence}"
        if len(candidate) > max_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    return chunks


def chunk_source_text(text: str, max_chars: int = 30000) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in SENTENCE_BOUNDARY.split(text) if part.strip()]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            if len(paragraph) > max_chars:
                chunks.extend(_split_long_text(paragraph, max_chars))
            else:
                current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def normalize_for_tts(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*", "", cleaned)
    cleaned = cleaned.removesuffix("```").strip()
    cleaned = cleaned.replace("(", "，").replace(")", "，")
    cleaned = re.sub(r"[*#>`_]+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _split_long_text(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars].strip() for index in range(0, len(text), max_chars)]


def _parse_vtt_timestamp(value: str) -> float:
    match = VTT_TIMESTAMP.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid VTT timestamp: {value}")
    return (
        int(match.group("hours")) * 3600
        + int(match.group("minutes")) * 60
        + int(match.group("seconds"))
        + int(match.group("millis") or 0) / 1000
    )


def _clean_caption_text(text: str) -> str:
    cleaned = html.unescape(text)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _caption_delta(previous: str, current: str) -> str:
    if not previous:
        return current
    if current == previous or previous.startswith(current):
        return ""
    if current.startswith(previous):
        return _strip_delta_punctuation(current[len(previous) :])
    for overlap in range(min(len(previous), len(current)), 3, -1):
        if previous.endswith(current[:overlap]):
            return _strip_delta_punctuation(current[overlap:])
    return current


def _strip_delta_punctuation(text: str) -> str:
    return text.strip()


def _segment_from_cues(cues: list[CaptionCue]) -> CaptionSegment:
    return CaptionSegment(
        start_seconds=cues[0].start_seconds,
        end_seconds=cues[-1].end_seconds,
        text=_join_caption_text(cue.text for cue in cues),
    )


def _join_caption_text(parts: Iterable[str]) -> str:
    joined = " ".join(str(part).strip() for part in parts if str(part).strip())
    joined = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", joined)
    joined = re.sub(r"\s+([，。！？、,.!?：；》）】])", r"\1", joined)
    joined = re.sub(r"([《（【“‘])\s+", r"\1", joined)
    return joined.strip()


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

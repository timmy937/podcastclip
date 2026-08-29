from __future__ import annotations

from dataclasses import dataclass

import httpx

from .text import CaptionCue, format_timestamped_segments, segment_caption_cues


class SupadataError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupadataTranscript:
    transcript: str
    timestamped_transcript: str
    language: str | None
    source: str


class SupadataClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 90.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_transcript(
        self,
        *,
        url: str,
        language: str = "zh",
        mode: str = "native",
        chunk_size: int = 1000,
    ) -> SupadataTranscript:
        params = {
            "url": url,
            "lang": language,
            "mode": mode,
            "text": "false",
            "chunkSize": str(chunk_size),
        }
        data = self._get_json("/transcript", params)
        return _parse_transcript_response(data, source="Supadata")

    def get_metadata(self, *, url: str) -> dict[str, object]:
        return self._get_json("/metadata", {"url": url})

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, object]:
        response: httpx.Response | None = None
        last_error: httpx.TransportError | None = None
        for attempt in range(3):
            try:
                response = httpx.get(
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=params,
                    headers={"x-api-key": self.api_key, "User-Agent": "podcastclip/0.1.0"},
                    timeout=self.timeout,
                    trust_env=False,
                )
            except httpx.TransportError as exc:
                last_error = exc
                continue
            if response.status_code < 500 or attempt == 2:
                break
        if response is None:
            raise SupadataError("Supadata request failed after 3 transport attempts.") from last_error

        try:
            data = response.json()
        except ValueError as exc:
            raise SupadataError(
                f"Supadata returned non-JSON response: status={response.status_code}, body={response.text[:300]}"
            ) from exc
        if response.status_code >= 400:
            raise SupadataError(f"Supadata API error {response.status_code}: {data}")
        if not isinstance(data, dict):
            raise SupadataError(f"Unexpected Supadata response: {data}")
        return data


def _parse_transcript_response(data: dict[str, object], *, source: str) -> SupadataTranscript:
    content = data.get("content")
    language = str(data.get("lang")) if data.get("lang") else None
    if isinstance(content, str):
        text = content.strip()
        if not text:
            raise SupadataError("Supadata returned an empty transcript.")
        return SupadataTranscript(text, text, language, source)

    if not isinstance(content, list):
        raise SupadataError(f"Supadata transcript content has unexpected type: {type(content).__name__}")

    cues: list[CaptionCue] = []
    for item in content:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = item["text"].strip()
        if not text:
            continue
        start_ms = _number(item.get("offset"))
        duration_ms = _number(item.get("duration"))
        cues.append(CaptionCue(start_ms / 1000, (start_ms + duration_ms) / 1000, text))

    if not cues:
        raise SupadataError("Supadata returned no transcript chunks.")
    segments = segment_caption_cues(cues)
    timestamped = format_timestamped_segments(segments)
    transcript = " ".join(cue.text for cue in cues).strip()
    return SupadataTranscript(transcript, timestamped or transcript, language, source)

def _number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0

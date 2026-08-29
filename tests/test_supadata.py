import httpx
import pytest

import podcastclip.supadata as supadata
from podcastclip.supadata import SupadataClient, SupadataError, _parse_transcript_response


def test_parse_supadata_chunks_into_timestamped_segments() -> None:
    result = _parse_transcript_response(
        {
            "lang": "en",
            "availableLangs": ["en"],
            "content": [
                {"text": "First sentence.", "offset": 0, "duration": 3000},
                {"text": "Second sentence.", "offset": 3000, "duration": 3000},
            ],
        },
        source="Supadata",
    )

    assert result.language == "en"
    assert result.transcript == "First sentence. Second sentence."
    assert result.timestamped_transcript.startswith("[0:00] First sentence.")


def test_transport_failure_retries_without_exposing_api_key(monkeypatch) -> None:
    calls = 0

    def failing_get(*_args: object, **_kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("network unavailable")

    monkeypatch.setattr(supadata.httpx, "get", failing_get)
    client = SupadataClient("private-test-key", "https://api.supadata.ai/v1")

    with pytest.raises(SupadataError) as exc_info:
        client.get_metadata(url="https://youtu.be/example")

    assert calls == 3
    assert "private-test-key" not in str(exc_info.value)
    assert "curl" not in str(exc_info.value).lower()

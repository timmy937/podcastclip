from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import podcastclip.youtube as youtube
from podcastclip.youtube import canonical_youtube_url, fetch_video_info, validate_youtube_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123XYZ_0",
        "https://youtube.com/shorts/abc123XYZ_0",
        "https://m.youtube.com/live/abc123XYZ_0",
        "https://youtu.be/abc123XYZ_0",
    ],
)
def test_validate_youtube_url_accepts_supported_hosts(url: str) -> None:
    assert validate_youtube_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc123XYZ_0",
        "https://youtube.com.attacker.example/watch?v=abc123XYZ_0",
        "https://user:password@youtube.com/watch?v=abc123XYZ_0",  # pragma: allowlist secret
        "https://youtube.com:8443/watch?v=abc123XYZ_0",
        "file:///tmp/video.mp4",
        "not-a-url",
    ],
)
def test_validate_youtube_url_rejects_untrusted_urls(url: str) -> None:
    with pytest.raises(ValueError, match="YouTube video URL"):
        validate_youtube_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.youtube.com/watch?v=abc123XYZ_0&list=WL&index=4&t=90s",
            "https://www.youtube.com/watch?v=abc123XYZ_0",
        ),
        ("https://youtu.be/abc123XYZ_0?si=tracking", "https://www.youtube.com/watch?v=abc123XYZ_0"),
        (
            "https://m.youtube.com/shorts/abc123XYZ_0?feature=share",
            "https://www.youtube.com/watch?v=abc123XYZ_0",
        ),
    ],
)
def test_canonical_youtube_url_strips_non_video_parameters(url: str, expected: str) -> None:
    assert canonical_youtube_url(url) == expected


def test_canonical_youtube_url_requires_video_id() -> None:
    with pytest.raises(ValueError, match="YouTube video URL"):
        canonical_youtube_url("https://www.youtube.com/")


def test_yt_dlp_ignores_global_config_and_separates_url(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout='{"id":"abc123XYZ_0","title":"Test","webpage_url":"https://youtu.be/abc123XYZ_0"}',
            stderr="",
        )

    monkeypatch.setattr(youtube.subprocess, "run", fake_run)

    result = fetch_video_info("https://youtu.be/abc123XYZ_0")

    assert result.video_id == "abc123XYZ_0"
    command = calls[0]
    assert command[:4] == [youtube.sys.executable, "-m", "yt_dlp", "--ignore-config"]
    assert command[-2:] == ["--", "https://youtu.be/abc123XYZ_0"]


def test_invalid_url_never_reaches_yt_dlp(monkeypatch) -> None:
    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("yt-dlp must not run")

    monkeypatch.setattr(youtube.subprocess, "run", fail_run)

    with pytest.raises(ValueError, match="YouTube video URL"):
        fetch_video_info("https://attacker.example/video")

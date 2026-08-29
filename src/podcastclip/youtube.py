from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .text import format_timestamped_segments, parse_vtt, parse_vtt_cues, segment_caption_cues


DEFAULT_CAPTION_LANGS = ["zh-Hans", "zh-CN", "zh", "en", "en-US", "en.*"]
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    webpage_url: str


@dataclass(frozen=True)
class CaptionTranscript:
    transcript: str
    timestamped_transcript: str
    source: str
    language: str | None
    video: VideoInfo


def validate_youtube_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("请输入有效的 YouTube video URL。") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or hostname not in YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("请输入有效的 YouTube video URL。")
    return value


def youtube_video_id(value: str) -> str:
    value = validate_youtube_url(value)
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    video_id = ""
    if hostname == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if not video_id:
            path_parts = [part for part in parsed.path.split("/") if part]
            if len(path_parts) >= 2 and path_parts[0] in {"embed", "live", "shorts"}:
                video_id = path_parts[1]
    if not YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("请输入有效的 YouTube video URL。")
    return video_id


def canonical_youtube_url(value: str) -> str:
    return f"https://www.youtube.com/watch?v={youtube_video_id(value)}"


def fetch_video_info(
    url: str,
    *,
    cookies_from_browser: str | None = None,
    cookies: Path | None = None,
    js_runtime: str | None = None,
    remote_components: str | None = None,
) -> VideoInfo:
    url = validate_youtube_url(url)
    output = _run_yt_dlp(
        [
            *_yt_dlp_auth_args(
                cookies_from_browser=cookies_from_browser,
                cookies=cookies,
                js_runtime=js_runtime,
                remote_components=remote_components,
            ),
            "--no-playlist",
            "--dump-single-json",
            "--skip-download",
            "--",
            url,
        ]
    )
    data = json.loads(output)
    return VideoInfo(
        video_id=str(data.get("id") or "youtube-video"),
        title=str(data.get("title") or "YouTube video"),
        webpage_url=str(data.get("webpage_url") or url),
    )


def get_caption_transcript(
    url: str,
    *,
    work_dir: Path,
    caption_langs: list[str] | None = None,
    cookies_from_browser: str | None = None,
    cookies: Path | None = None,
    js_runtime: str | None = None,
    remote_components: str | None = None,
) -> CaptionTranscript | None:
    url = validate_youtube_url(url)
    work_dir.mkdir(parents=True, exist_ok=True)
    video = fetch_video_info(
        url,
        cookies_from_browser=cookies_from_browser,
        cookies=cookies,
        js_runtime=js_runtime,
        remote_components=remote_components,
    )
    langs = caption_langs or DEFAULT_CAPTION_LANGS
    template = str(work_dir / "%(id)s.%(ext)s")

    _run_yt_dlp(
        [
            *_yt_dlp_auth_args(
                cookies_from_browser=cookies_from_browser,
                cookies=cookies,
                js_runtime=js_runtime,
                remote_components=remote_components,
            ),
            "--no-playlist",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(langs),
            "--sub-format",
            "vtt",
            "--skip-download",
            "--no-warnings",
            "-o",
            template,
            "--",
            url,
        ],
        check=False,
    )

    caption_path = _pick_caption(work_dir.glob("*.vtt"), langs)
    if not caption_path:
        return None

    vtt_text = caption_path.read_text(encoding="utf-8", errors="ignore")
    cues = parse_vtt_cues(vtt_text)
    transcript = parse_vtt(vtt_text)
    if not transcript:
        return None

    segments = segment_caption_cues(cues)
    timestamped_transcript = format_timestamped_segments(segments) or transcript

    return CaptionTranscript(
        transcript=transcript,
        timestamped_transcript=timestamped_transcript,
        source=str(caption_path),
        language=_caption_language(caption_path),
        video=video,
    )


def download_audio_for_asr(
    url: str,
    *,
    work_dir: Path,
    cookies_from_browser: str | None = None,
    cookies: Path | None = None,
    js_runtime: str | None = None,
    remote_components: str | None = None,
    start_seconds: int | None = None,
    duration_seconds: int | None = None,
) -> Path:
    url = validate_youtube_url(url)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(work_dir / "%(id)s.%(ext)s")
    _run_yt_dlp(
        [
            *_yt_dlp_auth_args(
                cookies_from_browser=cookies_from_browser,
                cookies=cookies,
                js_runtime=js_runtime,
                remote_components=remote_components,
            ),
            "--no-playlist",
            *_yt_dlp_section_args(start_seconds=start_seconds, duration_seconds=duration_seconds),
            "-f",
            "bestaudio/best",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "7",
            "-o",
            output_template,
            "--",
            url,
        ]
    )

    audio_files = sorted(work_dir.glob("*.mp3"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not audio_files:
        raise RuntimeError("yt-dlp finished but no MP3 audio file was produced.")

    raw_audio = audio_files[0]
    compact_audio = raw_audio.with_name(f"{raw_audio.stem}.asr.mp3")
    ffmpeg_args = ["-y"]
    if start_seconds is not None:
        ffmpeg_args.extend(["-ss", str(start_seconds)])
    ffmpeg_args.extend(["-i", str(raw_audio)])
    if duration_seconds is not None:
        ffmpeg_args.extend(["-t", str(duration_seconds)])
    ffmpeg_args.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            str(compact_audio),
        ]
    )
    _run_ffmpeg(ffmpeg_args)
    return compact_audio


def _run_yt_dlp(args: list[str], *, check: bool = True) -> str:
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", *args]
    try:
        result = subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError("yt-dlp is missing. Run: pip install -e .") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"yt-dlp failed: {message}") from exc
    return result.stdout


def _yt_dlp_auth_args(
    *,
    cookies_from_browser: str | None,
    cookies: Path | None,
    js_runtime: str | None,
    remote_components: str | None,
) -> list[str]:
    args: list[str] = []
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])
    if cookies:
        args.extend(["--cookies", str(cookies)])
    if js_runtime:
        args.extend(["--js-runtimes", js_runtime])
    if remote_components:
        args.extend(["--remote-components", remote_components])
    return args


def _yt_dlp_section_args(start_seconds: int | None, duration_seconds: int | None) -> list[str]:
    if duration_seconds is None:
        return []
    start = start_seconds or 0
    end = start + duration_seconds
    return ["--download-sections", f"*{start}-{end}"]


def _run_ffmpeg(args: list[str]) -> None:
    command = ["ffmpeg", *args]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is missing. Install ffmpeg before using ASR fallback.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"ffmpeg failed: {message}") from exc


def _pick_caption(paths: object, langs: list[str]) -> Path | None:
    candidates = list(paths)
    if not candidates:
        return None

    for lang in langs:
        normalized = lang.replace(".*", "")
        for path in candidates:
            if f".{normalized}." in path.name:
                return path
    return candidates[0]


def _caption_language(path: Path) -> str | None:
    parts = path.name.split(".")
    if len(parts) >= 3:
        return parts[-2]
    return None

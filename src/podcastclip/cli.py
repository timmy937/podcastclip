from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import load_settings
from .options import (
    DEFAULT_LANGUAGE_CODE,
    DURATION_PRESETS,
    normalize_language_code,
)
from .pipeline import run_youtube_to_audio, transcribe_youtube_audio
from .youtube import DEFAULT_CAPTION_LANGS


def main() -> None:
    parser = argparse.ArgumentParser(prog="podcastclip")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Turn a YouTube video into a short audio brief.")
    run_parser.add_argument("url", help="YouTube video URL.")
    run_parser.add_argument("--output-dir", default="output", help="Directory for generated files.")
    duration_group = run_parser.add_mutually_exclusive_group()
    duration_group.add_argument(
        "--duration",
        choices=tuple(DURATION_PRESETS),
        default=None,
        help="Duration preset: quick=2m, standard=5m, deep=8m, long=15m. Defaults to standard.",
    )
    duration_group.add_argument(
        "--minutes",
        type=positive_int,
        default=None,
        help="Backward-compatible custom target duration in minutes.",
    )
    run_parser.add_argument(
        "--target-language",
        type=parse_language_code,
        default=DEFAULT_LANGUAGE_CODE,
        metavar="{zh,en}",
        help="Output language code: zh=中文, en=English. Defaults to zh.",
    )
    run_parser.add_argument(
        "--caption-langs",
        default=",".join(DEFAULT_CAPTION_LANGS),
        help="Comma-separated YouTube caption languages to prefer.",
    )
    run_parser.add_argument("--no-asr-fallback", action="store_true", help="Do not download audio for ASR.")
    run_parser.add_argument("--asr-language", default=None, help="Optional ASR language hint, such as zh or en.")
    run_parser.add_argument("--feed-base-url", default=None, help="Public base URL for output/ when generating RSS.")
    run_parser.add_argument("--tts-speed", type=float, default=1.0, help="TTS speed.")
    run_parser.add_argument("--quiet", action="store_true", help="Disable progress logs.")
    add_youtube_auth_args(run_parser)

    transcribe_parser = subparsers.add_parser("transcribe", help="Download YouTube audio and run StepFun ASR only.")
    transcribe_parser.add_argument("url", help="YouTube video URL.")
    transcribe_parser.add_argument("--output-dir", default="output", help="Directory for generated files.")
    transcribe_parser.add_argument("--asr-language", default=None, help="Optional ASR language hint, such as zh or en.")
    transcribe_parser.add_argument("--start-seconds", type=int, default=None, help="Optional sample start offset.")
    transcribe_parser.add_argument("--duration-seconds", type=int, default=None, help="Optional sample duration.")
    transcribe_parser.add_argument("--quiet", action="store_true", help="Disable progress logs.")
    add_youtube_auth_args(transcribe_parser)

    web_parser = subparsers.add_parser("web", help="Start the local PodcastClip web dashboard.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Web server host.")
    web_parser.add_argument("--port", type=positive_int, default=8765, help="Web server port.")
    web_parser.add_argument("--output-dir", default="output", help="Directory for generated files.")

    delete_parser = subparsers.add_parser(
        "delete",
        help="Remove an episode locally and from the configured R2 feed.",
    )
    delete_parser.add_argument("episode_id", help="Metadata filename stem under output/episodes/.")
    delete_parser.add_argument("--output-dir", default="output", help="Directory for generated files.")

    args = parser.parse_args()

    if args.command == "web":
        from .web import _is_loopback_host, serve_web

        if not _is_loopback_host(args.host):
            parser.error(
                "the built-in web server only supports loopback hosts"
            )

        serve_web(
            host=args.host,
            port=args.port,
            output_dir=Path(args.output_dir),
        )
    elif args.command == "delete":
        from .episodes import delete_episode

        settings = load_settings()
        result = delete_episode(
            output_dir=Path(args.output_dir),
            episode_id=args.episode_id,
            settings=settings,
        )
        print(f"Deleted episode: {result.episode_id}")
        if result.feed_path:
            print(f"Feed: {result.feed_path}")
    elif args.command == "run":
        settings = load_settings()
        progress = None if args.quiet else print_progress
        result = run_youtube_to_audio(
            url=args.url,
            settings=settings,
            output_dir=Path(args.output_dir),
            minutes=args.minutes,
            target_language=args.target_language,
            duration_preset=args.duration,
            caption_langs=[item.strip() for item in args.caption_langs.split(",") if item.strip()],
            asr_fallback=not args.no_asr_fallback,
            asr_language=args.asr_language,
            feed_base_url=args.feed_base_url,
            tts_speed=args.tts_speed,
            cookies_from_browser=args.cookies_from_browser,
            cookies=Path(args.cookies) if args.cookies else None,
            js_runtime=args.js_runtime,
            remote_components=args.remote_components,
            progress=progress,
        )
        print(f"Title: {result.title}")
        print(f"Transcript: {result.transcript_path}")
        print(f"Overview: {result.overview_path}")
        print(f"Script: {result.script_path}")
        print(f"Audio: {result.audio_path}")
        print(f"Metadata: {result.metadata_path}")
        if result.feed_path:
            print(f"Feed: {result.feed_path}")
    elif args.command == "transcribe":
        settings = load_settings()
        progress = None if args.quiet else print_progress
        audio_path, transcript_path = transcribe_youtube_audio(
            url=args.url,
            settings=settings,
            output_dir=Path(args.output_dir),
            cookies_from_browser=args.cookies_from_browser,
            cookies=Path(args.cookies) if args.cookies else None,
            js_runtime=args.js_runtime,
            remote_components=args.remote_components,
            asr_language=args.asr_language,
            start_seconds=args.start_seconds,
            duration_seconds=args.duration_seconds,
            progress=progress,
        )
        print(f"Audio: {audio_path}")
        print(f"Transcript: {transcript_path}")


def add_youtube_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Pass a browser name to yt-dlp, such as chrome, safari, or firefox.",
    )
    parser.add_argument("--cookies", default=None, help="Path to a Netscape-format cookies.txt file.")
    parser.add_argument("--js-runtime", default=None, help="yt-dlp JavaScript runtime, such as node.")
    parser.add_argument(
        "--remote-components",
        default=None,
        help="yt-dlp remote component source, such as ejs:github for YouTube JS challenges.",
    )


def print_progress(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_language_code(value: str) -> str:
    try:
        return normalize_language_code(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


if __name__ == "__main__":
    main()

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def stitch_mp3(chunks: list[Path], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not chunks:
        raise ValueError("No audio chunks to stitch.")
    if len(chunks) == 1:
        shutil.copyfile(chunks[0], output_path)
        return output_path

    concat_file = output_path.with_suffix(".concat.txt")
    concat_file.write_text(
        "\n".join(f"file '{_escape_concat_path(path)}'" for path in chunks),
        encoding="utf-8",
    )
    _run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)])
    return output_path


def probe_duration_seconds(audio_path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _run_ffmpeg(args: list[str]) -> None:
    try:
        subprocess.run(["ffmpeg", *args], check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is missing. Install ffmpeg before generating final MP3.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"ffmpeg failed: {message}") from exc


def _escape_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")

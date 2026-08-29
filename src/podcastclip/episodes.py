from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree as ET

from .config import Settings
from .rss import SHOW_ARTWORK_RELATIVE, rebuild_feed
from .storage import create_storage


@dataclass(frozen=True)
class EpisodeDeletionResult:
    episode_id: str
    feed_path: Path | None


def delete_episode(
    *,
    output_dir: Path,
    episode_id: str,
    settings: Settings,
) -> EpisodeDeletionResult:
    output_dir = output_dir.resolve()
    normalized_id = episode_id.strip().removesuffix(".json")
    if not normalized_id or Path(normalized_id).name != normalized_id:
        raise ValueError("Invalid episode ID.")

    metadata_path = (output_dir / "episodes" / f"{normalized_id}.json").resolve()
    if metadata_path.parent != output_dir / "episodes" or not metadata_path.is_file():
        raise FileNotFoundError(f"Episode metadata not found: {normalized_id}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"Episode metadata must be an object: {metadata_path}")
    audio_relative = _validated_relative_path(output_dir, metadata.get("audio_file"), required=True)

    metadata["deleted"] = True
    _write_json_atomic(metadata_path, metadata)

    storage = create_storage(settings)
    feed_base_url = storage.public_base_url if storage else _existing_feed_base_url(output_dir / "feed.xml")
    feed_path: Path | None = None
    if feed_base_url:
        feed_path = rebuild_feed(
            output_dir=output_dir,
            feed_base_url=feed_base_url,
            include_source_url=getattr(settings, "rss_include_source_url", True),
        )
        if storage:
            storage.publish_files(
                output_dir,
                [SHOW_ARTWORK_RELATIVE, feed_path.relative_to(output_dir)],
            )

    if storage:
        storage.delete_files(output_dir, [audio_relative])

    cleanup_paths = [audio_relative]
    for key in ("transcript_file", "overview_file", "notes_file", "script_file"):
        relative = _validated_relative_path(output_dir, metadata.get(key), required=False)
        if relative is not None:
            cleanup_paths.append(relative)
            if key == "script_file":
                script_path = output_dir / relative
                cleanup_paths.append(script_path.with_suffix(".meta.json").relative_to(output_dir))

    for relative in cleanup_paths:
        (output_dir / relative).unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)
    return EpisodeDeletionResult(episode_id=normalized_id, feed_path=feed_path)


def _validated_relative_path(
    output_dir: Path,
    value: object,
    *,
    required: bool,
) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        if required:
            raise ValueError("Episode metadata is missing audio_file.")
        return None
    candidate = (output_dir / value).resolve()
    if candidate == output_dir or output_dir not in candidate.parents:
        raise ValueError(f"Episode file is outside output: {value}")
    return candidate.relative_to(output_dir)


def _existing_feed_base_url(feed_path: Path) -> str | None:
    if not feed_path.is_file():
        return None
    try:
        return ET.parse(feed_path).getroot().findtext("channel/link") or None
    except (ET.ParseError, OSError):
        return None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

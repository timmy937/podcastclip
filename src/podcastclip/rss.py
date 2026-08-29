from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
# ElementTree is only used to construct XML; this module never parses input.
from xml.etree import ElementTree as ET  # nosec B405

from .youtube import canonical_youtube_url


ITUNES_NAMESPACE = "http://www.itunes.com/dtds/podcast-1.0.dtd"
SHOW_ARTWORK_FILENAME = "ai-brief-cover-v1.jpg"
SHOW_ARTWORK_SOURCE = Path(__file__).with_name("assets") / SHOW_ARTWORK_FILENAME
SHOW_ARTWORK_RELATIVE = Path("artwork") / SHOW_ARTWORK_FILENAME

ET.register_namespace("itunes", ITUNES_NAMESPACE)


def rebuild_feed(
    *,
    output_dir: Path,
    feed_base_url: str,
    channel_title: str = "PodcastClip AI Briefs",
    channel_description: str = "Personal AI-generated running briefs.",
    include_source_url: bool = True,
) -> Path:
    base_url = feed_base_url.rstrip("/")
    episodes_dir = output_dir / "episodes"
    metadata_files = sorted(episodes_dir.glob("*.json"), reverse=True)
    artwork_path = ensure_show_artwork(output_dir)
    artwork_relative = artwork_path.relative_to(output_dir).as_posix()
    artwork_url = f"{base_url}/{quote(artwork_relative, safe='/')}"

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = channel_title
    ET.SubElement(channel, "link").text = base_url
    ET.SubElement(channel, "description").text = channel_description
    ET.SubElement(channel, "language").text = "zh-cn"
    ET.SubElement(channel, f"{{{ITUNES_NAMESPACE}}}image", {"href": artwork_url})
    rss_image = ET.SubElement(channel, "image")
    ET.SubElement(rss_image, "url").text = artwork_url
    ET.SubElement(rss_image, "title").text = channel_title
    ET.SubElement(rss_image, "link").text = base_url

    for metadata_file in metadata_files:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if metadata.get("deleted") is True:
            continue
        rel_audio = metadata["audio_file"]
        audio_path = output_dir / rel_audio
        if not audio_path.exists():
            continue

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = metadata["title"]
        ET.SubElement(item, "description").text = (
            metadata.get("translated_title") or metadata.get("description") or metadata["title"]
        )
        item_link = base_url
        if include_source_url and metadata.get("source_url"):
            try:
                item_link = canonical_youtube_url(str(metadata["source_url"]))
            except ValueError:
                pass
        ET.SubElement(item, "link").text = item_link
        ET.SubElement(item, "guid").text = metadata.get("guid") or metadata_file.stem

        published_at = _parse_datetime(metadata.get("published_at"))
        ET.SubElement(item, "pubDate").text = format_datetime(published_at)
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": f"{base_url}/{quote(rel_audio, safe='/')}",
                "length": str(audio_path.stat().st_size),
                "type": "audio/mpeg",
            },
        )

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    feed_path = output_dir / "feed.xml"
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    return feed_path


def ensure_show_artwork(output_dir: Path) -> Path:
    if not SHOW_ARTWORK_SOURCE.is_file():
        raise RuntimeError(f"Podcast artwork is missing: {SHOW_ARTWORK_SOURCE}")
    artwork_path = output_dir / SHOW_ARTWORK_RELATIVE
    artwork_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SHOW_ARTWORK_SOURCE, artwork_path)
    return artwork_path


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

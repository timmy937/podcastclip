from pathlib import Path
from xml.etree import ElementTree as ET

from podcastclip.rss import ITUNES_NAMESPACE, SHOW_ARTWORK_RELATIVE, rebuild_feed


def test_rss_enclosure_url_encodes_episode_filename(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    audio = episodes / "2026-08-20-中文 节目.mp3"
    audio.write_bytes(b"mp3")
    (episodes / "episode.json").write_text(
        '{"title":"AI Brief: Test Show","translated_title":"测试节目",'
        '"description":"Short 中文 running brief generated from YouTube.",'
        '"source_url":"https://www.youtube.com/watch?v=abc123XYZ_0&list=WL&index=4&t=90s",'
        '"audio_file":"episodes/2026-08-20-中文 节目.mp3"}',
        encoding="utf-8",
    )

    feed = rebuild_feed(output_dir=tmp_path, feed_base_url="https://feed.example.com/podcastclip")
    content = feed.read_text(encoding="utf-8")

    assert "2026-08-20-%E4%B8%AD%E6%96%87%20%E8%8A%82%E7%9B%AE.mp3" in content
    assert (tmp_path / SHOW_ARTWORK_RELATIVE).is_file()

    channel = ET.parse(feed).getroot().find("channel")
    assert channel is not None
    itunes_image = channel.find(f"{{{ITUNES_NAMESPACE}}}image")
    assert itunes_image is not None
    expected_artwork_url = "https://feed.example.com/podcastclip/artwork/ai-brief-cover-v1.jpg"
    assert itunes_image.attrib["href"] == expected_artwork_url
    assert channel.findtext("image/url") == expected_artwork_url
    assert channel.findtext("item/title") == "AI Brief: Test Show"
    assert channel.findtext("item/description") == "测试节目"
    assert channel.findtext("item/link") == "https://www.youtube.com/watch?v=abc123XYZ_0"
    assert "list=WL" not in content
    assert "index=4" not in content
    assert "t=90s" not in content


def test_rss_can_hide_source_url(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    (episodes / "episode.mp3").write_bytes(b"mp3")
    (episodes / "episode.json").write_text(
        '{"title":"AI Brief: Test Show",'
        '"source_url":"https://youtu.be/abc123XYZ_0",'
        '"audio_file":"episodes/episode.mp3"}',
        encoding="utf-8",
    )

    feed = rebuild_feed(
        output_dir=tmp_path,
        feed_base_url="https://feed.example.com/podcastclip",
        include_source_url=False,
    )

    channel = ET.parse(feed).getroot().find("channel")
    assert channel is not None
    assert channel.findtext("item/link") == "https://feed.example.com/podcastclip"


def test_rss_skips_tombstoned_episode(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    audio = episodes / "deleted.mp3"
    audio.write_bytes(b"mp3")
    (episodes / "deleted.json").write_text(
        '{"title":"Deleted","audio_file":"episodes/deleted.mp3","deleted":true}',
        encoding="utf-8",
    )

    feed = rebuild_feed(output_dir=tmp_path, feed_base_url="https://feed.example.com/podcastclip")

    channel = ET.parse(feed).getroot().find("channel")
    assert channel is not None
    assert channel.find("item") is None
    assert channel.findtext("description") == "Personal AI-generated running briefs."

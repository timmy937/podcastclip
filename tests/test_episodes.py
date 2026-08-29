from __future__ import annotations

import json
from pathlib import Path

import pytest

import podcastclip.episodes as episodes_module
from podcastclip.episodes import delete_episode


class FakeStorage:
    public_base_url = "https://feed.example.com/podcastclip"

    def __init__(self, *, fail_delete: bool = False) -> None:
        self.fail_delete = fail_delete
        self.published: list[list[Path]] = []
        self.deleted: list[list[Path]] = []

    def publish_files(self, _output_dir: Path, relative_paths: list[Path]) -> dict[str, str]:
        self.published.append(relative_paths)
        return {}

    def delete_files(self, _output_dir: Path, relative_paths: list[Path]) -> None:
        self.deleted.append(relative_paths)
        if self.fail_delete:
            raise RuntimeError("R2 delete failed")


def _write_episode(output_dir: Path) -> tuple[Path, Path]:
    episodes = output_dir / "episodes"
    text = output_dir / "text"
    episodes.mkdir()
    text.mkdir()
    audio = episodes / "episode.mp3"
    transcript = text / "episode-transcript.txt"
    audio.write_bytes(b"mp3")
    transcript.write_text("transcript", encoding="utf-8")
    metadata = episodes / "episode.json"
    metadata.write_text(
        json.dumps(
            {
                "title": "Episode",
                "audio_file": "episodes/episode.mp3",
                "transcript_file": "text/episode-transcript.txt",
            }
        ),
        encoding="utf-8",
    )
    return metadata, audio


def test_delete_episode_updates_feed_then_deletes_remote_and_local(monkeypatch, tmp_path: Path) -> None:
    metadata, audio = _write_episode(tmp_path)
    storage = FakeStorage()
    monkeypatch.setattr(episodes_module, "create_storage", lambda _settings: storage)

    result = delete_episode(output_dir=tmp_path, episode_id="episode", settings=object())

    assert result.episode_id == "episode"
    assert storage.published == [[Path("artwork/ai-brief-cover-v1.jpg"), Path("feed.xml")]]
    assert storage.deleted == [[Path("episodes/episode.mp3")]]
    assert not metadata.exists()
    assert not audio.exists()
    assert not (tmp_path / "text" / "episode-transcript.txt").exists()


def test_delete_episode_keeps_tombstone_for_retry_when_remote_delete_fails(monkeypatch, tmp_path: Path) -> None:
    metadata, audio = _write_episode(tmp_path)
    storage = FakeStorage(fail_delete=True)
    monkeypatch.setattr(episodes_module, "create_storage", lambda _settings: storage)

    with pytest.raises(RuntimeError, match="R2 delete failed"):
        delete_episode(output_dir=tmp_path, episode_id="episode", settings=object())

    assert audio.exists()
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert saved["deleted"] is True
    assert "Episode" not in (tmp_path / "feed.xml").read_text(encoding="utf-8")


def test_delete_episode_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid episode ID"):
        delete_episode(output_dir=tmp_path, episode_id="../secret", settings=object())

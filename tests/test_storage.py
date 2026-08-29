from pathlib import Path

from podcastclip.storage import R2Storage


class FakeR2Client:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str, dict[str, str]]] = []
        self.deletes: list[tuple[str, str]] = []

    def upload_file(self, filename: str, bucket: str, key: str, *, ExtraArgs: dict[str, str]) -> None:
        self.uploads.append((filename, bucket, key, ExtraArgs))

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deletes.append((Bucket, Key))


def test_r2_publishes_only_selected_episode_and_feed(tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    episodes_dir = tmp_path / "episodes"
    text_dir.mkdir()
    episodes_dir.mkdir()
    (text_dir / "episode-script.txt").write_text("中文脚本", encoding="utf-8")
    (episodes_dir / "2026-08-19-节目.mp3").write_bytes(b"mp3")
    (episodes_dir / "2026-08-18-历史节目.mp3").write_bytes(b"old")
    (episodes_dir / "episode.json").write_text("{}", encoding="utf-8")
    (tmp_path / "feed.xml").write_text("<rss />", encoding="utf-8")

    client = FakeR2Client()
    storage = R2Storage(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="podcastclip",
        access_key_id="access",
        secret_access_key="secret",  # pragma: allowlist secret
        region="auto",
        prefix="podcastclip",
        public_base_url="https://feed.example.com",
        client=client,
    )

    urls = storage.publish_files(
        tmp_path,
        [Path("episodes/2026-08-19-节目.mp3"), Path("feed.xml")],
    )

    assert storage.public_base_url == "https://feed.example.com/podcastclip"
    assert urls["feed.xml"] == "https://feed.example.com/podcastclip/feed.xml"
    assert urls["episodes/2026-08-19-节目.mp3"].endswith("/episodes/2026-08-19-%E8%8A%82%E7%9B%AE.mp3")
    assert {upload[2] for upload in client.uploads} == {
        "podcastclip/feed.xml",
        "podcastclip/episodes/2026-08-19-节目.mp3",
    }
    assert set(urls) == {"feed.xml", "episodes/2026-08-19-节目.mp3"}
    feed_upload = next(upload for upload in client.uploads if upload[2] == "podcastclip/feed.xml")
    assert feed_upload[3] == {
        "ContentType": "application/xml",
        "CacheControl": "public, max-age=300",
    }


def test_r2_checks_cancellation_before_each_upload(tmp_path: Path) -> None:
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir()
    (episodes_dir / "one.mp3").write_bytes(b"one")
    (episodes_dir / "two.mp3").write_bytes(b"two")

    client = FakeR2Client()
    storage = R2Storage(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="podcastclip",
        access_key_id="access",
        secret_access_key="secret",  # pragma: allowlist secret
        region="auto",
        prefix="podcastclip",
        public_base_url="https://feed.example.com",
        client=client,
    )
    checked: list[str] = []

    def before_upload(path: Path) -> None:
        checked.append(path.name)
        if path.name == "two.mp3":
            raise RuntimeError("cancelled")

    try:
        storage.publish_files(
            tmp_path,
            [Path("episodes/one.mp3"), Path("episodes/two.mp3")],
            before_upload=before_upload,
        )
    except RuntimeError as exc:
        assert str(exc) == "cancelled"
    else:
        raise AssertionError("Expected upload cancellation")

    assert checked == ["one.mp3", "two.mp3"]
    assert [upload[2] for upload in client.uploads] == ["podcastclip/episodes/one.mp3"]


def test_r2_publishes_artwork_and_selected_files(tmp_path: Path) -> None:
    artwork = tmp_path / "artwork" / "ai-brief-cover-v1.jpg"
    artwork.parent.mkdir()
    artwork.write_bytes(b"jpg")
    (tmp_path / "feed.xml").write_text("<rss />", encoding="utf-8")

    client = FakeR2Client()
    storage = R2Storage(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="podcastclip",
        access_key_id="access",
        secret_access_key="secret",  # pragma: allowlist secret
        region="auto",
        prefix="podcastclip",
        public_base_url="https://feed.example.com",
        client=client,
    )

    urls = storage.publish_files(
        tmp_path,
        [Path("artwork/ai-brief-cover-v1.jpg"), Path("feed.xml")],
    )

    assert set(urls) == {"artwork/ai-brief-cover-v1.jpg", "feed.xml"}
    assert {upload[2] for upload in client.uploads} == {
        "podcastclip/artwork/ai-brief-cover-v1.jpg",
        "podcastclip/feed.xml",
    }
    artwork_upload = next(upload for upload in client.uploads if upload[2].endswith(".jpg"))
    assert artwork_upload[3] == {
        "ContentType": "image/jpeg",
        "CacheControl": "public, max-age=31536000, immutable",
    }


def test_r2_deletes_only_valid_selected_files(tmp_path: Path) -> None:
    audio = tmp_path / "episodes" / "episode.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"mp3")
    client = FakeR2Client()
    storage = R2Storage(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="podcastclip",
        access_key_id="access",
        secret_access_key="secret",  # pragma: allowlist secret
        region="auto",
        prefix="podcastclip",
        public_base_url="https://feed.example.com",
        client=client,
    )

    storage.delete_files(tmp_path, [Path("episodes/episode.mp3")])

    assert client.deletes == [("podcastclip", "podcastclip/episodes/episode.mp3")]


def test_r2_rejects_delete_path_outside_output(tmp_path: Path) -> None:
    client = FakeR2Client()
    storage = R2Storage(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket="podcastclip",
        access_key_id="access",
        secret_access_key="secret",  # pragma: allowlist secret
        region="auto",
        prefix="podcastclip",
        public_base_url="https://feed.example.com",
        client=client,
    )

    try:
        storage.delete_files(tmp_path, [Path("../secret.mp3")])
    except ValueError as exc:
        assert "outside output" in str(exc)
    else:
        raise AssertionError("Expected an invalid delete path")

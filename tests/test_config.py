import pytest

from podcastclip.config import load_settings


def test_audio_defaults_to_chat_configuration(monkeypatch) -> None:
    for name in (
        "STEPFUN_API_KEY",
        "STEPFUN_BASE_URL",
        "STEPFUN_AUDIO_API_KEY",
        "STEPFUN_AUDIO_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("STEPFUN_CHAT_BASE_URL", "https://api.stepfun.com/step_plan/v1")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "local")

    settings = load_settings()

    assert settings.audio_api_key == settings.chat_api_key == "chat-key"  # pragma: allowlist secret
    assert settings.audio_base_url == settings.chat_base_url == "https://api.stepfun.com/step_plan/v1"
    assert settings.storage_backend == "local"
    assert settings.rss_include_source_url is True


def test_rss_source_url_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "local")
    monkeypatch.setenv("PODCASTCLIP_RSS_INCLUDE_SOURCE_URL", "false")

    settings = load_settings()

    assert settings.rss_include_source_url is False


def test_r2_storage_requires_publish_configuration(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "r2")
    for name in (
        "R2_ENDPOINT_URL",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_PUBLIC_BASE_URL",
    ):
        # An active local .env may contain real R2 settings; empty overrides
        # keep this test focused on the missing-configuration path.
        monkeypatch.setenv(name, "")

    with pytest.raises(RuntimeError, match="R2 storage is enabled"):
        load_settings()


def test_remote_model_base_url_requires_https(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("STEPFUN_CHAT_BASE_URL", "http://api.example.com/v1")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "local")

    with pytest.raises(RuntimeError, match="STEPFUN_CHAT_BASE_URL must use HTTPS"):
        load_settings()


def test_loopback_model_base_url_may_use_http(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("STEPFUN_CHAT_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "local")

    settings = load_settings()

    assert settings.chat_base_url == "http://127.0.0.1:11434/v1"


def test_model_base_url_rejects_invalid_port(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("STEPFUN_CHAT_BASE_URL", "https://api.example.com:99999/v1")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "local")

    with pytest.raises(RuntimeError, match="STEPFUN_CHAT_BASE_URL must be a valid service URL"):
        load_settings()


def test_r2_public_url_requires_https(monkeypatch) -> None:
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("STEPFUN_CHAT_BASE_URL", "https://api.stepfun.com/step_plan/v1")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://account.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_BUCKET", "podcastclip")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "http://feed.example.com")

    with pytest.raises(RuntimeError, match="R2_PUBLIC_BASE_URL must use HTTPS"):
        load_settings()

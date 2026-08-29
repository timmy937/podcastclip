from __future__ import annotations

import sys
from pathlib import Path

import pytest

from podcastclip.cli import main


def test_cli_rejects_remote_web_binding(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["podcastclip", "web", "--host", "0.0.0.0"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_cli_does_not_offer_allow_remote(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["podcastclip", "web", "--allow-remote"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
    assert "unrecognized arguments: --allow-remote" in capsys.readouterr().err


def test_cli_delete_removes_local_episode(monkeypatch, capsys, tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    audio = episodes / "episode.mp3"
    audio.write_bytes(b"mp3")
    (episodes / "episode.json").write_text(
        '{"title":"Episode","audio_file":"episodes/episode.mp3"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("STEPFUN_CHAT_API_KEY", "chat-key")
    monkeypatch.setenv("STEPFUN_CHAT_BASE_URL", "https://api.stepfun.com/step_plan/v1")
    monkeypatch.setenv("PODCASTCLIP_STORAGE_BACKEND", "local")
    monkeypatch.setattr(
        sys,
        "argv",
        ["podcastclip", "delete", "episode", "--output-dir", str(tmp_path)],
    )

    main()

    assert "Deleted episode: episode" in capsys.readouterr().out
    assert not audio.exists()
    assert not (episodes / "episode.json").exists()

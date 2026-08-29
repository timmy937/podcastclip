from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from podcastclip.pipeline import PipelineCancelledError
from podcastclip.web import JobManager, JobQueueFullError, PodcastClipServer, _is_loopback_host, serve_web


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch) -> None:
    monkeypatch.setattr("podcastclip.web.load_settings", lambda: object())


def _wait_for_status(manager: JobManager, job_id: str, expected: str) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job["status"] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {expected}")


def _state_change_headers(server: PodcastClipServer) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_port}"
    return {
        "Content-Type": "application/json",
        "Origin": origin,
        "Sec-Fetch-Site": "same-origin",
        "X-PodcastClip-Token": server.api_token,
    }


def test_queued_job_can_be_cancelled(monkeypatch, tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake_pipeline(**_kwargs: object) -> None:
        started.set()
        release.wait(timeout=1)
        raise RuntimeError("first job stopped for test")

    monkeypatch.setattr("podcastclip.web.run_youtube_to_audio", fake_pipeline)
    manager = JobManager(output_dir=tmp_path)
    try:
        first = manager.create_job(url="https://youtu.be/first01", duration="standard", target_language="zh")
        assert started.wait(timeout=1)
        second = manager.create_job(url="https://youtu.be/second2", duration="standard", target_language="zh")

        cancelled = manager.cancel_job(second.id)

        assert cancelled is not None
        assert cancelled["status"] == "cancelled"
        assert cancelled["can_cancel"] is False
    finally:
        release.set()
        _wait_for_status(manager, first.id, "failed")
        manager.close()


def test_running_job_stops_after_cancellation_checkpoint(monkeypatch, tmp_path: Path) -> None:
    started = threading.Event()
    continue_to_checkpoint = threading.Event()

    def fake_pipeline(*, cancel_requested, **_kwargs: object) -> None:
        started.set()
        assert continue_to_checkpoint.wait(timeout=1)
        if cancel_requested():
            raise PipelineCancelledError("cancelled")

    monkeypatch.setattr("podcastclip.web.run_youtube_to_audio", fake_pipeline)
    manager = JobManager(output_dir=tmp_path)
    try:
        job = manager.create_job(url="https://youtu.be/running1", duration="deep", target_language="zh")
        assert started.wait(timeout=1)

        cancelling = manager.cancel_job(job.id)
        assert cancelling is not None
        assert cancelling["status"] == "cancelling"
        continue_to_checkpoint.set()

        _wait_for_status(manager, job.id, "cancelled")
        cancelled = manager.get_job(job.id)
        assert cancelled is not None
        assert cancelled["error"] is None
    finally:
        continue_to_checkpoint.set()
        manager.close()


def test_delete_job_endpoint_requests_cancellation(monkeypatch, tmp_path: Path) -> None:
    started = threading.Event()
    continue_to_checkpoint = threading.Event()

    def fake_pipeline(*, cancel_requested, **_kwargs: object) -> None:
        started.set()
        assert continue_to_checkpoint.wait(timeout=1)
        if cancel_requested():
            raise PipelineCancelledError("cancelled")

    monkeypatch.setattr("podcastclip.web.run_youtube_to_audio", fake_pipeline)
    server = PodcastClipServer(("127.0.0.1", 0), output_dir=tmp_path)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(
            "POST",
            "/api/jobs",
            body=json.dumps(
                {
                    "url": "https://youtu.be/delete01",
                    "duration": "deep",
                    "target_language": "zh",
                }
            ),
            headers=_state_change_headers(server),
        )
        response = connection.getresponse()
        created = json.loads(response.read().decode("utf-8"))
        assert response.status == 201
        assert started.wait(timeout=1)

        connection.request(
            "DELETE",
            f"/api/jobs/{created['id']}",
            headers={
                "Origin": f"http://127.0.0.1:{server.server_port}",
                "Sec-Fetch-Site": "same-origin",
                "X-PodcastClip-Token": server.api_token,
            },
        )
        response = connection.getresponse()
        cancelling = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert cancelling["status"] == "cancelling"
        continue_to_checkpoint.set()
        _wait_for_status(server.jobs, created["id"], "cancelled")
    finally:
        continue_to_checkpoint.set()
        connection.close()
        server.shutdown()
        server.server_close()
        server.jobs.close()
        server_thread.join(timeout=1)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "::1", "localhost"])
def test_loopback_hosts_are_allowed(host: str) -> None:
    assert _is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com", ""])
def test_non_loopback_hosts_are_rejected(host: str) -> None:
    assert _is_loopback_host(host) is False


def test_web_server_requires_explicit_remote_opt_in(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="non-loopback"):
        serve_web(host="0.0.0.0", port=8765, output_dir=tmp_path)


def test_request_with_non_loopback_host_is_rejected(tmp_path: Path) -> None:
    server = PodcastClipServer(("127.0.0.1", 0), output_dir=tmp_path)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/api/health", headers={"Host": "attacker.example"})
        response = connection.getresponse()
        response.read()
        assert response.status == 421
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server.jobs.close()
        server_thread.join(timeout=1)


def test_cross_site_job_creation_is_rejected(tmp_path: Path) -> None:
    server = PodcastClipServer(("127.0.0.1", 0), output_dir=tmp_path)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(
            "POST",
            "/api/jobs",
            body='{"url":"https://youtu.be/cross01"}',
            headers={
                "Content-Type": "text/plain",
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 403
        assert server.jobs.list_jobs() == []
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server.jobs.close()
        server_thread.join(timeout=1)


def test_job_creation_requires_json_and_api_token(tmp_path: Path) -> None:
    server = PodcastClipServer(("127.0.0.1", 0), output_dir=tmp_path)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.dumps({"url": "https://youtu.be/token01", "duration": "standard"})
        connection.request(
            "POST",
            "/api/jobs",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": origin,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        missing_token = connection.getresponse()
        missing_token.read()
        assert missing_token.status == 403

        headers = _state_change_headers(server)
        headers["Content-Type"] = "text/plain"
        connection.request("POST", "/api/jobs", body=body, headers=headers)
        wrong_type = connection.getresponse()
        wrong_type.read()
        assert wrong_type.status == 415
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server.jobs.close()
        server_thread.join(timeout=1)


def test_dashboard_injects_session_token(tmp_path: Path) -> None:
    server = PodcastClipServer(("127.0.0.1", 0), output_dir=tmp_path)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        content = response.read().decode("utf-8")
        assert response.status == 200
        assert server.api_token in content
        assert "__PODCASTCLIP_API_TOKEN__" not in content
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server.jobs.close()
        server_thread.join(timeout=1)


def test_job_manager_limits_active_queue(monkeypatch, tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake_pipeline(**_kwargs: object) -> None:
        started.set()
        release.wait(timeout=1)

    monkeypatch.setattr("podcastclip.web.run_youtube_to_audio", fake_pipeline)
    manager = JobManager(output_dir=tmp_path, max_active_jobs=1)
    try:
        manager.create_job(url="https://youtu.be/first01", duration="standard", target_language="zh")
        assert started.wait(timeout=1)
        with pytest.raises(JobQueueFullError, match="queue is full"):
            manager.create_job(url="https://youtu.be/second2", duration="standard", target_language="zh")
    finally:
        release.set()
        manager.close()

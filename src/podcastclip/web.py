from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import load_settings
from .options import DURATION_PRESETS, language_label, normalize_language_code
from .pipeline import PipelineCancelledError, PipelineResult, run_youtube_to_audio
from .youtube import validate_youtube_url


WEB_ROOT = Path(__file__).resolve().parent / "web_assets"
API_TOKEN_PLACEHOLDER = "__PODCASTCLIP_API_TOKEN__"
DEFAULT_MAX_ACTIVE_JOBS = 5
DEFAULT_MAX_JOB_HISTORY = 100
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
}


class JobQueueFullError(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    url: str
    duration: str
    target_language: str
    status: str = "queued"
    title: str = "待处理的 YouTube 视频"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    future: Future[None] | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "duration": self.duration,
            "target_language": self.target_language,
            "status": self.status,
            "title": self.title,
            "logs": self.logs[-12:],
            "result": self.result,
            "error": self.error,
            "can_cancel": self.status in {"queued", "running"},
        }


class JobManager:
    def __init__(
        self,
        *,
        output_dir: Path,
        max_active_jobs: int = DEFAULT_MAX_ACTIVE_JOBS,
        max_history: int = DEFAULT_MAX_JOB_HISTORY,
    ) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_active_jobs = max_active_jobs
        self.max_history = max(max_history, max_active_jobs)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="podcastclip")

    def create_job(self, *, url: str, duration: str, target_language: str) -> Job:
        with self._lock:
            active_jobs = sum(
                job.status in {"queued", "running", "cancelling"}
                for job in self._jobs.values()
            )
            if active_jobs >= self.max_active_jobs:
                raise JobQueueFullError(
                    f"The processing queue is full ({self.max_active_jobs} active jobs)."
                )
            self._prune_history_locked()
            job = Job(
                id=uuid.uuid4().hex[:12],
                url=url,
                duration=duration,
                target_language=target_language,
            )
            self._jobs[job.id] = job
        future = self._executor.submit(self._run_job, job.id)
        with self._lock:
            job.future = future
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.as_dict() for job in reversed(jobs)]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.as_dict() if job else None

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in {"completed", "failed", "cancelled"}:
                return job.as_dict()

            job.cancel_event.set()
            if job.future is not None and job.future.cancel():
                job.status = "cancelled"
                job.logs.append("任务已取消")
            else:
                job.status = "cancelling"
                job.logs.append("正在取消任务")
            return job.as_dict()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_job(self, job_id: str) -> None:
        job = self._get_job_object(job_id)
        if job is None:
            return
        if job.cancel_event.is_set():
            self._update(job_id, status="cancelled", logs=["任务已取消"])
            return
        self._update(job_id, status="running", logs=["开始处理任务"])
        try:
            settings = load_settings()
            result = run_youtube_to_audio(
                url=job.url,
                settings=settings,
                output_dir=self.output_dir,
                minutes=None,
                target_language=job.target_language,
                duration_preset=job.duration,
                progress=lambda message: self._log(job_id, message),
                cancel_requested=job.cancel_event.is_set,
            )
            result_data = self._result_data(result)
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                if current.cancel_event.is_set():
                    current.status = "cancelled"
                    current.logs.append("任务已取消")
                else:
                    current.status = "completed"
                    current.title = result.title
                    current.result = result_data
                    current.logs.append("任务完成")
        except PipelineCancelledError:
            self._update(job_id, status="cancelled", error=None, logs=["任务已取消"])
        except Exception as exc:  # Keep errors visible in the local dashboard.
            if job.cancel_event.is_set():
                self._update(job_id, status="cancelled", error=None, logs=["任务已取消"])
            else:
                self._update(job_id, status="failed", error=str(exc), logs=[f"任务失败：{exc}"])

    def _result_data(self, result: PipelineResult) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {
            "audio_url": self._result_url(result, result.audio_path),
            "script_url": self._result_url(result, result.script_path),
            "transcript_url": self._result_url(result, result.transcript_path),
            "overview_url": self._result_url(result, result.overview_path),
            "metadata_url": self._result_url(result, result.metadata_path),
            "feed_url": self._result_url(result, result.feed_path) if result.feed_path else None,
            "duration_seconds": metadata.get("duration_seconds"),
        }

    def _result_url(self, result: PipelineResult, path: Path) -> str:
        relative = path.resolve().relative_to(self.output_dir.resolve()).as_posix()
        return result.remote_urls.get(relative) or self._media_url(path)

    def _media_url(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.output_dir.resolve())
        return "/media/" + "/".join(relative.parts)

    def _get_job_object(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            logs = changes.pop("logs", [])
            for key, value in changes.items():
                setattr(job, key, value)
            job.logs.extend(logs)

    def _log(self, job_id: str, message: str) -> None:
        self._update(job_id, logs=[message])

    def _prune_history_locked(self) -> None:
        terminal = {"completed", "failed", "cancelled"}
        while len(self._jobs) >= self.max_history:
            oldest_terminal = next(
                (job_id for job_id, job in self._jobs.items() if job.status in terminal),
                None,
            )
            if oldest_terminal is None:
                raise JobQueueFullError("The job history is full with active jobs.")
            del self._jobs[oldest_terminal]


class PodcastClipServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], *, output_dir: Path) -> None:
        super().__init__(address, PodcastClipRequestHandler)
        self.output_dir = output_dir.resolve()
        self.jobs = JobManager(output_dir=self.output_dir)
        self.api_token = secrets.token_urlsafe(32)


class PodcastClipRequestHandler(BaseHTTPRequestHandler):
    server: PodcastClipServer

    def do_GET(self) -> None:
        if not self._guard_request_host():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return
        if parsed.path == "/api/jobs":
            self._send_json({"jobs": self.server.jobs.list_jobs()})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = self.server.jobs.get_job(job_id)
            if job is None:
                self._send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
            else:
                self._send_json(job)
            return
        if parsed.path.startswith("/media/"):
            self._serve_media(parsed.path.removeprefix("/media/"))
            return
        filename = STATIC_FILES.get(parsed.path)
        if filename:
            self._serve_static(filename)
            return
        self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._guard_request_host() or not self._guard_state_change(require_json=True):
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/jobs":
            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            url = str(payload.get("url", "")).strip()
            duration = str(payload.get("duration", "standard")).strip()
            target_language = normalize_language_code(str(payload.get("target_language", "zh")))
            validate_youtube_url(url)
            if duration not in DURATION_PRESETS:
                raise ValueError("Invalid duration preset.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            job = self.server.jobs.create_job(
                url=url,
                duration=duration,
                target_language=target_language,
            )
        except JobQueueFullError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
            return
        self._send_json(job.as_dict(), status=HTTPStatus.CREATED)

    def do_DELETE(self) -> None:
        if not self._guard_request_host() or not self._guard_state_change():
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/jobs/"):
            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
            return
        job_id = parsed.path.rsplit("/", 1)[-1]
        job = self.server.jobs.cancel_job(job_id)
        if job is None:
            self._send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_json(job)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request body.") from exc
        if length <= 0 or length > 32_000:
            raise ValueError("Request body is empty or too large.")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _serve_static(self, filename: str) -> None:
        path = (WEB_ROOT / filename).resolve()
        if not path.exists() or WEB_ROOT.resolve() not in path.parents:
            self._send_json({"error": "Static file not found."}, status=HTTPStatus.NOT_FOUND)
            return
        if filename == "index.html":
            content = path.read_text(encoding="utf-8").replace(
                API_TOKEN_PLACEHOLDER,
                self.server.api_token,
            )
            self._send_content(content.encode("utf-8"), _content_type_for_path(path))
            return
        self._send_file(path)

    def _serve_media(self, relative_path: str) -> None:
        candidate = (self.server.output_dir / unquote(relative_path)).resolve()
        if self.server.output_dir not in candidate.parents or not candidate.is_file():
            self._send_json({"error": "Media file not found."}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_file(candidate)

    def _send_file(self, path: Path) -> None:
        self._send_content(path.read_bytes(), _content_type_for_path(path))

    def _send_content(self, content: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _guard_request_host(self) -> bool:
        authority = _parse_authority(self.headers.get("Host", ""))
        if authority is None:
            self._send_json(
                {"error": "Request Host must be a loopback address on this server port."},
                status=HTTPStatus.MISDIRECTED_REQUEST,
            )
            return False
        hostname, port = authority
        if not _is_loopback_host(hostname) or port != self.server.server_port:
            self._send_json(
                {"error": "Request Host must be a loopback address on this server port."},
                status=HTTPStatus.MISDIRECTED_REQUEST,
            )
            return False
        return True

    def _guard_state_change(self, *, require_json: bool = False) -> bool:
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site != "same-origin":
            self._send_json({"error": "Cross-site requests are not allowed."}, status=HTTPStatus.FORBIDDEN)
            return False

        origin = self.headers.get("Origin")
        if origin and not _origin_matches_authority(origin, self.headers.get("Host", "")):
            self._send_json({"error": "Cross-origin requests are not allowed."}, status=HTTPStatus.FORBIDDEN)
            return False

        token = self.headers.get("X-PodcastClip-Token", "")
        if not hmac.compare_digest(token, self.server.api_token):
            self._send_json({"error": "Invalid local API token."}, status=HTTPStatus.FORBIDDEN)
            return False

        if require_json:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._send_json(
                    {"error": "Content-Type must be application/json."},
                    status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
                return False
        return True

    def _send_json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def serve_web(*, host: str, port: int, output_dir: Path) -> None:
    if not _is_loopback_host(host):
        raise RuntimeError(
            "Refusing to bind the local web server to a non-loopback host."
        )
    server = PodcastClipServer((host, port), output_dir=output_dir)
    print(f"PodcastClip web: http://{host}:{port}")
    print(f"Output directory: {output_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping PodcastClip web server.")
    finally:
        server.jobs.close()
        server.server_close()


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _parse_authority(value: str) -> tuple[str, int] | None:
    try:
        parsed = urlparse(f"//{value}")
        if not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        return None
    return parsed.hostname.lower(), port


def _origin_matches_authority(origin: str, authority: str) -> bool:
    target = _parse_authority(authority)
    try:
        parsed = urlparse(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if target is None or parsed.scheme != "http" or not parsed.hostname or origin_port is None:
        return False
    return (parsed.hostname.lower(), origin_port) == target


def _content_type_for_path(path: Path) -> str:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
        "application/xml",
    }:
        return f"{content_type}; charset=utf-8"
    return content_type

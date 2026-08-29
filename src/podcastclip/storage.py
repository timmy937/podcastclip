from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .config import Settings


class R2Storage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        prefix: str,
        public_base_url: str,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._public_base_url = public_base_url.rstrip("/")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("R2 storage requires boto3. Install project dependencies first.") from exc
            client = boto3.client(
                service_name="s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name=region,
            )
        self.client = client

    @property
    def public_base_url(self) -> str:
        return _join_url(self._public_base_url, self.prefix)

    def publish_files(
        self,
        output_dir: Path,
        relative_paths: Iterable[Path],
        *,
        before_upload: Callable[[Path], None] | None = None,
    ) -> dict[str, str]:
        output_root = output_dir.resolve()
        paths: list[Path] = []
        for relative in relative_paths:
            path = (output_root / relative).resolve()
            if output_root not in path.parents or not path.is_file():
                raise ValueError(f"Publish file is outside output or missing: {relative}")
            paths.append(path)
        return self._publish_paths(output_root, paths, before_upload=before_upload)

    def delete_files(self, output_dir: Path, relative_paths: Iterable[Path]) -> None:
        output_root = output_dir.resolve()
        for relative in relative_paths:
            path = (output_root / relative).resolve()
            if path == output_root or output_root not in path.parents:
                raise ValueError(f"Delete file is outside output: {relative}")
            normalized = path.relative_to(output_root)
            self.client.delete_object(
                Bucket=self.bucket,
                Key=self._object_key(normalized),
            )

    def _publish_paths(
        self,
        output_dir: Path,
        paths: Iterable[Path],
        *,
        before_upload: Callable[[Path], None] | None,
    ) -> dict[str, str]:
        urls: dict[str, str] = {}
        for path in paths:
            if before_upload:
                before_upload(path)
            relative = path.relative_to(output_dir)
            key = self._object_key(relative)
            self.client.upload_file(
                str(path),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": _content_type(path),
                    "CacheControl": _cache_control(relative),
                },
            )
            urls[relative.as_posix()] = self._object_url(relative)
        return urls

    def _object_key(self, relative: Path) -> str:
        return "/".join(part for part in (self.prefix, relative.as_posix()) if part)

    def _object_url(self, relative: Path) -> str:
        return _join_url(self.public_base_url, quote(relative.as_posix(), safe="/"))


def create_storage(settings: Settings) -> R2Storage | None:
    if settings.storage_backend == "local":
        return None
    return R2Storage(
        endpoint_url=settings.r2_endpoint_url,
        bucket=settings.r2_bucket,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        region=settings.r2_region,
        prefix=settings.r2_prefix,
        public_base_url=settings.r2_public_base_url,
    )


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _cache_control(relative: Path) -> str:
    if relative.name == "feed.xml":
        return "public, max-age=300"
    return "public, max-age=31536000, immutable"


def _join_url(base: str, suffix: str) -> str:
    return "/".join(part.strip("/") for part in (base, suffix) if part.strip("/"))

from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

from dotenv import load_dotenv


DEFAULT_STEPFUN_BASE_URL = "https://api.stepfun.com/step_plan/v1"
DEFAULT_SUPADATA_BASE_URL = "https://api.supadata.ai/v1"
DEFAULT_CHAT_MODEL = "step-3.5-flash-2603"
DEFAULT_TTS_MODEL = "stepaudio-2.5-tts"
DEFAULT_ASR_MODEL = "stepaudio-2.5-asr"
DEFAULT_TTS_VOICE = "zixinnansheng"
DEFAULT_R2_REGION = "auto"


@dataclass(frozen=True)
class Settings:
    chat_api_key: str
    chat_base_url: str
    audio_api_key: str
    audio_base_url: str
    chat_model: str
    tts_model: str
    asr_model: str
    tts_voice: str
    supadata_api_key: str
    supadata_base_url: str
    rss_include_source_url: bool
    storage_backend: str
    r2_endpoint_url: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_region: str
    r2_prefix: str
    r2_public_base_url: str


def load_settings() -> Settings:
    load_dotenv()

    legacy_api_key = os.getenv("STEPFUN_API_KEY", "").strip()
    legacy_base_url = os.getenv("STEPFUN_BASE_URL", DEFAULT_STEPFUN_BASE_URL).strip()
    chat_api_key = os.getenv("STEPFUN_CHAT_API_KEY", legacy_api_key).strip()
    if not chat_api_key:
        raise RuntimeError("STEPFUN_CHAT_API_KEY is missing. Put it in .env or export it.")

    # The StepFun plan endpoint uses the same authenticated account for chat,
    # ASR, and TTS unless an explicit audio override is configured.
    chat_base_url = _validated_base_url(
        "STEPFUN_CHAT_BASE_URL",
        os.getenv("STEPFUN_CHAT_BASE_URL", legacy_base_url).strip(),
    )
    audio_api_key = (os.getenv("STEPFUN_AUDIO_API_KEY") or chat_api_key).strip()
    audio_base_url = _validated_base_url(
        "STEPFUN_AUDIO_BASE_URL",
        (os.getenv("STEPFUN_AUDIO_BASE_URL") or chat_base_url).strip(),
    )
    supadata_base_url = _validated_base_url(
        "SUPADATA_BASE_URL",
        os.getenv("SUPADATA_BASE_URL", DEFAULT_SUPADATA_BASE_URL).strip(),
    )
    storage_backend = os.getenv("PODCASTCLIP_STORAGE_BACKEND", "local").strip().lower()
    if storage_backend not in {"local", "r2"}:
        raise RuntimeError("PODCASTCLIP_STORAGE_BACKEND must be local or r2.")

    r2_endpoint_url = os.getenv("R2_ENDPOINT_URL", "").strip().rstrip("/")
    r2_bucket = os.getenv("R2_BUCKET", "").strip()
    r2_access_key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    r2_secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    r2_region = os.getenv("R2_REGION", DEFAULT_R2_REGION).strip() or DEFAULT_R2_REGION
    r2_prefix = os.getenv("R2_PREFIX", "podcastclip").strip().strip("/")
    r2_public_base_url = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if storage_backend == "r2":
        missing = [
            name
            for name, value in {
                "R2_ENDPOINT_URL": r2_endpoint_url,
                "R2_BUCKET": r2_bucket,
                "R2_ACCESS_KEY_ID": r2_access_key_id,
                "R2_SECRET_ACCESS_KEY": r2_secret_access_key,
                "R2_PUBLIC_BASE_URL": r2_public_base_url,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"R2 storage is enabled but missing: {', '.join(missing)}")
        r2_endpoint_url = _validated_base_url("R2_ENDPOINT_URL", r2_endpoint_url)
        r2_public_base_url = _validated_base_url("R2_PUBLIC_BASE_URL", r2_public_base_url)

    return Settings(
        chat_api_key=chat_api_key,
        chat_base_url=chat_base_url,
        audio_api_key=audio_api_key,
        audio_base_url=audio_base_url,
        chat_model=os.getenv("STEPFUN_CHAT_MODEL", DEFAULT_CHAT_MODEL).strip(),
        tts_model=os.getenv("STEPFUN_TTS_MODEL", DEFAULT_TTS_MODEL).strip(),
        asr_model=os.getenv("STEPFUN_ASR_MODEL", DEFAULT_ASR_MODEL).strip(),
        tts_voice=os.getenv("STEPFUN_TTS_VOICE", DEFAULT_TTS_VOICE).strip(),
        supadata_api_key=os.getenv("SUPADATA_API_KEY", "").strip(),
        supadata_base_url=supadata_base_url,
        rss_include_source_url=_env_bool("PODCASTCLIP_RSS_INCLUDE_SOURCE_URL", default=True),
        storage_backend=storage_backend,
        r2_endpoint_url=r2_endpoint_url,
        r2_bucket=r2_bucket,
        r2_access_key_id=r2_access_key_id,
        r2_secret_access_key=r2_secret_access_key,
        r2_region=r2_region,
        r2_prefix=r2_prefix,
        r2_public_base_url=r2_public_base_url,
    )


def _validated_base_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    try:
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid service URL.") from exc
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f"{name} must be a valid service URL.")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{name} must not include a query string or fragment.")
    if parsed.scheme == "https":
        return value.rstrip("/")
    if parsed.scheme == "http" and _is_loopback_hostname(hostname):
        return value.rstrip("/")
    raise RuntimeError(f"{name} must use HTTPS unless it targets a loopback host.")


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")

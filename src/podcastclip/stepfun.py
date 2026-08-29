from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Iterable

import httpx


class StepFunError(RuntimeError):
    pass


class StepFunClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=timeout,
            # The local macOS NO_PROXY value may contain IPv6 CIDR entries
            # that older httpx releases parse as an invalid URL port.
            trust_env=False,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "podcastclip/0.1.0",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "StepFunClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 8192,
        reasoning_effort: str | None = "low",
        retry_truncated: bool = True,
    ) -> str:
        request_max_tokens = max_tokens
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": request_max_tokens,
        }
        if model == "step-3.5-flash-2603" and reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        while True:
            response = self.client.post(self._url("/chat/completions"), json=payload)
            self._raise_for_error(response)
            data = response.json()
            try:
                choice = data["choices"][0]
                message = choice["message"]
                content = _message_content(message).strip()
                reasoning_chars = _reasoning_content_length(message)
                if choice.get("finish_reason") == "length":
                    if content and not retry_truncated:
                        return content
                    if retry_truncated and request_max_tokens < 16000:
                        request_max_tokens = min(request_max_tokens * 2, 16000)
                        payload["max_tokens"] = request_max_tokens
                        continue
                    raise StepFunError(
                        "Chat response was truncated: "
                        f"content_chars={len(content)}, reasoning_chars={reasoning_chars}."
                    )
                if content:
                    return content
                raise StepFunError(
                    f"Chat response content was empty: reasoning_chars={reasoning_chars}."
                )
            except (KeyError, IndexError, TypeError) as exc:
                raise StepFunError(f"Unexpected chat response: {data}") from exc

    def transcribe_audio_sse(
        self,
        *,
        audio_path: Path,
        model: str,
        language: str | None = None,
    ) -> str:
        audio_format = audio_path.suffix.lstrip(".").lower() or "mp3"
        transcription: dict[str, object] = {
            "model": model,
            "enable_itn": True,
        }
        if language:
            transcription["language"] = language

        payload = {
            "audio": {
                "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                "input": {
                    "transcription": transcription,
                    "format": {"type": audio_format},
                },
            }
        }

        chunks: list[str] = []
        done_text: str | None = None
        with self.client.stream(
            "POST",
            self._url("/audio/asr/sse"),
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as response:
            self._raise_for_error(response)
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                if not raw_line.startswith("data:"):
                    continue
                raw_data = raw_line.removeprefix("data:").strip()
                if raw_data == "[DONE]":
                    break
                try:
                    event = json.loads(raw_data)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type") or event.get("event")
                text = _extract_text(event)
                if event_type and "error" in str(event_type).lower():
                    raise StepFunError(str(event))
                if event_type and "done" in str(event_type).lower() and text:
                    done_text = text
                elif text:
                    chunks.append(text)

        transcript = done_text or "".join(_dedupe_consecutive(chunks))
        if not transcript.strip():
            raise StepFunError("ASR completed without transcript text.")
        return transcript.strip()

    def synthesize_speech(
        self,
        *,
        model: str,
        voice: str,
        text: str,
        output_path: Path,
        speed: float = 1.0,
        response_format: str = "mp3",
        instruction: str | None = None,
    ) -> Path:
        payload: dict[str, object] = {
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": response_format,
            "speed": speed,
        }
        if instruction:
            payload["instruction"] = instruction

        response = self.client.post(self._url("/audio/speech"), json=payload)
        self._raise_for_error(response)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return output_path

    def _raise_for_error(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:2000]
            raise StepFunError(f"StepFun API error {response.status_code}: {body}") from exc


def _extract_text(event: dict[str, object]) -> str:
    for key in ("text", "delta", "content"):
        value = event.get(key)
        if isinstance(value, str):
            return value

    data = event.get("data")
    if isinstance(data, dict):
        for key in ("text", "delta", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value

    return ""


def _message_content(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _reasoning_content_length(message: object) -> int:
    if not isinstance(message, dict):
        return 0
    lengths = [
        len(value)
        for key in ("reasoning", "reasoning_content")
        if isinstance((value := message.get(key)), str)
    ]
    return max(lengths, default=0)


def _dedupe_consecutive(items: Iterable[str]) -> Iterable[str]:
    previous = ""
    for item in items:
        if item == previous:
            continue
        previous = item
        yield item

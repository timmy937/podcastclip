from __future__ import annotations

from dataclasses import dataclass

import pytest

from podcastclip.stepfun import StepFunClient, StepFunError


@dataclass
class FakeResponse:
    payload: dict[str, object]
    status_code: int = 200
    text: str = ""

    def json(self) -> dict[str, object]:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, object]] = []

    def post(self, _url: str, *, json: dict[str, object]) -> FakeResponse:
        self.payloads.append(dict(json))
        return self.responses.pop(0)


def _chat_response(content: str, finish_reason: str, *, reasoning: str = "") -> FakeResponse:
    return FakeResponse(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": content, "reasoning": reasoning},
                }
            ]
        }
    )


def test_chat_completion_retries_nonempty_truncated_response() -> None:
    client = StepFunClient.__new__(StepFunClient)
    client.base_url = "https://example.test/v1"
    http = FakeHttpClient(
        [
            _chat_response("半截内容，", "length"),
            _chat_response("完整内容。", "stop"),
        ]
    )
    client.client = http

    result = client.chat_completion(
        model="step-3.5-flash-2603",
        messages=[{"role": "user", "content": "test"}],
        max_tokens=100,
    )

    assert result == "完整内容。"
    assert [payload["max_tokens"] for payload in http.payloads] == [100, 200]
    assert all(payload["reasoning_effort"] == "low" for payload in http.payloads)
    assert all("thinking" not in payload for payload in http.payloads)


def test_chat_completion_returns_nonempty_truncated_content_when_fail_fast() -> None:
    client = StepFunClient.__new__(StepFunClient)
    client.base_url = "https://example.test/v1"
    http = FakeHttpClient([_chat_response("半截内容，", "length")])
    client.client = http

    result = client.chat_completion(
        model="step-3.5-flash-2603",
        messages=[{"role": "user", "content": "test"}],
        max_tokens=300,
        retry_truncated=False,
    )

    assert result == "半截内容，"
    assert [payload["max_tokens"] for payload in http.payloads] == [300]


def test_chat_completion_fail_fast_reports_reasoning_only_truncation() -> None:
    client = StepFunClient.__new__(StepFunClient)
    client.base_url = "https://example.test/v1"
    http = FakeHttpClient([_chat_response("", "length", reasoning="内部推理")])
    client.client = http

    with pytest.raises(StepFunError, match="content_chars=0, reasoning_chars=4"):
        client.chat_completion(
            model="step-3.5-flash-2603",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=300,
            retry_truncated=False,
        )


def test_client_does_not_parse_invalid_local_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1,127.0.0.0/8,::1/128")
    client = StepFunClient("test-key", "https://example.test/v1")
    client.close()

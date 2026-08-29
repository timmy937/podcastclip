from datetime import date
from pathlib import Path
import time

from podcastclip.pipeline import (
    INTRO_SOURCE_MAX_CHARS,
    episode_description,
    episode_guid,
    episode_audio_filename,
    extract_overview_for_chunk,
    extract_structured_overview,
    generate_episode_intro,
    intro_length_budget,
    partition_overview_for_script,
    render_intro_source,
    script_segment_count,
    summarize_to_running_script,
    translate_title_to_chinese,
    _parallel_map_ordered,
)
from podcastclip.stepfun import StepFunError
from podcastclip.web import _content_type_for_path


class FakeClient:
    def chat_completion(self, **_kwargs: object) -> str:
        return """说明文字
```json
{
  "chapters": [{"start": "0:12", "title": "开场", "summary": "介绍主题"}],
  "key_points": ["明确观点"],
  "key_quotes": [{"start": "0:12", "quote": "短引文"}],
  "speaker_notes": [{"speaker": "说话者不明确", "claim": "明确表达"}],
  "guests": ["测试嘉宾"],
}
        ```"""


class IntroClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def chat_completion(self, *, messages: list[dict[str, str]], **_kwargs: object) -> str:
        self.prompts.append(messages[-1]["content"])
        return self.response


class SequenceClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.prompts: list[str] = []
        self.calls: list[dict[str, object]] = []

    def chat_completion(self, *, messages: list[dict[str, str]], **kwargs: object) -> str:
        self.prompts.append(messages[-1]["content"])
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TruncatedIntroClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def chat_completion(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        raise StepFunError("Chat response was truncated.")


def test_structured_overview_accepts_loose_model_json() -> None:
    overview = extract_overview_for_chunk(
        client=FakeClient(),
        chunk="[0:12] 原始转录",
        title="测试视频",
        index=1,
        total=1,
        model="step-3.5-flash-2603",
    )

    assert overview["chapters"] == [{"start": "0:12", "title": "开场", "summary": "介绍主题"}]
    assert overview["key_points"] == ["明确观点"]
    assert overview["key_quotes"] == [{"start": "0:12", "quote": "短引文"}]
    assert overview["guests"] == ["测试嘉宾"]


def test_parallel_map_preserves_input_order() -> None:
    def worker(index: int, value: int) -> int:
        time.sleep((2 - index) * 0.01)
        return value * 10

    assert _parallel_map_ordered([1, 2, 3], max_workers=3, worker=worker) == [10, 20, 30]


def test_structured_overview_merges_parallel_chunks_in_input_order(monkeypatch) -> None:
    import podcastclip.pipeline as pipeline

    def fake_extract(**kwargs: object) -> dict[str, object]:
        index = int(kwargs["index"])
        time.sleep((4 - index) * 0.01)
        return {
            "chapters": [{"start": str(index), "title": f"章节 {index}", "summary": "摘要"}],
            "key_points": [f"观点 {index}"],
            "key_quotes": [],
            "speaker_notes": [],
            "guests": [],
        }

    monkeypatch.setattr(pipeline, "extract_overview_for_chunk", fake_extract)
    transcript = "\n\n".join("原始转录 " + ("内容" * 5000) for _ in range(4))

    overview = extract_structured_overview(
        client=FakeClient(),
        transcript=transcript,
        title="测试视频",
        model="step-3.5-flash-2603",
    )

    assert [item["title"] for item in overview["chapters"]] == ["章节 1", "章节 2", "章节 3", "章节 4"]


def test_intro_budget_is_ten_to_fifteen_seconds() -> None:
    assert intro_length_budget("中文") == (55, 82)
    assert intro_length_budget("English") == (25, 37)


def test_episode_intro_requires_guest_before_topic_when_explicit() -> None:
    client = IntroClient("本期我们邀请到嘉宾，聊聊人工智能研究者的成长路径。")

    intro = generate_episode_intro(
        client=client,
        title="研究者的成长路径",
        target_language="中文",
        model="step-3.5-flash-2603",
        grounded_overview={"guests": ["Gabriel"]},
    )

    assert intro.startswith("本期")
    assert "Gabriel" in client.prompts[0]
    assert "必须先自然介绍嘉宾" in client.prompts[0]


def test_episode_intro_without_guest_does_not_invent_one() -> None:
    client = IntroClient("")

    intro = generate_episode_intro(
        client=client,
        title="人工智能的未来",
        target_language="中文",
        model="step-3.5-flash-2603",
        grounded_overview={"guests": []},
    )

    assert intro == "本期我们先来了解《人工智能的未来》的主题和重点。"
    assert "不要编造或暗示嘉宾" in client.prompts[0]


def test_intro_source_is_bounded_and_samples_the_full_overview() -> None:
    overview = {
        "guests": ["Peter Steinberger"],
        "chapters": [
            {
                "start": str(index),
                "title": f"章节 {index}",
                "summary": "章节摘要" * 40,
            }
            for index in range(50)
        ],
        "key_points": [f"观点 {index} " + ("事实" * 40) for index in range(100)],
        "key_quotes": [{"start": "1:00", "quote": "不应进入开场证据"}],
        "speaker_notes": [{"speaker": "主持人", "claim": "不应进入开场证据"}],
    }

    source = render_intro_source(overview=overview, grounded_notes=None)

    assert len(source) <= INTRO_SOURCE_MAX_CHARS
    assert "Peter Steinberger" in source
    assert "章节 0" in source
    assert "章节 49" in source
    assert "观点 0" in source
    assert "观点 99" in source
    assert "不应进入开场证据" not in source


def test_episode_intro_uses_fallback_when_chat_is_truncated() -> None:
    client = TruncatedIntroClient()
    logs: list[str] = []

    intro = generate_episode_intro(
        client=client,
        title="OpenClaw: The Viral AI Agent",
        target_language="中文",
        model="step-3.5-flash-2603",
        grounded_overview={"guests": ["Peter Steinberger"], "key_points": ["事实"]},
        progress=logs.append,
    )

    assert intro == "本期我们邀请到Peter Steinberger，一起聊聊《OpenClaw: The Viral AI Agent》的主题。"
    assert client.calls[0]["retry_truncated"] is False
    assert any("fallback" in message.lower() for message in logs)


def test_long_duration_segment_counts() -> None:
    assert script_segment_count(2) == 1
    assert script_segment_count(5) == 1
    assert script_segment_count(8) == 2
    assert script_segment_count(15) == 3


def test_overview_partition_preserves_order_and_all_guests() -> None:
    overview = {
        "chapters": [{"start": str(index), "title": f"章节 {index}", "summary": "摘要"} for index in range(9)],
        "key_points": [f"观点 {index}" for index in range(9)],
        "key_quotes": [{"start": str(index), "quote": f"原话 {index}"} for index in range(9)],
        "speaker_notes": [{"speaker": "嘉宾", "claim": f"说法 {index}"} for index in range(9)],
        "guests": ["测试嘉宾"],
    }

    sections = partition_overview_for_script(overview, count=3)

    assert [[item["title"] for item in section["chapters"]] for section in sections] == [
        ["章节 0", "章节 1", "章节 2"],
        ["章节 3", "章节 4", "章节 5"],
        ["章节 6", "章节 7", "章节 8"],
    ]
    assert [section["key_points"] for section in sections] == [
        ["观点 0", "观点 1", "观点 2"],
        ["观点 3", "观点 4", "观点 5"],
        ["观点 6", "观点 7", "观点 8"],
    ]
    assert all(section["guests"] == ["测试嘉宾"] for section in sections)


def test_fifteen_minute_script_generates_three_sections_and_retries_only_failed_section() -> None:
    intro = "开" * 60 + "。"
    first_section = "甲" * 1600 + "。"
    second_section = "乙" * 1600 + "。"
    third_section = "丙" * 1600 + "。"
    client = SequenceClient(
        [
            intro,
            first_section,
            StepFunError("Chat response was truncated."),
            second_section,
            third_section,
        ]
    )
    logs: list[str] = []
    overview = {
        "chapters": [{"start": str(index), "title": f"章节 {index}", "summary": "摘要"} for index in range(9)],
        "key_points": [f"观点 {index}" for index in range(9)],
        "key_quotes": [],
        "speaker_notes": [{"speaker": "嘉宾", "claim": f"说法 {index}"} for index in range(9)],
        "guests": ["测试嘉宾"],
    }

    result = summarize_to_running_script(
        client=client,
        transcript="转录",
        title="长访谈",
        target_language="中文",
        minutes=15,
        model="step-3.5-flash-2603",
        grounded_overview=overview,
        grounded_notes="不会用于分段正文",
        progress=logs.append,
    )

    assert result.endswith(third_section)
    assert len(client.prompts) == 5
    assert "第 1/3 段" in client.prompts[1]
    assert "第 2/3 段" in client.prompts[2]
    assert client.prompts[2] == client.prompts[3]
    assert "第 3/3 段" in client.prompts[4]
    assert client.calls[1]["max_tokens"] == 8000
    assert client.calls[2]["max_tokens"] == 8000
    assert client.calls[3]["max_tokens"] == 16000
    assert any("retrying" in message.lower() and "2/3" in message for message in logs)


def test_title_translation_only_translates_source_title() -> None:
    client = IntroClient("从高中辍学到加入 OpenAI 的研究员对话")

    translated = translate_title_to_chinese(
        client=client,
        title="From High School Dropout to OpenAI Researcher",
        model="step-3.5-flash-2603",
    )

    assert translated == "从高中辍学到加入 OpenAI 的研究员对话"
    assert "只翻译原标题" in client.prompts[0]


def test_episode_description_is_only_the_translated_title() -> None:
    assert episode_description("  黑帽美国2026：OpenAI与Hugging Face事件  ") == "黑帽美国2026：OpenAI与Hugging Face事件"


def test_episode_guid_is_stable_without_exposing_youtube_id() -> None:
    guid = episode_guid("abc123XYZ_0")

    assert guid == episode_guid("abc123XYZ_0")
    assert guid.startswith("urn:podcastclip:")
    assert "abc123XYZ_0" not in guid


def test_short_script_uses_second_expansion_attempt() -> None:
    intro = "开" * 60 + "。"
    first_script = "短" * 500 + "。"
    first_expansion = "仍" * 600 + "。"
    second_expansion = "足" * 650 + "。"
    client = SequenceClient([intro, first_script, first_expansion, second_expansion])

    result = summarize_to_running_script(
        client=client,
        transcript="转录",
        title="测试节目",
        target_language="中文",
        minutes=2,
        model="step-3.5-flash-2603",
        grounded_overview={"guests": [], "key_points": ["事实"]},
        grounded_notes="事实",
    )

    assert result.endswith(second_expansion)
    assert len(client.prompts) == 4


def test_near_target_complete_script_is_accepted_after_retries() -> None:
    intro = "开" * 60 + "。"
    first_script = "短" * 500 + "。"
    first_expansion = "较" * 590 + "。"
    second_expansion = "近" * 610 + "。"
    logs: list[str] = []
    client = SequenceClient([intro, first_script, first_expansion, second_expansion])

    result = summarize_to_running_script(
        client=client,
        transcript="转录",
        title="测试节目",
        target_language="中文",
        minutes=2,
        model="step-3.5-flash-2603",
        grounded_overview={"guests": [], "key_points": ["事实"]},
        grounded_notes="事实",
        progress=logs.append,
    )

    assert result.endswith(second_expansion)
    assert any("near-target" in message for message in logs)


def test_episode_audio_filename_keeps_chinese_title() -> None:
    filename = episode_audio_filename(
        "From High School Dropout to OpenAI Researcher",
        chinese_title="从高中辍学到加入 OpenAI 的研究员对话",
        generated_date=date(2026, 8, 19),
    )

    assert filename == "2026-08-19-From High School Dropout to OpenAI Researcher - 从高中辍学到加入 OpenAI 的研究员对话.mp3"


def test_episode_audio_filename_sanitizes_filesystem_characters() -> None:
    filename = episode_audio_filename(
        "Title: A/B? C",
        chinese_title="中文: A/B? C",
        generated_date=date(2026, 8, 19),
    )

    assert filename == "2026-08-19-Title-A-B-C - 中文-A-B-C.mp3"


def test_web_text_files_declare_utf8_without_changing_audio_type() -> None:
    assert _content_type_for_path(Path("script.txt")) == "text/plain; charset=utf-8"
    assert _content_type_for_path(Path("audio.mp3")) == "audio/mpeg"

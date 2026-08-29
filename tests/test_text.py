from podcastclip.text import (
    format_timestamped_segments,
    parse_loose_json,
    parse_vtt,
    parse_vtt_cues,
    segment_caption_cues,
    split_for_tts,
)


def test_parse_vtt_removes_timestamps_tags_and_duplicates() -> None:
    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
<c>Hello</c>

00:00:02.000 --> 00:00:03.000
Hello world

00:00:03.000 --> 00:00:04.000
Hello world
"""

    assert parse_vtt(vtt) == "Hello world"


def test_split_for_tts_respects_limit() -> None:
    chunks = split_for_tts("第一句。第二句。第三句。", max_chars=5)

    assert chunks
    assert all(len(chunk) <= 5 for chunk in chunks)


def test_timestamped_cues_dedupe_rolling_captions_without_merging_adjacent_cues() -> None:
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
你好

00:00:01.500 --> 00:00:03.500
你好，今天我们讨论字幕。

00:00:04.000 --> 00:00:06.000
第二句话。
"""

    cues = parse_vtt_cues(vtt)
    assert [cue.text for cue in cues] == ["你好", "，今天我们讨论字幕。", "第二句话。"]
    segments = segment_caption_cues(cues, min_chars=1, max_chars=15)
    assert format_timestamped_segments(segments).startswith("[0:00] 你好，今天我们讨论字幕。")
    assert "[0:04] 第二句话。" in format_timestamped_segments(segments)
    assert parse_vtt(vtt) == "你好，今天我们讨论字幕。 第二句话。"


def test_parse_loose_json_accepts_fence_prefix_and_trailing_commas() -> None:
    parsed = parse_loose_json('前置说明\n```json\n{"key_points": ["事实",],}\n```')

    assert parsed == {"key_points": ["事实"]}

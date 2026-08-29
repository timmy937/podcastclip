from __future__ import annotations

import hashlib
import json
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar, cast

from .audio import probe_duration_seconds, stitch_mp3
from .config import Settings
from .options import language_label, resolve_duration_minutes
from .rss import SHOW_ARTWORK_RELATIVE, rebuild_feed
from .stepfun import StepFunClient, StepFunError
from .storage import create_storage
from .text import chunk_source_text, normalize_for_tts, parse_loose_json, split_for_tts
from .supadata import SupadataClient, SupadataError
from .youtube import (
    DEFAULT_CAPTION_LANGS,
    VideoInfo,
    canonical_youtube_url,
    download_audio_for_asr,
    fetch_video_info,
    get_caption_transcript,
)


ProgressCallback = Callable[[str], None]
CancelRequested = Callable[[], bool]
OVERVIEW_SCHEMA_VERSION = 2
OVERVIEW_PROMPT_VERSION = "overview-v2-guests-timestamped-captions"
SCRIPT_PROMPT_VERSION = "script-v4-segmented-long"
INTRO_MIN_SECONDS = 10
INTRO_TARGET_SECONDS = 12
INTRO_MAX_SECONDS = 15
INTRO_SOURCE_MAX_CHARS = 4000
INTRO_CHAPTER_SAMPLE_LIMIT = 10
INTRO_KEY_POINT_SAMPLE_LIMIT = 6
SCRIPT_MIN_RATIO = 0.90
SCRIPT_MAX_RATIO = 1.10
SCRIPT_FALLBACK_RATIO = 0.80
SCRIPT_EXPANSION_ATTEMPTS = 2
SCRIPT_SECTION_GENERATION_ATTEMPTS = 2
SCRIPT_SECTION_MAX_MINUTES = 5
SCRIPT_SECTION_CHAPTER_LIMIT = 12
SCRIPT_SECTION_KEY_POINT_LIMIT = 18
SCRIPT_SECTION_QUOTE_LIMIT = 6
SCRIPT_SECTION_SPEAKER_LIMIT = 12
OVERVIEW_WORKERS = 3
TTS_WORKERS = 2

Item = TypeVar("Item")
Result = TypeVar("Result")


class PipelineCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelineResult:
    title: str
    transcript_path: Path
    overview_path: Path
    script_path: Path
    audio_path: Path
    metadata_path: Path
    feed_path: Path | None
    remote_urls: dict[str, str] = field(default_factory=dict)


def run_youtube_to_audio(
    *,
    url: str,
    settings: Settings,
    output_dir: Path,
    minutes: int | None,
    target_language: str,
    duration_preset: str | None = None,
    caption_langs: list[str] | None = None,
    asr_fallback: bool = True,
    asr_language: str | None = None,
    feed_base_url: str | None = None,
    tts_speed: float = 1.0,
    cookies_from_browser: str | None = None,
    cookies: Path | None = None,
    js_runtime: str | None = None,
    remote_components: str | None = None,
    progress: ProgressCallback | None = None,
    cancel_requested: CancelRequested | None = None,
) -> PipelineResult:
    url = canonical_youtube_url(url)
    check_cancelled(cancel_requested)
    minutes = resolve_duration_minutes(preset=duration_preset, minutes=minutes)
    target_language = language_label(target_language)
    output_dir.mkdir(parents=True, exist_ok=True)
    text_dir = output_dir / "text"
    episodes_dir = output_dir / "episodes"
    text_dir.mkdir(parents=True, exist_ok=True)
    episodes_dir.mkdir(parents=True, exist_ok=True)
    storage = create_storage(settings)

    with tempfile.TemporaryDirectory(prefix="podcastclip-") as tmp:
        work_dir = Path(tmp)

        with ExitStack() as clients:
            chat_client = clients.enter_context(StepFunClient(settings.chat_api_key, settings.chat_base_url))
            audio_client = chat_client
            if (settings.audio_api_key, settings.audio_base_url) != (
                settings.chat_api_key,
                settings.chat_base_url,
            ):
                audio_client = clients.enter_context(
                    StepFunClient(settings.audio_api_key, settings.audio_base_url)
                )
            transcript: str | None = None
            transcript_source = ""
            video_info = VideoInfo(
                video_id=_video_id_from_url(url),
                title="YouTube video",
                webpage_url=url,
            )

            if settings.supadata_api_key:
                check_cancelled(cancel_requested)
                report(progress, "Fetching Supadata native transcript")
                try:
                    supadata_client = SupadataClient(
                        settings.supadata_api_key,
                        settings.supadata_base_url,
                    )
                    supadata_transcript = supadata_client.get_transcript(url=url, language="zh", mode="native")
                    check_cancelled(cancel_requested)
                    transcript = supadata_transcript.timestamped_transcript or supadata_transcript.transcript
                    transcript_source = f"Supadata: {supadata_transcript.language or 'unknown'}"
                    try:
                        check_cancelled(cancel_requested)
                        metadata = supadata_client.get_metadata(url=url)
                        check_cancelled(cancel_requested)
                        title = str(metadata.get("title") or metadata.get("name") or "YouTube video")
                        if title != "YouTube video":
                            video_info = VideoInfo(
                                video_id=video_info.video_id,
                                title=title,
                                webpage_url=url,
                            )
                    except SupadataError as exc:
                        report(progress, f"Supadata metadata unavailable; using URL title: {exc}")
                    report(
                        progress,
                        f"Using Supadata transcript: language={supadata_transcript.language or 'unknown'}, chars={len(transcript)}",
                    )
                except SupadataError as exc:
                    report(progress, f"Supadata failed; falling back to YouTube: {exc}")

            if transcript is None:
                check_cancelled(cancel_requested)
                report(progress, "Fetching YouTube metadata")
                video_info = fetch_video_info(
                    url,
                    cookies_from_browser=cookies_from_browser,
                    cookies=cookies,
                    js_runtime=js_runtime,
                    remote_components=remote_components,
                )
                check_cancelled(cancel_requested)
                report(progress, "Fetching YouTube captions")
                caption = get_caption_transcript(
                    url,
                    work_dir=work_dir / "captions",
                    caption_langs=caption_langs or DEFAULT_CAPTION_LANGS,
                    cookies_from_browser=cookies_from_browser,
                    cookies=cookies,
                    js_runtime=js_runtime,
                    remote_components=remote_components,
                )
                check_cancelled(cancel_requested)
                if caption:
                    transcript = caption.timestamped_transcript or caption.transcript
                    transcript_source = f"YouTube caption: {caption.language or caption.source}"
                    video_info = caption.video
                    report(progress, f"Using captions: language={caption.language or 'unknown'}, chars={len(transcript)}")
                elif asr_fallback:
                    check_cancelled(cancel_requested)
                    report(progress, "No captions found; downloading audio for ASR")
                    audio_for_asr = download_audio_for_asr(
                        url,
                        work_dir=work_dir / "audio",
                        cookies_from_browser=cookies_from_browser,
                        cookies=cookies,
                        js_runtime=js_runtime,
                        remote_components=remote_components,
                    )
                    check_cancelled(cancel_requested)
                    report(progress, "Running audio transcription")
                    transcript = audio_client.transcribe_audio_sse(
                        audio_path=audio_for_asr,
                        model=settings.asr_model,
                        language=asr_language,
                    )
                    check_cancelled(cancel_requested)
                    transcript_source = "StepFun ASR"
                    report(progress, f"ASR transcript ready: chars={len(transcript)}")
                else:
                    raise RuntimeError("No YouTube captions found, and ASR fallback is disabled.")

            check_cancelled(cancel_requested)
            slug = _episode_slug(video_info.title, video_info.video_id)

            transcript_path = text_dir / f"{slug}-transcript.txt"
            transcript_path.write_text(transcript, encoding="utf-8")
            report(progress, f"Wrote transcript: {transcript_path}")

            overview_path = text_dir / f"{slug}-overview.json"
            cache_key = _overview_cache_key(transcript, settings.chat_model)
            overview = load_cached_overview(overview_path, cache_key=cache_key)
            if overview is not None:
                report(progress, f"Using cached overview: {overview_path}")
            else:
                report(progress, "Extracting structured overview")
                overview = extract_structured_overview(
                    client=chat_client,
                    transcript=transcript,
                    title=video_info.title,
                    model=settings.chat_model,
                    progress=progress,
                    cancel_requested=cancel_requested,
                )
                check_cancelled(cancel_requested)
                overview_path.write_text(
                    json.dumps(
                        {
                            "schema_version": OVERVIEW_SCHEMA_VERSION,
                            "prompt_version": OVERVIEW_PROMPT_VERSION,
                            "model": settings.chat_model,
                            "source_sha256": cache_key,
                            "video_title": video_info.title,
                            "overview": overview,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                report(progress, f"Wrote overview: {overview_path}")

            notes_path = text_dir / f"{slug}-notes.txt"
            grounded_notes = render_overview(overview)
            notes_path.write_text(grounded_notes, encoding="utf-8")

            script_path = text_dir / f"{slug}-script.txt"
            script_cache_key = _script_cache_key(
                overview_cache_key=cache_key,
                model=settings.chat_model,
                target_language=target_language,
                minutes=minutes,
            )
            script = load_cached_script(script_path, cache_key=script_cache_key)
            if script is not None:
                report(progress, f"Using cached script: {script_path}")
            else:
                report(progress, "Writing short running script")
                check_cancelled(cancel_requested)
                script = summarize_to_running_script(
                    client=chat_client,
                    transcript=transcript,
                    title=video_info.title,
                    target_language=target_language,
                    minutes=minutes,
                    model=settings.chat_model,
                    grounded_overview=overview,
                    grounded_notes=grounded_notes,
                    progress=progress,
                    cancel_requested=cancel_requested,
                )
                check_cancelled(cancel_requested)
                script_path.write_text(script, encoding="utf-8")
                script_path.with_suffix(".meta.json").write_text(
                    json.dumps(
                        {"prompt_version": SCRIPT_PROMPT_VERSION, "cache_key": script_cache_key},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                report(progress, f"Wrote script: chars={len(script)}, path={script_path}")

            check_cancelled(cancel_requested)
            translated_title = translate_title_to_chinese(
                client=chat_client,
                title=video_info.title,
                model=settings.chat_model,
                progress=progress,
            )
            check_cancelled(cancel_requested)

            tts_chunks = split_for_tts(script)
            report(progress, f"Generating TTS chunks: count={len(tts_chunks)}, workers={TTS_WORKERS}")

            def synthesize_chunk(index: int, chunk: str) -> Path:
                check_cancelled(cancel_requested)
                report(progress, f"Generating TTS chunk {index + 1}/{len(tts_chunks)}: chars={len(chunk)}")
                chunk_path = work_dir / "tts" / f"{slug}-{index + 1:02d}.mp3"
                # Use one client per request so parallel TTS does not share a
                # mutable response stream or connection state.
                with StepFunClient(settings.audio_api_key, settings.audio_base_url) as tts_client:
                    tts_client.synthesize_speech(
                        model=settings.tts_model,
                        voice=settings.tts_voice,
                        text=chunk,
                        output_path=chunk_path,
                        speed=tts_speed,
                        instruction="用清晰、自然、适合跑步时收听的播客语气朗读。节奏稳定，不夸张。",
                    )
                check_cancelled(cancel_requested)
                report(progress, f"Generated TTS chunk {index + 1}/{len(tts_chunks)}")
                return chunk_path

            chunk_paths = _parallel_map_ordered(
                tts_chunks,
                max_workers=TTS_WORKERS,
                worker=synthesize_chunk,
            )
            check_cancelled(cancel_requested)

            generated_at = datetime.now(timezone.utc)
            audio_path = episodes_dir / episode_audio_filename(
                video_info.title,
                chinese_title=translated_title,
                generated_date=generated_at.astimezone().date(),
            )
            staged_audio_path = work_dir / "final" / audio_path.name
            report(progress, "Stitching MP3")
            stitch_mp3(chunk_paths, staged_audio_path)
            check_cancelled(cancel_requested)
            staged_audio_path.replace(audio_path)

        check_cancelled(cancel_requested)
        duration = probe_duration_seconds(audio_path)
        metadata = {
            "title": f"AI Brief: {video_info.title}",
            "source_title": video_info.title,
            "translated_title": translated_title,
            "description": episode_description(translated_title),
            "source_url": url,
            "audio_file": str(audio_path.relative_to(output_dir)),
            "transcript_file": str(transcript_path.relative_to(output_dir)),
            "overview_file": str(overview_path.relative_to(output_dir)),
            "notes_file": str(notes_path.relative_to(output_dir)),
            "script_file": str(script_path.relative_to(output_dir)),
            "duration_seconds": duration,
            "guid": episode_guid(video_info.video_id),
            "published_at": generated_at.isoformat(),
        }
        metadata_path = episodes_dir / f"{slug}.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        report(progress, f"Wrote metadata: {metadata_path}")

    feed_path = None
    effective_feed_base_url = feed_base_url or (storage.public_base_url if storage else None)
    if effective_feed_base_url:
        check_cancelled(cancel_requested)
        report(progress, "Rebuilding RSS feed")
        feed_path = rebuild_feed(
            output_dir=output_dir,
            feed_base_url=effective_feed_base_url,
            include_source_url=settings.rss_include_source_url,
        )

    remote_urls: dict[str, str] = {}
    if storage:
        check_cancelled(cancel_requested)
        report(progress, "Uploading output to R2")
        remote_urls = storage.publish_files(
            output_dir,
            [
                audio_path.relative_to(output_dir),
                SHOW_ARTWORK_RELATIVE,
                feed_path.relative_to(output_dir),
            ],
            before_upload=lambda _path: check_cancelled(cancel_requested),
        )
        check_cancelled(cancel_requested)
        report(progress, f"Uploaded files to R2: count={len(remote_urls)}")

    return PipelineResult(
        title=video_info.title,
        transcript_path=transcript_path,
        overview_path=overview_path,
        script_path=script_path,
        audio_path=audio_path,
        metadata_path=metadata_path,
        feed_path=feed_path,
        remote_urls=remote_urls,
    )


def transcribe_youtube_audio(
    *,
    url: str,
    settings: Settings,
    output_dir: Path,
    cookies_from_browser: str | None = None,
    cookies: Path | None = None,
    js_runtime: str | None = None,
    remote_components: str | None = None,
    asr_language: str | None = None,
    start_seconds: int | None = None,
    duration_seconds: int | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[Path, Path]:
    url = canonical_youtube_url(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / "audio"
    text_dir = output_dir / "text"
    audio_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="podcastclip-transcribe-") as tmp:
        work_dir = Path(tmp)
        report(progress, "Fetching YouTube metadata")
        video_info = fetch_video_info(
            url,
            cookies_from_browser=cookies_from_browser,
            cookies=cookies,
            js_runtime=js_runtime,
            remote_components=remote_components,
        )
        slug = _episode_slug(video_info.title, video_info.video_id)
        report(progress, "Downloading audio for ASR")
        audio_for_asr = download_audio_for_asr(
            url,
            work_dir=work_dir / "audio",
            cookies_from_browser=cookies_from_browser,
            cookies=cookies,
            js_runtime=js_runtime,
            remote_components=remote_components,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
        )
        saved_audio = audio_dir / f"{slug}.asr.mp3"
        saved_audio.write_bytes(audio_for_asr.read_bytes())
        report(progress, f"Wrote ASR audio sample: {saved_audio}")

        with StepFunClient(settings.audio_api_key, settings.audio_base_url) as audio_client:
            report(progress, "Running audio transcription")
            transcript = audio_client.transcribe_audio_sse(
                audio_path=saved_audio,
                model=settings.asr_model,
                language=asr_language,
            )

    suffix = "sample" if duration_seconds else "full"
    transcript_path = text_dir / f"{slug}-{suffix}-asr-transcript.txt"
    transcript_path.write_text(transcript, encoding="utf-8")
    report(progress, f"Wrote ASR transcript: chars={len(transcript)}, path={transcript_path}")
    return saved_audio, transcript_path


def summarize_to_running_script(
    *,
    client: StepFunClient,
    transcript: str,
    title: str,
    target_language: str,
    minutes: int,
    model: str,
    grounded_overview: dict[str, object] | None = None,
    grounded_notes: str | None = None,
    progress: ProgressCallback | None = None,
    cancel_requested: CancelRequested | None = None,
) -> str:
    check_cancelled(cancel_requested)
    source = grounded_notes
    if grounded_overview is not None:
        source = render_overview(grounded_overview)
    if not source:
        source = extract_grounded_notes(client=client, transcript=transcript, title=title, model=model)
    intro = generate_episode_intro(
        client=client,
        title=title,
        target_language=target_language,
        model=model,
        grounded_overview=grounded_overview,
        grounded_notes=source,
        progress=progress,
    )
    check_cancelled(cancel_requested)
    segment_count = script_segment_count(minutes)
    if segment_count > 1 and grounded_overview is not None:
        script = generate_segmented_running_script(
            client=client,
            overview=grounded_overview,
            title=title,
            target_language=target_language,
            minutes=minutes,
            model=model,
            segment_count=segment_count,
            progress=progress,
            cancel_requested=cancel_requested,
        )
        return f"{intro}\n\n{script}".strip()

    min_length, max_length = script_length_budget(minutes, target_language)
    length_unit = script_length_unit(target_language)
    prompt = f"""
把下面的转录内容整理成一段适合跑步时听的 {target_language} 音频播客脚本。

要求：
1. 时长约 {minutes} 分钟，脚本长度控制在 {min_length} 到 {max_length} 个{length_unit}之间。
2. 只能使用“事实笔记”中明确出现的信息，不要补全面试、录用、平台、作品集、论文、项目细节等笔记里没有的桥段。
3. 严格区分 Gabriel、主持人、说话者不明确的信息。不要把主持人的第一人称经历写成 Gabriel 的经历。
4. 如果笔记没有明确说话者，就写“访谈中有人提到”，不要写“他”或“这位嘉宾”。
5. 开场介绍已经单独生成，主体不要重复介绍嘉宾或重复开场，直接讲 3 到 5 个核心观点，最后用“三个可以带走的点”收尾。
6. 如果某个过程没有细节，就明确说“原视频没有展开”，不要为了故事顺滑而编细节。
7. 只输出可直接朗读的正文，不要 Markdown，不要项目符号，不要标题。
8. 不要添加节目名、音效、脚步声、主持串场、括号舞台指示或夸张称呼。
9. 避免括号，因为部分 TTS 会把括号当成朗读指令。
10. 不要说“核心成员”“传奇”“科幻片”等笔记没有明确支持的评价性说法。

视频标题：{title}

事实笔记：
{source}
""".strip()

    check_cancelled(cancel_requested)
    script = client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是事实约束很强的音频编辑。你不发挥，不鸡汤，不编造，不加节目包装，也不混淆说话者。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max(1800, max_length * 2),
    )
    check_cancelled(cancel_requested)
    script = normalize_for_tts(script)
    current_length = script_length(script, target_language)
    for attempt in range(1, SCRIPT_EXPANSION_ATTEMPTS + 1):
        if current_length >= min_length:
            break
        deficit = min_length - current_length
        buffered_min = min(max_length, min_length + max(60, deficit // 2))
        report(
            progress,
            f"Script is short; expansion {attempt}/{SCRIPT_EXPANSION_ATTEMPTS}: "
            f"length={current_length}, min={min_length}, target={buffered_min}",
        )
        check_cancelled(cancel_requested)
        expanded_script = normalize_for_tts(
            expand_short_script(
                client=client,
                script=script,
                notes=source,
                target_language=target_language,
                current_length=current_length,
                min_chars=buffered_min,
                max_chars=max_length,
                model=model,
            )
        )
        check_cancelled(cancel_requested)
        expanded_length = script_length(expanded_script, target_language)
        if expanded_length > current_length and _ends_with_sentence_punctuation(expanded_script):
            script = expanded_script
            current_length = expanded_length
    if not script.strip():
        raise RuntimeError("Script generation returned empty text.")
    current_length = script_length(script, target_language)
    if current_length > max_length:
        check_cancelled(cancel_requested)
        report(progress, f"Script is long; shortening: length={current_length}, max={max_length}")
        script = shorten_script_to_budget(
            client=client,
            script=script,
            min_chars=min_length,
            max_chars=max_length,
            target_language=target_language,
            model=model,
        )
        check_cancelled(cancel_requested)
        script = normalize_for_tts(script)
    if script_length(script, target_language) > max_length:
        report(progress, f"Model shortening missed budget; trimming deterministically: length={script_length(script, target_language)}, max={max_length}")
        script = trim_script_to_budget(
            script,
            max_chars=max_length,
            target_language=target_language,
        )
    current_length = script_length(script, target_language)
    accepted_min_length = min_length
    if current_length < min_length:
        fallback_min = int(script_target_length(minutes, target_language) * SCRIPT_FALLBACK_RATIO)
        if current_length < fallback_min:
            raise RuntimeError(
                f"Generated script is too short after expansion: length={current_length}, min={min_length}."
            )
        accepted_min_length = fallback_min
        report(
            progress,
            f"Script remains near-target after retries; continuing: "
            f"length={current_length}, preferred_min={min_length}, fallback_min={fallback_min}",
        )
    if not _ends_with_sentence_punctuation(script):
        check_cancelled(cancel_requested)
        report(progress, "Script ended without sentence punctuation; repairing before TTS")
        script = repair_incomplete_script(
            client=client,
            script=script,
            notes=source,
            target_language=target_language,
            min_chars=min_length,
            max_chars=max_length,
            model=model,
        )
        check_cancelled(cancel_requested)
        script = normalize_for_tts(script)
        if script_length(script, target_language) > max_length:
            script = trim_script_to_budget(script, max_chars=max_length, target_language=target_language)
        if script_length(script, target_language) < accepted_min_length:
            raise RuntimeError(
                f"Repaired script is too short: length={script_length(script, target_language)}, "
                f"min={accepted_min_length}."
            )
        if not _ends_with_sentence_punctuation(script):
            raise RuntimeError("Generated script still ends without complete sentence punctuation.")
    return f"{intro}\n\n{script.strip()}".strip()


def generate_segmented_running_script(
    *,
    client: StepFunClient,
    overview: dict[str, object],
    title: str,
    target_language: str,
    minutes: int,
    model: str,
    segment_count: int,
    progress: ProgressCallback | None = None,
    cancel_requested: CancelRequested | None = None,
) -> str:
    sections = partition_overview_for_script(overview, count=segment_count)
    section_targets = _distribute_budget(script_target_length(minutes, target_language), segment_count)
    scripts: list[str] = []

    for index, (section, target_length) in enumerate(zip(sections, section_targets, strict=True), start=1):
        check_cancelled(cancel_requested)
        source = render_script_section_source(section)
        min_length = int(target_length * SCRIPT_MIN_RATIO)
        max_length = int(target_length * SCRIPT_MAX_RATIO)
        prompt = _script_section_prompt(
            title=title,
            source=source,
            target_language=target_language,
            index=index,
            count=segment_count,
            min_length=min_length,
            max_length=max_length,
        )
        report(
            progress,
            f"Writing script section {index}/{segment_count}: source_chars={len(source)}, "
            f"target={min_length}-{max_length}",
        )

        section_script = ""
        base_max_tokens = max(8000, max_length * 2)
        for attempt in range(1, SCRIPT_SECTION_GENERATION_ATTEMPTS + 1):
            check_cancelled(cancel_requested)
            try:
                section_script = normalize_for_tts(
                    client.chat_completion(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": "你是事实约束很强的音频编辑。只写当前分段，不发挥、不编造、不重复开场。",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.2,
                        max_tokens=min(base_max_tokens * (2 ** (attempt - 1)), 16000),
                        retry_truncated=False,
                    )
                )
                break
            except StepFunError:
                if attempt == SCRIPT_SECTION_GENERATION_ATTEMPTS:
                    raise
                report(
                    progress,
                    f"Script section {index}/{segment_count} failed; retrying "
                    f"{attempt + 1}/{SCRIPT_SECTION_GENERATION_ATTEMPTS}",
                )

        section_script = _fit_script_section(
            client=client,
            script=section_script,
            notes=source,
            target_language=target_language,
            target_length=target_length,
            min_length=min_length,
            max_length=max_length,
            model=model,
            index=index,
            count=segment_count,
            progress=progress,
            cancel_requested=cancel_requested,
        )
        scripts.append(section_script)
        report(
            progress,
            f"Completed script section {index}/{segment_count}: "
            f"length={script_length(section_script, target_language)}",
        )

    combined = "\n\n".join(scripts).strip()
    global_min, global_max = script_length_budget(minutes, target_language)
    if script_length(combined, target_language) > global_max:
        combined = trim_script_to_budget(combined, max_chars=global_max, target_language=target_language)
    combined_length = script_length(combined, target_language)
    if combined_length < global_min:
        fallback_min = int(script_target_length(minutes, target_language) * SCRIPT_FALLBACK_RATIO)
        if combined_length < fallback_min:
            raise RuntimeError(
                f"Generated segmented script is too short: length={combined_length}, min={global_min}."
            )
        report(
            progress,
            f"Segmented script remains near-target: length={combined_length}, "
            f"preferred_min={global_min}, fallback_min={fallback_min}",
        )
    if not _ends_with_sentence_punctuation(combined):
        raise RuntimeError("Generated segmented script ends without complete sentence punctuation.")
    return combined


def _fit_script_section(
    *,
    client: StepFunClient,
    script: str,
    notes: str,
    target_language: str,
    target_length: int,
    min_length: int,
    max_length: int,
    model: str,
    index: int,
    count: int,
    progress: ProgressCallback | None,
    cancel_requested: CancelRequested | None,
) -> str:
    if not script.strip():
        raise RuntimeError(f"Script section {index}/{count} returned empty text.")

    current_length = script_length(script, target_language)
    for attempt in range(1, SCRIPT_EXPANSION_ATTEMPTS + 1):
        if current_length >= min_length:
            break
        deficit = min_length - current_length
        buffered_min = min(max_length, min_length + max(60, deficit // 2))
        report(
            progress,
            f"Script section {index}/{count} is short; expansion "
            f"{attempt}/{SCRIPT_EXPANSION_ATTEMPTS}: length={current_length}, target={buffered_min}",
        )
        check_cancelled(cancel_requested)
        try:
            expanded = normalize_for_tts(
                expand_short_script(
                    client=client,
                    script=script,
                    notes=notes,
                    target_language=target_language,
                    current_length=current_length,
                    min_chars=buffered_min,
                    max_chars=max_length,
                    model=model,
                    retry_truncated=False,
                    max_tokens_override=min(8000 * (2 ** (attempt - 1)), 16000),
                )
            )
        except StepFunError:
            report(progress, f"Script section {index}/{count} expansion failed")
            continue
        expanded_length = script_length(expanded, target_language)
        if expanded_length > current_length and _ends_with_sentence_punctuation(expanded):
            script = expanded
            current_length = expanded_length

    if script_length(script, target_language) > max_length:
        script = trim_script_to_budget(script, max_chars=max_length, target_language=target_language)

    if not _ends_with_sentence_punctuation(script):
        complete_script = _drop_incomplete_trailing_sentence(script)
        if complete_script:
            script = complete_script

    current_length = script_length(script, target_language)
    fallback_min = int(target_length * SCRIPT_FALLBACK_RATIO)
    if current_length < fallback_min:
        raise RuntimeError(
            f"Generated script section {index}/{count} is too short: "
            f"length={current_length}, min={min_length}."
        )
    if not _ends_with_sentence_punctuation(script):
        raise RuntimeError(f"Generated script section {index}/{count} ends without complete sentence punctuation.")
    return script


def _script_section_prompt(
    *,
    title: str,
    source: str,
    target_language: str,
    index: int,
    count: int,
    min_length: int,
    max_length: int,
) -> str:
    ending_rule = (
        "这是最后一段，最后必须用‘三个可以带走的点’自然收尾。"
        if index == count
        else "这不是最后一段，不要总结全篇，也不要提前写‘三个可以带走的点’。"
    )
    return f"""
请把下面的事实笔记写成完整播客正文的第 {index}/{count} 段，输出语言为 {target_language}。

要求：
1. 本段长度控制在 {min_length} 到 {max_length} 个{script_length_unit(target_language)}之间。
2. 只能使用本段事实笔记，不补因果、故事、身份或外部知识。
3. 严格保持说话者归属；说话者不明确时写“访谈中有人提到”。
4. 开场和嘉宾介绍已单独生成，本段直接进入内容，不要重复开场。
5. {ending_rule}
6. 只输出可直接朗读的正文，不要标题、Markdown、项目符号、括号或舞台指示。
7. 必须以完整句子结束，不要截断。

视频标题：{title}

本段事实笔记：
{source}
""".strip()


def generate_episode_intro(
    *,
    client: StepFunClient,
    title: str,
    target_language: str,
    model: str,
    grounded_overview: dict[str, object] | None = None,
    grounded_notes: str | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    min_length, max_length = intro_length_budget(target_language)
    source = render_intro_source(overview=grounded_overview, grounded_notes=grounded_notes)
    guests = _overview_guests(grounded_overview)
    guest_rule = (
        "转录明确介绍了以下嘉宾，必须先自然介绍嘉宾，再用一句话概括整个播客主题：\n"
        + "、".join(guests)
        if guests
        else "转录没有明确介绍嘉宾，不要编造或暗示嘉宾；先用一句话概括整个播客主题。"
    )
    prompt = f"""
请为视频《{title}》写一段独立的 {target_language} 播客开场白。

{guest_rule}

要求：
1. 这是主体脚本之前的开场，不计入主体的 {target_language} 时长预算。
2. 目标朗读时长约 {INTRO_TARGET_SECONDS} 秒，长度控制在 {min_length} 到 {max_length} 个{script_length_unit(target_language)}之间，对应约 {INTRO_MIN_SECONDS} 到 {INTRO_MAX_SECONDS} 秒。
3. 只能使用事实笔记和视频标题中明确出现的信息，不要使用外部知识。
4. 只输出可直接朗读的正文，不要 Markdown、项目符号、标题、括号、音效或舞台指示。
5. 如果是英文输出，使用自然的 English；如果是中文输出，使用自然的中文。

视频标题：{title}

事实笔记：
{source}
    """.strip()
    report(progress, "Writing standalone episode intro")
    try:
        intro = normalize_for_tts(
            client.chat_completion(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是严格基于来源材料写开场的音频编辑。只写明确事实，不编造嘉宾、身份或主题。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=max(300, max_length * 2),
                retry_truncated=False,
            )
        )
    except StepFunError:
        report(progress, "Intro generation failed; using deterministic fallback")
        return _fallback_episode_intro(title=title, target_language=target_language, guests=guests)
    if not intro:
        intro = _fallback_episode_intro(title=title, target_language=target_language, guests=guests)
    if script_length(intro, target_language) < min_length:
        report(progress, f"Intro is short; expanding: length={script_length(intro, target_language)}, min={min_length}")
        try:
            expanded_intro = expand_episode_intro(
                client=client,
                intro=intro,
                title=title,
                source=source,
                target_language=target_language,
                min_length=min_length,
                max_length=max_length,
                guests=guests,
                model=model,
            )
        except StepFunError:
            report(progress, "Intro expansion failed; keeping deterministic fallback")
            expanded_intro = ""
        if expanded_intro.strip():
            intro = normalize_for_tts(expanded_intro)
    if script_length(intro, target_language) > max_length:
        intro = trim_script_to_budget(intro, max_chars=max_length, target_language=target_language)
    return intro


def expand_episode_intro(
    *,
    client: StepFunClient,
    intro: str,
    title: str,
    source: str,
    target_language: str,
    min_length: int,
    max_length: int,
    guests: list[str],
    model: str,
) -> str:
    guest_requirement = (
        f"必须先介绍明确嘉宾：{'、'.join(guests)}，然后概括主题。"
        if guests
        else "不要加入嘉宾，只概括主题。"
    )
    prompt = f"""
把下面的 {target_language} 播客开场扩写到 {min_length} 到 {max_length} 个{script_length_unit(target_language)}之间。

规则：
1. {guest_requirement}
2. 只能使用事实笔记和视频标题中的信息，不得新增事实。
3. 只输出可直接朗读的正文，不要解释、标题、项目符号、括号、音效或舞台指示。

视频标题：{title}
事实笔记：
{source}

当前开场：
{intro}
""".strip()
    return client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是严格控时的播客开场编辑。只扩展已有信息，不编造嘉宾或主题。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=max(300, max_length * 2),
        retry_truncated=False,
    )


def intro_length_budget(target_language: str = "中文") -> tuple[int, int]:
    """Return the independent 10-15 second intro budget."""
    units_per_second = 330 / 60 if target_language == "中文" else 150 / 60
    return int(INTRO_MIN_SECONDS * units_per_second), int(INTRO_MAX_SECONDS * units_per_second)


def render_intro_source(
    *,
    overview: dict[str, object] | None,
    grounded_notes: str | None,
) -> str:
    if not overview:
        return _limit_intro_source(grounded_notes or "")

    lines: list[str] = []
    guests = _overview_guests(overview)
    if guests:
        lines.append("明确介绍的嘉宾：" + "、".join(guests))

    chapters = overview.get("chapters", [])
    chapter_items = [item for item in chapters if isinstance(item, dict)] if isinstance(chapters, list) else []
    if chapter_items:
        lines.append("代表性章节：")
        for item in _sample_evenly(chapter_items, INTRO_CHAPTER_SAMPLE_LIMIT):
            start = str(item.get("start", "")).strip()
            title = str(item.get("title", "")).strip()
            summary = str(item.get("summary", "")).strip()
            prefix = f"[{start}] " if start else ""
            lines.append(_compact_intro_item(f"{prefix}{title}：{summary}"))

    key_points = overview.get("key_points", [])
    point_items = [str(item).strip() for item in key_points if str(item).strip()] if isinstance(key_points, list) else []
    if point_items:
        lines.append("代表性观点：")
        lines.extend(f"- {_compact_intro_item(item)}" for item in _sample_evenly(point_items, INTRO_KEY_POINT_SAMPLE_LIMIT))

    source = "\n".join(lines).strip()
    return _limit_intro_source(source or grounded_notes or "")


def script_segment_count(minutes: int) -> int:
    if minutes < 8:
        return 1
    return max(2, (minutes + SCRIPT_SECTION_MAX_MINUTES - 1) // SCRIPT_SECTION_MAX_MINUTES)


def partition_overview_for_script(
    overview: dict[str, object],
    *,
    count: int,
) -> list[dict[str, object]]:
    if count < 1:
        raise ValueError("Script section count must be at least one.")
    guests = overview.get("guests", [])
    guest_items = list(guests) if isinstance(guests, list) else []
    sections: list[dict[str, object]] = [
        {
            "chapters": [],
            "key_points": [],
            "key_quotes": [],
            "speaker_notes": [],
            "guests": guest_items.copy(),
        }
        for _ in range(count)
    ]
    for field in ("chapters", "key_points", "key_quotes", "speaker_notes"):
        values = overview.get(field, [])
        items = list(values) if isinstance(values, list) else []
        for index in range(count):
            start = index * len(items) // count
            end = (index + 1) * len(items) // count
            sections[index][field] = items[start:end]
    return sections


def render_script_section_source(overview: dict[str, object]) -> str:
    compact: dict[str, object] = {
        "chapters": [],
        "key_points": [],
        "key_quotes": [],
        "speaker_notes": [],
        "guests": [_compact_intro_item(str(item), 100) for item in _overview_guests(overview)],
    }
    chapters = overview.get("chapters", [])
    if isinstance(chapters, list):
        compact["chapters"] = [
            {
                "start": _compact_intro_item(str(item.get("start", "")), 20),
                "title": _compact_intro_item(str(item.get("title", "")), 100),
                "summary": _compact_intro_item(str(item.get("summary", "")), 220),
            }
            for item in _sample_evenly(
                [item for item in chapters if isinstance(item, dict)],
                SCRIPT_SECTION_CHAPTER_LIMIT,
            )
        ]
    key_points = overview.get("key_points", [])
    if isinstance(key_points, list):
        compact["key_points"] = [
            _compact_intro_item(str(item), 220)
            for item in _sample_evenly(key_points, SCRIPT_SECTION_KEY_POINT_LIMIT)
        ]
    key_quotes = overview.get("key_quotes", [])
    if isinstance(key_quotes, list):
        compact["key_quotes"] = [
            {
                "start": _compact_intro_item(str(item.get("start", "")), 20),
                "quote": _compact_intro_item(str(item.get("quote", "")), 180),
            }
            for item in _sample_evenly(
                [item for item in key_quotes if isinstance(item, dict)],
                SCRIPT_SECTION_QUOTE_LIMIT,
            )
        ]
    speaker_notes = overview.get("speaker_notes", [])
    if isinstance(speaker_notes, list):
        compact["speaker_notes"] = [
            {
                "speaker": _compact_intro_item(str(item.get("speaker", "说话者不明确")), 100),
                "claim": _compact_intro_item(str(item.get("claim", "")), 220),
            }
            for item in _sample_evenly(
                [item for item in speaker_notes if isinstance(item, dict)],
                SCRIPT_SECTION_SPEAKER_LIMIT,
            )
        ]
    return render_overview(compact)


def _distribute_budget(total: int, count: int) -> list[int]:
    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _sample_evenly(items: list[Item], limit: int) -> list[Item]:
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return items
    if limit == 1:
        return [items[0]]
    last_index = len(items) - 1
    return [items[index * last_index // (limit - 1)] for index in range(limit)]


def _compact_intro_item(value: str, max_chars: int = 220) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _limit_intro_source(source: str) -> str:
    source = source.strip()
    if len(source) <= INTRO_SOURCE_MAX_CHARS:
        return source
    clipped = source[:INTRO_SOURCE_MAX_CHARS]
    complete_lines = clipped.rsplit("\n", 1)[0].strip()
    return complete_lines or clipped.strip()


def _overview_guests(overview: dict[str, object] | None) -> list[str]:
    if not overview:
        return []
    guests = overview.get("guests", [])
    return [str(item).strip() for item in guests if str(item).strip()] if isinstance(guests, list) else []


def _fallback_episode_intro(*, title: str, target_language: str, guests: list[str]) -> str:
    if target_language == "English":
        if guests:
            return f"Today we are joined by {', '.join(guests)} to discuss {title}."
        return f"Today we are looking at the main theme of {title}."
    if guests:
        return f"本期我们邀请到{'、'.join(guests)}，一起聊聊《{title}》的主题。"
    return f"本期我们先来了解《{title}》的主题和重点。"


def extract_structured_overview(
    *,
    client: StepFunClient,
    transcript: str,
    title: str,
    model: str,
    progress: ProgressCallback | None = None,
    cancel_requested: CancelRequested | None = None,
) -> dict[str, object]:
    check_cancelled(cancel_requested)
    source_chunks = chunk_source_text(transcript, max_chars=12000)
    report(progress, f"Extracting overview chunks: count={len(source_chunks)}, workers={OVERVIEW_WORKERS}")

    def extract_chunk(index: int, chunk: str) -> dict[str, object]:
        check_cancelled(cancel_requested)
        report(progress, f"Extracting overview chunk {index + 1}/{len(source_chunks)}: chars={len(chunk)}")
        partial = extract_overview_for_chunk(
            client=client,
            chunk=chunk,
            title=title,
            index=index + 1,
            total=len(source_chunks),
            model=model,
        )
        check_cancelled(cancel_requested)
        report(progress, f"Extracted overview chunk {index + 1}/{len(source_chunks)}")
        return partial

    partials = _parallel_map_ordered(
        source_chunks,
        max_workers=OVERVIEW_WORKERS,
        worker=extract_chunk,
    )
    return merge_overviews(partials)


def _parallel_map_ordered(
    items: list[Item],
    *,
    max_workers: int,
    worker: Callable[[int, Item], Result],
) -> list[Result]:
    if not items:
        return []
    if len(items) == 1:
        return [worker(0, items[0])]

    results: list[Result | None] = [None] * len(items)
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="podcastclip-stage") as executor:
        futures = {
            executor.submit(worker, index, item): index
            for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [cast(Result, result) for result in results]


def extract_overview_for_chunk(
    *,
    client: StepFunClient,
    chunk: str,
    title: str,
    index: int,
    total: int,
    model: str,
) -> dict[str, object]:
    prompt = f"""
这是视频《{title}》带时间戳转录的第 {index}/{total} 段。请抽取可核查的结构化摘要。

只输出 JSON 对象，不要 Markdown，不要解释。字段必须是：
{{
  "chapters": [{{"start": "0:00", "title": "章节名", "summary": "一句话摘要"}}],
  "key_points": ["明确说到的观点或事实"],
  "key_quotes": [{{"start": "0:00", "quote": "短引文"}}],
  "speaker_notes": [{{"speaker": "说话者或不明确", "claim": "该说话者明确表达的内容"}}],
  "guests": ["转录中明确介绍的嘉宾姓名或身份"]
}}

规则：
1. 只使用转录中明确出现的信息，不使用外部知识，不补因果。
2. 时间戳只能使用对应原文段落的时间戳；无法确认就省略或写空字符串。
3. 严格区分 Gabriel、主持人和说话者不明确，不要把主持人的第一人称经历归给 Gabriel。
4. key_quotes 只放短句，不要改写成新事实。
5. guests 只填写转录中明确被介绍为嘉宾、受访者或节目来宾的姓名或身份；普通说话者、主持人和无法确认的名字不要填写。
6. 没有内容的数组返回 []。

转录：
{chunk}
""".strip()
    raw = client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是保守的事实抽取器。输出严格 JSON，不推断，不补故事。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=7000,
    )
    try:
        parsed = parse_loose_json(raw)
    except ValueError:
        # Keeping the raw answer as one point is safer than silently dropping
        # source material when a compatible gateway returns malformed JSON.
        return {"chapters": [], "key_points": [raw.strip()], "key_quotes": [], "speaker_notes": [], "guests": []}
    return normalize_overview(parsed)


def merge_overviews(partials: list[dict[str, object]]) -> dict[str, object]:
    merged: dict[str, list[object]] = {
        "chapters": [],
        "key_points": [],
        "key_quotes": [],
        "speaker_notes": [],
        "guests": [],
    }
    for partial in partials:
        for field in merged:
            values = partial.get(field, [])
            if isinstance(values, list):
                merged[field].extend(values)

    for field in ("key_points", "key_quotes", "speaker_notes", "guests"):
        merged[field] = _dedupe_json_items(merged[field])
    return merged


def normalize_overview(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Overview must be a JSON object")
    normalized: dict[str, object] = {
        "chapters": [],
        "key_points": [],
        "key_quotes": [],
        "speaker_notes": [],
        "guests": [],
    }
    chapters = value.get("chapters")
    if isinstance(chapters, list):
        normalized["chapters"] = [
            {
                "start": str(item.get("start", "")),
                "title": str(item.get("title", "")),
                "summary": str(item.get("summary", "")),
            }
            for item in chapters
            if isinstance(item, dict) and (item.get("title") or item.get("summary"))
        ]
    key_points = value.get("key_points")
    if isinstance(key_points, list):
        normalized["key_points"] = [str(item).strip() for item in key_points if str(item).strip()]
    key_quotes = value.get("key_quotes")
    if isinstance(key_quotes, list):
        normalized["key_quotes"] = [
            {"start": str(item.get("start", "")), "quote": str(item.get("quote", ""))}
            for item in key_quotes
            if isinstance(item, dict) and str(item.get("quote", "")).strip()
        ]
    speaker_notes = value.get("speaker_notes")
    if isinstance(speaker_notes, list):
        normalized["speaker_notes"] = [
            {"speaker": str(item.get("speaker", "说话者不明确")), "claim": str(item.get("claim", ""))}
            for item in speaker_notes
            if isinstance(item, dict) and str(item.get("claim", "")).strip()
        ]
    guests = value.get("guests")
    if isinstance(guests, list):
        normalized["guests"] = [str(item).strip() for item in guests if str(item).strip()]
    return normalized


def render_overview(overview: dict[str, object]) -> str:
    lines: list[str] = []
    chapters = overview.get("chapters", [])
    if isinstance(chapters, list) and chapters:
        lines.append("章节：")
        for item in chapters:
            if isinstance(item, dict):
                lines.append(f"[{item.get('start', '')}] {item.get('title', '')}：{item.get('summary', '')}")
    key_points = overview.get("key_points", [])
    if isinstance(key_points, list) and key_points:
        lines.append("核心观点：")
        lines.extend(f"- {item}" for item in key_points)
    key_quotes = overview.get("key_quotes", [])
    if isinstance(key_quotes, list) and key_quotes:
        lines.append("原话：")
        for item in key_quotes:
            if isinstance(item, dict):
                lines.append(f"[{item.get('start', '')}] {item.get('quote', '')}")
    speaker_notes = overview.get("speaker_notes", [])
    if isinstance(speaker_notes, list) and speaker_notes:
        lines.append("说话者：")
        for item in speaker_notes:
            if isinstance(item, dict):
                lines.append(f"{item.get('speaker', '说话者不明确')}：{item.get('claim', '')}")
    guests = overview.get("guests", [])
    if isinstance(guests, list) and guests:
        lines.append("明确介绍的嘉宾：")
        lines.extend(f"- {item}" for item in guests)
    return "\n".join(lines).strip()


def load_cached_overview(path: Path, *, cache_key: str) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != OVERVIEW_SCHEMA_VERSION:
        return None
    if payload.get("prompt_version") != OVERVIEW_PROMPT_VERSION:
        return None
    if payload.get("source_sha256") != cache_key:
        return None
    overview = payload.get("overview")
    return normalize_overview(overview) if isinstance(overview, dict) else None


def _overview_cache_key(transcript: str, model: str) -> str:
    source = f"{OVERVIEW_PROMPT_VERSION}\n{model}\n{transcript}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def _script_cache_key(
    *,
    overview_cache_key: str,
    model: str,
    target_language: str,
    minutes: int,
) -> str:
    source = f"{SCRIPT_PROMPT_VERSION}\n{overview_cache_key}\n{model}\n{target_language}\n{minutes}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def load_cached_script(path: Path, *, cache_key: str) -> str | None:
    metadata_path = path.with_suffix(".meta.json")
    if not path.exists() or not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        script = normalize_for_tts(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("prompt_version") != SCRIPT_PROMPT_VERSION or metadata.get("cache_key") != cache_key:
        return None
    return script or None


def _dedupe_json_items(items: list[object]) -> list[object]:
    seen: set[str] = set()
    result: list[object] = []
    for item in items:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def extract_grounded_notes(
    *,
    client: StepFunClient,
    transcript: str,
    title: str,
    model: str,
    progress: ProgressCallback | None = None,
) -> str:
    source_chunks = chunk_source_text(transcript, max_chars=5000)
    notes = []
    for index, chunk in enumerate(source_chunks, start=1):
        report(progress, f"Extracting notes chunk {index}/{len(source_chunks)}: chars={len(chunk)}")
        note = extract_notes_for_chunk(
            client=client,
            chunk=chunk,
            title=title,
            index=index,
            total=len(source_chunks),
            model=model,
        )
        if not note.strip():
            raise RuntimeError(f"Fact note extraction returned empty text for chunk {index}/{len(source_chunks)}.")
        report(progress, f"Extracted notes chunk {index}/{len(source_chunks)}: chars={len(note)}")
        notes.append(note)
    return "\n\n".join(notes)


def extract_notes_for_chunk(
    *,
    client: StepFunClient,
    chunk: str,
    title: str,
    index: int,
    total: int,
    model: str,
) -> str:
    prompt = (
        f"这是视频《{title}》转录的第 {index}/{total} 段。"
        "请输出中文事实笔记，供之后写播客脚本使用。\n\n"
        "规则：\n"
        "1. 只保留转录里明确出现的事实、观点、例子、建议。\n"
        "2. 不要补充转录没有说的因果、面试过程、录用过程、作品集、社交平台、论文或项目细节。\n"
        "3. 尽量标注说话者：Gabriel、主持人、或说话者不明确。\n"
        "4. 不要把主持人的第一人称经历归给 Gabriel；无法确认归属就标为说话者不明确。\n"
        "5. 如果一件事只被泛泛提到，就写成泛泛提到，不要扩写。\n"
        "6. 输出 8 到 14 条短笔记，每条都要具体，但不要写成朗读稿。\n\n"
        f"{chunk}"
    )
    note = client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是事实核查型编辑，只从材料抽取明示信息。不要推断，不要补故事，不要使用外部知识。",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=6000,
    )
    if note.strip():
        return note

    fallback_prompt = (
        "从下面转录中提取 8 条中文事实笔记。只写原文明确说到的内容。"
        "如果说话者不明确，就写“说话者不明确”。\n\n"
        f"{chunk[:3500]}"
    )
    return client.chat_completion(
        model=model,
        messages=[
            {"role": "system", "content": "只抽取事实，不推断。"},
            {"role": "user", "content": fallback_prompt},
        ],
        max_tokens=8000,
    )


def expand_short_script(
    *,
    client: StepFunClient,
    script: str,
    notes: str,
    target_language: str,
    current_length: int,
    min_chars: int,
    max_chars: int,
    model: str,
    retry_truncated: bool = True,
    max_tokens_override: int | None = None,
) -> str:
    prompt = f"""
下面这版 {target_language} 播客脚本太短，当前长度是 {current_length} 个{script_length_unit(target_language)}。
请在不增加事实、不补故事、不混淆说话者的前提下，把它扩写到 {min_chars} 到 {max_chars} 个{script_length_unit(target_language)}之间。

硬规则：
1. 只能使用事实笔记里的信息。
2. 严格区分 Gabriel、主持人、说话者不明确的信息。
3. 不要把主持人的经历写成 Gabriel 的经历。
4. 如果说话者不明确，就写“访谈中有人提到”。
5. 不要添加节目名、音效、脚步声、括号舞台指示或夸张评价。
6. 保持口播自然，仍然只输出可直接朗读的正文。

事实笔记：
{notes}

当前脚本：
{script}
""".strip()
    return client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是事实约束很强的音频编辑。扩写只能增加已有事实的解释和过渡，不能增加新事实。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens_override or max(1800, max_chars * 2),
        retry_truncated=retry_truncated,
    )


def shorten_script_to_budget(
    *,
    client: StepFunClient,
    script: str,
    min_chars: int,
    max_chars: int,
    target_language: str,
    model: str,
) -> str:
    prompt = f"""
把下面的 {target_language} 播客脚本压缩到 {min_chars} 到 {max_chars} 个{script_length_unit(target_language)}之间。

硬规则：
1. 只输出可直接朗读的正文。
2. 不要新增事实。
3. 不要项目符号、标题、括号、节目名、音效或舞台指示。
4. 严格保留核心事实、学习方法、职业建议和最后三个带走点。
5. 如果无法完整保留细节，优先删除例子，不要删除结论。

原脚本：
{script}
""".strip()
    return client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是严格控长的音频编辑。必须压缩，不要解释。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max(1800, max_chars * 2),
    )


def repair_incomplete_script(
    *,
    client: StepFunClient,
    script: str,
    notes: str,
    target_language: str,
    min_chars: int,
    max_chars: int,
    model: str,
) -> str:
    prompt = f"""
下面这段 {target_language} 播客脚本的结尾不完整。请在不新增事实的前提下，重写成一段完整、可直接朗读的脚本，长度控制在 {min_chars} 到 {max_chars} 个{script_length_unit(target_language)}之间。

硬规则：
1. 只使用事实笔记中的信息。
2. 保留核心事实、结论和最后的可执行要点。
3. 只输出正文，不要解释、标题、项目符号、括号或舞台指示。
4. 必须以完整句子结束，最后一个字符必须是句号、问号或感叹号。

事实笔记：
{notes}

不完整脚本：
{script}
""".strip()
    return client.chat_completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是严格事实约束的播客编辑。修复结尾，不编造事实，不保留不完整句子。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=max(1800, max_chars * 2),
    )


def script_length_budget(minutes: int, target_language: str = "中文") -> tuple[int, int]:
    target = script_target_length(minutes, target_language)
    return int(target * SCRIPT_MIN_RATIO), int(target * SCRIPT_MAX_RATIO)


def script_target_length(minutes: int, target_language: str = "中文") -> int:
    return max(700 if target_language == "中文" else 300, minutes * (330 if target_language == "中文" else 150))


def script_char_budget(minutes: int) -> tuple[int, int]:
    """Backward-compatible Chinese script budget helper."""
    return script_length_budget(minutes, "中文")


def script_length_unit(target_language: str) -> str:
    return "中文字符" if target_language == "中文" else "英文单词"


def script_length(text: str, target_language: str) -> int:
    if target_language == "中文":
        return len(text.strip())
    return len(re.findall(r"\b[\w]+(?:['-][\w]+)*\b", text))


def _ends_with_sentence_punctuation(text: str) -> bool:
    stripped = text.strip().rstrip("\"'”’」』）》)]")
    return bool(stripped) and stripped.endswith(("。", "！", "？", ".", "!", "?", "…"))


def _drop_incomplete_trailing_sentence(text: str) -> str:
    last_index = max((text.rfind(mark) for mark in ("。", "！", "？", ".", "!", "?", "…")), default=-1)
    return text[: last_index + 1].strip() if last_index >= 0 else ""


def trim_script_to_budget(script: str, *, max_chars: int, target_language: str = "中文") -> str:
    script = script.strip()
    if script_length(script, target_language) <= max_chars:
        return script

    sentences = split_sentences(script)
    if not sentences:
        return script[:max_chars].strip()

    outro = pick_outro(sentences, max_chars=max_chars)
    kept: list[str] = []
    current_len = 0
    for sentence in sentences:
        if outro and sentence in outro:
            continue
        next_len = current_len + len(sentence)
        if kept:
            next_len += 1
        candidate = " ".join([*kept, sentence, *outro]) if outro else " ".join([*kept, sentence])
        if script_length(candidate, target_language) > max_chars:
            break
        kept.append(sentence)
        current_len = next_len

    if outro:
        kept.extend(outro)
    trimmed = " ".join(kept).strip()
    if script_length(trimmed, target_language) <= max_chars:
        return trimmed
    return _trim_text_by_units(trimmed, max_chars=max_chars, target_language=target_language)


def _trim_text_by_units(text: str, *, max_chars: int, target_language: str) -> str:
    if target_language == "中文":
        return text[:max_chars].strip()
    words = re.findall(r"\b[\w]+(?:['-][\w]+)*\b", text)
    return " ".join(words[:max_chars]).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*", text.strip())
    return [part.strip() for part in parts if part.strip()]


def pick_outro(sentences: list[str], *, max_chars: int) -> list[str]:
    for index, sentence in enumerate(sentences):
        if "三个可以带走" in sentence or "三个带走" in sentence:
            outro = sentences[index : index + 4]
            return outro if len(" ".join(outro)) < max_chars * 0.45 else []
    return []


def report(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)


def check_cancelled(cancel_requested: CancelRequested | None) -> None:
    if cancel_requested and cancel_requested():
        raise PipelineCancelledError("Task cancelled by user.")


def translate_title_to_chinese(
    *,
    client: StepFunClient,
    title: str,
    model: str,
    progress: ProgressCallback | None = None,
) -> str:
    """Translate only the source title for the user-facing MP3 filename."""
    prompt = f"""
把下面的播客原标题翻译成自然、简洁的中文标题。

硬规则：
1. 只翻译原标题，不总结、不扩写、不添加原标题没有的信息。
2. 只输出中文标题本身，不要引号、前缀、解释或换行。
3. 如果原标题已经是中文，返回一个自然的中文标题。

原标题：{title}
""".strip()
    report(progress, "Translating source title to Chinese")
    try:
        translated = client.chat_completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是严格忠实的播客标题翻译器，只翻译标题，不添加信息。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=128,
        )
    except Exception as exc:
        report(progress, f"Chinese title translation failed; using fallback: {exc}")
        return "未命名中文标题"

    cleaned = _clean_title_translation(translated)
    if not cleaned:
        report(progress, "Chinese title translation was empty; using fallback")
        return "未命名中文标题"
    report(progress, f"Translated source title: {cleaned}")
    return cleaned


def episode_description(translated_title: str) -> str:
    return translated_title.strip() or "未命名中文标题"


def episode_guid(video_id: str) -> str:
    digest = hashlib.sha256(f"youtube:{video_id}".encode("utf-8")).hexdigest()
    return f"urn:podcastclip:{digest[:32]}"


def _clean_title_translation(value: str) -> str:
    value = value.strip().splitlines()[0].strip()
    value = re.sub(r"^(?:中文标题|标题|翻译)\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    value = value.strip().strip('"\'“”‘’《》')
    return re.sub(r"\s+", " ", value).strip(" .。-")


def episode_audio_filename(
    title: str,
    *,
    chinese_title: str | None = None,
    generated_date: date | None = None,
) -> str:
    """Return the stable, user-facing filename for a generated podcast MP3."""
    generated_date = generated_date or datetime.now().astimezone().date()
    safe_source_title = _sanitize_filename_part(title, fallback="未命名播客")
    safe_chinese_title = _sanitize_filename_part(chinese_title or "未命名中文标题", fallback="未命名中文标题")
    return f"{generated_date.isoformat()}-{safe_source_title} - {safe_chinese_title}.mp3"


def _sanitize_filename_part(value: str, *, fallback: str) -> str:
    safe_value = re.sub(r'[\s]*[\\/:*?"<>|\x00-\x1f]+[\s]*', "-", value)
    safe_value = re.sub(r"\s+", " ", safe_value).strip(" .-")
    safe_value = safe_value.encode("utf-8")[:100].decode("utf-8", errors="ignore").rstrip(" .-")
    return safe_value or fallback


def _episode_slug(title: str, video_id: str) -> str:
    safe_title = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", title).strip("-").lower()
    safe_title = safe_title[:70].strip("-") or "youtube"
    return f"{safe_title}-{video_id}"


def _video_id_from_url(url: str) -> str:
    match = re.search(r"(?:[?&]v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})", url)
    return match.group(1) if match else "youtube-video"

from api.services.ai.prompts import (
    _chunk_time_label,
    _fmt_clock_ms,
    _has_video_chunks,
    build_system_prompt,
)


def test_fmt_clock_ms() -> None:
    assert _fmt_clock_ms(0) == "0:00"
    assert _fmt_clock_ms(65000) == "1:05"
    assert _fmt_clock_ms("bad") == ""


def test_chunk_time_label() -> None:
    assert _chunk_time_label({"startMs": 1000, "endMs": 5000}) == " 时段=0:01-0:05"
    assert _chunk_time_label({"startMs": 90000}) == " 起点=1:30"
    assert _chunk_time_label({}) == ""


def test_has_video_chunks() -> None:
    assert _has_video_chunks([{"preview_kind": "video"}])
    assert _has_video_chunks([{"file_type": "mp4"}])
    assert _has_video_chunks([{"startMs": 0}])
    assert not _has_video_chunks([{"preview_kind": "pdf"}])


async def test_build_system_prompt_marks_video() -> None:
    text = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [
            {
                "name": "培训.mp4",
                "content": "安全帽必须佩戴",
                "score": 0.9,
                "preview_kind": "video",
                "startMs": 12000,
                "endMs": 20000,
                "chunk_index": 2,
            }
        ],
        use_knowledge=True,
    )
    assert text is not None
    assert "类型=视频转写" in text
    assert "时段=0:12-0:20" in text
    assert "【本次含视频转写片段】" in text

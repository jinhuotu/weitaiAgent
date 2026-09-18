from api.services.knowledge.asr.base import TranscriptSegment
from api.services.knowledge.asr.chunking import split_transcript_segments


def test_split_transcript_segments_keeps_time_range(monkeypatch) -> None:
    monkeypatch.setenv("KB_CHUNK_SIZE", "20")
    from common.config import get_settings

    get_settings.cache_clear()
    segs = [
        TranscriptSegment(text="一二三四五六七八", start_ms=0, end_ms=1000),
        TranscriptSegment(text="九十一二三四五", start_ms=1000, end_ms=2000),
        TranscriptSegment(text="六七八九十", start_ms=2000, end_ms=3000),
    ]
    chunks = split_transcript_segments(segs)
    assert chunks
    assert chunks[0].start_ms == 0
    assert chunks[-1].end_ms == 3000
    assert "".join(c.content for c in chunks)
    get_settings.cache_clear()


def test_split_transcript_segments_empty() -> None:
    assert split_transcript_segments([]) == []

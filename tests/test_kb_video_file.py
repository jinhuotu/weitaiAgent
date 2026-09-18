from pathlib import Path

from starlette.responses import FileResponse


def test_query_token_allows_kb_file() -> None:
    from api.middleware.auth import _query_token_allowed

    assert _query_token_allowed("/api/v1/knowledge/documents/x/file") is True
    assert _query_token_allowed("/api/v1/knowledge/documents/x/file/") is True
    assert _query_token_allowed("/api/v1/knowledge/documents/x/download") is False
    assert _query_token_allowed("/api/v1/knowledge/bases") is False


def test_file_response_supports_range_headers(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0123456789abcdef" * 64)
    resp = FileResponse(
        path,
        media_type="video/mp4",
        filename="clip.mp4",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=60"},
    )
    assert resp.media_type == "video/mp4"
    assert resp.headers.get("accept-ranges") == "bytes"

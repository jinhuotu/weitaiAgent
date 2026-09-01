"""对话附图：校验、落盘、拼多模态 content。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from api.services.ai.chat_images import (
    DEFAULT_IMAGE_PROMPT,
    MAX_IMAGES,
    decode_chat_images,
    hydrate_images_for_api,
    llm_image_parts,
    multimodal_user_content,
    persist_chat_images,
    state_images_from_input,
)
from api.services.ai.memory import hot_messages_for_llm
from api.services.ai.prompts import VISION_HINT, build_system_prompt
from api.services.mcp.runtime import _message_content
from common.errors import AppError


def _b64(n: int = 32) -> str:
    return base64.b64encode(b"\xff\xd8" + b"x" * n).decode("ascii")


def test_decode_accepts_data_url_and_jpg_alias() -> None:
    raw = _b64()
    out = decode_chat_images(
        [{"mimeType": "image/jpg", "data": f"data:image/jpeg;base64,{raw}"}]
    )
    assert len(out) == 1
    assert out[0][0] == "image/jpeg"
    assert out[0][1].startswith(b"\xff\xd8")


def test_decode_rejects_too_many_and_bad_mime() -> None:
    with pytest.raises(AppError) as too_many:
        decode_chat_images(
            [{"mimeType": "image/png", "data": _b64()} for _ in range(MAX_IMAGES + 1)]
        )
    assert too_many.value.status_code == 422
    with pytest.raises(AppError) as bad_mime:
        decode_chat_images([{"mimeType": "application/pdf", "data": _b64()}])
    assert "jpeg" in bad_mime.value.msg


def test_persist_hydrate_and_llm_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr("api.services.ai.chat_images._storage_root", lambda: root)
    blobs = decode_chat_images([{"mimeType": "image/png", "data": _b64(64)}])
    saved = persist_chat_images(session_public_id="sess1", msg_id="abc", blobs=blobs)
    assert saved[0]["fileKey"].startswith("chat/sess1/")
    assert (root / saved[0]["fileKey"]).is_file()

    hydrated = hydrate_images_for_api(saved)
    assert hydrated[0]["dataUrl"].startswith("data:image/png;base64,")
    parts = llm_image_parts(saved)
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_hot_messages_for_llm_multimodal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr("api.services.ai.chat_images._storage_root", lambda: root)
    blobs = decode_chat_images([{"mimeType": "image/jpeg", "data": _b64()}])
    saved = persist_chat_images(session_public_id="s2", msg_id="m1", blobs=blobs)
    out = hot_messages_for_llm(
        [{"role": "user", "content": "", "images": saved}]
    )
    content = out[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": DEFAULT_IMAGE_PROMPT}
    assert content[1]["type"] == "image_url"


def test_mcp_keeps_list_content() -> None:
    parts = [{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "x"}}]
    assert _message_content({"role": "user", "content": parts}) is parts
    assert _message_content({"role": "user", "content": "hi"}) == "hi"


def test_multimodal_user_content_from_blobs() -> None:
    blobs = decode_chat_images([{"mimeType": "image/png", "data": _b64()}])
    parts = multimodal_user_content("解读这张图", blobs)
    assert parts[0] == {"type": "text", "text": "解读这张图"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_vision_system_prompt_without_kb() -> None:
    text = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [],
        use_knowledge=False,
        has_images=True,
    )
    assert text is not None
    assert VISION_HINT in text
    empty = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [],
        use_knowledge=False,
        has_images=False,
    )
    assert empty is None


def test_state_images_from_input_data_url() -> None:
    raw = _b64()
    out = state_images_from_input(
        [{"mimeType": "image/png", "dataUrl": f"data:image/png;base64,{raw}"}]
    )
    assert len(out) == 1
    assert out[0]["dataUrl"].startswith("data:image/png;base64,")

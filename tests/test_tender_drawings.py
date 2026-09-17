from pathlib import Path

import pytest
from PIL import Image

from common.errors import AppError


def _png(path: Path) -> bytes:
    Image.new("RGB", (40, 30), (20, 80, 140)).save(path, "PNG")
    return path.read_bytes()


def test_drawings_are_scoped_to_invitation(tmp_path, monkeypatch) -> None:
    from api.services.tenders import slots as slots_mod
    from api.services.tenders.placeholders import TECH_DRAWING_KEY
    from api.services.tenders.slots import (
        attach_invitation_drawings,
        attachments_for_slots,
        clear_drawing_files,
        list_drawing_files,
        save_drawing_file,
        save_slot_file,
    )

    root = tmp_path / "tender-assets"
    root.mkdir()
    monkeypatch.setattr(slots_mod, "tender_assets_dir", lambda: root)
    data = _png(tmp_path / "a.png")

    with pytest.raises(AppError, match="识别邀请书"):
        save_drawing_file(invitation_id="", filename="a.png", data=data)

    save_drawing_file(invitation_id="aaaaaaaaaaaa", filename="site.png", data=data)
    save_drawing_file(invitation_id="bbbbbbbbbbbb", filename="other.png", data=data)
    a_files = list_drawing_files("aaaaaaaaaaaa")
    b_files = list_drawing_files("bbbbbbbbbbbb")
    assert len(a_files) == 1
    assert len(b_files) == 1
    assert a_files[0].parent != b_files[0].parent

    media = attach_invitation_drawings({}, "aaaaaaaaaaaa")
    assert TECH_DRAWING_KEY in media
    assert media[TECH_DRAWING_KEY] == a_files
    media = attach_invitation_drawings({TECH_DRAWING_KEY: b_files}, "cccccccccccc")
    assert TECH_DRAWING_KEY not in media

    save_slot_file("tech_drawings", filename="legacy.png", data=data)
    from api.services.tenders.schema import PlaceholderItem

    leaked = attachments_for_slots(
        [PlaceholderItem(key=TECH_DRAWING_KEY, title="实施方案图纸")]
    )
    assert TECH_DRAWING_KEY not in leaked
    clear_drawing_files("aaaaaaaaaaaa")
    assert list_drawing_files("aaaaaaaaaaaa") == []
    assert len(list_drawing_files("bbbbbbbbbbbb")) == 1

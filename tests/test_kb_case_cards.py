"""布置案例卡整篇切块，避免 1. 2. 3. 拆成碎片。"""

from pathlib import Path

from api.services.knowledge.chunking import is_layout_case_card, split_text
from api.services.knowledge.drawings import is_drawing_ext, preview_jpeg_from_file
from api.services.knowledge.ingest import _want_drawing_attachment


def test_layout_case_card_stays_one_chunk() -> None:
    text = Path("scripts/kb_cases/充电站布置案例_模板.md").read_text(encoding="utf-8")
    assert is_layout_case_card(text)
    chunks = split_text(text)
    assert len(chunks) == 1
    assert "方案摘要" in chunks[0]
    assert "生成时如何复用" in chunks[0]


def test_layout_case_prompt_strips_scale_numbers() -> None:
    from api.services.knowledge.chunking import summarize_layout_case_for_prompt

    text = Path("scripts/kb_cases/充电站布置案例_斜列15桩3台1600kVA.md").read_text(
        encoding="utf-8"
    )
    out = summarize_layout_case_for_prompt(text, name="斜列15桩")
    assert "禁止照抄" in out
    assert "规模配置" not in out
    assert "15 台 320kW" not in out


def test_manual_sections_still_split() -> None:
    text = "Intro paragraph.\n8.3 Coolant shutdown\nBody A.\n9.2 Power outage\nBody B."
    chunks = split_text(text)
    assert len(chunks) >= 2


def test_drawing_ext_and_attachment_flag() -> None:
    assert is_drawing_ext("pdf")
    assert is_drawing_ext("PNG")
    assert not is_drawing_ext("docx")
    assert _want_drawing_attachment(ext="pdf", as_attachment=True, parent_id=None, tags=[])
    assert not _want_drawing_attachment(ext="pdf", as_attachment=False, parent_id=None, tags=[])
    assert _want_drawing_attachment(ext="jpg", as_attachment=None, parent_id="abc", tags=[])
    assert _want_drawing_attachment(
        ext="pdf", as_attachment=None, parent_id=None, tags=["图纸附件"]
    )
    assert not _want_drawing_attachment(
        ext="pdf", as_attachment=None, parent_id=None, tags=["手动上传"]
    )


def test_page_and_sheet_chunk_boundaries() -> None:
    text = (
        "[第1页]\n"
        "第一页正文甲甲甲。\n"
        "[第2页]\n"
        "第二页正文乙乙乙。\n"
        "## 冷却\n"
        "工作表行丙丙丙。"
    )
    chunks = split_text(text)
    assert len(chunks) >= 3
    assert any("第1页" in c and "甲" in c for c in chunks)
    assert any("第2页" in c and "乙" in c for c in chunks)
    assert any("冷却" in c and "丙" in c for c in chunks)



def test_preview_jpeg_from_png(tmp_path: Path) -> None:
    from PIL import Image

    src = tmp_path / "a.png"
    Image.new("RGB", (1600, 900), (255, 255, 255)).save(src)
    jpeg = preview_jpeg_from_file(src, ext="png", max_px=512)
    assert jpeg[:3] == b"\xff\xd8\xff"
    dest = tmp_path / "x.jpg"
    dest.write_bytes(jpeg)
    img = Image.open(dest)
    assert max(img.size) <= 512

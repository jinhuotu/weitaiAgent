import asyncio
from types import SimpleNamespace

import pytest

from api.services.tenders.tech_fill import fill_boq, fill_drawing, sheet_qty_text
from common.errors import AppError


def test_qty_text_keeps_quantity_not_price() -> None:
    line = SimpleNamespace(name="电缆", spec="YJV", unit="米", qty=120, unitPrice=30, amount=3600)
    text = sheet_qty_text(SimpleNamespace(lines=[line]))
    assert "电缆" in text
    assert "120" in text
    assert "3600" not in text


def test_fill_boq_without_llm_lists_rows() -> None:
    raw = "名称,规格,单位,数量\n电缆,YJV,米,120\n".encode("utf-8")
    text = asyncio.run(fill_boq(raw, "boq.csv", client=None))
    assert "电缆" in text
    assert "不含报价" in text or "【待补充】" in text
    assert "3600" not in text


def test_fill_boq_prefers_llm_text() -> None:
    class Client:
        async def complete(self, messages, mode="fast"):
            del mode
            assert "无单价" in messages[1]["content"]
            return "按清单编制施工组织。"

    raw = "项目,单位,数量\n桥架,米,40\n".encode("utf-8")
    text = asyncio.run(fill_boq(raw, "boq.csv", client=Client()))
    assert text == "按清单编制施工组织。"


def test_drawing_pdf_only() -> None:
    with pytest.raises(AppError) as exc:
        asyncio.run(fill_drawing(b"not-a-pdf", "site.png", client=None))
    assert "PDF" in str(exc.value)


def test_drawing_without_text_is_pending() -> None:
    text = asyncio.run(fill_drawing(b"%PDF-1.1\n%\n", "draw.pdf", client=None))
    assert "待补充" in text or "摘录" in text

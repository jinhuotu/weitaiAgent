"""投标文件填空与人民币大写。"""

from __future__ import annotations

from decimal import Decimal

from api.services.tenders.assets import find_chapter5_template
from api.services.tenders.document import build_bid_docx
from api.services.tenders.money import rmb_lowercase, rmb_uppercase
from api.services.tenders.schema import BidBrief


def test_default_brief_project_fields_empty() -> None:
    from api.services.tenders.schema import default_brief

    brief = default_brief()
    assert brief.projectName == ""
    assert brief.tenderer == ""
    assert brief.bidContent == ""
    assert brief.bidderName == "河南伟泰光电科技有限公司"


def test_chapter5_template_exists() -> None:
    path = find_chapter5_template()
    assert path is not None
    assert path.is_file()
    assert path.stat().st_size > 10000


def test_rmb_uppercase_integer() -> None:
    assert rmb_uppercase(518800) == "伍拾壹万捌仟捌佰元整"
    assert rmb_uppercase(0) == "零元整"
    assert rmb_uppercase("1.23") == "壹元贰角叁分"


def test_rmb_lowercase() -> None:
    assert rmb_lowercase(518800) == "518,800.00"
    assert rmb_lowercase(Decimal("1000.5")) == "1,000.50"


def test_build_bid_docx_fills_company(tmp_path) -> None:
    brief = BidBrief(
        projectName="高途智成港（一期）汽车充电桩采购安装项目",
        tenderer="郑州有爱文化科技有限公司",
        bidContent="地下车库汽车充电桩供货、安装调试及技术服务",
        bidPriceYuan=518800,
        legalPersonName="测试",
        attachQualifications=False,
    )
    dest = tmp_path / "bid.docx"
    path, warnings = build_bid_docx(brief, dest, qualification_pdf=None)
    assert path.is_file()
    assert path.stat().st_size > 2000
    assert not any("未找到资质" in w for w in warnings)

    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    table_text = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    all_text = text + "\n" + table_text
    assert "河南伟泰光电科技有限公司" in all_text
    assert "伍拾壹万捌仟捌佰元整" in all_text
    assert "高途智成港" in all_text
    assert "测试" in all_text
    assert "投  标  文  件" in text
    assert "录" in text
    assert "投标函及投标函附录" in text
    assert "其他材料" in text
    assert "法定代表人身份证明" in text
    assert "第五章" not in text
    assert "本部分附企业介绍" not in all_text
    assert "待补附件占位" not in all_text
    assert "（一）法定代表人身份证正反面" in all_text
    assert "（在此粘贴扫描件）" in all_text
    assert "投标承诺书" in text
    assert "附件五：" in text
    assert "我公司现做出如下承诺" in text
    assert "保证投标文件无虚假内容" in text
    assert any('w:val="dashed"' in t._tbl.xml for t in doc.tables)

    toc = next(p for p in doc.paragraphs if "一、投标函及投标函附录" in p.text)
    toc_xml = toc._p.xml
    assert 'w:leader="dot"' in toc_xml
    assert 'w:val="right"' in toc_xml
    assert "underscore" not in toc_xml
    assert "w:numPr" not in toc_xml
    assert "2" in toc.text
    assert 'w:val="none"' in toc_xml
    tab_run = next(run for run in toc.runs if run._element.find(qn("w:tab")) is not None)
    rfonts = tab_run._element.find(qn("w:rPr")).find(qn("w:rFonts"))
    assert rfonts.get(qn("w:ascii")) == "Times New Roman"
    toc_sizes = {run.font.size.pt for run in toc.runs if run.font.size is not None}
    assert toc_sizes == {10.5}

    assert "（招标人名称）" in text
    assert brief.tenderer in text
    assert f"{brief.tenderer}）：" not in text
    assert f"{brief.tenderer} )：" not in text

    underlined = any("w:u" in p._p.xml for p in doc.paragraphs)
    assert underlined

    # 横线填空字：有下划线、不加粗、不强制放大字号
    blank_filled = [
        run
        for p in doc.paragraphs
        for run in p.runs
        if (run.text or "").strip()
        and run.underline is True
        and "河南伟泰" in (run.text or "")
    ]
    assert blank_filled
    for run in blank_filled:
        assert run.bold is not True
        rpr = run._element.find(qn("w:rPr"))
        assert rpr is not None
        bold_el = rpr.find(qn("w:b"))
        assert bold_el is None
        sz = rpr.find(qn("w:sz"))
        if sz is not None:
            assert int(sz.get(qn("w:val")) or "0") <= 24  # ≤12pt
        # 不再强制把填空改成宋体放大观感，应跟邻行一致或无自设字号
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is not None:
            assert rfonts.get(qn("w:eastAsia")) != "黑体"

    # 封面签字空位、投标函「投 标 人」：横线不加粗，不用形状线
    cover = next(
        p
        for p in doc.paragraphs
        if "法定代表人或其委托代理人" in (p.text or "")
        and "（签字）" in (p.text or "")
        and "盖单位公章" in (p.text or "")
    )
    for run in cover.runs:
        rpr = run._element.find(qn("w:rPr"))
        if rpr is None or rpr.find(qn("w:u")) is None:
            continue
        if (run.text or "").strip():
            continue
        assert rpr.find(qn("w:b")) is None
        assert run.bold is not True

    spaced = next(p for p in doc.paragraphs if (p.text or "").startswith("投 标 人"))
    assert "河南伟泰" in spaced.text
    assert "w:drawing" not in spaced._p.xml
    assert "w:pict" not in spaced._p.xml
    assert "AlternateContent" not in spaced._p.xml

    east_asia = {
        run._element.find(qn("w:rPr")).find(qn("w:rFonts")).get(qn("w:eastAsia"))
        for p in doc.paragraphs
        for run in p.runs
        if run.text.strip()
        and run._element.find(qn("w:rPr")) is not None
        and run._element.find(qn("w:rPr")).find(qn("w:rFonts")) is not None
    }
    assert "宋体" in east_asia

    footer_xml = doc.sections[-1].footer.paragraphs[0]._p.xml
    assert "PAGE" in footer_xml
    assert "w:fldChar" in footer_xml
    header_text = doc.sections[-1].header.paragraphs[0].text
    assert "投标文件" in header_text

    appendix = next(t for t in doc.tables if "项目名称" in t.rows[0].cells[0].text)
    assert brief.projectName in appendix.rows[0].cells[-1].text
    assert "w:pBdr" not in appendix.rows[0].cells[-1]._tc.xml

    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    deviation = next(
        t
        for t in doc.tables
        if len(t.rows[0].cells) >= 4 and "招标文件要求" in t.rows[0].cells[1].text
    )
    for row in deviation.rows:
        for cell in row.cells:
            assert "w:pBdr" not in cell._tc.xml
            for para in cell.paragraphs:
                for run in para.runs:
                    assert run.underline is not True
                    rpr = run._element.find(qn("w:rPr"))
                    if rpr is not None:
                        assert rpr.find(qn("w:u")) is None
    serial = deviation.rows[1].cells[0]
    assert serial.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert serial.vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER
    note = deviation.rows[1].cells[3]
    assert "无偏差" in note.text
    assert note.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert note.vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER
    long_req = deviation.rows[1].cells[1]
    assert long_req.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert long_req.vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER

    quote = next(
        t
        for t in doc.tables
        if any("不含税综合单价" in (c.text or "") for c in t.rows[0].cells)
    )
    assert "133" in quote.rows[1].cells[4].text
    assert "56" in quote.rows[2].cells[4].text
    assert "518,800.00" in quote.rows[-1].cells[-1].text
    assert any("二次报价函" in w for w in warnings)
    assert not any("折算" in w for w in warnings)


def test_quote_table_fits_page_width(tmp_path) -> None:
    brief = BidBrief(
        projectName="宽表测试",
        tenderer="招标人",
        bidPriceYuan=518800,
        legalPersonName="测试",
        attachQualifications=False,
        includePlaceholders=False,
        includeCommitment=False,
    )
    path, _warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(path))
    quote = next(
        t
        for t in doc.tables
        if any("不含税综合单价" in (c.text or "") for c in t.rows[0].cells)
    )
    tbl_w = quote._tbl.tblPr.find(qn("w:tblW"))
    assert tbl_w is not None
    assert tbl_w.get(qn("w:type")) == "dxa"
    table_twips = int(tbl_w.get(qn("w:w")))
    section = doc.sections[0]
    usable = (
        int(section.page_width.twips)
        - int(section.left_margin.twips)
        - int(section.right_margin.twips)
    )
    assert table_twips <= usable
    layout = quote._tbl.tblPr.find(qn("w:tblLayout"))
    assert layout is not None
    assert layout.get(qn("w:type")) == "fixed"
    settings_xml = doc.settings.element.xml
    assert "hideSpellingErrors" in settings_xml
    assert "hideGrammaticalErrors" in settings_xml
    rpr_default = doc.styles.element.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
    rfonts = rpr_default.find(qn("w:rFonts"))
    assert rfonts.get(qn("w:eastAsia")) == "SimSun"
    assert qn("w:eastAsiaTheme") not in rfonts.attrib


def test_quote_sheet_scales_to_second_round() -> None:
    from api.services.tenders.assets import find_quote_xlsx
    from api.services.tenders.quote import SECOND_ROUND_YUAN, load_quote_sheet, scale_quote

    path = find_quote_xlsx()
    assert path is not None
    sheet = load_quote_sheet(path)
    assert sheet.lines[0].qty == 133
    scaled = scale_quote(sheet, SECOND_ROUND_YUAN)
    assert scaled.lines[0].qty == 133
    assert scaled.total_inc_tax == SECOND_ROUND_YUAN
    assert scaled.lines[0].unit_price != sheet.lines[0].unit_price


def test_build_bid_docx_quote_scaled(tmp_path) -> None:
    brief = BidBrief(
        bidPriceYuan=439700,
        legalPersonName="测试",
        attachQualifications=False,
    )
    path, warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document(str(path))
    quote = next(
        t
        for t in doc.tables
        if any("不含税综合单价" in (c.text or "") for c in t.rows[0].cells)
    )
    assert "133" in quote.rows[1].cells[4].text
    assert "439,700.00" in quote.rows[-1].cells[-1].text
    assert any("折算" in w for w in warnings)
    appendix = next(t for t in doc.tables if "项目名称" in t.rows[0].cells[0].text and len(t.rows[0].cells) >= 3)
    assert brief.projectName in appendix.rows[0].cells[-1].text
    pay = next(t for t in doc.tables if "支付阶段" in t.rows[0].cells[1].text)
    for row in pay.rows[1:]:
        pct_cell = row.cells[2]
        assert "支付至" in pct_cell.text
        assert "%" in pct_cell.text
        para = next(p for p in pct_cell.paragraphs if "支付至" in p.text)
        assert para.alignment == WD_ALIGN_PARAGRAPH.CENTER
        assert "w:u" in para._p.xml
        assert "w:tab" in para._p.xml
    assert "17" in pay.rows[3].cells[2].text
    assert "3" in pay.rows[4].cells[2].text
    settle_xml = next(p._p.xml for p in pay.rows[3].cells[2].paragraphs if "支付至" in p.text)
    prepay_xml = next(p._p.xml for p in pay.rows[1].cells[2].paragraphs if "支付至" in p.text)
    assert settle_xml.count("w:tab") == prepay_xml.count("w:tab")


def test_placeholders_can_be_skipped(tmp_path) -> None:
    brief = BidBrief(
        bidPriceYuan=518800,
        attachQualifications=False,
        includePlaceholders=False,
        includeCommitment=False,
    )
    path, _warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "（一）法定代表人身份证正反面" not in text
    assert "本部分附企业介绍" not in text
    assert "投标承诺书" not in text


def test_factory_commitment_follows_tender_name(tmp_path) -> None:
    brief = BidBrief(
        projectName="厂内新能源充电桩采购项目",
        tenderer="测试招标人新能源有限公司",
        bidPriceYuan=100000,
        legalPersonName="张三",
        deliveryDays=45,
        warrantyYears=3,
        attachQualifications=False,
        includePlaceholders=False,
        includeCommitment=False,
    )
    path, _warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    factory = next(p for p in doc.paragraphs if "作为充电设备生产厂商" in (p.text or ""))
    assert "测试招标人新能源有限公司" in factory.text
    assert "厂内新能源充电桩采购项目" in factory.text
    assert "联源热电" not in factory.text
    assert "45" in factory.text
    assert "3" in factory.text
    filled = [run.text for run in factory.runs if run.underline is True]
    assert any("测试招标人新能源有限公司" in t for t in filled)
    assert any("厂内新能源充电桩采购项目" in t for t in filled)


def test_factory_commitment_falls_back_to_project_name(tmp_path) -> None:
    brief = BidBrief(
        projectName="备件采购投标项目",
        tenderer="",
        bidPriceYuan=100000,
        legalPersonName="张三",
        attachQualifications=False,
        includePlaceholders=False,
        includeCommitment=False,
    )
    path, _warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    factory = next(p for p in doc.paragraphs if "作为充电设备生产厂商" in (p.text or ""))
    assert factory.text.startswith("备件采购投标项目：")


def test_commitment_letter_fills_tenderer(tmp_path) -> None:
    brief = BidBrief(
        projectName="厂内新能源充电桩采购项目",
        tenderer="二连浩特市联源热电有限公司",
        bidPriceYuan=100000,
        legalPersonName="张三",
        attachQualifications=False,
        includePlaceholders=False,
        includeCommitment=True,
        bidDate="2026-08-25",
    )
    path, warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "附件五：" in text
    assert "投标承诺书" in text
    assert "二连浩特市联源热电有限公司" in text
    assert "河南伟泰光电科技有限公司" in text
    assert "张三" in text
    assert "2026" in text
    assert any("投标承诺书" in w for w in warnings)
    # 填空不加粗
    filled = [
        run
        for p in doc.paragraphs
        for run in p.runs
        if run.underline is True and "联源热电" in (run.text or "")
    ]
    assert filled
    for run in filled:
        assert run.bold is not True


def test_extra_placeholder_box(tmp_path) -> None:
    from api.services.tenders.schema import PlaceholderItem

    brief = BidBrief(
        bidPriceYuan=518800,
        attachQualifications=False,
        extraPlaceholders=[PlaceholderItem(key="iso", title="ISO体系证书", hint="按邀请书第3.8条")],
    )
    path, warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "ISO体系证书" in text
    assert "（一）法定代表人身份证正反面" in text
    assert any("待补虚线框" in w or "已将" in w for w in warnings)


def test_slot_image_fills_placeholder(tmp_path, monkeypatch) -> None:
    from PIL import Image

    from api.services.tenders import slots as slots_mod
    from api.services.tenders.slots import clear_slot, save_slot_file

    root = tmp_path / "tender-assets"
    root.mkdir()
    monkeypatch.setattr(slots_mod, "tender_assets_dir", lambda: root)

    img = Image.new("RGB", (120, 80), color=(200, 200, 200))
    buf = tmp_path / "id.png"
    img.save(buf, format="PNG")
    save_slot_file("id_legal", filename="id.png", data=buf.read_bytes(), replace=True)

    brief = BidBrief(
        bidPriceYuan=100000,
        attachQualifications=False,
        includePlaceholders=True,
        includeCommitment=False,
    )
    path, warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "（一）法定代表人身份证正反面" in text
    assert any("已将" in w and "扫描件" in w for w in warnings)
    assert any("待补虚线框" in w for w in warnings)
    box_hits = sum(
        1
        for t in doc.tables
        for row in t.rows
        for c in row.cells
        if "在此粘贴扫描件" in (c.text or "")
    )
    assert box_hits >= 1
    xml = "\n".join(p._p.xml for p in doc.paragraphs)
    assert "a:blip" in xml or "pic:blipFill" in xml
    clear_slot("id_legal")


def test_collect_slots_status_keys() -> None:
    from api.services.tenders.slots import library_payload, list_slots_status

    rows = list_slots_status()
    keys = {r["key"] for r in rows}
    assert "id_legal" in keys
    assert "bond" in keys

    lib = library_payload()
    assert lib["totalCount"] == 8
    assert len(lib["slots"]) == 8
    assert all(s.get("title") for s in lib["slots"])
    assert "常备" in str(lib.get("hint") or "") or "扫描" in str(lib.get("hint") or "")


def test_extract_patch_keeps_bidder_and_legal() -> None:
    from api.services.tenders.extract import apply_extract_patch, parse_extract_payload, parse_kb_ids
    from api.services.tenders.placeholders import collect_slots
    from api.services.tenders.schema import PlaceholderItem

    base = BidBrief(
        projectName="旧项目",
        tenderer="旧招标人",
        legalPersonName="张三",
        bidderName="河南伟泰光电科技有限公司",
        bidPriceYuan=100,
    )
    blob = """<think>先读邀请书</think>
```json
{
  "projectName": "南京广场光伏充电桩项目",
  "tenderer": "南京某建设有限公司",
  "deliveryDays": 45,
  "bidPriceYuan": 0,
  "bidderName": "郑州容新新能源有限公司",
  "legalPersonName": "李四",
  "notes": ["核对保证金账户"],
  "missingMaterials": [{"key": "iso", "title": "ISO体系证书", "reason": "资格预审额外要求"}]
}
```
"""
    patch = parse_extract_payload(blob)
    brief, filled = apply_extract_patch(base, patch)
    assert brief.projectName == "南京广场光伏充电桩项目"
    assert brief.tenderer == "南京某建设有限公司"
    assert brief.deliveryDays == 45
    assert brief.bidPriceYuan == 100
    assert brief.bidderName == "河南伟泰光电科技有限公司"
    assert brief.legalPersonName == "张三"
    assert "projectName" in filled
    assert "bidPriceYuan" not in filled
    slots = collect_slots([PlaceholderItem(key="iso", title="ISO体系证书", hint="资格预审额外要求")])
    assert any(s.key == "iso" for s in slots)
    assert any(s.key == "id_legal" for s in slots)
    assert parse_kb_ids('["a","b"]') == ["a", "b"]
    assert parse_kb_ids("a, b") == ["a", "b"]


def test_parse_generic_xlsx_bom(tmp_path) -> None:
    from decimal import Decimal

    from openpyxl import Workbook

    from api.services.tenders.quote import parse_quote_path

    wb = Workbook()
    ws = wb.active
    ws.append(["南京广场光伏充电桩工程量清单"])
    ws.append(["序号", "项目名称", "规格", "单位", "数量", "单价", "合价"])
    ws.append([1, "7kW交流桩", "7kW", "台", 20, 1000, 20000])
    ws.append([2, "30kW直流桩", "30kW", "台", 8, 5000, 40000])
    ws.append([None, "不含税合计", None, None, None, None, 60000])
    ws.append([None, "税率", None, None, None, None, 0.13])
    ws.append([None, "含税合计", None, None, None, None, 67800])
    path = tmp_path / "bom.xlsx"
    wb.save(path)
    sheet = parse_quote_path(path)
    assert sheet is not None
    assert len(sheet.lines) == 2
    assert sheet.lines[0].qty == Decimal("20")
    assert sheet.lines[1].qty == Decimal("8")
    assert "南京广场" in sheet.title
    assert sheet.total_inc_tax == Decimal("67800")


def test_parse_quote_from_pipe_text() -> None:
    from api.services.tenders.quote import parse_quote_from_text

    text = (
        "前言\n"
        "序号 | 设备 | 单位 | 数量 | 单价 | 合价\n"
        "1 | 充电桩 | 台 | 12 | 800 | 9600\n"
        "2 | 立柱 | 个 | 12 | 100 | 1200\n"
        "结束"
    )
    sheet = parse_quote_from_text(text)
    assert sheet is not None
    assert len(sheet.lines) == 2
    assert sheet.lines[0].name == "充电桩"
    assert int(sheet.lines[0].qty) == 12


def test_build_uses_brief_quote_lines(tmp_path) -> None:
    from api.services.tenders.schema import QuoteLineIn

    brief = BidBrief(
        projectName="南京广场光伏充电板安装项目",
        bidPriceYuan=22600,
        legalPersonName="测试",
        attachQualifications=False,
        includePlaceholders=False,
        quoteSource="file",
        quoteTitle="南京广场清单",
        quoteTaxRate=0.13,
        quoteSourceIncTax=22600,
        quoteLines=[
            QuoteLineIn(
                seq="1",
                name="7kW交流桩",
                spec="7kW",
                unit="台",
                qty=20,
                unitPrice=1000,
                amount=20000,
            )
        ],
    )
    path, warnings = build_bid_docx(brief, tmp_path / "bid.docx", qualification_pdf=None)
    from docx import Document

    doc = Document(str(path))
    quote = next(
        t
        for t in doc.tables
        if any("不含税综合单价" in (c.text or "") for c in t.rows[0].cells)
    )
    assert "7kW交流桩" in quote.rows[1].cells[1].text
    assert "20" in quote.rows[1].cells[4].text
    assert "133" not in quote.rows[1].cells[4].text
    assert "22,600.00" in quote.rows[-1].cells[-1].text
    assert not any("二次报价函" in w for w in warnings)
    assert any("南京广场清单" in w or "分项报价来自" in w for w in warnings)


def test_quote_payload_from_llm_patch() -> None:
    from api.services.tenders.quote import quote_payload_from_patch

    sheet = quote_payload_from_patch(
        {
            "quoteTitle": "邀请书清单",
            "quoteTaxRate": 0.13,
            "quoteLines": [
                {"seq": "1", "name": "直流桩", "unit": "台", "qty": 6, "unitPrice": 4000, "amount": 24000}
            ],
        }
    )
    assert sheet is not None
    assert sheet.lines[0].name == "直流桩"
    assert int(sheet.lines[0].qty) == 6

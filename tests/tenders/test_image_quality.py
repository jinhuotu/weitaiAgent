import io

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm
from PIL import Image
import pypdfium2 as pdfium
import pytest

from api.services.cablepkg.image_quality import image_png, inspect_docx, pdf_page_png
from api.services.tenders import placeholders
from api.services.tenders.scan_placement import _pictures
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.section_materials import insert_material


def pdf_fixture(path):
    import fitz
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((40, 55), "Cable test report 10 kV", fontsize=10)
    page.draw_rect(fitz.Rect(40, 75, 550, 760))
    pdf.save(path)
    pdf.close()


def test_pdf_renders_a4_as_300_dpi_png(tmp_path):
    path = tmp_path / "report.pdf"
    pdf_fixture(path)
    pdf = pdfium.PdfDocument(path)
    page = pdf[0]
    try:
        with Image.open(pdf_page_png(page)) as image:
            assert image.format == "PNG"
            assert image.size == (2480, 3509)
    finally:
        page.close()
        pdf.close()


def test_image_pixels_follow_display_size_and_never_upscale():
    with Image.open(image_png(Image.new("RGB", (4000, 2000)), width_cm=10.16, height_cm=5.08)) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 600)
    with Image.open(image_png(Image.new("RGB", (300, 150)), width_cm=10.16, height_cm=5.08)) as image:
        assert image.size == (300, 150)


def test_quality_checks_actual_pixels_against_placement(tmp_path):
    document = Document()
    high = io.BytesIO()
    Image.new("RGB", (1200, 600)).save(high, format="PNG")
    low = io.BytesIO()
    Image.new("RGB", (300, 150)).save(low, format="PNG")
    document.add_paragraph().add_run().add_picture(high, width=Cm(10.16))
    document.add_paragraph().add_run().add_picture(low, width=Cm(10.16))
    path = tmp_path / "quality.docx"
    document.save(path)
    report = inspect_docx(path)
    assert report["lowResolutionCount"] == 1
    assert report["images"][0]["pixels"] == [1200, 600]
    assert report["images"][0]["widthCm"] == 10.16
    assert report["images"][0]["heightCm"] == 5.08
    assert report["images"][0]["effectiveDpi"] == [300, 300]
    assert report["images"][1]["effectiveDpi"] == [75, 75]


def test_section_pdf_keeps_full_page_and_png_after_placement(tmp_path):
    source = tmp_path / "report.pdf"
    pdf_fixture(source)
    document = Document()
    anchor = document.add_paragraph("产品检测资料")
    document.add_paragraph("下一章", style="Heading 1")
    insert_material(document, anchor._p, PlaceholderItem(key="product", title="产品检测报告"), [source])
    path = tmp_path / "section.docx"
    document.save(path)
    report = inspect_docx(path)
    assert len(report["images"]) == 1
    image = report["images"][0]
    assert image["part"].endswith(".png")
    assert image["pixels"] == [2480, 3509]
    assert image["heightCm"] > 20
    assert min(image["effectiveDpi"]) >= 300
    drawing = next(document.element.body.iter(qn("w:drawing")))
    paragraph = drawing.getparent().getparent()
    assert paragraph.find("w:pPr/w:pageBreakBefore", paragraph.nsmap) is not None


def test_native_attachment_path_preserves_full_page_quality(tmp_path):
    source = tmp_path / "report.pdf"
    pdf_fixture(source)
    document = Document()
    anchor = document.add_paragraph("报告附件")
    paragraphs, errors = _pictures(document, anchor._p, [source], "product")
    assert not errors
    assert len(paragraphs) == 1
    path = tmp_path / "native.docx"
    document.save(path)
    report = inspect_docx(path)
    image = report["images"][0]
    assert image["part"].endswith(".png")
    assert image["heightCm"] > 20
    assert min(image["effectiveDpi"]) >= 299
    assert paragraphs[0].find("w:pPr/w:pageBreakBefore", paragraphs[0].nsmap) is not None


def test_portrait_image_avoids_fixed_800_pixel_cap(tmp_path):
    source = tmp_path / "portrait.png"
    Image.new("RGB", (2400, 3400), "white").save(source)
    document = Document()
    assert placeholders._insert_image(document, source)
    path = tmp_path / "image.docx"
    document.save(path)
    image = inspect_docx(path)["images"][0]
    assert image["pixels"][0] > 800
    assert image["heightCm"] > 20
    assert not image["lowResolution"]


def test_generation_returns_quality_metrics_and_low_resolution_warning(tmp_path, monkeypatch):
    from api.services.tenders import generate, assemble, native_format, material_intents
    from api.services.tenders import invitation_attachment, company_identity
    from api.services.tenders.schema import BidBrief

    monkeypatch.setattr(generate, "tenders_output_dir", lambda: tmp_path)
    monkeypatch.setattr(native_format, "prefer_native_format", lambda *_: False)
    monkeypatch.setattr(material_intents, "prepare_structural_demands", lambda *_: None)
    monkeypatch.setattr(material_intents, "coverage", lambda *_: {})
    monkeypatch.setattr(invitation_attachment, "insert_required_invitation", lambda *_: [])
    monkeypatch.setattr(company_identity, "missing_company_notes", lambda *_: [])

    def builder(brief, dest, **kwargs):
        document = Document()
        source = io.BytesIO()
        Image.new("RGB", (300, 150)).save(source, format="PNG")
        document.add_paragraph().add_run().add_picture(source, width=Cm(10.16))
        document.save(dest)
        return dest, []

    monkeypatch.setattr(assemble, "assemble_bid_docx", builder)
    result = generate.generate_bid(BidBrief(attachQualifications=False))
    metrics = result["imageQuality"]["business"]
    assert metrics["lowResolutionCount"] == 1
    assert metrics["images"][0]["pixels"] == [300, 150]
    assert metrics["images"][0]["effectiveDpi"] == [75, 75]
    assert any("DPI" in warning and "75" in warning for warning in result["warnings"])


def test_volume_merge_retains_both_quality_reports():
    from api.services.tenders.generate import merge_generated
    result = merge_generated({"imageQuality": {"business": {"images": ["b"]}}},
                             {"imageQuality": {"technical": {"images": ["t"]}}})
    assert result["imageQuality"] == {"business": {"images": ["b"]}, "technical": {"images": ["t"]}}


def test_multipage_report_uses_one_page_before_per_image_without_extra_breaks(tmp_path):
    import fitz
    source = tmp_path / "two-pages.pdf"
    pdf = fitz.open()
    for index in range(2):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((40, 55), f"Report page {index + 1}")
    pdf.save(source)
    pdf.close()
    document = Document()
    for _ in range(12):
        document.add_paragraph("报告前的项目说明。" * 20)
    anchor = document.add_paragraph("检测报告附件")
    insert_material(document, anchor._p, PlaceholderItem(key="product", title="检测报告"), [source])
    drawings = list(document.element.body.iter(qn("w:drawing")))
    assert len(drawings) == 2
    for drawing in drawings:
        paragraph = drawing.getparent().getparent()
        assert paragraph.find("w:pPr/w:pageBreakBefore", paragraph.nsmap) is not None
    assert not [node for node in document.element.body.iter(qn("w:br")) if node.get(qn("w:type")) == "page"]


@pytest.mark.asyncio
async def test_record_generation_returns_quality_report_to_page(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from api.services.tenders import records, bid_archive
    from api.services.tenders.schema import BidBrief

    metrics = {"business": {"images": [{"pixels": [300, 150], "effectiveDpi": [75, 75]}]}}
    generated = {"docxFile": "example.docx", "warnings": ["附件清晰度不足"], "imageQuality": metrics}
    monkeypatch.setattr(records, "_generate_for_record", AsyncMock(return_value=(generated, {})))
    monkeypatch.setattr(records, "_drop_user_drafts", AsyncMock())
    monkeypatch.setattr(records, "_record_to_dict", lambda row: {})
    monkeypatch.setattr(bid_archive, "schedule_archive", Mock())
    db = SimpleNamespace(add=Mock(), commit=AsyncMock(),
                         refresh=AsyncMock(side_effect=lambda row: setattr(row, "id", 1)))
    result = await records.create_record_from_generate(db, BidBrief(), user_id=1, username="test")
    assert result["imageQuality"] == metrics
    assert result["warnings"] == ["附件清晰度不足"]

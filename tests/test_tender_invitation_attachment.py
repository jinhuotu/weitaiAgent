import json
from hashlib import sha256

import pytest
from docx import Document
from docx.oxml.ns import qn

from api.services.tenders import invitation_attachment as attachment

ID = 'abc123def456'


def setup_sources(tmp_path, monkeypatch, required=True, reverse=False):
    monkeypatch.setattr(attachment, 'tender_invitations_dir', lambda: tmp_path)
    form = Document()
    form.add_heading('第五章 投标文件格式', 1)
    form.add_heading('一、投标邀请书' if required else '一、投标函', 2)
    form.add_paragraph('此处附投标邀请书复印件。' if required else '我方收到投标邀请书后参加本次投标。')
    form.save(tmp_path / 'format.docx')
    letter = Document()
    letter.add_heading('投标邀请书', 1)
    letter.add_paragraph('致本次受邀企业：请于本月参加项目投标。邀请编号：TEST-001。')
    letter.save(tmp_path / 'letter.docx')
    rows = [(tmp_path / 'format.docx', '标书格式.docx', '\n'.join(p.text for p in form.paragraphs)),
            (tmp_path / 'letter.docx', '投标邀请书.docx', '\n'.join(p.text for p in letter.paragraphs))]
    if reverse:
        rows.reverse()
    attachment.save_sources(ID, *zip(*rows))
    assert attachment.format_source([row[0] for row in rows]) == tmp_path / 'format.docx'
    return form


@pytest.mark.parametrize('reverse', [False, True])
def test_two_uploads_insert_original_at_required_site_only(tmp_path, monkeypatch, reverse):
    form = setup_sources(tmp_path, monkeypatch, reverse=reverse)
    form.add_heading('二、投标函', 2)
    form.add_paragraph('下一章节内容不应被改变。')
    dest = tmp_path / 'result.docx'
    form.save(dest)
    original = (tmp_path / 'letter.docx').read_bytes()
    # A real two-page PDF exercises the existing material writer without Office.
    import fitz
    pdf = fitz.open()
    for i in range(2):
        page = pdf.new_page()
        page.insert_text((72, 72), f'Original invitation page {i + 1}')
    rendered = tmp_path / 'letter.pdf'
    pdf.save(rendered)
    pdf.close()
    from api.services.tenders import preview_pdf
    seen = []
    def render(path):
        seen.append(path.name)
        return rendered
    monkeypatch.setattr(preview_pdf, 'ensure_preview_pdf', render)
    assert attachment.insert_required_invitation(dest, ID) == []
    assert seen == ['letter.docx']
    result = Document(dest)
    body = list(result.element.body)
    image_positions = [i for i, n in enumerate(body) if list(n.iter(qn('w:drawing')))]
    assert len(image_positions) == 2
    next_heading = next(i for i, n in enumerate(body) if '二、投标函' == attachment._text(n))
    assert max(image_positions) < next_heading
    assert (tmp_path / 'letter.docx').read_bytes() == original
    once = dest.read_bytes()
    assert attachment.insert_required_invitation(dest, ID) == []
    assert dest.read_bytes() == once


def test_no_requirement_is_byte_identical(tmp_path, monkeypatch):
    form = setup_sources(tmp_path, monkeypatch, required=False)
    dest = tmp_path / 'result.docx'
    form.save(dest)
    before = sha256(dest.read_bytes()).hexdigest()
    assert attachment.insert_required_invitation(dest, ID) == []
    assert sha256(dest.read_bytes()).hexdigest() == before


def test_required_but_missing_original_warns_without_wrong_file(tmp_path, monkeypatch):
    form = setup_sources(tmp_path, monkeypatch)
    (tmp_path / 'letter.docx').unlink()
    dest = tmp_path / 'result.docx'
    form.save(dest)
    before = dest.read_bytes()
    assert '缺失' in attachment.insert_required_invitation(dest, ID)[0]
    assert dest.read_bytes() == before


def test_negative_requirement_and_passing_reference_do_not_attach(tmp_path, monkeypatch):
    form = setup_sources(tmp_path, monkeypatch, required=False)
    form.add_paragraph('无需附投标邀请书。')
    form.save(tmp_path / 'format.docx')
    dest = tmp_path / 'result.docx'
    form.save(dest)
    before = dest.read_bytes()
    assert attachment.insert_required_invitation(dest, ID) == []
    assert dest.read_bytes() == before


def test_manifest_rejects_cross_task_path(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment, 'tender_invitations_dir', lambda: tmp_path)
    (tmp_path / f'{ID}.sources.json').write_text(json.dumps([{'file': '../other.docx', 'letter': True}]))
    assert attachment._source_rows(ID) == []


def test_explicit_exemption_overrides_blank_invitation_heading(tmp_path, monkeypatch):
    form = setup_sources(tmp_path, monkeypatch)
    form.add_paragraph('本项目无需附投标邀请书。')
    form.save(tmp_path / 'format.docx')
    dest = tmp_path / 'result.docx'
    form.save(dest)
    before = dest.read_bytes()
    assert attachment.insert_required_invitation(dest, ID) == []
    assert dest.read_bytes() == before


def test_no_target_in_this_volume_does_not_append_at_end(tmp_path, monkeypatch):
    setup_sources(tmp_path, monkeypatch)
    doc = Document()
    doc.add_heading('技术方案', 1)
    dest = tmp_path / 'technical.docx'
    doc.save(dest)
    before = dest.read_bytes()
    assert '未找到指定位置' in attachment.insert_required_invitation(dest, ID)[0]
    assert dest.read_bytes() == before


def test_conversion_failure_keeps_output_unchanged(tmp_path, monkeypatch):
    form = setup_sources(tmp_path, monkeypatch)
    dest = tmp_path / 'result.docx'
    form.save(dest)
    from api.services.tenders import preview_pdf
    def fail(path):
        raise RuntimeError('converter offline')
    monkeypatch.setattr(preview_pdf, 'ensure_preview_pdf', fail)
    before = dest.read_bytes()
    assert '转换或插入失败' in attachment.insert_required_invitation(dest, ID)[0]
    assert dest.read_bytes() == before

import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import ModuleType

sys.path[:0] = ['apps', 'packages']
from docx import Document
from docx.shared import Cm, Pt
from api.services.tenders import document as render
from api.services.tenders.pdf_format import _fill_word_blanks
from api.services.tenders.schema import BidBrief

ROOT = Path('tmp/underline-render-fix')
OUT = ROOT / 'visual'
OUT.mkdir(exist_ok=True)
brief = BidBrief(tenderNo='RENDER-001', bidderName='通用联合技术服务测试单位' * 4,
                 legalPersonName='测试人员', bidDate='2026-11-01',
                 bidderAddress='测试地址十六号', bidderWebsite='https://example.test',
                 bidderPhone='010-12345678', bidderFax='010-87654321')


def slot(p):
    r = p.add_run('\t');r.underline = True;r.font.size = Pt(12)
    return r


doc = Document()
for _ in range(29):
    p = doc.add_paragraph('原版正文连续性核验。')
    p.paragraph_format.line_spacing = Pt(20)
    p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(0)
signs = []
for label, suffix in [('投标人：', '（盖单位章）'), ('姓名：', '（签字或盖章）'), ('日期：', '')]:
    p = doc.add_paragraph(label)
    p.paragraph_format.line_spacing = Pt(20)
    p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(0)
    slot(p)
    if suffix:p.add_run(suffix)
    signs.append(p)
doc.save(OUT / 'pagination-source.docx')
old_pdf = ModuleType('old_pdf_render')
exec(compile((ROOT/'pdf_format.before.py').read_text(encoding='utf-8'), 'old_pdf_render', 'exec'), old_pdf.__dict__)
tree = ast.parse((ROOT/'document.before.py').read_text(encoding='utf-8'))
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_glue_sign_off')
namespace = dict(render.__dict__)
exec(compile(ast.Module(body=[node],type_ignores=[]), 'old_sign_render', 'exec'), namespace)
old = Document(OUT/'pagination-source.docx')
old_pdf._fill_word_blanks(old, brief)
namespace['_glue_sign_off'](old, None, old.paragraphs[-3:])
old.save(OUT/'pagination-old.docx')
new = Document(OUT/'pagination-source.docx')
_fill_word_blanks(new, brief)
render._glue_sign_off(new, None, new.paragraphs[-3:])
render._pad_signature_blocks(new)
new.save(OUT/'pagination-fixed.docx')

contacts = Document()
contacts.add_paragraph('原版制表位对齐核验',style='Heading 1')
p=contacts.add_paragraph()
p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
p.paragraph_format.line_spacing=Pt(24)
p.paragraph_format.space_before=p.paragraph_format.space_after=Pt(0)
for row, fields in enumerate([[('地　址：', 'address'), ('网　址：', 'web')], [('电　话：', 'phone'),('传　真：', 'fax')]]):
    if row:p.add_run().add_break()
    for index,(label,_) in enumerate(fields):
        if index:p.add_run().add_tab()
        p.add_run(label);slot(p)
contacts.save(OUT/'contacts-source.docx')
_fill_word_blanks(contacts,brief)
contacts.save(OUT/'contacts-fixed.docx')
print('created 5 rendering verification documents')

"""Internal rendering QA using the application's actual DOCX rendering helpers."""
from pathlib import Path
import sys
from types import ModuleType

sys.path[:0] = ['apps', 'packages']
from docx import Document
from docx.shared import Cm, Pt
from api.services.tenders import document as render
from api.services.tenders.pdf_format import _fill_word_blanks
from api.services.tenders.schema import BidBrief

ROOT = Path('tmp/readable-signoff-fix')
OUT = ROOT / 'visual'
OUT.mkdir(exist_ok=True)
old = ModuleType('old_render')
exec(compile((ROOT/'pdf_format.before.py').read_text(encoding='utf-8'), 'old_render', 'exec'), old.__dict__)
brief = BidBrief(bidderName='通用联合服务测试单位名称'*5, legalPersonName='法人测试人员',
                 bidderPhone='010-12345678', bidderFax='010-87654321',
                 bidderAddress='通用地址可读性核验'*10, bidderWebsite='https://example.test',
                 bidDate='2026-11-01')

def base():
    doc=Document()
    section=doc.sections[0]
    section.page_width=Cm(21);section.page_height=Cm(29.7)
    section.left_margin=section.right_margin=Cm(2.5)
    section.top_margin=section.bottom_margin=Cm(2)
    doc.styles['Normal'].font.name='SimSun'
    doc.styles['Normal'].font.size=Pt(12)
    doc.styles['Normal'].paragraph_format.space_after=Pt(0)
    return doc

def line(doc,label,suffix='',size=12,group=False):
    p=doc.add_paragraph(label)
    p.paragraph_format.line_spacing=Pt(22)
    p.paragraph_format.keep_together=p.paragraph_format.keep_with_next=group
    r=p.add_run('\t');r.underline=True;r.font.size=Pt(size)
    if suffix:p.add_run(suffix)
    return p

doc=base();doc.add_paragraph('填充字号与自然换行核验',style='Heading 1')
line(doc,'投标人：','（盖单位章）',14)
line(doc,'地址：')
line(doc,'电话：');line(doc,'传真：');line(doc,'委托代理人：','（签字）')
doc.save(OUT/'readability-source.docx')
for name,fill in [('old',old._fill_word_blanks),('fixed',_fill_word_blanks)]:
    current=Document(OUT/'readability-source.docx');fill(current,brief)
    current.save(OUT/f'readability-{name}.docx')

doc=base();doc.add_paragraph('落款与联系方式间隔核验',style='Heading 1')
for n in range(6):doc.add_paragraph(f'原版正文连续性核验第{n+1}段。')
line(doc,'投标人：','（盖单位章）',group=True)
line(doc,'法定代表人：','（签字）',group=True)
gap=doc.add_paragraph();render._apply_sign_spacer_paragraph(gap,8)
for _ in range(4):
    gap=doc.add_paragraph();gap.paragraph_format.line_spacing=Cm(2)
line(doc,'电话：',group=True);line(doc,'传真：',group=True);line(doc,'日期：',group=True)
doc.save(OUT/'gap-source.docx')
short=brief.model_copy(update={'bidderName':'通用服务测试单位','bidderAddress':'通用地址'})
current=Document(OUT/'gap-source.docx');_fill_word_blanks(current,short)
render._pad_signature_blocks(current)
current.save(OUT/'gap-fixed.docx')

contacts=base();contacts.add_paragraph('原制表位与独立标签核验',style='Heading 1')
p=contacts.add_paragraph();p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
p.paragraph_format.line_spacing=Pt(24)
for row, labels in enumerate([['地　址：','网　址：'],['电　话：','传　真：']]):
    if row:p.add_run().add_break()
    for index,label in enumerate(labels):
        if index:p.add_run().add_tab()
        p.add_run(label);r=p.add_run('\t');r.underline=True;r.font.size=Pt(12)
contacts.save(OUT/'contacts-source.docx')
_fill_word_blanks(contacts,short);contacts.save(OUT/'contacts-fixed.docx')
print('Prepared 7 rendering test documents')

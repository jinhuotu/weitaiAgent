from pathlib import Path
from types import ModuleType
import sys
sys.path[:0]=['apps','packages']
from docx import Document
from docx.shared import Cm,Pt
from api.services.tenders.pdf_format import _fill_word_blanks
from api.services.tenders.schema import BidBrief

root=Path('tmp/layout-tab-fix');out=root/'visual';out.mkdir(exist_ok=True)
old=ModuleType('before_layout_fix')
exec(compile((root/'pdf_format.before.py').read_text(encoding='utf-8'),'before_layout_fix','exec'),old.__dict__)
doc=Document();doc.styles['Normal'].font.name='SimSun';doc.styles['Normal'].font.size=Pt(12)
s=doc.sections[0];s.page_width=Cm(21);s.page_height=Cm(29.7);s.left_margin=s.right_margin=Cm(2.5)
p=doc.add_paragraph();p.paragraph_format.tab_stops.add_tab_stop(Cm(8));p.paragraph_format.line_spacing=Pt(24)
for n,labels in enumerate([['地　址：','网','　址：'],['电　话：','传','　真：']]):
    if n:p.add_run().add_break()
    p.add_run(labels[0]);p.add_run('\t').underline=True
    for label in labels[1:]:p.add_run(label)
    p.add_run('\t').underline=True
doc.save(out/'columns-source.docx')
b=BidBrief(bidderAddress='通用服务地址',bidderWebsite='https://example.test',bidderPhone='010-12345678',bidderFax='010-87654321')
for name,fill in [('old',old._fill_word_blanks),('fixed',_fill_word_blanks)]:
    current=Document(out/'columns-source.docx');fill(current,b);current.save(out/f'columns-{name}.docx')
print('Prepared source / old / fixed single-tab column samples')

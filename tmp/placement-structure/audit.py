from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from PIL import Image,ImageDraw
from api.services.tenders.section_materials import append_section_materials
from api.services.tenders.schema import PlaceholderItem

root=Path('tmp/placement-structure');doc=Document()
style=doc.styles['Normal'];style.font.name='SimSun';style.element.rPr.rFonts.set(qn('w:eastAsia'),'SimSun')
slots=[];media={}
doc.add_paragraph('保证金资料',style='Heading 1');doc.add_paragraph('附：汇款凭证');doc.add_paragraph('的复印件或扫描件。');doc.add_paragraph('原版后续文字和落款保持连续。')
slots.append(PlaceholderItem(key='bond',title='汇款凭证',attachToSection='保证金资料'))
for key,title in [('license','营业执照'),('credit','信用报告')]:
    doc.add_paragraph(title,style='Heading 1');doc.add_paragraph('附：'+title+'扫描件。')
    p=root/(key+'.png');im=Image.new('RGB',(720,340),'#e6f2ff');ImageDraw.Draw(im).text((30,30),'TEST '+key.upper()+' VERIFIED SOURCE',fill='black');im.save(p)
    slots.append(PlaceholderItem(key=key,title=title,attachToSection=title));media[key]=[p]
doc.add_paragraph('已标价的工程量清单',style='Heading 1');doc.add_paragraph('请按照附件格式填写并提交。')
t=doc.add_table(rows=3,cols=2);t.style='Table Grid';t.cell(0,0).text='序号';t.cell(0,1).text='原版报价表格';t.cell(1,0).text='1';t.cell(1,1).text='原版填报位置';t.cell(2,0).text='2';t.cell(2,1).text='原版填报位置'
original=[(b,b.xml) for b in doc.element.body]
append_section_materials(doc,slots,media,[s.key for s in slots])
assert all(b.xml==xml for b,xml in original)
doc.save(root/'audit.docx')
(root/'convert.xml').write_text('<TaskQueueDataConvert><m_sFileFrom>/tmp/tender-audit-structure/audit.docx</m_sFileFrom><m_sFileTo>/tmp/tender-audit-structure/audit.pdf</m_sFileTo><m_nFormatTo>513</m_nFormatTo></TaskQueueDataConvert>',encoding='utf-8')

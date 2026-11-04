import sys,json,re
from pathlib import Path
sys.path[:0]=['apps','packages']
from docx import Document
from docx.text.paragraph import Paragraph
from api.services.tenders.schema import BidBrief,DocumentFormat
from api.services.tenders.technical_toc import sync,_regions,_instruction
from api.services.tenders.assemble import _fill_toc_pages
from api.services.tenders.native_format import validate_native_toc
root=Path('tmp/technical-plan-repair')
b=BidBrief.model_validate(json.loads((root/'baseline.json').read_text(encoding='utf-8'))['brief'])
b.technicalPackage=json.loads((root/'real-package.json').read_text(encoding='utf-8'))
report={}
for anonymous in [False,True]:
 b.techAnonymous=anonymous
 doc=Document(root/('real-anonymous.docx' if anonymous else 'real-named.docx'))
 sync(doc,b)
 _fill_toc_pages(doc,DocumentFormat(pageNumberStart='body'),subset=True)
 regions=_regions(doc)
 rows=[p for p in doc.paragraphs if 'PAGEREF' in _instruction(p._p)]
 assert len(regions)==1
 assert len(rows)==sum('PAGEREF' in _instruction(n) for n in regions[0])
 assert all(re.search(r'\t\d+$',p.text) for p in rows)
 before=doc.element.xml
 sync(doc,b)
 _fill_toc_pages(doc,DocumentFormat(pageNumberStart='body'),subset=True)
 assert before==doc.element.xml
 check=validate_native_toc(doc)
 report[str(anonymous)]={'tocCount':len(regions),'entries':len(rows),'allInsideField':True,'numericCaches':True,'idempotent':True,'nativeValidation':check}
 doc.save(root/('toc-anonymous.docx' if anonymous else 'toc-named.docx'))
Path('tmp/technical-toc-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))

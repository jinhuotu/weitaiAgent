import hashlib,json,re
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.schema import BidBrief,OutlineItem
from api.services.tenders.outline import titles_from_format_text,classify_kind
from api.services.tenders.native_format import _read_toc_entries,validate_native_toc
from api.services.tenders.pdf_format import toc_block_ids
from api.services.tenders.document import _pageref_bookmark
from api.services.tenders.generate import generate_bid
from api.services.tenders.requirement_bid import prepare_requirements_bid
from unittest.mock import patch
ROOT=Path('tmp/general-tenders/actual-invitations');ROOT.mkdir(exist_ok=True)
cases=[('construction','091a152bd8c7','青山建设有限公司','郑州项目施工投标','本次工程施工'),('medical','0f68e2b0514b','杏林医疗有限公司','河南省胸科医院车辆采购(救护车)项目','救护车采购'),('system','00b79875a04a','星河服务有限公司','精细氧化铝工业试验线建设项目包二十三数智化运营管理系统','数智化运营管理系统实施'),('goods','18b181e9dbfe','海风设备有限公司','天津市静海县环境监控与应急指挥中心建设项目','环境监控与应急指挥设备')]
reports=[]
with patch('api.services.tenders.generate.tenders_output_dir',lambda:ROOT):
 for industry,ident,company,project,scope in cases:
  src=Path('storage/tenders',ident+'.format.docx');before=hashlib.sha256(src.read_bytes()).hexdigest();doc=Document(src)
  entries=_read_toc_entries(toc_block_ids(doc),doc)
  items=[OutlineItem(id=f'src{i}',title=r['title'],rawTitle=r['title'],level=r['level'],kind=classify_kind(r['title'])) for i,r in enumerate(entries)] if entries else titles_from_format_text('\n'.join(p.text for p in doc.paragraphs))
  chapter=next((p.text for p in doc.paragraphs if re.search('(投标|响应)文件格式',p.text)), '投标文件格式')
  b=BidBrief(invitationId=ident,bidderName=company,projectName=project,bidContent=scope,tenderer='本次招标人',bidDate='2026-10-30',layoutMode='outline',formatConfirmed=True,outlineChapter=chapter,outlineItems=items,includeCommitment=False)
  prepare_notes=prepare_requirements_bid(b)
  for volume in ['business','technical','full']:
   brief=b.model_copy(deep=True);brief.generateVolume=volume
   try:
    result=generate_bid(brief,catalog_slots=[],catalog_media={},catalog_facts=[])
    filename=result['techDocxFile'] if volume=='technical' else result['docxFile'];out=Document(ROOT/filename)
    rows=[p for p in out.paragraphs if _pageref_bookmark(p)]
    names=[x.get(qn('w:name')) for x in out.element.iter(qn('w:bookmarkStart'))]
    targets={x.get(qn('w:name')) for x in out.element.iter(qn('w:bookmarkStart'))}
    assert rows and all(_pageref_bookmark(p) in targets and p.text.rsplit('\t',1)[-1].isdigit() for p in rows)
    assert len(names)==len(set(names))
    stats=validate_native_toc(out)
    assert hashlib.sha256(src.read_bytes()).hexdigest()==before
    reports.append({'industry':industry,'invitationId':ident,'volume':volume,'passed':True,'sourceTocEntries':len(entries),'resultTocEntries':len(rows),'requirementsDriven':brief.requirementsDriven,'file':filename})
   except Exception as exc:
    reports.append({'industry':industry,'invitationId':ident,'volume':volume,'passed':False,'error':str(exc)})
(ROOT/'results.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(reports,ensure_ascii=True))

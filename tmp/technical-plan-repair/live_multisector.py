"""Live-model regression on wholly synthetic, non-customer inputs."""
import asyncio,json,sys
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=['apps','packages']
from db.session import AsyncSessionLocal,engine
from api.services.models.runtime import build_llm_client
from api.services.tenders.schema import BidBrief,OutlineItem
from api.services.tenders.technical_package import draft_package
from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.native_format import validate_native_toc
from docx import Document
from docx.oxml.ns import qn

ROOT=Path('tmp/technical-plan-repair/live-multisector');ROOT.mkdir(exist_ok=True)
CASES=[
 ('goods-delivery','合成样本甲有限公司','实验仪器交付','显微成像设备与实验室检测仪器到货、安装、校准、培训及验收'),
 ('service-migration','合成样本乙有限公司','云平台迁移','应用迁移、数据备份、切换验证、回退及运维交接'),
 ('medical-maintenance','合成样本丙有限公司','诊疗设备维保','设备例行检查、性能测试、故障处置、隔离配合及维护记录交付'),
 ('unseen-mapping','合成样本丁有限公司','陌生海岸测绘','海岸遥感采集、控制点复核、成果质量检查及交付'),
]

async def main():
 engine.echo=False;report={}
 async with AsyncSessionLocal() as db:
  client=await build_llm_client(db,'pro')
  for filename,company,project,scope in CASES:
   source=Path('tests/tenders/fixtures/technical')/(filename+'.docx')
   brief=BidBrief(projectName='合成回归样本—'+project,bidderName=company,legalPersonName='合成人员',
    bidContent=scope,deliveryDays=30,layoutMode='outline',outlineChapter='第六章 投标文件格式',
    outlineItems=[OutlineItem(id='root',title='技术实施方案',kind='tech_chapter')])
   print('Generating',filename,flush=True)
   with patch('api.services.tenders.pdf_format._word_format_file',lambda _:source):
    package,notes=await draft_package(db,brief,client=client)
    (ROOT/(filename+'.json')).write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    brief.technicalPackage=package
    modes={}
    for anonymous in [False,True]:
     b=brief.model_copy(deep=True);b.techAnonymous=anonymous
     dest=ROOT/(filename+('-anonymous' if anonymous else '-named')+'.docx')
     assemble_bid_docx(b,dest,volume='technical',catalog_slots=[],catalog_media={})
     doc=Document(dest);validate_native_toc(doc)
     blob=''.join(n.text or '' for n in doc.element.iter(qn('w:t')))
     assert len(doc.inline_shapes)==2 and not list(doc.element.body.iter(qn('w:pBdr')))
     if anonymous:assert company not in blob and '合成人员' not in blob
     assert '充电桩' not in blob
     for point in package['sections']:
      heading=next(p for p in doc.paragraphs if p.text==point['title'] and 'PAGEREF' not in p._p.xml)
      assert len(''.join(heading._p.getnext().itertext()))>100
     modes[str(anonymous)]={'points':len(package['sections']),'tables':len(package['tables']),'figures':len(doc.inline_shapes),'boxes':0}
    report[filename]={'modes':modes,'notes':notes}
   (ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
   print('Passed',filename,modes,flush=True)
 await engine.dispose()

asyncio.run(main())

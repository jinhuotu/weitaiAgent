import asyncio,json,logging
from pathlib import Path
from unittest.mock import patch
from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders.generate import generate_bid
from api.services.tenders.placeholders import collect_slots
from api.services.tenders.native_format import validate_native_toc
from api.services.tenders.material_intents import recognize_material_demands
from api.services.tenders.schema import BidBrief,OutlineItem
from db.session import AsyncSessionLocal,engine

class DiagnosticClient:
    def __init__(self, inner): self.inner=inner; self.failures=[]
    async def complete(self,*args,**kwargs):
        try: return await self.inner.complete(*args,**kwargs)
        except Exception as exc:
            msg=str(exc).lower()
            category=('connection' if any(w in msg for w in ['无法连接','connection','connect']) else
                      'authorization' if any(w in msg for w in ['鉴权','密钥','401','unauthorized']) else
                      'timeout' if any(w in msg for w in ['超时','timeout']) else
                      'quota' if any(w in msg for w in ['余额','额度','quota','balance','429']) else 'provider_error')
            self.failures.append({'type':type(exc).__name__,'status':getattr(exc,'status_code',None),'category':category})
            raise

async def main():
    report=[]
    root=Path('tmp/material-intents/live-generated');root.mkdir(exist_ok=True)
    cases=[('engineer','候选人的递件包不可缺少《跨域协调纪要》及《履约资源札记》。'),
           ('goods','应答随行件由《兼容边界册》与《单体追溯页》组成，两者均须附在响应资料内。'),
           ('service','报名包中不可缺少《作业交接索引》。')]
    try:
        async with AsyncSessionLocal() as db:
            from api.services.models.runtime import build_llm_client
            model=DiagnosticClient(await build_llm_client(db,'fast'))
            for kind,text in cases:
                b=BidBrief(bidderName=kind+'回归主体有限公司',projectName=kind+'回归采购',
                    invitationId='abcdef123456',layoutMode='outline',formatConfirmed=True,
                    outlineChapter='第六章 投标文件格式',generateVolume='full',includeCommitment=False,
                    outlineItems=[OutlineItem(id='one',title='一、响应资料',kind='company')])
                await recognize_material_demands(b,text='一、响应资料\n'+text,catalog=[],db=db,client=model)
                src=root/(kind+'-source.docx');doc=Document()
                for value in ['第六章 投标文件格式','投 标 文 件','项目名称：____','投标人：____','目录','一、响应资料\t1']:
                    doc.add_paragraph(value)
                doc.add_page_break();doc.add_paragraph('一、响应资料',style='Heading 1');doc.add_paragraph(text)
                doc.save(src);before=src.read_bytes()
                scan=root/(kind+'-proof.png');Image.new('RGB',(130,90),kind=='goods' and 'blue' or 'red').save(scan)
                media={b.materialDemands[0].slotKey:[scan]} if b.materialDemands else {}
                with patch('api.services.tenders.pdf_format._word_format_file',lambda _:src),patch('api.services.tenders.generate.tenders_output_dir',lambda:root):
                    generated=generate_bid(b,catalog_slots=collect_slots(b.extraPlaceholders,include_keys=b.includeSlotKeys),catalog_media=media,catalog_facts=[])
                result=Document(root/generated['docxFile']);stats=validate_native_toc(result)
                names=[n.get(qn('w:name')) for n in result.element.iter(qn('w:bookmarkStart'))]
                assert len(names)==len(set(names)) and src.read_bytes()==before
                assert all(r['status'] in {'filled','pasted','placeholder'} for r in generated['materialCoverage']['full'])
                report.append({'case':kind,'complete':b.materialIntentComplete,
                    'titles':[d.title for d in b.materialDemands if not d.reviewRequired],
                    'reviewRequired':sum(d.reviewRequired for d in b.materialDemands),'providerFailures':model.failures.copy(),
                    'toc':stats,'sourceUnchanged':True,'bookmarksUnique':True,
                    'images':len(list(result.element.iter(qn('w:drawing')))),
                    'outcomes':[r['status'] for r in generated['materialCoverage']['full']],
                    'file':str(root/generated['docxFile'])})
    except Exception as exc:
        report.append({'errorType':type(exc).__name__})
    finally:
        await engine.dispose()
    Path('tmp/material-intents/live-model.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
asyncio.run(main())

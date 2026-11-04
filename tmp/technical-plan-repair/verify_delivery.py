"""Reproduce quantitative acceptance from saved outputs, without model calls or DB writes."""
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import json,sys
sys.path[:0]=['apps','packages']
from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.native_format import validate_native_toc
from api.services.tenders.schema import BidBrief
from api.services.tenders.technical_render import anonymous_text
import re

ROOT=Path('tmp/technical-plan-repair')

def results(path):
    cases=ET.parse(path).findall('.//testcase')
    failed={c.get('classname')+'::'+c.get('name') for c in cases if c.find('failure') is not None or c.find('error') is not None}
    skipped=sum(c.find('skipped') is not None for c in cases)
    return {'passed':len(cases)-len(failed)-skipped,'failed':len(failed),'skipped':skipped},failed

def props(node):
    return None if node is None else (node.tag,tuple(sorted(node.attrib.items())),tuple(props(c) for c in node))

baseline=json.loads((ROOT/'baseline.json').read_text(encoding='utf-8'))['brief']
package=json.loads((ROOT/'real-package.json').read_text(encoding='utf-8'))
source=Document('storage/tender-invitations/'+baseline['invitationId']+'.format.docx')
actual={}
for mode in ['named','anonymous']:
    path=ROOT/('real-'+mode+'.docx');doc=Document(path)
    blob=''.join(t.text or '' for part in doc.part.package.parts if getattr(part,'_element',None) is not None
                 for t in part._element.iter() if t.tag in {qn('w:t'),qn('a:t')})
    positions=[]
    for point in package['sections']:
        heading=next(p for p in doc.paragraphs if p.text==point['title'] and 'PAGEREF' not in p._p.xml)
        original=next(p for p in source.paragraphs if p.text==point['title'])
        assert props(heading._p.pPr)==props(original._p.pPr)
        rendered=[];node=heading._p.getnext()
        while node is not None and not any(m.get(qn('w:name'),'').startswith('toc_technical_') for m in node.iter(qn('w:bookmarkStart'))):
            rendered.append(''.join(t.text or '' for t in node.iter(qn('w:t'))));node=node.getnext()
        expected=anonymous_text(point['body'],BidBrief.model_validate(baseline)) if mode=='anonymous' else point['body']
        assert re.sub(r'\s+','',expected) in re.sub(r'\s+','',''.join(rendered))
        positions.append(list(doc.element.body).index(heading._p))
    assert positions==sorted(positions) and len(positions)==13
    filled=[sum(any(c.text.strip() for c in row.cells) for row in t.rows[1:]) for t in doc.tables]
    assert len(filled)==4 and all(n>0 for n in filled)
    identities={name:blob.count(baseline[name]) for name in ['bidderName','legalPersonName','agentName','bidderCreditCode','legalPersonIdNo','agentIdNo'] if baseline.get(name)}
    if mode=='anonymous':assert not any(identities.values())
    else:assert identities.get('bidderName',0)>0
    assert len(doc.inline_shapes)==2 and not list(doc.element.body.iter(qn('w:pBdr')))
    with ZipFile(path) as z:media=[n for n in z.namelist() if n.startswith('word/media/')]
    assert len(media)==2
    toc=validate_native_toc(doc)
    assert not any('待更新' in p.text for p in doc.paragraphs if 'PAGEREF toc_technical_tp_' in p._p.xml)
    actual[mode]={'points':13,'bodyChars':[len(s['body']) for s in package['sections']],
                  'filledRows':filled,'diagrams':2,'boxes':0,'retainedMedia':2,'identityMatches':identities,
                  'toc':toc,'originalHeadingPropertiesAndOrderPreserved':True}

target,_=results(ROOT/'last.xml')
full,failed=results(ROOT/'complete-full.xml')
_,old=results('tmp/signature-label-fix/full.xml')
live=json.loads((ROOT/'live-multisector/report.json').read_text(encoding='utf-8'))
assert len(live)==4
report={'actualProject':actual,'liveModelCases':{'actualProject':1,'syntheticUnseenProjects':list(live)},
        'testsTendersAndDrawingRegression':target,'fullRegression':full,'newFailures':sorted(failed-old),
        'resolvedHistoricalFailures':sorted(old-failed),
        'businessBaseline':json.loads((ROOT/'business-baseline.json').read_text(encoding='utf-8')),
        'backendRestarted':False,
        'changedFunctions':{
          'technical_package.py':['structure','draft_package','validate','scoped_inputs'],
          'technical_render.py':['render','sync_toc','anonymous_text','prune_unreferenced_images'],
          'records.py':['_prepare_and_generate'],
          'assemble.py':['assemble_bid_docx'],
          'schema.py':['BidBrief.technicalPackage'],
          'material_intents.py':['coverage'],
          'pdf_format.py':['scrub_anonymous_docx']}}
(ROOT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in {'actualProject','resolvedHistoricalFailures','changedFunctions'}},ensure_ascii=False,indent=2))
assert not report['newFailures']

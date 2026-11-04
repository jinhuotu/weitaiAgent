import json,random,sys
from pathlib import Path
from hashlib import sha256
from copy import deepcopy
from datetime import datetime,timezone
from collections import Counter
from zipfile import ZipFile
from lxml import etree
from api.services.tender_package.goods_ooxml import W,cell_text
from api.services.tender_package.goods_inputs import compile_inputs
from api.services.tender_package.goods_audit import build_review_catalog
from api.services.tender_package.web import PackageStore
run=Path(Path('tmp/current-offer-test-run.txt').read_text(encoding='utf-8'))
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
plan=read(run/'excel-test-plan.json');old=read(plan['state']);session=read(run/'inputs-test-001.json');result=read(run/'draft-test-only'/'result.json')
assert len(session['offer_history'])==769
assert len({(e['solidified_id'],e['target']) for e in session['offer_history']})==769
assert all(e['actor']=='admin' and e['basis']==plan['basis'] and e['status']=='响应' and e['confirmed_at'] for e in session['offer_history'])
assert sha256(Path(plan['source']).read_bytes()).hexdigest()==plan['source_sha256']
assert sha256(Path(plan['state']).read_bytes()).hexdigest()==plan['state_sha256']
compiled=compile_inputs(session,old['ssot']);catalog=build_review_catalog(old['ssot'],session)
assert not compiled['price_fills'] and catalog['pending_count']==0 and catalog['automatic_count']==769
assert len(result['technical'])==10 and len(result['packages'])==1 and result['packages'][0]['key']=='bid_1'
assert sum(len(t['rows']) for t in result['technical'])==769
assert all(r['status']=='响应' and r['written'] and r['guarantee'] for t in result['technical'] for r in t['rows'])
assert all(Path(a['path']).parts[0]=='bid_1' and sha256((run/'draft-test-only'/a['path']).read_bytes()).hexdigest()==a['sha256'] for a in result['artifacts'])
visual=result['visual_validation'];assert all(d['status']=='rendered_pending_review' and d['pages'] for d in visual['documents'])
sample_ids=random.Random(20261104).sample(sorted(t['solidified_id'] for t in result['technical']),3);checks=[]
for code in sample_ids:
    tech=next(t for t in result['technical'] if t['solidified_id']==code)
    with ZipFile(tech['file']) as z:document=etree.fromstring(z.read('word/document.xml'),etree.XMLParser(resolve_entities=False,no_network=True))
    tables=document.find(W+'body').findall(W+'tbl')
    for row in tech['rows']:
        position=row['target'];cell=cell_text(tables[position['table']-1].findall(W+'tr')[position['row']-1].findall(W+'tc')[position['column']-1])
        assert cell==row['guarantee'] or cell==row['guarantee']+'（响应）',(code,position,cell,row['guarantee'])
    checks.append(dict(solidified_id=code,rows_verified=len(tech['rows']),file=tech['file']))
# Verify the unchanged production-source baseline retained from the preceding CLI run.
previous=Path(old['cli_correction']['run'])/'protected-code-hashes.json'
assert all(sha256(Path(p).read_bytes()).hexdigest()==h for p,h in read(previous).items())
summary=dict(test_only=True,notice=plan['basis'],test_excel=plan['output'],test_excel_sha256=sha256(Path(plan['output']).read_bytes()).hexdigest(),
    offers=769,counts=dict(Counter(e['solidified_id'] for e in session['offer_history'])),technical_pending_before=769,technical_pending_after=0,
    technical_responses=769,prices_filled=0,technical_files=10,business_files=len(result['business']),price_templates=len(result['price']),
    rendered_documents=len(visual['documents']),rendered_pages=sum(len(d['pages']) for d in visual['documents']),visual_accepted=False,
    sampled_documents=checks,code_unchanged=True,ssot_pending=len(old['ssot']['pending']),ssot_conflicts=len(old['ssot']['conflicts']))
repo=PackageStore('storage',1)
with repo.lock(old['id'],old['revision']) as (folder,current):
    updated=deepcopy(current);updated.setdefault('inputs_history',[]).append(current['inputs']);updated.setdefault('result_history',[]).append(current['result'])
    updated.update(inputs=session,result=result,stage='generate',status='review_required',name=current['name']+'【测试占位数据，禁止用于投标】')
    updated['test_run']=dict(test_only=True,basis=plan['basis'],original_state=plan['state'],run=str(run),test_excel=plan['output'])
    updated['history'].append(dict(operation='cli_excel_test_offers',actor='admin',basis=plan['basis'],at=datetime.now(timezone.utc).isoformat(),count=769,exact_mapping=str(run/'exact-offer-map.json')))
    repo.save(folder,updated);summary['state']=str(folder/f"state-{updated['revision']:08d}.json")
with (run/'validation-test-only.json').open('x',encoding='utf-8') as f:json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False))

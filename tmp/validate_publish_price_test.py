import json,random
from pathlib import Path
from hashlib import sha256
from copy import deepcopy
from datetime import datetime,timezone
from zipfile import ZipFile
from lxml import etree
from collections import Counter
from api.services.tender_package.goods_inputs import compile_inputs
from api.services.tender_package.goods_ooxml import W,cell_text
from api.services.tender_package.web import PackageStore
run=Path(Path('tmp/current-price-test-run.txt').read_text(encoding='utf-8'))
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
context=read(run/'context.json');old=read(context['state']);session=read(run/'inputs-price-test-001.json');targets=read(run/'price-targets.json')
result=read(run/'draft-test-only'/'result.json');history=session['price_history'];assert len(history)==193
assert session['offer_history']==old['inputs']['offer_history'] and len(session['offer_history'])==769
keys=lambda rows:{tuple(r[k] for k in ('document_id','table','row','column')) for r in rows}
assert keys(history)==keys(targets) and len(keys(history))==193
assert all(e['text']=='100.00' and e['currency']=='人民币' and e['unit']=='元' and e['package_key']=='bid_1' and e['actor']=='admin' and e['basis']==context['basis'] and e['confirmed_at'] for e in history)
compiled=compile_inputs(session,old['ssot']);assert len(compiled['price_fills'])==193
prices=result['price'];assert len(prices)==4 and {p['table'] for p in prices}=={t['table'] for t in targets}
rejected=[]
for price in prices:
    assert price['proof']['applied'] and price['proof']['structure_verified']
    with ZipFile(price['file']) as z:table=etree.fromstring(z.read('word/document.xml'),etree.XMLParser(resolve_entities=False,no_network=True)).find(W+'body').find(W+'tbl')
    for change in price['proof']['applied']:
        actual=cell_text(table.findall(W+'tr')[change['row']-1].findall(W+'tc')[change['column']-1])
        assert actual=='100.00'
    rejected.extend(dict(document=price['source_document'],**c) for c in price['proof']['rejected'])
assert sum(len(p['proof']['applied'])+len(p['proof']['rejected']) for p in prices)==193
sample=random.Random(20261104).choice(prices)
assert all(Path(a['path']).parts[0]=='bid_1' and sha256((run/'draft-test-only'/a['path']).read_bytes()).hexdigest()==a['sha256'] for a in result['artifacts'])
visual=result['visual_validation'];assert len(visual['documents'])==15 and all(d['status']=='rendered_pending_review' and d['pages'] for d in visual['documents'])
assert all(sha256(Path(p).read_bytes()).hexdigest()==h for p,h in read(run/'code-hashes.json').items())
assert sha256(Path(context['state']).read_bytes()).hexdigest()==context['state_sha256']
summary=dict(test_only=True,notice=context['basis'],real_reconciliation=False,price_history=193,
    requested_by_table=context['table_counts'],title_distribution=context['title_counts'],
    written_by_table={str(p['table']):len(p['proof']['applied']) for p in prices},
    written_total=sum(len(p['proof']['applied']) for p in prices),protected_rejected=rejected,
    randomly_read_back_table=sample['table'],randomly_read_back_file=sample['file'],randomly_read_back_cells=len(sample['proof']['applied']),
    all_four_tables_read_back=True,technical_files=len(result['technical']),business_files=len(result['business']),price_files=4,
    rendered_documents=15,rendered_pages=sum(len(d['pages']) for d in visual['documents']),visual_accepted=False,
    offer_history_unchanged=True,backend_frontend_legacy_code_unchanged=True,full_regression_run=False)
repo=PackageStore('storage',1)
with repo.lock(old['id'],old['revision']) as (folder,current):
    updated=deepcopy(current)
    updated.setdefault('inputs_history',[]).append(current['inputs']);updated.setdefault('result_history',[]).append(current['result'])
    updated.update(inputs=session,result=result,stage='generate',status='review_required')
    updated.setdefault('test_run',{})['price_test']=dict(test_only=True,basis=context['basis'],run=str(run),original_state=context['state'],real_reconciliation=False)
    updated['history'].append(dict(operation='cli_price_test',actor='admin',basis=context['basis'],at=datetime.now(timezone.utc).isoformat(),requested=193,written=summary['written_total'],protected_rejected=rejected))
    repo.save(folder,updated);summary['state']=str(folder/f"state-{updated['revision']:08d}.json")
with (run/'validation-test-only.json').open('x',encoding='utf-8') as f:json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False))

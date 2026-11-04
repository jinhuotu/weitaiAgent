"""Test only. Run .venv Python with PYTHONPATH=apps;packages."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime
from collections import Counter
import json
from api.services.tender_package.goods_review import price_targets
from api.services.tender_package.goods_inputs import confirm_price,compile_inputs
job=Path('storage/tender-packages/1/32033926583940a88136054a155b4aa9').resolve()
state_path=sorted(job.glob('state-*.json'))[-1];state=json.loads(state_path.read_text(encoding='utf-8'))
assert state_path.name=='state-00000042.json'
session=state['inputs'];offers=json.loads(json.dumps(session['offer_history']))
assert len(offers)==769 and not session['price_history']
packages=session['package_history'][-1]['packages'];assert len(packages)==1 and packages[0]['key']=='bid_1' and len(packages[0]['material_ids'])==10
targets=price_targets(state['ssot'],job/state['root'])
assert len(targets)==len({tuple(t[k] for k in ('document_id','table','row','column')) for t in targets})==193
run=job/('price-test-'+datetime.now().strftime('%Y%m%d-%H%M%S'));run.mkdir(exist_ok=False)
def save(name,data):
    with (run/name).open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
save('inputs-original.json',session);save('price-targets.json',targets)
protected=['apps/api/services/tender_package/'+n for n in ('goods_inputs.py','goods_review.py','goods_adapter.py','goods_ooxml.py','goods_render.py','goods_workflow.py','unpack.py','ssot.py')]
protected+=['apps/api/routers/tenders.py','apps/api/services/tenders/generate.py',r'E:\code\python\weitaiAgentui\src\views\TendersView.vue',r'E:\code\python\weitaiAgentui\src\views\TenderPackageView.vue']
save('code-hashes.json',{p:sha256(Path(p).read_bytes()).hexdigest() for p in protected})
basis='测试流程占位报价，非真实价格，禁止用于投标'
for target in targets:
    session=confirm_price(session,package_key='bid_1',document_id=target['document_id'],table=target['table'],row=target['row'],column=target['column'],
                          amount='100.00',currency='人民币',unit='元',actor='admin',basis=basis)
assert len(session['price_history'])==193 and session['offer_history']==offers
assert len(compile_inputs(session,state['ssot'])['price_fills'])==193
save('inputs-price-test-001.json',session)
save('context.json',dict(state=str(state_path),state_sha256=sha256(state_path.read_bytes()).hexdigest(),run=str(run),basis=basis,
    table_counts=dict(Counter(str(t['table']) for t in targets)),title_counts=dict(Counter(t['title'] for t in targets))))
Path('tmp/current-price-test-run.txt').write_text(str(run),encoding='utf-8')
print(json.dumps(dict(run=str(run),prices=193,tables=dict(Counter(str(t['table']) for t in targets))),ensure_ascii=False))

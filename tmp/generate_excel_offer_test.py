import sys,json
from pathlib import Path
sys.path[:0]=['apps','packages'];sys.path.append(str(Path('.venv/Lib/site-packages').resolve()))
from api.services.tender_package.goods_inputs import compile_inputs
from api.services.tender_package.goods_adapter import generate_goods
run=Path(Path('tmp/current-offer-test-run.txt').read_text(encoding='utf-8'));plan=json.loads((run/'excel-test-plan.json').read_text(encoding='utf-8'))
state=json.loads(Path(plan['state']).read_text(encoding='utf-8'));session=json.loads((run/'inputs-test-001.json').read_text(encoding='utf-8'))
options=compile_inputs(session,state['ssot']);assert sum(map(len,options['offers'].values()))==769 and not options['price_fills']
options['input_provenance']['warnings'].append(dict(type='test_data_only',level='critical',message=plan['basis'],source=str(run/'inputs-test-001.json')))
result=generate_goods(state['ssot'],Path(plan['state']).parent/state['root'],run/'draft-test-only',**options)
print(json.dumps(dict(result=str(run/'draft-test-only'/'result.json'),technical=len(result['technical']),pages=sum(len(d['pages']) for d in result['visual_validation']['documents']),pending=sum(r['status']=='待确认' for t in result['technical'] for r in t['rows'])),ensure_ascii=False))

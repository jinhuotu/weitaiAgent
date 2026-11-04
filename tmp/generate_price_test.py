import sys,json
from pathlib import Path
sys.path[:0]=['apps','packages'];sys.path.append(str(Path('.venv/Lib/site-packages').resolve()))
from api.services.tender_package.goods_inputs import compile_inputs
from api.services.tender_package.goods_adapter import generate_goods
run=Path(Path('tmp/current-price-test-run.txt').read_text(encoding='utf-8'))
context=json.loads((run/'context.json').read_text(encoding='utf-8'));state=json.loads(Path(context['state']).read_text(encoding='utf-8'))
session=json.loads((run/'inputs-price-test-001.json').read_text(encoding='utf-8'));options=compile_inputs(session,state['ssot'])
assert len(options['price_fills'])==193
options['input_provenance']['warnings'].extend([dict(type='test_price_only',level='critical',source=str(run/'inputs-price-test-001.json'),message=context['basis']),
    dict(type='test_price_no_reconciliation',level='critical',message='本次仅测试链路；单价、合价、含税报价统一100.00，未做真实勾稽、万元换算或税费计算'),
    dict(type='test_guarantees_only',level='critical',message='所沿用769条保证值亦为测试占位数据，禁止用于投标')])
result=generate_goods(state['ssot'],Path(context['state']).parent/state['root'],run/'draft-test-only',**options)
print(json.dumps(dict(result=str(run/'draft-test-only'/'result.json'),price_tables=[dict(table=p['table'],applied=len(p['proof']['applied']),rejected=len(p['proof']['rejected'])) for p in result['price']],
    rendered_documents=len(result['visual_validation']['documents']),pages=sum(len(d['pages']) for d in result['visual_validation']['documents'])),ensure_ascii=False))

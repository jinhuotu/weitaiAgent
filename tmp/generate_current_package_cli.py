import sys,json
from pathlib import Path
sys.path[:0]=['apps','packages'];sys.path.append(str(Path('.venv/Lib/site-packages').resolve()))
from api.services.tender_package.goods_inputs import compile_inputs
from api.services.tender_package.goods_adapter import generate_goods
run=Path(Path('tmp/current-package-cli-run.txt').read_text(encoding='utf-8'))
context=json.loads((run/'run-context.json').read_text(encoding='utf-8'))
session=json.loads((run/'inputs-001.json').read_text(encoding='utf-8'))
options=compile_inputs(session,run/'ssot-complete.json')
assert not options['offers'] and not options['price_fills']
result=generate_goods(run/'ssot-complete.json',context['root'],run/'draft',**options)
print(json.dumps(dict(technical=len(result['technical']),business=len(result['business']),price=len(result['price']),rendered=[dict(file=Path(d['file']).name,status=d['status'],pages=len(d['pages'])) for d in result['visual_validation']['documents']]),ensure_ascii=False),flush=True)

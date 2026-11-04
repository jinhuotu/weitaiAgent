"""Run with PYTHONPATH=apps;packages and .venv Python. Test offers only."""
import json,re,sys
from pathlib import Path
from hashlib import sha256
from collections import defaultdict,Counter
from openpyxl import load_workbook
from api.services.tender_package.goods_inputs import confirm_offer,compile_inputs

run=Path(Path('tmp/current-offer-test-run.txt').read_text(encoding='utf-8'));plan=json.loads((run/'excel-test-plan.json').read_text(encoding='utf-8'))
state=json.loads(Path(plan['state']).read_text(encoding='utf-8'));session=state['inputs'];original=json.loads(json.dumps(session))
assert sha256(Path(plan['state']).read_bytes()).hexdigest()==plan['state_sha256']
assert sha256(Path(plan['source']).read_bytes()).hexdigest()==plan['source_sha256']
index=defaultdict(list)
for t in session['targets']:index[(t['solidified_id'],str(t['cells'][0]).strip())].append(t)
wb=load_workbook(plan['output'],read_only=True,data_only=False);used=set();mapping=[]
for sheet in wb:
    ids=re.findall(r'\d{3,8}(?:-\d{3,12}){1,3}',str(sheet['A1'].value or ''))
    if not ids:continue
    assert len(set(ids))==1;code=ids[0]
    for cells in sheet.iter_rows(min_row=3):
        serial=str(cells[0].value).strip();targets=index[(code,serial)]
        assert len(targets)==1,(code,serial)
        t=targets[0];key=(code,t['target']);assert key not in used;used.add(key)
        assert [str(c.value or '').strip() for c in cells[1:4]]==[str(v or '').strip() for v in t['cells'][1:4]]
        value=cells[4].value;assert isinstance(value,str) and value.strip()
        assert cells[5].value==plan['basis']
        session=confirm_offer(session,code,t['target'],value,actor='admin',basis=plan['basis'],status='响应')
        mapping.append(dict(solidified_id=code,serial=serial,target=t['target'],sheet=sheet.title,cell=cells[4].coordinate,guarantee=value))
        if len(mapping)%100==0:print('Confirmed test offers',len(mapping),flush=True)
wb.close()
assert len(used)==len(session['targets'])==769
assert not original['offer_history'] and len(session['offer_history'])==769
assert session['price_history']==original['price_history']==[]
with (run/'inputs-test-001.json').open('x',encoding='utf-8') as f:json.dump(session,f,ensure_ascii=False,indent=2)
with (run/'exact-offer-map.json').open('x',encoding='utf-8') as f:json.dump(mapping,f,ensure_ascii=False,indent=2)
with (run/'ssot.json').open('x',encoding='utf-8') as f:json.dump(state['ssot'],f,ensure_ascii=False,indent=2)
print(json.dumps(dict(offers=len(mapping),counts=dict(Counter(m['solidified_id'] for m in mapping)),output=str(run/'inputs-test-001.json')),ensure_ascii=False))

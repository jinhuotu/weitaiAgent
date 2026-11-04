from pathlib import Path
from hashlib import sha256
from datetime import datetime
import json,re
from collections import defaultdict
from openpyxl import load_workbook
job=Path('storage/tender-packages/1/32033926583940a88136054a155b4aa9').resolve()
state_path=sorted(job.glob('state-*.json'))[-1];s=json.loads(state_path.read_text(encoding='utf-8'))
assert state_path.name=='state-00000041.json' and len(s['inputs']['targets'])==769 and not s['inputs']['offer_history']
run=job/('offer-test-'+datetime.now().strftime('%Y%m%d-%H%M%S'));run.mkdir(exist_ok=False)
source=Path(r'C:\Users\mouzh\Doubao\chats\2026-10-23\new-chat\产品参数表_填写模板.xlsx')
basis='测试流程占位数据，非真实保证值，禁止用于投标'
index=defaultdict(list)
for t in s['inputs']['targets']:index[(t['solidified_id'],str(t['cells'][0]).strip())].append(t)
w=load_workbook(source,read_only=False,data_only=False);sheets=[];matches=[]
for sh in w:
    ids=re.findall(r'\d{3,8}(?:-\d{3,12}){1,3}',str(sh['A1'].value or ''))
    if not ids:continue
    assert len(set(ids))==1;code=ids[0];edits=[]
    for row in sh.iter_rows(min_row=3):
        serial=str(row[0].value).strip();found=index[(code,serial)];assert len(found)==1,(code,serial)
        target=found[0];actual=[str(c.value or '').strip() for c in row[1:4]]
        assert actual==[str(x or '').strip() for x in target['cells'][1:4]]
        assert row[4].value is None
        demand=str(row[3].value);value='测试占位值' if demand.strip().replace('（','(').replace('）',')')=='(投标人提供)' else demand
        assert value.strip();edits.append([value,basis])
        matches.append(dict(solidified_id=code,serial=serial,target=target['target'],sheet=sh.title,cell=row[4].coordinate,value=value,basis=basis,actor='admin',status='响应'))
    sheets.append(dict(name=sh.title,code=code,range=f'E3:F{sh.max_row}',values=edits))
w.close();assert len(matches)==769 and len({(m['solidified_id'],m['target']) for m in matches})==769
context=dict(run=str(run),source=str(source),source_sha256=sha256(source.read_bytes()).hexdigest(),state=str(state_path),state_sha256=sha256(state_path.read_bytes()).hexdigest(),
    output=str(run/'产品参数表_填写模板_测试已填.xlsx'),sheets=sheets,matches=matches,basis=basis)
(run/'excel-test-plan.json').write_text(json.dumps(context,ensure_ascii=False,indent=2),encoding='utf-8')
Path('tmp/current-offer-test-run.txt').write_text(str(run),encoding='utf-8')
print(str(run))

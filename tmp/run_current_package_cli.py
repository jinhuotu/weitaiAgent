"""One-task execution only: no production source changes or invented business inputs."""
import sys,json,os,subprocess,shutil
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
from copy import deepcopy
sys.path[:0]=['apps','packages']
from api.services.tender_package.web import PackageStore
from api.services.tender_package.recognition import recognize
from api.services.tender_package.classify import classify
from api.services.tender_package.closure import build_final_mapping
from api.services.tender_package.ssot import build_ssot,write_ssot
repo=PackageStore('storage',1)
latest=max((p for d in repo.root.iterdir() if d.is_dir() for p in sorted(d.glob('state-*.json'))[-1:]),key=lambda p:p.stat().st_mtime_ns)
s=json.loads(latest.read_text(encoding='utf-8'));original_hash=sha256(latest.read_bytes()).hexdigest()
folder=repo.folder(s['id']);root=folder/s['root']
run=folder/('cli-run-'+datetime.now().strftime('%Y%m%d-%H%M%S'));run.mkdir(exist_ok=False)
def save(name,data):
    with (run/name).open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
shutil.copy2(latest,run/'original-state.json');save('original-inputs.json',s['inputs'])
protected=['apps/api/services/tender_package/'+x for x in ('unpack.py','classify.py','composed.py','completion.py','ssot.py','goods_adapter.py','goods_inputs.py','goods_workflow.py','goods_render.py')]
protected+=['apps/api/routers/tenders.py','apps/api/services/tenders/generate.py',r'E:\code\python\weitaiAgentui\src\views\TendersView.vue']
save('protected-code-hashes.json',{p:sha256(Path(p).read_bytes()).hexdigest() for p in protected})
automatic=classify(s['manifest'],root);overrides=deepcopy(s.get('role_overrides',{}));restored=[]
for f in automatic['files']:
    if f['role']=='tech_spec' and overrides.get(f['storage_path'],{}).get('role')=='other':
        restored.append(dict(path=f['logical_path'],old=overrides.pop(f['storage_path']),new_role='tech_spec',evidence=f['role_evidence']))
classified=recognize(s['manifest'],root,overrides=overrides,previous=s['classified'])
final=build_final_mapping(classified,root);model=build_ssot(classified,root,final)
ids=sorted(m.solidified_id for m in model.materials)
assert len(ids)==len(set(ids))==10
save('classified-complete.json',classified);save('mapping-complete.json',final);write_ssot(model,run/'ssot-complete.json')
# The user explicitly states no real quotes/offers have been supplied. Old entries remain in original-inputs.json.
save('withheld-old-prices.json',dict(reason='用户本轮明确尚未提供实际报价；不将历史12000视为本次真实报价',records=s['inputs']['price_history']))
venv=str(Path('.venv/Scripts/python.exe').resolve());env=dict(os.environ,PYTHONPATH='apps;packages',PYTHONIOENCODING='utf-8')
def workflow(operation,args,out,prior=None):
    request=operation+'-request.json';save(request,dict(operation=operation,arguments=args))
    cmd=[venv,'-m','api.services.tender_package.goods_workflow','--ssot',str(run/'ssot-complete.json'),'--request',str(run/request),'--output',str(run/out)]
    if prior:cmd+=['--input',str(run/prior)]
    subprocess.run(cmd,env=env,check=True)
workflow('create',{},'inputs-000.json')
prior_lot=s['inputs']['package_history'][-1]['packages'][0]['lot_key']
lot=next(l for l in model.lots if l.key==prior_lot)
assert '10kV电力电缆' in lot.fields['lot_name'].value
package=dict(key='bid_1',package_code='包1',lot_key=lot.key,material_ids=ids)
workflow('packages',dict(packages=[package],actor='admin',basis='人工确认10个物料均属包1'),'inputs-001.json','inputs-000.json')
save('run-context.json',dict(task_id=s['id'],original_state=str(latest.resolve()),original_hash=original_hash,run=str(run.resolve()),root=str(root.resolve()),restored=restored,package=package,lot_name=lot.fields['lot_name'].value,lot_code=lot.fields['lot_code'].value))
Path('tmp/current-package-cli-run.txt').write_text(str(run.resolve()),encoding='utf-8')
print(json.dumps(dict(run=str(run),ids=ids,restored_files=len(restored),package=package),ensure_ascii=False))

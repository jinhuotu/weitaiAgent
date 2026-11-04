import sys,json
from pathlib import Path
from copy import deepcopy
from hashlib import sha256
from datetime import datetime,timezone
sys.path[:0]=['apps','packages']
from api.services.tender_package.web import PackageStore
from api.services.tender_package.goods_inputs import compile_inputs
from api.services.tender_package.goods_audit import build_review_catalog
run=Path(Path('tmp/current-package-cli-run.txt').read_text(encoding='utf-8'))
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def save(name,data):
    with (run/name).open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
c=read(run/'run-context.json');old=read(run/'original-state.json');session=read(run/'inputs-001.json');model=read(run/'ssot-complete.json')
result=read(run/'draft'/'result.json');compiled=compile_inputs(session,model);catalog=build_review_catalog(model,session)
ids=sorted(m['solidified_id'] for m in model['materials']);p=compiled['packages']
assert len(p)==1 and p[0]['key']=='bid_1' and p[0]['package_code']=='包1'
assert sorted(p[0]['material_ids'])==ids and len(set(ids))==len(ids)==10
assert not compiled['offers'] and not compiled['price_fills'] and not session['price_history'] and not session['offer_history']
assert len(result['technical'])==10 and all(row['status']=='待确认' and row['guarantee'] is None for t in result['technical'] for row in t['rows'])
assert all(not item.get('reviewed_fills') for item in result['price'])
for a in result['artifacts']:
    assert Path(a['path']).parts[0]=='bid_1' and sha256((run/'draft'/a['path']).read_bytes()).hexdigest()==a['sha256']
documents=result['visual_validation']['documents']
assert len(documents)==len(result['artifacts']) and all(d['status']=='rendered_pending_review' and d['pages'] for d in documents)
assert all(sha256(Path(page['path']).read_bytes()).hexdigest()==page['sha256'] for d in documents for page in d['pages'])
assert all(sha256(Path(path).read_bytes()).hexdigest()==value for path,value in read(run/'protected-code-hashes.json').items())
requirements=[]
for code in ids:
    material=next(m for m in model['materials'] if m['solidified_id']==code)
    rows=[r for r in catalog['pending'] if r['solidified_id']==code]
    requirements.append(dict(solidified_id=code,material_name=material['material_mapping']['material_name'],
        guarantee_fields=[dict(field=r['label'],unit=r['unit'],target=r['target'],required_value=r['requirement'],document=r['source_document'],source=r['source'],status='待用户提供真实保证值、来源文件/行号或确认人及依据') for r in rows],
        actual_price_fields=['实际未税单价','币种','计价单位','税率/税费','本次实际数量','适用报价原表及回填位置','报价确认人及批准依据']))
data=dict(package=p[0],materials=requirements,guarantee_count=catalog['pending_count'],
    common_pending=['真实资质/资格/承诺资料及本次选用文件','适用商务原表与有依据的填充内容','本次采用的报价表与空白价格列位置','供应商产品参数证据及逐项响应/偏离核验','平台容量与格式限制','新草稿逐页视觉验收'],
    withheld_history=read(run/'withheld-old-prices.json'),ssot_pending=model['pending'],ssot_conflicts=model['conflicts'])
save('required-real-data.json',data)
lines=['# 仍需用户提供的真实数据','',f"标包：bid_1 / 包1；分标：{c['lot_name']}（{c['lot_code']}）。",'',
    f"共有{catalog['pending_count']}个技术参数目标未提供真实保证值；未填入任何实际报价。逐项字段、单位、原文位置见 required-real-data.json。",'',
    '历史12000报价只保留审计，不作为本次实际报价使用。','']
for item in requirements:
    lines += [f"## {item['solidified_id']}",item['material_name'],'',f"- 保证值：{len(item['guarantee_fields'])}项，需提供实际值、单位、来源文件/行号或具名确认依据。",'- 参数字段：'+'；'.join(dict.fromkeys(r['field'] for r in item['guarantee_fields'])),'- 报价字段：'+'；'.join(item['actual_price_fields']), '']
lines+=['## 共同待补内容',*['- '+v for v in data['common_pending']],'','仅完成标包结构、生成和渲染检查；视觉状态为 rendered_pending_review，不代表可投标。']
(run/'required-real-data.md').write_text('\n'.join(lines),encoding='utf-8')
repo=PackageStore('storage',1)
with repo.lock(c['task_id'],old['revision']) as (folder,current):
    assert sha256(Path(c['original_state']).read_bytes()).hexdigest()==c['original_hash']
    updated=deepcopy(current)
    for field in ('ssot','mapping','classified','inputs','result'):
        updated.setdefault(field+'_history',[]).append(deepcopy(current[field]))
    updated.update(ssot=model,mapping=read(run/'mapping-complete.json'),classified=read(run/'classified-complete.json'),inputs=session,result=result,status='review_required',stage='generate')
    for restored in c['restored']:
        f=next(f for f in updated['classified']['files'] if f['logical_path']==restored['path']);updated['role_overrides'].pop(f['storage_path'],None)
    updated.pop('error',None)
    updated['history'].append(dict(operation='cli_scope_correction',actor='admin',basis='人工确认10个物料均属包1',at=datetime.now(timezone.utc).isoformat(),run=str(run),
        restored_files=c['restored'],withheld_price_history=current['inputs']['price_history'],note='通过既有识别/汇聚函数恢复遗漏两份技术规范；goods_workflow新会话及packages调用；不沿用未提供的报价或保证值'))
    updated['cli_correction']=dict(run=str(run),required_real_data=str(run/'required-real-data.json'),source_state=c['original_state'])
    repo.save(folder,updated)
summary=dict(state=str(repo.folder(c['task_id'])/f"state-{updated['revision']:08d}.json"),package=p[0],lot_name=c['lot_name'],lot_code=c['lot_code'],
    technical=len(result['technical']),business=len(result['business']),price=len(result['price']),rendered_documents=len(documents),rendered_pages=sum(len(d['pages']) for d in documents),
    guarantee_pending=catalog['pending_count'],prices_filled=0,ssot_pending=len(model['pending']),ssot_conflicts=len(model['conflicts']),visual_status=result['visual_validation']['visual_status'],all_code_unchanged=True)
save('validation-summary.json',summary);print(json.dumps(summary,ensure_ascii=False))

"""Prepare trusted local deliverables; API claims them for the authenticated owner."""
from pathlib import Path
from hashlib import sha256
from copy import deepcopy
import argparse,json,shutil
from uuid import uuid4
from .web import read
from .ssot import read_ssot,digest


def prepare(storage,manifest,root,ssot,inputs,result=None):
    model=read_ssot(ssot);session=read(inputs);inventory=read(manifest)
    if session['ssot_sha256']!=digest(model.model_dump()):raise ValueError('输入会话与SSOT不一致')
    token=uuid4().hex;base=Path(storage).resolve()/'tender-package-imports'/token
    base.mkdir(parents=True,exist_ok=False);unpacked=base/'unpacked';unpacked.mkdir()
    for f in inventory['files']:
        source=(Path(root)/f['storage_path']).resolve();target=(unpacked/f['storage_path']).resolve()
        if not source.is_relative_to(Path(root).resolve()) or not target.is_relative_to(unpacked.resolve()):raise ValueError('迁移来源路径越界')
        if sha256(source.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('迁移文件哈希不符')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    # Keep the entire prior ledger as an immutable audit source.
    shutil.copy2(inputs,base/'original-inputs.json')
    for event in [*session.get('product_history',[]),*session.get('quote_source_history',[])]:
        original=Path(event['path'])
        if sha256(original.read_bytes()).hexdigest()!=event['sha256']:raise ValueError('供应商或报价来源已变更')
        name=event['sha256']+original.suffix.lower();target=base/name
        if not target.exists():shutil.copy2(original,target)
        event['path']='bundle-source:'+name
    state=dict(name='本地交付成果导入',status='review_required',manifest=inventory,root='unpacked',
        classified=model.module2_mapping['recognition_snapshot'],mapping=model.module2_mapping,
        ssot=model.model_dump(),inputs=session,uploads={},history=[],handoff=dict(source_ssot_sha256=sha256(Path(ssot).read_bytes()).hexdigest(),
        original_inputs_sha256=sha256((base/'original-inputs.json').read_bytes()).hexdigest()))
    if result:
        report=read(result);source_base=Path(report['technical'][0]['file']).parents[2].resolve();draft=base/'draft';draft.mkdir()
        for artifact in report['artifacts']:
            src=(source_base/artifact['path']).resolve();target=(draft/artifact['path']).resolve()
            if not src.is_relative_to(source_base) or not target.is_relative_to(draft):raise ValueError('产物路径越界')
            if sha256(src.read_bytes()).hexdigest()!=artifact['sha256']:raise ValueError('产物哈希不符')
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
        for kind in ('technical','business','price'):
            for item in report[kind]:item['file']='bundle-artifact:'+str(Path(item['file']).resolve().relative_to(source_base))
        report.setdefault('visual_history',[]).append(deepcopy(report['visual_validation']))
        report['visual_validation']=dict(structure_only=True,visual_status='not_accepted',documents=[],reviews=[],message='迁移后保留旧视觉核验历史；正式任务仍须逐页复核')
        report['ready_for_submission']=False;state['result']=report
    # Inventory of every trusted prepared byte, verified again on API import.
    state['handoff']['files']={str(p.relative_to(base)):sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()}
    (base/'prepared.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    return token


def claim(repo,token,actor,basis):
    import re
    if not re.fullmatch(r'[a-f0-9]{32}',token) or not isinstance(basis,str) or not basis.strip():raise ValueError('须提供合法导入包ID和依据')
    source=repo.root.parent.parent/'tender-package-imports'/token
    if not source.is_dir() or source.is_symlink():raise FileNotFoundError('预备交付包不存在')
    state=read(source/'prepared.json')
    for name,expected in state['handoff']['files'].items():
        path=(source/name).resolve()
        if not path.is_relative_to(source.resolve()) or sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('交付包文件缺失或哈希变化')
    fresh=repo.create(state['name']);folder=repo.folder(fresh['id'])
    for item in source.iterdir():
        target=folder/item.name
        if item.is_dir():shutil.copytree(item,target)
        else:shutil.copy2(item,target)
    for event in [*state['inputs'].get('product_history',[]),*state['inputs'].get('quote_source_history',[])]:
        event['path']=str(folder/event['path'].removeprefix('bundle-source:'))
    if state.get('result'):
        for kind in ('technical','business','price'):
            for item in state['result'][kind]:item['file']=str(folder/'draft'/item['file'].removeprefix('bundle-artifact:'))
    state.update(id=fresh['id'],revision=fresh['revision'])
    from datetime import datetime,timezone
    state['history'].append(dict(operation='import_prepared',actor=actor,basis=basis,at=datetime.now(timezone.utc).isoformat()))
    repo.save(folder,state)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='管理员在服务器准备本地交付成果；不导入至任何用户、不认可业务结论')
    for name in ('storage','manifest','root','ssot','inputs'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--result');args=parser.parse_args()
    print(prepare(**vars(args)))

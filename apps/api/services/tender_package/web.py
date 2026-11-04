"""Persistent, owner-scoped orchestration; existing package algorithms stay intact."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


class PackageStore:
    def __init__(self,storage,user_id):
        self.root=Path(storage).resolve()/'tender-packages'/str(int(user_id))
        self.root.mkdir(parents=True,exist_ok=True)

    def folder(self,key):
        if not re.fullmatch(r'[a-f0-9]{32}',key):raise FileNotFoundError('压缩包任务不存在')
        p=self.root/key
        if not p.is_dir() or p.is_symlink():raise FileNotFoundError('压缩包任务不存在')
        return p

    def latest(self,key):
        files=sorted(self.folder(key).glob('state-*.json'))
        if not files:raise FileNotFoundError('压缩包任务不存在')
        return read(files[-1])

    def save(self,p,state):
        state['revision']=state.get('revision',0)+1
        state['updated_at']=datetime.now(timezone.utc).isoformat()
        target=p/f"state-{state['revision']:08d}.json"
        temp=p/(uuid4().hex+'.json.part')
        temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(target)

    def create(self,name):
        key=uuid4().hex;p=self.root/key;p.mkdir()
        state=dict(id=key,name=Path(name.replace('\\','/')).name,revision=0,status='uploaded',history=[],uploads={})
        self.save(p,state);return state

    @contextmanager
    def lock(self,key,revision):
        p=self.folder(key);lock=p/'busy.lock'
        try:stream=lock.open('x')
        except FileExistsError:raise ValueError('任务正在处理；若服务曾中断，请联系管理员核验锁后恢复，勿重复提交')
        try:
            state=self.latest(key)
            if state['revision']!=revision:raise ValueError('数据已更新，请刷新后重新提交，避免覆盖人工确认')
            yield p,state
        finally:stream.close();lock.unlink(missing_ok=True)

    def action(self,key,revision,operation,args,actor):
        with self.lock(key,revision) as (p,state):
            before=deepcopy(state);state['status']='processing';state['stage']=operation;self.save(p,state)
            try:
                state=self.apply(p,state,operation,args,actor)
                state['status']='review_required';state.pop('error',None)
            except Exception:
                before['revision']=state['revision'];before['status']='failed';before['stage']=operation
                before['error']='本阶段未完成，已保留上次有效结果；核对输入后可重试'
                self.save(p,before);raise
            audit=deepcopy(args);audit.pop('password',None)
            state['history'].append(dict(operation=operation,actor=actor,basis=args.get('basis'),arguments=audit,at=datetime.now(timezone.utc).isoformat()))
            self.save(p,state);return state

    def apply(self,p,s,op,args,actor):
        from .classify import classify
        from .composed import compose_materials
        from .closure import build_final_mapping
        from .ssot import build_ssot,read_ssot,confirm_field
        from .goods_inputs import create_input_session
        if op=='unpack':
            if not s.get('archive'):raise ValueError('上传未完成，请新建任务重新上传')
            if 'manifest' in s:raise ValueError('已完成解包，请上传新任务保留原始历史')
            from .formats import unpack_extended
            output='unpacked-'+uuid4().hex
            s['manifest']=unpack_extended(p/s['archive'],p/output,password=args.get('password'))
            s['root']=output
        elif op in ('classify','role'):
            if 'ssot' in s:raise ValueError('已汇聚SSOT；角色更改请创建新任务，避免覆盖已有事实与人工记录')
            overrides=s.setdefault('role_overrides',{})
            if op=='role':
                if not str(args.get('basis','')).strip():raise ValueError('角色纠正须填写依据')
                if args['storage_path'] not in {f['storage_path'] for f in s['manifest']['files']}:raise ValueError('文件不存在')
                overrides[args['storage_path']]=dict(role=args['role'],actor=actor,reason=args['basis'],
                    confirmed_at=datetime.now(timezone.utc).isoformat(),prior=deepcopy(overrides.get(args['storage_path'])))
            from .recognition import recognize
            s['classified']=recognize(s['manifest'],p/s['root'],overrides=overrides,previous=s.get('classified'))
            s.pop('mapping',None)
        elif op=='material':
            if 'ssot' in s:raise ValueError('SSOT已冻结，不能覆盖模块二结果')
            from .review import apply_manual_review
            a=deepcopy(args);a['actor']=actor
            if a.get('model'):
                from .closure import confirm_material
                s['classified']=confirm_material(s['classified'],a['solidified_id'],conductor=a.get('conductor'),name=a.get('name',''),model=a['model'],actor=actor,basis=a.get('basis',''))
            else:s['classified']=apply_manual_review(s['classified'],a)
            s.pop('mapping',None)
        elif op=='material_export':
            s['mapping']=build_final_mapping(s['classified'],p/s['root'])
        elif op=='material_retire':
            if 'ssot' in s:raise ValueError('SSOT已冻结，不能覆盖模块二结果')
            from .completion import retire_source
            s['classified']=retire_source(s['classified'],args['record_id'],actor,args.get('basis',''))
            s.pop('mapping',None)
        elif op=='mapping':
            if 'ssot' in s:raise ValueError('SSOT已冻结，不能覆盖模块二结果')
            from .completion import parse_mapping,complete_materials
            upload=s['uploads'][args['upload_id']]
            table=parse_mapping((p/upload['path']).read_bytes(),upload['name'],**{k:v for k,v in args.items() if k in ('id_column','name_column','encoding','sheet')})
            s['classified']=complete_materials(s['classified'],imports=[table])
            s.pop('mapping',None)
        elif op=='ssot':
            if 'ssot' in s:raise ValueError('SSOT已建立，请使用字段核验，不重复覆盖')
            final=build_final_mapping(s['classified'],p/s['root'])
            model=build_ssot(s['classified'],p/s['root'],final)
            s['mapping']=final;s['ssot']=model.model_dump();s['inputs']=create_input_session(model)
        elif op=='field':
            a=deepcopy(args);a['actor']=actor
            model=confirm_field(s['ssot'],**a)
            # SSOT confirmation changes its digest; preserve historical input snapshots.
            from .ssot import digest
            s['ssot_history']=s.get('ssot_history',[])+[deepcopy(s['ssot'])]
            s['ssot']=model.model_dump();s['inputs']['ssot_sha256']=digest(s['ssot'])
        elif op=='source':
            from .goods_workflow import apply_request
            upload=s['uploads'].get(args['upload_id'])
            if not upload:raise ValueError('须先上传本任务的真实来源文件')
            mode=args['kind']
            if mode not in ('product','quote_source'):raise ValueError('来源类型无效')
            a=dict(path=str(p/upload['path']))
            if mode=='product':a.update(document_id=args['upload_id'],infer_bindings=True)
            else:a.update(actor=actor,basis=args.get('basis',''))
            s['inputs']=apply_request(s['ssot'],s['inputs'],dict(operation=mode,arguments=a))
        elif op=='review':
            from .goods_review import submit_review
            a=deepcopy(args['arguments']);a['actor']=actor
            if args['operation'] not in ('review_rows','packages','price','source_price','business'):raise ValueError('核验操作无效')
            s['inputs']=submit_review(s['ssot'],s['inputs'],dict(operation=args['operation'],arguments=a),root=p/s['root'],draft=s.get('result'),library=s.get('library'))
        elif op=='generate':
            from .goods_adapter import generate_goods,load_library_snapshot
            from .goods_inputs import compile_inputs
            options=compile_inputs(s['inputs'],s['ssot']);output=p/('draft-'+uuid4().hex)
            library=s.get('library')
            selections=s['inputs'].get('business_library_history',[])
            if selections:library=selections[-1]['snapshot']
            if library:
                snapshot=p/(uuid4().hex+'-library.json');snapshot.write_text(json.dumps(library,ensure_ascii=False),encoding='utf-8')
                options['library']=load_library_snapshot(snapshot)
            s['result_history']=s.get('result_history',[])+([s['result']] if s.get('result') else [])
            s['result']=generate_goods(s['ssot'],p/s['root'],output,**options)
        else:raise ValueError('未知阶段操作')
        return s

    def view(self,key):
        s=self.latest(key);p=self.folder(key);result=deepcopy(s)
        if s.get('classified'):
            from .recognition import describe
            result['recognition']=describe(s['classified'])
        if s.get('ssot'):
            from .goods_audit import build_review_catalog
            from .goods_inputs import compile_inputs
            from .goods_quotes import quote_catalog
            from .goods_review import price_targets
            from .goods_business_review import business_catalog
            compiled=compile_inputs(s['inputs'],s['ssot'])
            result['review']=dict(catalog=build_review_catalog(s['ssot'],s['inputs']),price_targets=price_targets(s['ssot'],p/s['root']),
                packages=compiled['packages'],held_warnings=compiled['input_provenance']['warnings'],price_history=s['inputs']['price_history'],
                approved_quotes=quote_catalog(s['inputs'],s['ssot']),business=business_catalog(s['ssot'],s['inputs'],s.get('result'),s.get('library')),
                history=dict(responses=s['inputs'].get('review_history',[]),packages=s['inputs']['package_history'],business=s['inputs'].get('business_review_history',[])),
                draft_can_continue=True,ready_for_submission=False,regression_status='本轮跳过全量回归，仅做主链路验证')
        result['ready_for_submission']=False
        return result


def public(value,root):
    """Keep logical locators while withholding server absolute filesystem paths."""
    if isinstance(value,dict):return {k:public(v,root) for k,v in value.items()}
    if isinstance(value,list):return [public(v,root) for v in value]
    if isinstance(value,str) and Path(value).is_absolute():
        try:return str(Path(value).resolve().relative_to(root.resolve()))
        except ValueError:return Path(value).name
    return value


def review_html():
    html=Path(__file__).with_name('goods_review.html').read_text(encoding='utf-8')
    start=html.index('async function request(');end=html.index('function identity()',start)
    bridge="""let requestNumber=0;const pendingRequests=new Map();window.addEventListener('message',event=>{if(event.source!==parent||event.data?.kind!=='package-review-reply')return;const p=pendingRequests.get(event.data.id);if(!p)return;pendingRequests.delete(event.data.id);event.data.error?p.reject(Error(event.data.error)):p.resolve(event.data.result)});function request(path,body){return new Promise((resolve,reject)=>{const id=++requestNumber;pendingRequests.set(id,{resolve,reject});parent.postMessage({kind:'package-review-request',id,path,body},'*')})}\n"""
    html=html[:start]+bridge+html[end:]
    return html.replace('<style>','<style>#exportLibrary{display:none}')

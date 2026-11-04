"""Isolated authenticated package APIs; no legacy tender route changes."""
import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter,UploadFile,File,Form,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,ConfigDict
from starlette.concurrency import run_in_threadpool
from api.deps import CurrentUser,DbSession,user_is_admin
from api.services.menus import can_access_menu
from api.services.knowledge.access import require_tender_lib_read
from api.services.tender_package.web import PackageStore,public,review_html
from common.config import get_settings
from common.response import ok

router=APIRouter(prefix='/tender-package',tags=['tender-package'])

def store(user):
    if not user_is_admin(user) and not can_access_menu(user,'/tenders'):
        raise HTTPException(403,'没有标书功能权限')
    return PackageStore(get_settings().storage_root,user.id)

class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    arguments:dict=Field(default_factory=dict)

def guarded(fn,*args):
    try:return fn(*args)
    except FileNotFoundError:raise HTTPException(404,'任务或文件不存在')
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc))

async def save_upload(folder,file):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ('.zip','.rar','.7z','.docx','.xlsx','.csv'):raise HTTPException(422,'不支持的文件类型')
    target=folder/(uuid4().hex+suffix);size=0;digest=sha256()
    try:
        with target.open('xb') as stream:
            while chunk:=await file.read(65536):
                size+=len(chunk)
                if size>get_settings().kb_upload_max_bytes:raise HTTPException(413,'上传大小超限，请拆分文件')
                stream.write(chunk);digest.update(chunk)
        if not size:raise HTTPException(422,'文件为空')
    except Exception:target.unlink(missing_ok=True);raise
    finally:await file.close()
    return dict(path=target.name,name=Path((file.filename or '').replace('\\','/')).name,sha256=digest.hexdigest(),size=size)

@router.get('')
async def listing(user:CurrentUser):
    repo=store(user);items=[]
    for p in repo.root.iterdir():
        if p.is_dir():
            try:
                s=repo.latest(p.name);items.append({k:s.get(k) for k in ('id','name','revision','status','stage','updated_at')})
            except (ValueError,FileNotFoundError):continue
    return ok(sorted(items,key=lambda i:i['updated_at'],reverse=True))

@router.post('/upload')
async def upload(user:CurrentUser,file:UploadFile=File(...)):
    if Path(file.filename or '').suffix.lower() not in ('.zip','.rar','.7z'):raise HTTPException(422,'此入口仅接收压缩包；普通文档继续使用原入口')
    repo=store(user);s=repo.create(file.filename or 'archive');p=repo.folder(s['id'])
    try:record=await save_upload(p,file)
    except Exception:
        s['status']='upload_failed';s['error']='上传未完成，请重新上传压缩包';repo.save(p,s);raise
    s['archive']=record['path'];s['archive_source']=record;repo.save(p,s)
    return ok(public(s,p))

@router.post('/import-prepared/{token}')
async def import_prepared(token:str,body:Action,user:CurrentUser):
    repo=store(user)
    if not user_is_admin(user):raise HTTPException(403,'仅管理员可导入服务器预备成果')
    from api.services.tender_package.handoff import claim
    s=await run_in_threadpool(guarded,claim,repo,token,user.username,body.arguments.get('basis',''))
    return ok(dict(id=s['id'],revision=s['revision']))

@router.get('/{key}')
async def detail(key:str,user:CurrentUser):
    repo=store(user);s=await run_in_threadpool(guarded,repo.view,key)
    return ok(public(s,repo.folder(key)))

@router.get('/{key}/review-page')
async def review_page(key:str,user:CurrentUser):
    repo=store(user);guarded(repo.latest,key)
    return ok(dict(html=review_html()))

@router.get('/{key}/wizard')
async def wizard(key:str,user:CurrentUser):
    from api.services.tender_package.wizard import build_wizard
    repo=store(user)
    state=await run_in_threadpool(guarded,repo.view,key)
    return ok(await run_in_threadpool(build_wizard,state))

@router.post('/{key}/sources')
async def source_upload(key:str,user:CurrentUser,revision:int=Form(...),file:UploadFile=File(...)):
    repo=store(user)
    try:
        with repo.lock(key,revision) as (p,s):
            record=await save_upload(p,file);upload_id=uuid4().hex;s['uploads'][upload_id]=record;repo.save(p,s)
    except FileNotFoundError:raise HTTPException(404,'任务不存在')
    except ValueError as exc:raise HTTPException(422,str(exc))
    return ok(dict(upload_id=upload_id,revision=s['revision']))

@router.post('/{key}/actions/{operation}')
async def action(key:str,operation:str,body:Action,user:CurrentUser,db:DbSession):
    repo=store(user)
    current=guarded(repo.latest,key)
    if current.get('library') and (operation=='generate' or operation=='review' and body.arguments.get('operation')=='business'):
        await require_tender_lib_read(db,user)
    if operation=='library':
        await require_tender_lib_read(db,user)
        from api.services.tender_package.goods_adapter import resolve_business_library
        # Public adapter output is serialized exactly as the standalone snapshot contract.
        from api.services.tender_package.ssot import read_ssot
        try:
            with repo.lock(key,body.revision) as (p,s):
                lib=await resolve_business_library(db,read_ssot(s['ssot']))
                s['library']=dict(lib,loaded=True,slots=[x.model_dump() for x in lib['slots']],media={k:[str(v) for v in values] for k,values in lib['media'].items()},
                    documents=[dict(public_id=d.public_id,name=d.name,expire_at=d.expire_at.isoformat() if d.expire_at else None,is_perpetual=d.is_perpetual,path=str(path)) for d,path in lib['documents']])
                repo.save(p,s)
        except ValueError as exc:raise HTTPException(422,str(exc))
    else:await run_in_threadpool(guarded,repo.action,key,body.revision,operation,body.arguments,user.username)
    return ok(dict(id=key,revision=guarded(repo.latest,key)['revision']))

@router.get('/{key}/artifacts/{index}')
async def artifact(key:str,index:int,user:CurrentUser):
    repo=store(user);s=guarded(repo.latest,key);p=repo.folder(key)
    artifacts=(s.get('result') or {}).get('artifacts',[])
    if not 0<=index<len(artifacts):raise HTTPException(404,'产物不存在')
    result=s['result'];base=Path(result['technical'][0]['file']).parents[2] if result['technical'] else None
    if not base or not base.resolve().is_relative_to(p.resolve()):raise HTTPException(422,'产物来源无效')
    target=(base/artifacts[index]['path']).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file() or sha256(target.read_bytes()).hexdigest()!=artifacts[index]['sha256']:raise HTTPException(422,'产物不存在或已变更，请重新生成')
    return FileResponse(target,filename=target.name)

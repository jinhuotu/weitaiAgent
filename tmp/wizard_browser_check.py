"""Disposable loopback UI harness. Real package routes/actions, isolated fixtures only."""
import json,io,zipfile,socket,re,sys
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,Response
import httpx,uvicorn
from api.deps import get_current_user
from db.session import get_db
from api.routers import tender_package as routes
from api.services.tender_package.web import PackageStore
from api.services.tender_package.test_composed import fixture
from api.services.tender_package.goods_render import LibreOfficeRenderer
from docx import Document

base=Path('tmp/wizard-browser-check').resolve();base.mkdir(exist_ok=True)
resume=json.loads((base/'ready.json').read_text(encoding='utf-8'))['id'] if '--resume' in sys.argv else None
repo=PackageStore(base/'storage',1)
s=repo.create('隔离向导测试 · 不用于真实投标');folder=repo.folder(s['id'])
docdir=base/'fixtures';docdir.mkdir(exist_ok=True)
tech,_=fixture(docdir)
def document(name,paragraphs,rows=None):
    d=Document()
    for p in paragraphs:d.add_paragraph(p)
    if rows:
        t=d.add_table(rows=0,cols=len(rows[0]))
        for r in rows:
            for c,v in zip(t.add_row().cells,r):c.text=v
    path=docdir/name;d.save(path);return path
invitation=document('第一章_邀请书.docx',['第一章 投标邀请书','项目名称：隔离向导测试货物','采购方式：公开招标'])
notice=document('第二章_投标人须知.docx',['第二章 投标人须知','资格条件：具备所需资格'],[['分标编号','分标名称','包号','单位','数量'],['TEST-LOT','隔离测试分标','P1','件','1']])
price=document('货物报价表.docx',['货物报价表'],[['数量','单价','合价','备注'],['1','','','']])
with zipfile.ZipFile(folder/'sample.zip','w') as z:
    z.write(tech,'技术规范_1234-123456789-00001.docx')
    for p in (invitation,notice,price):z.write(p,p.name)
s.update(archive='sample.zip',library=dict(loaded=True,slots=[],candidate_media={},media={},documents=[],unresolved_keys=[]))
repo.save(folder,s)
if resume:s=repo.latest(resume)
routes.get_settings=lambda:SimpleNamespace(storage_root=str(base/'storage'),kb_upload_max_bytes=2000000)
LibreOfficeRenderer.available=lambda self:False # Isolate only external office dependency in test process.
app=FastAPI();app.include_router(routes.router,prefix='/api/v1')
app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=1,username='wizard-test',is_superuser=True,roles=[])
async def db():yield None
app.dependency_overrides[get_db]=db
@app.get('/wizard-check')
async def index():
    html='''<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;background:#f5f7fb;font-family:Arial,"Microsoft YaHei",sans-serif}*{box-sizing:border-box}</style><div id="app"></div><script type="module">
import {createApp,h} from '/node_modules/.vite/deps/vue.js';
import {createRouter,createWebHistory,RouterView} from '/node_modules/.vite/deps/vue-router.js';
import {createPinia} from '/node_modules/.vite/deps/pinia.js';
import Wizard from '/src/views/TenderPackageWizard.vue';
import Advanced from '/src/views/TenderPackageView.vue';
const router=createRouter({history:createWebHistory(),routes:[{path:'/wizard-check',component:Wizard},{path:'/tender-package-wizard',component:Wizard},{path:'/tender-package',component:Advanced}]});
const app=createApp({render:()=>h(RouterView)});app.use(createPinia());app.use(router);app.mount('#app');
</script></html>'''
    async with httpx.AsyncClient() as c:
        script=(await c.get('http://127.0.0.1:5173/src/views/TenderPackageWizard.vue')).text
    version=re.search(r'/node_modules/\.vite/deps/vue\.js\?v=([a-z0-9]+)',script).group(1)
    for name in ('vue','vue-router','pinia'):
        html=html.replace('/deps/'+name+".js'",'/deps/'+name+'.js?v='+version+"'")
    return HTMLResponse(html)
@app.api_route('/{path:path}',methods=['GET','POST'])
async def frontend(path:str,request:Request):
    if path.startswith('api/'):
        return Response('Not found',404)
    if path in ('tender-package-wizard','tender-package'):
        return await index()
    async with httpx.AsyncClient() as c:
        url='http://127.0.0.1:5173/'+path+('?' + request.url.query if request.url.query else '')
        r=await c.request(request.method,url,content=await request.body())
    headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','cache-control')}
    return Response(r.content,r.status_code,headers=headers)
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
port=sock.getsockname()[1]
(base/'ready.json').write_text(json.dumps(dict(url=f'http://127.0.0.1:{port}/wizard-check?id='+s['id'],id=s['id'],port=port,storage=str(base/'storage'))),encoding='utf-8')
print('ISOLATED_WIZARD_TEST http://127.0.0.1:'+str(port)+'/wizard-check?id='+s['id'],flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning')).run(sockets=[sock])

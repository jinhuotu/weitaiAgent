"""Local test-only application. No production router hot patch or real credentials."""
import sys,json
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=['apps','packages']
from fastapi import FastAPI
from api.deps import get_current_user
from db.session import get_db
from api.routers import tender_package as r
from api.services.tender_package.goods_render import LibreOfficeRenderer
import uvicorn,socket
base=Path('tmp/module5-smoke-storage').resolve();base.mkdir(exist_ok=True)
r.get_settings=lambda:SimpleNamespace(storage_root=str(base),kb_upload_max_bytes=2000000)
LibreOfficeRenderer.available=lambda self:False  # Explicitly exercise 'visual not accepted'.
app=FastAPI();app.include_router(r.router,prefix='/api/v1')
app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=987654,username='isolated-page-test',is_superuser=True,roles=[])
async def database():yield None
app.dependency_overrides[get_db]=database
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
Path('tmp/module5-smoke-ready.json').write_text(json.dumps(dict(url=f'http://127.0.0.1:{port}')),encoding='utf-8')
uvicorn.Server(uvicorn.Config(app,log_level='warning')).run(sockets=[sock])

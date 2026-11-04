"""Standalone localhost review UI; independent from application routes/frontend.

python -m api.services.tender_package.review --input ...json --output ...json
"""
import argparse
from copy import deepcopy
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse

from .completion import MappingError, parse_mapping, manual_confirmation, complete_materials, retire_source


def apply_manual_review(state, body):
    """Use the same page entry for all IDs, never branch on a particular ID."""
    code=body.get('solidified_id')
    if code not in {m['solidified_id'] for m in state['solidified_id_map']}:
        raise MappingError('固化ID不在当前清单中')
    if any(a['solidified_id']==code for a in state.get('composition_history',[])):
        from .closure import confirm_material
        if not body.get('conductor'):
            raise MappingError('请在页面选择人工确认的导体材质；系统不会代为选择')
        return confirm_material(state,code,conductor=body['conductor'],name=body.get('name',''),
                                actor=body.get('actor',''),basis=body.get('basis',''))
    entry=manual_confirmation(code,body.get('name',''),body.get('actor',''),body.get('basis',''))
    return complete_materials(state,confirmations=[entry])


def serve(input_path, output_path, port=0):
    state=json.loads(Path(input_path).read_text(encoding='utf-8'))
    token=secrets.token_urlsafe(32)
    output=Path(output_path)
    if output.resolve()==Path(input_path).resolve():
        raise ValueError('输出文件必须与原始识别清单分开')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_json(self,data,status=200):
            content=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(content)
        def do_GET(self):
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':
                self.send_error(403);return
            if self.path!='/':self.send_error(404);return
            html=Path(__file__).with_name('review.html').read_text(encoding='utf-8').replace('__TOKEN__',token)
            from .composed import load_rules
            options=sorted(set(load_rules()['conductor_values'].values()))
            html=html.replace('__CONDUCTOR_OPTIONS__',json.dumps(options,ensure_ascii=True).replace('<',r'\u003c'))
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(html.encode())
        def do_POST(self):
            nonlocal state
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or not hmac.compare_digest(self.headers.get('X-Review-Token',''),token):
                self.send_json({'error':'访问令牌无效'},403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=24*1024*1024:raise MappingError('请求大小超限')
                body=json.loads(self.rfile.read(size))
                if self.path=='/state':self.send_json(state);return
                if self.path=='/import':
                    import base64
                    table=parse_mapping(base64.b64decode(body['data'],validate=True),body['filename'],
                                        id_column=body.get('id_column'),name_column=body.get('name_column'),
                                        encoding=body.get('encoding'),sheet=body.get('sheet'))
                    updated=complete_materials(state,imports=[table])
                elif self.path=='/retire':
                    updated=retire_source(state,body['record_id'],body['actor'],body['basis'])
                elif self.path=='/manual':
                    updated=apply_manual_review(state,body)
                else:self.send_json({'error':'未知操作'},404);return
                output.parent.mkdir(parents=True,exist_ok=True)
                temporary=output.with_suffix(output.suffix+'.tmp')
                temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8')
                temporary.replace(output)
                state=updated
                self.send_json(state)
            except MappingError as exc:self.send_json({'error':str(exc)},422)
            except Exception:self.send_json({'error':'文件或输入无法处理；请核对格式、列号及输出路径权限'},422)
    server=HTTPServer(('127.0.0.1',port),Handler)
    print(f'独立补齐界面：http://127.0.0.1:{server.server_port}/ （Ctrl+C 退出；结果逐次保存至 {output}）',flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='独立物料名称补齐界面，不连接现有投标应用')
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args();serve(args.input,args.output,args.port)

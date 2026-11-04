"""Local-only module-four review tool; does not register application routes."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
from pathlib import Path
import secrets

from .goods_audit import build_review_catalog
from .goods_inputs import compile_inputs
from .goods_workflow import apply_request
from .ssot import read_ssot,norm
from .goods_adapter import load_rules,source_path


def price_targets(source,root):
    from .goods_adapter import price_templates
    ssot=read_ssot(source);rules=load_rules();items=[]
    import re
    for doc,header in price_templates(ssot,rules):
        source_path(doc,root)  # Verify actual original file and SHA before listing targets.
        rows=[b for b in doc.blocks if b.table_index==header.table_index and b.row]
        headers=[b for b in rows if b.row<=3]
        columns={i:c for b in headers for i,c in enumerate(b.cells) if re.search(rules['price_columns'],norm(c))}
        for row in rows:
            if row.row<=header.row:continue
            for index,title in columns.items():
                if index<len(row.cells) and not row.cells[index].strip():
                    items.append(dict(document_id=doc.document_id,table=header.table_index,row=row.row,
                                      column=index+1,title=title,source=row.model_dump(),raw_cells=list(row.cells),
                                      source_document=dict(logical_path=doc.logical_path,storage_path=doc.storage_path,sha256=doc.sha256)))
    return items


def submit_review(source,session,body,*,root,draft=None,library=None):
    operation=body.get('operation')
    if operation=='business':
        from .goods_business_review import confirm_business
        return confirm_business(source,session,body.get('arguments',{}),root=root,draft=draft,library=library)
    if operation not in ('review_rows','packages','price','quote_source','source_price'):raise ValueError('不支持的核对操作')
    if operation in ('price','source_price'):
        args=body['arguments'];key=tuple(args[k] for k in ('document_id','table','row','column'))
        target=next((t for t in price_targets(source,root) if key==tuple(t[k] for k in ('document_id','table','row','column'))),None)
        if not target:
            raise ValueError('价格位置不是原表空白价格/税费列，已保留原文，请核对来源位置')
        if operation=='source_price':
            from .goods_quotes import quote_catalog,_price_kind
            from .classify import ID_PATTERN
            import re
            quotes=[q for s in quote_catalog(session,source)['sources'] if s['sha256']==args['source_sha256'] for q in s['quotes'] if q['key']==args['quote_key']]
            if not quotes:raise ValueError('已批准报价项不存在或来源失效')
            quote=quotes[-1];kind=_price_kind(target['title'])
            if kind and kind!=quote['kind'] and not (kind.startswith('unspecified_') and kind.split('_',1)[1]==quote['kind'].split('_',1)[-1]):
                raise ValueError('报价类型与原表目标列不一致，不能混填单价、合价或税率')
            ids=set(re.findall(ID_PATTERN,' '.join(target['raw_cells'])))
            if ids and ids!={quote['solidified_id']}:raise ValueError('报价固化ID与目标行不一致')
    return apply_request(source,session,body)


def serve(source,input_path,output_dir,root,port=0,ready_file=None,draft_result=None,library_snapshot=None):
    source=read_ssot(source);state=json.loads(Path(input_path).read_text(encoding='utf-8-sig'))
    draft=json.loads(Path(draft_result).read_text(encoding='utf-8')) if draft_result else None
    library=json.loads(Path(library_snapshot).read_text(encoding='utf-8')) if library_snapshot else None
    if draft and draft['ssot_sha256']!=state['ssot_sha256']:raise ValueError('商务草稿与当前SSOT不一致')
    output=Path(output_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    if output==Path(input_path).resolve().parent:raise ValueError('核对输出目录须与原输入快照分开')
    # Resume the latest interface snapshot; never lose a prior page submission on restart.
    snapshots=sorted(output.glob('session-*.json'))
    if snapshots:
        restored=json.loads(snapshots[-1].read_text(encoding='utf-8-sig'))
        if restored.get('ssot_sha256')!=state.get('ssot_sha256'):raise ValueError('恢复会话与输入SSOT不一致，请使用独立输出目录')
        state=restored
    token=secrets.token_urlsafe(32)
    targets=price_targets(source,root)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def allowed(self):
            authority=f'127.0.0.1:{self.server.server_port}'
            return self.headers.get('Host')==authority and self.headers.get('Origin') in (None,'http://'+authority)
        def send_json(self,data,status=200):
            content=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(content)
        def do_GET(self):
            if not self.allowed():self.send_json(dict(error='访问来源无效'),403);return
            if self.path!='/':self.send_json(dict(error='未知入口'),404);return
            html=Path(__file__).with_name('goods_review.html').read_text(encoding='utf-8').replace('__TOKEN__',token)
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(html.encode('utf-8'))
        def do_POST(self):
            nonlocal state
            if not self.allowed() or not hmac.compare_digest(self.headers.get('X-Review-Token',''),token):self.send_json(dict(error='访问令牌无效'),403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4*1024*1024:raise ValueError('请求大小超限')
                body=json.loads(self.rfile.read(size))
                if self.path=='/business-library':
                    selections=state.get('business_library_history',[])
                    if not selections:raise ValueError('尚无已确认的真实资料选择，请先逐项核验')
                    self.send_json(selections[-1]['snapshot']);return
                if self.path=='/state':
                    from .goods_quotes import quote_catalog
                    from .goods_business_review import business_catalog
                    compiled=compile_inputs(state,source)
                    self.send_json(dict(catalog=build_review_catalog(source,state),price_targets=targets,
                                        packages=compiled['packages'],held_warnings=compiled['input_provenance']['warnings'],
                                        price_history=state['price_history'],approved_quotes=quote_catalog(state,source),
                                        business=business_catalog(source,state,draft,library),
                                        history=dict(responses=state.get('review_history',[]),packages=state['package_history'],business=state.get('business_review_history',[])),
                                        regression_status='awaiting_explicit_permission_after_human_review',ready_for_submission=False,
                                        reviewed_batches=len(state.get('review_history',[])),draft_can_continue=True))
                    return
                if self.path!='/apply':self.send_json(dict(error='未知操作'),404);return
                updated=submit_review(source,state,body,root=root,draft=draft,library=library)
                counter=1
                while (output/f'session-{counter:06d}.json').exists():counter+=1
                path=output/f'session-{counter:06d}.json'
                with path.open('x',encoding='utf-8') as stream:json.dump(updated,stream,ensure_ascii=False,indent=2)
                state=updated
                self.send_json(dict(saved=str(path),message='已通过正式确认函数保存，原始记录保留；请重新生成并验收新产物'))
            except (ValueError,KeyError,TypeError,OSError) as exc:self.send_json(dict(error=str(exc)),422)
            except Exception:self.send_json(dict(error='核对未保存，请检查输入及来源文件；原快照保持不变'),422)
    server=HTTPServer(('127.0.0.1',port),Handler)
    url=f'http://127.0.0.1:{server.server_port}/'
    if ready_file:
        with Path(ready_file).open('x',encoding='utf-8') as stream:json.dump(dict(url=url,input=str(Path(input_path).resolve()),output=str(output)),stream,ensure_ascii=False,indent=2)
    print('模块四独立核对页面：'+url,flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='独立模块四人工核对页，未接入正式前端')
    parser.add_argument('--ssot',required=True);parser.add_argument('--input',required=True)
    parser.add_argument('--output-dir',required=True);parser.add_argument('--root',required=True)
    parser.add_argument('--port',type=int,default=0);parser.add_argument('--ready-file')
    parser.add_argument('--draft-result');parser.add_argument('--library-snapshot')
    args=parser.parse_args();serve(args.ssot,args.input,args.output_dir,args.root,args.port,args.ready_file,args.draft_result,args.library_snapshot)

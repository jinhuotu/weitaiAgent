"""Structured local review commands. No application routes or JSON hand editing."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .goods_inputs import (
    create_input_session, confirm_offer, confirm_price, confirm_packages,
    compile_inputs, read_product_source,
)
from .ssot import read_ssot, digest


def apply_request(source, session, request):
    """Apply an explicit request through the public confirmation functions."""
    ssot=read_ssot(source)
    operation=request['operation'];args=deepcopy(request.get('arguments',{}))
    if operation=='create':
        if session is not None:raise ValueError('已有会话不能重新初始化，请新建输出保留历史')
        return create_input_session(ssot)
    if session is None or session.get('ssot_sha256')!=digest(ssot.model_dump()):
        raise ValueError('须提供与本次SSOT一致的输入会话')
    if operation=='offer':return confirm_offer(session,**args)
    if operation=='price':return confirm_price(session,**args)
    if operation=='packages':return confirm_packages(session,ssot,**args)
    if operation=='quote_source':
        from .goods_quotes import approve_quote_source
        return approve_quote_source(session,ssot,**args)
    if operation=='source_price':
        from .goods_quotes import confirm_source_price
        return confirm_source_price(session,ssot,**args)
    if operation=='review_rows':
        from .goods_audit import confirm_pending_rows
        return confirm_pending_rows(ssot,session,**args)
    if operation=='product':
        if not isinstance(args.get('document_id'),str) or not args['document_id'].strip():
            raise ValueError('产品资料来源ID必填')
        ids=args.get('solidified_ids',[])
        if not isinstance(ids,list) or len(set(ids))!=len(ids) or set(ids)-{m.solidified_id for m in ssot.materials}:
            raise ValueError('产品资料绑定须为SSOT中的非重复物料ID；未知对应可留空待确认')
        args.setdefault('solidified_ids',[])
        product=read_product_source(**args)
        if product['sha256'] in {d.sha256 for d in ssot.documents}:
            raise ValueError('招标要求文件不得用作投标人保证值依据')
        result=deepcopy(session)
        product['registered_at']=datetime.now(timezone.utc).isoformat()
        result.setdefault('product_history',[]).append(product)
        return result
    if operation=='match':
        result=deepcopy(session)
        compiled=compile_inputs(session,ssot)
        matched=compiled['input_provenance'].get('product_matching',dict(offers={},warnings=[]))
        result['matching_report']=matching_summary(ssot,matched)
        return result
    raise ValueError('未知操作；支持 create、product、match、offer、review_rows、price、packages')


def matching_summary(source,matched):
    ssot=read_ssot(source)
    targets=[dict(solidified_id=m.solidified_id,target=p.document_id+':'+p.locator)
             for m in ssot.materials for p in m.parameter_points]
    pending=[t for t in targets if t['target'] not in matched['offers'].get(t['solidified_id'],{})]
    warnings=matched['warnings']
    return dict(total_targets=len(targets),automatic_matches=len(targets)-len(pending),
                conflict_targets=sum(w['type']=='product_parameter_conflict' for w in warnings),
                unbound_sources=sum(w['type']=='product_binding_pending' for w in warnings),
                invalid_sources=sum(w['type']=='product_source_invalid' for w in warnings),
                pending_targets=pending,matches=deepcopy(matched['offers']),warnings=deepcopy(warnings))


def main(argv=None):
    parser=argparse.ArgumentParser(description='独立货物数据回填接口；每次输出新快照并保留来源历史')
    parser.add_argument('--ssot',required=True)
    parser.add_argument('--input',help='上次接口输出，不手工修改')
    parser.add_argument('--request',default='-',help='结构化请求文件；- 从标准输入读取')
    parser.add_argument('--output',required=True,help='新输出路径，不覆盖既有快照')
    args=parser.parse_args(argv)
    try:
        output=Path(args.output)
        if output.exists():raise ValueError('输出已存在，请使用新的快照路径以保留历史')
        session=json.loads(Path(args.input).read_text(encoding='utf-8-sig')) if args.input else None
        request=json.loads(sys.stdin.buffer.read().decode('utf-8-sig')) if args.request=='-' else json.loads(Path(args.request).read_text(encoding='utf-8-sig'))
        result=apply_request(args.ssot,session,request)
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
        summary=result.get('matching_report',{})
        print(json.dumps(dict(output=str(output.resolve()),operation=request['operation'],
                              automatic_matches=summary.get('automatic_matches'),
                              pending=len(summary.get('pending_targets',[]))),ensure_ascii=True))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        parser.exit(2,'回填未完成：'+str(exc)+'；请核对请求、真实来源和输出路径。\n')


if __name__=='__main__':main()

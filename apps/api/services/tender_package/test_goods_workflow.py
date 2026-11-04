import json
import io
from pathlib import Path

import pytest

from .goods_inputs import compile_inputs
from .goods_workflow import apply_request, main
from .goods_render import discover_libreoffice
from .test_goods_adapter import sample
from .test_goods_supplement import product


def test_workflow_product_match_manual_override_history(tmp_path):
    ssot=sample(tmp_path)
    session=apply_request(ssot,None,dict(operation='create'))
    code=ssot.materials[0].solidified_id
    source=product(tmp_path)
    registered=apply_request(ssot,session,dict(operation='product',arguments=dict(
        path=str(source),document_id='supplier',solidified_ids=[code],
        binding_actor='确认人',binding_basis='已核对供货产品与物料对应关系')))
    matched=apply_request(ssot,registered,dict(operation='match'))
    assert matched['matching_report']['automatic_matches']==1
    assert matched['product_history'][0]['registered_at']
    target=next(iter(matched['matching_report']['matches'][code]))
    revised=apply_request(ssot,matched,dict(operation='offer',arguments=dict(
        code=code,target=target,guarantee='人工复核值',actor='复核人',basis='核对变更后的产品参数')))
    options=compile_inputs(revised,ssot)
    assert options['offers'][code][target]['guarantee']=='人工复核值'
    assert options['input_provenance']['product_matching']['offers'][code][target]['guarantee']=='YJV22'
    assert not session.get('product_history')
    source.write_bytes(b'changed')
    assert not compile_inputs(matched,ssot)['offers']


def test_workflow_keeps_unbound_and_rejects_tender(tmp_path):
    ssot=sample(tmp_path);session=apply_request(ssot,None,dict(operation='create'))
    source=product(tmp_path)
    registered=apply_request(ssot,session,dict(operation='product',arguments=dict(
        path=str(source),document_id='unbound',solidified_ids=[])))
    report=apply_request(ssot,registered,dict(operation='match'))['matching_report']
    assert report['unbound_sources']==1 and report['automatic_matches']==0
    assert len(report['pending_targets'])==report['total_targets']
    original=tmp_path/ssot.documents[0].storage_path
    with pytest.raises(ValueError,match='招标要求'):
        apply_request(ssot,session,dict(operation='product',arguments=dict(
            path=str(original),document_id='tender',solidified_ids=[])))


def test_workflow_cli_creates_new_snapshots(tmp_path,capsys):
    from .ssot import write_ssot
    ssot=sample(tmp_path);ssot_path=tmp_path/'ssot.json';write_ssot(ssot,ssot_path)
    request=tmp_path/'request.json';request.write_text(json.dumps(dict(operation='create')))
    output=tmp_path/'session.json'
    args=['--ssot',str(ssot_path),'--request',str(request),'--output',str(output)]
    main(args)
    saved=output.read_bytes()
    assert json.loads(saved)['targets']
    with pytest.raises(SystemExit):main(args)
    assert saved==output.read_bytes()


def test_discovery_finds_install_without_path(tmp_path,monkeypatch):
    import api.services.tender_package.goods_render as render
    monkeypatch.setattr(render.shutil,'which',lambda _:None)
    monkeypatch.setenv('ProgramFiles',str(tmp_path))
    executable=tmp_path/'LibreOffice'/'program'/'soffice.exe'
    executable.parent.mkdir(parents=True);executable.write_bytes(b'fixture')
    assert discover_libreoffice()==str(executable)
    assert discover_libreoffice(str(tmp_path/'missing.exe')) is None


def test_section_inference_is_local_and_does_not_need_fake_confirmer(tmp_path):
    from docx import Document
    from .goods_inputs import read_product_source,match_product_offers
    ssot=sample(tmp_path);code=ssot.materials[0].solidified_id
    p=tmp_path/'multi.docx';doc=Document()
    for identifier,value in [(code,'YJV22'),('1234-987654321-00001','unrelated-value')]:
        doc.add_paragraph('技术规范ID：'+identifier)
        table=doc.add_table(rows=2,cols=4)
        for c,text in zip(table.rows[0].cells,['名称','单位','招标人要求值','投标人保证值']):c.text=text
        for c,text in zip(table.rows[1].cells,['产品型号规格','无','禁止当作保证值',value]):c.text=text
    doc.save(p)
    card=read_product_source(p,'supplier',infer_bindings=True)
    assert card['parameters'][0]['binding_evidence']['locator']=='paragraph:1'
    assert card['parameters'][0]['solidified_ids']==[code]
    assert card['binding_actor'] is None
    card['parameters'][1]['solidified_ids']=[code]  # Caller cannot change actual section evidence.
    result=match_product_offers(ssot,[card])
    offer=next(iter(result['offers'][code].values()))
    assert offer['guarantee']=='YJV22' and offer['sources'][0]['binding_mode']=='document_section'
    assert any(w['type']=='product_binding_pending' for w in result['warnings'])


def test_multiple_ids_in_section_stay_pending(tmp_path):
    from docx import Document
    from .goods_inputs import read_product_source,match_product_offers
    ssot=sample(tmp_path);code=ssot.materials[0].solidified_id
    p=tmp_path/'ambiguous.docx';doc=Document();doc.add_paragraph(code+' / 1234-987654321-00001')
    table=doc.add_table(rows=2,cols=3)
    for c,text in zip(table.rows[0].cells,['名称','保证值','单位']):c.text=text
    for c,text in zip(table.rows[1].cells,['产品型号规格','YJV22','无']):c.text=text
    doc.save(p)
    result=match_product_offers(ssot,[read_product_source(p,'ambiguous',infer_bindings=True)])
    assert not result['offers'] and any(w['type']=='product_binding_pending' for w in result['warnings'])


def test_existing_human_parameter_is_not_overridden(tmp_path,monkeypatch):
    from .goods_inputs import read_product_source,match_product_offers
    import api.services.tender_package.goods_inputs as inputs
    ssot=sample(tmp_path);material=ssot.materials[0];point=material.parameter_points[0]
    source=next(d for d in ssot.documents if d.document_id==point.document_id)
    material.material_mapping['selected_sources']=[dict(name_derivation=[dict(evidence=[dict(
        value_origin='manual',source_sha256=source.sha256,table_index=0,row=2,confirmed_value='reviewed-model',actor='原确认人',basis='原确认依据')])])]
    monkeypatch.setattr(inputs,'read_ssot',lambda _:ssot)  # Isolate the match policy from SSOT integrity validation.
    card=read_product_source(product(tmp_path),'supplier',solidified_ids=[material.solidified_id],binding_actor='人',binding_basis='确认')
    result=match_product_offers(ssot,[card])
    assert not result['offers']
    assert result['warnings'][0]['type']=='product_parameter_conflict'
    assert result['warnings'][0]['confirmed_evidence'][0]['actor']=='原确认人'


def test_cli_utf8_stdin_and_bad_document(tmp_path,monkeypatch):
    from .ssot import write_ssot
    from .goods_inputs import read_product_source
    ssot=sample(tmp_path);ssot_path=tmp_path/'ssot.json';write_ssot(ssot,ssot_path)
    source=product(tmp_path,'中文产品资料.docx')
    request=dict(operation='product',arguments=dict(path=str(source),document_id='中文资料',infer_bindings=True))
    initial=tmp_path/'input.json';initial.write_text(json.dumps(apply_request(ssot,None,dict(operation='create'))),encoding='utf-8')
    stdin=io.TextIOWrapper(io.BytesIO(json.dumps(request,ensure_ascii=False).encode('utf-8')),encoding='ascii')
    monkeypatch.setattr('sys.stdin',stdin)
    output=tmp_path/'next.json'
    main(['--ssot',str(ssot_path),'--input',str(initial),'--output',str(output)])
    assert json.loads(output.read_text(encoding='utf-8'))['product_history'][0]['document_id']=='中文资料'
    bad=tmp_path/'damaged.docx';bad.write_bytes(b'not a document')
    with pytest.raises(ValueError,match='无法解析'):read_product_source(bad,'bad')


def test_visual_issues_attach_to_result_without_changing_documents(tmp_path):
    from .goods_adapter import generate_goods
    from .goods_render import render_package,review_page,apply_visual_review,CHECKS
    from PIL import Image
    from hashlib import sha256
    ssot=sample(tmp_path);result=generate_goods(ssot,tmp_path,tmp_path/'draft')
    class Renderer:
        def available(self):return True
        def render(self,document,output):
            output.mkdir(parents=True);p=output/'page.png';Image.new('RGB',(20,20),'white').save(p);return [p]
    report=render_package(tmp_path/'draft',Renderer())
    before={d['file']:sha256(Path(d['file']).read_bytes()).hexdigest() for d in report['documents']}
    for document in report['documents']:
        for page in document['pages']:
            checks=dict.fromkeys(CHECKS,True);checks['pagination']=False
            report=review_page(report,page['path'],actor='测试核对人',basis='分页尚需确认',checks=checks)
    reviewed=apply_visual_review(result,report)
    assert any(w['type']=='visual_review_issue' and w['failed_checks']==['pagination'] for w in reviewed['warnings'])
    assert not reviewed['ready_for_submission'] and not result.get('visual_history')
    assert all(sha256(Path(path).read_bytes()).hexdigest()==value for path,value in before.items())
    incomplete=json.loads(json.dumps(report));incomplete['documents'].pop()
    with pytest.raises(ValueError,match='全部产物'):apply_visual_review(result,incomplete)

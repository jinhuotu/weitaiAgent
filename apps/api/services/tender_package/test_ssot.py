from copy import deepcopy
from pathlib import Path
import json
import pytest
from docx import Document
from .test_composed import fixture
from .composed import compose_materials
from .closure import build_final_mapping
from .ssot import build_ssot,read_ssot,write_ssot,resolve,confirm_field,load_rules
from .ssot_schema import Candidate,Evidence


def inputs(tmp_path, extra=None):
    path,initial=fixture(tmp_path)
    composed=compose_materials(initial,tmp_path)
    final=build_final_mapping(composed,tmp_path)
    file=deepcopy(initial['solidified_id_map'][0]);file.update(file_type='.docx',role='tech_spec')
    files=[file]
    from hashlib import sha256
    for i,(role,paras,tables) in enumerate(extra or []):
        d=Document()
        for text in paras:d.add_paragraph(text)
        for rows in tables:
            t=d.add_table(rows=0,cols=len(rows[0]))
            for row in rows:
                for cell,text in zip(t.add_row().cells,row):cell.text=text
        p=tmp_path/f'input_{i}.docx';d.save(p)
        files.append(dict(logical_path=p.name,storage_path=p.name,sha256=sha256(p.read_bytes()).hexdigest(),file_type='.docx',role=role))
    return dict(files=files),final

@pytest.mark.parametrize('label',['招标编号','采购编号','项目编号'])
def test_structures_and_provenance(tmp_path,label):
    manifest,final=inputs(tmp_path,[('invitation',[f'{label}：A-123','项目名称：测试货物','采购方式：公开招标','采购类型：goods'],[]),
       ('notice',['通用资格要求','资格条件：具备相关资格','投标无效情形：弄虚作假','专用资格要求：具备相应能力'],
        [[['分标编号','包号','项目单位','计量单位','采购数量'],['L1','P1','单位甲','台','5'],['L1','P2','单位乙','台','8']]])])
    ssot=build_ssot(manifest,tmp_path,final)
    assert ssot.fields['tender_number'].value=='A-123'
    assert ssot.fields['project_name'].value=='测试货物'
    assert len(ssot.lots)==2
    assert ssot.materials[0].material_mapping==final['payload']['entries'][0]
    assert ssot.materials[0].parameter_points and ssot.materials[0].response_table_ref
    assert ssot.materials[0].response_template_sources
    assert any(r.category=='rejection' for r in ssot.requirements)
    path=tmp_path/'ssot.json';write_ssot(ssot,path)
    assert read_ssot(path)==ssot


def test_authority_dedup_conflict_and_manual(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['招标编号：A1'],[]),('notice',['招标编号：A1','招标编号：B2'],[])])
    r=build_ssot(manifest,tmp_path,final);f=r.fields['tender_number']
    assert f.status=='conflict' and f.needs_confirmation and f.value is None
    assert len(f.candidates)==2 and len(next(c for c in f.candidates if c.value=='A1').sources)==2
    source=f.candidates[0].sources[0].model_dump()
    confirmed=confirm_field(r,'fields.tender_number','A1',actor='审查人',basis='人工核对',evidence_refs=[source])
    assert confirmed.fields['tender_number'].status=='manual' and confirmed.manual_history
    assert r.fields['tender_number'].needs_confirmation


def test_higher_authority_keeps_other_value(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['招标编号：A1'],[]),('notice',['招标编号：B2'],[])])
    r=build_ssot(manifest,tmp_path,final)
    assert r.fields['tender_number'].value=='B2' and r.fields['tender_number'].status=='conflict'


def test_missing_unranked_and_templates(tmp_path):
    manifest,final=inputs(tmp_path,[('other',['招标编号：A1'],[]),('notice',['项目名称： 招标编号： 分标编号：','招标项目名称：见前附表。'],[[['项目名称','货物名称','数量','单位']]])])
    r=build_ssot(manifest,tmp_path,final)
    assert r.fields['tender_number'].status=='unranked'
    assert r.fields['project_name'].status=='missing'
    assert r.strategy=='pending' and r.pending


def test_contract_structure_and_foreign_prices(tmp_path):
    manifest,final=inputs(tmp_path,[('contract',['第二部分 通用合同条款','1.1 交货约定','应在约定期限交货'],[]),
        ('contract',['第三部分 专用合同条款','1.1 交货约定','交货期：30天'],[]),
        ('quotation',[],[[['固化ID','物料名称','最高限价'],['9999-123456789-00001','其他物料','100']]])])
    r=build_ssot(manifest,tmp_path,final)
    assert {'general','special'}<={d.contract_kind for d in r.documents if d.role=='contract'}
    assert r.pricing_rows[0]['material_ref'] is None and r.pricing_rows[0]['needs_confirmation']


def test_type_and_isolation(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['项目名称：测试项目','协议库存公开招标'],[])])
    original=deepcopy(final)
    r=build_ssot(manifest,tmp_path,final)
    assert r.strategy=='goods'
    assert original==final
    broken=r.model_dump();broken['materials'][0]['material_mapping']['material_name']='tampered'
    with pytest.raises(ValueError):read_ssot(broken)
    broken=r.model_dump();broken['materials'][0]['parameter_points'][0]['quote']='invented'
    with pytest.raises(ValueError):read_ssot(broken)
    broken=r.model_dump();broken['module2_payload_sha256']='invalid'
    with pytest.raises(ValueError):read_ssot(broken)


def test_hash_and_mapping_missing(tmp_path):
    manifest,final=inputs(tmp_path)
    bad=deepcopy(manifest);bad['files'][0]['sha256']='bad'
    with pytest.raises(ValueError,match='哈希'):build_ssot(bad,tmp_path,final)
    with pytest.raises(ValueError,match='来源'):build_ssot({'files':[]},tmp_path,final)


def test_source_corpus_and_configurable_authority(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['招标编号：A1','未分类信息必须保留'],[]),('notice',['招标编号：B2'],[])])
    rules=load_rules();rules['authority']['identity']['invitation']=-1
    r=build_ssot(manifest,tmp_path,final,rules=rules)
    assert r.fields['tender_number'].value=='A1'
    assert any(b.text=='未分类信息必须保留' for d in r.documents for b in d.blocks)


def test_contract_effective_conflict_and_supplement(tmp_path):
    from .ssot import add_requirement
    manifest,final=inputs(tmp_path,[('contract',['第二部分 通用合同条款','1.1 交货期限为30天'],[]),('contract',['第三部分 专用合同条款','1.1 交货期限为60天'],[])])
    r=build_ssot(manifest,tmp_path,final)
    clause=next(item for item in r.requirements if item.key=='contract_effective:1.1')
    assert clause.fact.status=='conflict' and clause.fact.value=='1.1 交货期限为60天'
    assert len(clause.fact.candidates)==2
    refs=[clause.fact.candidates[0].sources[0].model_dump()]
    fixed=confirm_field(r,'requirements.'+clause.key,'以60天为准',actor='审查人',basis='补充澄清',evidence_refs=refs)
    assert next(item for item in fixed.requirements if item.key==clause.key).fact.status=='manual'
    supplemented=add_requirement(fixed,'qualification_special','需补充资格证明',actor='审查人',basis='核对条款',evidence_refs=refs)
    assert any(item.category=='qualification_special' for item in supplemented.requirements)


def test_selected_lot_and_unknown_province_supplement(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['项目名称：测试'],[[['分标编号','计量单位','采购数量'],['L1','台','5']]])])
    r=build_ssot(manifest,tmp_path,final)
    lot=r.lots[0];refs=[lot.row_sources[0].model_dump()]
    assert lot.fields['province'].status=='missing'
    fixed=confirm_field(r,'lots.'+lot.key+'.province','人工核对地区',actor='审查人',basis='清单核对',evidence_refs=refs)
    fixed=confirm_field(fixed,'fields.selected_lots',[lot.key],actor='审查人',basis='确认本次范围',evidence_refs=refs)
    assert 'lots.selected_scope' not in fixed.pending
    with pytest.raises(ValueError):confirm_field(r,'fields.selected_lots',['unknown'],actor='审查人',basis='依据',evidence_refs=refs)


def test_mixed_contract_sections_use_local_authority(tmp_path):
    manifest,final=inputs(tmp_path,[('contract',['第二部分 通用合同条款','1.1 交货期限30天','第三部分 专用合同条款','1.1 交货期限60天'],[])])
    r=build_ssot(manifest,tmp_path,final)
    effective=next(q for q in r.requirements if q.key=='contract_effective:1.1')
    assert effective.fact.value=='1.1 交货期限60天' and effective.fact.status=='conflict'
    assert {c.authority for c in effective.fact.candidates}=={0,2}
    broken=r.model_dump();broken['rules_snapshot']['version']='tampered'
    with pytest.raises(ValueError,match='规则摘要'):read_ssot(broken)



def test_pending_status_cannot_be_silently_cleared(tmp_path):
    manifest,final=inputs(tmp_path)
    r=build_ssot(manifest,tmp_path,final)
    broken=r.model_dump();broken['pending']=[]
    with pytest.raises(ValueError,match='状态'):read_ssot(broken)

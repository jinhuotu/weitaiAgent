from copy import deepcopy
import pytest
from .test_composed import fixture
from .composed import compose_materials
from .closure import confirm_material,build_final_mapping,read_final_mapping,write_final_mapping
from .completion import MappingError,parse_mapping,complete_materials


def test_manual_closure_and_history(tmp_path):
    path,initial=fixture(tmp_path,'YJLV22','铜')
    original=compose_materials(initial,tmp_path)
    code=original['solidified_id_map'][0]['solidified_id']
    with pytest.raises(MappingError,match='待确认'):build_final_mapping(original,tmp_path)
    result=confirm_material(original,code,conductor='铝',name='人工核对的铝导体物料',actor='审查员',basis='正式书面澄清')
    assert original['material_pending_confirmation']
    assert result['composition_history']==original['composition_history']
    m=result['solidified_id_map'][0]
    assert m['material_source_type']=='manual' and not m['needs_confirmation']
    artifact=build_final_mapping(result,tmp_path,expected_ids=[code])
    payload=read_final_mapping(artifact)
    entry=payload['entries'][0]
    assert entry['historical_conflict'] and entry['source_type']=='manual'
    assert entry['selected_sources'][0]['confirmed_parameters']['conductor']=='铝'
    assert entry['selected_sources'][0]['confirmed_at']
    assert entry['composition_history'][0]['parameters']['conductor'][0]['raw_value']=='铜'
    out=tmp_path/'final.json';write_final_mapping(artifact,out)
    assert read_final_mapping(out)==payload
    with pytest.raises(FileExistsError):write_final_mapping(artifact,out)
    artifact['payload']['entries'][0]['material_name']='tampered'
    with pytest.raises(MappingError,match='摘要'):read_final_mapping(artifact)

@pytest.mark.parametrize('actor,basis,conductor,name',[('', '依据','铜','名称'),('审查人','','铜','名称'),('审查人','依据','','名称'),('审查人','依据','铝','铜导体物料')])
def test_no_invented_confirmation(tmp_path,actor,basis,conductor,name):
    _,initial=fixture(tmp_path)
    with pytest.raises(MappingError):confirm_material(initial,initial['solidified_id_map'][0]['solidified_id'],conductor=conductor,name=name,actor=actor,basis=basis)


def test_composed_trace_and_hash(tmp_path):
    path,initial=fixture(tmp_path)
    result=compose_materials(initial,tmp_path)
    payload=read_final_mapping(build_final_mapping(result,tmp_path))
    assert payload['entries'][0]['selected_sources'][0]['components'][0]['evidence'][0]['row']
    with pytest.raises(MappingError,match='预期'):build_final_mapping(result,tmp_path,expected_ids=[])
    path.write_bytes(b'changed')
    with pytest.raises(MappingError,match='完整性'):build_final_mapping(result,tmp_path)


def test_mapping_remains_higher_priority(tmp_path):
    _,initial=fixture(tmp_path,'YJLV22','铜');r=compose_materials(initial,tmp_path)
    code=r['solidified_id_map'][0]['solidified_id']
    r=confirm_material(r,code,conductor='铝',name='确认名称',actor='审查人',basis='澄清')
    table=parse_mapping(f'固化ID,物料名称\n{code},确认名称'.encode(),'authority.csv')
    r=complete_materials(r,imports=[table])
    assert read_final_mapping(build_final_mapping(r,tmp_path))['entries'][0]['source_type']=='mapping'


def test_explicit_model_correction_keeps_raw_history(tmp_path):
    from .composed import compose_rows
    _,initial=fixture(tmp_path,'YJLV22','铜')
    original=compose_materials(initial,tmp_path)
    code=original['solidified_id_map'][0]['solidified_id']
    corrected_rows=deepcopy(original['composition_history'][0]['raw_rows'])
    corrected_rows[1][3]='YJV22'
    derived=compose_rows(corrected_rows)
    assert derived['valid']
    result=confirm_material(original,code,conductor='铜',model='YJV22',name=derived['name'],actor='用户',basis='书面人工确认',name_derivation=derived['components'])
    assert not result['material_pending_confirmation']
    source=result['material_source_ledger'][-1]
    assert source['confirmed_parameters']==dict(conductor='铜',model='YJV22')
    assert result['composition_history']==original['composition_history']
    assert original['composition_history'][0]['parameters']['model'][0]['raw_value']=='YJLV22'
    assert read_final_mapping(build_final_mapping(result,tmp_path))['entries'][0]['source_type']=='manual'
    with pytest.raises(MappingError,match='仍然矛盾'):
        confirm_material(original,code,conductor='铜',model='YJLV22',name='名称',actor='用户',basis='依据')

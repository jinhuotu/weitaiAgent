from copy import deepcopy
import pytest
from .test_composed import fixture
from .composed import compose_materials
from .review import apply_manual_review
from .completion import MappingError
from .closure import build_final_mapping


def test_page_never_decides_material(tmp_path):
    _,initial=fixture(tmp_path,'YJLV22','铜')
    state=compose_materials(initial,tmp_path);original=deepcopy(state)
    code=state['solidified_id_map'][0]['solidified_id']
    with pytest.raises(MappingError,match='页面选择'):
        apply_manual_review(state,dict(solidified_id=code,name='名称',actor='确认人',basis='书面确认'))
    assert state==original

@pytest.mark.parametrize('conductor',['铜','铝'])
def test_both_explicit_human_choices(tmp_path,conductor):
    path,initial=fixture(tmp_path,'YJLV22','铜')
    state=compose_materials(initial,tmp_path);raw=path.read_bytes()
    code=state['solidified_id_map'][0]['solidified_id']
    result=apply_manual_review(state,dict(solidified_id=code,name=conductor+'导体正式物料名称',conductor=conductor,actor='页面确认人',basis='已核对书面清单'))
    source=result['material_source_ledger'][-1]
    assert source['confirmed_parameters']['conductor']==conductor
    assert source['actor']=='页面确认人' and source['confirmed_at'] and source['basis']
    assert result['solidified_id_map'][0]['material_source_type']=='manual'
    assert not result['material_pending_confirmation']
    assert result['composition_history']==state['composition_history']
    assert result['material_conflicts'] and path.read_bytes()==raw
    assert build_final_mapping(result,tmp_path)['payload']['status']=='final'


def test_page_rejects_contradictory_input(tmp_path):
    _,initial=fixture(tmp_path,'YJLV22','铜')
    state=compose_materials(initial,tmp_path);code=state['solidified_id_map'][0]['solidified_id']
    with pytest.raises(MappingError,match='矛盾'):
        apply_manual_review(state,dict(solidified_id=code,name='铜导体物料',conductor='铝',actor='确认人',basis='依据'))

from copy import deepcopy
from hashlib import sha256
import json
import pytest
from docx import Document
from .composed import parse_model,compose_rows,compose_document,compose_materials
from .completion import complete_materials,manual_confirmation,parse_mapping


def rows(model='YJV22',conductor='铜',spec='3×120',voltage='10kV'):
    return [['序号','参数名称','单位','项目需求值或表述','投标人保证值'],
            ['1.1','产品型号规格','无',model,''],['1.2','导体材料','无',conductor,''],
            ['1.3','导体芯数×标称截面','芯数×mm2',spec,''],
            ['1.4','绝缘平均厚度','mm',f'大于等于4.5({voltage})','']]

@pytest.mark.parametrize('model,conductor,armor',[
 ('YJV22','铜','双钢带铠装'),('YJLV22','铝','双钢带铠装'),('YJV32','铜','细钢丝铠装'),
 ('YJLV63','铝','非磁性金属带铠装'),('YJV73','铜','非磁性金属丝铠装'),('VV','铜','无铠装')])
def test_codes(model,conductor,armor):
    parsed=parse_model(model)
    assert parsed['conductor']==conductor and parsed['armor']==armor

@pytest.mark.parametrize('model',['','UNKNOWN','YJLV999','SPECIAL-YJV22','YJ','YJLLV22'])
def test_unknown_codes(model):
    with pytest.raises(ValueError):parse_model(model)

@pytest.mark.parametrize('model,conductor,spec,voltage',[
 ('YJV22','铜','3×120','10kV'),('YJLV63','铝','1×400','35kV'),('YJV32','铜','4×2.5','0.6/1kV')])
def test_composition(model,conductor,spec,voltage):
    r=compose_rows(rows(model,conductor,spec,voltage))
    assert r['valid'] and r['name'].startswith(voltage+' '+model+' '+spec+'mm² '+conductor)
    assert len(r['components'])==5
    assert all(c['evidence'][0]['row']>=2 for c in r['components'])

@pytest.mark.parametrize('index,value',[(1,''),(2,''),(3,''),(1,'UNKNOWN'),(3,'0×120'),(3,'3×0')])
def test_missing_invalid(index,value):
    data=rows();data[index][3]=value
    assert compose_rows(data)['name'] is None


def test_conductor_and_outer_conflicts():
    r=compose_rows(rows('YJLV22','铜'))
    assert not r['valid'] and r['conflict'] and r['issues'][0]['field']=='conductor'
    data=rows('YJV63');data.append(['1.5','外护套材料','无','PVC',''])
    assert compose_rows(data)['conflict']


def test_no_test_voltage_inference():
    data=rows();data[4]=['1.4','绝缘试验电压','kV','30.5kV','']
    assert not compose_rows(data)['valid']
    data.append(['1.5','额定电压','kV','10kV',''])
    assert compose_rows(data)['valid']


def test_duplicate_parameter():
    data=rows();data.append(['2','产品型号规格','无','YJLV22',''])
    assert not compose_rows(data)['valid']
    assert not compose_rows([])['valid']


def fixture(tmp_path,model='YJV22',conductor='铜'):
    path=tmp_path/'random.docx';doc=Document();t=doc.add_table(rows=0,cols=5)
    for row in rows(model,conductor):
        cells=t.add_row().cells
        for c,v in zip(cells,row):c.text=v
    doc.save(path)
    mapping=dict(solidified_id='1234-123456789-00001',logical_path=path.name,storage_path=path.name,
                 sha256=sha256(path.read_bytes()).hexdigest(),material=None,material_evidence=[])
    return path,dict(files=[],solidified_id_map=[mapping])


def test_document_priority_history(tmp_path):
    path,original=fixture(tmp_path);before=path.read_bytes()
    result=compose_materials(original,tmp_path)
    m=result['solidified_id_map'][0]
    assert m['material_source_type']=='composed' and not m['needs_confirmation']
    manual=manual_confirmation(m['solidified_id'],'人工正式名称','确认人','核对清单')
    corrected=complete_materials(result,confirmations=[manual])
    m=corrected['solidified_id_map'][0]
    assert m['material']=='人工正式名称' and not m['needs_confirmation']
    assert m['conflict'] and corrected['composition_history']==result['composition_history']
    mapping=parse_mapping(f"固化ID,物料名称\n{m['solidified_id']},映射名称".encode(),'source.csv')
    authoritative=complete_materials(result,imports=[mapping])
    assert authoritative['solidified_id_map'][0]['material_source_type']=='mapping'
    assert path.read_bytes()==before and original['solidified_id_map'][0]['material'] is None
    assert len(compose_materials(result,tmp_path)['composition_history'])==1


def test_conflict_manual_override(tmp_path):
    path,original=fixture(tmp_path,'YJLV22','铜')
    result=compose_materials(original,tmp_path)
    assert result['material_pending_confirmation'] and result['material_conflicts']
    m=result['solidified_id_map'][0]
    fixed=complete_materials(result,confirmations=[manual_confirmation(m['solidified_id'],'核对后的名称','确认人','已核对原清单')])
    assert not fixed['material_pending_confirmation'] and fixed['material_conflicts']
    assert fixed['composition_history'][0]['issues']


def test_hash_check(tmp_path):
    path,_=fixture(tmp_path)
    with pytest.raises(ValueError):compose_document(path,expected_sha256='wrong')

from copy import deepcopy
from hashlib import sha256
import pytest
from docx import Document
from .materials import extract_material
from .classify import classify

@pytest.mark.parametrize('shape',['label','table','title'])
def test_document_structures(tmp_path,shape):
    d=Document()
    if shape=='label': d.add_paragraph('物资名称：测试设备甲')
    elif shape=='table':
        t=d.add_table(rows=1,cols=2);t.cell(0,0).text='货物名称';t.cell(0,1).text='测试设备甲'
    else: d.add_paragraph('测试设备甲技术规范书')
    p=tmp_path/'随机.docx';d.save(p)
    r=extract_material(p)
    assert r['material']=='测试设备甲'
    assert r['evidence'][0]['location']
    assert not r['needs_confirmation']

@pytest.mark.parametrize('text',['','普通技术规范书正文','产品型号：ABC','组件材料配置表\n名称\n组件甲\n组件乙'])
def test_insufficient(text):
    r=extract_material(text=text)
    assert r['material'] is None and r['needs_confirmation']

def test_conflicts_and_authority():
    r=extract_material(text='物料名称：设备甲\n产品名称：设备乙')
    assert r['material'] is None and r['conflict']
    r=extract_material(text='设备乙技术规范书\n物料名称：设备甲')
    assert r['material']=='设备甲' and r['conflict'] and r['needs_confirmation']


def test_override_preserves_raw_and_input(tmp_path):
    p=tmp_path/'技术规范_1234-123456789-00001.txt';p.write_text('物料名称：设备甲\n产品名称：设备乙',encoding='utf-8')
    f=dict(logical_path=p.name,storage_path=p.name,file_type='.txt',sha256=sha256(p.read_bytes()).hexdigest(),role='pending')
    manifest={'files':[f]};original=deepcopy(manifest)
    result=classify(manifest,tmp_path,overrides={p.name:dict(role='tech_spec',material='设备甲',actor='审查人',reason='核对正文')})
    assert manifest==original
    assert result['files'][0]['material_recognition']['material'] is None
    m=result['solidified_id_map'][0]
    assert m['material']=='设备甲' and m['material_override']['actor']=='审查人'
    assert not m['needs_confirmation']


def test_same_id_cross_file_conflict(tmp_path):
    files=[]
    for i in range(2):
        p=tmp_path/f'技术规范_1234-123456789-00001_{i}.txt';p.write_text(f'物料名称：设备{i}',encoding='utf-8')
        files.append(dict(logical_path=p.name,storage_path=p.name,file_type='.txt',sha256=sha256(p.read_bytes()).hexdigest(),role='pending'))
    r=classify({'files':files},tmp_path)
    assert len(r['material_conflicts'])==2

import csv
from io import StringIO,BytesIO
from copy import deepcopy
import pytest
from .completion import parse_mapping,manual_confirmation,complete_materials,retire_source,MappingError

CODE='1234-123456789-00001'

def base():
    return {'files':[], 'solidified_id_map':[dict(solidified_id=CODE,logical_path='source.docx',sha256='hash',material=None,material_evidence=[])]}

def table(name='可靠名称'):
    return parse_mapping(f'固化ID,物料名称\n{CODE},{name}\n'.encode(),'映射.csv')

@pytest.mark.parametrize('headers',[('固化ID','物料名称'),('solidified_id','material_name'),('固化ID编码','货物名称')])
def test_csv_headers(headers):
    r=parse_mapping(f'{headers[0]},{headers[1]}\n{CODE},设备甲'.encode(),'x.csv')
    assert r['entries'][0]['name']=='设备甲'
    assert r['entries'][0]['solidified_id']==CODE

@pytest.mark.parametrize('encoding',['utf-8-sig','gb18030'])
def test_encoding(encoding):
    assert parse_mapping(f'固化ID;产品名称\n{CODE};设备甲'.encode(encoding),'a.csv')['entries'][0]['valid']

def test_invalid_encoding():
    with pytest.raises(MappingError):parse_mapping(b'\xff','a.csv',encoding='utf-8')

def test_ambiguous_explicit():
    data=f'{CODE},设备甲,备注'.encode()
    with pytest.raises(MappingError,match='指定'):parse_mapping(data,'a.csv')
    assert parse_mapping(data,'a.csv',id_column=0,name_column=1)['entries'][0]['name']=='设备甲'

def test_duplicates_empty():
    r=parse_mapping(f'固化ID,物料名称\n{CODE},甲\n{CODE},乙\n{CODE},'.encode(),'a.csv')
    assert len(r['entries'])==3 and r['issues']
    out=complete_materials(base(),imports=[r])['solidified_id_map'][0]
    assert out['material'] is None and out['conflict'] and out['needs_confirmation']

def test_priority_history_and_retirement():
    original=base();copy=deepcopy(original)
    manual=manual_confirmation(CODE,'人工名称','审查员','正式清单核对')
    result=complete_materials(original,imports=[table()],confirmations=[manual])
    assert original==copy
    m=result['solidified_id_map'][0]
    assert m['material']=='可靠名称' and m['material_source_type']=='mapping' and m['conflict']
    assert len(m['material_sources'])==2 and manual['confirmed_at']
    result=retire_source(result,'source-0','审查员','旧映射表已失效')
    m=result['solidified_id_map'][0]
    assert m['material']=='人工名称' and not m['conflict']
    assert len(m['material_sources'])==2 and result['material_source_ledger'][0]['corrections']

def test_extracted_and_unknown():
    b=base();b['solidified_id_map'][0]['material_evidence']=[dict(name='原始名称',location='p1')]
    result=complete_materials(b)
    assert result['solidified_id_map'][0]['material_source_type']=='extracted'
    assert complete_materials(base())['material_pending_confirmation']

def test_xlsx_read():
    # Synthetic parser fixture only; no spreadsheet deliverable is authored.
    from openpyxl import Workbook
    wb=Workbook();ws=wb.active;ws.append(['说明']);ws.append(['固化ID','物料名称']);ws.append([CODE,'设备甲'])
    out=BytesIO();wb.save(out)
    r=parse_mapping(out.getvalue(),'a.xlsx')
    assert r['entries'][0]['name']=='设备甲' and r['entries'][0]['row']==3


def test_ten_ids_exact_and_persistable():
    import json
    b=base();b['solidified_id_map']=[]
    text='固化ID,物料名称\n'
    for i in range(10):
        code=f'1234-123456789-{i:05d}'
        b['solidified_id_map'].append(dict(solidified_id=code,logical_path=f'{i}.docx',sha256=str(i),material_evidence=[]))
        text+=f'{code},权威样本名称{i}\n'
    r=complete_materials(b,imports=[parse_mapping(text.encode(),'authoritative.csv')])
    r=json.loads(json.dumps(r))
    assert len(r['solidified_id_map'])==10 and not r['material_pending_confirmation']
    assert all(m['material_source_type']=='mapping' for m in r['solidified_id_map'])
    assert all(m['material_sources'][0]['source_sha256'] for m in r['solidified_id_map'])

def test_local_review_roundtrip(tmp_path):
    import subprocess,sys,os,json,re,urllib.request,base64
    source=tmp_path/'input.json';target=tmp_path/'output.json'
    source.write_text(json.dumps(base()),encoding='utf-8')
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(['apps','packages']),PYTHONIOENCODING='utf-8')
    process=subprocess.Popen([sys.executable,'-m','api.services.tender_package.review','--input',str(source),'--output',str(target)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8',env=env)
    try:
        line=process.stdout.readline()
        url=re.search(r'http://127.0.0.1:\d+/',line)[0]
        page=urllib.request.urlopen(url,timeout=5).read().decode()
        assert '__CONDUCTOR_OPTIONS__' not in page
        assert '人工确认的导体材质' in page
        token=re.search("const token='([^']+)'",page)[1]
        def post(path,body):
            request=urllib.request.Request(url+path,json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Review-Token':token})
            return json.loads(urllib.request.urlopen(request,timeout=5).read())
        post('manual',dict(solidified_id=CODE,name='测试名称',actor='测试确认人',basis='仅用于接口测试'))
        assert json.loads(target.read_text(encoding='utf-8'))['solidified_id_map'][0]['material_source_type']=='manual'
        data=base64.b64encode(f'固化ID,物料名称\n{CODE},测试名称'.encode()).decode()
        result=post('import',dict(filename='test.csv',data=data))
        assert result['solidified_id_map'][0]['material_source_type']=='mapping'
        assert not result['material_pending_confirmation']
        assert json.loads(source.read_text())==base()
        assert post('state',{})==json.loads(target.read_text(encoding='utf-8'))
    finally:
        process.terminate();process.wait(timeout=10)

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import pytest
from api.services.tender_package.classify import classify


def manifest(tmp_path,name,text):
    p=tmp_path/name;p.write_text(text,encoding='utf-8')
    return dict(files=[dict(logical_path=name,storage_path=name,file_type=p.suffix,sha256=sha256(p.read_bytes()).hexdigest(),role='pending')])

@pytest.mark.parametrize('name,text,role',[
 ('第一章.txt','投标邀请书','invitation'),('投标人须知.txt','第二章','notice'),
 ('协议书.txt','合同协议书','contract'),('货物清单.txt','报价表','quotation'),
 ('未知.txt','不确定正文','other'),('邀请书报价表.txt','投标邀请书 报价表','other'),
 ('邀请书.txt','合同协议书','other')])
def test_roles(tmp_path,name,text,role):
    m=manifest(tmp_path,name,text);original=deepcopy(m)
    r=classify(m,tmp_path)
    assert r['files'][0]['role']==role
    assert m==original
    assert r['files'][0]['role_evidence']


def test_ten_ids(tmp_path):
    files=[]
    for i in range(10):
        m=manifest(tmp_path,f'技术规范_1234-123456789-{i:05d}.txt',f'物料名称：测试物料{i}')
        files.extend(m['files'])
    r=classify({'files':files},tmp_path)
    assert len(r['solidified_id_map'])==10
    assert all(x['material'] for x in r['solidified_id_map'])
    assert len(r['files'])==10


def test_manual_and_hash(tmp_path):
    m=manifest(tmp_path,'a.txt','不明确')
    r=classify(m,tmp_path,overrides={'a.txt':dict(role='notice',reason='人工核对章节',actor='tester')})
    assert r['files'][0]['role']=='notice'
    (tmp_path/'a.txt').write_text('changed')
    assert classify(m,tmp_path)['files'][0]['role']=='other'


def test_format_missing(tmp_path):
    from api.services.tender_package.formats import unpack_extended
    from api.services.tender_package.unpack import PackageError
    p=tmp_path/'a.7z';p.write_bytes(b'bad')
    with pytest.raises(PackageError,match='7z'):unpack_extended(p,tmp_path/'out')
    assert not (tmp_path/'out').exists()

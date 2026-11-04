from io import BytesIO
from pathlib import Path
import zipfile
import pytest
from api.services.tender_package.unpack import unpack, Limits, PackageError


def archive(items):
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in items: z.writestr(name,data)
    return stream.getvalue()


def test_nested_manifest(tmp_path):
    content=archive([('目录/材料.txt','正文'.encode())])
    for i in range(3): content=archive([(f'层{i}.zip',content)])
    source=tmp_path/'input.zip';source.write_bytes(content)
    result=unpack(source,tmp_path/'output')
    assert result['file_count']==4
    assert result['files'][-1]['depth']==3
    assert len(result['files'][-1]['sha256'])==64
    assert len(result['files'][-1]['source_chain'])==3


@pytest.mark.parametrize('name',['../escape.txt','/escape.txt','C:/escape.txt','dir/../../escape.txt','.hidden.txt','test.exe','test.ps1','CON.txt','dir/file.txt:stream','dir/trailing.'])
def test_unsafe_names(tmp_path,name):
    source=tmp_path/'input.zip';source.write_bytes(archive([(name,b'x')]))
    with pytest.raises(PackageError): unpack(source,tmp_path/'output')
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('limits',[Limits(max_bytes=10),Limits(max_files=0),Limits(max_ratio=1)])
def test_limits(tmp_path,limits):
    source=tmp_path/'input.zip';source.write_bytes(archive([('f.txt',b'a'*1000)]))
    with pytest.raises(PackageError):unpack(source,tmp_path/'out',limits=limits)


def test_depth(tmp_path):
    source=tmp_path/'input.zip';source.write_bytes(archive([('nested.zip',archive([('f.txt',b'x')]))]))
    with pytest.raises(PackageError):unpack(source,tmp_path/'out',limits=Limits(max_depth=0))


def test_duplicates(tmp_path):
    source=tmp_path/'input.zip';source.write_bytes(archive([('a.txt',b'x'),('a.txt',b'x')]))
    result=unpack(source,tmp_path/'out')
    assert result['files'][1]['duplicate_of']=='a.txt'
    assert result['files'][0]['storage_path']!=result['files'][1]['storage_path']


def test_corrupt(tmp_path):
    source=tmp_path/'input.zip';source.write_bytes(b'not a zip')
    with pytest.raises(PackageError):unpack(source,tmp_path/'out')

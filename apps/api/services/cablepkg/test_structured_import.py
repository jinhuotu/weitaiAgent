from openpyxl import Workbook
from .structured_import import dry_run

def test_semantic_classification_and_exact_matching(tmp_path):
    p=tmp_path/'x.xlsx'; w=Workbook(); ws=w.active; ws.title='技术参数'
    ws.append(['技术规范ID','参数名称','单位','投标人保证值']); ws.append(['A','额定电压','kV','10'])
    q=w.create_sheet('报价'); q.append(['技术规范ID','单位','未含税单价']); q.append(['A','项',12])
    c=w.create_sheet('组件'); c.append(['组件名称','数量']); c.append(['导体',1])
    d=w.create_sheet('偏差'); d.append(['偏差事项','投标文件响应']); d.append(['交期','无偏差'])
    w.save(p)
    r=dry_run(p,targets=[{'solidified_id':'A','target':'额定电压','unit':'kV'}],price_targets=[{'solidified_id':'A'}])
    assert [x['kind'] for x in r['sheets']]==['offer','price','component','deviation']
    assert r['sheets'][0]['matched']==1 and r['sheets'][1]['matched']==1

def test_unknown_id_is_warning(tmp_path):
    p=tmp_path/'x.xlsx'; w=Workbook(); ws=w.active; ws.append(['技术规范ID','参数名称','单位','投标人保证值']); ws.append(['X','a','m','1']); w.save(p)
    r=dry_run(p,targets=[{'solidified_id':'A','target':'a','unit':'m'}])
    assert r['sheets'][0]['matched']==0 and r['warnings']

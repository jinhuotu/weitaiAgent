import importlib.util,json,time,statistics
from pathlib import Path
from docx import Document
from api.services.tenders import section_materials as current
from api.services.tenders import placeholders
from api.services.tenders.schema import PlaceholderItem

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
before=load('before_sections','tmp/material-ssot/section_materials.before.py')
old_writer=load('before_writer','tmp/material-ssot/placeholders.before.py')
slots=[PlaceholderItem(key='proof'+str(i),title='独立核验凭证'+str(i),attachToSection='核验章节'+str(i)) for i in range(27)]
def source():
    doc=Document()
    for i in range(27):
        doc.add_paragraph('核验章节'+str(i),style='Heading 1')
        for j in range(6):doc.add_paragraph('原版正文及完整说明，不得修改或拆分。')
    return doc
report={};new_writer=placeholders._drop_after
for name,renderer,writer in [('before',before,old_writer._drop_after),('after',current,new_writer)]:
    values=[]
    for _ in range(3):
        doc=source();placeholders._drop_after=writer
        start=time.perf_counter()
        try:renderer.append_section_materials(doc,slots,{},[s.key for s in slots])
        finally:placeholders._drop_after=new_writer
        values.append(time.perf_counter()-start)
    report[name]={'seconds':values,'median':statistics.median(values)}
report['ratio']=report['after']['median']/report['before']['median']
Path('tmp/material-ssot/benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))

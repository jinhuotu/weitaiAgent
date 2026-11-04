import sys,json
from pathlib import Path
sys.path[:0]=['apps','packages']
from api.services.tender_package.recognition import recognize,describe
from api.services.tender_package.classify import classify
from api.services.tender_package.composed import compose_materials
m=json.loads(Path('tmp/real-package-manifest.json').read_text(encoding='utf-8'))
r=recognize(m,Path('tmp/real-package-role-input'))
assert r==compose_materials(classify(m,Path('tmp/real-package-role-input')),Path('tmp/real-package-role-input'))
s=describe(r);assert s['id_count']==10
Path('tmp/recognition-real-results.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(r['files']),roles=s['role_counts'],ids=s['id_count'],pending=s['pending_ids'],names=[dict(id=e['solidified_id'],name=e['material_name'],source=e['source_type']) for e in s['entries']]),ensure_ascii=False))

from pathlib import Path
import sys,json
from hashlib import sha256
sys.path[:0]=['apps','packages']
from api.services.tender_package.web import PackageStore
from api.services.tender_package.handoff import claim
repo=PackageStore('tmp/module5-real-import-check',987654)
source=Path('storage/tender-package-imports/85134f2a006a4bf78eec3c538ea3624e')
staged=repo.root.parent.parent/'tender-package-imports'/source.name
import shutil
shutil.copytree(source,staged,dirs_exist_ok=True)
s=claim(repo,source.name,'隔离迁移验证（非人工业务确认）','只验证现有数据迁移与来源哈希，不新增任何业务结论')
v=repo.view(s['id']);review=v['review']
assert review['catalog']['pending_count']==532 and review['catalog']['automatic_count']==237
assert len(review['approved_quotes']['sources'][0]['quotes'])==50
assert not review['packages'] and not v['ready_for_submission']
assert sha256(Path('tmp/real-package-ssot-v1-validated.json').read_bytes()).hexdigest()=='f071afa5587d7a019c0161cd8386db07139ec88997178ac290be4c323b0ade3f'
assert sha256(Path('tmp/real-package-module2-final.json').read_bytes()).hexdigest()=='f09240a6507e0dfc95116ff1886edfcc51f780db6bcbdf7cc3e0b6e4c3f8fe01'
print(json.dumps(dict(real_files=len(s['manifest']['files']),pending=532,automatic=237,approved_quotes=50,packages_confirmed=False,visual_accepted=False,old_sources_unchanged=True),ensure_ascii=False))

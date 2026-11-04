import json,hashlib
from pathlib import Path
root=Path('E:/code/python/weitaiAgentui');stage=Path('E:/code/python/weitaiAgent/tmp/wizard-ui')
for p,h in json.loads(Path('E:/code/python/weitaiAgent/tmp/wizard-ui-originals.json').read_text(encoding='utf-8')).items():
 assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h,'导航或路由已被其他操作修改，请重新比对'
files={'tender-package-wizard.ts':'src/lib/tender-package-wizard.ts','IssueSchemaField.vue':'src/components/tender-package/IssueSchemaField.vue','IssueDynamicForm.vue':'src/components/tender-package/IssueDynamicForm.vue','TenderPackageWizard.vue':'src/views/TenderPackageWizard.vue','nav.ts':'src/config/nav.ts','router.ts':'src/router/index.ts'}
for name,target in files.items():
 p=root/target;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((stage/name).read_bytes());print(target)

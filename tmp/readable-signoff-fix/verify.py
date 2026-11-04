from pathlib import Path
from xml.etree import ElementTree as ET
import difflib
import hashlib
import json

ROOT = Path('tmp/readable-signoff-fix')

def report(path):
    cases = ET.parse(path).findall('.//testcase')
    failed = {c.get('classname')+'::'+c.get('name') for c in cases
              if c.find('failure') is not None or c.find('error') is not None}
    skipped = sum(c.find('skipped') is not None for c in cases)
    return {'passed': len(cases)-len(failed)-skipped, 'failed': len(failed),
            'skipped': skipped}, failed

baseline, baseline_failures = report('tmp/slot-placement-fix/delivery-full.xml')
full, failures = report(ROOT/'verified-full.xml')
focused, _ = report(ROOT/'verified.xml')
parts=[]
hashes={}
for name in ['pdf_format','document','native_format']:
    before=ROOT/(name+'.before.py')
    after=Path('apps/api/services/tenders')/(name+'.py')
    parts.extend(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(True),
                                    after.read_text(encoding='utf-8').splitlines(True),
                                    fromfile=str(before),tofile=str(after)))
    hashes[str(after)] = hashlib.sha256(after.read_bytes()).hexdigest()
(ROOT/'render-only.diff').write_text(''.join(parts),encoding='utf-8')
results={'focused': focused, 'full': full, 'baseline': baseline,
         'newFailures': sorted(failures-baseline_failures),
         'resolvedBaselineFailures': sorted(baseline_failures-failures),
         'visual': json.loads((ROOT/'visual-results.json').read_text(encoding='utf-8')),
         'renderFileSha256': hashes, 'backendRestarted': False,
         'scopeDiff': 'render-only.diff'}
(ROOT/'verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in results.items() if k not in {'visual','renderFileSha256'}},ensure_ascii=True,indent=2))
assert not results['newFailures'], results['newFailures']

"""Restore pre-task functions in this test process only; never rewrite files."""
import importlib
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import pytest

ROOT=Path.cwd();sys.path[:0]=[str(ROOT/'apps'),str(ROOT/'packages')]
def restore(name, source):
    if os.environ.get('BISECT_MODULE') and os.environ['BISECT_MODULE'] != name:
        return
    module=importlib.import_module('api.services.tenders.'+name)
    exec(compile(source,module.__file__,'exec'),module.__dict__)

for name in ['material_identity','placeholders','section_materials','material_layout']:
    restore(name,(ROOT/'tmp/library-consolidation'/f'{name}.before.py').read_text(encoding='utf-8'))

source=(ROOT/'apps/api/services/tenders/company_materials.py').read_text(encoding='utf-8')
start=source.index('def source_material_evidence(');end=source.index('async def library_bidder_name(',start)
source=source[:start]+source[end:]
source=source.replace("    doc = source_material_evidence(doc, key)\n    if getattr(doc, '_material_type_conflict', False):\n        logging.getLogger(__name__).warning('material type conflict file_id=%s key=%s',doc.public_id,key)\n        return False\n",'')
source=source.replace("    if owner == '__conflicting_company__':\n        return False\n",'')
source=source.replace('allowed_ids, person_keys, notes, wrong_types = set(), set(), [], set()', 'allowed_ids, person_keys, notes = set(), set(), []')
source=source.replace("        evidence = source_material_evidence(doc, key)\n        if getattr(evidence,'_material_type_conflict',False):wrong_types.add(doc.public_id)\n        matched = document_matches_company(evidence, brief, key=key)","        matched = document_matches_company(doc, brief, key=key)")
source=source.replace('child_code=credit_code_from_document(evidence)','child_code=credit_code_from_document(doc)')
source=source.replace("        parent_evidence=source_material_evidence(parent,key) if parent is not None else None\n        parent_code=credit_code_from_document(parent_evidence) if parent_evidence is not None else ''", "        parent_code=credit_code_from_document(parent) if parent is not None else ''")
source=source.replace('credit_codes_from_document(evidence)','credit_codes_from_document(doc)')
source=source.replace("not company_from_document(evidence) and not getattr(evidence,'_material_type_conflict',False)", 'not company_from_document(doc)')
source=source.replace('from api.services.tenders.library_kb import library_id_meta', 'from api.services.tenders.id_card import load_id_meta')
source=source.replace('library_id_meta(doc)', 'load_id_meta(doc.summary)')
source=source.replace("                    explicit_request = source_key == 'other_materials' and 'material-key:' + key in (doc.tags or [])\n                    if not explicit_request and (target is None or find_catalog_item([target],title=source_title) is None):", "                    if target is None or find_catalog_item([target],title=source_title) is None:")
source=source.replace("                if doc is not None and doc.public_id in wrong_types:\n                    notes.append(f'资料「{path.name}」正文类型与资料槽位不符，未采用；请上传对应原件')\n                else:\n                    notes.append(f'资料「{path.name}」所属公司不符或尚未核验，未采用；请在资料库标明所属投标人')", "                notes.append(f'资料「{path.name}」所属公司不符或尚未核验，未采用；请在资料库标明所属投标人')")
assert 'source_material_evidence' not in source and 'wrong_types' not in source
restore('company_materials',source)

source=(ROOT/'apps/api/services/tenders/match.py').read_text(encoding='utf-8')
source=source.replace('("bond", "银行保函", r"(?:银行|电子)保函")','("bank_guarantee", "银行保函", r"银行保函")')
start=source.index('    from api.services.tenders.material_identity import standard_material_key',source.index('def attachment_catalog_item'))
end=source.index('    if request.key in ',start)
source=source[:start]+source[end:]
source=source.replace('"型号证明"','"桩型证明"')
restore('match',source)
restore('library_kb',(ROOT/'tmp/library-consolidation/library_kb.before.py').read_text(encoding='utf-8'))
source=(ROOT/'apps/api/services/tenders/document.py').read_text(encoding='utf-8')
source=source.replace('    from api.services.tenders.material_layout import mark_repeating_headers\n    mark_repeating_headers(doc)\n','')
restore('document',source)
source=(ROOT/'apps/api/services/tenders/assemble.py').read_text(encoding='utf-8')
source=source.replace('        from api.services.tenders.material_layout import mark_repeating_headers\n        header_rows = mark_repeating_headers(document)\n        if removed or header_rows:\n            document.save(path)\n        if removed:', '        if removed:\n            document.save(path)')
restore('assemble',source)

report=ET.parse('tmp/library-consolidation/all-tests.xml')
nodes=[case.get('classname').replace('.','/')+'.py::'+case.get('name')
       for case in report.iter('testcase') if case.find('failure') is not None]
if os.environ.get('BISECT_MODULE'):
    nodes=['tests/test_tender_assemble.py::test_assemble_biz_essentials_use_title_and_box']
if os.environ.get('BASELINE_FULL'):
    nodes=['tests']
    for filename in ['test_library_consolidation_migration.py','test_library_future_canonical.py',
                     'test_material_compact_headers.py','test_material_ocr_evidence.py']:
        nodes.extend(['--ignore','tests/tenders/'+filename])
raise SystemExit(pytest.main([*nodes,'-q','-p','no:cacheprovider','--basetemp','tmp/library-consolidation/baseline-tests',
                            '--junitxml','tmp/library-consolidation/baseline-tests.xml']))

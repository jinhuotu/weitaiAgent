import importlib.util,sys,pytest
from api.services.tenders import section_materials as sm,placeholders
from api.services.tenders import classifier
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
if sys.argv[1]=='before':
 old=load('old_sections','tmp/material-ssot/section_materials.before.py')
 for name in ['append_section_materials','sections','insert_material','validate_material_uniqueness']:
  setattr(sm,name,getattr(old,name))
 placeholders._drop_after=load('old_writer','tmp/material-ssot/placeholders.before.py')._drop_after
 classifier.material_output_coverage=load('old_classifier','tmp/material-ssot/classifier.before.py').material_output_coverage
files=['tests/test_tender_section_materials.py','tests/test_tender_section_types.py','tests/test_tender_section_ownership.py','tests/test_tender_id_bond_pagination.py','tests/test_tender_material_layout.py']
sys.exit(pytest.main([*files,'-q','--tb=short','-p','no:cacheprovider','--basetemp=tmp/material-ssot/check-'+sys.argv[1],'--junitxml=tmp/material-ssot/check-'+sys.argv[1]+'.xml']))

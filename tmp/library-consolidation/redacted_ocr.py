import sys,re,hashlib
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'apps'),str(Path.cwd()/'packages')]
from api.services.knowledge.ingest import read_extracted_text
from api.services.tenders.company_identity import COMPANY_RE
sys.stdout.reconfigure(encoding='utf-8')
for identity in ['ee8bd2c93c0e','1fa15ebc1c1e','31f661180e4d']:
 text=read_extracted_text('tenderlib01',identity) or ''
 text=re.sub(r'[0-9A-Za-z][0-9A-Za-z_\-]{5,}', '<ID>',text)
 text=COMPANY_RE.sub(lambda match:'<ENTITY_'+hashlib.sha256(match.group().encode()).hexdigest()[:8]+'>',text)
 print(identity,repr(text[:1000]))

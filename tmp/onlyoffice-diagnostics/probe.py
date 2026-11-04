import json
from pathlib import Path

from api.services.tenders.onlyoffice import check_document_server
from common.errors import AppError

try:
    report = {'ok': True, **check_document_server()}
except AppError as exc:
    report = {'ok': False, 'status': exc.status_code, 'message': exc.msg}
Path('tmp/onlyoffice-diagnostics/live-probe.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
)
print(json.dumps(report, ensure_ascii=True))

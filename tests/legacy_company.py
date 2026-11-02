"""Explicit legacy project data for old layout tests; never production defaults."""
import json
from pathlib import Path
from api.services.tenders.schema import BidBrief, COMPANY_FIELD_KEYS


def legacy_brief(**kwargs):
    source = json.loads((Path(__file__).parent / 'fixtures/legacy-company.json').read_text(encoding='utf-8'))
    fields = dict(source)
    fields.update(kwargs)
    return BidBrief(**fields)

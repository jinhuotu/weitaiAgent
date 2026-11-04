import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path[:0] = [str(Path.cwd() / 'apps'), str(Path.cwd() / 'packages')]
from sqlalchemy import select
from common.config import get_settings
from db.session import engine
from db.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeIngestTask
from api.services.tenders.library_kb import _is_material_parent, key_from_tags
from types import SimpleNamespace


async def main():
    engine.echo = False
    root = Path(get_settings().storage_root).resolve()
    async with engine.connect() as connection:
        base = (await connection.execute(select(KnowledgeBase.__table__).where(KnowledgeBase.public_id == 'tenderlib01'))).mappings().one()
        rows = list((await connection.execute(select(KnowledgeDocument.__table__).where(KnowledgeDocument.base_id == base['id']))).mappings())
        tasks = list((await connection.execute(select(KnowledgeIngestTask.__table__).where(KnowledgeIngestTask.base_id == base['id'], KnowledgeIngestTask.status.in_(['queued', 'running'])))).mappings())
    def files(row):
        found = []
        for key in dict.fromkeys([row['file_key'], row['storage_path']]):
            if not key: continue
            path = (root / key).resolve()
            if path.is_relative_to(root) and path.is_file() and path.stat().st_size:
                found.append(key)
        return found
    parents = [row for row in rows if _is_material_parent(SimpleNamespace(**row))]
    groups = []
    for row in parents:
        members = [row, *[child for child in rows if child['parent_id'] == row['public_id']]]
        available = [{'id': member['id'], 'public_id': member['public_id'], 'files': files(member),
                      'own_parent': member['id'] == row['id'], 'tags': member['tags'],
                      'summaryType': (member['summary'] or '').split(':', 1)[0] if (member['summary'] or '').startswith(('FACTSJSON:', 'PERFJSON:', 'IDJSON:')) else 'text'}
                     for member in members if files(member)]
        groups.append({'id': row['id'], 'public_id': row['public_id'], 'key': key_from_tags(row['tags']),
                       'title': row['name'], 'children': len(members)-1, 'files': available,
                       'missingReferences': sum(bool(member['file_key'] or member['storage_path']) and not files(member) for member in members)})
    duplicates = {key: count for key, count in Counter(group['key'] for group in groups).items() if count > 1}
    storage_files = [path for path in root.rglob('*') if path.is_file()]
    report = {'base': base['public_id'], 'parents': len(parents), 'withFiles': sum(bool(group['files']) for group in groups),
              'empty': sum(not group['files'] for group in groups), 'duplicateKeys': duplicates,
              'activeTasks': len(tasks), 'missingReferences': sum(group['missingReferences'] for group in groups),
              'storageFiles': len(storage_files), 'storageBytes': sum(path.stat().st_size for path in storage_files),
              'groups': groups}
    Path('tmp/library-consolidation/baseline.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**{key: value for key, value in report.items() if key != 'groups'},
                      'populated': [group for group in groups if group['files']]}, ensure_ascii=False))
    await engine.dispose()


asyncio.run(main())

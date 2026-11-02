"""One-time, backup-first consolidation. Default invocation is strictly read-only.

Run from any directory with the configured environment:
  python scripts/consolidate_tender_library.py
  python scripts/consolidate_tender_library.py --commit --mysql-container <name>

No business service that seeds, migrates or deletes library data is invoked.
Files are copied and verified before the DB transaction; old copies are removed
only after commit. Vector failures leave a replayable cleanup journal.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for folder in ('apps', 'packages'):
    sys.path.insert(0, str(ROOT / folder))

from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.engine import make_url
from common.config import get_settings
from db.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeIngestTask
from api.services.tenders.material_identity import standard_material_key
from api.services.tenders.schema import PlaceholderItem

# Explicit legacy identifiers supplied for this migration, never business rules.
LEGACY_KEYS = {'m5641425fda52': 'license', 'mf3e9bb2565a1': 'bank_permit',
               'm171d4830fbbe': 'bond', '10000': 'bond'}
DOC = KnowledgeDocument.__table__
BASE = KnowledgeBase.__table__
TASK = KnowledgeIngestTask.__table__


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode('utf-8')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def slot_key(row):
    return next((str(tag)[5:] for tag in row.get('tags') or [] if str(tag).startswith('slot:')), '')


def parent(row):
    return not row.get('parent_id') and ('投标资料' in (row.get('tags') or []) or slot_key(row))


def safe_path(root, key):
    relative = str(key or '').replace('\\', '/')
    if not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Unsafe storage key')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Storage path escapes configured root')
    return path


def original_paths(root, row):
    paths = []
    for key in dict.fromkeys([row.get('file_key'), row.get('storage_path')]):
        if not key:
            continue
        path = safe_path(root, key)
        if path.is_file() and path.stat().st_size:
            paths.append(path)
    if (row.get('file_key') or row.get('storage_path')) and not paths:
        raise ValueError(f"Unreadable original, refusing to treat it as empty: {row['public_id']}")
    if len({digest(path) for path in paths}) > 1:
        raise ValueError(f"Conflicting file_key/storage_path originals: {row['public_id']}")
    return paths


def snapshot(connection, base_id, *, lock=False):
    base_query = select(BASE).where(BASE.c.public_id == base_id)
    if lock:
        base_query = base_query.with_for_update()
    base = dict(connection.execute(base_query).mappings().one())
    query = select(DOC).where(DOC.c.base_id == base['id']).order_by(DOC.c.id)
    if lock:
        query = query.with_for_update()
    rows = [dict(row) for row in connection.execute(query).mappings()]
    active = connection.execute(select(TASK.c.id).where(TASK.c.base_id == base['id'],
                TASK.c.status.in_(['queued', 'running']))).first()
    if active:
        raise ValueError('Active ingest tasks exist; finish/cancel them before migration')
    return base, rows


def build_plan(base, rows, root, mapping=None):
    mapping = LEGACY_KEYS if mapping is None else mapping
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row.get('parent_id'), []).append(row)
    parents = [row for row in rows if parent(row)]
    families, empty, files, members = {}, [], {}, {}
    visited = set()
    for container in parents:
        group, pending = [], [container]
        while pending:
            row = pending.pop()
            if row['id'] in visited:
                raise ValueError('Cyclic/overlapping material ancestry')
            visited.add(row['id'])
            group.append(row)
            pending.extend(by_parent.get(row['public_id'], []))
        members[container['id']] = group
        available = []
        for row in group:
            paths = original_paths(root, row)
            if paths:
                files[row['id']] = paths
                available.append(row)
        if not available:
            empty.extend(group)
            continue
        key = slot_key(container)
        standard = mapping.get(key) or standard_material_key(PlaceholderItem(key=key, title=container['name']))
        if not standard:
            raise ValueError(f"Populated material has no unambiguous standard key: {key}")
        families.setdefault(standard, []).append(container)
    creates, deletes, updates, moves, preserved = [], {row['id']: row for row in empty}, [], [], []
    for standard, group in sorted(families.items()):
        target = next((row for row in group if slot_key(row) == standard), None)
        if target is None:
            public_id = 'consolidated_' + hashlib.sha256((base['public_id'] + ':' + standard).encode()).hexdigest()[:18]
            if any(row['public_id'] == public_id for row in rows):
                raise ValueError('Deterministic target ID is already occupied')
            target = dict(public_id=public_id, base_id=base['id'], name=standard, source='text', kind='drawing',
                          summary='标准投标资料容器', tags=['投标资料', 'slot:' + standard], status='ready',
                          review_status='approved', char_count=0, chunk_count=0, page_count=0, ocr_pages=0,
                          ocr_capped=False, formula_fallback=False)
            from api.services.tenders.placeholders import DEFAULT_SLOTS
            target['name'] = next((slot.title for slot in DEFAULT_SLOTS if slot.key == standard), standard)
            creates.append(target)
        for container in group:
            for row in members[container['id']]:
                tags = [tag for tag in row.get('tags') or [] if not str(tag).startswith('slot:')]
                if row['id'] != container['id']:
                    # Preserve positive/contradictory parent evidence before
                    # replacing the old ancestry with a shared standard root.
                    from api.services.tenders.company_identity import company_from_document, credit_codes_from_document
                    own, previous = SimpleNamespace(**row), SimpleNamespace(**container)
                    own_codes, previous_codes = credit_codes_from_document(own), credit_codes_from_document(previous)
                    previous_owner = company_from_document(previous)
                    if not company_from_document(own) and (not own_codes or own_codes == previous_codes):
                        if previous_owner and previous_owner != '__conflicting_company__':
                            tags.append('bidder:' + previous_owner)
                        tags.extend('credit_code:' + code for code in sorted(previous_codes))
                    elif own_codes and previous_codes and own_codes != previous_codes:
                        tags.extend('credit_code:' + code for code in sorted(previous_codes))
                tags = list(dict.fromkeys([*tags, '投标资料', 'slot:' + standard]))
                values = {'tags': tags}
                if row['public_id'] != target['public_id']:
                    values['parent_id'] = target['public_id']
                paths = files.get(row['id'], [])
                if paths:
                    source = paths[0]
                    sha = digest(source)
                    destination = f"tender-assets/slots/{standard}/{row['public_id']}{source.suffix.lower()}"
                    safe_path(root, destination)
                    preserved.append({'id': row['id'], 'public_id': row['public_id'], 'key': standard,
                                      'sha256': sha, 'summary': row.get('summary'),
                                      'evidenceTags': [tag for tag in tags if not str(tag).startswith('slot:')]})
                    values.update(file_key=destination, storage_path=destination)
                    for path in paths:
                        if path != safe_path(root, destination):
                            moves.append({'source': path.relative_to(root).as_posix(), 'destination': destination, 'sha256': sha})
                elif row['id'] == container['id'] and row['public_id'] != target['public_id']:
                    deletes[row['id']] = row
                    continue
                # Keep original file IDs, summaries, company evidence and ingest
                # history. A file-bearing old root becomes a child, not a new file.
                if any(row.get(key) != value for key, value in values.items()):
                    updates.append({'id': row['id'], 'public_id': row['public_id'], 'values': values})
    result = {'base': base['public_id'], 'baseId': base['id'],
              'snapshotHash': hashlib.sha256(json_bytes(rows)).hexdigest(),
              'before': {'parents': len(parents), 'emptyParents': sum(not any(row['id'] in files for row in members[p['id']]) for p in parents),
                         'populatedParents': sum(any(row['id'] in files for row in members[p['id']]) for p in parents)},
              'create': creates, 'update': updates,
              'delete': [{'id': row['id'], 'public_id': row['public_id'], 'key': slot_key(row), 'title': row['name']} for row in deletes.values()],
              'move': moves, 'preserved': preserved, 'standardKeys': sorted(families)}
    result['planHash'] = hashlib.sha256(json_bytes(result)).hexdigest()
    return result


def changes(plan):
    return any(plan[key] for key in ('create', 'update', 'delete', 'move'))


def print_plan(plan):
    print(json.dumps({'dryRun': True, **{key: plan[key] for key in ('base', 'before', 'standardKeys', 'planHash')},
                      'counts': {key: len(plan[key]) for key in ('create', 'update', 'delete', 'move', 'preserved')}}, ensure_ascii=False), flush=True)
    for row in plan['create']:
        print('将新建标准容器 ' + slot_key(row) + ' ' + row['public_id'], flush=True)
    for row in plan['update']:
        print('将迁移/归并 ' + row['public_id'] + ' -> ' + slot_key(row['values']), flush=True)
    for row in plan['move']:
        print('将迁移文件 ' + row['source'] + ' -> ' + row['destination'], flush=True)
    for row in plan['delete']:
        print('将删除空项/旧容器 ' + row['public_id'] + ' key=' + row['key'] + ' ' + row['title'], flush=True)


def mysql_config(url, container=False):
    def quote(value):
        return '"' + str(value or '').replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
    return ('[client]\nuser=' + quote(url.username) + '\npassword=' + quote(url.password)
            + '\nhost=' + quote('127.0.0.1' if container else url.host)
            + '\nport=' + str(3306 if container else (url.port or 3306)) + '\n').encode()


def backup_database(connection, url, directory, mysql_container=None):
    target = directory / 'database.sql'
    if connection.dialect.name == 'sqlite':
        raw = connection.connection.driver_connection
        target.write_text('\n'.join(raw.iterdump()), encoding='utf-8')
    else:
        if connection.dialect.name != 'mysql':
            raise ValueError('Only MySQL and SQLite backup are supported')
        prefix = ['docker', 'exec', '-i', mysql_container] if mysql_container else []
        if not prefix and not shutil.which('mysqldump'):
            raise ValueError('mysqldump unavailable: provide --mysql-container')
        config = mysql_config(url, bool(prefix))
        # Verify that a supplied container is the server used by the application.
        if prefix:
            check = subprocess.run([*prefix, 'mysql', '--defaults-extra-file=/dev/stdin', '--batch', '--skip-column-names',
                                    '-e', 'SELECT @@server_uuid'], input=config, capture_output=True, check=False)
            expected = str(connection.execute(text('SELECT @@server_uuid')).scalar_one()).strip()
            if check.returncode or check.stdout.decode().strip() != expected:
                raise ValueError('Backup container does not match the connected database server')
        command = [*prefix, 'mysqldump', '--defaults-extra-file=/dev/stdin', '--single-transaction', '--routines',
                   '--events', '--triggers', '--hex-blob', '--no-tablespaces', '--set-gtid-purged=OFF', url.database]
        with target.open('wb') as stream:
            result = subprocess.run(command, input=config, stdout=stream, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            # Do not echo raw client diagnostics which could include credentials.
            raise RuntimeError(f'Database backup failed, exit={result.returncode}; no migration performed')
    if target.stat().st_size == 0:
        raise RuntimeError('Database backup is empty')
    return {'file': target.name, 'sha256': digest(target), 'bytes': target.stat().st_size}


def backup_storage(root, directory):
    target = directory / 'storage.zip'
    hashes = {}
    # Store rather than recompress already compressed scans and DOCX files.
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for index, path in enumerate(sorted(root.rglob('*')), 1):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Storage contains an external link; backup aborted')
            if not path.is_file():
                continue
            key = path.relative_to(root).as_posix()
            hashes[key] = digest(path)
            archive.write(path, key)
            if index % 500 == 0:
                print(f'备份 storage 文件 {len(hashes)}', flush=True)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Storage backup CRC verification failed')
        for key, sha in hashes.items():
            with archive.open(key) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != sha:
                    raise RuntimeError('Storage backup hash verification failed')
    return {'file': target.name, 'sha256': digest(target), 'files': hashes}


def vector_filter(base_id, doc_id=None):
    from qdrant_client.http import models
    conditions = [models.FieldCondition(key='kb_id', match=models.MatchValue(value=base_id))]
    if doc_id:
        conditions.append(models.FieldCondition(key='doc_id', match=models.MatchValue(value=doc_id)))
    return models.Filter(must=conditions)


def backup_vectors(store, base_id, directory):
    points, offset = [], None
    existing = {collection.name for collection in store.client.get_collections().collections}
    if store.collection in existing:
        while True:
            batch, offset = store.client.scroll(store.collection, scroll_filter=vector_filter(base_id),
                                               offset=offset, limit=100, with_vectors=True, with_payload=True)
            points.extend(point.model_dump(mode='json') for point in batch)
            if offset is None:
                break
    target = directory / 'vectors.json'
    target.write_bytes(json_bytes({'collection': store.collection, 'points': points}))
    return {'file': target.name, 'sha256': digest(target), 'points': len(points)}


def connect_vectors(settings):
    """Use the configured local endpoint directly, without ambient proxies."""
    from qdrant_client import QdrantClient
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None,
                          trust_env=False, check_compatibility=False, timeout=30)
    try:
        client.get_collections()
    except Exception:
        client.close()
        raise
    return SimpleNamespace(client=client, collection=settings.qdrant_collection)


def clean_vectors(store, plan):
    from qdrant_client.http import models
    names = {collection.name for collection in store.client.get_collections().collections}
    if store.collection not in names:
        return {'remainingDeletedPoints': 0}
    for row in plan['delete']:
        store.client.delete(store.collection, points_selector=models.FilterSelector(
            filter=vector_filter(plan['base'], row['public_id'])), wait=True)
    for row in plan['update']:
        store.client.set_payload(store.collection, payload={'tags': row['values']['tags']},
                                 points=vector_filter(plan['base'], row['public_id']), wait=True)
    deleted_ids = [row['public_id'] for row in plan['delete']]
    remaining = 0
    if deleted_ids:
        remaining = store.client.count(store.collection, count_filter=models.Filter(must=[
            models.FieldCondition(key='kb_id', match=models.MatchValue(value=plan['base'])),
            models.FieldCondition(key='doc_id', match=models.MatchAny(any=deleted_ids)),
        ]), exact=True).count
    if remaining:
        raise RuntimeError('Old-parent vectors still exist')
    return {'remainingDeletedPoints': remaining, 'deletedDocumentRefs': len(deleted_ids),
            'updatedDocumentRefs': len(plan['update'])}


def apply_database(connection, plan):
    for row in plan['create']:
        values = dict(row)
        if connection.dialect.name == 'sqlite':
            values['id'] = int(connection.execute(select(func.max(DOC.c.id))).scalar() or 0) + 1
        connection.execute(DOC.insert().values(**values))
    for row in plan['update']:
        connection.execute(update(DOC).where(DOC.c.id == row['id'], DOC.c.base_id == plan['baseId']).values(**row['values']))
    if plan['delete']:
        connection.execute(delete(DOC).where(DOC.c.base_id == plan['baseId'], DOC.c.id.in_([row['id'] for row in plan['delete']])))


def validate_after(connection, root, plan):
    base, rows = snapshot(connection, plan['base'])
    after = build_plan(base, rows, root, mapping={})
    if changes(after):
        raise RuntimeError('Migration is not idempotent; transaction aborted')
    by_id = {row['id']: row for row in rows}
    by_hash = {}
    for row in rows:
        paths = original_paths(root, row)
        if paths:by_hash.setdefault(digest(paths[0]), []).append(row)
    for original in plan['preserved']:
        row = by_id.get(original['id'])
        if row is None:
            # Existing library reads may content-deduplicate identical children.
            # Accept an alias only when bytes AND identity evidence survived.
            row = next((candidate for candidate in by_hash.get(original['sha256'], [])
                        if candidate.get('summary') == original['summary'] and
                        [tag for tag in candidate.get('tags') or [] if not str(tag).startswith('slot:')]
                        == original['evidenceTags']), None)
            if row is None:
                raise RuntimeError('Original content/identity evidence no longer exists')
        paths = original_paths(root, row)
        if not paths or digest(paths[0]) != original['sha256'] or row.get('summary') != original['summary']:
            raise RuntimeError('Original file/identity evidence changed')
        evidence = [tag for tag in row.get('tags') or [] if not str(tag).startswith('slot:')]
        if evidence != original['evidenceTags']:
            raise RuntimeError('Company/identity tags changed')
    keys = [slot_key(row) for row in rows if parent(row)]
    if len(keys) != len(set(keys)) or set(keys) != set(plan['standardKeys']):
        raise RuntimeError('Non-unique standard parents after migration')
    return {'parents': len(keys), 'keys': sorted(keys), 'originals': len(plan['preserved']),
            'emptyParents': after['before']['emptyParents'], 'idempotent': True}


def execute(connection, url, root, plan, directory, *, mysql_container=None, vector_store=None):
    directory = directory.resolve()
    if directory.is_relative_to(root):
        raise ValueError('Backup directory must be outside storage')
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'plan.json').write_bytes(json_bytes(plan))
    print('开始完整备份 ' + str(directory), flush=True)
    manifest = {'database': backup_database(connection, url, directory, mysql_container),
                'storage': backup_storage(root, directory)}
    if vector_store is not None:
        manifest['vectors'] = backup_vectors(vector_store, plan['base'], directory)
    (directory / 'manifest.json').write_bytes(json_bytes(manifest))
    connection.commit()  # end the backup's read transaction
    # The journal exists before commit, so even a process interruption between
    # DB commit and cleanup has a durable, explicitly replayable plan.
    journal = directory / 'pending-cleanup.json'
    journal.write_bytes(json_bytes(plan))
    created = []
    committed = False
    try:
        with connection.begin():
            base, rows = snapshot(connection, plan['base'], lock=True)
            current = build_plan(base, rows, root)
            if current['planHash'] != plan['planHash']:
                raise RuntimeError('Data/files changed after dry-run or backup; no migration performed')
            for move in plan['move']:
                source, destination = safe_path(root, move['source']), safe_path(root, move['destination'])
                if digest(source) != move['sha256']:
                    raise RuntimeError('Source changed after backup')
                if destination.exists():
                    if digest(destination) != move['sha256']:
                        raise RuntimeError('Destination exists with different content')
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    created.append(destination)
                if digest(destination) != move['sha256']:
                    raise RuntimeError('Copy verification failed')
            apply_database(connection, plan)
            result = validate_after(connection, root, plan)
        committed = True
    finally:
        if not committed:
            for path in created:
                path.unlink(missing_ok=True)
    # Persist the cleanup journal before doing nontransactional side effects.
    finish_cleanup(connection, root, plan, directory, vector_store)
    (directory / 'result.json').write_bytes(json_bytes(result))
    print(json.dumps({'committed': True, **result, 'backup': str(directory),
                      'pendingVectorCleanup': journal.exists()}, ensure_ascii=False), flush=True)
    return result


def finish_cleanup(connection, root, plan, directory, vector_store):
    # Only remove old copies that no current DB row anywhere still references.
    references = set()
    for row in connection.execute(select(DOC.c.file_key, DOC.c.storage_path)).mappings():
        references.update(value.replace('\\', '/') for value in row.values() if value)
    for move in plan['move']:
        if move['source'] not in references:
            source = safe_path(root, move['source'])
            if source.exists():
                if digest(source) != move['sha256']:
                    raise RuntimeError('Old copy changed; refusing cleanup')
                source.unlink()
    for row in plan['delete']:
        for suffix in ('.txt', '.asr.json'):
            key = f"knowledge/{plan['base']}/{row['public_id']}{suffix}"
            if key not in references:
                safe_path(root, key).unlink(missing_ok=True)
        key = row['key']
        if key and all(character.isalnum() or character in '_-' for character in key):
            folder = safe_path(root, 'tender-assets/slots/' + key)
            if folder.is_dir():
                for child in sorted(folder.rglob('*'), key=lambda value: len(value.parts), reverse=True):
                    if child.is_dir() and not child.is_symlink() and not any(child.iterdir()):
                        child.rmdir()
                if not any(folder.iterdir()):
                    folder.rmdir()
    try:
        if vector_store is None:
            raise RuntimeError('Vector store unavailable')
        present = set(connection.execute(select(DOC.c.public_id).where(DOC.c.base_id == plan['baseId'])).scalars())
        missing_aliases = [{'public_id': row['public_id']} for row in plan['preserved'] if row['public_id'] not in present]
        vector_result = clean_vectors(vector_store, dict(plan, delete=[*plan['delete'], *missing_aliases]))
    except Exception as exc:
        print('警告：向量清理待补偿 ' + type(exc).__name__ + '；用 --commit --resume 重试', flush=True)
    else:
        (directory / 'cleanup-result.json').write_bytes(json_bytes(vector_result))
        (directory / 'pending-cleanup.json').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', action='store_true', help='back up, verify and apply the printed plan')
    parser.add_argument('--base-id', default='tenderlib01')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--mysql-container', help='optional MySQL container for mysqldump')
    parser.add_argument('--expected-plan', help='refuse if the reviewed dry-run hash changed')
    parser.add_argument('--resume', type=Path, help='replay only pending filesystem/vector cleanup from this backup')
    args = parser.parse_args()
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.drivername.startswith('mysql+'):
        url = url.set(drivername='mysql+pymysql')
    root = Path(settings.storage_root).expanduser().resolve()
    engine = create_engine(url, echo=False, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if args.resume:
                if not args.commit:
                    raise ValueError('--resume also requires --commit')
                journal = args.resume / 'pending-cleanup.json'
                plan = json.loads((journal if journal.exists() else args.resume / 'plan.json').read_text(encoding='utf-8'))
                validate_after(connection, root, plan)
            else:
                base, rows = snapshot(connection, args.base_id)
                plan = build_plan(base, rows, root)
                print_plan(plan)
                if not args.commit or not changes(plan):
                    return
                if args.expected_plan and args.expected_plan != plan['planHash']:
                    raise ValueError('Reviewed plan hash changed')
            try:
                vector_store = connect_vectors(settings)
            except Exception:
                vector_store = None
            if args.resume:
                manifest_path = args.resume / 'manifest.json'
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if vector_store is not None and 'vectors' not in manifest:
                    manifest['vectors'] = backup_vectors(vector_store, plan['base'], args.resume)
                    manifest_path.write_bytes(json_bytes(manifest))
                finish_cleanup(connection, root, plan, args.resume, vector_store)
                if vector_store is not None:
                    vector_store.client.close()
                return
            directory = args.backup_dir or ROOT / 'backups' / 'tender-library' / (
                datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
            execute(connection, url, root, plan, directory, mysql_container=args.mysql_container, vector_store=vector_store)
            if vector_store is not None:
                vector_store.client.close()
    finally:
        engine.dispose()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()

"""Backup, dry-run, rollback, identity preservation and replay of stock cleanup."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from sqlalchemy import create_engine, event, select
from scripts import consolidate_tender_library as migration


@pytest.fixture
def stock(tmp_path):
    root = tmp_path / 'storage'
    root.mkdir()
    engine = create_engine('sqlite:///' + (tmp_path / 'stock.sqlite').as_posix())
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')
    for table in (migration.BASE, migration.DOC, migration.TASK):
        table.create(engine)
    with engine.begin() as connection:
        connection.execute(migration.BASE.insert().values(id=1, public_id='tenderlib01', name='测试资料库'))
        index = 0
        def add(key, count=0, parent_key=None, own_file=False, tags=()):
            nonlocal index
            index += 1
            number = index
            public = 'record' + str(number)
            values = dict(id=number, public_id=public, base_id=1, name=key, kind='doc' if parent_key else 'drawing',
                          source='file' if parent_key else 'text', tags=['投标资料', 'slot:' + key, *tags],
                          parent_id=parent_key, status='ready', review_status='approved', summary='original metadata')
            if own_file:
                path = root / 'knowledge' / 'tenderlib01' / (public + '.png')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(('original:' + public).encode())
                values['file_key'] = path.relative_to(root).as_posix()
                values['storage_path'] = values['file_key']
            connection.execute(migration.DOC.insert().values(**values))
            for _ in range(count):
                add(key, parent_key=public, own_file=True, tags=tags)
            return public
        add('id_legal', 2)
        for key in ('id_agent', 'credit', 'bond', 'm5641425fda52', 'mf3e9bb2565a1', 'm171d4830fbbe', '10000'):
            add(key, 1)
        for key in ['license', 'license', 'iso', 'iso', *['empty_' + str(i) for i in range(267)]]:
            add(key)
    yield engine, root, tmp_path
    engine.dispose()


def plan_of(connection, root):
    return migration.build_plan(*migration.snapshot(connection, 'tenderlib01'), root)


def local_vectors():
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
    client = QdrantClient(location=':memory:')
    client.create_collection('materials', vectors_config=models.VectorParams(size=1, distance=models.Distance.COSINE))
    return SimpleNamespace(client=client, collection='materials')


def test_dry_run_is_read_only_and_does_not_contact_vectors(stock, monkeypatch, capsys):
    engine, root, directory = stock
    before = {path.relative_to(directory).as_posix(): migration.digest(path) for path in directory.rglob('*') if path.is_file()}
    monkeypatch.setattr(migration, 'get_settings', lambda: SimpleNamespace(database_url=str(engine.url), storage_root=str(root)))
    monkeypatch.setattr('sys.argv', ['consolidate', '--base-id', 'tenderlib01'])
    migration.main()
    assert '"dryRun": true' in capsys.readouterr().out
    after = {path.relative_to(directory).as_posix(): migration.digest(path) for path in directory.rglob('*') if path.is_file()}
    assert before == after


def test_full_backup_merge_and_second_run_are_idempotent(stock):
    engine, root, directory = stock
    store = local_vectors()
    try:
        with engine.connect() as connection:
            plan = plan_of(connection, root)
            assert plan['before'] == {'parents': 279, 'emptyParents': 271, 'populatedParents': 8}
            assert len(plan['preserved']) == 9 and len(plan['delete']) == 275
            source_hashes = {row['sha256'] for row in plan['preserved']}
            result = migration.execute(connection, engine.url, root, plan, directory / 'backup', vector_store=store)
            assert result == {'parents': 6, 'keys': ['bank_permit', 'bond', 'credit', 'id_agent', 'id_legal', 'license'],
                              'originals': 9, 'emptyParents': 0, 'idempotent': True}
            assert not migration.changes(plan_of(connection, root))
        manifest = json.loads((directory / 'backup/manifest.json').read_text(encoding='utf-8'))
        assert migration.digest(directory / 'backup/database.sql') == manifest['database']['sha256']
        restored = sqlite3.connect(':memory:')
        restored.executescript((directory / 'backup/database.sql').read_text(encoding='utf-8'))
        assert restored.execute('SELECT COUNT(*) FROM knowledge_documents').fetchone()[0] == 288
        restored.close()
        with zipfile.ZipFile(directory / 'backup/storage.zip') as archive:
            assert len(archive.namelist()) == 9
        assert {migration.digest(path) for path in root.rglob('*.png')} == source_hashes
        assert not (directory / 'backup/pending-cleanup.json').exists()
    finally:
        store.client.close()


def test_backup_failure_cannot_mutate_database_or_files(stock, monkeypatch):
    engine, root, directory = stock
    with engine.connect() as connection:
        plan = plan_of(connection, root)
        monkeypatch.setattr(migration, 'backup_storage', Mock(side_effect=OSError('backup disk unavailable')))
        with pytest.raises(OSError):
            migration.execute(connection, engine.url, root, plan, directory / 'backup-failed')
        assert plan_of(connection, root)['planHash'] == plan['planHash']
    assert len(list(root.rglob('*.png'))) == 9


def test_destination_collision_rolls_back_and_keeps_originals(stock):
    engine, root, directory = stock
    with engine.connect() as connection:
        plan = plan_of(connection, root)
        destination = migration.safe_path(root, plan['move'][0]['destination'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'not the original')
        with pytest.raises(RuntimeError, match='different content'):
            migration.execute(connection, engine.url, root, plan, directory / 'collision')
        assert plan_of(connection, root)['planHash'] == plan['planHash']
        assert all(migration.safe_path(root, row['source']).exists() for row in plan['move'])


def test_vectors_unavailable_leave_replayable_journal(stock):
    engine, root, directory = stock
    with engine.connect() as connection:
        plan = plan_of(connection, root)
        migration.execute(connection, engine.url, root, plan, directory / 'pending')
        assert (directory / 'pending/pending-cleanup.json').is_file()
        store = local_vectors()
        try:
            migration.finish_cleanup(connection, root, plan, directory / 'pending', store)
            assert not (directory / 'pending/pending-cleanup.json').exists()
            assert not migration.changes(plan_of(connection, root))
        finally:
            store.client.close()


def test_missing_file_is_never_deleted_as_empty(stock):
    engine, root, _directory = stock
    with engine.connect() as connection:
        row = connection.execute(select(migration.DOC).where(migration.DOC.c.file_key.is_not(None))).mappings().first()
        migration.safe_path(root, row['file_key']).unlink()
        with pytest.raises(ValueError, match='Unreadable original'):
            plan_of(connection, root)


def test_file_bearing_root_keeps_its_id_and_becomes_a_child(stock):
    engine, root, directory = stock
    with engine.begin() as connection:
        connection.execute(migration.DOC.delete())
        path = root / 'root-original.png'
        path.write_bytes(b'root original')
        connection.execute(migration.DOC.insert().values(id=1, public_id='root-original', base_id=1,
            name='营业执照', tags=['投标资料', 'slot:mold', 'bidder:原证据主体'], file_key=path.name,
            storage_path=path.name, source='file', kind='doc', summary='original facts', status='ready', review_status='approved'))
    with engine.connect() as connection:
        plan = plan_of(connection, root)
        migration.execute(connection, engine.url, root, plan, directory / 'root-backup')
        row = connection.execute(select(migration.DOC).where(migration.DOC.c.id == 1)).mappings().one()
        assert row['parent_id'] and row['summary'] == 'original facts' and row['public_id'] == 'root-original'

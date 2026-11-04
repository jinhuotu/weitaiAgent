-- Running downgrade 0018_document_expiry -> 0017_library_dismissals

ALTER TABLE knowledge_documents DROP COLUMN is_perpetual;

ALTER TABLE knowledge_documents DROP COLUMN expire_at;

UPDATE alembic_version SET version_num='0017_library_dismissals' WHERE alembic_version.version_num = '0018_document_expiry';


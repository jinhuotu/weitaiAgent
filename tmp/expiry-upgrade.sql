-- Running upgrade 0017_library_dismissals -> 0018_document_expiry

ALTER TABLE knowledge_documents ADD COLUMN expire_at DATE;

ALTER TABLE knowledge_documents ADD COLUMN is_perpetual BOOL DEFAULT false;

UPDATE alembic_version SET version_num='0018_document_expiry' WHERE alembic_version.version_num = '0017_library_dismissals';


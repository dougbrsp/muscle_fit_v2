-- Copiado de fitness-platform/mysql/init/012_training_types_rag_folder.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

ALTER TABLE training_types ADD COLUMN rag_folder VARCHAR(255) NULL AFTER description;

-- Copiado de fitness-platform/mysql/init/010_activities_training_type.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 010_activities_training_type.sql
--
-- Motor de geracao de ficha v2 — vincula uma activity registrada ao
-- Tipo de Treinamento (training_types) que a originou, para que a tela
-- /registro-exercicios/[activityId] possa exibir, ao lado da ativacao
-- muscular real (GET /analytics/muscle-activation-by-activity), as metas
-- semanais por grupo muscular daquele training_type
-- (training_type_muscle_group_targets), reaproveitando
-- training_types.get_training_type_muscle_group_targets ja usada pelo
-- TemplateVolumePlanner.
--
-- Adiciona `activities.training_type_id BIGINT NULL`, FK para
-- `training_types.id` ON DELETE SET NULL (mesmo padrao de `activities.gym_id`
-- -> `gyms.id`). Opcional: activities antigas ou lancadas sem vinculo a um
-- training_type continuam com training_type_id NULL.
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005/006/007/008/009_*.sql).
-- =============================================================================

USE fitness;

SET @activities_has_training_type_id = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities'
    AND COLUMN_NAME = 'training_type_id'
);
SET @activities_training_type_id_ddl = IF(@activities_has_training_type_id = 0,
  'ALTER TABLE activities ADD COLUMN training_type_id BIGINT NULL AFTER gym_id',
  'SELECT 1'
);
PREPARE activities_training_type_id_stmt FROM @activities_training_type_id_ddl;
EXECUTE activities_training_type_id_stmt;
DEALLOCATE PREPARE activities_training_type_id_stmt;

SET @activities_has_training_type_fk = (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities'
    AND CONSTRAINT_NAME = 'fk_activities_training_type'
    AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @activities_training_type_fk_ddl = IF(@activities_has_training_type_fk = 0,
  'ALTER TABLE activities ADD CONSTRAINT fk_activities_training_type FOREIGN KEY (training_type_id) REFERENCES training_types(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE activities_training_type_fk_stmt FROM @activities_training_type_fk_ddl;
EXECUTE activities_training_type_fk_stmt;
DEALLOCATE PREPARE activities_training_type_fk_stmt;

SET @activities_has_training_type_idx = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities'
    AND INDEX_NAME = 'idx_activities_training_type_id'
);
SET @activities_training_type_idx_ddl = IF(@activities_has_training_type_idx = 0,
  'ALTER TABLE activities ADD INDEX idx_activities_training_type_id (training_type_id)',
  'SELECT 1'
);
PREPARE activities_training_type_idx_stmt FROM @activities_training_type_idx_ddl;
EXECUTE activities_training_type_idx_stmt;
DEALLOCATE PREPARE activities_training_type_idx_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (36, 'Motor de ficha v2: activities ganha training_type_id (FK opcional para training_types, ON DELETE SET NULL) — vincula a activity registrada ao Tipo de Treinamento que a originou, consumido por GET /analytics/muscle-activation-by-activity para expor as metas semanais por grupo muscular ao lado da ativacao real');

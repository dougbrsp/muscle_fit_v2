-- Copiado de fitness-platform/mysql/init/006_workout_plan_template_slot_items_nullable_sets.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

USE fitness;

-- Item de cardio não tem número de séries (sets_count é sempre NULL para
-- item_kind='cardio', ver validação em workout_plan_templates.py). A coluna
-- foi criada por engano como NOT NULL DEFAULT 3 em 005_workout_plan_templates.sql,
-- causando IntegrityError (1048) ao salvar um slot com item de cardio.
SET @col_nullable = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'sets_count'
    AND IS_NULLABLE = 'YES'
);
SET @ddl = IF(@col_nullable = 0,
  'ALTER TABLE workout_plan_template_slot_items MODIFY COLUMN sets_count INT NULL DEFAULT 3',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (30, 'workout_plan_template_slot_items.sets_count passa a aceitar NULL (item de cardio nao tem series) - corrige IntegrityError 1048 ao salvar modelo com cardio');

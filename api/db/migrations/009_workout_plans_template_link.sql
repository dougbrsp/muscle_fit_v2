-- Copiado de fitness-platform/mysql/init/007_workout_plans_template_link.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 007_workout_plans_template_link.sql
--
-- Feature "Modelos de Ficha de Treino Personalizados" — escolha de slot na
-- geracao (manual OU continuar do ultimo treino registrado que usou o
-- modelo), ver app/workout_plan_templates_engine.py::resolve_next_slot.
--
-- Introduz em workout_plans:
--   - template_id: de qual modelo esta ficha SALVA veio (NULL quando a
--     ficha nao veio de nenhum modelo). FK para workout_plan_templates(id)
--     ON DELETE SET NULL — apagar o modelo nao deve apagar fichas ja salvas
--     que vieram dele, so perder a referencia.
--   - template_slot_sequence: snapshot do `sequence` do slot usado nesta
--     geracao — NAO e FK para a linha do slot (workout_plan_template_slots),
--     ja que slots podem ser removidos/renumerados depois via edicao do
--     modelo no builder; e apenas um numero historico, usado por
--     resolve_next_slot para calcular "o proximo depois do ultimo".
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005_workout_plan_templates.sql/006_workout_plan_template_slot_items_nullable_sets.sql).
-- =============================================================================

USE fitness;

-- -----------------------------------------------------------------------------
-- 1. workout_plans.template_id
-- -----------------------------------------------------------------------------
SET @wp_has_template_id = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND COLUMN_NAME = 'template_id'
);
SET @wp_template_id_ddl = IF(@wp_has_template_id = 0,
  'ALTER TABLE workout_plans ADD COLUMN template_id BIGINT NULL AFTER split_day',
  'SELECT 1'
);
PREPARE wp_template_id_stmt FROM @wp_template_id_ddl; EXECUTE wp_template_id_stmt; DEALLOCATE PREPARE wp_template_id_stmt;

-- -----------------------------------------------------------------------------
-- 2. workout_plans.template_slot_sequence
-- -----------------------------------------------------------------------------
SET @wp_has_template_slot_sequence = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND COLUMN_NAME = 'template_slot_sequence'
);
SET @wp_template_slot_sequence_ddl = IF(@wp_has_template_slot_sequence = 0,
  'ALTER TABLE workout_plans ADD COLUMN template_slot_sequence TINYINT NULL AFTER template_id',
  'SELECT 1'
);
PREPARE wp_template_slot_sequence_stmt FROM @wp_template_slot_sequence_ddl;
EXECUTE wp_template_slot_sequence_stmt;
DEALLOCATE PREPARE wp_template_slot_sequence_stmt;

-- -----------------------------------------------------------------------------
-- 3. FK workout_plans.template_id -> workout_plan_templates.id ON DELETE SET NULL
-- -----------------------------------------------------------------------------
SET @wp_has_template_fk = (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND CONSTRAINT_NAME = 'fk_wp_template'
);
SET @wp_template_fk_ddl = IF(@wp_has_template_fk = 0,
  'ALTER TABLE workout_plans ADD CONSTRAINT fk_wp_template FOREIGN KEY (template_id) REFERENCES workout_plan_templates (id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE wp_template_fk_stmt FROM @wp_template_fk_ddl; EXECUTE wp_template_fk_stmt; DEALLOCATE PREPARE wp_template_fk_stmt;

-- -----------------------------------------------------------------------------
-- 4. Index de apoio para a consulta de resolve_next_slot (ultimo plano salvo
-- por usuario+template)
-- -----------------------------------------------------------------------------
SET @wp_has_template_idx = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND INDEX_NAME = 'idx_wp_user_template'
);
SET @wp_template_idx_ddl = IF(@wp_has_template_idx = 0,
  'ALTER TABLE workout_plans ADD INDEX idx_wp_user_template (user_id, template_id)',
  'SELECT 1'
);
PREPARE wp_template_idx_stmt FROM @wp_template_idx_ddl; EXECUTE wp_template_idx_stmt; DEALLOCATE PREPARE wp_template_idx_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (31, 'workout_plans ganha template_id (FK SET NULL) e template_slot_sequence (snapshot, sem FK) para a escolha de slot manual/automatico na geracao a partir de modelos');

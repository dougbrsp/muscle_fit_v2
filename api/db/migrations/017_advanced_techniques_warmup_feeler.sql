-- Copiado de fitness-platform/mysql/init/014_advanced_techniques_warmup_feeler.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 014_advanced_techniques_warmup_feeler.sql
--
-- Feature "Modo Equipamentos e séries: técnica avançada + aquecimento/ajuste
-- configurados". Enriquece cada card de exercício do modo
-- volume_mode='equipment_series' (workout_plan_template_slot_items,
-- item_kind='equipment') com:
--   1. técnica avançada aplicada a UM único exercício/série (família
--      rest_pause | drop_set | fst7 | gvt | forced_reps | negative_reps |
--      partial_reps | cheat_reps — RAG em
--      workspace/rag/markdown/TECNICAS_AVANCADAS.md). Bi-set/tri-set/giant
--      set/pré-exaustão/pós-exaustão/super-série ficam FORA de escopo (são
--      combinações de múltiplos exercícios, não cabem no modelo "1 card = 1
--      técnica").
--   2. série de aquecimento CONFIGURADA (warmup_reps/warmup_rest_seconds) —
--      campos NOVOS, NAO relacionados ao `warmup BOOLEAN` legado de
--      workout_plan_exercises (heurística automática de
--      workout_plan_ai.py::assign_warmup_flags, conceito totalmente
--      diferente, intocado).
--   3. série de ajuste (feeler_set_reps/feeler_set_rest_seconds).
--
-- As 7 colunas são espelhadas em DUAS tabelas:
--   - workout_plan_template_slot_items: onde o usuário configura no builder
--     de modelos (`/modelos-treino`), só usado quando item_kind='equipment'
--     (validado em workout_plan_templates.py::_validate_template_payload,
--     sem CHECK/trigger no banco — mesmo padrão do restante do projeto).
--   - workout_plan_exercises: espelho informativo, copiado pelo motor de
--     geração (workout_plan_templates_engine_equipment.py) e aceito também na
--     edição manual da ficha em /planos-treino (payload mais permissivo,
--     Optional[str]/Optional[int] sem Literal).
--
-- sets_count continua representando SOMENTE séries de trabalho — aquecimento
-- e ajuste nunca entram nele nem no cálculo de ativação muscular
-- (VolumeService, que usa apenas mapping_id+sets_count, sem mudança).
--
-- Todas as colunas NULL-safe, sem FK/CHECK novos. Idempotente (mesmo padrão
-- information_schema + PREPARE/EXECUTE usado em 013_workout_plan_template_equipment_series.sql).
-- =============================================================================

USE fitness;

-- -----------------------------------------------------------------------------
-- Helper repetido para as 7 colunas x 2 tabelas: ADD COLUMN idempotente via
-- information_schema.COLUMNS. Sem stored procedure (fora do padrão do
-- projeto) — cada bloco é uma checagem + PREPARE/EXECUTE independente.
-- -----------------------------------------------------------------------------

-- =============================================================================
-- 1. workout_plan_template_slot_items
-- =============================================================================

SET @wptsi_has_advanced_technique = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'advanced_technique'
);
SET @wptsi_advanced_technique_ddl = IF(@wptsi_has_advanced_technique = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN advanced_technique VARCHAR(20) NULL AFTER cardio_duration_minutes",
  'SELECT 1'
);
PREPARE wptsi_advanced_technique_stmt FROM @wptsi_advanced_technique_ddl;
EXECUTE wptsi_advanced_technique_stmt;
DEALLOCATE PREPARE wptsi_advanced_technique_stmt;

SET @wptsi_has_advanced_technique_applications = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'advanced_technique_applications'
);
SET @wptsi_advanced_technique_applications_ddl = IF(@wptsi_has_advanced_technique_applications = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN advanced_technique_applications INT NULL AFTER advanced_technique",
  'SELECT 1'
);
PREPARE wptsi_advanced_technique_applications_stmt FROM @wptsi_advanced_technique_applications_ddl;
EXECUTE wptsi_advanced_technique_applications_stmt;
DEALLOCATE PREPARE wptsi_advanced_technique_applications_stmt;

SET @wptsi_has_advanced_technique_duration_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'advanced_technique_duration_seconds'
);
SET @wptsi_advanced_technique_duration_seconds_ddl = IF(@wptsi_has_advanced_technique_duration_seconds = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN advanced_technique_duration_seconds INT NULL AFTER advanced_technique_applications",
  'SELECT 1'
);
PREPARE wptsi_advanced_technique_duration_seconds_stmt FROM @wptsi_advanced_technique_duration_seconds_ddl;
EXECUTE wptsi_advanced_technique_duration_seconds_stmt;
DEALLOCATE PREPARE wptsi_advanced_technique_duration_seconds_stmt;

SET @wptsi_has_warmup_reps = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'warmup_reps'
);
SET @wptsi_warmup_reps_ddl = IF(@wptsi_has_warmup_reps = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN warmup_reps INT NULL AFTER advanced_technique_duration_seconds",
  'SELECT 1'
);
PREPARE wptsi_warmup_reps_stmt FROM @wptsi_warmup_reps_ddl;
EXECUTE wptsi_warmup_reps_stmt;
DEALLOCATE PREPARE wptsi_warmup_reps_stmt;

SET @wptsi_has_warmup_rest_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'warmup_rest_seconds'
);
SET @wptsi_warmup_rest_seconds_ddl = IF(@wptsi_has_warmup_rest_seconds = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN warmup_rest_seconds INT NULL AFTER warmup_reps",
  'SELECT 1'
);
PREPARE wptsi_warmup_rest_seconds_stmt FROM @wptsi_warmup_rest_seconds_ddl;
EXECUTE wptsi_warmup_rest_seconds_stmt;
DEALLOCATE PREPARE wptsi_warmup_rest_seconds_stmt;

SET @wptsi_has_feeler_set_reps = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'feeler_set_reps'
);
SET @wptsi_feeler_set_reps_ddl = IF(@wptsi_has_feeler_set_reps = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN feeler_set_reps INT NULL AFTER warmup_rest_seconds",
  'SELECT 1'
);
PREPARE wptsi_feeler_set_reps_stmt FROM @wptsi_feeler_set_reps_ddl;
EXECUTE wptsi_feeler_set_reps_stmt;
DEALLOCATE PREPARE wptsi_feeler_set_reps_stmt;

SET @wptsi_has_feeler_set_rest_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'feeler_set_rest_seconds'
);
SET @wptsi_feeler_set_rest_seconds_ddl = IF(@wptsi_has_feeler_set_rest_seconds = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN feeler_set_rest_seconds INT NULL AFTER feeler_set_reps",
  'SELECT 1'
);
PREPARE wptsi_feeler_set_rest_seconds_stmt FROM @wptsi_feeler_set_rest_seconds_ddl;
EXECUTE wptsi_feeler_set_rest_seconds_stmt;
DEALLOCATE PREPARE wptsi_feeler_set_rest_seconds_stmt;

-- =============================================================================
-- 2. workout_plan_exercises (espelho informativo)
-- =============================================================================

SET @wpe_has_advanced_technique = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'advanced_technique'
);
SET @wpe_advanced_technique_ddl = IF(@wpe_has_advanced_technique = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN advanced_technique VARCHAR(20) NULL AFTER warmup",
  'SELECT 1'
);
PREPARE wpe_advanced_technique_stmt FROM @wpe_advanced_technique_ddl;
EXECUTE wpe_advanced_technique_stmt;
DEALLOCATE PREPARE wpe_advanced_technique_stmt;

SET @wpe_has_advanced_technique_applications = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'advanced_technique_applications'
);
SET @wpe_advanced_technique_applications_ddl = IF(@wpe_has_advanced_technique_applications = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN advanced_technique_applications INT NULL AFTER advanced_technique",
  'SELECT 1'
);
PREPARE wpe_advanced_technique_applications_stmt FROM @wpe_advanced_technique_applications_ddl;
EXECUTE wpe_advanced_technique_applications_stmt;
DEALLOCATE PREPARE wpe_advanced_technique_applications_stmt;

SET @wpe_has_advanced_technique_duration_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'advanced_technique_duration_seconds'
);
SET @wpe_advanced_technique_duration_seconds_ddl = IF(@wpe_has_advanced_technique_duration_seconds = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN advanced_technique_duration_seconds INT NULL AFTER advanced_technique_applications",
  'SELECT 1'
);
PREPARE wpe_advanced_technique_duration_seconds_stmt FROM @wpe_advanced_technique_duration_seconds_ddl;
EXECUTE wpe_advanced_technique_duration_seconds_stmt;
DEALLOCATE PREPARE wpe_advanced_technique_duration_seconds_stmt;

SET @wpe_has_warmup_reps = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'warmup_reps'
);
SET @wpe_warmup_reps_ddl = IF(@wpe_has_warmup_reps = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN warmup_reps INT NULL AFTER advanced_technique_duration_seconds",
  'SELECT 1'
);
PREPARE wpe_warmup_reps_stmt FROM @wpe_warmup_reps_ddl;
EXECUTE wpe_warmup_reps_stmt;
DEALLOCATE PREPARE wpe_warmup_reps_stmt;

SET @wpe_has_warmup_rest_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'warmup_rest_seconds'
);
SET @wpe_warmup_rest_seconds_ddl = IF(@wpe_has_warmup_rest_seconds = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN warmup_rest_seconds INT NULL AFTER warmup_reps",
  'SELECT 1'
);
PREPARE wpe_warmup_rest_seconds_stmt FROM @wpe_warmup_rest_seconds_ddl;
EXECUTE wpe_warmup_rest_seconds_stmt;
DEALLOCATE PREPARE wpe_warmup_rest_seconds_stmt;

SET @wpe_has_feeler_set_reps = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'feeler_set_reps'
);
SET @wpe_feeler_set_reps_ddl = IF(@wpe_has_feeler_set_reps = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN feeler_set_reps INT NULL AFTER warmup_rest_seconds",
  'SELECT 1'
);
PREPARE wpe_feeler_set_reps_stmt FROM @wpe_feeler_set_reps_ddl;
EXECUTE wpe_feeler_set_reps_stmt;
DEALLOCATE PREPARE wpe_feeler_set_reps_stmt;

SET @wpe_has_feeler_set_rest_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'feeler_set_rest_seconds'
);
SET @wpe_feeler_set_rest_seconds_ddl = IF(@wpe_has_feeler_set_rest_seconds = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN feeler_set_rest_seconds INT NULL AFTER feeler_set_reps",
  'SELECT 1'
);
PREPARE wpe_feeler_set_rest_seconds_stmt FROM @wpe_feeler_set_rest_seconds_ddl;
EXECUTE wpe_feeler_set_rest_seconds_stmt;
DEALLOCATE PREPARE wpe_feeler_set_rest_seconds_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (39, 'Tecnica avancada (rest_pause|drop_set|fst7|gvt|forced_reps|negative_reps|partial_reps|cheat_reps) + serie de aquecimento configurada (warmup_reps/warmup_rest_seconds, distinta do warmup BOOLEAN legado heuristico) + serie de ajuste (feeler_set_reps/feeler_set_rest_seconds): 7 colunas espelhadas em workout_plan_template_slot_items (builder do modo equipment_series) e workout_plan_exercises (ficha salva, espelho informativo); sets_count/VolumeService inalterados');

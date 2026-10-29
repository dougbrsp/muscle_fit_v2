-- Copiado de fitness-platform/mysql/init/015_warmup_feeler_sets_count.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 015_warmup_feeler_sets_count.sql
--
-- Refinamento da feature de série de aquecimento/ajuste (014): hoje só é
-- possível configurar UMA série de aquecimento e UMA de ajuste por card do
-- modo equipment_series. Este incremento adiciona a quantidade de séries de
-- cada uma, para permitir múltiplas séries de aquecimento e/ou de ajuste
-- antes das séries de trabalho:
--   - warmup_sets_count: nº de séries de aquecimento (default de UI = 2).
--   - feeler_set_sets_count: nº de séries de ajuste (default de UI = 2).
--
-- Posicionamento das colunas: cada *_sets_count fica IMEDIATAMENTE ANTES do
-- seu *_reps correspondente (warmup_sets_count antes de warmup_reps,
-- feeler_set_sets_count antes de feeler_set_reps), pois semanticamente
-- "quantas séries" precede "quantas reps por série" na leitura humana do
-- conjunto de campos (séries x reps x intervalo).
--
-- Espelhado nas MESMAS duas tabelas de 014:
--   - workout_plan_template_slot_items (builder de modelos, /modelos-treino)
--   - workout_plan_exercises (espelho informativo da ficha salva)
--
-- sets_count (séries de TRABALHO) permanece intocado — warmup_sets_count e
-- feeler_set_sets_count nunca entram nele nem no cálculo de ativação
-- muscular (VolumeService).
--
-- Idempotente (mesmo padrão information_schema + PREPARE/EXECUTE de 013/014).
-- =============================================================================

USE fitness;

-- =============================================================================
-- 1. workout_plan_template_slot_items
-- =============================================================================

SET @wptsi_has_warmup_sets_count = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'warmup_sets_count'
);
SET @wptsi_warmup_sets_count_ddl = IF(@wptsi_has_warmup_sets_count = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN warmup_sets_count INT NULL AFTER advanced_technique_duration_seconds",
  'SELECT 1'
);
PREPARE wptsi_warmup_sets_count_stmt FROM @wptsi_warmup_sets_count_ddl;
EXECUTE wptsi_warmup_sets_count_stmt;
DEALLOCATE PREPARE wptsi_warmup_sets_count_stmt;

SET @wptsi_has_feeler_set_sets_count = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'feeler_set_sets_count'
);
SET @wptsi_feeler_set_sets_count_ddl = IF(@wptsi_has_feeler_set_sets_count = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN feeler_set_sets_count INT NULL AFTER warmup_rest_seconds",
  'SELECT 1'
);
PREPARE wptsi_feeler_set_sets_count_stmt FROM @wptsi_feeler_set_sets_count_ddl;
EXECUTE wptsi_feeler_set_sets_count_stmt;
DEALLOCATE PREPARE wptsi_feeler_set_sets_count_stmt;

-- =============================================================================
-- 2. workout_plan_exercises (espelho informativo)
-- =============================================================================

SET @wpe_has_warmup_sets_count = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'warmup_sets_count'
);
SET @wpe_warmup_sets_count_ddl = IF(@wpe_has_warmup_sets_count = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN warmup_sets_count INT NULL AFTER advanced_technique_duration_seconds",
  'SELECT 1'
);
PREPARE wpe_warmup_sets_count_stmt FROM @wpe_warmup_sets_count_ddl;
EXECUTE wpe_warmup_sets_count_stmt;
DEALLOCATE PREPARE wpe_warmup_sets_count_stmt;

SET @wpe_has_feeler_set_sets_count = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'feeler_set_sets_count'
);
SET @wpe_feeler_set_sets_count_ddl = IF(@wpe_has_feeler_set_sets_count = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN feeler_set_sets_count INT NULL AFTER warmup_rest_seconds",
  'SELECT 1'
);
PREPARE wpe_feeler_set_sets_count_stmt FROM @wpe_feeler_set_sets_count_ddl;
EXECUTE wpe_feeler_set_sets_count_stmt;
DEALLOCATE PREPARE wpe_feeler_set_sets_count_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (40, 'Numero de series de aquecimento e de ajuste configuraveis (warmup_sets_count/feeler_set_sets_count, default de UI = 2 cada), permitindo multiplas series de aquecimento/ajuste antes das series de trabalho no modo equipment_series; colunas espelhadas em workout_plan_template_slot_items e workout_plan_exercises, posicionadas antes de warmup_reps/feeler_set_reps respectivamente; sets_count/VolumeService inalterados');

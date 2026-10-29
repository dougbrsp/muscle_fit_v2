-- Copiado de fitness-platform/mysql/init/009_template_slot_priority_range.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 009_template_slot_priority_range.sql
--
-- Feature "Faixa de prioridade percentual por slot" — captura, por SLOT do
-- modelo de ficha (workout_plan_template_slots), uma faixa min%-max% de
-- prioridade (ex.: 60%-70%). Propriedade do slot inteiro, nao de cada item
-- do slot.
--
-- Uso NESTA entrega: apenas captura/persistencia (CRUD do builder em
-- /modelos-treino). O motor de geracao de ficha (workout_plan_templates_engine.py,
-- abc_*.py) NAO le nem consome este valor ainda — reservado para uma
-- implementacao futura.
--
-- Introduz em workout_plan_template_slots:
--   - priority_min_pct, priority_max_pct: TINYINT UNSIGNED NULL (0-100).
--     Optou-se por TINYINT UNSIGNED em vez de DECIMAL(5,2) (padrao usado
--     para percentuais "reais" no schema, ex. body_composition.body_fat_percent)
--     porque este e um rotulo de prioridade relativa definido manualmente
--     pelo usuario, sem necessidade de casas decimais - inteiro 0-100 e
--     suficiente e mais simples. Ambos opcionais: nem todo slot precisa
--     definir faixa; quando definida, a API exige os dois preenchidos
--     juntos (min <= max), ver _validate_template_payload em
--     app/workout_plan_templates.py.
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005_workout_plan_templates.sql/006_.../007_.../008_training_types.sql).
-- =============================================================================

USE fitness;

-- -----------------------------------------------------------------------------
-- 1. workout_plan_template_slots.priority_min_pct
-- -----------------------------------------------------------------------------
SET @wpts_has_priority_min = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slots' AND COLUMN_NAME = 'priority_min_pct'
);
SET @wpts_priority_min_ddl = IF(@wpts_has_priority_min = 0,
  'ALTER TABLE workout_plan_template_slots ADD COLUMN priority_min_pct TINYINT UNSIGNED NULL AFTER slot_type',
  'SELECT 1'
);
PREPARE wpts_priority_min_stmt FROM @wpts_priority_min_ddl; EXECUTE wpts_priority_min_stmt; DEALLOCATE PREPARE wpts_priority_min_stmt;

-- -----------------------------------------------------------------------------
-- 2. workout_plan_template_slots.priority_max_pct
-- -----------------------------------------------------------------------------
SET @wpts_has_priority_max = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slots' AND COLUMN_NAME = 'priority_max_pct'
);
SET @wpts_priority_max_ddl = IF(@wpts_has_priority_max = 0,
  'ALTER TABLE workout_plan_template_slots ADD COLUMN priority_max_pct TINYINT UNSIGNED NULL AFTER priority_min_pct',
  'SELECT 1'
);
PREPARE wpts_priority_max_stmt FROM @wpts_priority_max_ddl; EXECUTE wpts_priority_max_stmt; DEALLOCATE PREPARE wpts_priority_max_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (34, 'workout_plan_template_slots ganha priority_min_pct/priority_max_pct (TINYINT UNSIGNED NULL, 0-100) - faixa de prioridade percentual por slot, apenas captura nesta entrega, sem consumo no motor de geracao');

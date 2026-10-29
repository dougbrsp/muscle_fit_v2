-- Copiado de fitness-platform/mysql/init/013_workout_plan_template_equipment_series.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 013_workout_plan_template_equipment_series.sql
--
-- LOTE 1 de "Modelos de Treino: modo equipment_series" — segundo modo de
-- operacao para workout_plan_templates, ORTOGONAL ao modo atual
-- (implicitamente "percent_budget", motor v2 por % de orcamento sobre
-- training_type_id). No modo novo o usuario define diretamente, por item de
-- slot, grupo muscular + equipamento + exercicio (mapping_id) + numero de
-- series fixo — sem % de orcamento, sem Tipo de Treinamento, sem a IA
-- escolher nada.
--
-- Introduz:
--   1. workout_plan_templates.volume_mode VARCHAR(20) NOT NULL DEFAULT
--      'percent_budget' — templates existentes (todos hoje implicitamente no
--      modo atual) continuam validos sem qualquer migracao de dado.
--   2. workout_plan_template_slot_items.item_kind ganha o valor 'equipment'
--      no ENUM (mantendo 'muscle_group','exercise','cardio' existentes —
--      'exercise' e legado/nao mais aceito em novos saves, ver
--      workout_plan_templates.py::_validate_template_payload, mas o valor do
--      ENUM e preservado para nao quebrar linhas antigas).
--   3. workout_plan_template_slot_items.equipment_id BIGINT NULL, FK para
--      equipment(id) ON DELETE SET NULL — mesmo padrao de nulabilidade de
--      mapping_id (ON DELETE SET NULL, nunca CASCADE, para nao apagar o item
--      do slot so porque o equipamento/exercicio foi removido do catalogo).
--
-- Validacao de negocio (item 'equipment' exige muscle_group_id + equipment_id
-- + mapping_id + sets_count >= 1; 'muscle_group' e vedado em modo
-- 'equipment_series'; 'equipment' e vedado em modo 'percent_budget') fica
-- SOMENTE na API (workout_plan_templates.py), sem CHECK/trigger — mesmo
-- padrao ja usado em todo o restante do projeto (ver cabecalho de
-- 005_workout_plan_templates.sql).
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005/006/007/008/009/010/011_*.sql).
-- =============================================================================

USE fitness;

-- -----------------------------------------------------------------------------
-- 1. workout_plan_templates.volume_mode
-- -----------------------------------------------------------------------------
SET @wpt_has_volume_mode = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_templates'
    AND COLUMN_NAME = 'volume_mode'
);
SET @wpt_volume_mode_ddl = IF(@wpt_has_volume_mode = 0,
  "ALTER TABLE workout_plan_templates ADD COLUMN volume_mode VARCHAR(20) NOT NULL DEFAULT 'percent_budget' AFTER training_type_id",
  'SELECT 1'
);
PREPARE wpt_volume_mode_stmt FROM @wpt_volume_mode_ddl;
EXECUTE wpt_volume_mode_stmt;
DEALLOCATE PREPARE wpt_volume_mode_stmt;

-- -----------------------------------------------------------------------------
-- 2. workout_plan_template_slot_items.item_kind ganha 'equipment' no ENUM
-- -----------------------------------------------------------------------------
SET @wptsi_item_kind_has_equipment = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'item_kind'
    AND COLUMN_TYPE LIKE '%''equipment''%'
);
SET @wptsi_item_kind_ddl = IF(@wptsi_item_kind_has_equipment = 0,
  "ALTER TABLE workout_plan_template_slot_items MODIFY COLUMN item_kind ENUM('muscle_group','exercise','cardio','equipment') NOT NULL",
  'SELECT 1'
);
PREPARE wptsi_item_kind_stmt FROM @wptsi_item_kind_ddl;
EXECUTE wptsi_item_kind_stmt;
DEALLOCATE PREPARE wptsi_item_kind_stmt;

-- -----------------------------------------------------------------------------
-- 3. workout_plan_template_slot_items.equipment_id (FK para equipment)
-- -----------------------------------------------------------------------------
SET @wptsi_has_equipment_id = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'equipment_id'
);
SET @wptsi_equipment_id_ddl = IF(@wptsi_has_equipment_id = 0,
  'ALTER TABLE workout_plan_template_slot_items ADD COLUMN equipment_id BIGINT NULL AFTER mapping_id',
  'SELECT 1'
);
PREPARE wptsi_equipment_id_stmt FROM @wptsi_equipment_id_ddl;
EXECUTE wptsi_equipment_id_stmt;
DEALLOCATE PREPARE wptsi_equipment_id_stmt;

SET @wptsi_has_equipment_idx = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND INDEX_NAME = 'idx_wptsi_equipment'
);
SET @wptsi_equipment_idx_ddl = IF(@wptsi_has_equipment_idx = 0,
  'ALTER TABLE workout_plan_template_slot_items ADD KEY idx_wptsi_equipment (equipment_id)',
  'SELECT 1'
);
PREPARE wptsi_equipment_idx_stmt FROM @wptsi_equipment_idx_ddl;
EXECUTE wptsi_equipment_idx_stmt;
DEALLOCATE PREPARE wptsi_equipment_idx_stmt;

SET @wptsi_has_equipment_fk = (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND CONSTRAINT_NAME = 'fk_wptsi_equipment'
);
SET @wptsi_equipment_fk_ddl = IF(@wptsi_has_equipment_fk = 0,
  'ALTER TABLE workout_plan_template_slot_items ADD CONSTRAINT fk_wptsi_equipment FOREIGN KEY (equipment_id) REFERENCES equipment (id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE wptsi_equipment_fk_stmt FROM @wptsi_equipment_fk_ddl;
EXECUTE wptsi_equipment_fk_stmt;
DEALLOCATE PREPARE wptsi_equipment_fk_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (38, 'Modelos de Treino LOTE 1: workout_plan_templates ganha volume_mode (percent_budget|equipment_series, default percent_budget); workout_plan_template_slot_items ganha item_kind=equipment no ENUM e equipment_id (FK equipment, ON DELETE SET NULL) — segundo modo de operacao ortogonal ao motor v2 por % de orcamento, sem alterar dado existente');

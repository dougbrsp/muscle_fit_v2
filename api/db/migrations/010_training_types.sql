-- Copiado de fitness-platform/mysql/init/008_training_types.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 008_training_types.sql
--
-- Feature "Tipo de Treinamento" (meta semanal de volume por grupo muscular) —
-- ver plano completo em melhoria-eager-origami.md, secoes 1 e 2.
--
-- Introduz:
--   - training_types: cadastro (Admin/PT) de um objetivo de treino
--     (ex: "Hipertrofia ABC"), com nome/descricao/ativo.
--   - training_type_muscle_group_targets: meta semanal (min/target/max) de
--     series equivalentes por grupo muscular, para um training_type. So
--     existe linha para grupos efetivamente configurados (min=target=max=0
--     nao e persistido, mesma filosofia de "ignorar item incompleto" ja
--     usada em get_muscle_activation_for_exercises).
--   - workout_plan_templates.training_type_id: associacao OPCIONAL de um
--     Modelo de Treino a um Tipo de Treinamento. `template_type` permanece
--     INTOCADO (continua controlando RAG/prompt da geracao via IA) — sao
--     conceitos ortogonais.
--
-- NOTA: o plano original previa registrar esta migracao como
-- schema_migrations.version = 32, mas nesse meio tempo outra migracao ja
-- ocupou a versao 32 (FK de mapping_id trocada para exercise_catalog).
-- Confirmado via `SELECT MAX(version) FROM schema_migrations` antes de
-- aplicar: usamos version = 33 aqui.
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005/006/007_*.sql).
-- =============================================================================

USE fitness;

-- -----------------------------------------------------------------------------
-- 1. training_types
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_types (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  description TEXT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_by_user_id BIGINT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_tt_created_by (created_by_user_id),
  KEY idx_tt_is_active (is_active),
  CONSTRAINT fk_tt_created_by FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
) ENGINE=InnoDB CHARACTER SET utf8mb4;

-- -----------------------------------------------------------------------------
-- 2. training_type_muscle_group_targets
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_type_muscle_group_targets (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  training_type_id BIGINT NOT NULL,
  muscle_group_id BIGINT NOT NULL,
  min_weekly_sets DECIMAL(6,2) NOT NULL,
  target_weekly_sets DECIMAL(6,2) NOT NULL,
  max_weekly_sets DECIMAL(6,2) NOT NULL,
  UNIQUE KEY uq_ttmgt_type_group (training_type_id, muscle_group_id),
  KEY idx_ttmgt_muscle_group (muscle_group_id),
  CONSTRAINT fk_ttmgt_type FOREIGN KEY (training_type_id) REFERENCES training_types(id) ON DELETE CASCADE,
  CONSTRAINT fk_ttmgt_muscle_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id) ON DELETE RESTRICT
) ENGINE=InnoDB;

-- -----------------------------------------------------------------------------
-- 3. workout_plan_templates.training_type_id (associacao opcional)
-- -----------------------------------------------------------------------------
SET @wpt_has_training_type_id = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_templates' AND COLUMN_NAME = 'training_type_id'
);
SET @wpt_training_type_id_ddl = IF(@wpt_has_training_type_id = 0,
  'ALTER TABLE workout_plan_templates ADD COLUMN training_type_id BIGINT NULL AFTER template_type',
  'SELECT 1'
);
PREPARE wpt_training_type_id_stmt FROM @wpt_training_type_id_ddl;
EXECUTE wpt_training_type_id_stmt;
DEALLOCATE PREPARE wpt_training_type_id_stmt;

SET @wpt_has_training_type_idx = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_templates' AND INDEX_NAME = 'idx_wpt_training_type'
);
SET @wpt_training_type_idx_ddl = IF(@wpt_has_training_type_idx = 0,
  'ALTER TABLE workout_plan_templates ADD KEY idx_wpt_training_type (training_type_id)',
  'SELECT 1'
);
PREPARE wpt_training_type_idx_stmt FROM @wpt_training_type_idx_ddl;
EXECUTE wpt_training_type_idx_stmt;
DEALLOCATE PREPARE wpt_training_type_idx_stmt;

SET @wpt_has_training_type_fk = (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_templates' AND CONSTRAINT_NAME = 'fk_wpt_training_type'
);
SET @wpt_training_type_fk_ddl = IF(@wpt_has_training_type_fk = 0,
  'ALTER TABLE workout_plan_templates ADD CONSTRAINT fk_wpt_training_type FOREIGN KEY (training_type_id) REFERENCES training_types (id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE wpt_training_type_fk_stmt FROM @wpt_training_type_fk_ddl;
EXECUTE wpt_training_type_fk_stmt;
DEALLOCATE PREPARE wpt_training_type_fk_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (33, 'Tipo de Treinamento: training_types + training_type_muscle_group_targets (meta semanal min/target/max por grupo muscular) e workout_plan_templates.training_type_id (FK SET NULL, associacao opcional, ortogonal a template_type)');

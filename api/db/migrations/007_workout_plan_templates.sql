-- Copiado de fitness-platform/mysql/init/005_workout_plan_templates.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 005_workout_plan_templates.sql
--
-- Feature "Modelos de Ficha de Treino Personalizados" (Fase 1 - sem IA), ver
-- plano completo em melhoria-eager-origami.md, secoes 1 e 2.
--
-- Introduz:
--   1. users.role: estende o ENUM para incluir 'personal_trainer' (idempotente).
--   2. workout_plan_templates: cabecalho do modelo (nome, tipo livre, autor).
--   3. workout_plan_template_slots: slots ciclicos por modelo.
--   4. workout_plan_template_slot_items: itens de cada slot (grupo muscular,
--      exercicio ou cardio).
--   5. workout_plan_template_assignments: atribuicao "vinculo vivo" de um
--      modelo a um usuario (sem versionamento/copia - o motor de geracao le
--      o modelo atual a cada chamada).
--
-- Regras de negocio (mutex grupo/exercicio por slot, descanso sem
-- grupo/exercicio, maximo 3 priorizados por slot) NAO sao expressas em SQL
-- (sem CHECK/trigger) - validadas apenas na API (app/workout_plan_templates.py),
-- mesmo padrao ja usado no restante do projeto.
--
-- mapping_id SEMPRE aponta para exercise_catalog(id) (fonte de verdade unica
-- pos-reestruturacao) - NUNCA para exercise_zepp_mappings (legado).
--
-- Idempotente (padrao information_schema + PREPARE/EXECUTE ja usado em
-- 002_reestruturacao.sql/003_exercise_domain_admin.sql/
-- 004_wpe_mapping_fk_exercise_catalog.sql); tabelas novas usam
-- CREATE TABLE IF NOT EXISTS.
-- =============================================================================

USE fitness;

-- -----------------------------------------------------------------------------
-- 1. users.role: adiciona 'personal_trainer' ao ENUM, preservando 'admin' e
-- 'regular' e o DEFAULT 'regular' existentes. Guardado por checagem em
-- information_schema.COLUMNS + COLUMN_TYPE, executado via PREPARE/EXECUTE
-- para ser idempotente (no-op se o ENUM ja tiver sido estendido).
-- -----------------------------------------------------------------------------
SET @users_role_has_pt = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'role'
    AND COLUMN_TYPE LIKE '%personal_trainer%'
);
SET @users_role_ddl = IF(@users_role_has_pt = 0,
  "ALTER TABLE users MODIFY COLUMN role ENUM('admin','regular','personal_trainer') NOT NULL DEFAULT 'regular'",
  'SELECT 1'
);
PREPARE users_role_stmt FROM @users_role_ddl; EXECUTE users_role_stmt; DEALLOCATE PREPARE users_role_stmt;

-- -----------------------------------------------------------------------------
-- 2. workout_plan_templates
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workout_plan_templates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  description TEXT NULL,
  -- Mesmo espirito de workout_plans.plan_type: texto livre, nao ENUM/Literal
  -- (valores de hoje: full_body, abc; futuramente outros, sem migracao de schema).
  template_type VARCHAR(40) NOT NULL DEFAULT 'full_body',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_by_user_id BIGINT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_wpt_created_by (created_by_user_id),
  KEY idx_wpt_is_active (is_active),
  CONSTRAINT fk_wpt_created_by FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB CHARACTER SET utf8mb4;

-- -----------------------------------------------------------------------------
-- 3. workout_plan_template_slots (N por template, sequence 1..N)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workout_plan_template_slots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  template_id BIGINT NOT NULL,
  sequence TINYINT NOT NULL,
  name VARCHAR(120) NOT NULL,
  -- 1=segunda .. 7=domingo. Metadado puramente informativo nesta fase (ver
  -- secao 4 do plano) - NAO usado no calculo do ciclo (dias_desde_epoch % N).
  weekday TINYINT NULL,
  slot_type ENUM('normal','descanso') NOT NULL DEFAULT 'normal',
  UNIQUE KEY uq_wpts_template_sequence (template_id, sequence),
  CONSTRAINT fk_wpts_template FOREIGN KEY (template_id) REFERENCES workout_plan_templates (id) ON DELETE CASCADE
) ENGINE=InnoDB CHARACTER SET utf8mb4;

-- -----------------------------------------------------------------------------
-- 4. workout_plan_template_slot_items
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workout_plan_template_slot_items (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  slot_id BIGINT NOT NULL,
  item_order TINYINT NOT NULL,
  item_kind ENUM('muscle_group','exercise','cardio') NOT NULL,
  muscle_group_id BIGINT NULL,
  -- FK para exercise_catalog(id), NUNCA exercise_zepp_mappings (legado) - ver
  -- 004_wpe_mapping_fk_exercise_catalog.sql para o precedente desta decisao.
  -- BIGINT UNSIGNED para bater com exercise_catalog.id (mesma licao do
  -- migration 004: MySQL recusa FK entre tipos incompativeis, ER 3780).
  mapping_id BIGINT UNSIGNED NULL,
  sets_count INT NOT NULL DEFAULT 3,
  reps_target VARCHAR(80) NULL,
  rpe_target VARCHAR(80) NULL,
  prioritize TINYINT(1) NOT NULL DEFAULT 0,
  cardio_duration_minutes INT NULL,
  KEY idx_wptsi_slot (slot_id),
  KEY idx_wptsi_muscle_group (muscle_group_id),
  KEY idx_wptsi_mapping (mapping_id),
  CONSTRAINT fk_wptsi_slot FOREIGN KEY (slot_id) REFERENCES workout_plan_template_slots (id) ON DELETE CASCADE,
  CONSTRAINT fk_wptsi_muscle_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups (id) ON DELETE RESTRICT,
  CONSTRAINT fk_wptsi_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_catalog (id) ON DELETE SET NULL
) ENGINE=InnoDB CHARACTER SET utf8mb4;

-- -----------------------------------------------------------------------------
-- 5. workout_plan_template_assignments ("vinculo vivo" - sem soft-delete/
-- historico; desatribuir e DELETE fisico)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workout_plan_template_assignments (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  template_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  assigned_by_user_id BIGINT NOT NULL,
  assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_wpta_template_user (template_id, user_id),
  KEY idx_wpta_user (user_id),
  CONSTRAINT fk_wpta_template FOREIGN KEY (template_id) REFERENCES workout_plan_templates (id) ON DELETE CASCADE,
  CONSTRAINT fk_wpta_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT fk_wpta_assigned_by FOREIGN KEY (assigned_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB CHARACTER SET utf8mb4;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (29, 'Modelos de Ficha de Treino Personalizados (Fase 1, sem IA): users.role ganha personal_trainer; novas tabelas workout_plan_templates/_slots/_slot_items/_assignments; mapping_id de slot item aponta para exercise_catalog, nunca exercise_zepp_mappings');

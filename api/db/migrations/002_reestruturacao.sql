-- Copiado de fitness-platform/mysql/init/002_reestruturacao.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- 002_reestruturacao.sql
-- Fase 2 da reestruturacao do dominio de exercicios/equipamentos/musculos.
-- Aditivo apenas: nenhuma tabela existente e alterada estruturalmente (exceto
-- UPDATE de conteudo em muscle_groups, preservando ids/FKs) e nenhuma tabela
-- e removida ou renomeada. Segue o mesmo padrao idempotente (guardas via
-- information_schema + PREPARE/EXECUTE) usado em 001_schema.sql.
--
-- Escopo (ver REESTRUTURACAO_AUDITORIA.md e REESTRUTURACAO.md secoes 7-22):
--   1. exercise_catalog          (novo catalogo canonico, sem contaminacao Amazfit)
--   2. exercise_external_mappings (mapeamento externo, ex.: Amazfit)
--   3. equipment_exercises        (renomeacao semantica de equipment_exercise_mappings)
--   4. muscle_groups              (UPDATE de rotulos/musculos, mesmos ids)
--   5. vw_muscle_group_equipment_exercises (view de navegacao)
--   6. indices adicionais
--
-- so pode ser aplicado contra fitness-mysql-reestrut (porta 3407). NUNCA
-- rodar isto contra o MySQL de producao (porta 3307).

USE fitness;

-- =============================================================================
-- 1. exercise_catalog: novo catalogo canonico de exercicios. Preserva os
-- mesmos ids de exercise_zepp_mappings (para nao quebrar nenhuma FK futura
-- que venha a apontar para ca), mas SEM os campos contaminados por Amazfit
-- (amazfit_name, mapping_status, source, primary_group_id, secondary_group_id,
-- secondary_participation, equipment texto livre) - esses ficam isolados em
-- exercise_external_mappings (mapeamento) ou em exercise_muscle_contributions
-- (contribuicao muscular), que ja sao o modelo-alvo.
-- =============================================================================
CREATE TABLE IF NOT EXISTS exercise_catalog (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  body_position VARCHAR(120) NULL,
  movement VARCHAR(120) NULL,
  trajectory VARCHAR(120) NULL,
  biomechanical_pattern VARCHAR(160) NULL,
  primary_joint VARCHAR(160) NULL,
  resistance_vector VARCHAR(120) NULL,
  keywords TEXT NULL,
  description TEXT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_exercise_catalog_name (name)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- Backfill idempotente: preserva o id original de exercise_zepp_mappings via
-- INSERT ... SELECT explicito (sem AUTO_INCREMENT gerando novo valor).
INSERT IGNORE INTO exercise_catalog (
  id, name, body_position, movement, trajectory, biomechanical_pattern,
  primary_joint, resistance_vector, keywords, description, is_active,
  created_at, updated_at
)
SELECT
  id, exercise_name, body_position, movement, trajectory, biomechanical_pattern,
  primary_joint, resistance_vector, keywords, description, is_active,
  created_at, updated_at
FROM exercise_zepp_mappings;

-- =============================================================================
-- 2. exercise_external_mappings: mapeamento externo (ex.: Amazfit/Zepp) para
-- um exercicio do catalogo canonico. Backfill apenas para amazfit_name nao
-- nulo/vazio.
-- =============================================================================
CREATE TABLE IF NOT EXISTS exercise_external_mappings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  exercise_id BIGINT UNSIGNED NOT NULL,
  provider VARCHAR(30) NOT NULL,
  external_id VARCHAR(255) NULL,
  external_name VARCHAR(255) NOT NULL,
  mapping_status VARCHAR(30) NOT NULL DEFAULT 'CONFIRMED',
  source VARCHAR(255) NULL,
  notes VARCHAR(500) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_eem_exercise_provider_name (exercise_id, provider, external_name),
  CONSTRAINT fk_exm_exercise FOREIGN KEY (exercise_id) REFERENCES exercise_catalog(id) ON DELETE CASCADE
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

INSERT IGNORE INTO exercise_external_mappings (
  exercise_id, provider, external_name, mapping_status, source, is_active
)
SELECT
  id, 'AMAZFIT', amazfit_name, mapping_status, source, is_active
FROM exercise_zepp_mappings
WHERE amazfit_name IS NOT NULL AND amazfit_name <> '';

-- Indices adicionais de consulta (item 7 do escopo).
SET @exm_idx_exercise_provider_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_external_mappings'
    AND INDEX_NAME = 'idx_exm_exercise_provider_active'
);
SET @exm_idx_exercise_provider_ddl = IF(@exm_idx_exercise_provider_exists = 0,
  'ALTER TABLE exercise_external_mappings ADD INDEX idx_exm_exercise_provider_active (exercise_id, provider, is_active)',
  'SELECT 1'
);
PREPARE exm_idx_exercise_provider_stmt FROM @exm_idx_exercise_provider_ddl; EXECUTE exm_idx_exercise_provider_stmt; DEALLOCATE PREPARE exm_idx_exercise_provider_stmt;

SET @exm_idx_provider_name_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_external_mappings'
    AND INDEX_NAME = 'idx_exm_provider_external_name'
);
SET @exm_idx_provider_name_ddl = IF(@exm_idx_provider_name_exists = 0,
  'ALTER TABLE exercise_external_mappings ADD INDEX idx_exm_provider_external_name (provider, external_name)',
  'SELECT 1'
);
PREPARE exm_idx_provider_name_stmt FROM @exm_idx_provider_name_ddl; EXECUTE exm_idx_provider_name_stmt; DEALLOCATE PREPARE exm_idx_provider_name_stmt;

-- =============================================================================
-- 3. equipment_exercises: renomeacao SEMANTICA (nao RENAME TABLE, aditiva) de
-- equipment_exercise_mappings, trocando a chave mapping_id por exercise_id
-- (apontando para exercise_catalog em vez de exercise_zepp_mappings).
-- =============================================================================
CREATE TABLE IF NOT EXISTS equipment_exercises (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  equipment_id BIGINT NOT NULL,
  exercise_id BIGINT UNSIGNED NOT NULL,
  is_preferred TINYINT(1) NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  notes VARCHAR(500) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_equipment_exercises_equipment_exercise (equipment_id, exercise_id),
  CONSTRAINT fk_eqex_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  CONSTRAINT fk_eqex_exercise FOREIGN KEY (exercise_id) REFERENCES exercise_catalog(id) ON DELETE CASCADE
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- Backfill 1:1 de equipment_exercise_mappings, trocando mapping_id por
-- exercise_id (mesmo valor numerico, ja que exercise_catalog.id preserva o id
-- de exercise_zepp_mappings.id).
INSERT IGNORE INTO equipment_exercises (
  equipment_id, exercise_id, is_active, created_at, updated_at
)
SELECT
  equipment_id, mapping_id, is_active, created_at, updated_at
FROM equipment_exercise_mappings;

SET @eqex_idx_equipment_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment_exercises'
    AND INDEX_NAME = 'idx_eqex_equipment_active'
);
SET @eqex_idx_equipment_ddl = IF(@eqex_idx_equipment_exists = 0,
  'ALTER TABLE equipment_exercises ADD INDEX idx_eqex_equipment_active (equipment_id, is_active)',
  'SELECT 1'
);
PREPARE eqex_idx_equipment_stmt FROM @eqex_idx_equipment_ddl; EXECUTE eqex_idx_equipment_stmt; DEALLOCATE PREPARE eqex_idx_equipment_stmt;

SET @eqex_idx_exercise_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment_exercises'
    AND INDEX_NAME = 'idx_eqex_exercise_active'
);
SET @eqex_idx_exercise_ddl = IF(@eqex_idx_exercise_exists = 0,
  'ALTER TABLE equipment_exercises ADD INDEX idx_eqex_exercise_active (exercise_id, is_active)',
  'SELECT 1'
);
PREPARE eqex_idx_exercise_stmt FROM @eqex_idx_exercise_ddl; EXECUTE eqex_idx_exercise_stmt; DEALLOCATE PREPARE eqex_idx_exercise_stmt;

-- =============================================================================
-- 4. muscle_groups: UPDATE de rotulos/musculos (mesmos ids, tabela definitiva
-- confirmada pelo usuario). NAO recria a tabela nem altera ids. Executado com
-- literais UTF-8 diretos; validar acentuacao depois com SELECT dedicado.
-- =============================================================================
UPDATE muscle_groups SET name = 'Abdômen', primary_muscles = 'Reto abdominal', secondary_muscles = 'Transverso do abdômen' WHERE id = 1;
UPDATE muscle_groups SET name = 'Adutores', primary_muscles = 'Adutores', secondary_muscles = 'Estabilizadores do quadril' WHERE id = 2;
UPDATE muscle_groups SET name = 'Antebraço anterior', primary_muscles = 'Flexores do antebraço', secondary_muscles = 'Braquiorradial, Braquial anterior' WHERE id = 3;
UPDATE muscle_groups SET name = 'Quadríceps', primary_muscles = 'Quadríceps, Reto femoral', secondary_muscles = 'Glúteos' WHERE id = 4;
UPDATE muscle_groups SET name = 'Bíceps', primary_muscles = 'Bíceps braquial, Cabeça longa, Cabeça curta', secondary_muscles = 'Braquial anterior, Braquiorradial' WHERE id = 5;
UPDATE muscle_groups SET name = 'Costas — largura dorsal', primary_muscles = 'Latíssimo do dorso', secondary_muscles = 'Redondo maior' WHERE id = 6;
UPDATE muscle_groups SET name = 'Costas — miolo/densidade', primary_muscles = 'Trapézio transverso, Romboides', secondary_muscles = 'Deltoide posterior' WHERE id = 7;
UPDATE muscle_groups SET name = 'Glúteos', primary_muscles = 'Glúteo máximo, Glúteo médio', secondary_muscles = 'Glúteo mínimo, Tensor da fáscia lata' WHERE id = 8;
UPDATE muscle_groups SET name = 'Lombar', primary_muscles = 'Eretores da coluna', secondary_muscles = 'Estabilizadores lombares' WHERE id = 9;
UPDATE muscle_groups SET name = 'Ombros / Deltoides', primary_muscles = 'Deltoide anterior, Deltoide lateral, Deltoide posterior', secondary_muscles = 'Manguito rotador' WHERE id = 10;
UPDATE muscle_groups SET name = 'Peitoral superior', primary_muscles = 'Peitoral maior — porção clavicular/superior', secondary_muscles = 'Deltoide anterior, Tríceps' WHERE id = 12;
UPDATE muscle_groups SET name = 'Posterior de coxa / Isquiotibiais', primary_muscles = 'Isquiotibiais: Bíceps femoral, Semitendinoso, Semimembranoso', secondary_muscles = 'Glúteos' WHERE id = 13;
UPDATE muscle_groups SET name = 'Tríceps', primary_muscles = 'Tríceps braquial: Cabeça longa, Cabeça lateral, Cabeça medial', secondary_muscles = NULL WHERE id = 14;
UPDATE muscle_groups SET name = 'Antebraço posterior', primary_muscles = 'Extensores do antebraço', secondary_muscles = 'Braquiorradial' WHERE id = 50;
UPDATE muscle_groups SET name = 'Panturrilha posterior', primary_muscles = 'Gastrocnêmio, Sóleo', secondary_muscles = NULL WHERE id = 51;
UPDATE muscle_groups SET name = 'Peitoral', primary_muscles = 'Peitoral maior', secondary_muscles = 'Deltoide anterior, Tríceps' WHERE id = 52;
UPDATE muscle_groups SET name = 'Panturrilha anterior', primary_muscles = 'Tibial anterior', secondary_muscles = NULL WHERE id = 53;
UPDATE muscle_groups SET name = 'Flexores do quadril', primary_muscles = 'Flexores do quadril, Íliopsoas, Reto femoral', secondary_muscles = NULL WHERE id = 54;
UPDATE muscle_groups SET name = 'Core', primary_muscles = 'Transverso do abdômen, Reto abdominal', secondary_muscles = 'Estabilizadores lombares' WHERE id = 55;
UPDATE muscle_groups SET name = 'Manguito rotador', primary_muscles = 'Manguito rotador / Rotadores do ombro', secondary_muscles = 'Estabilizadores do ombro' WHERE id = 56;

-- =============================================================================
-- 5. View de navegacao: cruza contribuicao muscular, grupo, exercicio,
-- associacao de equipamento e equipamento, todos filtrados por is_active.
-- =============================================================================
CREATE OR REPLACE VIEW vw_muscle_group_equipment_exercises AS
SELECT
  mg.id AS muscle_group_id,
  mg.name AS muscle_group_name,
  eq.id AS equipment_id,
  eq.name AS equipment_name,
  ec.id AS exercise_id,
  ec.name AS exercise_name,
  emc.role AS role,
  emc.stimulus_weight AS stimulus_weight,
  emc.fatigue_weight AS fatigue_weight
FROM exercise_muscle_contributions emc
JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
JOIN exercise_catalog ec ON ec.id = emc.mapping_id AND ec.is_active = 1
JOIN equipment_exercises eqex ON eqex.exercise_id = ec.id AND eqex.is_active = 1
JOIN equipment eq ON eq.id = eqex.equipment_id AND eq.is_active = 1
WHERE emc.is_active = 1;

-- =============================================================================
-- Tracking de versao (mesmo padrao de 001_schema.sql).
-- =============================================================================
INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (24, 'reestruturacao fase 2: exercise_catalog (catalogo canonico sem contaminacao Amazfit, preserva ids de exercise_zepp_mappings), exercise_external_mappings (mapeamento externo, backfill AMAZFIT), equipment_exercises (renomeacao semantica aditiva de equipment_exercise_mappings, chave exercise_id), UPDATE de muscle_groups (rotulos/musculos definitivos, mesmos ids), view vw_muscle_group_equipment_exercises');

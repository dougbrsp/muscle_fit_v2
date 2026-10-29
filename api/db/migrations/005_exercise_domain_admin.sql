-- Copiado de fitness-platform/mysql/init/003_exercise_domain_admin.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 003_exercise_domain_admin.sql
--
-- Fase 5 da reestruturacao: prepara o dominio exercicios/equipamentos/grupos
-- musculares para a nova secao admin (frontend `/admin/dominio-exercicios`).
--
-- NOTA: exercise_catalog.is_unilateral/is_compound e a renomeacao de
-- exercise_muscle_contributions.mapping_id -> exercise_id ja foram aplicadas
-- por uma tarefa em paralelo (schema_migrations versoes 25 e 26). Este
-- arquivo e idempotente em relacao a isso (os blocos correspondentes viram
-- no-op se as colunas ja existirem) e completa o que faltava:
--
-- 1. exercise_muscle_contributions.exercise_id: a coluna ja existe (rename
--    aplicado por outra tarefa), mas a FK ainda apontava para
--    exercise_zepp_mappings (tabela legada) em vez de exercise_catalog.
--    Validado sem orfaos/sem apontar para linha inativa antes de trocar a FK.
-- 2. exercise_aliases: mesma migracao mapping_id -> exercise_id ainda nao
--    tinha sido feita por ninguem; aplicada aqui (tambem validada sem
--    orfaos/sem apontar para linha inativa).
-- 3. View vw_muscle_group_equipment_exercises: recriada usando exercise_id
--    (nome de coluna atual) para o join com exercise_catalog.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. exercise_catalog.is_unilateral / is_compound (no-op se ja existirem)
-- -----------------------------------------------------------------------------
SET @ec_col_unilateral_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_catalog' AND COLUMN_NAME = 'is_unilateral'
);
SET @ec_col_unilateral_ddl = IF(@ec_col_unilateral_exists = 0,
  'ALTER TABLE exercise_catalog ADD COLUMN is_unilateral TINYINT(1) NOT NULL DEFAULT 0 AFTER is_active',
  'SELECT 1'
);
PREPARE ec_col_unilateral_stmt FROM @ec_col_unilateral_ddl; EXECUTE ec_col_unilateral_stmt; DEALLOCATE PREPARE ec_col_unilateral_stmt;

SET @ec_col_compound_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_catalog' AND COLUMN_NAME = 'is_compound'
);
SET @ec_col_compound_ddl = IF(@ec_col_compound_exists = 0,
  'ALTER TABLE exercise_catalog ADD COLUMN is_compound TINYINT(1) NULL AFTER is_unilateral',
  'SELECT 1'
);
PREPARE ec_col_compound_stmt FROM @ec_col_compound_ddl; EXECUTE ec_col_compound_stmt; DEALLOCATE PREPARE ec_col_compound_stmt;

-- -----------------------------------------------------------------------------
-- 2. exercise_muscle_contributions.exercise_id: coluna ja existe (rename
-- aplicado em paralelo); aqui so trocamos a FK para apontar para
-- exercise_catalog em vez de exercise_zepp_mappings.
-- -----------------------------------------------------------------------------
SET @emc_has_old_col = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_muscle_contributions' AND COLUMN_NAME = 'mapping_id'
);
SET @emc_has_new_col = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_muscle_contributions' AND COLUMN_NAME = 'exercise_id'
);

-- Caso a tarefa paralela ainda nao tenha rodado neste ambiente (mapping_id
-- continua existindo): faz a mesma migracao mapping_id -> exercise_id aqui,
-- traduzindo via exercise_legacy_id_map (cobre os 14 casos de consolidacao).
SET @emc_add_exercise_id_ddl = IF(@emc_has_new_col = 0,
  'ALTER TABLE exercise_muscle_contributions ADD COLUMN exercise_id BIGINT UNSIGNED NULL AFTER mapping_id',
  'SELECT 1'
);
PREPARE emc_add_stmt FROM @emc_add_exercise_id_ddl; EXECUTE emc_add_stmt; DEALLOCATE PREPARE emc_add_stmt;

SET @emc_backfill_ddl = IF(@emc_has_old_col = 1 AND @emc_has_new_col = 0,
  'UPDATE exercise_muscle_contributions emc JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = emc.mapping_id SET emc.exercise_id = elm.canonical_exercise_id WHERE emc.exercise_id IS NULL',
  'SELECT 1'
);
PREPARE emc_backfill_stmt FROM @emc_backfill_ddl; EXECUTE emc_backfill_stmt; DEALLOCATE PREPARE emc_backfill_stmt;

-- Remove qualquer FK existente em exercise_id/mapping_id que ainda aponte
-- para exercise_zepp_mappings (nome pode ser fk_emc_mapping ou
-- fk_emc_exercise dependendo de quem rodou a renomeacao) ANTES de mudar o
-- tipo da coluna para UNSIGNED, senao o MODIFY falha (ER 3780) com a FK
-- antiga ainda presa na coluna.
SET @emc_fk_wrong_name = (
  SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_muscle_contributions'
    AND REFERENCED_TABLE_NAME = 'exercise_zepp_mappings'
  LIMIT 1
);
SET @emc_drop_fk_ddl = IF(@emc_fk_wrong_name IS NOT NULL,
  CONCAT('ALTER TABLE exercise_muscle_contributions DROP FOREIGN KEY ', @emc_fk_wrong_name),
  'SELECT 1'
);
PREPARE emc_drop_fk_stmt FROM @emc_drop_fk_ddl; EXECUTE emc_drop_fk_stmt; DEALLOCATE PREPARE emc_drop_fk_stmt;

ALTER TABLE exercise_muscle_contributions MODIFY COLUMN exercise_id BIGINT UNSIGNED NOT NULL;

SET @emc_uq_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_muscle_contributions' AND INDEX_NAME = 'uq_emc_exercise_group'
);
SET @emc_uq_ddl = IF(@emc_uq_exists = 0,
  'ALTER TABLE exercise_muscle_contributions ADD UNIQUE KEY uq_emc_exercise_group (exercise_id, muscle_group_id)',
  'SELECT 1'
);
PREPARE emc_uq_stmt FROM @emc_uq_ddl; EXECUTE emc_uq_stmt; DEALLOCATE PREPARE emc_uq_stmt;

SET @emc_fk_correct_exists = (
  SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_muscle_contributions'
    AND COLUMN_NAME = 'exercise_id' AND REFERENCED_TABLE_NAME = 'exercise_catalog'
);
SET @emc_fk_correct_ddl = IF(@emc_fk_correct_exists = 0,
  'ALTER TABLE exercise_muscle_contributions ADD CONSTRAINT fk_emc_exercise_catalog FOREIGN KEY (exercise_id) REFERENCES exercise_catalog (id) ON DELETE CASCADE',
  'SELECT 1'
);
PREPARE emc_fk_correct_stmt FROM @emc_fk_correct_ddl; EXECUTE emc_fk_correct_stmt; DEALLOCATE PREPARE emc_fk_correct_stmt;

SET @emc_drop_old_col_ddl = IF(@emc_has_old_col = 1,
  'ALTER TABLE exercise_muscle_contributions DROP COLUMN mapping_id',
  'SELECT 1'
);
PREPARE emc_drop_old_col_stmt FROM @emc_drop_old_col_ddl; EXECUTE emc_drop_old_col_stmt; DEALLOCATE PREPARE emc_drop_old_col_stmt;

-- -----------------------------------------------------------------------------
-- 3. exercise_aliases: mapping_id (exercise_zepp_mappings) -> exercise_id
-- (exercise_catalog). Ninguem tinha feito esta ainda.
-- -----------------------------------------------------------------------------
SET @ea_has_old_col = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND COLUMN_NAME = 'mapping_id'
);
SET @ea_has_new_col = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND COLUMN_NAME = 'exercise_id'
);

SET @ea_add_ddl = IF(@ea_has_new_col = 0,
  'ALTER TABLE exercise_aliases ADD COLUMN exercise_id BIGINT UNSIGNED NULL AFTER mapping_id',
  'SELECT 1'
);
PREPARE ea_add_stmt FROM @ea_add_ddl; EXECUTE ea_add_stmt; DEALLOCATE PREPARE ea_add_stmt;

-- Backfill so faz sentido enquanto mapping_id ainda existe (idempotencia:
-- numa segunda execucao apos o DROP COLUMN mapping_id la embaixo, estas
-- UPDATEs virariam erro de coluna desconhecida se nao fossem guardadas).
--
-- AJUSTE LOCAL (nao presente no arquivo original fitness-platform/mysql/init/
-- 003_exercise_domain_admin.sql): a tabela exercise_legacy_id_map, segundo o
-- proprio cabecalho deste arquivo, veio de "uma tarefa em paralelo" no
-- ambiente on-premise legado (schema_migrations versoes 25/26) e nunca foi
-- capturada em nenhum dos 20 arquivos SQL numerados portados para este repo -
-- ou seja, ela nao existe em nenhuma migration desta pasta. Rodar este
-- arquivo do zero (schema fresh, docker-entrypoint-initdb.d) falhava com
-- "ERROR 1146: Table 'fitness.exercise_legacy_id_map' doesn't exist". Guarda
-- adicional (@ea_legacy_map_exists) faz este backfill virar no-op quando a
-- tabela nao existir; o backfill de fallback logo abaixo (via join direto com
-- exercise_catalog, ids 1:1 preservados) cobre o mesmo resultado nesse caso.
SET @ea_legacy_map_exists = (
  SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_legacy_id_map'
);
SET @ea_backfill_legacy_ddl = IF(@ea_has_old_col = 1 AND @ea_legacy_map_exists = 1,
  'UPDATE exercise_aliases ea JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = ea.mapping_id SET ea.exercise_id = elm.canonical_exercise_id WHERE ea.exercise_id IS NULL',
  'SELECT 1'
);
PREPARE ea_backfill_legacy_stmt FROM @ea_backfill_legacy_ddl; EXECUTE ea_backfill_legacy_stmt; DEALLOCATE PREPARE ea_backfill_legacy_stmt;

-- fallback: aliases cujo mapping_id ja e igual ao id em exercise_catalog
-- (caso o legacy map nao cubra, por exemplo id novo inserido depois do
-- backfill original) mas que ainda estejam com exercise_id NULL.
SET @ea_backfill_fallback_ddl = IF(@ea_has_old_col = 1,
  'UPDATE exercise_aliases ea JOIN exercise_catalog ec ON ec.id = ea.mapping_id SET ea.exercise_id = ec.id WHERE ea.exercise_id IS NULL',
  'SELECT 1'
);
PREPARE ea_backfill_fallback_stmt FROM @ea_backfill_fallback_ddl; EXECUTE ea_backfill_fallback_stmt; DEALLOCATE PREPARE ea_backfill_fallback_stmt;

ALTER TABLE exercise_aliases MODIFY COLUMN exercise_id BIGINT UNSIGNED NOT NULL;

SET @ea_fk_wrong_name = (
  SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases'
    AND REFERENCED_TABLE_NAME = 'exercise_zepp_mappings'
  LIMIT 1
);
SET @ea_drop_fk_ddl = IF(@ea_fk_wrong_name IS NOT NULL,
  CONCAT('ALTER TABLE exercise_aliases DROP FOREIGN KEY ', @ea_fk_wrong_name),
  'SELECT 1'
);
PREPARE ea_drop_fk_stmt FROM @ea_drop_fk_ddl; EXECUTE ea_drop_fk_stmt; DEALLOCATE PREPARE ea_drop_fk_stmt;

SET @ea_uq_old_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND INDEX_NAME = 'uq_exercise_aliases_mapping_alias'
);
SET @ea_uq_old_ddl = IF(@ea_uq_old_exists > 0,
  'ALTER TABLE exercise_aliases DROP INDEX uq_exercise_aliases_mapping_alias',
  'SELECT 1'
);
PREPARE ea_uq_old_stmt FROM @ea_uq_old_ddl; EXECUTE ea_uq_old_stmt; DEALLOCATE PREPARE ea_uq_old_stmt;

SET @ea_idx_old_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND INDEX_NAME = 'idx_exercise_aliases_mapping'
);
SET @ea_idx_old_ddl = IF(@ea_idx_old_exists > 0,
  'ALTER TABLE exercise_aliases DROP INDEX idx_exercise_aliases_mapping',
  'SELECT 1'
);
PREPARE ea_idx_old_stmt FROM @ea_idx_old_ddl; EXECUTE ea_idx_old_stmt; DEALLOCATE PREPARE ea_idx_old_stmt;

SET @ea_uq_new_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND INDEX_NAME = 'uq_exercise_aliases_exercise_alias'
);
SET @ea_uq_new_ddl = IF(@ea_uq_new_exists = 0,
  'ALTER TABLE exercise_aliases ADD UNIQUE KEY uq_exercise_aliases_exercise_alias (exercise_id, alias)',
  'SELECT 1'
);
PREPARE ea_uq_new_stmt FROM @ea_uq_new_ddl; EXECUTE ea_uq_new_stmt; DEALLOCATE PREPARE ea_uq_new_stmt;

SET @ea_fk_correct_exists = (
  SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases'
    AND COLUMN_NAME = 'exercise_id' AND REFERENCED_TABLE_NAME = 'exercise_catalog'
);
SET @ea_fk_correct_ddl = IF(@ea_fk_correct_exists = 0,
  'ALTER TABLE exercise_aliases ADD CONSTRAINT fk_exercise_aliases_exercise_catalog FOREIGN KEY (exercise_id) REFERENCES exercise_catalog (id) ON DELETE CASCADE',
  'SELECT 1'
);
PREPARE ea_fk_correct_stmt FROM @ea_fk_correct_ddl; EXECUTE ea_fk_correct_stmt; DEALLOCATE PREPARE ea_fk_correct_stmt;

SET @ea_has_old_col_final = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND COLUMN_NAME = 'mapping_id'
);
SET @ea_drop_old_col_ddl = IF(@ea_has_old_col_final = 1,
  'ALTER TABLE exercise_aliases DROP COLUMN mapping_id',
  'SELECT 1'
);
PREPARE ea_drop_old_col_stmt FROM @ea_drop_old_col_ddl; EXECUTE ea_drop_old_col_stmt; DEALLOCATE PREPARE ea_drop_old_col_stmt;

SET @ea_idx_new_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases' AND INDEX_NAME = 'idx_exercise_aliases_exercise'
);
SET @ea_idx_new_ddl = IF(@ea_idx_new_exists = 0,
  'ALTER TABLE exercise_aliases ADD INDEX idx_exercise_aliases_exercise (exercise_id)',
  'SELECT 1'
);
PREPARE ea_idx_new_stmt FROM @ea_idx_new_ddl; EXECUTE ea_idx_new_stmt; DEALLOCATE PREPARE ea_idx_new_stmt;

-- -----------------------------------------------------------------------------
-- 4. View de navegacao: recriada usando exercise_id (nome de coluna atual)
-- -----------------------------------------------------------------------------
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
JOIN exercise_catalog ec ON ec.id = emc.exercise_id AND ec.is_active = 1
JOIN equipment_exercises eqex ON eqex.exercise_id = ec.id AND eqex.is_active = 1
JOIN equipment eq ON eq.id = eqex.equipment_id AND eq.is_active = 1
WHERE emc.is_active = 1;

-- =============================================================================
-- Tracking de versao (mesmo padrao de 001_schema.sql / 002_reestruturacao.sql).
-- Usa 27 pois 25 e 26 ja foram consumidos por uma tarefa em paralelo.
-- =============================================================================
INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (27, 'reestruturacao fase 5 (admin dominio exercicios): FK de exercise_muscle_contributions.exercise_id corrigida para exercise_catalog (antes ainda apontava para exercise_zepp_mappings); exercise_aliases migrada de mapping_id para exercise_id (FK exercise_catalog); view vw_muscle_group_equipment_exercises recriada');

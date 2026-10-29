-- Copiado de fitness-platform/mysql/init/004_wpe_mapping_fk_exercise_catalog.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 004_wpe_mapping_fk_exercise_catalog.sql
--
-- Fase de "fonte de verdade unica" da reestruturacao: workout_plan_exercises.
-- mapping_id ainda referenciava exercise_zepp_mappings(id) (tabela legada,
-- fora de leitura para os motores desde a Fase 4). Como exercise_catalog
-- preserva o MESMO espaco de ids de exercise_zepp_mappings.id (inclusive os
-- 14 ids absorvidos por consolidacao, que continuam existindo em
-- exercise_catalog com is_active=0), trocar a FK e seguro e nao perde
-- nenhuma referencia existente - nenhum UPDATE de dado e necessario, so a
-- troca da constraint em si.
--
-- Idempotente: so mexe na FK se ela ainda apontar para exercise_zepp_mappings.
-- so pode ser aplicado contra fitness-mysql-reestrut (porta 3407). NUNCA
-- rodar isto contra o MySQL de producao (porta 3307).
-- =============================================================================

USE fitness;

SET @wpe_fk_wrong_name = (
  SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'mapping_id' AND REFERENCED_TABLE_NAME = 'exercise_zepp_mappings'
  LIMIT 1
);
SET @wpe_drop_fk_ddl = IF(@wpe_fk_wrong_name IS NOT NULL,
  CONCAT('ALTER TABLE workout_plan_exercises DROP FOREIGN KEY ', @wpe_fk_wrong_name),
  'SELECT 1'
);
PREPARE wpe_drop_fk_stmt FROM @wpe_drop_fk_ddl; EXECUTE wpe_drop_fk_stmt; DEALLOCATE PREPARE wpe_drop_fk_stmt;

-- exercise_catalog.id e BIGINT UNSIGNED; workout_plan_exercises.mapping_id
-- foi criada como BIGINT (signed) simples. MySQL recusa a FK entre tipos
-- incompativeis (ER 3780) mesmo com a FK antiga removida - alinha o tipo
-- antes de recriar a constraint. Seguro: mapping_id so guarda ids positivos.
SET @wpe_col_type = (
  SELECT COLUMN_TYPE FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises' AND COLUMN_NAME = 'mapping_id'
);
SET @wpe_modify_col_ddl = IF(@wpe_col_type = 'bigint',
  'ALTER TABLE workout_plan_exercises MODIFY COLUMN mapping_id BIGINT UNSIGNED NULL',
  'SELECT 1'
);
PREPARE wpe_modify_col_stmt FROM @wpe_modify_col_ddl; EXECUTE wpe_modify_col_stmt; DEALLOCATE PREPARE wpe_modify_col_stmt;

SET @wpe_fk_correct_exists = (
  SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'mapping_id' AND REFERENCED_TABLE_NAME = 'exercise_catalog'
);
SET @wpe_fk_correct_ddl = IF(@wpe_fk_correct_exists = 0,
  'ALTER TABLE workout_plan_exercises ADD CONSTRAINT fk_wpe_mapping_catalog FOREIGN KEY (mapping_id) REFERENCES exercise_catalog (id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE wpe_fk_correct_stmt FROM @wpe_fk_correct_ddl; EXECUTE wpe_fk_correct_stmt; DEALLOCATE PREPARE wpe_fk_correct_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (28, 'workout_plan_exercises.mapping_id: FK trocada de exercise_zepp_mappings para exercise_catalog (fonte de verdade unica pos-reestruturacao; mesmo espaco de ids, nenhum dado perdido)');

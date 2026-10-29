-- Copiado de fitness-platform/mysql/init/002b_relatorio_divergencia_legado.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- 002b_relatorio_divergencia_legado.sql
-- Script de CONSULTA (nao e migration, nao roda automaticamente e nao deve
-- ser referenciado em schema_migrations). Compara o mapeamento legado
-- (exercise_zepp_mappings.primary_group_id/secondary_group_id) com o modelo
-- novo (exercise_muscle_contributions) por exercicio, seguindo a secao 43 do
-- documento de reestruturacao.
--
-- Status possiveis:
--   LEGACY_NULL           -> exercicio legado sem primary_group_id (nada a comparar)
--   MISSING_CONTRIBUTION  -> legado tem primary_group_id mas nao ha nenhuma linha
--                            em exercise_muscle_contributions para o exercicio
--   MULTIPLE_PRIMARY      -> exercise_muscle_contributions tem mais de uma linha
--                            role='PRIMARY' para o exercicio (esperado no maximo 1)
--   MATCH                 -> a(s) role='PRIMARY' de exercise_muscle_contributions
--                            bate exatamente com primary_group_id do legado
--   MISMATCH              -> ha PRIMARY em exercise_muscle_contributions, mas
--                            nenhuma bate com primary_group_id do legado
--
-- Uso: mysql -u<user> -p<senha> fitness < 002b_relatorio_divergencia_legado.sql

USE fitness;

WITH primary_new AS (
  SELECT
    mapping_id AS exercise_id,
    GROUP_CONCAT(DISTINCT muscle_group_id ORDER BY muscle_group_id) AS primary_group_ids_new,
    COUNT(*) AS primary_count_new
  FROM exercise_muscle_contributions
  WHERE role = 'PRIMARY' AND is_active = 1
  GROUP BY mapping_id
),
any_contribution AS (
  SELECT DISTINCT mapping_id AS exercise_id
  FROM exercise_muscle_contributions
  WHERE is_active = 1
)
SELECT
  zm.id AS exercise_id,
  zm.exercise_name,
  zm.primary_group_id AS legacy_primary_group_id,
  pn.primary_group_ids_new,
  pn.primary_count_new,
  CASE
    WHEN zm.primary_group_id IS NULL THEN 'LEGACY_NULL'
    WHEN ac.exercise_id IS NULL THEN 'MISSING_CONTRIBUTION'
    WHEN pn.primary_count_new > 1 THEN 'MULTIPLE_PRIMARY'
    WHEN FIND_IN_SET(zm.primary_group_id, pn.primary_group_ids_new) > 0 THEN 'MATCH'
    ELSE 'MISMATCH'
  END AS status
FROM exercise_zepp_mappings zm
LEFT JOIN primary_new pn ON pn.exercise_id = zm.id
LEFT JOIN any_contribution ac ON ac.exercise_id = zm.id
ORDER BY status, zm.exercise_name;

-- Resumo por status (rodar em seguida para o total por categoria).
WITH primary_new AS (
  SELECT
    mapping_id AS exercise_id,
    GROUP_CONCAT(DISTINCT muscle_group_id ORDER BY muscle_group_id) AS primary_group_ids_new,
    COUNT(*) AS primary_count_new
  FROM exercise_muscle_contributions
  WHERE role = 'PRIMARY' AND is_active = 1
  GROUP BY mapping_id
),
any_contribution AS (
  SELECT DISTINCT mapping_id AS exercise_id
  FROM exercise_muscle_contributions
  WHERE is_active = 1
)
SELECT
  CASE
    WHEN zm.primary_group_id IS NULL THEN 'LEGACY_NULL'
    WHEN ac.exercise_id IS NULL THEN 'MISSING_CONTRIBUTION'
    WHEN pn.primary_count_new > 1 THEN 'MULTIPLE_PRIMARY'
    WHEN FIND_IN_SET(zm.primary_group_id, pn.primary_group_ids_new) > 0 THEN 'MATCH'
    ELSE 'MISMATCH'
  END AS status,
  COUNT(*) AS total
FROM exercise_zepp_mappings zm
LEFT JOIN primary_new pn ON pn.exercise_id = zm.id
LEFT JOIN any_contribution ac ON ac.exercise_id = zm.id
GROUP BY status
ORDER BY total DESC;

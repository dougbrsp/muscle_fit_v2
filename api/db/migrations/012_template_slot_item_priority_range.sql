-- Copiado de fitness-platform/mysql/init/009_template_slot_item_priority_range.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 009_template_slot_item_priority_range.sql
--
-- Motor de geracao de ficha v2 (orientado a Tipo de Treinamento + Modelo de
-- Treino/slots) — ver plano completo em
-- C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md
-- e a especificacao de negocio MELHORIA_MOTOR_FICHA.md (secoes 4, 11, 12, 36).
--
-- Introduz, em workout_plan_template_slot_items:
--   - priority_min_pct TINYINT UNSIGNED NULL
--   - priority_max_pct TINYINT UNSIGNED NULL
-- (apos a coluna `prioritize`) — faixa percentual (0-100) de quanto da meta
-- semanal (`training_type_muscle_group_targets.target_weekly_sets`) do grupo
-- muscular daquele item deve ser entregue NESTE slot especifico. Ortogonal a
-- `prioritize` (que so afeta ORDEM da sessao, nunca volume — ver secao 36 do
-- documento de especificacao).
--
-- NOTA DE DESVIO DO PLANO ORIGINAL: o plano aprovado previa esta migration
-- como "010_..." com schema_migrations.version=35, backfill a partir de
-- `workout_plan_template_slots.priority_min_pct/priority_max_pct` (que o
-- plano presumia existirem desde a migration 009, e training_types.py como
-- CRUD ja completo desde a migration 008). Auditoria do repositorio real
-- (2026-08-29) mostrou que training_types so existia em um branch orfao
-- nunca mergeado (worktree-agent-ae7962e57ce78e91d, commit 89ef10fe,
-- cherry-picked para esta branch como 008_training_types.sql/
-- training_types.py) e que a faixa percentual NUNCA existiu no nivel de
-- slot (nem em codigo, nem em SQL) — e portanto nao ha nada para fazer
-- backfill. Preservado o CONCEITO do plano (faixa percentual por item,
-- aditiva, idempotente, mesmo padrao information_schema+PREPARE/EXECUTE);
-- adaptada a implementacao: sem colunas deprecated no nivel de slot (nunca
-- existiram) e sem backfill (nao ha dado legado para copiar).
--
-- CORRECAO (2026-08-29, antes de aplicar em producao): a nota acima
-- presumia MAX(version)=33, mas o banco de producao real ja tem version=34
-- ocupada por OUTRA migration ("workout_plan_template_slots ganha
-- priority_min_pct/priority_max_pct... por slot", nivel de SLOT, nao de
-- item) — aplicada direto em producao por uma sessao anterior, cujo
-- arquivo .sql correspondente tambem nunca foi commitado neste repositorio
-- (mesma situacao orfa do cherry-pick de training_types acima; ver
-- C:\openclaw-dev-agent\fitness-platform\mysql\init\009_template_slot_priority_range.sql
-- no checkout principal, fora desta worktree — nao trazido para ca por
-- estar fora do escopo desta entrega). Version corrigida para 35, unico
-- valor realmente livre confirmado por leitura direta de
-- schema_migrations em producao.
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005/006/007/008_*.sql).
-- =============================================================================

USE fitness;

SET @wptsi_has_priority_min_pct = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'priority_min_pct'
);
SET @wptsi_priority_min_pct_ddl = IF(@wptsi_has_priority_min_pct = 0,
  'ALTER TABLE workout_plan_template_slot_items ADD COLUMN priority_min_pct TINYINT UNSIGNED NULL AFTER prioritize',
  'SELECT 1'
);
PREPARE wptsi_priority_min_pct_stmt FROM @wptsi_priority_min_pct_ddl;
EXECUTE wptsi_priority_min_pct_stmt;
DEALLOCATE PREPARE wptsi_priority_min_pct_stmt;

SET @wptsi_has_priority_max_pct = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'priority_max_pct'
);
SET @wptsi_priority_max_pct_ddl = IF(@wptsi_has_priority_max_pct = 0,
  'ALTER TABLE workout_plan_template_slot_items ADD COLUMN priority_max_pct TINYINT UNSIGNED NULL AFTER priority_min_pct',
  'SELECT 1'
);
PREPARE wptsi_priority_max_pct_stmt FROM @wptsi_priority_max_pct_ddl;
EXECUTE wptsi_priority_max_pct_stmt;
DEALLOCATE PREPARE wptsi_priority_max_pct_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (35, 'Motor de ficha v2: workout_plan_template_slot_items ganha priority_min_pct/priority_max_pct (faixa percentual da meta semanal do training_type alocada a este item/slot) — aditivo, sem coluna equivalente no nivel de slot (nunca existiu), ortogonal a prioritize');

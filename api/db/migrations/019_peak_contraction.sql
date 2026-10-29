-- Copiado de fitness-platform/mysql/init/016_peak_contraction.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 016_peak_contraction.sql
--
-- Incremento da feature de técnica de execução do modo equipment_series:
-- adiciona a opção de "pico de contração" (segurar a contração na última rep
-- da série de trabalho por N segundos), configurável por item do card de
-- exercício, com o mesmo padrão de presença = checkbox marcado / ausência =
-- desmarcado usado em warmup_reps/feeler_set_reps (sem BOOLEAN redundante):
--   - peak_contraction_seconds: duração em segundos do pico de contração
--     (default de UI = 2; NULL = não configurado / checkbox desmarcado).
--
-- Posicionamento das colunas:
--   - workout_plan_template_slot_items: logo APÓS sets_count (séries de
--     TRABALHO) — pico de contração é uma propriedade da execução da série
--     de trabalho em si, então fica ao lado do campo que ela complementa,
--     antes dos demais campos de meta (reps_target/rpe_target/etc.).
--   - workout_plan_exercises: APÓS feeler_set_rest_seconds (último campo do
--     bloco de técnica/aquecimento/ajuste de 014/015), mantendo o padrão já
--     estabelecido de acrescentar cada novo campo informativo ao final desse
--     bloco espelhado.
--
-- Espelhado nas MESMAS duas tabelas de 014/015:
--   - workout_plan_template_slot_items (builder de modelos, /modelos-treino)
--   - workout_plan_exercises (espelho informativo da ficha salva)
--
-- sets_count (séries de TRABALHO) permanece intocado — pico de contração é
-- só uma anotação de execução, não entra no cálculo de ativação muscular
-- (VolumeService).
--
-- Idempotente (mesmo padrão information_schema + PREPARE/EXECUTE de 013/014/015).
-- =============================================================================

USE fitness;

-- =============================================================================
-- 1. workout_plan_template_slot_items
-- =============================================================================

SET @wptsi_has_peak_contraction_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_template_slot_items'
    AND COLUMN_NAME = 'peak_contraction_seconds'
);
SET @wptsi_peak_contraction_seconds_ddl = IF(@wptsi_has_peak_contraction_seconds = 0,
  "ALTER TABLE workout_plan_template_slot_items ADD COLUMN peak_contraction_seconds INT NULL AFTER sets_count",
  'SELECT 1'
);
PREPARE wptsi_peak_contraction_seconds_stmt FROM @wptsi_peak_contraction_seconds_ddl;
EXECUTE wptsi_peak_contraction_seconds_stmt;
DEALLOCATE PREPARE wptsi_peak_contraction_seconds_stmt;

-- =============================================================================
-- 2. workout_plan_exercises (espelho informativo)
-- =============================================================================

SET @wpe_has_peak_contraction_seconds = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises'
    AND COLUMN_NAME = 'peak_contraction_seconds'
);
SET @wpe_peak_contraction_seconds_ddl = IF(@wpe_has_peak_contraction_seconds = 0,
  "ALTER TABLE workout_plan_exercises ADD COLUMN peak_contraction_seconds INT NULL AFTER feeler_set_rest_seconds",
  'SELECT 1'
);
PREPARE wpe_peak_contraction_seconds_stmt FROM @wpe_peak_contraction_seconds_ddl;
EXECUTE wpe_peak_contraction_seconds_stmt;
DEALLOCATE PREPARE wpe_peak_contraction_seconds_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (41, 'Pico de contracao configuravel no modo equipment_series (peak_contraction_seconds, default de UI = 2s, NULL = checkbox desmarcado): segurar a contracao na ultima repeticao da serie de trabalho; coluna espelhada em workout_plan_template_slot_items (apos sets_count) e workout_plan_exercises (apos feeler_set_rest_seconds, fim do bloco tecnica/aquecimento/ajuste); sets_count/VolumeService inalterados');

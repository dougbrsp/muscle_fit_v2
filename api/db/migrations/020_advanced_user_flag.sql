-- Copiado de fitness-platform/mysql/init/017_advanced_user_flag.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 017_advanced_user_flag.sql
--
-- Flag "usuário avançado" por usuário: quando marcado, um RPE 10 precoce
-- (antes da última série de um exercício — falha técnica precoce) deixa de
-- ser tratado como sinal de "carga excessiva" para dois motores:
--   - progression_service.suggest_load_and_reps (reducao de -5% na carga
--     sugerida da proxima sessao);
--   - intelligence/volume_service.py: calculo de rpe_anticipated, consumido
--     por classify_readiness/reconcile_recovery_pct em
--     intelligence/muscle_recovery_service.py (rebaixamento de
--     readiness_status/recovery_pct do grupo muscular).
--
-- MuscleRecoveryService.failure_penalty() fica FORA DE ESCOPO desta mudanca
-- (nao mexido) — continua penalizando dose por RPE10 adicional igualmente
-- para todos os usuarios.
--
-- users.is_advanced_user: TINYINT(1) NOT NULL DEFAULT 0 (mesmo padrao de
-- BOOLEAN-como-TINYINT ja usado em outras colunas de users, ex.: is_active).
--
-- Ao final, marca o usuario real 'doug' como avancado (UPDATE incondicional,
-- seguro de repetir).
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE de
-- 013/014/015/016).
-- =============================================================================

USE fitness;

SET @users_has_is_advanced_user = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
    AND COLUMN_NAME = 'is_advanced_user'
);
SET @users_is_advanced_user_ddl = IF(@users_has_is_advanced_user = 0,
  "ALTER TABLE users ADD COLUMN is_advanced_user TINYINT(1) NOT NULL DEFAULT 0 AFTER is_active",
  'SELECT 1'
);
PREPARE users_is_advanced_user_stmt FROM @users_is_advanced_user_ddl;
EXECUTE users_is_advanced_user_stmt;
DEALLOCATE PREPARE users_is_advanced_user_stmt;

-- Marca o usuario real como avancado. Incondicional e seguro de repetir
-- (idempotente por natureza: sempre resulta em is_advanced_user = 1 para
-- username = 'doug', se ele existir).
UPDATE users SET is_advanced_user = 1 WHERE username = 'doug';

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (42, 'Flag usuarios.is_advanced_user (TINYINT(1) NOT NULL DEFAULT 0, apos is_active): usuario avancado nao tem RPE10 precoce (antes da ultima serie) tratado como carga excessiva em suggest_load_and_reps (progression_service) nem em rpe_anticipated (intelligence/volume_service.py); failure_penalty() inalterado; usuario doug marcado como avancado');

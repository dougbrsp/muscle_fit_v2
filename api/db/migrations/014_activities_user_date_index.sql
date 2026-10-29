-- Copiado de fitness-platform/mysql/init/011_activities_user_date_index.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

-- =============================================================================
-- 011_activities_user_date_index.sql
--
-- Dashboard novo (hub paralelo /dashboard/*, ver plano completo em
-- C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md)
-- — Fase 1 (MVP: overview + status por grupo muscular).
--
-- `activities` so tinha indices SIMPLES (local_date, user_id, activity_type,
-- gym_id, session_status) — nenhum indice COMPOSTO (user_id, local_date), que
-- toda query nova do dashboard usa pesadamente (todo endpoint filtra por
-- `a.user_id = %s AND a.local_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)`,
-- mesmo padrao WHERE de analytics.py/volume_service.py). Indice aditivo,
-- puramente de performance — nao muda nenhuma query existente, nenhum
-- contrato de API.
--
-- Idempotente (mesmo padrao information_schema + PREPARE/EXECUTE usado em
-- 005/006/007/008/009/010_*.sql).
-- =============================================================================

USE fitness;

SET @activities_has_user_date_idx = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities'
    AND INDEX_NAME = 'idx_activities_user_date'
);
SET @activities_user_date_idx_ddl = IF(@activities_has_user_date_idx = 0,
  'ALTER TABLE activities ADD INDEX idx_activities_user_date (user_id, local_date)',
  'SELECT 1'
);
PREPARE activities_user_date_idx_stmt FROM @activities_user_date_idx_ddl;
EXECUTE activities_user_date_idx_stmt;
DEALLOCATE PREPARE activities_user_date_idx_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (37, 'Dashboard novo (hub paralelo /dashboard/*) Fase 1: indice composto idx_activities_user_date (user_id, local_date) em activities - performance para as queries de janela por usuario que os endpoints dashboard-overview/dashboard-muscle-status/dashboard-muscle-drilldown usam pesadamente; puramente aditivo, sem mudanca de contrato');

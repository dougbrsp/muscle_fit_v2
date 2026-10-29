-- =============================================================================
-- 021_exercise_legacy_id_map.sql
--
-- Tabela `exercise_legacy_id_map` existe de verdade na Aurora de producao,
-- populada com o historico real de consolidacao de exercicios duplicados
-- (old_exercise_id -> canonical_exercise_id). Ela foi criada por uma
-- migracao paralela no ambiente on-premise legado, nunca capturada em
-- nenhum dos 20 arquivos numerados originais que foram portados para
-- api/db/migrations/ -- o comentario de topo de
-- 005_exercise_domain_admin.sql ja documenta essa lacuna (schema_migrations
-- versoes 25/26 do legado, aplicadas "em paralelo" e nunca formalizadas
-- como arquivo numerado).
--
-- Esta migracao cria a tabela vazia (sem dados). Nao ha como reconstruir o
-- historico real de consolidacao de exercicios sem acesso ao dump de
-- producao, e isso nao e necessario para o ambiente local: todo call-site
-- no backend consome esta tabela via `LEFT JOIN exercise_legacy_id_map elm
-- ON elm.old_exercise_id = <algo>.mapping_id` seguido de
-- `COALESCE(elm.canonical_exercise_id, <algo>.mapping_id)`, ou via SELECT
-- direto com fallback explicito para o proprio id quando nao ha linha
-- (ver api/app/services.py::_resolve_canonical_exercise_id e
-- api/app/exercise_catalog_admin.py). Ou seja, "sem mapeamento legado para
-- este id" ja e um caso normal e tolerado em todo o codigo -- localmente,
-- sem os dados historicos, o comportamento observado e "nenhum exercicio
-- tem duplicata historica consolidada", o que e uma degradacao graciosa
-- (nao uma quebra).
-- =============================================================================

CREATE TABLE IF NOT EXISTS exercise_legacy_id_map (
  old_exercise_id BIGINT UNSIGNED NOT NULL,
  canonical_exercise_id BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (old_exercise_id),
  KEY idx_exercise_legacy_id_map_canonical (canonical_exercise_id),
  CONSTRAINT fk_exercise_legacy_id_map_canonical FOREIGN KEY (canonical_exercise_id) REFERENCES exercise_catalog (id) ON DELETE CASCADE
);

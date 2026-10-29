-- Copiado de fitness-platform/mysql/init/003_rename_emc_mapping_id_and_catalog_flags.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

USE fitness;

-- ---------------------------------------------------------------------------
-- 1. exercise_muscle_contributions.mapping_id -> exercise_id
--
-- A coluna sempre guardou o id de exercise_zepp_mappings / exercise_catalog
-- (ids 1:1 preservados na reforma da fase 2), mas o nome "mapping_id" ficou
-- desatualizado depois que exercise_catalog virou a fonte canonica. Rename
-- puramente de nomenclatura, sem mudanca de dado ou de FK de destino.
-- ---------------------------------------------------------------------------

ALTER TABLE exercise_muscle_contributions
  DROP FOREIGN KEY fk_emc_mapping;

ALTER TABLE exercise_muscle_contributions
  CHANGE COLUMN mapping_id exercise_id BIGINT NOT NULL;

ALTER TABLE exercise_muscle_contributions
  DROP INDEX uq_emc_mapping_group,
  ADD UNIQUE KEY uq_emc_exercise_group (exercise_id, muscle_group_id);

ALTER TABLE exercise_muscle_contributions
  ADD CONSTRAINT fk_emc_exercise FOREIGN KEY (exercise_id) REFERENCES exercise_zepp_mappings (id) ON DELETE CASCADE;

-- Recria a view que referenciava emc.mapping_id.
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

-- ---------------------------------------------------------------------------
-- 2. exercise_catalog.is_unilateral / is_compound
--
-- Puramente aditivo, sem backfill de dado real (curadoria fica para uma
-- tarefa futura). is_unilateral tem default FALSE (maioria dos exercicios
-- e bilateral); is_compound fica NULL ate ser curado (nao sabemos ainda).
-- ---------------------------------------------------------------------------

ALTER TABLE exercise_catalog
  ADD COLUMN is_unilateral BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN is_compound BOOLEAN NULL;

INSERT INTO schema_migrations (version, name) VALUES
  (25, 'rename exercise_muscle_contributions.mapping_id para exercise_id (nomenclatura desatualizada; valor sempre foi o id de exercise_catalog/exercise_zepp_mappings) e recria vw_muscle_group_equipment_exercises'),
  (26, 'add exercise_catalog.is_unilateral (default false) e is_compound (nullable), aditivo sem backfill, para curadoria futura de flags do catalogo canonico');

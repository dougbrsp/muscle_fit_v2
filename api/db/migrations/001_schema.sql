-- Copiado de fitness-platform/mysql/init/001_schema.sql (repo legado C:\openclaw-dev-agent, congelado).
-- Renumerado sequencialmente para api/db/migrations/ (ver docs/local-dev.md para o mapeamento completo).

CREATE DATABASE IF NOT EXISTS fitness CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE fitness;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sources (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  type VARCHAR(60) NOT NULL,
  description TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS imports (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source_id BIGINT NULL,
  import_type VARCHAR(80) NOT NULL,
  external_id VARCHAR(255) NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'success',
  payload_hash CHAR(64) NULL,
  raw_payload JSON NULL,
  error_message TEXT NULL,
  imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_import_external (source_id, external_id, import_type),
  KEY idx_imports_imported_at (imported_at),
  CONSTRAINT fk_imports_sources FOREIGN KEY (source_id) REFERENCES sources(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS activities (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source_id BIGINT NULL,
  import_id BIGINT NULL,
  external_id VARCHAR(255) NULL,
  local_date DATE NOT NULL,
  started_at DATETIME NULL,
  ended_at DATETIME NULL,
  activity_type VARCHAR(60) NOT NULL DEFAULT 'musculacao',
  name VARCHAR(255) NOT NULL,
  description TEXT NULL,
  duration_seconds INT NULL,
  calories_kcal DECIMAL(10,2) NULL,
  avg_hr_bpm DECIMAL(6,2) NULL,
  max_hr_bpm DECIMAL(6,2) NULL,
  avg_rpe DECIMAL(4,2) NULL,
  notes TEXT NULL,
  -- Quem conduziu o treino: 'personal' (personal trainer) ou 'dougfitness'
  -- (seguiu a ficha gerada pelo agente de IA). Texto livre, essas duas sao
  -- so a convencao usada pelo frontend/prompts; NULL quando nao informado.
  conducted_by VARCHAR(60) NULL,
  raw_payload JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_activity_external (source_id, external_id),
  KEY idx_activities_date (local_date),
  KEY idx_activities_type (activity_type),
  CONSTRAINT fk_activities_sources FOREIGN KEY (source_id) REFERENCES sources(id),
  CONSTRAINT fk_activities_imports FOREIGN KEY (import_id) REFERENCES imports(id)
) ENGINE=InnoDB;

-- ALTER tardio: conducted_by pode nao existir em bancos ja provisionados
-- antes desta coluna ser adicionada.
SET @conducted_by_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities' AND COLUMN_NAME = 'conducted_by'
);
SET @conducted_by_col_ddl = IF(@conducted_by_col_exists = 0,
  'ALTER TABLE activities ADD COLUMN conducted_by VARCHAR(60) NULL AFTER notes',
  'SELECT 1'
);
PREPARE conducted_by_col_stmt FROM @conducted_by_col_ddl;
EXECUTE conducted_by_col_stmt;
DEALLOCATE PREPARE conducted_by_col_stmt;

CREATE TABLE IF NOT EXISTS activity_hr_zones (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  activity_id BIGINT NOT NULL,
  zone_number INT NOT NULL,
  zone_name VARCHAR(80) NULL,
  min_bpm INT NULL,
  max_bpm INT NULL,
  duration_seconds INT NULL,
  percent_time DECIMAL(6,2) NULL,
  UNIQUE KEY uq_activity_zone (activity_id, zone_number),
  CONSTRAINT fk_hr_zones_activity FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS exercise_zepp_mappings (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  exercise_name VARCHAR(255) NOT NULL,
  amazfit_name VARCHAR(255) NOT NULL,
  equipment VARCHAR(120) NULL,
  body_position VARCHAR(120) NULL,
  movement VARCHAR(120) NULL,
  trajectory VARCHAR(120) NULL,
  biomechanical_pattern VARCHAR(160) NULL,
  primary_joint VARCHAR(160) NULL,
  resistance_vector VARCHAR(120) NULL,
  primary_group VARCHAR(120) NULL,
  secondary_group VARCHAR(160) NULL,
  primary_muscles VARCHAR(255) NULL,
  secondary_muscles VARCHAR(255) NULL,
  keywords TEXT NULL,
  description TEXT NULL,
  mapping_status VARCHAR(40) NOT NULL DEFAULT 'confirmed',
  source VARCHAR(160) NOT NULL DEFAULT 'manual',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_exercise_zepp_mappings_exercise_name (exercise_name),
  KEY idx_exercise_zepp_mappings_amazfit_name (amazfit_name),
  KEY idx_exercise_zepp_mappings_primary_group (primary_group),
  KEY idx_exercise_zepp_mappings_status (mapping_status),
  FULLTEXT KEY ft_exercise_zepp_mappings_search (
    exercise_name,
    amazfit_name,
    keywords,
    description,
    primary_muscles,
    secondary_muscles
  )
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- gyms: academias onde os treinos acontecem. usual_weekdays e um array JSON
-- de dias ISO (1=segunda..7=domingo) so para referencia/sugestao; qualquer
-- academia pode ser usada em qualquer dia. is_recurring=false marca uma
-- academia avulsa (visita unica, sem frequencia habitual) — nesse caso
-- usual_weekdays e ignorado/mantido vazio.
CREATE TABLE IF NOT EXISTS gyms (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  usual_weekdays JSON NULL,
  is_recurring TINYINT(1) NOT NULL DEFAULT 1,
  notes TEXT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_gyms_name (name)
) ENGINE=InnoDB;

-- ALTER tardio: is_recurring pode nao existir em bancos ja provisionados
-- antes desta coluna ser adicionada.
SET @gym_is_recurring_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gyms' AND COLUMN_NAME = 'is_recurring'
);
SET @gym_is_recurring_col_ddl = IF(@gym_is_recurring_col_exists = 0,
  'ALTER TABLE gyms ADD COLUMN is_recurring TINYINT(1) NOT NULL DEFAULT 1 AFTER usual_weekdays',
  'SELECT 1'
);
PREPARE gym_is_recurring_col_stmt FROM @gym_is_recurring_col_ddl;
EXECUTE gym_is_recurring_col_stmt;
DEALLOCATE PREPARE gym_is_recurring_col_stmt;

-- ALTER tardio: activities.gym_id so pode ser criado depois que gyms existe.
SET @gym_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities' AND COLUMN_NAME = 'gym_id'
);
SET @gym_col_ddl = IF(@gym_col_exists = 0,
  'ALTER TABLE activities ADD COLUMN gym_id BIGINT NULL AFTER source_id, ADD KEY idx_activities_gym_id (gym_id), ADD CONSTRAINT fk_activities_gym FOREIGN KEY (gym_id) REFERENCES gyms(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE gym_col_stmt FROM @gym_col_ddl;
EXECUTE gym_col_stmt;
DEALLOCATE PREPARE gym_col_stmt;

-- equipment: catalogo global de equipamentos (nome unico + foto). Um mesmo
-- equipamento (ex.: "Leg Press 45") e cadastrado uma unica vez aqui e depois
-- associado a N academias/grupos musculares via gym_equipment, evitando
-- duplicar nome/foto quando mais de uma academia tem o mesmo equipamento.
CREATE TABLE IF NOT EXISTS equipment (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  notes TEXT NULL,
  photo_path VARCHAR(500) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_equipment_name (name)
) ENGINE=InnoDB;

-- gym_equipment: associa um equipamento do catalogo a uma academia e a um
-- mapeamento canonico de exercise_zepp_mappings (1 mapeamento -> N
-- associacoes; 1 equipamento -> N associacoes). A recomendacao de variacao
-- consulta por grupo muscular (primary_group do mapeamento), nao apenas
-- pelo mapeamento exato.
CREATE TABLE IF NOT EXISTS gym_equipment (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  gym_id BIGINT NOT NULL,
  mapping_id BIGINT NOT NULL,
  equipment_id BIGINT NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_gym_equipment_gym_mapping_equipment (gym_id, mapping_id, equipment_id),
  KEY idx_gym_equipment_mapping (mapping_id),
  KEY idx_gym_equipment_gym (gym_id),
  KEY idx_gym_equipment_equipment (equipment_id),
  CONSTRAINT fk_gym_equipment_gym FOREIGN KEY (gym_id) REFERENCES gyms(id) ON DELETE CASCADE,
  CONSTRAINT fk_gym_equipment_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_zepp_mappings(id) ON DELETE CASCADE,
  CONSTRAINT fk_gym_equipment_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Migracao tardia: bancos provisionados antes do catalogo `equipment`
-- existir tinham name/notes/photo_path direto em gym_equipment. Quando essas
-- colunas ainda existem, cria uma linha em `equipment` por nome distinto
-- (preservando foto, se houver), aponta cada gym_equipment para o
-- equipment_id correspondente e remove as colunas antigas.
DROP PROCEDURE IF EXISTS migrate_gym_equipment_to_catalog;

DELIMITER $$
CREATE PROCEDURE migrate_gym_equipment_to_catalog()
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gym_equipment' AND COLUMN_NAME = 'name'
  ) THEN
    INSERT IGNORE INTO equipment (name, photo_path)
    SELECT name, MAX(photo_path) FROM gym_equipment GROUP BY name;

    ALTER TABLE gym_equipment ADD COLUMN equipment_id BIGINT NULL AFTER mapping_id;

    UPDATE gym_equipment ge
    JOIN equipment eq ON eq.name = ge.name
    SET ge.equipment_id = eq.id;

    ALTER TABLE gym_equipment
      DROP KEY uq_gym_equipment_gym_mapping_name,
      DROP COLUMN name,
      DROP COLUMN notes,
      DROP COLUMN photo_path,
      MODIFY COLUMN equipment_id BIGINT NOT NULL,
      ADD CONSTRAINT fk_gym_equipment_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
      ADD UNIQUE KEY uq_gym_equipment_gym_mapping_equipment (gym_id, mapping_id, equipment_id),
      ADD KEY idx_gym_equipment_equipment (equipment_id);
  END IF;
END$$
DELIMITER ;

CALL migrate_gym_equipment_to_catalog();
DROP PROCEDURE migrate_gym_equipment_to_catalog;

-- equipment_exercise_mappings: associa um equipamento do catalogo (equipment)
-- a um mapeamento canonico (exercise_zepp_mappings) de forma GLOBAL, sem
-- depender de academia. Responde "este equipamento serve este exercicio?"
-- independente de onde o equipamento esta fisicamente instalado.
CREATE TABLE IF NOT EXISTS equipment_exercise_mappings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  equipment_id BIGINT NOT NULL,
  mapping_id BIGINT NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_equipment_exercise_mappings (equipment_id, mapping_id),
  KEY idx_eem_mapping (mapping_id),
  CONSTRAINT fk_eem_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  CONSTRAINT fk_eem_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_zepp_mappings(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- gym_equipment_catalog: registra apenas QUAIS equipamentos do catalogo uma
-- academia possui (sem grupo muscular/exercicio). A dimensao "serve este
-- exercicio" fica isolada em equipment_exercise_mappings.
CREATE TABLE IF NOT EXISTS gym_equipment_catalog (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  gym_id BIGINT NOT NULL,
  equipment_id BIGINT NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_gym_equipment_catalog (gym_id, equipment_id),
  KEY idx_gec_gym (gym_id),
  KEY idx_gec_equipment (equipment_id),
  CONSTRAINT fk_gec_gym FOREIGN KEY (gym_id) REFERENCES gyms(id) ON DELETE CASCADE,
  CONSTRAINT fk_gec_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Backfill idempotente a partir de gym_equipment existente (fonte de verdade
-- ate agora), sem apagar nem alterar nada em gym_equipment. A partir daqui,
-- gym_equipment passa a ser tratada como cache materializado, recalculado por
-- gym_equipment_sync.py a cada escrita em equipment_exercise_mappings ou
-- gym_equipment_catalog.
INSERT IGNORE INTO equipment_exercise_mappings (equipment_id, mapping_id, is_active)
SELECT equipment_id, mapping_id, MAX(is_active) FROM gym_equipment GROUP BY equipment_id, mapping_id;

INSERT IGNORE INTO gym_equipment_catalog (gym_id, equipment_id, is_active)
SELECT gym_id, equipment_id, MAX(is_active) FROM gym_equipment GROUP BY gym_id, equipment_id;

-- exercises: original_name preserva o texto bruto do log (import/MCP).
-- Nome/grupo/musculos canonicos vivem apenas em exercise_zepp_mappings,
-- referenciados por mapping_id. equipment_id registra o equipamento usado
-- (da academia do treino) para permitir consultar o ultimo equipamento
-- usado por grupo muscular/academia.
CREATE TABLE IF NOT EXISTS exercises (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  activity_id BIGINT NOT NULL,
  sequence INT NOT NULL,
  original_name VARCHAR(255) NOT NULL,
  mapping_id BIGINT NULL,
  sets_count INT NOT NULL DEFAULT 0,
  reps_total INT NULL,
  volume_kg DECIMAL(12,2) NULL,
  min_weight_kg DECIMAL(8,2) NULL,
  best_weight_kg DECIMAL(8,2) NULL,
  avg_rpe DECIMAL(4,2) NULL,
  notes TEXT NULL,
  source_row_hash CHAR(64) NULL,
  raw_sets_text VARCHAR(120) NULL,
  raw_reps_text VARCHAR(255) NULL,
  raw_load_text VARCHAR(255) NULL,
  raw_rpe_text VARCHAR(255) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_exercise_source_hash (source_row_hash),
  KEY idx_exercises_activity_sequence (activity_id, sequence),
  KEY idx_exercises_mapping_id (mapping_id),
  CONSTRAINT fk_exercises_activity FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE,
  CONSTRAINT fk_exercises_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_zepp_mappings(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ALTER tardio: exercises.equipment_id so pode ser criado depois que
-- gym_equipment existe. CREATE TABLE IF NOT EXISTS nao adiciona colunas em
-- tabelas ja existentes, entao isso cobre tanto instalacoes novas quanto
-- bancos ja provisionados.
SET @equip_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercises' AND COLUMN_NAME = 'equipment_id'
);
SET @equip_col_ddl = IF(@equip_col_exists = 0,
  'ALTER TABLE exercises ADD COLUMN equipment_id BIGINT NULL AFTER mapping_id, ADD KEY idx_exercises_equipment_id (equipment_id), ADD CONSTRAINT fk_exercises_equipment FOREIGN KEY (equipment_id) REFERENCES gym_equipment(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE equip_col_stmt FROM @equip_col_ddl;
EXECUTE equip_col_stmt;
DEALLOCATE PREPARE equip_col_stmt;

CREATE TABLE IF NOT EXISTS exercise_sets (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  exercise_id BIGINT NOT NULL,
  set_number INT NOT NULL,
  set_type VARCHAR(40) NOT NULL DEFAULT 'working',
  reps INT NULL,
  weight_kg DECIMAL(8,2) NULL,
  rpe DECIMAL(4,2) NULL,
  rest_seconds INT NULL,
  notes TEXT NULL,
  raw_reps_text VARCHAR(255) NULL,
  raw_load_text VARCHAR(255) NULL,
  raw_rpe_text VARCHAR(255) NULL,
  UNIQUE KEY uq_exercise_set_number (exercise_id, set_number),
  KEY idx_sets_rpe (rpe),
  CONSTRAINT fk_sets_exercise FOREIGN KEY (exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS daily_biometrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  local_date DATE NOT NULL UNIQUE,
  weight_kg DECIMAL(6,2) NULL,
  resting_hr_bpm DECIMAL(6,2) NULL,
  sleep_minutes INT NULL,
  steps INT NULL,
  calories_active_kcal DECIMAL(10,2) NULL,
  calories_total_kcal DECIMAL(10,2) NULL,
  notes TEXT NULL,
  raw_payload JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_daily_biometrics_date (local_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS body_composition (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  local_date DATE NOT NULL,
  measured_at DATETIME NULL,
  weight_kg DECIMAL(6,2) NULL,
  bmi DECIMAL(5,2) NULL,
  body_fat_percent DECIMAL(5,2) NULL,
  fat_mass_kg DECIMAL(6,2) NULL,
  fat_free_mass_kg DECIMAL(6,2) NULL,
  muscle_mass_kg DECIMAL(6,2) NULL,
  skeletal_muscle_percent DECIMAL(5,2) NULL,
  body_water_percent DECIMAL(5,2) NULL,
  visceral_fat_rating DECIMAL(5,2) NULL,
  bone_mass_kg DECIMAL(5,2) NULL,
  bmr_kcal DECIMAL(8,2) NULL,
  subcutaneous_fat_percent DECIMAL(5,2) NULL,
  protein_percent DECIMAL(5,2) NULL,
  metabolic_age INT NULL,
  source VARCHAR(120) NULL,
  notes TEXT NULL,
  raw_payload JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_body_composition_date_source (local_date, source),
  KEY idx_body_composition_date (local_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS exercise_mappings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  original_name VARCHAR(255) NOT NULL,
  amazfit_name VARCHAR(255) NOT NULL,
  mapped_name VARCHAR(255) NULL,
  muscle_primary VARCHAR(120) NULL,
  muscle_secondary VARCHAR(120) NULL,
  confidence DECIMAL(5,2) NULL,
  is_user_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
  notes TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_mapping_original_amazfit (original_name, amazfit_name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS workout_plans (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_date DATE NULL,
  plan_time TIME NULL,
  name VARCHAR(255) NOT NULL,
  plan_type VARCHAR(80) NOT NULL DEFAULT 'full_body',
  split_day VARCHAR(1) NULL,
  goal TEXT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'planned',
  raw_payload JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_workout_plans_date (plan_date),
  KEY idx_workout_plans_status (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS workout_plan_exercises (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_id BIGINT NOT NULL,
  sequence INT NOT NULL,
  exercise_name VARCHAR(255) NOT NULL,
  amazfit_name VARCHAR(255) NULL,
  muscle_primary VARCHAR(120) NULL,
  muscle_secondary VARCHAR(120) NULL,
  sets_count INT NULL,
  reps_target VARCHAR(80) NULL,
  load_target VARCHAR(80) NULL,
  rpe_target VARCHAR(80) NULL,
  rest_seconds INT NULL,
  warmup BOOLEAN NOT NULL DEFAULT FALSE,
  substitution_options JSON NULL,
  notes TEXT NULL,
  UNIQUE KEY uq_plan_sequence (plan_id, sequence),
  CONSTRAINT fk_plan_exercises_plan FOREIGN KEY (plan_id) REFERENCES workout_plans(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS weekly_summaries (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  week_start_date DATE NOT NULL UNIQUE,
  week_end_date DATE NOT NULL,
  workouts_count INT NOT NULL DEFAULT 0,
  total_sets INT NULL,
  total_reps INT NULL,
  total_volume_kg DECIMAL(14,2) NULL,
  high_rpe_sets INT NULL,
  summary_text TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_weekly_summaries_week_start (week_start_date)
) ENGINE=InnoDB;

INSERT IGNORE INTO sources (name, type, description) VALUES
  ('telegram', 'chat', 'Sessões registradas pelo Telegram/OpenClaw'),
  ('openclaw', 'agent', 'Dados enviados pelo agente OpenClaw'),
  ('manual', 'manual', 'Dados importados manualmente'),
  ('amazfit-zepp', 'wearable', 'Dados extraídos do Amazfit/Zepp'),
  ('intervals-icu', 'external', 'Dados extraídos do Intervals.icu');

INSERT IGNORE INTO gyms (name, usual_weekdays) VALUES
  ('Gaviões', JSON_ARRAY(2, 4, 6, 7)),
  ('SmartFit', JSON_ARRAY(1, 5));

-- Equipamento DEFAULT (catalogo unico) associado a cada grupo muscular em
-- cada academia, ligado a um mapeamento "ancora" (o de menor id) daquele
-- grupo. Serve como fallback ate os equipamentos reais serem cadastrados.
INSERT IGNORE INTO equipment (name) VALUES ('DEFAULT');

-- Dinamico porque esta INSERT roda antes da migracao de muscle_groups (mais
-- abaixo neste mesmo arquivo) na primeira execucao contra um banco antigo,
-- mas roda DEPOIS dela (coluna primary_group ja removida) em reexecucoes
-- manuais idempotentes contra um banco ja migrado.
SET @zm_has_primary_group_col = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'primary_group'
);
SET @default_gym_equipment_ddl = IF(@zm_has_primary_group_col > 0,
  'INSERT IGNORE INTO gym_equipment (gym_id, mapping_id, equipment_id)
   SELECT g.id, anchor.mapping_id, (SELECT id FROM equipment WHERE name = ''DEFAULT'')
   FROM gyms g
   CROSS JOIN (
     SELECT MIN(id) AS mapping_id
     FROM exercise_zepp_mappings
     WHERE primary_group IS NOT NULL AND primary_group <> ''''
     GROUP BY primary_group
   ) anchor',
  'INSERT IGNORE INTO gym_equipment (gym_id, mapping_id, equipment_id)
   SELECT g.id, anchor.mapping_id, (SELECT id FROM equipment WHERE name = ''DEFAULT'')
   FROM gyms g
   CROSS JOIN (
     SELECT MIN(id) AS mapping_id
     FROM exercise_zepp_mappings
     WHERE primary_group_id IS NOT NULL
     GROUP BY primary_group_id
   ) anchor'
);
PREPARE default_gym_equipment_stmt FROM @default_gym_equipment_ddl;
EXECUTE default_gym_equipment_stmt;
DEALLOCATE PREPARE default_gym_equipment_stmt;

-- Traducao de muscle_group (texto livre vindo de primary_group/secondary_group
-- de exercise_zepp_mappings, ou dos apelidos canonicos de normalization_service)
-- para os slugs aceitos pela biblioteca react-body-highlighter no frontend.
-- Mantida em tabela (nao hardcoded) para poder ser ajustada sem deploy.
CREATE TABLE IF NOT EXISTS muscle_group_body_map (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  muscle_group VARCHAR(120) NOT NULL,
  slugs VARCHAR(255) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_muscle_group_body_map_group (muscle_group)
) ENGINE=InnoDB;

-- Guardado: muscle_group (texto) so existe nesta tabela antes da migracao
-- para muscle_groups (mais abaixo neste arquivo) rodar. Em reexecucoes
-- idempotentes contra um banco ja migrado, a coluna nao existe mais e este
-- seed fica obsoleto (o backfill de muscle_groups + muscle_group_body_map.
-- muscle_group_id ja cobre os mesmos dados).
SET @mgbm_seed_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_group_body_map' AND COLUMN_NAME = 'muscle_group'
);
SET @mgbm_seed_ddl = IF(@mgbm_seed_col_exists > 0,
  'INSERT IGNORE INTO muscle_group_body_map (muscle_group, slugs) VALUES
    (''Tríceps'', ''triceps''),
    (''Bíceps'', ''biceps''),
    (''Ombros'', ''front-deltoids,back-deltoids''),
    (''Costas'', ''upper-back''),
    (''Lombar'', ''lower-back''),
    (''Peito'', ''chest''),
    (''Peitoral'', ''chest''),
    (''Coxas'', ''quadriceps,hamstring''),
    (''Quadríceps'', ''quadriceps''),
    (''Frontais coxas'', ''quadriceps''),
    (''Posterior de coxa'', ''hamstring''),
    (''Posteriores coxas'', ''hamstring''),
    (''Glúteos'', ''gluteal''),
    (''Glúteos/Quadril'', ''gluteal,abductors''),
    (''Abdutores Quadril'', ''abductors''),
    (''Adutores Quadril'', ''adductor''),
    (''Panturrilhas'', ''calves''),
    (''Core'', ''abs,obliques''),
    (''Abdômen'', ''abs''),
    (''Antebraços'', ''forearm'')',
  'SELECT 1'
);
PREPARE mgbm_seed_stmt FROM @mgbm_seed_ddl;
EXECUTE mgbm_seed_stmt;
DEALLOCATE PREPARE mgbm_seed_stmt;

-- ALTER tardio: workout_plans.gym_id / workout_plan_exercises.equipment_name,
-- para o recurso de plano de treino gerado via IA (pagina /planos-treino).
SET @wp_gym_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND COLUMN_NAME = 'gym_id'
);
SET @wp_gym_ddl = IF(@wp_gym_col_exists = 0,
  'ALTER TABLE workout_plans ADD COLUMN gym_id BIGINT NULL AFTER plan_date, ADD KEY idx_workout_plans_gym_id (gym_id), ADD CONSTRAINT fk_workout_plans_gym FOREIGN KEY (gym_id) REFERENCES gyms(id)',
  'SELECT 1'
);
PREPARE wp_gym_stmt FROM @wp_gym_ddl; EXECUTE wp_gym_stmt; DEALLOCATE PREPARE wp_gym_stmt;

-- ALTER tardio: workout_plans.plan_time, para poder informar o horario
-- pretendido do treino (alem da data) na pagina /planos-treino.
SET @wp_time_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND COLUMN_NAME = 'plan_time'
);
SET @wp_time_ddl = IF(@wp_time_col_exists = 0,
  'ALTER TABLE workout_plans ADD COLUMN plan_time TIME NULL AFTER plan_date',
  'SELECT 1'
);
PREPARE wp_time_stmt FROM @wp_time_ddl; EXECUTE wp_time_stmt; DEALLOCATE PREPARE wp_time_stmt;

-- ALTER tardio: workout_plans.split_day, letra do ciclo ABC (A/B/C) por
-- alcas musculares quando plan_type='abc'; NULL para os demais plan_type.
SET @wp_split_day_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND COLUMN_NAME = 'split_day'
);
SET @wp_split_day_ddl = IF(@wp_split_day_col_exists = 0,
  'ALTER TABLE workout_plans ADD COLUMN split_day VARCHAR(1) NULL AFTER plan_type',
  'SELECT 1'
);
PREPARE wp_split_day_stmt FROM @wp_split_day_ddl; EXECUTE wp_split_day_stmt; DEALLOCATE PREPARE wp_split_day_stmt;

SET @wpe_equip_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises' AND COLUMN_NAME = 'equipment_name'
);
SET @wpe_equip_ddl = IF(@wpe_equip_col_exists = 0,
  'ALTER TABLE workout_plan_exercises ADD COLUMN equipment_name VARCHAR(160) NULL AFTER muscle_secondary',
  'SELECT 1'
);
PREPARE wpe_equip_stmt FROM @wpe_equip_ddl; EXECUTE wpe_equip_stmt; DEALLOCATE PREPARE wpe_equip_stmt;

-- v7: suporte ao app Zepp OS (Amazfit T-Rex 3 Pro).
-- devices: tokens de pareamento por dispositivo (alternativa revogavel ao
-- API_TOKEN mestre; o token cru nunca e persistido, apenas o hash SHA-256).
CREATE TABLE IF NOT EXISTS devices (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  device_name VARCHAR(160) NOT NULL,
  platform VARCHAR(80) NOT NULL DEFAULT 'zepp',
  token_hash CHAR(64) NOT NULL,
  paired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NULL,
  revoked_at TIMESTAMP NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_devices_token_hash (token_hash),
  KEY idx_devices_active (is_active)
) ENGINE=InnoDB;

-- workout_plan_exercises.mapping_id / preferred_equipment_id: vinculo resolvido
-- com o catalogo canonico e com o equipamento da academia, para o relogio poder
-- chamar /gym-equipment/recommend (fluxo "Ocupado") e logar equipment_id.
SET @wpe_map_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises' AND COLUMN_NAME = 'mapping_id'
);
SET @wpe_map_ddl = IF(@wpe_map_col_exists = 0,
  'ALTER TABLE workout_plan_exercises ADD COLUMN mapping_id BIGINT NULL AFTER amazfit_name, ADD KEY idx_wpe_mapping (mapping_id), ADD CONSTRAINT fk_wpe_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_zepp_mappings(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE wpe_map_stmt FROM @wpe_map_ddl; EXECUTE wpe_map_stmt; DEALLOCATE PREPARE wpe_map_stmt;

SET @wpe_pref_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plan_exercises' AND COLUMN_NAME = 'preferred_equipment_id'
);
SET @wpe_pref_ddl = IF(@wpe_pref_col_exists = 0,
  'ALTER TABLE workout_plan_exercises ADD COLUMN preferred_equipment_id BIGINT NULL AFTER equipment_name, ADD KEY idx_wpe_pref_equipment (preferred_equipment_id), ADD CONSTRAINT fk_wpe_pref_equipment FOREIGN KEY (preferred_equipment_id) REFERENCES gym_equipment(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE wpe_pref_stmt FROM @wpe_pref_ddl; EXECUTE wpe_pref_stmt; DEALLOCATE PREPARE wpe_pref_stmt;

-- exercise_sets.hr_avg_bpm / hr_max_bpm: frequencia cardiaca capturada por
-- serie pelo relogio (nullable; importadores existentes ignoram).
SET @es_hr_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_sets' AND COLUMN_NAME = 'hr_avg_bpm'
);
SET @es_hr_ddl = IF(@es_hr_col_exists = 0,
  'ALTER TABLE exercise_sets ADD COLUMN hr_avg_bpm DECIMAL(6,2) NULL AFTER rest_seconds, ADD COLUMN hr_max_bpm DECIMAL(6,2) NULL AFTER hr_avg_bpm',
  'SELECT 1'
);
PREPARE es_hr_stmt FROM @es_hr_ddl; EXECUTE es_hr_stmt; DEALLOCATE PREPARE es_hr_stmt;

-- activities.session_status: NULL para importacoes legadas (tratadas como
-- completas); 'in_progress'/'done' para sessoes gravadas ao vivo pelo relogio.
SET @act_status_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities' AND COLUMN_NAME = 'session_status'
);
SET @act_status_ddl = IF(@act_status_col_exists = 0,
  'ALTER TABLE activities ADD COLUMN session_status VARCHAR(40) NULL AFTER conducted_by, ADD KEY idx_activities_session_status (session_status)',
  'SELECT 1'
);
PREPARE act_status_stmt FROM @act_status_ddl; EXECUTE act_status_stmt; DEALLOCATE PREPARE act_status_stmt;

-- exercise_zepp_mappings.secondary_participation: participacao explicita
-- (0-1) do grupo secundario para o modelo de recuperacao muscular v2
-- (fullbody-no-reps-v1). NULL = tratar como 0.30 (default) no codigo; so usar
-- 0.40 fixo quando esta coluna estiver setada para 0.40 naquele exercicio.
SET @zm_secpart_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'secondary_participation'
);
SET @zm_secpart_ddl = IF(@zm_secpart_col_exists = 0,
  'ALTER TABLE exercise_zepp_mappings ADD COLUMN secondary_participation DECIMAL(3,2) NULL AFTER secondary_group',
  'SELECT 1'
);
PREPARE zm_secpart_stmt FROM @zm_secpart_ddl; EXECUTE zm_secpart_stmt; DEALLOCATE PREPARE zm_secpart_stmt;

-- exercises.muscle_pain / joint_pain: sinais subjetivos opcionais informados
-- pelo usuario por exercicio (0-10 dor muscular residual; booleano dor
-- articular), usados pelo modelo de recuperacao v2 para classificar
-- NO_DIRECT_WORK sem depender so de tempo/dose.
SET @ex_musclepain_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercises' AND COLUMN_NAME = 'muscle_pain'
);
SET @ex_musclepain_ddl = IF(@ex_musclepain_col_exists = 0,
  'ALTER TABLE exercises ADD COLUMN muscle_pain TINYINT NULL AFTER notes, ADD COLUMN joint_pain TINYINT(1) NULL AFTER muscle_pain',
  'SELECT 1'
);
PREPARE ex_musclepain_stmt FROM @ex_musclepain_ddl; EXECUTE ex_musclepain_stmt; DEALLOCATE PREPARE ex_musclepain_stmt;

-- exercise_sets.muscle_pain / joint_pain: mesmos sinais subjetivos de
-- exercises.muscle_pain/joint_pain (dor muscular 0-10, dor articular
-- booleana), agora capturados por SERIE em vez de por exercicio (tela
-- /modo-treino). exercises.muscle_pain/joint_pain continuam existindo como
-- ROLLUP derivado (MAX/OR das series), recalculado automaticamente por
-- app.workout_sessions._refresh_exercise_aggregates a cada serie salva, para
-- nao exigir mudanca nenhuma na pipeline de recuperacao muscular
-- (volume_service.py/muscle_recovery_service.py) nem na geracao de ficha
-- (workout_plan_ai.py), que continuam lendo exclusivamente de exercises.
SET @es_pain_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_sets' AND COLUMN_NAME = 'muscle_pain'
);
SET @es_pain_ddl = IF(@es_pain_col_exists = 0,
  'ALTER TABLE exercise_sets ADD COLUMN muscle_pain TINYINT NULL AFTER hr_max_bpm, ADD COLUMN joint_pain TINYINT(1) NULL AFTER muscle_pain',
  'SELECT 1'
);
PREPARE es_pain_stmt FROM @es_pain_ddl; EXECUTE es_pain_stmt; DEALLOCATE PREPARE es_pain_stmt;

-- muscle_recovery_coefficients: coeficiente pessoal (0.90-1.10) por
-- grupamento muscular, usado para ajustar calculate_recovery_hours() do
-- modelo v2 (fullbody-no-reps-v1). Ajustado por evidencia acumulada de
-- varias sessoes, nunca por uma observacao isolada (ver
-- app/intelligence/muscle_recovery_service.py).
CREATE TABLE IF NOT EXISTS muscle_recovery_coefficients (
  muscle_group VARCHAR(120) NOT NULL PRIMARY KEY,
  coefficient DECIMAL(3,2) NOT NULL DEFAULT 1.00,
  valid_observations INT NOT NULL DEFAULT 0,
  confidence VARCHAR(20) NOT NULL DEFAULT 'LOW',
  last_adjusted_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- muscle_groups: tabela canonica de grupo muscular, substituindo o texto
-- livre repetido em exercise_zepp_mappings.primary_group/secondary_group e
-- em muscle_group_body_map.muscle_group. A "canonicalizacao" ate aqui era
-- feita apenas por um dicionario Python hardcoded
-- (NormalizationService.MUSCLE_GROUP_ALIASES), o que ja permitiu
-- inconsistencias como secondary_group="Ombros, Triceps" (multiplos nomes
-- colados numa unica string), que confundia a geracao de ficha de treino.
-- primary_muscles/secondary_muscles ficam NULL no backfill (o usuario
-- preenche depois pela UI de cadastro de grupos musculares) - nao inventar
-- dado aqui.
CREATE TABLE IF NOT EXISTS muscle_groups (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  primary_muscles VARCHAR(255) NULL,
  secondary_muscles VARCHAR(255) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Backfill idempotente: uniao de todos os nomes distintos ja usados como
-- grupo muscular em qualquer lugar do schema. UNIQUE KEY(name) dedupe.
-- Guardado por coluna: primary_group/secondary_group/muscle_group (texto)
-- so existem antes desta migracao rodar; em reexecucoes contra um banco ja
-- migrado essas colunas nao existem mais e o backfill fica sem fonte (nao
-- ha nada novo a inserir, os ids ja foram populados na primeira execucao).
SET @zm_seed_primary_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'primary_group'
);
SET @mg_backfill_primary_ddl = IF(@zm_seed_primary_group_col_exists > 0,
  'INSERT IGNORE INTO muscle_groups (name)
   SELECT DISTINCT primary_group FROM exercise_zepp_mappings
   WHERE primary_group IS NOT NULL AND primary_group <> ''''',
  'SELECT 1'
);
PREPARE mg_backfill_primary_stmt FROM @mg_backfill_primary_ddl;
EXECUTE mg_backfill_primary_stmt;
DEALLOCATE PREPARE mg_backfill_primary_stmt;

SET @zm_seed_secondary_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'secondary_group'
);
SET @mg_backfill_secondary_ddl = IF(@zm_seed_secondary_group_col_exists > 0,
  'INSERT IGNORE INTO muscle_groups (name)
   SELECT DISTINCT secondary_group FROM exercise_zepp_mappings
   WHERE secondary_group IS NOT NULL AND secondary_group <> ''''',
  'SELECT 1'
);
PREPARE mg_backfill_secondary_stmt FROM @mg_backfill_secondary_ddl;
EXECUTE mg_backfill_secondary_stmt;
DEALLOCATE PREPARE mg_backfill_secondary_stmt;

SET @mgbm_seed_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_group_body_map' AND COLUMN_NAME = 'muscle_group'
);
SET @mg_backfill_bodymap_ddl = IF(@mgbm_seed_group_col_exists > 0,
  'INSERT IGNORE INTO muscle_groups (name)
   SELECT DISTINCT muscle_group FROM muscle_group_body_map
   WHERE muscle_group IS NOT NULL AND muscle_group <> ''''',
  'SELECT 1'
);
PREPARE mg_backfill_bodymap_stmt FROM @mg_backfill_bodymap_ddl;
EXECUTE mg_backfill_bodymap_stmt;
DEALLOCATE PREPARE mg_backfill_bodymap_stmt;

-- exercise_zepp_mappings: primary_group_id/secondary_group_id (FK) substituem
-- primary_group/secondary_group (texto livre) e primary_muscles/
-- secondary_muscles (que passam a viver em muscle_groups, um valor por
-- grupo em vez de repetido por exercicio).
SET @zm_primary_group_id_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'primary_group_id'
);
SET @zm_primary_group_id_ddl = IF(@zm_primary_group_id_exists = 0,
  'ALTER TABLE exercise_zepp_mappings ADD COLUMN primary_group_id BIGINT NULL AFTER primary_group, ADD KEY idx_exercise_zepp_mappings_primary_group_id (primary_group_id), ADD CONSTRAINT fk_zm_primary_group FOREIGN KEY (primary_group_id) REFERENCES muscle_groups(id)',
  'SELECT 1'
);
PREPARE zm_primary_group_id_stmt FROM @zm_primary_group_id_ddl; EXECUTE zm_primary_group_id_stmt; DEALLOCATE PREPARE zm_primary_group_id_stmt;

SET @zm_secondary_group_id_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'secondary_group_id'
);
SET @zm_secondary_group_id_ddl = IF(@zm_secondary_group_id_exists = 0,
  'ALTER TABLE exercise_zepp_mappings ADD COLUMN secondary_group_id BIGINT NULL AFTER secondary_group, ADD KEY idx_exercise_zepp_mappings_secondary_group_id (secondary_group_id), ADD CONSTRAINT fk_zm_secondary_group FOREIGN KEY (secondary_group_id) REFERENCES muscle_groups(id)',
  'SELECT 1'
);
PREPARE zm_secondary_group_id_stmt FROM @zm_secondary_group_id_ddl; EXECUTE zm_secondary_group_id_stmt; DEALLOCATE PREPARE zm_secondary_group_id_stmt;

-- Backfill dos ids a partir do texto ainda presente nas colunas antigas
-- (idempotente: se primary_group ja foi removida em execucao anterior, os
-- UPDATEs abaixo simplesmente nao encontram a coluna e sao pulados via guard).
SET @zm_primary_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'primary_group'
);
SET @zm_backfill_primary_ddl = IF(@zm_primary_group_col_exists > 0,
  'UPDATE exercise_zepp_mappings zm JOIN muscle_groups mg ON mg.name = zm.primary_group SET zm.primary_group_id = mg.id WHERE zm.primary_group_id IS NULL',
  'SELECT 1'
);
PREPARE zm_backfill_primary_stmt FROM @zm_backfill_primary_ddl; EXECUTE zm_backfill_primary_stmt; DEALLOCATE PREPARE zm_backfill_primary_stmt;

SET @zm_secondary_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'secondary_group'
);
SET @zm_backfill_secondary_ddl = IF(@zm_secondary_group_col_exists > 0,
  'UPDATE exercise_zepp_mappings zm JOIN muscle_groups mg ON mg.name = zm.secondary_group SET zm.secondary_group_id = mg.id WHERE zm.secondary_group_id IS NULL',
  'SELECT 1'
);
PREPARE zm_backfill_secondary_stmt FROM @zm_backfill_secondary_ddl; EXECUTE zm_backfill_secondary_stmt; DEALLOCATE PREPARE zm_backfill_secondary_stmt;

-- Remove o FULLTEXT antigo (incluia primary_muscles/secondary_muscles, que
-- deixam de existir nesta tabela) e recria sem essas 2 colunas. Idempotente:
-- so dropa se o indice ainda existir com a definicao antiga (detectado pela
-- existencia da coluna primary_muscles, que so some depois do DROP COLUMN
-- abaixo).
SET @zm_primary_muscles_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_zepp_mappings' AND COLUMN_NAME = 'primary_muscles'
);
SET @zm_ft_drop_ddl = IF(@zm_primary_muscles_col_exists > 0,
  'ALTER TABLE exercise_zepp_mappings DROP INDEX ft_exercise_zepp_mappings_search',
  'SELECT 1'
);
PREPARE zm_ft_drop_stmt FROM @zm_ft_drop_ddl; EXECUTE zm_ft_drop_stmt; DEALLOCATE PREPARE zm_ft_drop_stmt;

SET @zm_ft_recreate_ddl = IF(@zm_primary_muscles_col_exists > 0,
  'ALTER TABLE exercise_zepp_mappings ADD FULLTEXT KEY ft_exercise_zepp_mappings_search (exercise_name, amazfit_name, keywords, description)',
  'SELECT 1'
);
PREPARE zm_ft_recreate_stmt FROM @zm_ft_recreate_ddl; EXECUTE zm_ft_recreate_stmt; DEALLOCATE PREPARE zm_ft_recreate_stmt;

-- Remove as colunas de texto livre agora que os ids estao populados.
SET @zm_drop_old_cols_ddl = IF(@zm_primary_group_col_exists > 0,
  'ALTER TABLE exercise_zepp_mappings DROP COLUMN primary_group, DROP COLUMN secondary_group, DROP COLUMN primary_muscles, DROP COLUMN secondary_muscles',
  'SELECT 1'
);
PREPARE zm_drop_old_cols_stmt FROM @zm_drop_old_cols_ddl; EXECUTE zm_drop_old_cols_stmt; DEALLOCATE PREPARE zm_drop_old_cols_stmt;

-- muscle_group_body_map: muscle_group_id (FK) substitui muscle_group (texto).
SET @mgbm_group_id_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_group_body_map' AND COLUMN_NAME = 'muscle_group_id'
);
SET @mgbm_group_id_ddl = IF(@mgbm_group_id_exists = 0,
  'ALTER TABLE muscle_group_body_map ADD COLUMN muscle_group_id BIGINT NULL AFTER muscle_group, ADD CONSTRAINT fk_mgbm_muscle_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id)',
  'SELECT 1'
);
PREPARE mgbm_group_id_stmt FROM @mgbm_group_id_ddl; EXECUTE mgbm_group_id_stmt; DEALLOCATE PREPARE mgbm_group_id_stmt;

SET @mgbm_muscle_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_group_body_map' AND COLUMN_NAME = 'muscle_group'
);
SET @mgbm_backfill_ddl = IF(@mgbm_muscle_group_col_exists > 0,
  'UPDATE muscle_group_body_map mgbm JOIN muscle_groups mg ON mg.name = mgbm.muscle_group SET mgbm.muscle_group_id = mg.id WHERE mgbm.muscle_group_id IS NULL',
  'SELECT 1'
);
PREPARE mgbm_backfill_stmt FROM @mgbm_backfill_ddl; EXECUTE mgbm_backfill_stmt; DEALLOCATE PREPARE mgbm_backfill_stmt;

-- muscle_group_id vira NOT NULL apos o backfill (so quando ainda estiver
-- NULLABLE, para nao repetir o ALTER a cada boot).
SET @mgbm_group_id_nullable = (
  SELECT IS_NULLABLE FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_group_body_map' AND COLUMN_NAME = 'muscle_group_id'
);
SET @mgbm_not_null_ddl = IF(@mgbm_group_id_nullable = 'YES',
  'ALTER TABLE muscle_group_body_map MODIFY COLUMN muscle_group_id BIGINT NOT NULL',
  'SELECT 1'
);
PREPARE mgbm_not_null_stmt FROM @mgbm_not_null_ddl; EXECUTE mgbm_not_null_stmt; DEALLOCATE PREPARE mgbm_not_null_stmt;

-- UNIQUE KEY passa de muscle_group (texto) para muscle_group_id (FK), e a
-- coluna de texto e removida (slugs continua existindo).
SET @mgbm_unique_ddl = IF(@mgbm_muscle_group_col_exists > 0,
  'ALTER TABLE muscle_group_body_map DROP KEY uq_muscle_group_body_map_group, ADD UNIQUE KEY uq_muscle_group_body_map_group_id (muscle_group_id), DROP COLUMN muscle_group',
  'SELECT 1'
);
PREPARE mgbm_unique_stmt FROM @mgbm_unique_ddl; EXECUTE mgbm_unique_stmt; DEALLOCATE PREPARE mgbm_unique_stmt;

-- muscle_recovery_coefficients: mesma fragilidade de texto livre (tabela
-- criada na sessao anterior, PR #17) - troca a PK de muscle_group (texto)
-- para muscle_group_id (FK).
SET @mrc_group_id_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_recovery_coefficients' AND COLUMN_NAME = 'muscle_group_id'
);
SET @mrc_group_id_ddl = IF(@mrc_group_id_exists = 0,
  'ALTER TABLE muscle_recovery_coefficients ADD COLUMN muscle_group_id BIGINT NULL AFTER muscle_group',
  'SELECT 1'
);
PREPARE mrc_group_id_stmt FROM @mrc_group_id_ddl; EXECUTE mrc_group_id_stmt; DEALLOCATE PREPARE mrc_group_id_stmt;

SET @mrc_muscle_group_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_recovery_coefficients' AND COLUMN_NAME = 'muscle_group'
);
SET @mrc_backfill_ddl = IF(@mrc_muscle_group_col_exists > 0,
  'UPDATE muscle_recovery_coefficients mrc JOIN muscle_groups mg ON mg.name = mrc.muscle_group SET mrc.muscle_group_id = mg.id WHERE mrc.muscle_group_id IS NULL',
  'SELECT 1'
);
PREPARE mrc_backfill_stmt FROM @mrc_backfill_ddl; EXECUTE mrc_backfill_stmt; DEALLOCATE PREPARE mrc_backfill_stmt;

-- Troca a PK: dropa a PK antiga (muscle_group texto), remove a coluna e
-- promove muscle_group_id a PRIMARY KEY. So roda enquanto a coluna de texto
-- antiga (e portanto a PK antiga) ainda existir.
SET @mrc_pk_swap_ddl = IF(@mrc_muscle_group_col_exists > 0,
  'ALTER TABLE muscle_recovery_coefficients DROP PRIMARY KEY, DROP COLUMN muscle_group, MODIFY COLUMN muscle_group_id BIGINT NOT NULL, ADD PRIMARY KEY (muscle_group_id), ADD CONSTRAINT fk_mrc_muscle_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id)',
  'SELECT 1'
);
PREPARE mrc_pk_swap_stmt FROM @mrc_pk_swap_ddl; EXECUTE mrc_pk_swap_stmt; DEALLOCATE PREPARE mrc_pk_swap_stmt;

-- Limpeza pos-migracao 11: o backfill de muscle_groups uniu os nomes
-- padronizados manualmente pelo usuario em exercise_zepp_mappings
-- (primary_group/secondary_group, ja migrados p/ FK) com a seed antiga de
-- muscle_group_body_map (nomes legados/com encoding corrompido, de antes da
-- padronizacao). Isso deixou 2 problemas:
--   a) grupos orfaos em muscle_groups (0 uso em exercise_zepp_mappings) -
--      nomes com mojibake (dupla-codificacao UTF-8, ex. 'AbdÃ´men') e nomes
--      legados que o usuario renomeou (ex. 'Costas', 'Coxas', 'Adutores
--      Quadril');
--   b) grupos EM USO (primary_group_id/secondary_group_id) sem entrada em
--      muscle_group_body_map, porque o nome padronizado pelo usuario nao bate
--      exatamente com nenhum nome da seed antiga - o mapa corporal do
--      frontend nao destaca esses musculos.
-- Primeiro preenchemos (b) reaproveitando os slugs ja usados pelo grupo
-- legado equivalente, depois removemos (a). A ordem importa: preencher antes
-- de limpar evita que o DELETE por "sem uso" tire uma linha de
-- muscle_group_body_map que ainda precisamos criar com nome novo.

-- 'Costas médio' (meio das costas) reaproveita o slug do extinto 'Costas'.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'upper-back' FROM muscle_groups WHERE name = 'Costas médio';

-- 'Costas superior' (trapezio/parte alta das costas) ganha slug proprio,
-- distinto de 'Costas médio', ja que o vocabulario do react-body-highlighter
-- tem um slug dedicado para trapezio.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'trapezius' FROM muscle_groups WHERE name = 'Costas superior';

-- 'Antebraço' (singular) reaproveita o slug ja usado por 'Antebraços'/
-- 'AntebraÃ§os'.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'forearm' FROM muscle_groups WHERE name = 'Antebraço';

-- 'Anterior de coxas' (quadriceps) reaproveita o slug ja usado por
-- 'Frontais coxas'.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'quadriceps' FROM muscle_groups WHERE name = 'Anterior de coxas';

-- 'Adutores do quadril' reaproveita o slug ja usado por 'Adutores Quadril'.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'adductor' FROM muscle_groups WHERE name = 'Adutores do quadril';

-- 'Posterior de coxas' reaproveita o slug ja usado por 'Posterior de coxa'/
-- 'Posteriores coxas'.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'hamstring' FROM muscle_groups WHERE name = 'Posterior de coxas';

-- Remove entradas de muscle_group_body_map cujo muscle_group_id ficou orfao
-- (sem uso em exercise_zepp_mappings) - precisa rodar antes do DELETE em
-- muscle_groups por causa da FK. Idempotente: so afeta linhas que continuam
-- sem uso.
DELETE mgbm FROM muscle_group_body_map mgbm
WHERE mgbm.muscle_group_id NOT IN (
  SELECT DISTINCT primary_group_id FROM exercise_zepp_mappings WHERE primary_group_id IS NOT NULL
  UNION
  SELECT DISTINCT secondary_group_id FROM exercise_zepp_mappings WHERE secondary_group_id IS NOT NULL
);

-- Remove de muscle_groups os grupos sem nenhum uso real (nem em
-- exercise_zepp_mappings, nem em muscle_group_body_map). Idempotente: rodar
-- de novo nao tem efeito, ja que so afeta linhas que continuam orfas.
DELETE FROM muscle_groups
WHERE id NOT IN (
  SELECT DISTINCT primary_group_id FROM exercise_zepp_mappings WHERE primary_group_id IS NOT NULL
  UNION
  SELECT DISTINCT secondary_group_id FROM exercise_zepp_mappings WHERE secondary_group_id IS NOT NULL
)
AND id NOT IN (
  SELECT muscle_group_id FROM muscle_group_body_map
);

-- Grupos musculares novos criados pelo usuario apos a limpeza da migracao 12
-- (divisao de Antebraço/Peitoral/Panturrilha em partes mais especificas na
-- tela de mapeamentos). O id do grupo "irmao" que ja existia antes da divisao
-- foi preservado (renomear nao quebra FK); so os 3 grupos NOVOS abaixo
-- ficaram sem entrada em muscle_group_body_map, entao o mapa corporal do
-- frontend nao destaca nada para eles. O vocabulario do
-- react-body-highlighter (app/muscle_map.py) nao tem slugs separados para
-- anterior/posterior de antebraço/panturrilha nem para superior/medio-inferior
-- de peitoral, entao reaproveitamos o mesmo slug do irmao (destaque visual
-- da mesma regiao e o esperado). Idempotente via INSERT IGNORE + resolucao
-- do id pelo nome no momento da execucao.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'forearm' FROM muscle_groups WHERE name = 'Antebraço posterior';

INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'calves' FROM muscle_groups WHERE name = 'Panturrilha posterior';

INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'chest' FROM muscle_groups WHERE name = 'Peitoral médio/inferior';

-- Grupos musculares mais novos (Core, Flexores do quadril, Manguito rotador,
-- Panturrilha anterior) criados apos o seed original de
-- muscle_group_body_map e nunca adicionados a tabela de traducao -> o mapa
-- corporal do frontend (MuscleBodyMap.tsx) fica cinza para qualquer
-- exercicio cujo grupo primario/secundario seja um desses (ex.: "Rotacao
-- Externa do Ombro na Polia", primario = Manguito rotador). O
-- react-body-highlighter nao tem slug dedicado para manguito rotador nem
-- para flexor de quadril, entao reaproveitamos o slug anatomico mais
-- proximo disponivel (mesmo criterio da migracao 13 acima). Idempotente via
-- INSERT IGNORE + resolucao do id pelo nome no momento da execucao.
INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'front-deltoids,back-deltoids' FROM muscle_groups WHERE name = 'Manguito rotador';

INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'abs,obliques' FROM muscle_groups WHERE name = 'Core';

INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'calves' FROM muscle_groups WHERE name = 'Panturrilha anterior';

INSERT IGNORE INTO muscle_group_body_map (muscle_group_id, slugs)
SELECT id, 'quadriceps' FROM muscle_groups WHERE name = 'Flexores do quadril';

-- exercise_muscle_contributions: pesos de contribuicao configuraveis por
-- par exercicio x grupamento muscular (PRIMARY/SECONDARY/STABILIZER), fase 1
-- de uma reforma do calculo de volume por grupamento. Hoje o volume conta
-- cada serie INTEIRA tanto para o grupo primario quanto para o secundario
-- (sem peso), distorcendo relatorios. Esta tabela separa participacao direta
-- (serie completa) de participacao indireta (estimulo/fadiga parciais),
-- permitindo que fases seguintes (camada Python) agreguem volume ponderado
-- em vez de contagem cheia por grupo. stimulus_weight e fatigue_weight sao
-- decorrelacionados (um exercicio pode estimular pouco mas fatigar bastante
-- um grupo secundario, ou vice-versa). NUNCA altera dado historico de
-- treino: exercise_sets/exercises/activities nao sao tocados por esta
-- tabela nem pelo backfill abaixo.
CREATE TABLE IF NOT EXISTS exercise_muscle_contributions (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  mapping_id BIGINT NOT NULL,
  muscle_group_id BIGINT NOT NULL,
  role VARCHAR(20) NOT NULL,
  stimulus_weight DECIMAL(3,2) NOT NULL,
  fatigue_weight DECIMAL(3,2) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_emc_mapping_group (mapping_id, muscle_group_id),
  KEY idx_emc_muscle_group (muscle_group_id),
  CONSTRAINT fk_emc_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_zepp_mappings(id) ON DELETE CASCADE,
  CONSTRAINT fk_emc_muscle_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id)
) ENGINE=InnoDB;

-- Backfill idempotente a partir de exercise_zepp_mappings.primary_group_id/
-- secondary_group_id/secondary_participation (que permanecem como estao e
-- continuam servindo de fallback para o codigo Python existente, nao
-- alterado nesta fase). INSERT IGNORE (nao ON DUPLICATE KEY UPDATE): a
-- UNIQUE KEY garante que reexecucoes nao dupliquem, e nao deve sobrescrever
-- eventuais edicoes manuais feitas depois via API futura.
INSERT IGNORE INTO exercise_muscle_contributions
  (mapping_id, muscle_group_id, role, stimulus_weight, fatigue_weight)
SELECT zm.id, zm.primary_group_id, 'PRIMARY', 1.00, 1.00
FROM exercise_zepp_mappings zm
WHERE zm.primary_group_id IS NOT NULL;

INSERT IGNORE INTO exercise_muscle_contributions
  (mapping_id, muscle_group_id, role, stimulus_weight, fatigue_weight)
SELECT
  zm.id,
  zm.secondary_group_id,
  'SECONDARY',
  COALESCE(zm.secondary_participation, 0.50),
  COALESCE(zm.secondary_participation, 0.50)
FROM exercise_zepp_mappings zm
WHERE zm.secondary_group_id IS NOT NULL
  AND (zm.primary_group_id IS NULL OR zm.secondary_group_id <> zm.primary_group_id);

-- exercise_aliases: apelidos/textos alternativos que resolvem para um
-- exercicio canonico de exercise_zepp_mappings (N:1 — varios apelidos podem
-- apontar para o mesmo exercicio, cada apelido resolve para exatamente um).
-- Motivacao: o ABC_TEMPLATE fixo (abc_workout_template.py) foi escrito
-- parafraseando o RAG TREINO_ABC_PERFEITO.md sem nunca ter sido cruzado com
-- os nomes reais do catalogo, entao a reconciliacao com o equipamento da
-- academia (reconcile_with_gym_catalog) quase sempre caia no fallback
-- generico por nao achar nome exato. Mecanismo generico e reutilizavel para
-- futuras entradas do RAG, nao um fix pontual so para o ABC.
CREATE TABLE IF NOT EXISTS exercise_aliases (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  mapping_id BIGINT NOT NULL,
  alias VARCHAR(255) NOT NULL,
  source VARCHAR(160) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_exercise_aliases_alias (alias),
  KEY idx_exercise_aliases_mapping (mapping_id),
  CONSTRAINT fk_exercise_aliases_mapping FOREIGN KEY (mapping_id) REFERENCES exercise_zepp_mappings(id) ON DELETE CASCADE
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- Seed: 2 exercicios canonicos novos, lacunas reais do catalogo (nenhum
-- movimento livre/composto equivalente existia) identificadas ao cruzar os
-- 21 exercicios do ABC_TEMPLATE com os mapeamentos reais.
INSERT IGNORE INTO exercise_zepp_mappings
  (exercise_name, amazfit_name, primary_group_id, mapping_status, source)
SELECT 'Agachamento Livre', 'Squat (aproximação)', id, 'confirmed', 'TREINO_ABC_PERFEITO.md'
FROM muscle_groups WHERE name = 'Coxas anterior';

INSERT IGNORE INTO exercise_zepp_mappings
  (exercise_name, amazfit_name, primary_group_id, mapping_status, source)
SELECT 'Rosca Direta Barra reta ou W', 'Barbell Curl (aproximação)', id, 'confirmed', 'TREINO_ABC_PERFEITO.md'
FROM muscle_groups WHERE name = 'Bíceps';

-- Seed: 19 apelidos ligando o texto original do ABC_TEMPLATE ao exercicio
-- canonico real correspondente (confirmado via query contra o catalogo real,
-- nao apenas pela prosa do RAG). "Tríceps Testa na Polia" e uma aproximacao
-- (angulo diferente do movimento de testa tradicional, mas mesma familia de
-- equipamento/grupo — nao ha exercicio de testa dedicado no catalogo).
INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Pulley Frente', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Front Pull Down / Lat Pulldown';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Remada Máquina Fechada', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Hammer Strength Row';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Remada Pronada Aberta', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Remada baixa articulada com apoio peitoral';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Crucifixo Inverso Máquina', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Crucifixo invertido no voador';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Rosca Scott Máquina', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Rosca Scott';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Banco Romano / Extensão Lombar', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Cadeira romana / Extensão lombar';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Leg Press 45º', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Leg Press 45°';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Cadeira Extensora', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Leg Extension (cadeira extensora)';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Mesa Flexora', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Mesa flexora';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Cadeira Flexora', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Seated Leg Curl (cadeira flexora)';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Panturrilha Sentada', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Elevação de panturrilha sentada';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Panturrilha em Pé', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Elevação de panturrilha em pé';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Supino Máquina', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Supino reto na máquina (Dual Press)';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Dumbbell Press Inclinado 30º', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Supino inclinado com halteres (30°)';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Crucifixo Máquina / Peck Deck', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Voador (Pec Deck)';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Elevação Diagonal / Plano Escapular', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Lateral Raise Machine';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Tríceps Pulley / Polia Alta', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Tríceps na polia alta com barra V';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Tríceps Testa na Polia', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Tríceps na polia alta com corda';

INSERT IGNORE INTO exercise_aliases (mapping_id, alias, source)
SELECT id, 'Abdominal na Máquina', 'TREINO_ABC_PERFEITO.md' FROM exercise_zepp_mappings WHERE exercise_name = 'Abdômen máquina';

-- ===========================================================================
-- Multiusuario (Parte 1): fundacao de identidade/autenticacao/sessao.
--
-- users/user_sessions seguem o mesmo padrao ja usado em `devices`
-- (token opaco gerado com secrets.token_urlsafe, apenas o hash SHA-256 e
-- persistido em token_hash). O INSERT do usuario admin seed ('doug') com
-- senha temporaria aleatoria NAO fica aqui (SQL puro nao roda bcrypt) - fica
-- em app.auth.ensure_admin_seed(), chamado no startup do servidor (ver
-- app/main.py). Ver comentario completo em app/auth.py.
--
-- As colunas user_id/owner_user_id abaixo ficam NULL nesta entrega, sem
-- backfill nem NOT NULL - toda escrita hoje continua acessivel via token
-- mestre, que security.resolve_identity resolve para o usuario admin, entao
-- nada quebra com a coluna ainda NULL. O backfill + NOT NULL fica para uma
-- proxima entrega, tabela por tabela.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS users (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(80) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  full_name VARCHAR(160) NULL,
  email VARCHAR(160) NULL,
  photo_path VARCHAR(255) NULL,
  birth_date DATE NULL,
  sex VARCHAR(20) NULL,
  role ENUM('admin','regular') NOT NULL DEFAULT 'regular',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  -- intervals_api_key_encrypted: chave do Intervals.icu do usuario,
  -- criptografada em repouso com Fernet (ver app/crypto_utils.py, chave
  -- simetrica em SECRET_ENCRYPTION_KEY). Gravada por app/users.py
  -- (create_user/update_my_profile/update_user) e descriptografada sob
  -- demanda por app/intervals_integration.py.
  intervals_athlete_id VARCHAR(80) NULL,
  intervals_api_key_encrypted VARCHAR(255) NULL,
  failed_login_attempts INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMP NULL,
  must_change_password TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_sessions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  token_hash CHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  last_seen_at TIMESTAMP NULL,
  revoked_at TIMESTAMP NULL,
  UNIQUE KEY uq_user_sessions_token_hash (token_hash),
  KEY idx_user_sessions_user (user_id),
  CONSTRAINT fk_user_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ALTER tardio: activities.user_id (dono do treino).
SET @activities_user_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities' AND COLUMN_NAME = 'user_id'
);
SET @activities_user_ddl = IF(@activities_user_col_exists = 0,
  'ALTER TABLE activities ADD COLUMN user_id BIGINT NULL AFTER id, ADD KEY idx_activities_user (user_id), ADD CONSTRAINT fk_activities_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE activities_user_stmt FROM @activities_user_ddl; EXECUTE activities_user_stmt; DEALLOCATE PREPARE activities_user_stmt;

-- ALTER tardio: gyms.owner_user_id (dono da academia cadastrada).
SET @gyms_owner_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gyms' AND COLUMN_NAME = 'owner_user_id'
);
SET @gyms_owner_ddl = IF(@gyms_owner_col_exists = 0,
  'ALTER TABLE gyms ADD COLUMN owner_user_id BIGINT NULL AFTER id, ADD KEY idx_gyms_owner (owner_user_id), ADD CONSTRAINT fk_gyms_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE gyms_owner_stmt FROM @gyms_owner_ddl; EXECUTE gyms_owner_stmt; DEALLOCATE PREPARE gyms_owner_stmt;

-- ALTER tardio: body_composition.user_id (dono do registro de bioimpedancia).
SET @bodycomp_user_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'body_composition' AND COLUMN_NAME = 'user_id'
);
SET @bodycomp_user_ddl = IF(@bodycomp_user_col_exists = 0,
  'ALTER TABLE body_composition ADD COLUMN user_id BIGINT NULL AFTER id, ADD KEY idx_body_composition_user (user_id), ADD CONSTRAINT fk_body_composition_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE bodycomp_user_stmt FROM @bodycomp_user_ddl; EXECUTE bodycomp_user_stmt; DEALLOCATE PREPARE bodycomp_user_stmt;

-- ALTER tardio: daily_biometrics.user_id (dono do registro de biometria diaria).
SET @dailybio_user_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'daily_biometrics' AND COLUMN_NAME = 'user_id'
);
SET @dailybio_user_ddl = IF(@dailybio_user_col_exists = 0,
  'ALTER TABLE daily_biometrics ADD COLUMN user_id BIGINT NULL AFTER id, ADD KEY idx_daily_biometrics_user (user_id), ADD CONSTRAINT fk_daily_biometrics_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE dailybio_user_stmt FROM @dailybio_user_ddl; EXECUTE dailybio_user_stmt; DEALLOCATE PREPARE dailybio_user_stmt;

-- ALTER tardio: workout_plans.user_id (dono do plano de treino).
SET @workoutplans_user_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND COLUMN_NAME = 'user_id'
);
SET @workoutplans_user_ddl = IF(@workoutplans_user_col_exists = 0,
  'ALTER TABLE workout_plans ADD COLUMN user_id BIGINT NULL AFTER id, ADD KEY idx_workout_plans_user (user_id), ADD CONSTRAINT fk_workout_plans_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE workoutplans_user_stmt FROM @workoutplans_user_ddl; EXECUTE workoutplans_user_stmt; DEALLOCATE PREPARE workoutplans_user_stmt;

-- Nota: sleep_metrics.user_id, manual_effort_metrics.user_id,
-- cardio_recommendations.user_id, devices.user_id, intervals_wellness.user_id
-- e intervals_activities.user_id sao adicionados nos respectivos
-- _ensure_schema() em Python (app/sleep_metrics.py, app/manual_effort_metrics.py,
-- app/cardio_recommendations.py, app/devices.py, app/intervals_integration.py),
-- pois essas tabelas ja sao criadas em Python, nao neste arquivo.
--
-- Parte 2 (integracao Intervals.icu por usuario): intervals_wellness.user_id
-- e intervals_activities.user_id deixam de ser NULLABLE. app/intervals_integration.py
-- ::_ensure_schema() faz backfill idempotente das linhas orfas (user_id NULL,
-- sincronizadas antes da Parte 1) para o usuario admin e promove a chave para
-- composta: intervals_wellness vira PRIMARY KEY (user_id, wellness_date) (era
-- so wellness_date) e intervals_activities vira UNIQUE KEY (user_id, external_id)
-- (era so external_id) - evita colisao de dados entre usuarios diferentes com
-- o mesmo dia de wellness ou o mesmo external_id do Intervals.icu.

-- ===========================================================================
-- Multiusuario (Parte 4): backfill final + promocao a NOT NULL das colunas
-- user_id/owner_user_id que a Parte 1 deixou NULLABLE sem backfill em
-- activities, gyms, body_composition, daily_biometrics e workout_plans.
-- Mesmo padrao defensivo ja usado em app/intervals_integration.py::_ensure_schema
-- para intervals_wellness/intervals_activities (Parte 2): so promove a coluna
-- para NOT NULL quando ja nao sobrar nenhuma linha com user_id/owner_user_id
-- NULL apos o backfill - se ainda nao existir nenhum usuario admin (banco
-- novo, seed ainda nao rodou), o backfill/promocao simplesmente nao roda
-- nesta execucao e tenta de novo na proxima (idempotente, nunca destrutivo).
-- ===========================================================================

SET @mu4_admin_id = (
  SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1
);

-- activities.user_id
UPDATE activities SET user_id = @mu4_admin_id WHERE user_id IS NULL AND @mu4_admin_id IS NOT NULL;
SET @mu4_activities_pending = (SELECT COUNT(*) FROM activities WHERE user_id IS NULL);

-- fk_activities_user foi criada na Parte 1 com ON DELETE SET NULL, o que o
-- MySQL rejeita ao promover user_id para NOT NULL logo abaixo ("Column
-- 'user_id' cannot be NOT NULL: needed in a foreign key constraint ...
-- SET NULL"). Recria a FK com ON DELETE RESTRICT antes da promocao -
-- consistente com o padrao do projeto de nunca fazer hard-delete de usuario
-- (ver app/users.py::deactivate_user), so ativa quando a FK ainda estiver
-- como SET NULL (idempotente).
-- DROP e ADD ficam em ALTER TABLE separados: o MySQL processa ADDs antes de
-- DROPs dentro de um unico ALTER TABLE, o que faz um DROP+ADD com o MESMO
-- nome de FK na mesma instrucao falhar com "Duplicate foreign key
-- constraint name" mesmo sendo logicamente uma substituicao.
SET @mu4_activities_fk_delete_rule = (
  SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'activities' AND CONSTRAINT_NAME = 'fk_activities_user'
);
SET @mu4_activities_fk_drop_ddl = IF(@mu4_activities_fk_delete_rule = 'SET NULL',
  'ALTER TABLE activities DROP FOREIGN KEY fk_activities_user',
  'SELECT 1'
);
PREPARE mu4_activities_fk_drop_stmt FROM @mu4_activities_fk_drop_ddl; EXECUTE mu4_activities_fk_drop_stmt; DEALLOCATE PREPARE mu4_activities_fk_drop_stmt;

SET @mu4_activities_fk_add_ddl = IF(@mu4_activities_fk_delete_rule = 'SET NULL',
  'ALTER TABLE activities ADD CONSTRAINT fk_activities_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT',
  'SELECT 1'
);
PREPARE mu4_activities_fk_add_stmt FROM @mu4_activities_fk_add_ddl; EXECUTE mu4_activities_fk_add_stmt; DEALLOCATE PREPARE mu4_activities_fk_add_stmt;

SET @mu4_activities_ddl = IF(@mu4_activities_pending = 0,
  'ALTER TABLE activities MODIFY COLUMN user_id BIGINT NOT NULL',
  'SELECT 1'
);
PREPARE mu4_activities_stmt FROM @mu4_activities_ddl; EXECUTE mu4_activities_stmt; DEALLOCATE PREPARE mu4_activities_stmt;

-- gyms.owner_user_id
UPDATE gyms SET owner_user_id = @mu4_admin_id WHERE owner_user_id IS NULL AND @mu4_admin_id IS NOT NULL;
SET @mu4_gyms_pending = (SELECT COUNT(*) FROM gyms WHERE owner_user_id IS NULL);

-- fk_gyms_owner: mesmo ajuste de ON DELETE SET NULL -> RESTRICT feito acima
-- para fk_activities_user, necessario antes da promocao a NOT NULL.
SET @mu4_gyms_fk_delete_rule = (
  SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'gyms' AND CONSTRAINT_NAME = 'fk_gyms_owner'
);
SET @mu4_gyms_fk_drop_ddl = IF(@mu4_gyms_fk_delete_rule = 'SET NULL',
  'ALTER TABLE gyms DROP FOREIGN KEY fk_gyms_owner',
  'SELECT 1'
);
PREPARE mu4_gyms_fk_drop_stmt FROM @mu4_gyms_fk_drop_ddl; EXECUTE mu4_gyms_fk_drop_stmt; DEALLOCATE PREPARE mu4_gyms_fk_drop_stmt;

SET @mu4_gyms_fk_add_ddl = IF(@mu4_gyms_fk_delete_rule = 'SET NULL',
  'ALTER TABLE gyms ADD CONSTRAINT fk_gyms_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE RESTRICT',
  'SELECT 1'
);
PREPARE mu4_gyms_fk_add_stmt FROM @mu4_gyms_fk_add_ddl; EXECUTE mu4_gyms_fk_add_stmt; DEALLOCATE PREPARE mu4_gyms_fk_add_stmt;

SET @mu4_gyms_ddl = IF(@mu4_gyms_pending = 0,
  'ALTER TABLE gyms MODIFY COLUMN owner_user_id BIGINT NOT NULL',
  'SELECT 1'
);
PREPARE mu4_gyms_stmt FROM @mu4_gyms_ddl; EXECUTE mu4_gyms_stmt; DEALLOCATE PREPARE mu4_gyms_stmt;

-- body_composition.user_id + troca da UNIQUE KEY (local_date, source) para
-- (user_id, local_date, source) - sem isso, dois usuarios registrando
-- bioimpedancia na mesma data/fonte colidiriam no INSERT ... ON DUPLICATE
-- KEY UPDATE de upsert_body_composition (app/services.py).
UPDATE body_composition SET user_id = @mu4_admin_id WHERE user_id IS NULL AND @mu4_admin_id IS NOT NULL;
SET @mu4_bodycomp_pending = (SELECT COUNT(*) FROM body_composition WHERE user_id IS NULL);

-- fk_body_composition_user: mesmo ajuste de ON DELETE SET NULL -> RESTRICT
-- feito acima, necessario antes da promocao a NOT NULL.
SET @mu4_bodycomp_fk_delete_rule = (
  SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'body_composition' AND CONSTRAINT_NAME = 'fk_body_composition_user'
);
SET @mu4_bodycomp_fk_drop_ddl = IF(@mu4_bodycomp_fk_delete_rule = 'SET NULL',
  'ALTER TABLE body_composition DROP FOREIGN KEY fk_body_composition_user',
  'SELECT 1'
);
PREPARE mu4_bodycomp_fk_drop_stmt FROM @mu4_bodycomp_fk_drop_ddl; EXECUTE mu4_bodycomp_fk_drop_stmt; DEALLOCATE PREPARE mu4_bodycomp_fk_drop_stmt;

SET @mu4_bodycomp_fk_add_ddl = IF(@mu4_bodycomp_fk_delete_rule = 'SET NULL',
  'ALTER TABLE body_composition ADD CONSTRAINT fk_body_composition_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT',
  'SELECT 1'
);
PREPARE mu4_bodycomp_fk_add_stmt FROM @mu4_bodycomp_fk_add_ddl; EXECUTE mu4_bodycomp_fk_add_stmt; DEALLOCATE PREPARE mu4_bodycomp_fk_add_stmt;

SET @mu4_bodycomp_ddl = IF(@mu4_bodycomp_pending = 0,
  'ALTER TABLE body_composition MODIFY COLUMN user_id BIGINT NOT NULL',
  'SELECT 1'
);
PREPARE mu4_bodycomp_stmt FROM @mu4_bodycomp_ddl; EXECUTE mu4_bodycomp_stmt; DEALLOCATE PREPARE mu4_bodycomp_stmt;

SET @mu4_bodycomp_composite_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'body_composition' AND INDEX_NAME = 'uq_body_composition_user_date_source'
);
SET @mu4_bodycomp_unique_ddl = IF(@mu4_bodycomp_pending = 0 AND @mu4_bodycomp_composite_exists = 0,
  'ALTER TABLE body_composition DROP KEY uq_body_composition_date_source, ADD UNIQUE KEY uq_body_composition_user_date_source (user_id, local_date, source)',
  'SELECT 1'
);
PREPARE mu4_bodycomp_unique_stmt FROM @mu4_bodycomp_unique_ddl; EXECUTE mu4_bodycomp_unique_stmt; DEALLOCATE PREPARE mu4_bodycomp_unique_stmt;

-- daily_biometrics.user_id (sem endpoint de escrita hoje - so backfill/NOT
-- NULL, sem trocar a UNIQUE KEY existente por falta de consumidor ativo).
SET @mu4_dailybio_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'daily_biometrics' AND COLUMN_NAME = 'user_id'
);
SET @mu4_dailybio_ddl = IF(@mu4_dailybio_col_exists = 0,
  'ALTER TABLE daily_biometrics ADD COLUMN user_id BIGINT NULL AFTER id, ADD KEY idx_daily_biometrics_user (user_id), ADD CONSTRAINT fk_daily_biometrics_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL',
  'SELECT 1'
);
PREPARE mu4_dailybio_add_stmt FROM @mu4_dailybio_ddl; EXECUTE mu4_dailybio_add_stmt; DEALLOCATE PREPARE mu4_dailybio_add_stmt;

UPDATE daily_biometrics SET user_id = @mu4_admin_id WHERE user_id IS NULL AND @mu4_admin_id IS NOT NULL;
SET @mu4_dailybio_pending = (SELECT COUNT(*) FROM daily_biometrics WHERE user_id IS NULL);

-- fk_daily_biometrics_user: mesmo ajuste de ON DELETE SET NULL -> RESTRICT
-- feito acima, necessario antes da promocao a NOT NULL.
SET @mu4_dailybio_fk_delete_rule = (
  SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'daily_biometrics' AND CONSTRAINT_NAME = 'fk_daily_biometrics_user'
);
SET @mu4_dailybio_fk_drop_ddl = IF(@mu4_dailybio_fk_delete_rule = 'SET NULL',
  'ALTER TABLE daily_biometrics DROP FOREIGN KEY fk_daily_biometrics_user',
  'SELECT 1'
);
PREPARE mu4_dailybio_fk_drop_stmt FROM @mu4_dailybio_fk_drop_ddl; EXECUTE mu4_dailybio_fk_drop_stmt; DEALLOCATE PREPARE mu4_dailybio_fk_drop_stmt;

SET @mu4_dailybio_fk_add_ddl = IF(@mu4_dailybio_fk_delete_rule = 'SET NULL',
  'ALTER TABLE daily_biometrics ADD CONSTRAINT fk_daily_biometrics_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT',
  'SELECT 1'
);
PREPARE mu4_dailybio_fk_add_stmt FROM @mu4_dailybio_fk_add_ddl; EXECUTE mu4_dailybio_fk_add_stmt; DEALLOCATE PREPARE mu4_dailybio_fk_add_stmt;

SET @mu4_dailybio_notnull_ddl = IF(@mu4_dailybio_pending = 0,
  'ALTER TABLE daily_biometrics MODIFY COLUMN user_id BIGINT NOT NULL',
  'SELECT 1'
);
PREPARE mu4_dailybio_notnull_stmt FROM @mu4_dailybio_notnull_ddl; EXECUTE mu4_dailybio_notnull_stmt; DEALLOCATE PREPARE mu4_dailybio_notnull_stmt;

-- workout_plans.user_id
UPDATE workout_plans SET user_id = @mu4_admin_id WHERE user_id IS NULL AND @mu4_admin_id IS NOT NULL;
SET @mu4_workoutplans_pending = (SELECT COUNT(*) FROM workout_plans WHERE user_id IS NULL);

-- fk_workout_plans_user: mesmo ajuste de ON DELETE SET NULL -> RESTRICT
-- feito acima, necessario antes da promocao a NOT NULL.
SET @mu4_workoutplans_fk_delete_rule = (
  SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'workout_plans' AND CONSTRAINT_NAME = 'fk_workout_plans_user'
);
SET @mu4_workoutplans_fk_drop_ddl = IF(@mu4_workoutplans_fk_delete_rule = 'SET NULL',
  'ALTER TABLE workout_plans DROP FOREIGN KEY fk_workout_plans_user',
  'SELECT 1'
);
PREPARE mu4_workoutplans_fk_drop_stmt FROM @mu4_workoutplans_fk_drop_ddl; EXECUTE mu4_workoutplans_fk_drop_stmt; DEALLOCATE PREPARE mu4_workoutplans_fk_drop_stmt;

SET @mu4_workoutplans_fk_add_ddl = IF(@mu4_workoutplans_fk_delete_rule = 'SET NULL',
  'ALTER TABLE workout_plans ADD CONSTRAINT fk_workout_plans_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT',
  'SELECT 1'
);
PREPARE mu4_workoutplans_fk_add_stmt FROM @mu4_workoutplans_fk_add_ddl; EXECUTE mu4_workoutplans_fk_add_stmt; DEALLOCATE PREPARE mu4_workoutplans_fk_add_stmt;

SET @mu4_workoutplans_ddl = IF(@mu4_workoutplans_pending = 0,
  'ALTER TABLE workout_plans MODIFY COLUMN user_id BIGINT NOT NULL',
  'SELECT 1'
);
PREPARE mu4_workoutplans_stmt FROM @mu4_workoutplans_ddl; EXECUTE mu4_workoutplans_stmt; DEALLOCATE PREPARE mu4_workoutplans_stmt;

-- muscle_recovery_coefficients: chave primaria passa de muscle_group_id
-- (singleton global) para composta (user_id, muscle_group_id) - o
-- coeficiente e fisiologicamente calibrado a partir do historico de UMA
-- pessoa, entao nao faz sentido compartilhado entre usuarios. Backfill das
-- linhas existentes (calibradas ate aqui a partir dos dados do admin, unico
-- usuario com historico) para o usuario admin.
SET @mu4_mrc_user_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_recovery_coefficients' AND COLUMN_NAME = 'user_id'
);
SET @mu4_mrc_add_ddl = IF(@mu4_mrc_user_col_exists = 0,
  'ALTER TABLE muscle_recovery_coefficients ADD COLUMN user_id BIGINT NULL AFTER muscle_group_id',
  'SELECT 1'
);
PREPARE mu4_mrc_add_stmt FROM @mu4_mrc_add_ddl; EXECUTE mu4_mrc_add_stmt; DEALLOCATE PREPARE mu4_mrc_add_stmt;

UPDATE muscle_recovery_coefficients SET user_id = @mu4_admin_id WHERE user_id IS NULL AND @mu4_admin_id IS NOT NULL;
SET @mu4_mrc_pending = (SELECT COUNT(*) FROM muscle_recovery_coefficients WHERE user_id IS NULL);

SET @mu4_mrc_composite_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_recovery_coefficients'
    AND INDEX_NAME = 'PRIMARY' AND COLUMN_NAME = 'user_id'
);

-- A PRIMARY KEY (muscle_group_id) atual e o unico indice que sustenta
-- fk_mrc_muscle_group (muscle_group_id -> muscle_groups.id). Drop-la sem
-- substituto deixaria a FK sem indice de apoio - e a nova PRIMARY KEY
-- composta (user_id, muscle_group_id) nao serve, pois muscle_group_id nao e
-- a coluna mais a esquerda. Garante um indice secundario dedicado antes do
-- swap da PK, para nao quebrar "Cannot drop index 'PRIMARY': needed in a
-- foreign key constraint".
SET @mu4_mrc_fk_index_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'muscle_recovery_coefficients'
    AND INDEX_NAME = 'idx_mrc_muscle_group_id'
);
SET @mu4_mrc_fk_index_ddl = IF(@mu4_mrc_pending = 0 AND @mu4_mrc_composite_exists = 0 AND @mu4_mrc_fk_index_exists = 0,
  'ALTER TABLE muscle_recovery_coefficients ADD KEY idx_mrc_muscle_group_id (muscle_group_id)',
  'SELECT 1'
);
PREPARE mu4_mrc_fk_index_stmt FROM @mu4_mrc_fk_index_ddl; EXECUTE mu4_mrc_fk_index_stmt; DEALLOCATE PREPARE mu4_mrc_fk_index_stmt;

SET @mu4_mrc_pk_ddl = IF(@mu4_mrc_pending = 0 AND @mu4_mrc_composite_exists = 0,
  'ALTER TABLE muscle_recovery_coefficients DROP PRIMARY KEY, MODIFY COLUMN user_id BIGINT NOT NULL, ADD PRIMARY KEY (user_id, muscle_group_id), ADD CONSTRAINT fk_mrc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE',
  'SELECT 1'
);
PREPARE mu4_mrc_pk_stmt FROM @mu4_mrc_pk_ddl; EXECUTE mu4_mrc_pk_stmt; DEALLOCATE PREPARE mu4_mrc_pk_stmt;

-- activities.ai_summary / ai_summary_generated_at: resumo de fim de treino
-- gerado via IA (POST /activities/{id}/ai-summary/generate), persistido para
-- consulta futura na tela /registro-exercicios. Mesmo padrao aditivo de
-- conducted_by (ALTER tardio via information_schema + PREPARE/EXECUTE).
SET @act_ai_summary_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activities' AND COLUMN_NAME = 'ai_summary'
);
SET @act_ai_summary_ddl = IF(@act_ai_summary_col_exists = 0,
  'ALTER TABLE activities ADD COLUMN ai_summary TEXT NULL, ADD COLUMN ai_summary_generated_at TIMESTAMP NULL',
  'SELECT 1'
);
PREPARE act_ai_summary_stmt FROM @act_ai_summary_ddl; EXECUTE act_ai_summary_stmt; DEALLOCATE PREPARE act_ai_summary_stmt;

-- ALTER tardio: users.email pode nao existir em bancos ja provisionados
-- antes desta coluna ser adicionada. Campo opcional, sem UNIQUE/NOT NULL -
-- login continua sendo por username/senha, nao por email.
SET @users_email_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'
);
SET @users_email_col_ddl = IF(@users_email_col_exists = 0,
  'ALTER TABLE users ADD COLUMN email VARCHAR(160) NULL AFTER full_name',
  'SELECT 1'
);
PREPARE users_email_col_stmt FROM @users_email_col_ddl; EXECUTE users_email_col_stmt; DEALLOCATE PREPARE users_email_col_stmt;

-- ALTER tardio: gyms.logo_path/opens_at/closes_at (edicao de perfil da
-- academia restrita a admin: nome ja existia via UPDATE, agora ganha logo e
-- horario de funcionamento).
SET @gyms_logo_path_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gyms' AND COLUMN_NAME = 'logo_path'
);
SET @gyms_logo_path_ddl = IF(@gyms_logo_path_col_exists = 0,
  'ALTER TABLE gyms ADD COLUMN logo_path VARCHAR(500) NULL AFTER notes',
  'SELECT 1'
);
PREPARE gyms_logo_path_stmt FROM @gyms_logo_path_ddl; EXECUTE gyms_logo_path_stmt; DEALLOCATE PREPARE gyms_logo_path_stmt;

SET @gyms_opens_at_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gyms' AND COLUMN_NAME = 'opens_at'
);
SET @gyms_opens_at_ddl = IF(@gyms_opens_at_col_exists = 0,
  'ALTER TABLE gyms ADD COLUMN opens_at TIME NULL AFTER logo_path',
  'SELECT 1'
);
PREPARE gyms_opens_at_stmt FROM @gyms_opens_at_ddl; EXECUTE gyms_opens_at_stmt; DEALLOCATE PREPARE gyms_opens_at_stmt;

SET @gyms_closes_at_col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gyms' AND COLUMN_NAME = 'closes_at'
);
SET @gyms_closes_at_ddl = IF(@gyms_closes_at_col_exists = 0,
  'ALTER TABLE gyms ADD COLUMN closes_at TIME NULL AFTER opens_at',
  'SELECT 1'
);
PREPARE gyms_closes_at_stmt FROM @gyms_closes_at_ddl; EXECUTE gyms_closes_at_stmt; DEALLOCATE PREPARE gyms_closes_at_stmt;

INSERT IGNORE INTO schema_migrations (version, name) VALUES
  (1, 'initial fitness schema'),
  (2, 'exercises: remove original_name duplicated canonical columns; mapping_id references exercise_zepp_mappings'),
  (3, 'add gyms, gym_equipment, activities.gym_id, exercises.equipment_id'),
  (4, 'equipment catalog: gym_equipment now references equipment.id instead of duplicating name/photo per gym'),
  (5, 'add muscle_group_body_map table (muscle_group -> react-body-highlighter slugs)'),
  (6, 'add workout_plans.gym_id and workout_plan_exercises.equipment_name for AI-generated training plans'),
  (7, 'zepp watch app: devices pairing, workout_plan_exercises.mapping_id/preferred_equipment_id, exercise_sets hr columns, activities.session_status'),
  (8, 'add gyms.is_recurring to support one-off (avulsa) gyms without a regular weekday schedule'),
  (9, 'add equipment_exercise_mappings (global equipment->exercise) and gym_equipment_catalog (gym->equipment); gym_equipment becomes a materialized cache synced from these two tables'),
  (10, 'muscle recovery v2 fullbody-no-reps: session dose model, pain fields, personal coefficient table'),
  (11, 'add canonical muscle_groups table; exercise_zepp_mappings/muscle_group_body_map/muscle_recovery_coefficients now reference muscle_groups(id) via FK instead of free text'),
  (12, 'cleanup muscle_groups orphans and fill missing muscle_group_body_map entries after canonical migration'),
  (13, 'fill muscle_group_body_map for new split groups (Antebraço posterior, Panturrilha posterior, Peitoral médio/inferior) reusing sibling slugs'),
  (14, 'add workout_plans.plan_time (horario pretendido do treino, alem da data)'),
  (15, 'add workout_plans.split_day (letra A/B/C do ciclo ABC por alcas musculares, quando plan_type=abc)'),
  (16, 'add exercise_aliases table; seed 2 new canonical exercises (Agachamento Livre, Rosca Direta Barra reta ou W) and 19 aliases connecting ABC_TEMPLATE text to real catalog exercises'),
  (17, 'add exercise_sets.muscle_pain/joint_pain (dor por serie, em vez de so por exercicio) for /modo-treino'),
  (18, 'multiuser foundation: users/user_sessions tables; nullable user_id/owner_user_id on activities/gyms/body_composition/daily_biometrics/workout_plans (devices/sleep_metrics/manual_effort_metrics/cardio_recommendations/intervals_wellness/intervals_activities added in their Python _ensure_schema)'),
  (19, 'multiuser data isolation (Parte 4): backfill + NOT NULL for activities.user_id/gyms.owner_user_id/body_composition.user_id/daily_biometrics.user_id/workout_plans.user_id; body_composition unique key becomes (user_id, local_date, source); muscle_recovery_coefficients primary key becomes composite (user_id, muscle_group_id)'),
  (20, 'add activities.ai_summary/ai_summary_generated_at (resumo de fim de treino via IA, tela /modo-treino, exibido em /registro-exercicios)'),
  (21, 'add users.email (campo opcional no perfil do usuario, sem UNIQUE/NOT NULL)'),
  (22, 'add gyms.logo_path/opens_at/closes_at (edicao de perfil de academia admin-only: nome, logo e horario de funcionamento)'),
  (23, 'add exercise_muscle_contributions (pesos de contribuicao PRIMARY/SECONDARY/STABILIZER por exercicio x grupo muscular) with backfill from exercise_zepp_mappings.primary_group_id/secondary_group_id/secondary_participation; fase 1 de uma reforma do calculo de volume por grupamento, fases seguintes vao consumir esta tabela na camada Python'),
  (24, 'fill muscle_group_body_map for Manguito rotador/Core/Panturrilha anterior/Flexores do quadril (grupos criados apos o seed original, mapa corporal ficava cinza para exercicios desses grupos)');

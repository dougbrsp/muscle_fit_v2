"""036 - estado do Motor de Progressao v2 (PR-10; PLANO_20260912.md secoes 5.1,
5.11 e 8.2, onde a migracao esta numerada "027"; decisoes D2, D12 e D15).

Tres tabelas novas, todas so aditivas (nenhuma tabela existente e tocada):

    progression_group_states   fase F1-F6 POR GRUPO MUSCULAR (D2)
      user_id                      BIGINT NOT NULL -> users(id)          ON DELETE CASCADE
      muscle_group_id              BIGINT NOT NULL -> muscle_groups(id)  ON DELETE CASCADE
      phase                        ENUM('F1','F2','F3','F4','F5','F6')
      phase_status                 ENUM('ACTIVE','SKIPPED_NO_ELIGIBLE_TECHNIQUE',
                                        'SKIPPED_LOAD_NOT_APPLICABLE','BLOCKED_ACTIVATION_MAX')
      reps_min / reps_max          TINYINT NOT NULL  (F1 = 6-8; F2+ = 10-12, D1)
      pending_action               VARCHAR(40) NULL  (so a acao PENDENTE; efetiva no proximo finish)
      pending_payload              JSON NULL
      phase_entered_at             DATETIME NOT NULL
      last_rep_range_change_at / last_load_change_at / last_sets_change_at /
      technique_applied_at / next_eligible_progression_at   DATETIME NULL
      engine_version               VARCHAR(40) NOT NULL
      updated_at                   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      PRIMARY KEY (id), UNIQUE KEY uk_pgs_user_group (user_id, muscle_group_id)

    progression_tracks         carga/series/tecnica POR PAR exercicio+equipamento
      user_id          BIGINT NOT NULL          -> users(id)            ON DELETE CASCADE
      exercise_id      BIGINT UNSIGNED NOT NULL (FK LOGICA para exercise_catalog(id) - ver abaixo)
      equipment_id     BIGINT NOT NULL          -> equipment(id)        ON DELETE CASCADE
      muscle_group_id  BIGINT NULL              -> muscle_groups(id)    ON DELETE SET NULL
                       (grupo PRIMARY desnormalizado; NULL = exercicio sem PRIMARY catalogado)
      load_status      ENUM('CALIBRATION','ACTIVE','LOAD_NOT_APPLICABLE')
      working_load_kg  DECIMAL(6,2) NULL  (carga TOTAL, D12 - nunca por lado)
      sets_count       TINYINT NOT NULL DEFAULT 3
      technique_code   VARCHAR(20) NULL   (FK LOGICA para advanced_techniques.code: a 023/031
                       nao cria FK fisica porque ha valores legados sujos em outras tabelas)
      pending_action_applied_at / last_load_change_at / last_sets_change_at /
      last_executed_at DATETIME NULL
      engine_version   VARCHAR(40) NOT NULL
      PRIMARY KEY (id), UNIQUE KEY uk_ptr_user_pair (user_id, exercise_id, equipment_id),
      KEY idx_ptr_user_group_executed (user_id, muscle_group_id, last_executed_at)

    progression_decisions      eventos append-only (auditoria; retencao 18 meses, D15)
      group_state_id           BIGINT NOT NULL -> progression_group_states(id) ON DELETE CASCADE
      track_id                 BIGINT NULL     -> progression_tracks(id)      ON DELETE CASCADE
      user_id                  BIGINT NOT NULL -> users(id)                   ON DELETE CASCADE
      kind                     ENUM('EVALUATION','PRESCRIPTION')
      activity_id              BIGINT NULL     -> activities(id)              ON DELETE SET NULL
      workout_plan_exercise_id BIGINT NULL     -> workout_plan_exercises(id)  ON DELETE SET NULL
      target_datetime          DATETIME NULL
      phase_before / phase_after VARCHAR(2) NULL
      decision                 VARCHAR(40) NOT NULL
      output_json / evidence_json / blocked_by  JSON NULL
      engine_version           VARCHAR(40) NOT NULL
      created_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      KEY idx_pd_group_created (group_state_id, created_at),
      KEY idx_pd_track_created (track_id, created_at),
      KEY idx_pd_wpe (workout_plan_exercise_id),
      KEY idx_pd_user_created (user_id, created_at)   (retencao D15 e as rotas /progression)

`exercise_id` e `BIGINT UNSIGNED` porque a chave canonica de exercicio e
`exercise_catalog.id`, que e unsigned (o id veio de `exercise_zepp_mappings`).
A FK para `exercise_catalog` e LOGICA, com indice proprio (`idx_ptr_exercise`) e
sem constraint fisica: a migracao 028 enumera TODAS as FKs que apontam para
`exercise_catalog` e falha diante de uma nao prevista, e ela ja esta aplicada em
producao (nao pode ser editada). A secao 8.2 tambem nao pede essa FK - ela lista
so `UK (user_id, exercise_id, equipment_id)` e o indice por grupo.
`equipment_id` e `equipment.id` (catalogo global) e NAO `gym_equipment.id`
(cache por academia), conforme secao 5.1.

Tipos referenciados conferidos (ver `_origem` do recorte usado pelo teste
`tests/unit/test_migration_036_plano_aurora.py`):
`users.id`, `muscle_groups.id`, `equipment.id`, `activities.id` e
`workout_plan_exercises.id` sao BIGINT signed, PRIMARY KEY; `exercise_catalog.id`
e BIGINT UNSIGNED, PRIMARY KEY. Todas InnoDB.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano, na ordem das tabelas (as FKs de
`progression_decisions` exigem que as outras duas existam antes).

Idempotencia: tabela ausente -> `CREATE TABLE`; presente no formato esperado ->
nada; presente em outro formato -> erro. Estado convergido = 0 statements.
Qualquer erro (`ProgressionStateError`) acontece ANTES do primeiro statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido: nao importa codigo do app nem usa classes de dados.
"""

from __future__ import annotations

import re

GROUP_STATES = "progression_group_states"
TRACKS = "progression_tracks"
DECISIONS = "progression_decisions"

PHASES_SQL = "'F1','F2','F3','F4','F5','F6'"
PHASE_STATUS_SQL = "'ACTIVE','SKIPPED_NO_ELIGIBLE_TECHNIQUE','SKIPPED_LOAD_NOT_APPLICABLE','BLOCKED_ACTIVATION_MAX'"
LOAD_STATUS_SQL = "'CALIBRATION','ACTIVE','LOAD_NOT_APPLICABLE'"

_TABLE_SUFFIX = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

CREATE_GROUP_STATES_SQL = f"""
CREATE TABLE IF NOT EXISTS {GROUP_STATES} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  muscle_group_id BIGINT NOT NULL,
  phase ENUM({PHASES_SQL}) NOT NULL,
  phase_status ENUM({PHASE_STATUS_SQL}) NOT NULL DEFAULT 'ACTIVE',
  reps_min TINYINT NOT NULL,
  reps_max TINYINT NOT NULL,
  pending_action VARCHAR(40) NULL,
  pending_payload JSON NULL,
  phase_entered_at DATETIME NOT NULL,
  last_rep_range_change_at DATETIME NULL,
  last_load_change_at DATETIME NULL,
  last_sets_change_at DATETIME NULL,
  technique_applied_at DATETIME NULL,
  next_eligible_progression_at DATETIME NULL,
  engine_version VARCHAR(40) NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_pgs_user_group (user_id, muscle_group_id),
  KEY idx_pgs_group (muscle_group_id),
  CONSTRAINT fk_pgs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_pgs_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id) ON DELETE CASCADE
) {_TABLE_SUFFIX}
"""

CREATE_TRACKS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TRACKS} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  exercise_id BIGINT UNSIGNED NOT NULL,
  equipment_id BIGINT NOT NULL,
  muscle_group_id BIGINT NULL,
  load_status ENUM({LOAD_STATUS_SQL}) NOT NULL DEFAULT 'CALIBRATION',
  working_load_kg DECIMAL(6,2) NULL,
  sets_count TINYINT NOT NULL DEFAULT 3,
  technique_code VARCHAR(20) NULL,
  pending_action_applied_at DATETIME NULL,
  last_load_change_at DATETIME NULL,
  last_sets_change_at DATETIME NULL,
  last_executed_at DATETIME NULL,
  engine_version VARCHAR(40) NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ptr_user_pair (user_id, exercise_id, equipment_id),
  KEY idx_ptr_user_group_executed (user_id, muscle_group_id, last_executed_at),
  KEY idx_ptr_exercise (exercise_id),
  KEY idx_ptr_equipment (equipment_id),
  CONSTRAINT fk_ptr_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_ptr_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  CONSTRAINT fk_ptr_group FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id) ON DELETE SET NULL
) {_TABLE_SUFFIX}
"""

CREATE_DECISIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {DECISIONS} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  group_state_id BIGINT NOT NULL,
  track_id BIGINT NULL,
  user_id BIGINT NOT NULL,
  kind ENUM('EVALUATION','PRESCRIPTION') NOT NULL,
  activity_id BIGINT NULL,
  workout_plan_exercise_id BIGINT NULL,
  target_datetime DATETIME NULL,
  phase_before VARCHAR(2) NULL,
  phase_after VARCHAR(2) NULL,
  decision VARCHAR(40) NOT NULL,
  output_json JSON NULL,
  evidence_json JSON NULL,
  blocked_by JSON NULL,
  engine_version VARCHAR(40) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_pd_group_created (group_state_id, created_at),
  KEY idx_pd_track_created (track_id, created_at),
  KEY idx_pd_wpe (workout_plan_exercise_id),
  KEY idx_pd_user_created (user_id, created_at),
  KEY idx_pd_activity (activity_id),
  CONSTRAINT fk_pd_group_state FOREIGN KEY (group_state_id) REFERENCES {GROUP_STATES}(id) ON DELETE CASCADE,
  CONSTRAINT fk_pd_track FOREIGN KEY (track_id) REFERENCES {TRACKS}(id) ON DELETE CASCADE,
  CONSTRAINT fk_pd_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_pd_activity FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE SET NULL,
  CONSTRAINT fk_pd_wpe FOREIGN KEY (workout_plan_exercise_id) REFERENCES workout_plan_exercises(id) ON DELETE SET NULL
) {_TABLE_SUFFIX}
"""

# (tabela, coluna) referenciada -> COLUMN_TYPE esperado. Uma FK exige tipo
# IDENTICO (signed/unsigned inclusive) no MySQL.
REFERENCED = {
    ("users", "id"): "bigint",
    ("muscle_groups", "id"): "bigint",
    ("exercise_catalog", "id"): "bigint unsigned",
    ("equipment", "id"): "bigint",
    ("activities", "id"): "bigint",
    ("workout_plan_exercises", "id"): "bigint",
}

# tabela -> {coluna: (COLUMN_TYPE normalizado, IS_NULLABLE)}
EXPECTED_COLUMNS = {
    GROUP_STATES: {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "muscle_group_id": ("bigint", "NO"),
        "phase": (f"enum({PHASES_SQL})", "NO"),
        "phase_status": (f"enum({PHASE_STATUS_SQL})", "NO"),
        "reps_min": ("tinyint", "NO"),
        "reps_max": ("tinyint", "NO"),
        "pending_action": ("varchar(40)", "YES"),
        "pending_payload": ("json", "YES"),
        "phase_entered_at": ("datetime", "NO"),
        "last_rep_range_change_at": ("datetime", "YES"),
        "last_load_change_at": ("datetime", "YES"),
        "last_sets_change_at": ("datetime", "YES"),
        "technique_applied_at": ("datetime", "YES"),
        "next_eligible_progression_at": ("datetime", "YES"),
        "engine_version": ("varchar(40)", "NO"),
        "updated_at": ("timestamp", "NO"),
    },
    TRACKS: {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "exercise_id": ("bigint unsigned", "NO"),
        "equipment_id": ("bigint", "NO"),
        "muscle_group_id": ("bigint", "YES"),
        "load_status": (f"enum({LOAD_STATUS_SQL})", "NO"),
        "working_load_kg": ("decimal(6,2)", "YES"),
        "sets_count": ("tinyint", "NO"),
        "technique_code": ("varchar(20)", "YES"),
        "pending_action_applied_at": ("datetime", "YES"),
        "last_load_change_at": ("datetime", "YES"),
        "last_sets_change_at": ("datetime", "YES"),
        "last_executed_at": ("datetime", "YES"),
        "engine_version": ("varchar(40)", "NO"),
        "updated_at": ("timestamp", "NO"),
    },
    DECISIONS: {
        "id": ("bigint", "NO"),
        "group_state_id": ("bigint", "NO"),
        "track_id": ("bigint", "YES"),
        "user_id": ("bigint", "NO"),
        "kind": ("enum('EVALUATION','PRESCRIPTION')", "NO"),
        "activity_id": ("bigint", "YES"),
        "workout_plan_exercise_id": ("bigint", "YES"),
        "target_datetime": ("datetime", "YES"),
        "phase_before": ("varchar(2)", "YES"),
        "phase_after": ("varchar(2)", "YES"),
        "decision": ("varchar(40)", "NO"),
        "output_json": ("json", "YES"),
        "evidence_json": ("json", "YES"),
        "blocked_by": ("json", "YES"),
        "engine_version": ("varchar(40)", "NO"),
        "created_at": ("timestamp", "NO"),
    },
}

# tabela -> {nome do indice: [colunas na ordem]}. Indices adicionais no banco
# nao sao erro (o MySQL cria indices auxiliares de FK); os listados aqui
# precisam existir com exatamente estas colunas.
EXPECTED_INDEXES = {
    GROUP_STATES: {"PRIMARY": ["id"], "uk_pgs_user_group": ["user_id", "muscle_group_id"]},
    TRACKS: {
        "PRIMARY": ["id"],
        "uk_ptr_user_pair": ["user_id", "exercise_id", "equipment_id"],
        "idx_ptr_user_group_executed": ["user_id", "muscle_group_id", "last_executed_at"],
    },
    DECISIONS: {
        "PRIMARY": ["id"],
        "idx_pd_group_created": ["group_state_id", "created_at"],
        "idx_pd_track_created": ["track_id", "created_at"],
        "idx_pd_wpe": ["workout_plan_exercise_id"],
        "idx_pd_user_created": ["user_id", "created_at"],
    },
}

# indices que precisam ser UNIQUE (NON_UNIQUE = 0)
UNIQUE_INDEXES = {GROUP_STATES: {"PRIMARY", "uk_pgs_user_group"}, TRACKS: {"PRIMARY", "uk_ptr_user_pair"}, DECISIONS: {"PRIMARY"}}

# tabela -> {coluna: tabela referenciada}
EXPECTED_FKS = {
    GROUP_STATES: {"user_id": "users", "muscle_group_id": "muscle_groups"},
    TRACKS: {
        "user_id": "users",
        "equipment_id": "equipment",
        "muscle_group_id": "muscle_groups",
    },
    DECISIONS: {
        "group_state_id": GROUP_STATES,
        "track_id": TRACKS,
        "user_id": "users",
        "activity_id": "activities",
        "workout_plan_exercise_id": "workout_plan_exercises",
    },
}

# ordem de criacao: progression_decisions referencia as outras duas
CREATE_ORDER = ((GROUP_STATES, CREATE_GROUP_STATES_SQL), (TRACKS, CREATE_TRACKS_SQL), (DECISIONS, CREATE_DECISIONS_SQL))

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class ProgressionStateError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ProgressionStateError(f"036: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    """`tinyint(4)` -> `tinyint` (MySQL < 8.0.19 mostra a largura); espacos de
    ENUM removidos para comparar com o literal declarado aqui."""
    normalized = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    if normalized.startswith("enum("):
        normalized = normalized.replace(", ", ",")
    return normalized


def _check_referenced(engines: dict[str, str], columns: dict) -> None:
    for (table, column), expected_type in REFERENCED.items():
        if table not in engines:
            _fail(f"tabela referenciada {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
        info = columns.get((table, column))
        if info is None:
            _fail(f"{table}.{column} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{column} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["COLUMN_KEY"]) != "PRI":
            _fail(f"{table}.{column} nao e PRIMARY KEY")


def _check_existing(table: str, engines: dict[str, str], columns: dict, indexes: dict, fks: dict) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS[table].items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        # o esperado passa pela MESMA normalizacao (caixa do ENUM declarado aqui)
        if _normalize_type(info["COLUMN_TYPE"]) != _normalize_type(expected_type):
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")

    table_indexes = indexes.get(table, {})
    for name, expected_columns in EXPECTED_INDEXES[table].items():
        found = table_indexes.get(name)
        if found is None or found["columns"] != expected_columns:
            _fail(f"indice {name} de {table} e {found['columns'] if found else None}, esperado {expected_columns}")
        if name in UNIQUE_INDEXES[table] and found["non_unique"]:
            _fail(f"indice {name} de {table} nao e UNIQUE")

    if fks.get(table, {}) != EXPECTED_FKS[table]:
        _fail(f"FKs de {table} sao {fks.get(table, {})}, esperado {EXPECTED_FKS[table]}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(sorted({GROUP_STATES, TRACKS, DECISIONS, *(table for table, _ in REFERENCED)}))
    placeholders = ", ".join(["%s"] * len(names))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    _check_referenced(engines, columns)

    existing = [table for table, _ in CREATE_ORDER if table in engines]
    if existing:
        indexes: dict[str, dict[str, dict]] = {}
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            names,
        ):
            table = _text(row["TABLE_NAME"])
            if table not in existing:
                continue
            entry = indexes.setdefault(table, {}).setdefault(
                _text(row["INDEX_NAME"]), {"columns": [], "non_unique": int(row["NON_UNIQUE"] or 0)}
            )
            entry["columns"].append(_text(row["COLUMN_NAME"]))
        fks: dict[str, dict[str, str]] = {}
        for row in db.query(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            f"WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN ({placeholders}) "
            "AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            names,
        ):
            table = _text(row["TABLE_NAME"])
            if table in existing:
                fks.setdefault(table, {})[_text(row["COLUMN_NAME"])] = _text(row["REFERENCED_TABLE_NAME"])
        for table in existing:
            _check_existing(table, engines, columns, indexes, fks)

    return [(sql, None) for table, sql in CREATE_ORDER if table not in engines]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

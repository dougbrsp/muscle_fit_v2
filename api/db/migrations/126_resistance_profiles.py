"""126 - perfis de resistencia por par equipamento x exercicio (issue #288, fatia 1 parte A).

    equipment_exercise_resistance_profiles          1 linha por par (UNIQUE equipment_exercise_id)
      id                    BIGINT AUTO_INCREMENT PK
      equipment_exercise_id BIGINT NOT NULL -> equipment_exercises(id) ON DELETE CASCADE
      source, mechanism, trajectory, profile, line, direction_changes   VARCHAR(32) NOT NULL DEFAULT 'unknown'
      flags_json            MEDIUMTEXT NOT NULL   lista de sinalizadores (o app grava '[]' quando vazia)
      confidence VARCHAR(16) DEFAULT 'low', status VARCHAR(16) DEFAULT 'suggested', origin VARCHAR(16) DEFAULT 'ai'
      rationale TEXT NULL, evidence TEXT NULL
      vocabulary_version    VARCHAR(32) NOT NULL, model_version VARCHAR(64) NULL
      created_by, reviewed_by  BIGINT NULL -> users(id) ON DELETE SET NULL;  reviewed_at DATETIME NULL
      created_at, updated_at

    equipment_exercise_resistance_profile_history   trilha de cada gravacao
      id, profile_id -> profiles(id) ON DELETE CASCADE, before_json MEDIUMTEXT NULL, after_json MEDIUMTEXT NOT NULL,
      actor_user_id -> users(id) ON DELETE SET NULL, origin VARCHAR(16), reason VARCHAR(500) NULL, created_at

Decisoes: nenhuma FK para `exercise_catalog` (o par ja a tem); valores e vocabulario sao do dono
`domain/resistance_profile.py`, este modulo so guarda (defaults so refletem o dono: unknown/low/suggested/ai).
`down` DESTRUTIVO e guardado: so solta as tabelas com o perfil vazio.

Fluxo: `plan(db)` so LE `information_schema`; tabela ausente -> 1 `CREATE TABLE`; presente na forma esperada -> 0;
divergencia (coluna/tipo/nulabilidade/indice/engine) ou pais incompativeis (`users`, `equipment_exercises`) ->
`ResistanceProfilesError` ANTES do primeiro statement. Pai ausente: erro numa execucao real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com ':'; autocontido.
"""

from __future__ import annotations

import re

PROFILES = "equipment_exercise_resistance_profiles"
HISTORY = "equipment_exercise_resistance_profile_history"
USERS_TABLE = "users"
PAIRS_TABLE = "equipment_exercises"
PARENTS = (USERS_TABLE, PAIRS_TABLE)

IDX_PAIR = "uq_erp_pair"
IDX_HISTORY = "idx_erph_profile"

_COMPONENTS = ("source", "mechanism", "trajectory", "profile", "line", "direction_changes")
_COMPONENT_COLUMNS_SQL = "\n".join(f"  {c} VARCHAR(32) NOT NULL DEFAULT 'unknown'," for c in _COMPONENTS)

CREATE_PROFILES_SQL = f"""
CREATE TABLE IF NOT EXISTS {PROFILES} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  equipment_exercise_id BIGINT NOT NULL,
{_COMPONENT_COLUMNS_SQL}
  flags_json MEDIUMTEXT NOT NULL,
  confidence VARCHAR(16) NOT NULL DEFAULT 'low',
  status VARCHAR(16) NOT NULL DEFAULT 'suggested',
  origin VARCHAR(16) NOT NULL DEFAULT 'ai',
  rationale TEXT NULL,
  evidence TEXT NULL,
  vocabulary_version VARCHAR(32) NOT NULL,
  model_version VARCHAR(64) NULL,
  created_by BIGINT NULL,
  reviewed_by BIGINT NULL,
  reviewed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_PAIR} (equipment_exercise_id),
  CONSTRAINT fk_erp_pair FOREIGN KEY (equipment_exercise_id) REFERENCES {PAIRS_TABLE}(id) ON DELETE CASCADE,
  CONSTRAINT fk_erp_created FOREIGN KEY (created_by) REFERENCES {USERS_TABLE}(id) ON DELETE SET NULL,
  CONSTRAINT fk_erp_reviewed FOREIGN KEY (reviewed_by) REFERENCES {USERS_TABLE}(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_HISTORY_SQL = f"""
CREATE TABLE IF NOT EXISTS {HISTORY} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  profile_id BIGINT NOT NULL,
  before_json MEDIUMTEXT NULL,
  after_json MEDIUMTEXT NOT NULL,
  actor_user_id BIGINT NULL,
  origin VARCHAR(16) NOT NULL,
  reason VARCHAR(500) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY {IDX_HISTORY} (profile_id, id),
  CONSTRAINT fk_erph_profile FOREIGN KEY (profile_id) REFERENCES {PROFILES}(id) ON DELETE CASCADE,
  CONSTRAINT fk_erph_actor FOREIGN KEY (actor_user_id) REFERENCES {USERS_TABLE}(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_HISTORY_SQL = f"DROP TABLE IF EXISTS {HISTORY}"
DROP_PROFILES_SQL = f"DROP TABLE IF EXISTS {PROFILES}"
COUNT_PROFILES_SQL = f"SELECT COUNT(*) AS total FROM {PROFILES}"

_PROFILE_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "equipment_exercise_id": ("bigint", "NO"),
    **{c: ("varchar(32)", "NO") for c in _COMPONENTS},
    "flags_json": ("mediumtext", "NO"),
    "confidence": ("varchar(16)", "NO"),
    "status": ("varchar(16)", "NO"),
    "origin": ("varchar(16)", "NO"),
    "rationale": ("text", "YES"),
    "evidence": ("text", "YES"),
    "vocabulary_version": ("varchar(32)", "NO"),
    "model_version": ("varchar(64)", "YES"),
    "created_by": ("bigint", "YES"),
    "reviewed_by": ("bigint", "YES"),
    "reviewed_at": ("datetime", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

# tabela -> (CREATE, colunas {nome: (tipo normalizado, IS_NULLABLE)}, indices {nome: (UNIQUE?, colunas)})
SPECS: dict[str, tuple[str, dict[str, tuple[str, str]], dict[str, tuple[bool, tuple[str, ...]]]]] = {
    PROFILES: (CREATE_PROFILES_SQL, _PROFILE_COLUMNS, {IDX_PAIR: (True, ("equipment_exercise_id",))}),
    HISTORY: (
        CREATE_HISTORY_SQL,
        {
            "id": ("bigint", "NO"),
            "profile_id": ("bigint", "NO"),
            "before_json": ("mediumtext", "YES"),
            "after_json": ("mediumtext", "NO"),
            "actor_user_id": ("bigint", "YES"),
            "origin": ("varchar(16)", "NO"),
            "reason": ("varchar(500)", "YES"),
            "created_at": ("datetime", "NO"),
        },
        {IDX_HISTORY: (False, ("profile_id", "id"))},
    ),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class ResistanceProfilesError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ResistanceProfilesError(f"126: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (*PARENTS, PROFILES, HISTORY)
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
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    indexes: dict[tuple[str, str], list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
        (PROFILES, HISTORY),
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(row)
    return engines, columns, indexes


def _check_parent(table, engines, columns) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    info = columns.get((table, "id"))
    if info is None:
        _fail(f"{table}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{table}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{table}.id nao e PRIMARY KEY")


def _check_existing(table, engines, columns, indexes) -> None:
    """Confere engine, colunas e indices; NAO confere FKs nem regra de exclusao (mesma lacuna do molde 125)."""
    _create, expected_columns, expected_indexes = SPECS[table]
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in expected_columns.items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    for name, (unique, cols) in expected_indexes.items():
        rows = indexes.get((table, name))
        if not rows:
            _fail(f"{table}.{name} nao existe")
        ordered = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
        found = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
        if found != cols:
            _fail(f"{table}.{name} cobre {found}, esperado {cols}")
        if (int(ordered[0]["NON_UNIQUE"]) == 0) != unique:
            _fail(f"{table}.{name} {'NAO e' if unique else 'e'} UNIQUE")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem (perfis antes do historico)."""
    engines, columns, indexes = _read(db)
    ausentes = [t for t in PARENTS if t not in engines]
    if ausentes:
        if bool(getattr(db, "dry_run", False)):
            return [(SPECS[t][0], None) for t in (PROFILES, HISTORY)]
        _fail(f"tabela {ausentes[0]} nao existe")
    for parent in PARENTS:
        _check_parent(parent, engines, columns)
    steps: list[tuple[str, tuple | None]] = []
    for table in (PROFILES, HISTORY):
        if table in engines:
            _check_existing(table, engines, columns, indexes)
        else:
            steps.append((SPECS[table][0], None))
    return steps


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: historico antes dos perfis (a FK), so o que existe."""
    engines, _columns, _indexes = _read(db)
    if PROFILES in engines:
        rows = db.query(COUNT_PROFILES_SQL)
        if rows and int(next(iter(rows[0].values()))) > 0:
            _fail(f"o down e destrutivo e so roda com {PROFILES} vazia (ha perfis salvos)")
    steps: list[tuple[str, tuple | None]] = []
    if HISTORY in engines:
        steps.append((DROP_HISTORY_SQL, None))
    if PROFILES in engines:
        steps.append((DROP_PROFILES_SQL, None))
    return steps


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

"""079 - metas diárias materializadas (plano de nutrição, Lote L2b, 2026-10-17).

    nutrition_daily_targets
      id                    BIGINT AUTO_INCREMENT PK
      user_id               BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      local_date            DATE NOT NULL     dia civil no fuso do aluno
      plan_id               BIGINT NOT NULL   plano em vigor no dia; referência LÓGICA (+ índice)
      day_kind              VARCHAR(16)       `carb_cycling.DayKind` (normal até o L8)
      is_training_day       TINYINT(1)
      training_known        TINYINT(1)        decisão do motor (`DayContext.training_known`) no
                                              momento do cálculo: 0 = dia futuro, treino ainda
                                              desconhecido. Lida como está; nunca recalculada
                                              comparando a data com hoje
      phase_kind            VARCHAR(16) NULL  `phases.PhaseKind` (L8; o L2b grava NULL)
      kcal_target           DECIMAL(8,2) NULL NULL = não calculável (motor devolveu None)
      protein_g / carbs_g / fat_g / fiber_g  DECIMAL(7,2) NULL
      water_ml              DECIMAL(7,1) NULL sem regra no motor ainda: o L2b grava NULL
      bmr_kcal              DECIMAL(8,2) NULL
      bmr_formula           VARCHAR(24)       fórmula EFETIVA (`bmr.BmrFormula`)
      tdee_kcal             DECIMAL(8,2) NULL gasto total do dia (`TdeeResult.kcal`)
      training_kcal         DECIMAL(8,2) NULL kcal do treino usada (`TrainingKcal.kcal`)
      training_kcal_nature  VARCHAR(16) NULL  `TrainingKcalNature` (informada × MET)
      inputs_json           JSON NULL         sujeito, parâmetros do plano e contexto do dia
      limitations_json      JSON NULL         códigos do motor
      inputs_hash           CHAR(64) NULL     sha256 das entradas do cálculo: o recálculo pula
                                              o upsert quando plano, versão do motor e entradas
                                              não mudaram (igualdade pura, sem regra)
      engine_version        VARCHAR(32)
      computed_at           DATETIME          último recálculo (relógio do banco)
      created_at            DATETIME
      UNIQUE uq_ndt_user_date (user_id, local_date)
      KEY idx_ndt_plan (plan_id)

--- Decisões do DDL -------------------------------------------------------

1. **Uma meta por (aluno, dia)** (decisão E2 do plano): a api-core faz upsert
   SÓ dos dias não congelados (`daily_target.is_target_frozen`, lag de 2 dias
   pelo sync do Intervals); dia congelado nunca é reescrito. SQL e dashboard
   leem o valor pronto.
2. **Valores do motor, nomes do motor**: `tdee_kcal` e `training_kcal` (o
   rascunho do plano §6.2 dizia `tdee_base_kcal`/`training_kcal_planned`: a
   base sem treino exigiria subtrair fora do dono, e a kcal do treino é
   observada ou estimada, não planejada). `data_quality` do rascunho não
   entra: o motor não define qualidade da meta; a natureza da kcal do treino
   (`training_kcal_nature`) carrega a qualidade (`energy_expenditure`).
3. **DECIMAL sem arredondar**: a coluna guarda o número do motor (2 casas); a
   tela formata. NULL = não calculável, nunca 0.
4. **FK só para `users`**; `plan_id` é lógico (a meta congelada sobrevive ao
   plano, que nunca é apagado depois de publicado). Nenhuma FK para
   `exercise_catalog`.

--- Fluxo -----------------------------------------------------------------

Mesmo de 050/078: tabela ausente -> 1 `CREATE TABLE`; convergido -> 0;
forma divergente, ENGINE != InnoDB ou `users` incompatível ->
`NutritionDailyTargetsError` ANTES do primeiro statement; `users` ausente só
passa em DRY-RUN. `down` = DROP TABLE (nada referencia a tabela).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import re

TABLE = "nutrition_daily_targets"
USERS_TABLE = "users"

IDX_USER_DATE = "uq_ndt_user_date"
IDX_PLAN = "idx_ndt_plan"
FK_USER = "fk_ndt_user"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
  plan_id BIGINT NOT NULL,
  day_kind VARCHAR(16) NOT NULL,
  is_training_day TINYINT(1) NOT NULL DEFAULT 0,
  training_known TINYINT(1) NOT NULL DEFAULT 1,
  phase_kind VARCHAR(16) NULL,
  kcal_target DECIMAL(8,2) NULL,
  protein_g DECIMAL(7,2) NULL,
  carbs_g DECIMAL(7,2) NULL,
  fat_g DECIMAL(7,2) NULL,
  fiber_g DECIMAL(7,2) NULL,
  water_ml DECIMAL(7,1) NULL,
  bmr_kcal DECIMAL(8,2) NULL,
  bmr_formula VARCHAR(24) NOT NULL,
  tdee_kcal DECIMAL(8,2) NULL,
  training_kcal DECIMAL(8,2) NULL,
  training_kcal_nature VARCHAR(16) NULL,
  inputs_json JSON NULL,
  limitations_json JSON NULL,
  inputs_hash CHAR(64) NULL,
  engine_version VARCHAR(32) NOT NULL,
  computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_USER_DATE} (user_id, local_date),
  KEY {IDX_PLAN} (plan_id),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

MACRO_COLUMNS = ("protein_g", "carbs_g", "fat_g", "fiber_g")

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "local_date": ("date", "NO"),
    "plan_id": ("bigint", "NO"),
    "day_kind": ("varchar(16)", "NO"),
    "is_training_day": ("tinyint(1)", "NO"),
    "training_known": ("tinyint(1)", "NO"),
    "phase_kind": ("varchar(16)", "YES"),
    "kcal_target": ("decimal(8,2)", "YES"),
    **{name: ("decimal(7,2)", "YES") for name in MACRO_COLUMNS},
    "water_ml": ("decimal(7,1)", "YES"),
    "bmr_kcal": ("decimal(8,2)", "YES"),
    "bmr_formula": ("varchar(24)", "NO"),
    "tdee_kcal": ("decimal(8,2)", "YES"),
    "training_kcal": ("decimal(8,2)", "YES"),
    "training_kcal_nature": ("varchar(16)", "YES"),
    "inputs_json": ("json", "YES"),
    "limitations_json": ("json", "YES"),
    "inputs_hash": ("char(64)", "YES"),
    "engine_version": ("varchar(32)", "NO"),
    "computed_at": ("datetime", "NO"),
    "created_at": ("datetime", "NO"),
}

# `tinyint(4)` -> `tinyint`; `tinyint(1)` (booleano) fica como está.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class NutritionDailyTargetsError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise NutritionDailyTargetsError(f"079: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (TABLE, USERS_TABLE)
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
    indexes: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    ):
        indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    return engines, columns, indexes


def _check_users(engines, columns) -> None:
    if engines[USERS_TABLE].lower() != "innodb":
        _fail(f"{USERS_TABLE} usa ENGINE={engines[USERS_TABLE]}, esperado InnoDB")
    info = columns.get((USERS_TABLE, "id"))
    if info is None:
        _fail(f"{USERS_TABLE}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{USERS_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{USERS_TABLE}.id nao e PRIMARY KEY")


def _check_index(indexes, name: str, *, unique: bool, cols: tuple[str, ...]) -> None:
    rows = indexes.get(name)
    if not rows:
        _fail(f"{TABLE}.{name} nao existe")
    encontradas = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if encontradas != cols:
        _fail(f"{TABLE}.{name} cobre {encontradas}, esperado {cols}")
    if (int(rows[0]["NON_UNIQUE"]) == 0) != unique:
        _fail(f"{TABLE}.{name} {'NAO e' if unique else 'e'} UNIQUE")


def _check_existing(engines, columns, indexes) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    _check_index(indexes, IDX_USER_DATE, unique=True, cols=("user_id", "local_date"))
    _check_index(indexes, IDX_PLAN, unique=False, cols=("plan_id",))


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(CREATE_SQL, None)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns, indexes)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senão nada."""
    engines, _columns, _indexes = _read(db)
    if TABLE not in engines:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

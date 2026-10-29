"""037 - congelamento da prescricao na ficha (PR-11; PLANO_20260912.md secoes
6.3, 8.2 - la numerada "028" - e 12).

Colunas novas, todas ADITIVAS (nenhuma coluna existente e tocada):

    workout_plan_exercises.prescription_json        JSON NULL
        snapshot da prescricao no momento em que a ficha foi criada
        (ProgressionDecision + clamps de seguranca). NULL = ficha legada,
        anterior a este PR -> leitura legada da secao 12.
    workout_plan_exercises.progression_decision_id  BIGINT NULL
        `progression_decisions.id` que originou o snapshot (FK LOGICA - ver
        abaixo), com indice proprio `idx_wpe_progression_decision`.
    workout_plan_exercises.engine_version           VARCHAR(40) NULL
        de qual motor veio a prescricao congelada (hoje o caminho legado;
        os motores determinísticos entram nos PRs 12/13 e so trocam o valor).
    workout_plan_exercises.requires_user_choice     TINYINT(1) NOT NULL DEFAULT 0
        exercicio que exige escolha do usuario antes de executar (secao 6.3).
        E a UNICA coluna NOT NULL: o default 0 vale para todas as linhas
        existentes e mantem exatamente o significado de hoje (ninguem exige
        escolha), entao nenhuma linha muda de comportamento.

    exercise_sets.rpe_target_effective              TINYINT NULL
        RPE alvo efetivamente exibido na execucao daquela serie (depois do
        overlay de seguranca). NULL = serie legada.
    exercise_sets.safety_event                      VARCHAR(40) NULL
        evento de seguranca registrado na serie (ex.: clamp aplicado).
        NULL = nada a registrar / serie legada.

`progression_decision_id` e BIGINT COM SINAL porque `progression_decisions.id`
e `bigint` signed na Aurora (conferido no recorte usado pelo teste
`tests/unit/test_migration_037_plano_aurora.py`; a armadilha do catalogo -
`exercise_catalog.id` BIGINT UNSIGNED - nao se aplica aqui).

A FK e LOGICA, com indice proprio e sem constraint fisica, por duas razoes:
1. `progression_decisions.workout_plan_exercise_id` JA tem FK fisica para
   `workout_plan_exercises` (migracao 036, `fk_pd_wpe`). Uma FK fisica no
   sentido inverso fecharia um CICLO entre as duas tabelas, que o MySQL
   aceita mas que complica DELETE, dump/restore e a retencao de 18 meses das
   decisoes (D15) - apagar uma decisao antiga exigiria a ordem certa.
2. A secao 8.2 pede apenas `idx progression_decision_id`, nao FK.
O snapshot em `prescription_json` e autocontido: a ficha continua legivel
mesmo depois de a decisao correspondente sair pela retencao.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- coluna/indice ausente -> entra no `ALTER TABLE ... ADD ...` da sua tabela
  (UM statement por tabela, so com o que falta);
- ja presente no formato esperado -> nada (estado convergido = 0 statements);
- tabela ausente, ENGINE != InnoDB, coluna presente com tipo/nulabilidade/
  default diferente, ou indice presente em outras colunas ->
  `PrescricaoCongeladaError` ANTES do primeiro statement.

Aurora hoje (recorte tests/unit/data/aurora_schema_2026_09_18_037.json,
versao 36): nenhuma das 6 colunas existe -> 2 statements. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app nem usa classes de dados).
"""

from __future__ import annotations

import re

WPE = "workout_plan_exercises"
SETS = "exercise_sets"

DECISION_INDEX = "idx_wpe_progression_decision"

# tabela -> [(coluna, DDL do ADD COLUMN, COLUMN_TYPE normalizado, IS_NULLABLE,
#             COLUMN_DEFAULT esperado)]
COLUMNS = {
    WPE: (
        ("prescription_json", "JSON NULL", "json", "YES", None),
        ("progression_decision_id", "BIGINT NULL", "bigint", "YES", None),
        ("engine_version", "VARCHAR(40) NULL", "varchar(40)", "YES", None),
        ("requires_user_choice", "TINYINT(1) NOT NULL DEFAULT 0", "tinyint", "NO", "0"),
    ),
    SETS: (
        ("rpe_target_effective", "TINYINT NULL", "tinyint", "YES", None),
        ("safety_event", "VARCHAR(40) NULL", "varchar(40)", "YES", None),
    ),
}

# tabela -> {nome do indice: [colunas na ordem]}
INDEXES = {WPE: {DECISION_INDEX: ["progression_decision_id"]}, SETS: {}}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class PrescricaoCongeladaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise PrescricaoCongeladaError(f"037: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    """`tinyint(1)`/`tinyint(4)` -> `tinyint` (largura de exibicao legada)."""
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _normalize_default(value) -> str | None:
    """`'0'`, `0` e `b'0'` sao o mesmo default para um TINYINT."""
    if value is None:
        return None
    text = _text(value).strip().lower()
    if text.startswith("b'") and text.endswith("'"):
        text = text[2:-1]
    return text.strip("'")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(COLUMNS)
    placeholders = ", ".join(["%s"] * len(names))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    for table in names:
        if table not in engines:
            _fail(f"tabela {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")

    existing: dict[tuple[str, str], dict] = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }

    indexes: dict[tuple[str, str], list[str]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        names,
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(_text(row["COLUMN_NAME"]))

    statements: list[tuple[str, tuple | None]] = []
    for table, columns in COLUMNS.items():
        missing: list[str] = []
        for column, ddl, expected_type, expected_nullable, expected_default in columns:
            info = existing.get((table, column))
            if info is None:
                missing.append(f"ADD COLUMN {column} {ddl}")
                continue
            label = f"{table}.{column}"
            if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
                _fail(f"{label} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
            if _text(info["IS_NULLABLE"]) != expected_nullable:
                _fail(f"{label} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
            if _normalize_default(info["COLUMN_DEFAULT"]) != _normalize_default(expected_default):
                _fail(f"{label} tem DEFAULT {_text(info['COLUMN_DEFAULT'])!r}, esperado {expected_default!r}")

        for index, index_columns in INDEXES[table].items():
            found = indexes.get((table, index))
            if found is None:
                missing.append(f"ADD KEY {index} ({', '.join(index_columns)})")
            elif found != index_columns:
                _fail(f"indice {index} de {table} e {found}, esperado {index_columns}")

        if missing:
            statements.append((f"ALTER TABLE {table} " + ", ".join(missing), None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

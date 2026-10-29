"""045 - convencao de carga do par equipamento<->exercicio (Novo Dashboard,
F0.2 - plano §4, §8.1, §14).

    equipment_exercises.load_convention
        ENUM('total','per_side','per_dumbbell','assisted','bodyweight','bodyweight_plus') NULL

Por que: `exercise_sets.weight_kg` guarda SEMPRE carga total em quilos
(decisao D12, documentada em `app/weight_units.py`), mas o que aquele numero
SIGNIFICA depende do aparelho: 20 kg no leg press horizontal nao e 20 kg no
graviton (assistido) nem 20 kg em cada halter. Sem essa informacao, comparar
duas exposicoes do mesmo exercicio em equipamentos diferentes produz
conclusao errada - e por isso a `comparability_key` (§8.1) inclui a
convencao.

NULL = `total`, nao "desconhecido": e exatamente o que o sistema ja assume
hoje em 100% das linhas (D12). Por isso a coluna nasce NULA e SEM BACKFILL -
nenhum historico e reescrito e nenhum motor muda de comportamento com o
deploy. O cadastro por par e manual (tela `/equipamentos-exercicios`), e a
lista de pares sem convencao vira um item de qualidade de dados (§8.3, F0.3).

ENUM e nao VARCHAR: o dominio e FECHADO e pequeno (as seis convencoes
fisicas possiveis de uma maquina/haltere), definido por nos, nao por um
padrao externo - o oposto do caso de `users.timezone` (044), onde o dominio
e o banco IANA e por isso virou VARCHAR. Com ENUM o banco recusa um valor
fora do vocabulario mesmo que algum caminho de escrita futuro esqueca de
validar, e o espelho do enum em `intelligence/dashboard/comparability.py`
tem um teste que compara os dois lados.

Posicao: SEM `AFTER` de proposito - independente da 032 (`max_safe_rpe_override`,
`safety_notes`) e de qualquer outra coluna; entra no fim de
`equipment_exercises`. Nada no app depende da ordem fisica das colunas
(`SELECT` com lista explicita + acesso por nome).

Fluxo (mesmo padrao da 044): `plan(db)` so LE `information_schema` e devolve
os statements; `up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o
inverso (DROP COLUMN se a coluna existir). O runner (`app.migrate`) so chama
`up`; `down` existe para reversao manual controlada e para o teste de plano
provar que a migracao e reversivel.

- coluna ausente -> exatamente 1 `ALTER TABLE equipment_exercises ADD COLUMN ...`;
- coluna presente no formato esperado -> nada (estado convergido);
- tabela ausente, ENGINE != InnoDB, ou coluna presente com tipo/nulabilidade/
  default diferente -> `LoadConventionError` ANTES do primeiro statement.

Aurora (recorte real somente leitura, tests/unit/data/aurora_schema_2026_09_17_035.json
- `equipment_exercises` NAO tem `load_convention`) -> 1 statement.
Reexecucao: 0 statements.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "equipment_exercises"
COLUMN = "load_convention"

# Ordem CANONICA das convencoes (espelhada em
# `app/intelligence/dashboard/comparability.py::LoadConvention`).
LOAD_CONVENTIONS = (
    "total",  # o numero e a carga total do aparelho (default historico, D12)
    "per_side",  # valor por lado (barra/aparelho carregado dos dois lados)
    "per_dumbbell",  # valor por halter (dois halteres em uso)
    "assisted",  # assistencia: MENOS kg = progresso (graviton, barra assistida)
    "bodyweight",  # so peso corporal: comparar repeticoes, nao carga
    "bodyweight_plus",  # peso corporal + carga externa (cinto, colete)
)

_ENUM_VALUES_SQL = ",".join(f"'{value}'" for value in LOAD_CONVENTIONS)
COLUMN_DDL = f"ENUM({_ENUM_VALUES_SQL}) NULL"
ALTER_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}"
DROP_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

EXPECTED_TYPE = f"enum({_ENUM_VALUES_SQL})"
EXPECTED_NULLABLE = "YES"


class LoadConventionError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise LoadConventionError(f"045: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read_columns(db) -> dict:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")

    return {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
        if _text(row["TABLE_NAME"]) == TABLE
    }


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    columns = _read_columns(db)
    info = columns.get(COLUMN)
    if info is None:
        return [(ALTER_SQL, None)]

    actual_type = _text(info["COLUMN_TYPE"]).strip().lower()
    if actual_type != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(info['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(info["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    if info["COLUMN_DEFAULT"] is not None:
        _fail(f"{TABLE}.{COLUMN} tem DEFAULT={info['COLUMN_DEFAULT']!r}, esperado NULL")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP COLUMN` se a coluna existe, senao nada."""
    columns = _read_columns(db)
    if COLUMN not in columns:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

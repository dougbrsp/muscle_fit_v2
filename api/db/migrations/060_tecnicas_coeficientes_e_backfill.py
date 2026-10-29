"""060 - coeficientes das técnicas avançadas e backfill retroativo do vínculo
série -> técnica -> âncora (Fase 5, PR 2 - dados).

    advanced_techniques
      + max_equivalent_per_anchor  DECIMAL(4,2) NULL  teto de séries
                                   equivalentes por série âncora
      + default_sets_count         INT NULL           séries que a técnica
                                   preenche no Modelo (fst7 = 7, gvt = 10)
      UPDATE dos coeficientes (tabela COEFFICIENTS abaixo)

    exercise_sets (colunas da 059)
      backfill de technique_code e anchor_set_number nas aplicações antigas

Por que: a regra da Fase 5 (`app/domain/technique_accounting.py`) usa o
catálogo como coeficiente do PLANEJADO e como fallback do realizado sem reps,
e pondera cada aplicação realizada pelas reps da série âncora. Até aqui o
catálogo tinha `activation_set_equivalent` = 0 e `recovery_cost_factor` NULL
em todas as linhas (031, "não calibrado"), e as aplicações antigas não tinham
técnica nem âncora. Decisão do usuário (2026-09-28): recálculo RETROATIVO.

--- Coeficientes (literatura; editáveis no catálogo) -----------------------

| code          | equiv./aplicação | teto por âncora | recuperação | séries |
|---------------|------------------|-----------------|-------------|--------|
| drop_set      | 0,50             | 1,00            | 1,15        |        |
| rest_pause    | 0,40             | 1,00            | 1,10        |        |
| forced_reps   | 0,25             | 0,50            | 1,20        |        |
| negative_reps | 0,25             | 0,50            | 1,25        |        |
| partial_reps  | 0,15             | 0,50            | 1,05        |        |
| cheat_reps    | 0,15             | 0,50            | 1,10        |        |
| fst7          | 0                | 0               | 1,00        | 7      |
| gvt           | 0                | 0               | 1,00        | 10     |

FST-7/GVT (decisão 5): só preenchem o número de séries; as séries são de
trabalho comuns, sem equivalente extra (teto 0) nem fator próprio (1,00).

Os UPDATEs só tocam linhas com `max_equivalent_per_anchor IS NULL` (coluna
nova: NULL = "a 060 ainda não calibrou esta linha"), então uma reexecução
nunca sobrescreve um ajuste feito depois no catálogo. Código fora da tabela
(técnica criada depois) não é tocado.

--- Backfill ------------------------------------------------------------------

Só linhas de APLICAÇÃO de técnica (`set_type` rest_pause/drop_set/forced_rep,
com os aliases legados de `app/set_types.py`), e só onde a coluna ainda é
NULL - idempotente, nunca sobrescreve o que a API já gravou.

1. `technique_code`: a técnica do item da ficha
   (`exercises.workout_plan_exercise_id` -> `workout_plan_exercises.
   advanced_technique`) quando ela é do catálogo e grava o mesmo `set_type`
   (ex.: ficha com `negative_reps` + linha `forced_rep` -> `negative_reps`);
   senão, o padrão do `set_type`: drop_set -> drop_set, rest_pause ->
   rest_pause, forced_rep -> forced_reps.
2. `anchor_set_number`: a última série `working` ANTERIOR (menor
   `set_number`) do mesmo exercício. Sem série de trabalho antes, fica NULL
   (a regra usa o topo da faixa prescrita).

Cada backfill é UM UPDATE set-based (JOIN; o da âncora usa tabela derivada
com GROUP BY, que o MySQL materializa - sem o erro 1093 de ler e escrever a
mesma tabela). Não há padrão de lotes nas migrações do projeto, e o UPDATE só
escreve linhas de técnica (fração pequena de `exercise_sets`); a varredura é
uma leitura da tabela. `plan()` conta as pendentes antes (SELECT COUNT) e
omite o UPDATE sem pendência - reexecução: 0 statements.

Nada lê estas colunas ainda (PRs 4-6 da Fase 5): nenhum volume, aderência,
recuperação ou progressão muda com esta migração.

--- Fluxo ----------------------------------------------------------------------

`plan(db)` lê `information_schema`, o catálogo (<= dezenas de linhas) e duas
contagens; `up(db)` executa o plano; `plan_down`/`down` removem as duas
colunas do catálogo e devolvem os coeficientes que ainda estão com o valor da
060 ao estado da 031 (0,00 / NULL). O backfill não é desfeito aqui: as
colunas de `exercise_sets` são removidas pelo `down` da 059.

Dependências: a 031 (`advanced_techniques`) e a 059
(`exercise_sets.technique_code`/`anchor_set_number`). Numa execução real, a
ausência de qualquer uma é erro (falha segura). Só em DRY-RUN (`db.dry_run`),
em que a 031/059 pendentes no mesmo lote não criam tabela/colunas, a ausência
é aceita: as colunas e todos os coeficientes são planejados e o backfill é
planejado sem contagem - padrão da 033/041/042/057.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import re
from decimal import Decimal

TECHNIQUES_TABLE = "advanced_techniques"
SETS_TABLE = "exercise_sets"

COL_MAX = "max_equivalent_per_anchor"
COL_DEFAULT_SETS = "default_sets_count"

ADD_MAX_SQL = f"ALTER TABLE {TECHNIQUES_TABLE} ADD COLUMN {COL_MAX} DECIMAL(4,2) NULL"
ADD_DEFAULT_SETS_SQL = f"ALTER TABLE {TECHNIQUES_TABLE} ADD COLUMN {COL_DEFAULT_SETS} INT NULL"
DROP_MAX_SQL = f"ALTER TABLE {TECHNIQUES_TABLE} DROP COLUMN {COL_MAX}"
DROP_DEFAULT_SETS_SQL = f"ALTER TABLE {TECHNIQUES_TABLE} DROP COLUMN {COL_DEFAULT_SETS}"

# code -> (activation_set_equivalent, max_equivalent_per_anchor, recovery_cost_factor, default_sets_count)
COEFFICIENTS: dict[str, tuple[Decimal, Decimal, Decimal, int | None]] = {
    "drop_set": (Decimal("0.50"), Decimal("1.00"), Decimal("1.15"), None),
    "rest_pause": (Decimal("0.40"), Decimal("1.00"), Decimal("1.10"), None),
    "forced_reps": (Decimal("0.25"), Decimal("0.50"), Decimal("1.20"), None),
    "negative_reps": (Decimal("0.25"), Decimal("0.50"), Decimal("1.25"), None),
    "partial_reps": (Decimal("0.15"), Decimal("0.50"), Decimal("1.05"), None),
    "cheat_reps": (Decimal("0.15"), Decimal("0.50"), Decimal("1.10"), None),
    "fst7": (Decimal("0.00"), Decimal("0.00"), Decimal("1.00"), 7),
    "gvt": (Decimal("0.00"), Decimal("0.00"), Decimal("1.00"), 10),
}

UPDATE_COEFFICIENTS_SQL = (
    f"UPDATE {TECHNIQUES_TABLE} SET activation_set_equivalent = %s, {COL_MAX} = %s, "
    f"recovery_cost_factor = %s, {COL_DEFAULT_SETS} = %s WHERE code = %s AND {COL_MAX} IS NULL"
)
RESTORE_COEFFICIENTS_SQL = (
    f"UPDATE {TECHNIQUES_TABLE} SET activation_set_equivalent = 0.00, recovery_cost_factor = NULL "
    "WHERE code = %s AND activation_set_equivalent = %s AND recovery_cost_factor = %s"
)
PENDING_CALIBRATION_SQL = f"SELECT code FROM {TECHNIQUES_TABLE} WHERE {COL_MAX} IS NULL"

# Aliases de `app/set_types.py` (_ALIASES), já em minúsculas.
_DROP = ("drop_set", "dropset", "drop-set", "drop set")
_REST = ("rest_pause", "restpause", "rest-pause", "rest pause")
_FORCED = ("forced_rep", "forced_reps")
_WORKING = ("working", "")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _canon(alias: str) -> str:
    kind = f"LOWER(TRIM({alias}.set_type))"
    return (
        f"CASE WHEN {kind} IN {_in(_DROP)} THEN 'drop_set' "
        f"WHEN {kind} IN {_in(_REST)} THEN 'rest_pause' ELSE 'forced_rep' END"
    )


_APPLICATION = _in(_DROP + _REST + _FORCED)

BACKFILL_TECHNIQUE_SQL = f"""
UPDATE {SETS_TABLE} s
JOIN exercises e ON e.id = s.exercise_id
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
LEFT JOIN {TECHNIQUES_TABLE} t ON t.code = wpe.advanced_technique
SET s.technique_code = CASE
  WHEN t.code IS NOT NULL AND t.set_type = {_canon('s')} THEN t.code
  WHEN {_canon('s')} = 'drop_set' THEN 'drop_set'
  WHEN {_canon('s')} = 'rest_pause' THEN 'rest_pause'
  ELSE 'forced_reps'
END
WHERE s.technique_code IS NULL AND LOWER(TRIM(s.set_type)) IN {_APPLICATION}
"""

BACKFILL_ANCHOR_SQL = f"""
UPDATE {SETS_TABLE} s
JOIN (
  SELECT a.id AS set_id, MAX(w.set_number) AS anchor
  FROM {SETS_TABLE} a
  JOIN {SETS_TABLE} w ON w.exercise_id = a.exercise_id AND w.set_number < a.set_number
   AND LOWER(TRIM(w.set_type)) IN {_in(_WORKING)}
  WHERE a.anchor_set_number IS NULL AND LOWER(TRIM(a.set_type)) IN {_APPLICATION}
  GROUP BY a.id
) x ON x.set_id = s.id
SET s.anchor_set_number = x.anchor
"""

PENDING_TECHNIQUE_SQL = (
    f"SELECT COUNT(*) AS n FROM {SETS_TABLE} s "
    f"WHERE s.technique_code IS NULL AND LOWER(TRIM(s.set_type)) IN {_APPLICATION}"
)
PENDING_ANCHOR_SQL = (
    f"SELECT COUNT(*) AS n FROM {SETS_TABLE} a "
    f"WHERE a.anchor_set_number IS NULL AND LOWER(TRIM(a.set_type)) IN {_APPLICATION} "
    f"AND EXISTS (SELECT 1 FROM {SETS_TABLE} w WHERE w.exercise_id = a.exercise_id "
    f"AND w.set_number < a.set_number AND LOWER(TRIM(w.set_type)) IN {_in(_WORKING)})"
)

# (tabela, coluna) -> (tipo normalizado, IS_NULLABLE)
EXPECTED = {
    (TECHNIQUES_TABLE, COL_MAX): ("decimal(4,2)", "YES"),
    (TECHNIQUES_TABLE, COL_DEFAULT_SETS): ("int", "YES"),
    (SETS_TABLE, "technique_code"): ("varchar(20)", "YES"),
    (SETS_TABLE, "anchor_set_number"): ("int", "YES"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TechniqueCoefficientsError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise TechniqueCoefficientsError(f"060: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read_schema(db):
    names = (TECHNIQUES_TABLE, SETS_TABLE)
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    return engines, columns


def _present(columns) -> dict[tuple[str, str], bool]:
    present = {}
    for key, (expected_type, expected_nullable) in EXPECTED.items():
        info = columns.get(key)
        present[key] = info is not None
        if info is None:
            continue
        table, name = key
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    return present


def _count(db, sql: str) -> int:
    row = db.query_one(sql)
    return int((row or {}).get("n") or 0)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Lê schema, catálogo e contagens; devolve [(sql, params)]. Sem escrita."""
    dry_run = bool(getattr(db, "dry_run", False))
    engines, columns = _read_schema(db)
    if SETS_TABLE not in engines:
        _fail(f"tabela {SETS_TABLE} nao existe")
    tem_031 = TECHNIQUES_TABLE in engines
    if not tem_031 and not dry_run:
        _fail(f"tabela {TECHNIQUES_TABLE} nao existe (a 031 precisa estar aplicada)")
    present = _present(columns)
    tem_059 = present[(SETS_TABLE, "technique_code")] and present[(SETS_TABLE, "anchor_set_number")]
    if not tem_059 and not dry_run:
        _fail(f"{SETS_TABLE}.technique_code/anchor_set_number nao existem (a 059 precisa estar aplicada)")

    statements: list[tuple[str, tuple | None]] = []
    tem_max = present[(TECHNIQUES_TABLE, COL_MAX)]
    if not tem_max:
        statements.append((ADD_MAX_SQL, None))
    if not present[(TECHNIQUES_TABLE, COL_DEFAULT_SETS)]:
        statements.append((ADD_DEFAULT_SETS_SQL, None))

    if tem_max and tem_031:
        pendentes = {_text(row["code"]) for row in db.query(PENDING_CALIBRATION_SQL)}
    else:
        # Coluna nascendo agora (ou, só em dry-run, catálogo ainda por criar
        # pela 031 pendente): toda linha do catálogo nasce NULL nela.
        pendentes = set(COEFFICIENTS)
    for code, (equiv, teto, recuperacao, series) in COEFFICIENTS.items():
        if code in pendentes:
            statements.append((UPDATE_COEFFICIENTS_SQL, (equiv, teto, recuperacao, series, code)))

    if not tem_059:
        # Dry-run com a 059 pendente: as colunas não podem ser contadas.
        statements.append((BACKFILL_TECHNIQUE_SQL, None))
        statements.append((BACKFILL_ANCHOR_SQL, None))
        return statements
    if _count(db, PENDING_TECHNIQUE_SQL):
        statements.append((BACKFILL_TECHNIQUE_SQL, None))
    if _count(db, PENDING_ANCHOR_SQL):
        statements.append((BACKFILL_ANCHOR_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Coeficientes da 060 -> estado da 031; remove as duas colunas."""
    engines, columns = _read_schema(db)
    if TECHNIQUES_TABLE not in engines:
        return []
    statements: list[tuple[str, tuple | None]] = []
    for code, (equiv, _teto, recuperacao, _series) in COEFFICIENTS.items():
        statements.append((RESTORE_COEFFICIENTS_SQL, (code, equiv, recuperacao)))
    if (TECHNIQUES_TABLE, COL_MAX) in columns:
        statements.append((DROP_MAX_SQL, None))
    if (TECHNIQUES_TABLE, COL_DEFAULT_SETS) in columns:
        statements.append((DROP_DEFAULT_SETS_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

"""063 - M55: alvo da tecnica da F4 no estado do grupo.

    progression_group_states
      + technique_target_exercise_id   BIGINT UNSIGNED NULL
      + technique_target_equipment_id  BIGINT NULL

Por que: `progression_tracks.technique_code` e gravado pela EXECUCAO, entao
guarda tanto a tecnica da F4 (depois da efetivacao do `APPLY_TECHNIQUE`)
quanto a tecnica que so foi executada (tecnica fixa de outro Modelo,
rest-pause feito por conta propria). O filtro `track_technique_in_force` (PR
#288) era por GRUPO: quando o grupo efetiva a F4, TODA trilha dele com
`technique_code` gravado voltava a receber a tecnica, e nao so a trilha que
foi o alvo. O alvo existe no `pending_payload.track_key` do `APPLY_TECHNIQUE`,
mas a efetivacao apaga o payload. Estas colunas guardam o par
(exercise_id, equipment_id) do alvo na efetivacao.

--- Decisoes do DDL -------------------------------------------------------

1. **Par, nao `progression_tracks.id`.** O motor identifica trilhas pelo par
   (`TrackState.key`, `pending_payload.track_key`); a trilha alvo pode nem ter
   id ainda quando a decisao e tomada, e o replay
   (`rebuild_for_user`) apaga e recria as trilhas, trocando os ids. Os tipos
   sao os de `progression_tracks.exercise_id` (BIGINT UNSIGNED) e
   `.equipment_id` (BIGINT).

2. **Sem FK.** A 028 recusa FK nova para `exercise_catalog`, e a 036 confere
   as FKs de `progression_group_states` por IGUALDADE (`EXPECTED_FKS`) - uma
   FK nova para `equipment` faria a 036 acusar formato inesperado. A
   referencia e logica, como em `progression_tracks.exercise_id`.

3. **NULL, sem DEFAULT, sem `AFTER`.** NULL = alvo desconhecido; o codigo
   sempre nomeia as colunas.

--- Backfill --------------------------------------------------------------

**Nenhuma linha e escrita: todas ficam NULL.** Regra conservadora:

- grupo que ainda NAO efetivou a F4 (`technique_applied_at` NULL): o alvo
  sera gravado pela propria efetivacao, ja com o codigo novo. NULL e o valor
  correto ate la, e o filtro ja nao prescreve tecnica executada nesses
  grupos (F1-F3, ou F5/F6 alcancada pulando a F4).
- grupo que JA efetivou a F4 (`technique_applied_at` preenchido, F4-F6): nao
  ha fonte segura do alvo. O `pending_payload` foi apagado na efetivacao, e
  as decisoes de avaliacao (`progression_decisions`) nao guardam o
  `track_key`. "A unica trilha do grupo com `technique_code`" tambem nao e
  prova: a trilha alvo pode nao ter executado a tecnica e outra pode te-la
  executado por conta propria. Esses grupos ficam NULL = comportamento atual
  (qualquer trilha do grupo com tecnica gravada). O replay
  (`scripts/rebuild_progression_tracks.py`) recalcula o alvo a partir do
  historico, se um dia for preciso.

Producao em 2026-10-02 (SELECT somente leitura): nenhum grupo em F4-F6 e
nenhum com `technique_applied_at`; logo nenhum grupo cai no caso legado.

Fluxo (mesmo padrao da 062): `plan(db)` so LE `information_schema`;
`up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> 1 `ALTER TABLE ... ADD COLUMN` por coluna ausente;
- coluna presente com o tipo esperado e NULL -> nada (reexecucao: 0);
- coluna presente com outro tipo/nulabilidade, ou tabela ausente ->
  `ProgressionTechniqueTargetError` ANTES do primeiro statement.

`progression_group_states` ausente (036 pendente no mesmo lote): erro numa
execucao real; aceito so em DRY-RUN (`db.dry_run`), como na 041/042/057/059,
para o plano do lote poder ser exibido - o plano mostra os 2 ADD COLUMN que a
execucao real faria depois da 036.

Aurora (sem as colunas) -> 2 statements. Reexecucao: 0.
`down` -> 1 `DROP COLUMN` por coluna presente (reversivel: sem as colunas o
codigo anterior volta ao filtro por grupo).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "progression_group_states"
COLUMNS = ("technique_target_exercise_id", "technique_target_equipment_id")

ADD_EXERCISE_SQL = f"ALTER TABLE {TABLE} ADD COLUMN technique_target_exercise_id BIGINT UNSIGNED NULL"
ADD_EQUIPMENT_SQL = f"ALTER TABLE {TABLE} ADD COLUMN technique_target_equipment_id BIGINT NULL"
DROP_EXERCISE_SQL = f"ALTER TABLE {TABLE} DROP COLUMN technique_target_exercise_id"
DROP_EQUIPMENT_SQL = f"ALTER TABLE {TABLE} DROP COLUMN technique_target_equipment_id"

_ADD = {"technique_target_exercise_id": ADD_EXERCISE_SQL, "technique_target_equipment_id": ADD_EQUIPMENT_SQL}
_DROP = {"technique_target_exercise_id": DROP_EXERCISE_SQL, "technique_target_equipment_id": DROP_EQUIPMENT_SQL}

EXPECTED_TYPES = {
    "technique_target_exercise_id": "bigint unsigned",
    "technique_target_equipment_id": "bigint",
}
EXPECTED_NULLABLE = "YES"

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class ProgressionTechniqueTargetError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ProgressionTechniqueTargetError(f"063: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20) unsigned` -> `bigint unsigned`
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[str, dict]]:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    columns: dict[str, dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN (%s) AND COLUMN_NAME IN (%s, %s)",
        (TABLE, *COLUMNS),
    ):
        name = _text(row["COLUMN_NAME"])
        if _text(row["TABLE_NAME"]) == TABLE and name in COLUMNS:
            columns[name] = row
    return engines, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    if TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(_ADD[name], None) for name in COLUMNS]  # 036 pendente: ver docstring
        _fail(f"tabela {TABLE} nao existe (a 036 precisa estar aplicada)")
    statements: list[tuple[str, tuple | None]] = []
    for name in COLUMNS:
        column = columns.get(name)
        if column is None:
            statements.append((_ADD[name], None))
            continue
        if _normalize_type(column["COLUMN_TYPE"]) != EXPECTED_TYPES[name]:
            _fail(f"{TABLE}.{name} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPES[name]}")
        if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP COLUMN so das colunas que existem."""
    engines, columns = _read(db)
    if TABLE not in engines:
        return []
    return [(_DROP[name], None) for name in reversed(COLUMNS) if name in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

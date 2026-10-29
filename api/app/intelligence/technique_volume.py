"""Técnicas avançadas no volume REALIZADO (Fase 5, PR 4) - adaptador de
leitura.

A regra (quanto vale cada aplicação, teto por âncora, fator de recuperação)
mora SÓ em `app/domain/technique_accounting.py` e roda em Python. Este módulo
não tem fórmula: ele entrega à regra as linhas que cada consumidor já lê, com
o mínimo de idas ao banco, e devolve a unidade de série equivalente de cada
linha.

Dois formatos de consumidor existem no projeto:

1. **Série a série** (`VolumeService._fetch_window_sets`, tendência semanal,
   drill-down, leitura agregada da recuperação): as linhas já estão na
   memória - `annotate_set_units` grava `set_units` em cada uma, sem statement
   novo.
2. **Agregado no banco por exercício** (`COUNT(es.id)` por `e.id`): a
   consulta ganha a contagem de aplicações (`sql_application_rows`) e, SÓ para
   os exercícios que têm aplicação, `apply_application_units` lê as séries
   desses exercícios numa leitura (paginada) e troca "1 por aplicação" pela
   fatia do bloco. Usuário sem técnica: zero statements a mais.

O agregado por GRUPO do Novo Dashboard (`muscle_summary_service`) e da
aderência usa uma terceira via (`TECHNIQUE_SETS_SQL`): as consultas de
agregação deixam de contar a aplicação e UMA leitura extra traz as séries dos
exercícios com aplicação, com as contribuições - de novo, só quando a
agregação acusou aplicação na janela.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from ..domain.free_exercise_item import FREE_EXERCISE_PRIMARY_WEIGHT, is_free_exercise_item
from ..domain.laboral import is_laboral_item
from ..domain.technique_accounting import LazyCatalog, planned_equivalent, planned_work_sets, realized_set_units
from ..set_types import (
    TECHNIQUE_APPLICATION_SET_TYPES,
    normalize_set_type,
    sql_is_technique_application,
    sql_not_preparatory,
    sql_set_confirmed,
)

EXERCISE_PAGE_SIZE = 400


def load_catalog() -> dict[str, dict[str, Any]]:
    """Catálogo `advanced_techniques` (inclusive inativas: um registro antigo
    de técnica desativada continua valendo o que valia), do cache de processo
    de `advanced_techniques.list_techniques`."""
    from ..advanced_techniques import list_techniques

    return {item["code"]: item for item in list_techniques(include_inactive=True)}


def new_catalog(loader: Optional[Callable[[], Mapping[str, Any]]] = None) -> LazyCatalog:
    """Catálogo preguiçoso de UMA requisição: só lê quando alguma linha da
    leitura é aplicação de técnica."""
    return LazyCatalog(loader or load_catalog)


def _item_field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


ACTIVATION_PAYLOAD_FIELDS: tuple[str, ...] = (
    "mapping_id",
    "sets_count",
    "advanced_technique",
    "advanced_technique_applications",
    # M100: item de exercicio laboral (planejado = 0 em `planned_item_sets`).
    "is_laboral",
)


def activation_payload(item: Any) -> dict[str, Any]:
    """Payload ÚNICO de ativação planejada (M76 E3): os campos da linha que a
    ativação lê, com os nomes do item do Modelo. Serve ficha, preview,
    breakdown, resumo do Modo B e o preview do Modelo - quem calcula as
    séries com técnica é `planned_item_sets`, dentro do VolumeService; o
    chamador nunca pré-soma o equivalente."""
    return {name: _item_field(item, name) for name in ACTIVATION_PAYLOAD_FIELDS}


def planned_item_sets(item: Any, catalog: Optional[Mapping[str, Any]]) -> float:
    """PLANEJADO (Fase 5, PR 5): séries de trabalho de um item do Modelo
    (Pydantic do builder ou dict de `_get_template`/da geração) com o
    equivalente da técnica pelo catálogo - `technique_accounting.planned_work_sets`.
    Item sem técnica não consulta o catálogo (nenhum statement).

    M100: item marcado como laboral (`is_laboral`, `domain/laboral.py`) não
    é trabalho - 0 séries planejadas (fora de volume e ativação)."""
    if is_laboral_item(item):
        return 0.0
    return planned_work_sets(
        _item_field(item, "sets_count"),
        _item_field(item, "advanced_technique"),
        _item_field(item, "advanced_technique_applications"),
        catalog,
    )


def free_exercise_primary_sets(item: Any, catalog: Optional[Mapping[str, Any]]) -> float:
    """M101c: o que um item de exercicio livre (`domain/free_exercise_item`)
    soma no grupo PRIMARIO dele (`muscle_group_id`): `planned_item_sets` x
    `FREE_EXERCISE_PRIMARY_WEIGHT`. 0 para qualquer outro item. Fonte unica
    da previa do editor e do planejado do dashboard (a metodologia aplica o
    mesmo peso no motor puro)."""
    if not is_free_exercise_item(item):
        return 0.0
    return planned_item_sets(item, catalog) * FREE_EXERCISE_PRIMARY_WEIGHT


def planned_item_equivalent(item: Any, catalog: Optional[Mapping[str, Any]]) -> float:
    """Só a parcela da técnica de `planned_item_sets` (0 sem séries)."""
    if not (_item_field(item, "sets_count") or 0) > 0:
        return 0.0
    return planned_equivalent(
        _item_field(item, "advanced_technique"), _item_field(item, "advanced_technique_applications"), catalog
    )


def is_application(row: Mapping[str, Any]) -> bool:
    return normalize_set_type(row.get("set_type")) in TECHNIQUE_APPLICATION_SET_TYPES


def annotate_set_units(
    rows: Sequence[dict[str, Any]],
    catalog: Optional[Mapping[str, Any]],
    *,
    exercise_key: str = "exercise_id",
    key: str = "set_units",
) -> Sequence[dict[str, Any]]:
    """Grava em cada linha a sua unidade de série equivalente (ver
    `technique_accounting.realized_set_units`). Devolve as próprias linhas."""
    for row, unit in zip(rows, realized_set_units(rows, catalog=catalog, exercise_key=exercise_key)):
        row[key] = unit
    return rows


def sql_application_rows(column: str = "es.set_type") -> str:
    """Contagem de linhas de aplicação num agregado por exercício."""
    return f"SUM(CASE WHEN {sql_is_technique_application(column)} THEN 1 ELSE 0 END)"


def sql_work_unit(column: str = "es.set_type") -> str:
    """1 para série que NÃO é aplicação de técnica, 0 para aplicação - a
    aplicação é somada à parte, com a regra do domínio."""
    return f"(CASE WHEN {sql_is_technique_application(column)} THEN 0 ELSE 1 END)"


_EXERCISE_SETS_SQL = f"""
SELECT
  es.exercise_id        AS exercise_id,
  es.id                 AS set_id,
  es.set_number         AS set_number,
  es.set_type           AS set_type,
  es.reps               AS reps,
  es.rpe                AS rpe,
  es.technique_code     AS technique_code,
  es.anchor_set_number  AS anchor_set_number
FROM exercise_sets es
WHERE es.exercise_id IN ({{placeholders}})
  AND {{not_preparatory}}
  AND {sql_set_confirmed('es.reps_pending')}
ORDER BY es.exercise_id ASC, es.set_number ASC
"""


def application_units_by_exercise(
    fetch_all: Callable[..., list[dict[str, Any]]],
    exercise_ids: Iterable[int],
    catalog: Optional[Mapping[str, Any]],
) -> dict[int, float]:
    """Soma das fatias das APLICAÇÕES de cada exercício (linha de
    `exercises`), lendo as séries só desses exercícios - uma leitura por
    página de `EXERCISE_PAGE_SIZE`."""
    ids = sorted({int(value) for value in exercise_ids if value is not None})
    totals: dict[int, float] = {}
    for start in range(0, len(ids), EXERCISE_PAGE_SIZE):
        page = ids[start : start + EXERCISE_PAGE_SIZE]
        rows = fetch_all(
            _EXERCISE_SETS_SQL.format(
                placeholders=",".join(["%s"] * len(page)),
                not_preparatory=sql_not_preparatory("es.set_type"),
            ),
            tuple(page),
        )
        for row, unit in zip(rows, realized_set_units(rows, catalog=catalog)):
            if is_application(row):
                exercise_id = int(row["exercise_id"])
                totals[exercise_id] = totals.get(exercise_id, 0.0) + unit
    return totals


def apply_application_units(
    rows: Sequence[dict[str, Any]],
    fetch_all: Callable[..., list[dict[str, Any]]],
    catalog: Optional[Mapping[str, Any]],
    *,
    id_key: str = "exercise_id",
    count_key: str = "sets_count",
    applications_key: str = "technique_rows",
) -> Sequence[dict[str, Any]]:
    """`count_key` de cada linha (contagem de séries do exercício, com uma
    unidade por aplicação) passa a valer `trabalho + Σ fatias`. Linha sem
    aplicação não muda nem gera leitura."""
    pending = [row for row in rows if int(row.get(applications_key) or 0) > 0]
    if not pending:
        return rows
    units = application_units_by_exercise(fetch_all, (row[id_key] for row in pending), catalog)
    for row in pending:
        applications = int(row.get(applications_key) or 0)
        count = float(row.get(count_key) or 0)
        row[count_key] = max(0.0, count - applications) + units.get(int(row[id_key]), 0.0)
    return rows


def series_number(value: float) -> float | int:
    """Contagem de séries para exibição: inteiro quando é inteiro (o contrato
    de sempre), fração com 2 casas quando a técnica contribuiu com fração."""
    rounded = round(float(value or 0.0), 2)
    return int(rounded) if float(rounded).is_integer() else rounded


# ---------------------------------------------------------------------------
# Séries dos exercícios COM aplicação, com as contribuições musculares -
# usada pelos agregados por grupo/sessão do Novo Dashboard e da aderência.
# ---------------------------------------------------------------------------

_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"


def technique_sets_sql(scope: str) -> str:
    """Séries não-preparatórias dos exercícios que têm ao menos UMA aplicação
    de técnica, uma linha por (série, contribuição ativa). `scope` é a
    condição sobre `a` (janela ou lista de sessões), com os parâmetros depois
    de `a.user_id = %s`.

    `LEFT JOIN` nas contribuições: exercício sem contribuição cadastrada ainda
    traz as séries (a aderência conta a técnica aplicada do item mesmo assim).

    Sem `trained_at` (M76 Lote 2b): nenhum consumidor lia a coluna, e ela era
    mais uma cópia da expressão que agora só existe em
    `time_windows.trained_at_sql`.
    """
    return f"""
SELECT
  a.id                                             AS activity_id,
  a.name                                           AS activity_name,
  a.local_date                                     AS local_date,
  e.id                                             AS exercise_row_id,
  {_CANONICAL}                                     AS exercise_id,
  e.original_name                                  AS original_name,
  eq.name                                          AS equipment_name,
  es.id                                            AS set_id,
  es.set_number                                    AS set_number,
  es.set_type                                      AS set_type,
  es.reps                                          AS reps,
  es.rpe                                           AS rpe,
  es.technique_code                                AS technique_code,
  es.anchor_set_number                             AS anchor_set_number,
  emc.muscle_group_id                              AS muscle_group_id,
  UPPER(TRIM(emc.role))                            AS role,
  emc.stimulus_weight                              AS stimulus_weight,
  emc.fatigue_weight                               AS fatigue_weight
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = {_CANONICAL}
      AND emc.is_active = 1
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN equipment eq ON eq.id = ge.equipment_id
WHERE a.user_id = %s
  AND {scope}
  AND {sql_not_preparatory("es.set_type")}
  AND EXISTS (
    SELECT 1 FROM exercise_sets tq
    WHERE tq.exercise_id = e.id AND {sql_is_technique_application("tq.set_type")}
      AND {sql_set_confirmed('tq.reps_pending')}
  )
ORDER BY e.id ASC, es.set_number ASC
"""


class TechniqueSets:
    """As linhas de `technique_sets_sql`, já com a unidade de cada série.

    `applications()` devolve só as linhas de APLICAÇÃO que chegaram a um
    grupo (uma por contribuição), com a fatia do bloco - é o que os agregados
    somam por fora, já que as consultas de agregação deixaram de contar a
    aplicação."""

    def __init__(self, rows: Sequence[Mapping[str, Any]], catalog: Optional[Mapping[str, Any]]) -> None:
        self.rows = list(rows)
        unique: dict[int, Mapping[str, Any]] = {}
        for row in self.rows:
            unique.setdefault(int(row["set_id"]), row)
        sets = list(unique.values())
        units = realized_set_units(sets, catalog=catalog, exercise_key="exercise_row_id")
        self.units_by_set: dict[int, float] = {int(row["set_id"]): unit for row, unit in zip(sets, units)}
        self._sets = sets

    @classmethod
    def empty(cls) -> "TechniqueSets":
        return cls([], None)

    def applications(self) -> list[tuple[Mapping[str, Any], float]]:
        return [
            (row, self.units_by_set.get(int(row["set_id"]), 0.0))
            for row in self.rows
            if row.get("muscle_group_id") is not None and is_application(row)
        ]

    def application_units_by_exercise_row(self) -> dict[int, float]:
        totals: dict[int, float] = {}
        for row in self._sets:
            if is_application(row):
                key = int(row["exercise_row_id"])
                totals[key] = totals.get(key, 0.0) + self.units_by_set[int(row["set_id"])]
        return totals


__all__ = [
    "TechniqueSets",
    "annotate_set_units",
    "application_units_by_exercise",
    "apply_application_units",
    "is_application",
    "load_catalog",
    "new_catalog",
    "series_number",
    "sql_application_rows",
    "sql_work_unit",
    "technique_sets_sql",
]

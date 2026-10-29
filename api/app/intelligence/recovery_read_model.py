"""Leitura BARATA dos insumos da recuperacao muscular (melhoria M20).

O que muda e o que NAO muda
===========================

NAO muda: o modelo. `fullbody-no-reps-v1` continua inteiro em
`muscle_recovery_service.py` - `effort_points`, `repetition_factor`,
`set_type_factor`, `calculate_recovery_hours`, `classify_readiness`,
`reconcile_recovery_pct`, coeficiente pessoal, fator global por TSB e as
recomendacoes por status seguem sendo a UNICA fonte de verdade, usada
igualmente pela geracao de ficha, pelo Modo Treino e pelo dashboard. Este
modulo nao calcula nem classifica NADA do modelo: ele so entrega os mesmos
numeros de entrada em menos idas ao banco.

Muda: quantas consultas custam esses insumos.
`VolumeService.get_muscle_group_overview` lia DOIS datasets de series (janela
+ historico completo), paginados por atividade, em ~16 statements - e o custo
medido na F1.2 e de NUMERO de statements, nao de volume: no shim da RDS Data
API cada statement paga ~0,4s fixos (sem transacao, cada chamada abre conexao
nova), entao encolher o historico de 180 para 35 dias mudou o total de 6,1s
para 5,8s.

Aqui sao 2 consultas:

1. `HISTORY_PACKED_SQL` - UMA linha por SESSAO, com todos os exercicios e
   todas as series daquela sessao empacotados em texto. As 16.000 series de
   tres anos viram 400 linhas; o custo de serializacao por LINHA da Data API
   (que e o que pesa) cai junto.
2. `WINDOW_SETS_SQL` - as series da janela (14 dias no uso real), com as
   MESMAS colunas que `_fetch_window_sets` devolvia em 1+N+N consultas
   paginadas.

Mais uma terceira, `CONTRIBUTIONS_SQL`, que substitui as tres leituras de
`exercise_muscle_contributions` que o caminho antigo fazia separadamente.

Por que EMPACOTAR em vez de agregar a dose no SQL
=================================================

A primeira versao deste modulo somava a dose no banco (`SUM(effort x rep x
set_type)`). O resultado batia com o do Python ate ~1e-16 - mas soma de
ponto flutuante NAO e associativa, e `round(dose, 3)` num valor que cai
exatamente em cima do meio (o dataset sintetico de 3 anos produz muitos)
escolhia a casa para cima num caminho e para baixo no outro: 181 entradas de
`activity_dose_history` divergiam em 0,001.

Empacotando as series cruas, a dose volta a ser somada EXATAMENTE pelo mesmo
laco Python de sempre, sobre os MESMOS valores, na MESMA ordem - a
equivalencia deixa de ser "dentro da tolerancia" e passa a ser identidade
(`tests/integration/dataapi/test_dashboard_drilldown_performance.py::
test_payload_nao_muda_com_leitura_agregada`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from ..domain.set_measures import normalize_measure, sql_if_rep_based
from ..set_types import KNOWN_SET_TYPES, WORKING, sql_canonical_set_type_case, sql_not_preparatory, sql_set_confirmed
from .dashboard.identity import require_user_id
from .technique_volume import annotate_set_units, new_catalog

logger = logging.getLogger("fitness.intelligence")

# Linhas por pagina quando a consulta unica estoura o teto de 1 MB da
# resposta da RDS Data API (`db_dataapi`, `UnsupportedResultException`). O
# caminho normal e uma pagina so.
PAGE_ROWS = 100
MAX_PAGES = 200

_RESULT_TOO_LARGE_MARKERS = ("UnsupportedResultException", "exceeds the size limit")

# Id canonico do exercicio - mesmo COALESCE de `_FULL_HISTORY_EXERCISE_SELECT`.
_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"

# Vocabulario de `set_type` que o modelo distingue. O codigo numerico existe so
# para encurtar o texto empacotado; quem converte em fator continua sendo
# `technique_accounting.recovery_factor` (catalogo para tecnica - M76 B5 -,
# `set_type_factor` para o resto; indice 0 = "working", que tambem cobre NULL,
# vazio e qualquer valor fora do vocabulario - todos valem 1.00 no modelo).
# M76 Lote 3b: o vocabulario e os apelidos (`dropset`, `warm-up`...) sao do
# dono, `set_types` - o `CASE` sai de `sql_canonical_set_type_case`, entao uma
# grafia legada vira o MESMO canonico que `normalize_set_type` devolveria no
# caminho Python (antes, `drop-set`/`warm-up` caiam em "working" aqui).
SET_TYPE_CODES: tuple[str, ...] = (WORKING,) + tuple(sorted(KNOWN_SET_TYPES - {WORKING}))

_SET_TYPE_CASE = sql_canonical_set_type_case(
    "es.set_type", codes={name: str(code) for code, name in enumerate(SET_TYPE_CODES)}
)

# Separadores do empacotamento. `original_name` vai por ULTIMO e e o unico
# campo de texto livre, entao um '|' dentro do nome nao quebra nada (o split
# em Python tem limite de campos).
_FIELD_SEP = "|"
_SET_SEP = ","
_SET_FIELD_SEP = ":"

# Fase 5 (PR 4): aquecimento e serie de ajuste nao geram estimulo nem dose -
# as MESMAS series que `VolumeService._fetch_set_rows` passou a devolver.
_ES_NOT_PREPARATORY = sql_not_preparatory("es.set_type")

# Lote T3 (medidas de serie): a medida vai empacotada VAZIA quando a serie e
# de repeticoes (o caso de toda linha anterior a 089) - 1 byte a mais por
# serie (o separador), longe do `group_concat_max_len`; `_parse_set` volta a
# `reps` pelo dono (`normalize_measure`). Vocabulario sem `:`/`,`.
_PACKED_MEASURE = sql_if_rep_based("''", "es.measure", "es.measure")

# ---------------------------------------------------------------------------
# 1) Historico empacotado: UMA linha por sessao
# ---------------------------------------------------------------------------

HISTORY_PACKED_SQL = f"""
SELECT
  a.id                                             AS activity_id,
  a.local_date                                     AS local_date,
  {{trained_at}}                                   AS trained_at,
  a.deload_source                                  AS deload_source,
  JSON_ARRAYAGG(x.packed)                          AS exercises_packed
FROM activities a
JOIN (
  SELECT
    e.activity_id AS activity_id,
    CONCAT_WS(
      '{_FIELD_SEP}',
      CAST(e.id AS CHAR),
      COALESCE(CAST({_CANONICAL} AS CHAR), ''),
      COALESCE(CAST(e.muscle_pain AS CHAR), ''),
      COALESCE(CAST(e.joint_pain AS CHAR), ''),
      CAST(COUNT(es.id) AS CHAR),
      GROUP_CONCAT(
        CONCAT_WS(
          '{_SET_FIELD_SEP}',
          COALESCE(CAST(es.set_number AS CHAR), ''),
          COALESCE(CAST(es.reps AS CHAR), ''),
          COALESCE(CAST(es.rpe AS CHAR), ''),
          COALESCE(CAST(es.weight_kg AS CHAR), ''),
          {_SET_TYPE_CASE},
          COALESCE(es.technique_code, ''),
          {_PACKED_MEASURE}
        )
        ORDER BY es.set_number ASC
        SEPARATOR '{_SET_SEP}'
      ),
      COALESCE(CAST(ge.equipment_id AS CHAR), ''),
      COALESCE(eqex.load_convention, ''),
      COALESCE(e.original_name, '')
    ) AS packed
  FROM activities a2
  JOIN exercises e ON e.activity_id = a2.id
  JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
  LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
  LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
  LEFT JOIN equipment_exercises eqex
    ON eqex.equipment_id = ge.equipment_id
   AND eqex.exercise_id = {_CANONICAL}
   AND eqex.is_active = 1
  WHERE a2.user_id = %s
    AND {_ES_NOT_PREPARATORY}
    {{history_filter}}
  GROUP BY e.id, e.activity_id, e.mapping_id, e.muscle_pain, e.joint_pain,
           e.original_name, elm.canonical_exercise_id, ge.equipment_id, eqex.load_convention
) x ON x.activity_id = a.id
GROUP BY a.id, a.local_date, {{trained_at}}, a.deload_source
ORDER BY a.id
"""

# ---------------------------------------------------------------------------
# 2) Series da JANELA: uma consulta so, com as MESMAS colunas que
#    `VolumeService._fetch_window_sets` devolve (o laco de agregados por
#    janela do overview nao mudou uma linha).
# ---------------------------------------------------------------------------

WINDOW_SETS_SQL = f"""
SELECT
  es.id                                            AS set_id,
  es.exercise_id                                   AS exercise_id,
  es.set_number                                    AS set_number,
  es.reps                                          AS reps,
  es.weight_kg                                     AS weight_kg,
  es.rpe                                           AS rpe,
  es.set_type                                      AS set_type,
  es.technique_code                                AS technique_code,
  es.anchor_set_number                             AS anchor_set_number,
  es.measure                                       AS measure,
  a.id                                             AS activity_id,
  a.local_date                                     AS local_date,
  a.name                                           AS activity_name,
  a.activity_type                                  AS activity_type,
  a.duration_seconds                               AS duration_seconds,
  {{trained_at}}                                   AS trained_at,
  zm.id                                            AS mapping_id,
  e.training_goal                                  AS training_goal,
  CAST({_CANONICAL} AS SIGNED)                     AS canonical_exercise_id
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN exercise_catalog zm ON zm.id = {_CANONICAL}
WHERE a.user_id = %s
  AND a.local_date >= %s
  AND {_ES_NOT_PREPARATORY}
ORDER BY es.exercise_id, es.set_number
"""

# ---------------------------------------------------------------------------
# 3) Dados de referencia do usuario, numa consulta so
#
# Substitui TRES leituras do caminho antigo: as duas de
# `exercise_muscle_contributions` (`_fetch_contributions_by_mapping` e
# `_fetch_group_attributes_by_exercise`, esta ultima paginada) e o
# `SELECT id, name FROM muscle_groups`. (O coeficiente pessoal de
# `muscle_recovery_coefficients`, tabela sem escritor, saiu no M76 Lote 2c.)
# Sao todas pequenas e
# todas por usuario: no shim da Data API o que pesa nelas nao e o dado, e a
# ida ao banco (~0,4s cada).
#
# O `IN (subconsulta)` evita ter que descobrir antes a lista de ids
# canonicos: o conjunto devolvido e um superconjunto do observado, e
# superconjunto nao muda nenhuma resolucao (todas as buscas sao por chave).
# ---------------------------------------------------------------------------

REFERENCE_DATA_SQL = f"""
SELECT
  'contribution'       AS kind,
  emc.id               AS id,
  emc.exercise_id      AS exercise_id,
  emc.muscle_group_id  AS muscle_group_id,
  emc.role             AS role,
  emc.stimulus_weight  AS stimulus_weight,
  emc.fatigue_weight   AS fatigue_weight,
  mg.name              AS muscle_group_name
FROM exercise_muscle_contributions emc
LEFT JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
WHERE emc.is_active = 1
  AND emc.exercise_id IN (
    SELECT DISTINCT CAST({_CANONICAL} AS SIGNED)
    FROM activities a
    JOIN exercises e ON e.activity_id = a.id
    LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
    WHERE a.user_id = %s
      AND {_CANONICAL} IS NOT NULL
  )
ORDER BY kind ASC, id ASC
"""


class PackedHistoryError(RuntimeError):
    """Empacotamento inconsistente (ver `_parse_exercise`). Quem chama volta
    para a leitura antiga - nunca segue com dado pela metade."""


def _result_too_large(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _RESULT_TOO_LARGE_MARKERS)


def _fetch_paginated(service: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Uma consulta so no caminho normal. Se a resposta estourar o teto de
    1 MB da Data API, repete em paginas (a ordenacao das consultas acima e
    total, entao `LIMIT/OFFSET` nao embaralha nada)."""
    try:
        return service.fetch_all(sql, params)
    except Exception as exc:  # noqa: BLE001 - so o estouro de 1 MB e tratado
        if not _result_too_large(exc):
            raise
        logger.warning("recovery_read_model: resposta acima de 1 MB, relendo paginado")

    rows: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        page_rows = service.fetch_all(f"{sql}\nLIMIT {PAGE_ROWS} OFFSET {page * PAGE_ROWS}", params)
        rows.extend(page_rows)
        if len(page_rows) < PAGE_ROWS:
            return rows
    return rows


def _optional(text: str) -> str | None:
    return text if text != "" else None


def _parse_set(chunk: str) -> dict[str, Any]:
    """Uma serie empacotada -> o mesmo dicionario que a leitura antiga
    entregava ao laco de dose (so as chaves que o modelo consome)."""
    set_number, reps, rpe, weight_kg, type_code, technique_code, measure = chunk.split(_SET_FIELD_SEP)
    index = int(type_code) if type_code.isdigit() else 0
    return {
        "set_number": int(set_number) if set_number else None,
        "reps": int(reps) if reps else None,
        "rpe": float(rpe) if rpe else None,
        "weight_kg": float(weight_kg) if weight_kg else None,
        "set_type": SET_TYPE_CODES[index] if index < len(SET_TYPE_CODES) else SET_TYPE_CODES[0],
        # Tecnica da linha (Fase 5, PR 4): o fator de recuperacao da dose sai
        # do catalogo por ela. Codigo do catalogo e snake_case - nunca contem
        # os separadores do empacotamento.
        "technique_code": technique_code or None,
        # Lote T3: medida da serie (vazio = reps).
        "measure": normalize_measure(measure).value,
    }


def _parse_exercise(packed: str) -> dict[str, Any]:
    """Um exercicio empacotado. `sets_count` vem do proprio `COUNT(*)` do
    banco e e conferido contra o numero de series lidas: e essa conferencia
    que detecta um `GROUP_CONCAT` truncado por `group_concat_max_len`
    (1024 bytes por padrao, ~55 series num mesmo exercicio) em vez de
    devolver uma dose menor em silencio."""
    parts = packed.split(_FIELD_SEP, 8)
    if len(parts) != 9:
        raise PackedHistoryError(f"exercicio empacotado invalido: {packed[:80]!r}")
    (
        exercise_id,
        mapping_id,
        muscle_pain,
        joint_pain,
        sets_count,
        sets_packed,
        equipment_id,
        load_convention,
        original_name,
    ) = parts

    set_rows = [_parse_set(chunk) for chunk in sets_packed.split(_SET_SEP) if chunk]
    if len(set_rows) != int(sets_count):
        raise PackedHistoryError(
            f"exercicio {exercise_id}: {len(set_rows)} series lidas x {sets_count} contadas "
            "(group_concat_max_len?)"
        )

    return {
        "exercise_id": int(exercise_id),
        "mapping_id": int(mapping_id) if mapping_id else None,
        "muscle_pain": int(muscle_pain) if muscle_pain else None,
        "joint_pain": int(joint_pain) if joint_pain else None,
        "original_name": _optional(original_name),
        # Chave de comparabilidade do sinal "Desempenho recente"
        # (`exercise_performance`): equipamento CANONICO e convencao de carga,
        # as MESMAS colunas do caminho serie a serie.
        "equipment_id": int(equipment_id) if equipment_id else None,
        "load_convention": _optional(load_convention),
        "set_rows": set_rows,
    }


def _group_attributes(contribution_rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Grupo primario/secundario por exercicio canonico - MESMO criterio de
    `VolumeService._fetch_group_attributes_by_exercise`: entre as
    contribuicoes ativas, a de MENOR `id` com aquele papel."""
    from .volume_service import _EMPTY_GROUP_ATTRIBUTES

    attributes: dict[int, dict[str, Any]] = {}
    for row in contribution_rows:
        exercise_id = row.get("exercise_id")
        if exercise_id is None:
            continue
        entry = attributes.setdefault(int(exercise_id), dict(_EMPTY_GROUP_ATTRIBUTES))
        role = (row.get("role") or "").strip().upper()
        name = row.get("muscle_group_name") or None
        if role == "PRIMARY" and entry["_primary_seen"] is False:
            entry["_primary_seen"] = True
            entry["primary_group"] = name
            entry["primary_group_id"] = row.get("muscle_group_id")
        elif role == "SECONDARY" and entry["_secondary_seen"] is False:
            entry["_secondary_seen"] = True
            entry["secondary_group"] = name
            entry["secondary_group_id"] = row.get("muscle_group_id")
            entry["secondary_participation"] = row.get("stimulus_weight")
    return attributes


def _history_items(
    service: Any, user_id: int, history_days: int | None, attributes_by_exercise: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Itens de historico (um por exercicio de cada sessao), na MESMA ordem
    do caminho antigo - `exercise_id` crescente. A ordem importa: a dose de
    um grupo numa sessao e uma soma de floats, e somar na mesma ordem e o que
    torna os dois caminhos identicos ate o ultimo bit."""
    from .volume_service import _GROUP_ATTRIBUTE_KEYS

    history_filter = ""
    params: tuple[Any, ...] = (user_id,)
    if history_days is not None:
        history_filter = "AND a2.local_date >= %s"
        params = (user_id, service._days_ago(user_id, int(history_days)))

    sql = HISTORY_PACKED_SQL.format(history_filter=history_filter, trained_at=service._trained_at_sql(user_id))
    items: list[dict[str, Any]] = []
    for row in _fetch_paginated(service, sql, params):
        packed = row.get("exercises_packed")
        if not packed:
            continue
        exercises = json.loads(packed) if isinstance(packed, (str, bytes)) else packed
        for entry in exercises:
            item = _parse_exercise(entry)
            item["activity_id"] = row.get("activity_id")
            item["local_date"] = row.get("local_date")
            item["trained_at"] = row.get("trained_at")
            # M97 D: origem do deload da sessao (fora do e1RM de referencia e
            # do "Desempenho recente"; a dose conta).
            item["deload_source"] = row.get("deload_source")
            mapping_id = item["mapping_id"]
            resolved = attributes_by_exercise.get(int(mapping_id)) if mapping_id else None
            for key in _GROUP_ATTRIBUTE_KEYS:
                item[key] = (resolved or {}).get(key)
            items.append(item)

    items.sort(key=lambda item: item["exercise_id"])
    return items


def _window_sets(
    service: Any, days: int, user_id: int, attributes_by_exercise: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Series da janela em UMA consulta, com as mesmas colunas de
    `VolumeService._fetch_window_sets` (que faz 1+N+N consultas paginadas por
    atividade). A janela e curta (14 dias no uso real da recuperacao), entao o
    resultado e pequeno."""
    from .volume_service import _EMPTY_GROUP_ATTRIBUTES, _GROUP_ATTRIBUTE_KEYS

    composed: list[dict[str, Any]] = []
    sql = WINDOW_SETS_SQL.format(trained_at=service._trained_at_sql(user_id))
    for row in _fetch_paginated(service, sql, (user_id, service._days_ago(user_id, days))):
        entry = dict(row)
        canonical_id = entry.pop("canonical_exercise_id")
        resolved = attributes_by_exercise.get(int(canonical_id)) if canonical_id else None
        for key in _GROUP_ATTRIBUTE_KEYS:
            entry[key] = (resolved or _EMPTY_GROUP_ATTRIBUTES)[key]
        composed.append(entry)

    composed.sort(key=lambda row: (int(row["exercise_id"]), int(row["set_number"] or 0)))
    # Series equivalentes de cada linha - a MESMA anotacao de
    # `VolumeService._fetch_sets_dataset`, para os dois caminhos darem o
    # mesmo overview.
    catalog = service.technique_catalog() if hasattr(service, "technique_catalog") else new_catalog()
    annotate_set_units(composed, catalog)
    return composed


def fetch_overview_datasets(
    service: Any, days: int, user_id: int, history_days: int | None = None
) -> dict[str, Any]:
    """Os quatro insumos de `VolumeService.get_muscle_group_overview` em 3
    consultas: contribuicoes/nomes de grupo, historico empacotado por sessao e
    series da janela.

    `service` e um `VolumeService` (so `fetch_all` e usado). `user_id` e
    OBRIGATORIO: nenhuma leitura nova do dashboard aceita "sem filtro de
    usuario" (plano §15)."""
    user_id = require_user_id(user_id)

    reference_rows = _fetch_paginated(service, REFERENCE_DATA_SQL, (user_id,))
    contribution_rows = [row for row in reference_rows if row.get("kind") == "contribution"]
    attributes_by_exercise = _group_attributes(contribution_rows)

    contributions_by_mapping: dict[int, list[dict[str, Any]]] = {}
    muscle_group_names_by_id: dict[int, str] = {}
    for row in contribution_rows:
        exercise_id = row.get("exercise_id")
        if exercise_id is not None:
            contributions_by_mapping.setdefault(int(exercise_id), []).append(
                {
                    "exercise_id": row.get("exercise_id"),
                    "muscle_group_id": row.get("muscle_group_id"),
                    "role": row.get("role"),
                    "stimulus_weight": row.get("stimulus_weight"),
                    "fatigue_weight": row.get("fatigue_weight"),
                }
            )
        group_id = row.get("muscle_group_id")
        if group_id is not None and row.get("muscle_group_name"):
            muscle_group_names_by_id[int(group_id)] = row["muscle_group_name"]

    return {
        "window_sets": _window_sets(service, days, user_id, attributes_by_exercise),
        "history_items": _history_items(service, user_id, history_days, attributes_by_exercise),
        "contributions_by_mapping": contributions_by_mapping,
        "muscle_group_names_by_id": muscle_group_names_by_id,
    }


__all__ = [
    "REFERENCE_DATA_SQL",
    "HISTORY_PACKED_SQL",
    "SET_TYPE_CODES",
    "WINDOW_SETS_SQL",
    "PackedHistoryError",
    "fetch_overview_datasets",
]

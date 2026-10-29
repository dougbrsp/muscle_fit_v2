"""Planejado x executado e aderencia por ciclo (plano §5, §6 `adherence-v2`,
§7 "Cumpri o planejado?" e "Frequencia coerente com o ciclo?", §10, §11, §15).

Este modulo responde tres perguntas, e as tres saem do MESMO vinculo:

1. **O que aconteceu em cada sessao** (`build_workouts`): a lista de sessoes
   da janela com series fisicas e efetivas, estimulo, fadiga, RPE, recordes e
   a contagem de itens por status.
2. **O que aconteceu dentro de uma sessao** (`build_workout_detail`):
   prescrito x executado item a item, com as series de origem.
3. **Se o ciclo esta sendo cumprido** (`build_adherence`): sessoes esperadas,
   realizadas, nao executadas e a proxima do ciclo.

## Os status sao DERIVADOS - nao ha coluna nem migracao

O §18 descartou `execution_status` explicitamente ("dupla verdade"). O que o
banco guarda e o VINCULO (`exercises.workout_plan_exercise_id`, migracao 026)
e as series; o resto e leitura, a cada consulta (§5):

| Status        | Regra                                                          |
|---------------|----------------------------------------------------------------|
| `COMPLETED`   | series de trabalho >= `sets_count` prescrito                    |
| `INTERRUPTED` | >= 1 serie de trabalho, porem menos que o prescrito             |
| `SKIPPED`     | item da ficha sem nenhuma serie na sessao                       |
| `SUBSTITUTED` | vinculo presente e exercicio executado != prescrito             |
| `EXTRA`       | exercicio executado sem vinculo com item da ficha               |

"Serie de trabalho" e a mesma definicao dos demais agregados do dashboard
(`sql_not_preparatory`): aquecimento e serie de ajuste nao contam - um item
com 3 aquecimentos e nenhuma serie de trabalho foi PULADO, nao concluido.

O PRESCRITO vem do snapshot congelado (`workout_plan_exercises.prescription_json`,
migracao 037) quando existe, com fallback para as colunas da linha em fichas
legadas. Qual das duas fontes valeu para cada item vai no payload
(`prescribed.source`) - sem isso, "faltaram 2 series" seria uma afirmacao sem
origem declarada.

## Agrupamento de sessoes: slot do template, nao string de nome

A tela v2 agrupava sessoes por `activities.name` (documentado como limitacao
em `DashboardTreinosClient.tsx`), entao "Treino A" e "treino a " viravam
grupos distintos e qualquer renomeacao quebrava a serie historica. O vinculo
real existe desde a migracao 026 + 009: `activities.workout_plan_id` ->
`workout_plans.template_id` + `template_slot_sequence`. E ele que este modulo
devolve em `plan_link`; o nome fica so como rotulo.

## Desempenho (§15, e o timeout de producao do #188)

Mesmo padrao que a F1.2 estabeleceu em `muscle_summary_service`: a agregacao
acontece NO BANCO, dentro da janela, em poucas consultas que devolvem dezenas
a poucas centenas de linhas curtas. Especificamente:

- a lista e PAGINADA (`limit`/`offset`) e todas as consultas de detalhe
  recebem o `IN (...)` da pagina - nunca a janela inteira;
- nada de N+1: uma consulta por dimensao (totais, series por exercicio, itens
  da ficha, historico de recordes), nao uma por sessao;
- `VolumeService.get_muscle_group_overview` NAO e usado: ele le o historico
  completo do usuario por chamada (8,3s no dataset de 3 anos) e aqui nao ha
  nenhuma pergunta sobre recuperacao;
- o prescrito congelado e lido com `JSON_EXTRACT` das 5 chaves usadas, em vez
  de trazer o JSON inteiro de cada item.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence

from ... import request_memo
from ...domain.deload import deload_source_label, deload_source_value
from ...domain.technique_accounting import SET_TYPE_BY_CODE
from ...observability import log_event
from ...template_versions import plan_version_expr
from .time_windows import TRAINED_AT_TOKEN, with_trained_at
from ...set_types import (
    PREPARATORY_SET_TYPES,
    TECHNIQUE_APPLICATION_SET_TYPES,
    normalize_set_type,
    sql_is_technique_application,
    sql_not_preparatory,
    sql_set_confirmed,
)
from ..methodology_volume import sql_hypertrophy_volume_weight
from ..technique_volume import TechniqueSets, new_catalog, sql_application_rows, sql_work_unit, technique_sets_sql
from ..slot_session_comparison import session_group_of
from ..template_lineage_reader import load_lineage_roots
from ..volume_service import template_cycle_length_days
from ...load_conventions import load_convention_select_expr
from .coercion import to_date, to_datetime, to_float
from .contracts import DataNature, DataQuality, PrescriptionSource, WindowInfo
from .identity import require_user_id
from .session_records import SETS_SQL as SESSION_RECORD_SETS_SQL
from .session_records import compute_session_records, exposures_to_compare, history_sql, parse_history
from .session_records import record_exercise_ids as _record_exercise_ids
from .metrics_registry import (
    EXECUTED_SESSION_STATUSES,
    PlanItemStatus,
    SessionExecutionStatus,
    metric_version,
    normalize_session_status,
)
from ...db_helpers import rows as _rows

# Paginacao da lista de sessoes (§10: "sem paginacao exceto `workouts`").
DEFAULT_WORKOUTS_LIMIT = 20
MAX_WORKOUTS_LIMIT = 50

# Teto de series devolvidas pelo DETALHE de uma sessao. A resposta da RDS Data
# API tem limite de 1 MB (`db_dataapi.py`); 400 series com ~8 campos curtos
# ficam bem abaixo disso. O corte e nos ULTIMOS itens da sessao e e DECLARADO
# (`sets_limit`/`sets_truncated`) - a tela diz que faltam series em vez de
# exibir um total errado.
MAX_DETAIL_SETS = 400

_NOT_PREPARATORY = sql_not_preparatory("es.set_type")

# Fase 5 (PR 4): "serie de trabalho" da COMPLETUDE nao inclui aplicacao de
# tecnica (mini-bloco de rest-pause, drop, reps alem da falha) - ela e
# indicador proprio do item ("tecnica aplicada X/N"). No estimulo/fadiga da
# sessao a aplicacao entra com a sua fatia do bloco da ancora, somada em
# Python pela regra unica (`domain/technique_accounting.py`).
_IS_APPLICATION = sql_is_technique_application("es.set_type")
_WORK = sql_work_unit("es.set_type")

# Id canonico do exercicio executado - mesmo padrao de `exposure_service` e
# `muscle_summary_service`: exercicio renomeado/absorvido continua no MESMO
# historico. E este id que se compara com `workout_plan_exercises.mapping_id`
# (que ja e id de `exercise_catalog`).
_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# PR3: cada sessao traz a VERSAO do Modelo da ficha (`template_version_id`):
# a gravada na ficha ou a vigente no `plan_date`. E por
# ela que o nome do slot e resolvido (`_template_slot_names`), entao uma
# sessao antiga mostra o nome que o slot tinha NAQUELA versao. Subconsulta
# correlata no statement que ja existe - nenhuma ida a mais ao banco.
_SESSIONS_PAGE_TEMPLATE = """
SELECT
  a.id                AS activity_id,
  a.local_date        AS local_date,
  {trained_at} AS trained_at,
  a.name              AS activity_name,
  a.duration_seconds  AS duration_seconds,
  a.session_status    AS session_status,
  a.notes             AS notes,
  a.deload_source     AS deload_source,
  a.workout_plan_id   AS workout_plan_id,
  wp.name             AS plan_name,
  wp.template_id      AS template_id,
  wp.template_slot_sequence AS template_slot_sequence,
  wpt.name            AS template_name,
  {version_expr} AS template_version_id
FROM activities a
LEFT JOIN workout_plans wp
       ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
LEFT JOIN workout_plan_templates wpt
       ON wpt.id = wp.template_id
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
ORDER BY {trained_at} DESC, a.id DESC
LIMIT %s OFFSET %s
"""

SESSIONS_COUNT_SQL = """
SELECT COUNT(*) AS total
FROM activities a
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
"""

_SESSION_BY_ID_TEMPLATE = """
SELECT
  a.id                AS activity_id,
  a.local_date        AS local_date,
  {trained_at} AS trained_at,
  a.name              AS activity_name,
  a.duration_seconds  AS duration_seconds,
  a.session_status    AS session_status,
  a.notes             AS notes,
  a.deload_source     AS deload_source,
  a.workout_plan_id   AS workout_plan_id,
  wp.name             AS plan_name,
  wp.template_id      AS template_id,
  wp.template_slot_sequence AS template_slot_sequence,
  wpt.name            AS template_name,
  {version_expr} AS template_version_id
FROM activities a
LEFT JOIN workout_plans wp
       ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
LEFT JOIN workout_plan_templates wpt
       ON wpt.id = wp.template_id
WHERE a.user_id = %s AND a.id = %s
"""

SESSIONS_PAGE_SQL = _SESSIONS_PAGE_TEMPLATE.format(version_expr=plan_version_expr("wp", by_date=True), trained_at=TRAINED_AT_TOKEN)
SESSION_BY_ID_SQL = _SESSION_BY_ID_TEMPLATE.format(version_expr=plan_version_expr("wp", by_date=True), trained_at=TRAINED_AT_TOKEN)

# Totais fisicos por sessao (sem contribuicao muscular: aqui `COUNT` de serie
# precisa ser de SERIE, e o JOIN de contribuicoes multiplicaria as linhas).
SESSION_TOTALS_SQL = f"""
SELECT
  a.id                     AS activity_id,
  COUNT(DISTINCT e.id)     AS exercises_count,
  SUM(CASE WHEN {_NOT_PREPARATORY} THEN 1 ELSE 0 END) AS physical_sets,
  SUM(CASE WHEN {_NOT_PREPARATORY} AND es.rpe IS NOT NULL THEN es.rpe ELSE 0 END) AS rpe_sum,
  SUM(CASE WHEN {_NOT_PREPARATORY} AND es.rpe IS NOT NULL THEN 1 ELSE 0 END)      AS rpe_n
FROM activities a
JOIN exercises e ON e.activity_id = a.id
LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
WHERE a.user_id = %s
  AND a.id IN ({{placeholders}})
GROUP BY a.id
"""

# Estimulo e fadiga por sessao: aqui o JOIN com as contribuicoes e o ponto,
# porque cada serie soma o peso de CADA grupo que ela atinge.
# M97 F4: o estimulo (volume hipertrofico) pondera a serie de forca pelo peso
# do dono (`methodology_volume.sql_hypertrophy_volume_weight`); a fadiga segue
# cheia.
SESSION_EFFECTIVE_SQL = f"""
SELECT
  a.id                        AS activity_id,
  SUM({_WORK} * emc.stimulus_weight * {sql_hypertrophy_volume_weight("e")}) AS stimulus_sets,
  SUM({_WORK} * emc.fatigue_weight)     AS fatigue_sets,
  {sql_application_rows("es.set_type")} AS technique_rows
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = {_CANONICAL}
      AND emc.is_active = 1
WHERE a.user_id = %s
  AND a.id IN ({{placeholders}})
  AND {_NOT_PREPARATORY}
GROUP BY a.id
"""

# Exercicios EXECUTADOS das sessoes da pagina, com as series ja contadas por
# tipo. `LEFT JOIN exercise_sets` de proposito: exercicio registrado sem
# nenhuma serie tem de aparecer (e um item a classificar, nao uma linha a
# sumir).
SESSION_EXERCISES_SQL = f"""
SELECT
  e.activity_id                 AS activity_id,
  e.id                          AS exercise_row_id,
  e.sequence                    AS position_in_session,
  e.original_name               AS original_name,
  e.workout_plan_exercise_id    AS workout_plan_exercise_id,
  {_CANONICAL}                  AS exercise_id,
  e.notes                       AS notes,
  COUNT(es.id)                                                      AS total_sets,
  SUM(CASE WHEN {_NOT_PREPARATORY} AND NOT {_IS_APPLICATION} THEN 1 ELSE 0 END) AS working_sets,
  SUM(CASE WHEN {_IS_APPLICATION} THEN 1 ELSE 0 END)                AS technique_sets,
  SUM(CASE WHEN {_NOT_PREPARATORY} THEN COALESCE(es.reps, 0) ELSE 0 END) AS total_reps,
  MAX(CASE WHEN {_NOT_PREPARATORY} THEN es.weight_kg ELSE NULL END) AS best_weight_kg,
  SUM(CASE WHEN {_NOT_PREPARATORY} AND es.rpe IS NOT NULL THEN es.rpe ELSE 0 END) AS rpe_sum,
  SUM(CASE WHEN {_NOT_PREPARATORY} AND es.rpe IS NOT NULL THEN 1 ELSE 0 END)      AS rpe_n
FROM activities a
JOIN exercises e ON e.activity_id = a.id
LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
WHERE a.user_id = %s
  AND a.id IN ({{placeholders}})
GROUP BY e.activity_id, e.id, e.sequence, e.original_name,
         e.workout_plan_exercise_id, {_CANONICAL}, e.notes
ORDER BY e.activity_id ASC, e.sequence ASC
"""

# Itens das FICHAS ligadas as sessoes da pagina. O prescrito congelado e lido
# chave a chave (`JSON_EXTRACT`), nunca o JSON inteiro: o snapshot carrega
# clamps, evidencias e substituicao que esta tela nao usa.
PLAN_ITEMS_SQL = """
SELECT
  wpe.plan_id                 AS plan_id,
  wpe.id                      AS plan_exercise_id,
  wpe.sequence                AS plan_sequence,
  wpe.exercise_name           AS exercise_name,
  wpe.mapping_id              AS mapping_id,
  wpe.sets_count              AS sets_count,
  wpe.reps_target             AS reps_target,
  wpe.load_target             AS load_target,
  wpe.rpe_target              AS rpe_target,
  wpe.rest_seconds            AS rest_seconds,
  wpe.warmup                  AS warmup,
  wpe.notes                   AS notes,
  wpe.advanced_technique      AS advanced_technique,
  wpe.advanced_technique_applications AS advanced_technique_applications,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.sets_count'))    AS frozen_sets_count,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.reps_target'))   AS frozen_reps_target,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.load_target'))   AS frozen_load_target,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.rpe_target'))    AS frozen_rpe_target,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.engine_version')) AS frozen_engine_version
FROM workout_plan_exercises wpe
JOIN workout_plans wp ON wp.id = wpe.plan_id
WHERE wp.user_id = %s
  AND wpe.plan_id IN ({placeholders})
ORDER BY wpe.plan_id ASC, wpe.sequence ASC
"""

# Recordes e progressoes da pagina: `session_records.HISTORY_SQL_TEMPLATE`
# (uma linha por exposicao dos exercicios da pagina) e `session_records.SETS_SQL`
# (series so das exposicoes comparadas) - a regra e a de `comparability`.

# Series dos exercicios com aplicacao de tecnica das sessoes da pagina (Fase 5,
# PR 4) - lida so quando `SESSION_EXERCISES_SQL` acusou aplicacao.
TECHNIQUE_PAGE_SETS_SQL = technique_sets_sql("a.id IN ({placeholders})")

# Series de origem dos exercicios de UMA sessao (so o detalhe usa).
DETAIL_SETS_SQL = f"""
SELECT
  es.exercise_id  AS exercise_row_id,
  es.id           AS set_id,
  es.set_number   AS set_number,
  es.set_type     AS set_type,
  es.reps         AS reps,
  es.weight_kg    AS weight_kg,
  es.rpe          AS rpe,
  es.rest_seconds AS rest_seconds
FROM exercise_sets es
JOIN exercises e ON e.id = es.exercise_id
JOIN activities a ON a.id = e.activity_id
WHERE a.user_id = %s AND a.id = %s AND {sql_set_confirmed('es.reps_pending')}
ORDER BY e.sequence ASC, es.set_number ASC
"""

# Sessoes executadas por slot do Modelo, para a posicao no ciclo e para o
# "planejado e nao executado". Uma linha por (slot, sessao).
SESSIONS_BY_SLOT_SQL = """
SELECT
  wp.template_slot_sequence AS slot_sequence,
  a.id                      AS activity_id,
  a.local_date              AS local_date,
  a.session_status          AS session_status
FROM activities a
JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
WHERE a.user_id = %s
  AND wp.template_id = %s
  AND wp.template_slot_sequence IS NOT NULL
  AND a.local_date BETWEEN %s AND %s
ORDER BY a.local_date ASC, a.id ASC
"""

# Sessoes da janela resumidas (sem series) - base dos contadores de aderencia.
SESSIONS_SUMMARY_SQL = """
SELECT
  a.id              AS activity_id,
  a.local_date      AS local_date,
  a.session_status  AS session_status,
  a.workout_plan_id AS workout_plan_id,
  wp.template_id    AS template_id
FROM activities a
LEFT JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
"""

# Nomes de slot por (VERSAO, sequence) - PR3. As versoes vem das proprias
# sessoes (`template_version_id`), entao cada sessao le o nome do slot da
# versao que a governava; um statement por pagina, como antes.
TEMPLATE_SLOT_NAMES_SQL = """
SELECT s.version_id AS version_id, s.sequence AS sequence, s.name AS name, s.slot_type AS slot_type
FROM workout_plan_template_slots s
WHERE s.version_id IN ({placeholders})
ORDER BY s.version_id ASC, s.sequence ASC
"""


_METRIC_VERSIONS: Mapping[str, str] = {
    key: metric_version(key)
    for key in (
        "physical_sets",
        "effective_sets_stimulus",
        "effective_sets_fatigue",
        "adherence",
        "session_records",
    )
}


# ---------------------------------------------------------------------------
# Limitacoes declaradas (texto unico, nunca escrito na tela)
# ---------------------------------------------------------------------------

NO_TEMPLATE_LIMITATION = (
    "Nenhum Modelo de Ficha atribuido: nao existe numero de sessoes esperadas para comparar, "
    "entao a aderencia fica sem denominador (nada e estimado no lugar)."
)

NO_PLAN_LINK_LIMITATION = (
    "Sessao sem vinculo com ficha: todos os exercicios entram como extra, porque nao ha "
    "planejado para comparar."
)

NO_PRESCRIPTION_LIMITATION = (
    "Item da ficha sem prescricao de series: qualquer serie de trabalho registrada conta como "
    "concluida, porque nao ha quantidade prescrita para comparar."
)

IN_PROGRESS_LIMITATION = (
    "Sessao em andamento: os itens ainda nao executados nao sao classificados como pulados."
)

IMPORTED_SESSION_LIMITATION = (
    "Sessao sem status final (importada): as series existem, mas ninguem finalizou o treino."
)

RECORDS_LIMITATION = (
    "Recorde e progressao comparados dentro do mesmo exercicio, equipamento e convencao de carga "
    "(mesma regra de /dashboard/exercicios/[id]): trocar de maquina nao conta, assistido conta menos "
    "assistencia como melhora e mais carga com menos reps so progride quando o e1RM decide."
)

EXPECTED_FROM_WEEKLY_SLOTS_LIMITATION = (
    "Sessoes esperadas derivadas do Modelo contando um slot por dia (slot de descanso ocupa o dia "
    "e nao e sessao esperada; mesma convencao do motor de geracao e de /analytics/dashboard-cycle); "
    "o Modelo nao registra datas planejadas."
)


# ---------------------------------------------------------------------------
# Helpers puros (testados sem banco)
# ---------------------------------------------------------------------------


def metric(
    value: Optional[float],
    *,
    unit: Optional[str],
    nature: DataNature,
    version_key: str,
    quality: DataQuality = DataQuality.HIGH,
    limitations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Envelope `Metric` (§8.4). `None` continua `None`: ausencia nunca vira
    zero - "nao da para calcular" e diferente de "foi zero"."""
    return {
        "value": round(value, 2) if isinstance(value, float) else value,
        "unit": unit,
        "nature": nature,
        "metric_version": _METRIC_VERSIONS[version_key],
        "data_quality": quality,
        "limitations": list(limitations or []),
    }


def _int(value: Any) -> Optional[int]:
    number = to_float(value)
    return int(number) if number is not None else None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def prescribed_from_plan_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """PRESCRITO de um item, com a fonte DECLARADA (plano §4, migracao 037).

    Prioridade: snapshot congelado (`prescription_json`) > colunas da linha
    (ficha legada) > `unavailable`. A fonte nunca e adivinhada campo a campo:
    havendo snapshot, ele e a verdade do item inteiro - misturar as duas
    origens produziria uma prescricao que nunca existiu."""
    frozen_sets = _int(row.get("frozen_sets_count"))
    frozen_reps = _text(row.get("frozen_reps_target"))
    has_frozen = frozen_sets is not None or frozen_reps is not None

    if has_frozen:
        return {
            "source": PrescriptionSource.FROZEN_SNAPSHOT,
            "sets_count": frozen_sets,
            "reps_target": frozen_reps,
            "load_target": _text(row.get("frozen_load_target")),
            "rpe_target": _text(row.get("frozen_rpe_target")),
            "rest_seconds": _int(row.get("rest_seconds")),
            "engine_version": _text(row.get("frozen_engine_version")),
        }

    sets_count = _int(row.get("sets_count"))
    reps_target = _text(row.get("reps_target"))
    if sets_count is None and reps_target is None:
        return {
            "source": PrescriptionSource.UNAVAILABLE,
            "sets_count": None,
            "reps_target": None,
            "load_target": None,
            "rpe_target": None,
            "rest_seconds": _int(row.get("rest_seconds")),
            "engine_version": None,
        }
    return {
        "source": PrescriptionSource.PLAN_COLUMNS,
        "sets_count": sets_count,
        "reps_target": reps_target,
        "load_target": _text(row.get("load_target")),
        "rpe_target": _text(row.get("rpe_target")),
        "rest_seconds": _int(row.get("rest_seconds")),
        "engine_version": None,
    }


def derive_item_status(
    *,
    planned_exercise_id: Optional[int],
    performed_exercise_id: Optional[int],
    has_plan_link: bool,
    working_sets: int,
    prescribed_sets: Optional[int],
) -> PlanItemStatus:
    """As cinco regras do §5, em UM lugar.

    - sem vinculo com item da ficha -> `EXTRA` (treino avulso ou exercicio
      adicionado na hora);
    - item da ficha sem nenhuma serie de trabalho -> `SKIPPED`;
    - exercicio executado diferente do prescrito -> `SUBSTITUTED` (a troca
      precede o "quanto fez": o que foi feito nao e o que estava na ficha);
    - series de trabalho abaixo do prescrito -> `INTERRUPTED`;
    - resto -> `COMPLETED`.

    `prescribed_sets=None` (ficha sem prescricao) NUNCA gera `INTERRUPTED`:
    sem quantidade prescrita nao existe "faltou" - o item conta como
    concluido, com a limitacao declarada no payload."""
    if not has_plan_link:
        return PlanItemStatus.EXTRA
    if working_sets <= 0:
        return PlanItemStatus.SKIPPED
    if (
        planned_exercise_id is not None
        and performed_exercise_id is not None
        and int(planned_exercise_id) != int(performed_exercise_id)
    ):
        return PlanItemStatus.SUBSTITUTED
    if prescribed_sets is not None and working_sets < int(prescribed_sets):
        return PlanItemStatus.INTERRUPTED
    return PlanItemStatus.COMPLETED


def technique_indicator(
    plan_row: Optional[Mapping[str, Any]], performed: Optional[Mapping[str, Any]]
) -> Optional[dict[str, Any]]:
    """"Tecnica aplicada X/N" do item (Fase 5, PR 4) - indicador PROPRIO, fora
    da completude (que conta so series de trabalho).

    N = `advanced_technique_applications` da ficha quando a tecnica e de
    APLICACAO (rest-pause, drop, reps alem da falha); FST-7/GVT so preenchem
    series, entao nao ha N. X = linhas de aplicacao registradas no item.
    `equivalent_sets` = soma das fatias do bloco (a mesma que entra no
    volume); `None` quando quem chamou nao leu as series (`engine`).
    Item sem tecnica planejada e sem aplicacao registrada -> `None`."""
    code = _text((plan_row or {}).get("advanced_technique"))
    performed_count = int((performed or {}).get("technique_sets") or 0)
    if not code and performed_count <= 0:
        return None
    applies = bool(code) and SET_TYPE_BY_CODE.get(code) in TECHNIQUE_APPLICATION_SET_TYPES
    planned = _int((plan_row or {}).get("advanced_technique_applications")) if applies else None
    equivalent = (performed or {}).get("technique_equivalent_sets")
    return {
        "code": code,
        "planned_applications": planned,
        "performed_applications": performed_count,
        "equivalent_sets": round(float(equivalent), 2) if equivalent is not None else None,
    }


def _performed_payload(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Um bloco `performed` a partir das linhas executadas de um mesmo item.

    Mais de uma linha acontece quando o exercicio foi registrado duas vezes
    apontando para o mesmo item da ficha: as series SOMAM (foi tudo trabalho
    daquele item) e a linha representativa - a que da nome e posicao - e a de
    maior volume de trabalho."""
    principal = max(
        rows,
        key=lambda row: (int(to_float(row.get("working_sets")) or 0), -(_int(row.get("position_in_session")) or 0)),
    )
    working = sum(int(to_float(row.get("working_sets")) or 0) for row in rows)
    technique = sum(int(to_float(row.get("technique_sets")) or 0) for row in rows)
    equivalents = [row.get("technique_equivalent_sets") for row in rows if row.get("technique_equivalent_sets") is not None]
    total = sum(int(to_float(row.get("total_sets")) or 0) for row in rows)
    reps = sum(int(to_float(row.get("total_reps")) or 0) for row in rows)
    loads = [to_float(row.get("best_weight_kg")) for row in rows]
    loads = [value for value in loads if value is not None]
    rpe_sum = sum(to_float(row.get("rpe_sum")) or 0.0 for row in rows)
    rpe_n = sum(int(to_float(row.get("rpe_n")) or 0) for row in rows)
    return {
        "exercise_row_id": _int(principal.get("exercise_row_id")),
        "exercise_id": _int(principal.get("exercise_id")),
        "name": _text(principal.get("original_name")),
        "position_in_session": _int(principal.get("position_in_session")),
        "working_sets": working,
        "technique_sets": technique,
        "technique_equivalent_sets": sum(float(value) for value in equivalents) if equivalents else None,
        "total_sets": total,
        "total_reps": reps if reps else None,
        "best_load_kg": max(loads) if loads else None,
        "avg_rpe": round(rpe_sum / rpe_n, 2) if rpe_n else None,
        "sets": [],
    }


def build_session_items(
    plan_items: Sequence[Mapping[str, Any]],
    performed_rows: Sequence[Mapping[str, Any]],
    *,
    classify_missing: bool = True,
) -> list[dict[str, Any]]:
    """Junta a FICHA com o EXECUTADO e devolve um item por linha da ficha,
    mais um por exercicio extra.

    `classify_missing=False` (sessao em andamento) omite os itens da ficha que
    ainda nao tem serie nenhuma: chamar de "pulado" um item de um treino que
    ainda esta rolando seria inventar um fato."""
    by_plan_item: dict[int, list[Mapping[str, Any]]] = {}
    extras: list[Mapping[str, Any]] = []
    for row in performed_rows:
        plan_exercise_id = _int(row.get("workout_plan_exercise_id"))
        if plan_exercise_id is None:
            extras.append(row)
        else:
            by_plan_item.setdefault(plan_exercise_id, []).append(row)

    items: list[dict[str, Any]] = []
    linked_ids = set()

    for plan_row in plan_items:
        plan_exercise_id = _int(plan_row.get("plan_exercise_id"))
        rows = by_plan_item.get(plan_exercise_id or -1, [])
        linked_ids.add(plan_exercise_id)
        prescribed = prescribed_from_plan_item(plan_row)
        planned_exercise_id = _int(plan_row.get("mapping_id"))
        performed = _performed_payload(rows) if rows else None
        working_sets = int(performed["working_sets"]) if performed else 0

        if not rows and not classify_missing:
            continue

        status = derive_item_status(
            planned_exercise_id=planned_exercise_id,
            performed_exercise_id=performed["exercise_id"] if performed else None,
            has_plan_link=True,
            working_sets=working_sets,
            prescribed_sets=prescribed["sets_count"],
        )

        limitations: list[str] = []
        if prescribed["source"] is PrescriptionSource.UNAVAILABLE:
            limitations.append(NO_PRESCRIPTION_LIMITATION)

        missing = None
        if prescribed["sets_count"] is not None and working_sets < int(prescribed["sets_count"]):
            missing = int(prescribed["sets_count"]) - working_sets

        substitution = None
        if status is PlanItemStatus.SUBSTITUTED and performed is not None:
            substitution = {
                "planned_exercise_id": planned_exercise_id,
                "planned_name": _text(plan_row.get("exercise_name")),
                "performed_exercise_id": performed["exercise_id"],
                "performed_name": performed["name"],
            }

        items.append(
            {
                "status": status.value,
                "plan_exercise_id": plan_exercise_id,
                "plan_sequence": _int(plan_row.get("plan_sequence")),
                "planned_name": _text(plan_row.get("exercise_name")),
                "prescribed": prescribed,
                "performed": _public_performed(performed),
                "missing_sets": missing,
                "technique": technique_indicator(plan_row, performed),
                "substitution": substitution,
                "is_record": False,
                "notes": _text(plan_row.get("notes")),
                "limitations": limitations,
            }
        )

    # Exercicios executados apontando para um item que nao veio na ficha
    # (ficha apagada/editada depois) entram como EXTRA - nunca somem.
    orphans = [
        row
        for plan_exercise_id, rows in by_plan_item.items()
        if plan_exercise_id not in linked_ids
        for row in rows
    ]

    for row in list(extras) + orphans:
        performed = _performed_payload([row])
        items.append(
            {
                "status": PlanItemStatus.EXTRA.value,
                "plan_exercise_id": None,
                "plan_sequence": None,
                "planned_name": None,
                "prescribed": {
                    "source": PrescriptionSource.UNAVAILABLE,
                    "sets_count": None,
                    "reps_target": None,
                    "load_target": None,
                    "rpe_target": None,
                    "rest_seconds": None,
                    "engine_version": None,
                },
                "performed": _public_performed(performed),
                "missing_sets": None,
                "technique": technique_indicator(None, performed),
                "substitution": None,
                "is_record": False,
                "notes": _text(row.get("notes")),
                "limitations": [],
            }
        )

    items.sort(
        key=lambda item: (
            item["plan_sequence"] if item["plan_sequence"] is not None else 10_000,
            (item["performed"] or {}).get("position_in_session") or 0,
        )
    )
    return items


def _public_performed(performed: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """`performed` sem o equivalente interno (ele sai no bloco `technique`)."""
    if performed is None:
        return None
    return {key: value for key, value in performed.items() if key != "technique_equivalent_sets"}


def count_item_statuses(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Contagem por status derivado (as chaves do contrato, em minusculas)."""
    counts = {status.value.lower(): 0 for status in PlanItemStatus}
    for item in items:
        key = str(item["status"]).lower()
        if key in counts:
            counts[key] += 1
    return counts


def compute_records_and_progressions(
    history_rows: Sequence[Mapping[str, Any]],
    set_rows: Sequence[Mapping[str, Any]],
    activity_ids: Iterable[int],
) -> dict[int, dict[str, int]]:
    """Recordes e progressoes POR SESSAO (contagem de exercicios).

    Regra unica de `comparability` via `session_records`: progressao =
    `compare_exposures` PROGRESSING contra a exposicao anterior de MESMA chave
    (exercicio + equipamento + convencao); recorde = melhor carga direcional da
    chave (`assisted`: menos assistencia) superando todo o historico anterior
    da chave. A primeira exposicao de uma chave nao e recorde."""
    history = parse_history(history_rows)
    computed = compute_session_records(history, set_rows, activity_ids)
    return {
        activity_id: {"records": item["records"], "progressions": item["progressions"]}
        for activity_id, item in computed.items()
    }


def record_exercise_ids(history_rows: Sequence[Mapping[str, Any]]) -> dict[int, set[int]]:
    """Quais EXERCICIOS bateram recorde em cada sessao - a mesma regra de
    `compute_records_and_progressions`, identificando o item em vez de
    conta-lo. E o que o detalhe usa para marcar a linha (`is_record`)."""
    return _record_exercise_ids(parse_history(history_rows))


def expected_sessions_in_window(
    *, window_days: int, training_slots: int, cycle_length_days: Optional[int]
) -> Optional[float]:
    """Sessoes esperadas na janela, derivadas do Modelo atribuido.

    `cycle_length_days` vem de `volume_service.template_cycle_length_days`
    (um slot = um dia, a convencao do motor de geracao e da metodologia) - a
    mesma de `/analytics/dashboard-cycle`; esta funcao NAO cria uma segunda.
    Sem Modelo (`training_slots = 0`) devolve `None`: nao existe denominador
    a inventar (§6)."""
    if not training_slots or not cycle_length_days:
        return None
    cycles = float(window_days) / float(cycle_length_days)
    return round(cycles * training_slots, 2)


def adherence_rate(completed: Optional[float], expected: Optional[float]) -> Optional[float]:
    """Sessoes realizadas / esperadas. `None` quando nao ha denominador."""
    if expected is None or not expected:
        return None
    if completed is None:
        return None
    return round(float(completed) / float(expected), 4)


# ---------------------------------------------------------------------------
# Ciclo do Modelo atribuido
# ---------------------------------------------------------------------------


def resolve_current_cycle(user_id: int, *, as_of: Optional[date] = None) -> Optional[dict[str, Any]]:
    """Posicao no ciclo do Modelo atribuido: slots, tamanho do ciclo, o que ja
    rodou, o proximo e o que falta.

    REUSA as funcoes que ja existem - `_resolve_latest_assigned_template_id`
    (Modelo mais recentemente atribuido), `get_template_if_assigned`
    (validacao de posse) e `resolve_next_slot` (wraparound) - e o MESMO
    criterio de `done_sequences` de `GET /analytics/dashboard-cycle`: percorre
    as fichas salvas do usuario+modelo do mais recente para tras, ate repetir
    um `sequence` (sinal de que o ciclo anterior comecou ali).

    Devolve `None` quando nao ha Modelo atribuido - e o unico caso em que
    `window=cycle` nao tem resposta possivel."""
    from ...analytics import _resolve_latest_assigned_template_id
    from ...db import transaction
    from ...workout_plan_templates_engine import get_template_if_assigned, resolve_next_slot

    user_id = require_user_id(user_id)
    template_id = _resolve_latest_assigned_template_id(user_id)
    if template_id is None:
        return None
    # `as_of` (PR3): janela HISTORICA - slots e tamanho do ciclo da versao do
    # Modelo no fim dela (mesmos statements: `_get_template` resolve a versao
    # da data no cabecalho). Sem `as_of`, a vigente.
    template = get_template_if_assigned(template_id, user_id, as_of=as_of)
    if template is None:
        return None

    slots = sorted(template.get("slots") or [], key=lambda slot: slot["sequence"])
    if not slots:
        return None

    def read_plan_rows() -> list[dict[str, Any]]:
        with transaction() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT template_slot_sequence
                    FROM workout_plans
                    WHERE user_id = %s AND template_id = %s AND template_slot_sequence IS NOT NULL
                    ORDER BY id DESC
                    """,
                    (user_id, template_id),
                )
                return list(cursor.fetchall() or [])

    # Mesma leitura do projetado de `muscle_summary_service` (mesma chamada GET):
    # uma ida ao banco por requisicao (PERF-01, `request_memo`).
    plan_rows = request_memo.memoize(("workout_plan_slot_sequences", user_id, template_id), read_plan_rows)

    done: set[Any] = set()
    for row in plan_rows:
        sequence = row.get("template_slot_sequence")
        if sequence in done:
            break
        done.add(sequence)
        if len(done) >= len(slots):
            break

    next_slot = resolve_next_slot(template, user_id)
    next_sequence = next_slot.get("sequence") if next_slot else None
    all_sequences = {slot["sequence"] for slot in slots}

    return {
        "template_id": template_id,
        "template_name": template.get("name"),
        "slots": slots,
        # Um slot = um dia (`template_cycle_length_days`, a convencao do
        # motor de geracao). Ate 2026-10-02 era `7 x slots`: o Modelo de 8
        # slots virava um ciclo de 56 dias, a janela `window=cycle` ficava 7x
        # maior e as sessoes esperadas 7x menores (aderencia de 500%+).
        "cycle_length_days": template_cycle_length_days(len(slots)),
        "done_sequences": done,
        "pending_sequences": all_sequences - done,
        "next_sequence": next_sequence,
    }


def resolve_cycle_length_days(user_id: int) -> Optional[int]:
    """Tamanho do ciclo para `window=cycle` (`time_windows.resolve_window`).

    E ESTE endpoint que destrava a janela por ciclo para todo o hub: ate a
    F1.1 nenhum servico sabia resolver o tamanho do ciclo, e por isso
    `cycle` respondia 400 em todos os endpoints v3."""
    try:
        cycle = resolve_current_cycle(user_id)
    except Exception as exc:  # noqa: BLE001 - a janela nunca pode virar 500
        log_event("dashboard.cycle_unavailable", user_id=user_id, error=str(exc))
        return None
    return cycle["cycle_length_days"] if cycle else None


def _training_slots(slots: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Slots que representam um TREINO. Slot de descanso (`slot_type =
    'descanso'`) faz parte do ciclo, mas nao e sessao esperada - conta-lo
    como esperada baixaria a aderencia de quem seguiu o Modelo a risca."""
    return [slot for slot in slots if str(slot.get("slot_type") or "normal").lower() != "descanso"]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _in_clause(sql: str, values: Sequence[Any]) -> str:
    return sql.format(placeholders=",".join(["%s"] * len(values)))


def _template_slot_names(conn: Any, version_ids: set[int]) -> dict[tuple[int, int], str]:
    """`(version_id, sequence) -> nome do slot` (PR3)."""
    if not version_ids:
        return {}
    ordered = sorted(version_ids)
    rows = _rows(conn, _in_clause(TEMPLATE_SLOT_NAMES_SQL, ordered), tuple(ordered))
    return {
        (int(row["version_id"]), int(row["sequence"])): row.get("name")
        for row in rows
        if row.get("version_id") is not None and row.get("sequence") is not None
    }


def _session_quality(
    status: SessionExecutionStatus, has_plan_link: bool
) -> tuple[DataQuality, list[str]]:
    """Qualidade da leitura DAQUELA sessao e o que ela impede de concluir."""
    limitations: list[str] = []
    quality = DataQuality.HIGH
    if status is SessionExecutionStatus.IMPORTED:
        quality = DataQuality.MEDIUM
        limitations.append(IMPORTED_SESSION_LIMITATION)
    elif status is SessionExecutionStatus.IN_PROGRESS:
        quality = DataQuality.MEDIUM
        limitations.append(IN_PROGRESS_LIMITATION)
    if not has_plan_link:
        # MEDIUM, nao LOW: a sessao aconteceu e as series sao reais - o que
        # falta e o planejado para comparar (mesma leitura da regra de
        # qualidade `SESSIONS_NOT_LINKED_TO_PLAN`, severidade ATTENTION).
        if quality is DataQuality.HIGH:
            quality = DataQuality.MEDIUM
        limitations.append(NO_PLAN_LINK_LIMITATION)
    return quality, limitations


def _session_payload(
    row: Mapping[str, Any],
    *,
    totals: Mapping[str, Any],
    effective: Mapping[str, Any],
    counts: Mapping[str, int],
    records: Mapping[str, int],
    slot_names: Mapping[tuple[int, int], str],
    lineage_roots: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    status = normalize_session_status(row.get("session_status"))
    plan_id = _int(row.get("workout_plan_id"))
    template_id = _int(row.get("template_id"))
    version_id = _int(row.get("template_version_id"))
    slot_sequence = _int(row.get("template_slot_sequence"))
    quality, limitations = _session_quality(status, plan_id is not None)

    rpe_n = int(to_float(totals.get("rpe_n")) or 0)
    rpe_sum = to_float(totals.get("rpe_sum")) or 0.0
    stimulus = to_float(effective.get("stimulus_sets"))
    fatigue = to_float(effective.get("fatigue_sets"))

    slot_name = (
        slot_names.get((version_id, slot_sequence))
        if plan_id is not None and version_id is not None and slot_sequence is not None
        else None
    )
    plan_link = None
    if plan_id is not None:
        plan_link = {
            "workout_plan_id": plan_id,
            "plan_name": _text(row.get("plan_name")),
            "template_id": template_id,
            "template_slot_sequence": slot_sequence,
            "template_slot_name": slot_name,
        }
    # Grupo da tela: identidade de slot do dono (`SessionSlot.key` = Modelo +
    # posicao), nunca o nome; nomes so como rotulo (o do Modelo e o atual).
    group = session_group_of(
        workout_plan_id=plan_id,
        template_id=template_id if plan_id is not None else None,
        slot_sequence=slot_sequence if plan_id is not None else None,
        slot_name=slot_name,
        template_name=_text(row.get("template_name")),
        lineage_roots=lineage_roots,
    )

    return {
        "activity_id": int(row["activity_id"]),
        "local_date": to_date(row.get("local_date")),
        "started_at": to_datetime(row.get("trained_at")),
        "name": _text(row.get("activity_name")),
        "session_status": status.value,
        # M97 F4: sessao de deload sinalizada (origem e rotulo do dono).
        "deload_source": deload_source_value(row.get("deload_source")),
        "deload_label": deload_source_label(row.get("deload_source")),
        "duration_seconds": _int(row.get("duration_seconds")),
        "exercises_count": int(to_float(totals.get("exercises_count")) or 0),
        "physical_sets": metric(
            float(to_float(totals.get("physical_sets")) or 0.0),
            unit="series",
            nature=DataNature.OBSERVED,
            version_key="physical_sets",
        ),
        "effective_sets_stimulus": metric(
            stimulus,
            unit="series",
            nature=DataNature.DERIVED,
            version_key="effective_sets_stimulus",
        ),
        "effective_sets_fatigue": metric(
            fatigue,
            unit="series",
            nature=DataNature.DERIVED,
            version_key="effective_sets_fatigue",
        ),
        # Media simples do RPE das series de TRABALHO (a mesma fronteira de
        # `physical_sets`, por isso a versao e a mesma). `None` sem nenhum RPE
        # registrado - nunca 0, que seria "esforco zero".
        "avg_rpe": metric(
            round(rpe_sum / rpe_n, 2) if rpe_n else None,
            unit="RPE",
            nature=DataNature.OBSERVED,
            version_key="physical_sets",
        ),
        "records": metric(
            float(records.get("records", 0)),
            unit="exercicios",
            nature=DataNature.DERIVED,
            version_key="session_records",
            limitations=[RECORDS_LIMITATION],
        ),
        "progressions": metric(
            float(records.get("progressions", 0)),
            unit="exercicios",
            nature=DataNature.DERIVED,
            version_key="session_records",
            limitations=[RECORDS_LIMITATION],
        ),
        "item_status_counts": dict(counts),
        "plan_link": plan_link,
        "group": group,
        "data_quality": quality,
        "limitations": limitations,
    }


def _collect_page_details(
    conn: Any,
    *,
    user_id: int,
    session_rows: Sequence[Mapping[str, Any]],
    max_local_date: date,
) -> dict[str, Any]:
    """TODAS as consultas de apoio de uma pagina de sessoes, cada dimensao uma
    vez so (o oposto de uma consulta por sessao)."""
    activity_ids = [int(row["activity_id"]) for row in session_rows]
    if not activity_ids:
        return {
            "totals": {},
            "effective": {},
            "items": {},
            "records": {},
            "record_exercise_ids": {},
            "slot_names": {},
            "lineage_roots": {},
        }

    totals_rows = _rows(
        conn, _in_clause(SESSION_TOTALS_SQL, activity_ids), (user_id, *activity_ids)
    )
    effective_rows = _rows(
        conn, _in_clause(SESSION_EFFECTIVE_SQL, activity_ids), (user_id, *activity_ids)
    )
    exercise_rows = _rows(
        conn, _in_clause(SESSION_EXERCISES_SQL, activity_ids), (user_id, *activity_ids)
    )

    plan_ids = sorted(
        {
            int(row["workout_plan_id"])
            for row in session_rows
            if row.get("workout_plan_id") is not None
        }
    )
    plan_item_rows = (
        _rows(conn, _in_clause(PLAN_ITEMS_SQL, plan_ids), (user_id, *plan_ids)) if plan_ids else []
    )
    plan_items_by_plan: dict[int, list[Mapping[str, Any]]] = {}
    for row in plan_item_rows:
        plan_items_by_plan.setdefault(int(row["plan_id"]), []).append(row)

    # Aplicacoes de tecnica da pagina (Fase 5, PR 4): UMA leitura, so quando a
    # pagina tem aplicacao. Soma a fatia de cada uma ao estimulo/fadiga da
    # sessao e da ao item o equivalente da tecnica aplicada.
    if any(int(to_float(row.get("technique_sets")) or 0) > 0 for row in exercise_rows):
        technique = TechniqueSets(
            _rows(
                conn,
                _in_clause(TECHNIQUE_PAGE_SETS_SQL, activity_ids),
                (user_id, *activity_ids),
            ),
            new_catalog(),
        )
        effective_by_activity = {int(row["activity_id"]): dict(row) for row in effective_rows}
        for row, units in technique.applications():
            entry = effective_by_activity.setdefault(
                int(row["activity_id"]),
                {"activity_id": int(row["activity_id"]), "stimulus_sets": 0.0, "fatigue_sets": 0.0},
            )
            entry["stimulus_sets"] = (to_float(entry.get("stimulus_sets")) or 0.0) + (
                to_float(row.get("stimulus_weight")) or 0.0
            ) * units
            entry["fatigue_sets"] = (to_float(entry.get("fatigue_sets")) or 0.0) + (
                to_float(row.get("fatigue_weight")) or 0.0
            ) * units
        effective_rows = list(effective_by_activity.values())
        equivalent_by_row = technique.application_units_by_exercise_row()
        exercise_rows = [
            {**row, "technique_equivalent_sets": equivalent_by_row.get(int(row["exercise_row_id"]), 0.0)}
            if int(to_float(row.get("technique_sets")) or 0) > 0
            else row
            for row in exercise_rows
        ]

    performed_by_activity: dict[int, list[Mapping[str, Any]]] = {}
    for row in exercise_rows:
        performed_by_activity.setdefault(int(row["activity_id"]), []).append(row)

    exercise_ids = sorted(
        {int(row["exercise_id"]) for row in exercise_rows if row.get("exercise_id") is not None}
    )
    history_rows: list[dict[str, Any]] = []
    set_rows: list[dict[str, Any]] = []
    if exercise_ids:
        with conn.cursor() as cursor:
            convention_expr = load_convention_select_expr(cursor)
        history_rows = _rows(
            conn,
            _in_clause(history_sql(convention_expr), exercise_ids),
            (user_id, max_local_date, *exercise_ids),
        )
        compared_row_ids = exposures_to_compare(parse_history(history_rows), activity_ids)
        if compared_row_ids:
            set_rows = _rows(
                conn, _in_clause(SESSION_RECORD_SETS_SQL, compared_row_ids), (user_id, *compared_row_ids)
            )

    items_by_activity: dict[int, list[dict[str, Any]]] = {}
    for row in session_rows:
        activity_id = int(row["activity_id"])
        status = normalize_session_status(row.get("session_status"))
        plan_id = _int(row.get("workout_plan_id"))
        items_by_activity[activity_id] = build_session_items(
            plan_items_by_plan.get(plan_id or -1, []),
            performed_by_activity.get(activity_id, []),
            classify_missing=status in EXECUTED_SESSION_STATUSES,
        )

    version_ids = {
        int(row["template_version_id"]) for row in session_rows if row.get("template_version_id") is not None
    }

    return {
        "totals": {int(row["activity_id"]): row for row in totals_rows},
        "effective": {int(row["activity_id"]): row for row in effective_rows},
        "items": items_by_activity,
        "records": compute_records_and_progressions(history_rows, set_rows, activity_ids),
        "record_exercise_ids": record_exercise_ids(history_rows),
        "slot_names": _template_slot_names(conn, version_ids),
        # Copia de Modelo (090) cai no grupo da origem: a raiz vem do dono.
        "lineage_roots": load_lineage_roots(
            lambda sql, params: _rows(conn, sql, params),
            (_int(row.get("template_id")) for row in session_rows),
        ),
    }


def build_workouts(
    conn: Any,
    *,
    user_id: int,
    window: WindowInfo,
    limit: int = DEFAULT_WORKOUTS_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """`GET /analytics/dashboard/workouts` - a lista de sessoes da janela.

    Sempre 200: janela sem nenhuma sessao responde `items: []` com
    `data_quality=INSUFFICIENT` (nunca 404, nunca zeros inventados)."""
    user_id = require_user_id(user_id)
    limit = max(1, min(int(limit), MAX_WORKOUTS_LIMIT))
    offset = max(0, int(offset))

    total_rows = _rows(conn, SESSIONS_COUNT_SQL, (user_id, window.date_from, window.date_to))
    total = int(to_float((total_rows[0] if total_rows else {}).get("total")) or 0)

    session_rows = _rows(
        conn,
        with_trained_at(SESSIONS_PAGE_SQL, window.timezone),
        (user_id, window.date_from, window.date_to, limit, offset),
    )
    details = _collect_page_details(
        conn, user_id=user_id, session_rows=session_rows, max_local_date=window.date_to
    )

    items = [
        _session_payload(
            row,
            totals=details["totals"].get(int(row["activity_id"]), {}),
            effective=details["effective"].get(int(row["activity_id"]), {}),
            counts=count_item_statuses(details["items"].get(int(row["activity_id"]), [])),
            records=details["records"].get(int(row["activity_id"]), {}),
            slot_names=details["slot_names"],
            lineage_roots=details["lineage_roots"],
        )
        for row in session_rows
    ]

    log_event(
        "dashboard.computed",
        endpoint="workouts",
        user_id=user_id,
        window=window.kind.value,
        sessions=len(items),
        total=total,
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": _envelope_quality(items),
        "limitations": _envelope_limitations(items),
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }


def _envelope_quality(items: Sequence[Mapping[str, Any]]) -> DataQuality:
    """Confianca da RESPOSTA: a PIOR qualidade entre as sessoes. Sem sessao
    nenhuma -> `INSUFFICIENT` ("nao ha o que concluir" != "esta tudo bem")."""
    if not items:
        return DataQuality.INSUFFICIENT
    order = [DataQuality.HIGH, DataQuality.MEDIUM, DataQuality.LOW, DataQuality.INSUFFICIENT]
    worst = DataQuality.HIGH
    for item in items:
        quality = item["data_quality"]
        if order.index(quality) > order.index(worst):
            worst = quality
    return worst


def _envelope_limitations(items: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: list[str] = []
    for item in items:
        for limitation in item.get("limitations", []):
            if limitation not in seen:
                seen.append(limitation)
    return seen


def build_workout_detail(
    conn: Any,
    activity_id: int,
    *,
    user_id: int,
    window: WindowInfo,
    sets_limit: int = MAX_DETAIL_SETS,
) -> Optional[dict[str, Any]]:
    """`GET /analytics/dashboard/workouts/{activity_id}` - prescrito x
    executado item a item, com as series de origem.

    `None` quando a sessao nao existe OU nao e do usuario: o endpoint devolve
    404 nos dois casos, entao nenhum id alheio e confirmado (IDOR)."""
    user_id = require_user_id(user_id)
    sets_limit = max(1, min(int(sets_limit), MAX_DETAIL_SETS))

    session_rows = _rows(
        conn,
        with_trained_at(SESSION_BY_ID_SQL, window.timezone),
        (user_id, int(activity_id)),
    )
    if not session_rows:
        return None
    session_row = session_rows[0]

    details = _collect_page_details(
        conn,
        user_id=user_id,
        session_rows=[session_row],
        max_local_date=to_date(session_row.get("local_date")) or window.date_to,
    )
    items = details["items"].get(int(session_row["activity_id"]), [])

    set_rows = _rows(conn, DETAIL_SETS_SQL, (user_id, int(activity_id)))
    sets_by_exercise: dict[int, list[dict[str, Any]]] = {}
    for row in set_rows:
        exercise_row_id = _int(row.get("exercise_row_id"))
        if exercise_row_id is None:
            continue
        set_type = normalize_set_type(row.get("set_type"))
        sets_by_exercise.setdefault(exercise_row_id, []).append(
            {
                "set_id": int(row["set_id"]),
                "set_number": _int(row.get("set_number")),
                "set_type": set_type,
                # Mesma fronteira do SQL de `working_sets`: aquecimento, serie
                # de ajuste e (Fase 5, PR 4) aplicacao de tecnica nao sao
                # serie de trabalho.
                "is_working": set_type not in PREPARATORY_SET_TYPES
                and set_type not in TECHNIQUE_APPLICATION_SET_TYPES,
                "reps": _int(row.get("reps")),
                "weight_kg": to_float(row.get("weight_kg")),
                "rpe": to_float(row.get("rpe")),
                "rest_seconds": _int(row.get("rest_seconds")),
            }
        )

    remaining = sets_limit
    truncated = False
    for item in items:
        performed = item.get("performed")
        if not performed or performed.get("exercise_row_id") is None:
            continue
        rows_for_item = sets_by_exercise.get(int(performed["exercise_row_id"]), [])
        if remaining <= 0:
            truncated = truncated or bool(rows_for_item)
            continue
        performed["sets"] = rows_for_item[:remaining]
        if len(rows_for_item) > remaining:
            truncated = True
        remaining -= len(performed["sets"])

    recordes = details["record_exercise_ids"].get(int(session_row["activity_id"]), set())
    for item in items:
        performed = item.get("performed")
        if performed and performed.get("exercise_id") in recordes:
            item["is_record"] = True

    records_for_session = details["records"].get(int(session_row["activity_id"]), {})
    session = _session_payload(
        session_row,
        totals=details["totals"].get(int(session_row["activity_id"]), {}),
        effective=details["effective"].get(int(session_row["activity_id"]), {}),
        counts=count_item_statuses(items),
        records=records_for_session,
        slot_names=details["slot_names"],
        lineage_roots=details["lineage_roots"],
    )

    log_event(
        "dashboard.computed",
        endpoint="workout_detail",
        user_id=user_id,
        activity_id=int(activity_id),
        items=len(items),
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": session["data_quality"],
        "limitations": session["limitations"],
        "session": session,
        "items": items,
        "sets_limit": sets_limit,
        "sets_truncated": truncated,
        "notes": _text(session_row.get("notes")),
    }


def build_adherence(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    """`GET /analytics/dashboard/adherence` - o bloco "Consistencia" (§7).

    Sem Modelo atribuido a resposta EXISTE, com os contadores observados
    (sessoes realizadas, com e sem ficha) e `rate: null` +
    `data_quality=LOW`: o denominador so pode vir do Modelo, e inventa-lo
    seria responder "cumpri o planejado?" sem plano (§6)."""
    user_id = require_user_id(user_id)

    summary_rows = _rows(conn, SESSIONS_SUMMARY_SQL, (user_id, window.date_from, window.date_to))
    executed = [
        row
        for row in summary_rows
        if normalize_session_status(row.get("session_status")) in EXECUTED_SESSION_STATUSES
    ]
    completed = len(executed)
    planned = len([row for row in executed if row.get("workout_plan_id") is not None])
    unplanned = completed - planned

    try:
        # PR3: o Modelo e lido na versao do FIM da janela (sessoes esperadas e
        # slots de uma janela antiga saem da estrutura da epoca).
        cycle = resolve_current_cycle(user_id, as_of=window.date_to)
    except Exception as exc:  # noqa: BLE001 - o bloco de ciclo e opcional
        log_event("dashboard.cycle_unavailable", user_id=user_id, error=str(exc))
        cycle = None

    limitations: list[str] = []
    slots_payload: list[dict[str, Any]] = []
    next_slot_payload: Optional[dict[str, Any]] = None
    expected: Optional[float] = None
    cycle_completed: Optional[int] = None
    cycle_expected: Optional[int] = None
    template_id = None
    template_name = None
    cycle_length_days = None

    if cycle is None:
        limitations.append(NO_TEMPLATE_LIMITATION)
        quality = DataQuality.LOW
    else:
        template_id = cycle["template_id"]
        template_name = cycle["template_name"]
        cycle_length_days = cycle["cycle_length_days"]
        training_slots = _training_slots(cycle["slots"])
        expected = expected_sessions_in_window(
            window_days=window.days,
            training_slots=len(training_slots),
            cycle_length_days=cycle_length_days,
        )
        limitations.append(EXPECTED_FROM_WEEKLY_SLOTS_LIMITATION)
        quality = DataQuality.HIGH if completed else DataQuality.INSUFFICIENT

        slot_sessions = _rows(
            conn,
            SESSIONS_BY_SLOT_SQL,
            (user_id, template_id, window.date_from, window.date_to),
        )
        last_by_slot: dict[int, Mapping[str, Any]] = {}
        for row in slot_sessions:
            sequence = _int(row.get("slot_sequence"))
            if sequence is None:
                continue
            if normalize_session_status(row.get("session_status")) in EXECUTED_SESSION_STATUSES:
                last_by_slot[sequence] = row

        for slot in cycle["slots"]:
            sequence = int(slot["sequence"])
            if sequence == cycle["next_sequence"]:
                status = "next"
            elif sequence in cycle["done_sequences"]:
                status = "done"
            else:
                status = "pending"
            last = last_by_slot.get(sequence)
            payload = {
                "sequence": sequence,
                "name": slot.get("name"),
                "slot_type": slot.get("slot_type"),
                "weekday": slot.get("weekday"),
                "status": status,
                "last_activity_id": _int((last or {}).get("activity_id")),
                "last_local_date": to_date((last or {}).get("local_date")),
            }
            slots_payload.append(payload)
            if status == "next":
                next_slot_payload = payload

        cycle_expected = len(training_slots)
        cycle_completed = len(
            [slot for slot in training_slots if int(slot["sequence"]) in cycle["done_sequences"]]
        )

    missed = None
    if expected is not None:
        missed = max(0.0, round(expected - planned, 2))

    quality_limitations = list(limitations)

    log_event(
        "dashboard.computed",
        endpoint="adherence",
        user_id=user_id,
        window=window.kind.value,
        has_template=cycle is not None,
        completed=completed,
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": quality,
        "limitations": quality_limitations,
        "has_template": cycle is not None,
        "template_id": template_id,
        "template_name": template_name,
        "cycle_length_days": cycle_length_days,
        "slots": slots_payload,
        "next_slot": next_slot_payload,
        "expected_sessions": metric(
            expected,
            unit="sessoes",
            nature=DataNature.PLANNED,
            version_key="adherence",
            quality=DataQuality.HIGH if expected is not None else DataQuality.LOW,
            limitations=[] if expected is not None else [NO_TEMPLATE_LIMITATION],
        ),
        "completed_sessions": metric(
            float(completed), unit="sessoes", nature=DataNature.OBSERVED, version_key="adherence"
        ),
        "planned_sessions": metric(
            float(planned), unit="sessoes", nature=DataNature.OBSERVED, version_key="adherence"
        ),
        "unplanned_sessions": metric(
            float(unplanned), unit="sessoes", nature=DataNature.OBSERVED, version_key="adherence"
        ),
        "missed_sessions": metric(
            missed,
            unit="sessoes",
            nature=DataNature.DERIVED,
            version_key="adherence",
            quality=DataQuality.HIGH if missed is not None else DataQuality.LOW,
            limitations=[] if missed is not None else [NO_TEMPLATE_LIMITATION],
        ),
        "rate": metric(
            adherence_rate(float(planned), expected),
            unit=None,
            nature=DataNature.DERIVED,
            version_key="adherence",
            quality=DataQuality.HIGH if expected is not None else DataQuality.LOW,
            limitations=[] if expected is not None else [NO_TEMPLATE_LIMITATION],
        ),
        "cycle_rate": metric(
            adherence_rate(
                float(cycle_completed) if cycle_completed is not None else None,
                float(cycle_expected) if cycle_expected else None,
            ),
            unit=None,
            nature=DataNature.DERIVED,
            version_key="adherence",
            quality=DataQuality.HIGH if cycle_expected else DataQuality.LOW,
            limitations=[] if cycle_expected else [NO_TEMPLATE_LIMITATION],
        ),
    }


__all__ = [
    "DEFAULT_WORKOUTS_LIMIT",
    "MAX_DETAIL_SETS",
    "MAX_WORKOUTS_LIMIT",
    "adherence_rate",
    "build_adherence",
    "build_session_items",
    "build_workout_detail",
    "build_workouts",
    "compute_records_and_progressions",
    "count_item_statuses",
    "derive_item_status",
    "expected_sessions_in_window",
    "prescribed_from_plan_item",
    "record_exercise_ids",
    "resolve_current_cycle",
    "resolve_cycle_length_days",
]

"""Motor de geracao no Novo Dashboard - `GET /analytics/dashboard/engine` e
`GET /analytics/dashboard/engine/plans/{plan_id}` (plano §4 "Engine runs",
§10, §17 F3.1).

A pergunta que NENHUMA outra tela do hub responde: **"por que a minha ficha
saiu assim, e o que eu fiz com ela?"**. Tres colunas por item, e a fronteira
entre elas e o contrato inteiro desta tela:

| Coluna       | Fonte                                                        |
|--------------|--------------------------------------------------------------|
| PLANEJADO    | `workout_plan_template_slot_items` (o Modelo), ou o ORCAMENTO |
|              | do grupo (`generation_audit.activation`) no modo              |
|              | `percent_budget`, que nao nomeia exercicio nenhum             |
| GERADO       | `workout_plan_exercises` + `prescription_json` (migracao 037) |
|              | + o PORQUE: `selection`, `safety_clamps`, `substitutions`,    |
|              | `fallbacks` do `generation_audit`, e `progression_decisions`  |
| EXECUTADO    | `exercises`/`exercise_sets` pelo vinculo                      |
|              | `exercises.workout_plan_exercise_id` (migracao 026)           |

## Duas fontes para o `generation_audit`, com PREFERENCIA e fallback declarado

`generation_runs` (migracao 054, M16) so existe a partir de 2026-09-26: a
tabela nasceu vazia e nao houve backfill possivel (a data da GERACAO nao
existe para o passado, e `request_json` morreu com o TTL de 6h dos jobs).
Fichas geradas antes disso nao tem linha ali - mas tem o bloco ecoado em
`workout_plans.raw_payload.generation_audit`, que continua sendo gravado
(duplicacao DELIBERADA, declarada no cabecalho da 054).

Entao a leitura e: **`generation_runs` primeiro, `raw_payload` depois**, e a
fonte usada vai no payload (`EngineAuditSource`). Nao e detalhe interno: do
`raw_payload` nao sai a data da geracao, nem o `job_id`, nem o pedido - e uma
tela que exibisse "gerada em -" sem dizer por que seria indistinguivel de
defeito. As tres limitacoes possiveis estao em `AUDIT_*_LIMITATION`, no
padrao de `insight_rules.context_limitation`: a frase muda com o ESTADO, e
nenhuma delas troca ausencia por zero.

## Custo: contagem FIXA de statements, nada por item

O detalhe de uma ficha custa **6 consultas**, nenhuma em laco:

1. cabecalho da ficha (+ Modelo, slot e o `generation_audit` do `raw_payload`
   por `JSON_EXTRACT`, para o fallback nao custar uma segunda ida);
2. a linha de `generation_runs` da ficha (a mais recente);
3. os itens da ficha (`workout_plan_exercises` + as 5 chaves do snapshot
   congelado, exatamente como `adherence_service` as le);
4. os itens do SLOT do Modelo;
5. o executado de TODAS as sessoes da ficha, ja agregado por exercicio;
6. as decisoes de progressao dos itens, num unico `IN (...)`.

Mais uma setima, condicional, so quando a auditoria cita exercicios que nao
estao na ficha (os candidatos RECUSADOS): um `IN (...)` em
`exercise_catalog` para dar nome a eles. Ela nunca vira uma consulta por
candidato.

A lista da janela custa **2**: as linhas de `generation_runs` e as fichas com
`generation_audit` no `raw_payload` que NAO tem linha na tabela. As contagens
de `selection`/`safety_clamps`/`substitutions`/`fallbacks` saem de
`JSON_LENGTH` no banco - o `audit_json` inteiro nunca trafega na lista (§15,
teto de 1 MB da RDS Data API).

## O que este modulo NAO reimplementa

As cinco regras de status do §5 (`COMPLETED/INTERRUPTED/SKIPPED/SUBSTITUTED/
EXTRA`), o prescrito com fonte declarada e a juncao ficha x executado vem
INTEIRAS de `adherence_service` (`build_session_items`,
`prescribed_from_plan_item`, `count_item_statuses`). Copiar aqui criaria duas
respostas possiveis para "este item foi pulado?" - exatamente a dupla verdade
que o §18 descartou.

## `template_generation_logs` nao entra aqui

Aquela tabela (migracao 038) audita a geracao de um **MODELO** pela IA de
periodizacao. Esta tela audita a geracao de uma **FICHA** a partir de um
Modelo que ja existe, pelos motores deterministicos. Objetos diferentes; elas
se encontram so em `template_id`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ...domain.free_exercise_item import is_free_exercise_item
from ...observability import log_event
from ...template_versions import plan_version_expr
from ...set_types import sql_is_technique_application, sql_not_preparatory, sql_set_confirmed
from .adherence_service import (
    PLAN_ITEMS_SQL,
    build_session_items,
    count_item_statuses,
)
from .coercion import to_date, to_datetime, to_float
from .contracts import (
    DataQuality,
    EngineAuditSource,
    EnginePlannedSource,
    EngineReasonKind,
    WindowInfo,
)
from .identity import require_user_id
from .metrics_registry import metric_version
from ...db_helpers import rows as _rows

# Teto de geracoes devolvidas pela lista. Uma geracao por dia durante um ano
# cabe folgado; o teto existe pelo limite de 1 MB da RDS Data API (§15) e e
# DECLARADO no payload (`limit`/`total`), nunca um corte silencioso.
DEFAULT_RUNS_LIMIT = 50
MAX_RUNS_LIMIT = 200

# Teto de candidatos RECUSADOS exibidos. O `selection` de uma geracao do Modo
# A pode ter dezenas de entradas por grupo muscular (cada candidato avaliado),
# e a tela responde "por que este exercicio, e nao outro" com as primeiras -
# a lista inteira e ruido, nao explicacao.
MAX_REJECTIONS = 60

_METRIC_VERSIONS = {
    "generation_audit": metric_version("generation_audit"),
    "engine_plan_diff": metric_version("engine_plan_diff"),
    "adherence": metric_version("adherence"),
}


# ---------------------------------------------------------------------------
# Limitacoes - a frase VERDADEIRA para cada estado (padrao de
# `insight_rules.context_limitation`: o texto e escolhido pelo ESTADO, e
# ausencia nunca vira zero)
# ---------------------------------------------------------------------------

AUDIT_FROM_RAW_PAYLOAD_LIMITATION = (
    "Auditoria lida de `workout_plans.raw_payload` (ficha anterior à tabela `generation_runs`, "
    "migração 054): o conteúdo do bloco é o mesmo, mas a data da geração, o job que a produziu e "
    "o pedido que a originou não existem para esta ficha."
)
AUDIT_UNAVAILABLE_LIMITATION = (
    "Esta ficha não tem bloco de auditoria de geração: ela foi criada à mão ou salva sem o "
    "payload do motor. O que ela prescreve continua visível; por que o motor escolheu isso, não."
)
AUDIT_DRAFT_NOT_SAVED_LIMITATION = (
    "Geração cujo rascunho não virou ficha: não há prescrição salva nem execução para comparar — "
    "só o que o motor produziu no momento."
)
NO_TEMPLATE_LIMITATION = (
    "Ficha sem Modelo de Ficha vinculado: a coluna \"planejado\" fica vazia porque não há um "
    "plano do qual ela pudesse vir — não porque o Modelo não pediu nada."
)
BUDGET_MODE_LIMITATION = (
    "Modelo no modo `percent_budget`: ele planeja VOLUME por grupo muscular, não exercícios. A "
    "coluna \"planejado\" mostra o orçamento do grupo; quem escolheu cada exercício foi o motor."
)
NO_PROGRESSION_DECISION_LIMITATION = (
    "Um ou mais itens não têm decisão de progressão vinculada (`progression_decisions.workout_"
    "plan_exercise_id`): isso significa que a decisão não foi gravada com o vínculo, não que a "
    "carga tenha sido mantida."
)
NOT_EXECUTED_LIMITATION = (
    "Esta ficha ainda não tem nenhuma sessão registrada: a coluna \"executado\" está vazia porque "
    "não houve execução, não porque ela foi zerada."
)
COVERED_BY_OTHER_SLOT_LIMITATION = (
    "Item sem séries próprias cujo exercício foi registrado no slot de OUTRO item da mesma ficha "
    "(efeito da substituição corrigida no PR #211): ele aparece como pulado, mas a execução "
    "existe — no item que a recebeu."
)
NO_RUNS_IN_WINDOW_LIMITATION = (
    "Nenhuma geração de ficha neste período. A tabela `generation_runs` passou a registrar cada "
    "geração em 2026-09-26; antes disso só existe o que as fichas salvas ecoaram no `raw_payload`."
)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# Contagens derivadas do JSON NO BANCO: o `audit_json` inteiro nunca trafega
# na lista. `JSON_LENGTH` de um caminho ausente devolve NULL - e NULL aqui
# quer dizer "o motor nao gravou esta secao", que e diferente de `0`.
_AUDIT_COUNTS = """
  JSON_LENGTH(JSON_EXTRACT({col}, '$.selection'))      AS selection_count,
  JSON_LENGTH(JSON_EXTRACT({col}, '$.safety_clamps'))  AS safety_clamp_count,
  JSON_LENGTH(JSON_EXTRACT({col}, '$.substitutions'))  AS substitution_count,
  JSON_LENGTH(JSON_EXTRACT({col}, '$.fallbacks'))      AS fallback_count
"""

RUNS_SQL = f"""
SELECT
  gr.id                     AS run_id,
  gr.created_at             AS created_at,
  gr.plan_date              AS plan_date,
  gr.workout_plan_id        AS workout_plan_id,
  gr.job_id                 AS job_id,
  gr.template_id            AS template_id,
  gr.template_slot_sequence AS template_slot_sequence,
  gr.volume_mode            AS volume_mode,
  gr.engine_version         AS engine_version,
  gr.llm_used               AS llm_used,
  wp.name                   AS plan_name,
{_AUDIT_COUNTS.format(col="gr.audit_json")}
FROM generation_runs gr
LEFT JOIN workout_plans wp ON wp.id = gr.workout_plan_id AND wp.user_id = gr.user_id
WHERE gr.user_id = %s
  AND DATE(gr.created_at) BETWEEN %s AND %s
ORDER BY gr.created_at DESC, gr.id DESC
LIMIT %s
"""

# Fichas da janela cuja auditoria SO existe no `raw_payload` - as anteriores
# ao M16. O `LEFT JOIN ... IS NULL` evita que uma ficha ja coberta pela tabela
# apareca duas vezes; e uma consulta, nao uma por ficha.
RAW_PAYLOAD_RUNS_SQL = """
SELECT
  wp.id                       AS workout_plan_id,
  wp.name                     AS plan_name,
  wp.plan_date                AS plan_date,
  wp.created_at               AS created_at,
  wp.template_id              AS template_id,
  wp.template_slot_sequence   AS template_slot_sequence,
  JSON_UNQUOTE(JSON_EXTRACT(wp.raw_payload, '$.generation_audit.volume_mode'))    AS volume_mode,
  JSON_UNQUOTE(JSON_EXTRACT(wp.raw_payload, '$.generation_audit.engine_version')) AS engine_version,
  JSON_EXTRACT(wp.raw_payload, '$.generation_audit.llm_used')                     AS llm_used,
  JSON_LENGTH(JSON_EXTRACT(wp.raw_payload, '$.generation_audit.selection'))     AS selection_count,
  JSON_LENGTH(JSON_EXTRACT(wp.raw_payload, '$.generation_audit.safety_clamps')) AS safety_clamp_count,
  JSON_LENGTH(JSON_EXTRACT(wp.raw_payload, '$.generation_audit.substitutions')) AS substitution_count,
  JSON_LENGTH(JSON_EXTRACT(wp.raw_payload, '$.generation_audit.fallbacks'))     AS fallback_count
FROM workout_plans wp
LEFT JOIN generation_runs gr ON gr.workout_plan_id = wp.id AND gr.user_id = wp.user_id
WHERE wp.user_id = %s
  AND wp.plan_date BETWEEN %s AND %s
  AND JSON_EXTRACT(wp.raw_payload, '$.generation_audit') IS NOT NULL
  AND gr.id IS NULL
ORDER BY wp.plan_date DESC, wp.id DESC
LIMIT %s
"""

# Cabecalho da ficha. O `generation_audit` do `raw_payload` vem JUNTO: o
# fallback das fichas antigas nao pode custar uma segunda ida ao banco.
#
# PR3: o slot (e portanto a coluna PLANEJADO, que `TEMPLATE_ITEMS_SQL` le pelo
# `slot_id` daqui) e o `volume_mode` sao os da VERSAO DA FICHA - a gravada
# nela (066) ou, em ficha antiga, a vigente no `plan_date`. A
# versao entra por JOIN neste mesmo statement (`plan_version_expr`).
_PLAN_HEADER_TEMPLATE = """
SELECT
  wp.id                     AS plan_id,
  wp.name                   AS plan_name,
  wp.plan_date              AS plan_date,
  wp.created_at             AS created_at,
  wp.template_id            AS template_id,
  wp.template_slot_sequence AS template_slot_sequence,
  wpt.name                  AS template_name,
  CASE WHEN wptv.id IS NULL THEN wpt.volume_mode ELSE wptv.volume_mode END AS template_volume_mode,
  wptv.id                   AS template_version_id,
  wptv.version_number       AS template_version_number,
  wpts.id                   AS slot_id,
  wpts.name                 AS slot_name,
  JSON_EXTRACT(wp.raw_payload, '$.generation_audit') AS payload_audit
FROM workout_plans wp
LEFT JOIN workout_plan_templates wpt ON wpt.id = wp.template_id
LEFT JOIN workout_plan_template_versions wptv ON wptv.id = {version_expr}
LEFT JOIN workout_plan_template_slots wpts
       ON wpts.version_id = wptv.id
      AND wpts.sequence = wp.template_slot_sequence
WHERE wp.user_id = %s AND wp.id = %s
"""

# Versao da ficha (gravada nela ou vigente no `plan_date`).
PLAN_HEADER_SQL = _PLAN_HEADER_TEMPLATE.format(version_expr=plan_version_expr("wp", by_date=True))

PLAN_RUN_SQL = f"""
SELECT
  gr.id                     AS run_id,
  gr.created_at             AS created_at,
  gr.plan_date              AS plan_date,
  gr.workout_plan_id        AS workout_plan_id,
  gr.job_id                 AS job_id,
  gr.template_id            AS template_id,
  gr.template_slot_sequence AS template_slot_sequence,
  gr.volume_mode            AS volume_mode,
  gr.engine_version         AS engine_version,
  gr.llm_used               AS llm_used,
  gr.audit_json             AS audit_json,
{_AUDIT_COUNTS.format(col="gr.audit_json")}
FROM generation_runs gr
WHERE gr.user_id = %s AND gr.workout_plan_id = %s
ORDER BY gr.created_at DESC, gr.id DESC
LIMIT 1
"""

# Itens do SLOT do Modelo - a coluna PLANEJADO no modo `equipment_series`.
TEMPLATE_ITEMS_SQL = """
SELECT
  i.id                  AS template_item_id,
  i.item_order          AS item_order,
  i.item_kind           AS item_kind,
  i.muscle_group_id     AS muscle_group_id,
  mg.name               AS muscle_group_name,
  i.mapping_id          AS exercise_id,
  ec.name               AS exercise_name,
  i.equipment_id        AS equipment_id,
  i.sets_count          AS sets_count,
  i.reps_target         AS reps_target,
  i.rpe_target          AS rpe_target,
  i.prioritize          AS prioritize,
  i.advanced_technique  AS advanced_technique
FROM workout_plan_template_slot_items i
LEFT JOIN muscle_groups mg ON mg.id = i.muscle_group_id
LEFT JOIN exercise_catalog ec ON ec.id = i.mapping_id
WHERE i.slot_id = %s
ORDER BY i.item_order ASC, i.id ASC
"""

# EXECUTADO de todas as sessoes da ficha, ja agregado por exercicio. Mesma
# forma de `adherence_service.SESSION_EXERCISES_SQL` (e por isso consumivel
# por `build_session_items` sem adaptacao), com o recorte pela FICHA em vez de
# pela pagina de sessoes.
# Mesma fronteira "serie de trabalho" do resto do dashboard: aquecimento e
# serie de ajuste nao sao trabalho, e a definicao vive em UM lugar
# (`app/set_types.py`), nunca copiada por servico.
_NOT_PREPARATORY = sql_not_preparatory("es.set_type")
# Fase 5 (PR 4): aplicacao de tecnica nao e serie de trabalho da completude -
# mesma fronteira de `adherence_service.SESSION_EXERCISES_SQL`.
_IS_APPLICATION = sql_is_technique_application("es.set_type")
_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"

PLAN_PERFORMED_SQL = f"""
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
  AND a.workout_plan_id = %s
GROUP BY e.activity_id, e.id, e.sequence, e.original_name,
         e.workout_plan_exercise_id, {_CANONICAL}, e.notes
ORDER BY e.activity_id ASC, e.sequence ASC
"""

# A decisao de progressao de CADA item, num unico `IN (...)`. `ORDER BY` +
# leitura da PRIMEIRA por item: a decisao que vale e a mais recente.
PROGRESSION_BY_PLAN_ITEM_SQL = """
SELECT
  pd.id                       AS id,
  pd.workout_plan_exercise_id AS workout_plan_exercise_id,
  pd.kind                     AS kind,
  pd.decision                 AS decision,
  pd.phase_before             AS phase_before,
  pd.phase_after              AS phase_after,
  pd.engine_version           AS engine_version,
  pd.created_at               AS created_at
FROM progression_decisions pd
WHERE pd.user_id = %s
  AND pd.workout_plan_exercise_id IN ({placeholders})
ORDER BY pd.workout_plan_exercise_id ASC, pd.created_at DESC, pd.id DESC
"""

EXERCISE_NAMES_SQL = """
SELECT ec.id AS exercise_id, ec.name AS exercise_name
FROM exercise_catalog ec
WHERE ec.id IN ({placeholders})
"""


# ---------------------------------------------------------------------------
# Coercoes locais
# ---------------------------------------------------------------------------


def _int(value: Any) -> Optional[int]:
    parsed = to_float(value)
    return None if parsed is None else int(parsed)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any) -> Optional[bool]:
    """`TINYINT(1)` do MySQL, `true`/`false` do JSON, `"1"` do shim da Data
    API. Ausencia continua ausencia - nunca `False`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        texto = value.strip().lower()
        if texto in ("true", "1"):
            return True
        if texto in ("false", "0"):
            return False
        return None
    parsed = to_float(value)
    return None if parsed is None else bool(parsed)


def parse_audit(value: Any) -> Optional[dict[str, Any]]:
    """`audit_json`/`JSON_EXTRACT` -> dict.

    Os dois backends devolvem a mesma coluna JSON em tipos diferentes (o
    PyMySQL ja decodifica quando a coluna e JSON; o shim da Data API entrega
    a string). JSON invalido devolve `None` - a tela dira "auditoria
    indisponivel", que e verdade, em vez de estourar 500."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value if isinstance(value, str) else str(value))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Leitura do `generation_audit` - funcoes PURAS sobre o bloco
# ---------------------------------------------------------------------------


def _entries(audit: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    """Entradas de uma secao do bloco.

    `selection` e um DICIONARIO por grupo muscular no Modo A (`{"3": [...]}`)
    e o Modo B nem o grava; `safety_clamps`/`substitutions`/`fallbacks` sao
    listas. As duas formas viram a mesma lista aqui - e a razao de esta
    funcao existir e de os motores poderem mudar a forma sem quebrar a tela."""
    value = audit.get(key)
    if isinstance(value, Mapping):
        plano: list[Mapping[str, Any]] = []
        for grupo, itens in value.items():
            if not isinstance(itens, (list, tuple)):
                continue
            for item in itens:
                if isinstance(item, Mapping):
                    plano.append({"muscle_group_id": _int(grupo), **dict(item)})
        return plano
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _reason(kind: EngineReasonKind, entry: Mapping[str, Any], *, code_keys: Sequence[str]) -> dict[str, Any]:
    """Uma razao a partir de uma entrada de auditoria.

    `data` recebe o RESTO da entrada - nada e acrescentado e nada e
    renomeado. Uma chave nova de um motor futuro aparece na tela como dado
    bruto em vez de sumir."""
    code = None
    for key in code_keys:
        code = _text(entry.get(key))
        if code:
            break
    data = {
        key: value
        for key, value in entry.items()
        if key not in code_keys and key not in ("note", "message", "rejected", "ranking")
    }
    return {
        "kind": kind,
        "code": code,
        "detail": _text(entry.get("note")) or _text(entry.get("message")),
        "data": json.loads(json.dumps(data, default=str)),
    }


def reasons_by_exercise(audit: Mapping[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """Razoes indexadas pelo id do exercicio a que elas se referem.

    O indice e o `exercise_id` porque ele e a UNICA chave que as quatro
    secoes tem em comum e que tambem existe na ficha
    (`workout_plan_exercises.mapping_id`). O `sequence` que o Modo B grava
    nos clamps NAO serve: ele e a posicao ANTES da ordenacao por fadiga, e
    casar por ela colaria o clamp no item errado."""
    indice: dict[int, list[dict[str, Any]]] = {}

    def anexa(exercise_id: Any, reason: dict[str, Any]) -> None:
        chave = _int(exercise_id)
        if chave is None:
            return
        indice.setdefault(chave, []).append(reason)

    for entry in _entries(audit, "selection"):
        if not _bool(entry.get("selected")):
            continue
        anexa(entry.get("exercise_id"), _reason(EngineReasonKind.SELECTION, entry, code_keys=("reason",)))
    for entry in _entries(audit, "safety_clamps"):
        anexa(entry.get("exercise_id"), _reason(EngineReasonKind.SAFETY_CLAMP, entry, code_keys=("reason", "clamp", "field")))
    for entry in _entries(audit, "substitutions"):
        reason = _reason(EngineReasonKind.SUBSTITUTION, entry, code_keys=("reason",))
        anexa(entry.get("to_exercise_id"), reason)
    for entry in _entries(audit, "fallbacks"):
        if entry.get("exercise_id") is None:
            continue
        anexa(entry.get("exercise_id"), _reason(EngineReasonKind.FALLBACK, entry, code_keys=("reason",)))
    return indice


def plan_level_reasons(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Fallbacks que nao sao de um item: eles falam do GRUPO (nenhum candidato
    sobrou, deficit de volume). Exibi-los colados a um exercicio qualquer
    seria atribuir a ele uma decisao que nao foi sobre ele."""
    return [
        _reason(EngineReasonKind.FALLBACK, entry, code_keys=("reason",))
        for entry in _entries(audit, "fallbacks")
        if entry.get("exercise_id") is None
    ]


def rejections(audit: Mapping[str, Any], *, limit: int = MAX_REJECTIONS) -> list[dict[str, Any]]:
    """Candidatos avaliados e NAO escolhidos, na ordem em que o motor os
    recusou. E o que responde "por que este exercicio, e nao aquele"."""
    saida: list[dict[str, Any]] = []
    for entry in _entries(audit, "selection"):
        if _bool(entry.get("selected")):
            continue
        dados = {
            key: value
            for key, value in entry.items()
            if key not in ("muscle_group_id", "exercise_id", "rank", "reason", "selected", "rejected", "ranking")
        }
        saida.append(
            {
                "muscle_group_id": _int(entry.get("muscle_group_id")),
                "exercise_id": _int(entry.get("exercise_id")),
                "exercise_name": _text(entry.get("exercise_name")),
                "rank": _int(entry.get("rank")),
                "reason": _text(entry.get("reason")),
                "data": json.loads(json.dumps(dados, default=str)),
            }
        )
        if len(saida) >= limit:
            break
    return saida


def group_budgets(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`generation_audit.activation` -> uma linha por grupo.

    Os nomes das chaves vem de `GroupBudget.as_dict` (`generation/mode_a.py`);
    o que faltar sai `None`, nunca `0`: "o motor nao gravou o maximo" e
    diferente de "o maximo era zero"."""
    activation = audit.get("activation")
    if not isinstance(activation, Mapping):
        return []
    linhas: list[dict[str, Any]] = []
    for chave, valor in activation.items():
        if not isinstance(valor, Mapping):
            continue
        linhas.append(
            {
                "muscle_group_id": _int(valor.get("muscle_group_id")) or _int(chave),
                "muscle_group_name": _text(valor.get("muscle_group_name")),
                "target_weekly_sets": to_float(valor.get("target_weekly")),
                "target_min_sets": to_float(valor.get("target_min")),
                "target_max_sets": to_float(valor.get("target_max")),
                "generated_sets": to_float(valor.get("result")) or to_float(valor.get("planned_activation")),
                "deviation_status": _text(valor.get("deviation_status")),
                "recovery_status": _text(valor.get("recovery_status")),
            }
        )
    linhas.sort(key=lambda linha: (linha["muscle_group_name"] or "", linha["muscle_group_id"] or 0))
    return linhas


# ---------------------------------------------------------------------------
# Coluna PLANEJADO
# ---------------------------------------------------------------------------


def _planned_unavailable() -> dict[str, Any]:
    return {"source": EnginePlannedSource.UNAVAILABLE}


def _planned_from_template_item(row: Mapping[str, Any], *, free_exercise: bool = False) -> dict[str, Any]:
    return {
        "source": EnginePlannedSource.TEMPLATE_ITEM,
        "free_exercise": free_exercise,
        "item_kind": _text(row.get("item_kind")),
        "item_order": _int(row.get("item_order")),
        "muscle_group_id": _int(row.get("muscle_group_id")),
        "muscle_group_name": _text(row.get("muscle_group_name")),
        "exercise_id": _int(row.get("exercise_id")),
        "exercise_name": _text(row.get("exercise_name")),
        "equipment_id": _int(row.get("equipment_id")),
        "sets_count": _int(row.get("sets_count")),
        "reps_target": _text(row.get("reps_target")),
        "rpe_target": _text(row.get("rpe_target")),
        "advanced_technique": _text(row.get("advanced_technique")),
        "prioritize": _bool(row.get("prioritize")),
    }


def _planned_from_budget(budget: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": EnginePlannedSource.MUSCLE_GROUP_BUDGET,
        "muscle_group_id": budget.get("muscle_group_id"),
        "muscle_group_name": budget.get("muscle_group_name"),
        "budget_min_sets": budget.get("target_min_sets"),
        "budget_max_sets": budget.get("target_max_sets"),
        "budget_weekly_sets": budget.get("target_weekly_sets"),
    }


def _template_item_as_model_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """Linha de `TEMPLATE_ITEMS_SQL` na forma do item que o congelamento
    (`freeze_mode_b._plan_context`) le: o exercicio do item e `mapping_id`."""
    return {**dict(row), "mapping_id": row.get("exercise_id")}


def free_items_by_plan_row(
    conn: Any,
    plan_rows: Sequence[Mapping[str, Any]],
    template_rows: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """`plan_exercise_id` -> linha do item de exercicio LIVRE do Modelo que
    originou a linha da ficha (M101d).

    O vinculo item <-> linha nao e gravado na ficha; ele e re-derivado pelo
    MESMO casamento do congelamento, `freeze_mode_b.match_rows_to_model_items`
    (exercicio -> posicao + grupo PRIMARY -> so o grupo), sobre os MESMOS itens
    (so os `equipment`, na ordem do slot). Aqui nao ha regra nova: so se le o
    resultado, e so quando o item casado e livre (`is_free_exercise_item`).
    Item com par e Modo A nao mudam - quem os casa segue sendo `match_planned`
    pelo exercicio. Modelo sem item livre = nenhum statement a mais."""
    items = [
        _template_item_as_model_item(row)
        for row in template_rows
        if _text(row.get("item_kind")) == "equipment"
    ]
    if not any(is_free_exercise_item(item) for item in items):
        return {}
    rows = [
        {
            "workout_plan_exercise_id": _int(row.get("plan_exercise_id")),
            "mapping_id": _int(row.get("mapping_id")),
            "sequence": _int(row.get("plan_sequence")),
        }
        for row in plan_rows
        if _int(row.get("plan_exercise_id")) is not None
    ]
    # Import tardio: o congelamento puxa a prescricao deterministica inteira,
    # e so a ficha de Modelo com item livre precisa dele.
    from ...generation.freeze_mode_b import match_rows_to_model_items

    matched = match_rows_to_model_items(conn, rows, items)
    return {row_id: item for row_id, item in matched.items() if is_free_exercise_item(item)}


def match_planned(
    *,
    exercise_id: Optional[int],
    template_rows: Sequence[Mapping[str, Any]],
    used_template_items: set[int],
    free_template_item: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """A coluna PLANEJADO de UM item gerado.

    Ordem de preferencia, e ela importa:

    1. o item do Modelo que nomeia ESTE exercicio (`mapping_id` igual) e que
       ainda nao foi casado com outro item da ficha - um Modelo pode pedir o
       mesmo exercicio duas vezes, e cada pedido casa uma vez so;
    2. o item de exercicio LIVRE do Modelo que originou esta linha da ficha
       (M101d, `free_items_by_plan_row`), se ainda nao foi casado - o motor
       escolheu o exercicio, e o planejado e o daquele item;
    3. nada, DECLARADO (`UNAVAILABLE`) - e o chamador ainda tenta o orcamento
       do grupo (`_planned_from_budget`) quando o Modelo planeja por volume.

    Nao ha passo "casa pelo grupo muscular do item do Modelo": um item
    `muscle_group` do Modelo pede volume, nao exercicio, e casa-lo com um
    exercicio especifico faria a tela dizer que o Modelo pediu aquele
    exercicio."""
    if exercise_id is not None:
        for row in template_rows:
            item_id = _int(row.get("template_item_id"))
            if item_id in used_template_items:
                continue
            if _int(row.get("exercise_id")) == exercise_id:
                if item_id is not None:
                    used_template_items.add(item_id)
                return _planned_from_template_item(row)
    if free_template_item is not None:
        item_id = _int(free_template_item.get("template_item_id"))
        if item_id is not None and item_id not in used_template_items:
            used_template_items.add(item_id)
            return _planned_from_template_item(free_template_item, free_exercise=True)
    return _planned_unavailable()


# ---------------------------------------------------------------------------
# Itens cobertos por outro slot (PR #211)
# ---------------------------------------------------------------------------


def covered_by_another_slot(
    plan_rows: Sequence[Mapping[str, Any]],
    performed_rows: Sequence[Mapping[str, Any]],
) -> set[int]:
    """Itens SEM serie propria cujo exercicio foi registrado no slot de outro
    item da mesma ficha.

    Mesma regra de `findPlanItemsCoveredByAnotherSlot`
    (`frontend/src/lib/workout-mode-plan-flow.ts`), do lado do servidor: ate o
    PR #211, escolher na substituicao um exercicio que a propria ficha
    prescrevia em OUTRO item gravava a execucao no slot errado. O item certo
    fica sem serie nenhuma e o §5 o classifica como `SKIPPED` - o que e
    verdade sobre o VINCULO e mentira sobre o treino. Aqui a tela nao
    reclassifica nada (isso seria a dupla verdade do §18): ela acrescenta a
    limitacao ao item, com a frase que diz onde a execucao foi parar."""
    planejado_por_item: dict[int, Optional[int]] = {}
    for row in plan_rows:
        item_id = _int(row.get("plan_exercise_id"))
        if item_id is not None:
            planejado_por_item[item_id] = _int(row.get("mapping_id"))

    itens_com_serie: set[int] = set()
    substituido_em: dict[int, int] = {}
    for row in performed_rows:
        slot = _int(row.get("workout_plan_exercise_id"))
        if slot is None:
            continue
        itens_com_serie.add(slot)
        executado = _int(row.get("exercise_id"))
        planejado = planejado_por_item.get(slot)
        if executado is None or planejado is None or executado == planejado:
            continue
        substituido_em.setdefault(executado, slot)

    if not substituido_em:
        return set()
    cobertos: set[int] = set()
    for item_id, planejado in planejado_por_item.items():
        if item_id in itens_com_serie or planejado is None:
            continue
        slot = substituido_em.get(planejado)
        if slot is not None and slot != item_id:
            cobertos.add(item_id)
    return cobertos


# ---------------------------------------------------------------------------
# Lista de geracoes
# ---------------------------------------------------------------------------


def _run_payload(row: Mapping[str, Any], *, source: EngineAuditSource) -> dict[str, Any]:
    limitations: list[str] = []
    if source is EngineAuditSource.PLAN_RAW_PAYLOAD:
        limitations.append(AUDIT_FROM_RAW_PAYLOAD_LIMITATION)
    if source is EngineAuditSource.GENERATION_RUNS and row.get("workout_plan_id") is None:
        limitations.append(AUDIT_DRAFT_NOT_SAVED_LIMITATION)
    return {
        "run_id": _int(row.get("run_id")),
        "source": source,
        "created_at": to_datetime(row.get("created_at")),
        "plan_date": to_date(row.get("plan_date")),
        "workout_plan_id": _int(row.get("workout_plan_id")),
        "plan_name": _text(row.get("plan_name")),
        "template_id": _int(row.get("template_id")),
        "template_slot_sequence": _int(row.get("template_slot_sequence")),
        "volume_mode": _text(row.get("volume_mode")),
        "engine_version": _text(row.get("engine_version")),
        "llm_used": _bool(row.get("llm_used")),
        "job_id": _text(row.get("job_id")),
        "selection_count": _int(row.get("selection_count")),
        "safety_clamp_count": _int(row.get("safety_clamp_count")),
        "substitution_count": _int(row.get("substitution_count")),
        "fallback_count": _int(row.get("fallback_count")),
        "limitations": limitations,
    }


def build_engine_runs(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    limit: int = DEFAULT_RUNS_LIMIT,
) -> dict[str, Any]:
    """`GET /analytics/dashboard/engine` - as geracoes da janela, das DUAS
    fontes, com a fonte declarada linha a linha. **2 statements.**

    Sempre 200. Janela sem geracao nenhuma responde `items: []` com
    `INSUFFICIENT` e a frase que diz desde quando a serie existe - nunca um
    zero que pareca medida."""
    owner = require_user_id(user_id)
    limit = max(1, min(int(limit), MAX_RUNS_LIMIT))

    runs = _rows(conn, RUNS_SQL, (owner, window.date_from, window.date_to, limit))
    legados = _rows(conn, RAW_PAYLOAD_RUNS_SQL, (owner, window.date_from, window.date_to, limit))

    itens = [_run_payload(row, source=EngineAuditSource.GENERATION_RUNS) for row in runs]
    itens += [_run_payload(row, source=EngineAuditSource.PLAN_RAW_PAYLOAD) for row in legados]
    # `created_at` das linhas legadas e o da FICHA, nao o da geracao - por
    # isso a limitacao viaja com cada uma delas. A ordenacao usa o que existe.
    itens.sort(key=lambda item: (item["created_at"] or datetime.min, item["run_id"] or 0), reverse=True)
    itens = itens[:limit]

    limitations: list[str] = []
    if not itens:
        limitations.append(NO_RUNS_IN_WINDOW_LIMITATION)
    if legados:
        limitations.append(AUDIT_FROM_RAW_PAYLOAD_LIMITATION)

    log_event(
        "dashboard.computed",
        screen="engine_runs",
        user_id=owner,
        window=window.kind.value,
        runs=len(runs),
        raw_payload_runs=len(legados),
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": DataQuality.INSUFFICIENT if not itens else DataQuality.HIGH,
        "limitations": limitations,
        "items": itens,
        "total": len(itens),
        "limit": limit,
        "runs_table_total": len(runs),
        "raw_payload_total": len(legados),
    }


# ---------------------------------------------------------------------------
# Detalhe de uma ficha
# ---------------------------------------------------------------------------


def _progression_by_item(
    conn: Any, *, user_id: int, plan_exercise_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """A decisao MAIS RECENTE por item, numa consulta so.

    A tabela pode nem existir (banco anterior a migracao 036): a evidencia e
    opcional e a ausencia dela nao pode derrubar a tela - mesmo tratamento de
    `exposure_service._fetch_progression_decisions`."""
    if not plan_exercise_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(plan_exercise_ids))
    try:
        linhas = _rows(
            conn,
            PROGRESSION_BY_PLAN_ITEM_SQL.format(placeholders=placeholders),
            (user_id, *[int(item) for item in plan_exercise_ids]),
        )
    except Exception as exc:  # noqa: BLE001 - evidencia opcional nunca derruba a resposta
        log_event("dashboard.progression_decisions_unavailable", screen="engine", error=str(exc))
        return {}

    saida: dict[int, dict[str, Any]] = {}
    for linha in linhas:
        item_id = _int(linha.get("workout_plan_exercise_id"))
        if item_id is None or item_id in saida:
            continue
        saida[item_id] = {
            "id": int(linha["id"]),
            "decision": _text(linha.get("decision")),
            "kind": _text(linha.get("kind")),
            "phase_before": _text(linha.get("phase_before")),
            "phase_after": _text(linha.get("phase_after")),
            "engine_version": _text(linha.get("engine_version")),
            "created_at": to_datetime(linha.get("created_at")),
        }
    return saida


def _exercise_names(conn: Any, exercise_ids: Sequence[int]) -> dict[int, str]:
    """Nome dos exercicios CITADOS pela auditoria que nao estao na ficha (os
    recusados). Uma consulta com `IN (...)`, nunca uma por candidato; lista
    vazia nao emite statement nenhum."""
    if not exercise_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(exercise_ids))
    linhas = _rows(conn, EXERCISE_NAMES_SQL.format(placeholders=placeholders), tuple(int(i) for i in exercise_ids))
    return {
        int(linha["exercise_id"]): str(linha["exercise_name"])
        for linha in linhas
        if linha.get("exercise_id") is not None and linha.get("exercise_name")
    }


def build_engine_plan(
    conn: Any,
    plan_id: int,
    *,
    user_id: Any,
    window: WindowInfo,
) -> Optional[dict[str, Any]]:
    """`GET /analytics/dashboard/engine/plans/{plan_id}` - planejado x gerado
    x executado, item a item, com o motivo de cada desvio. **6 statements**
    (7 quando a auditoria cita exercicios fora da ficha).

    `None` quando a ficha nao existe OU nao e do usuario: o endpoint devolve
    404 nos dois casos, entao nenhum id alheio e confirmado (IDOR)."""
    owner = require_user_id(user_id)

    cabecalho = _rows(conn, PLAN_HEADER_SQL, (owner, int(plan_id)))
    if not cabecalho:
        return None
    header = cabecalho[0]

    run_rows = _rows(conn, PLAN_RUN_SQL, (owner, int(plan_id)))
    limitations: list[str] = []
    if run_rows:
        run_row = run_rows[0]
        audit = parse_audit(run_row.get("audit_json")) or {}
        source = EngineAuditSource.GENERATION_RUNS if audit else EngineAuditSource.UNAVAILABLE
    else:
        audit = parse_audit(header.get("payload_audit")) or {}
        source = EngineAuditSource.PLAN_RAW_PAYLOAD if audit else EngineAuditSource.UNAVAILABLE
        run_row = {
            "run_id": None,
            "created_at": header.get("created_at"),
            "plan_date": header.get("plan_date"),
            "workout_plan_id": header.get("plan_id"),
            "template_id": header.get("template_id"),
            "template_slot_sequence": header.get("template_slot_sequence"),
            "volume_mode": audit.get("volume_mode"),
            "engine_version": audit.get("engine_version"),
            "llm_used": audit.get("llm_used"),
            "selection_count": len(_entries(audit, "selection")) or None,
            "safety_clamp_count": len(_entries(audit, "safety_clamps")) or None,
            "substitution_count": len(_entries(audit, "substitutions")) or None,
            "fallback_count": len(_entries(audit, "fallbacks")) or None,
        }
    if source is EngineAuditSource.PLAN_RAW_PAYLOAD:
        limitations.append(AUDIT_FROM_RAW_PAYLOAD_LIMITATION)
    elif source is EngineAuditSource.UNAVAILABLE:
        limitations.append(AUDIT_UNAVAILABLE_LIMITATION)

    run = _run_payload({**run_row, "plan_name": header.get("plan_name")}, source=source)

    plan_rows = _rows(conn, PLAN_ITEMS_SQL.format(placeholders="%s"), (owner, int(plan_id)))
    slot_id = _int(header.get("slot_id"))
    template_rows = _rows(conn, TEMPLATE_ITEMS_SQL, (slot_id,)) if slot_id is not None else []
    if header.get("template_id") is None:
        limitations.append(NO_TEMPLATE_LIMITATION)

    performed_rows = _rows(conn, PLAN_PERFORMED_SQL, (owner, int(plan_id)))
    sessions = {_int(row.get("activity_id")) for row in performed_rows}
    sessions.discard(None)
    if not performed_rows:
        limitations.append(NOT_EXECUTED_LIMITATION)

    plan_exercise_ids = [
        item for item in (_int(row.get("plan_exercise_id")) for row in plan_rows) if item is not None
    ]
    progressoes = _progression_by_item(conn, user_id=owner, plan_exercise_ids=plan_exercise_ids)

    # Juncao ficha x executado: as regras do §5 vem INTEIRAS de
    # `adherence_service` (`build_session_items`), nunca reescritas aqui.
    base_items = build_session_items(plan_rows, performed_rows)
    cobertos = covered_by_another_slot(plan_rows, performed_rows)

    orcamentos = group_budgets(audit)
    orcamento_por_grupo = {
        linha["muscle_group_id"]: linha for linha in orcamentos if linha["muscle_group_id"] is not None
    }
    razoes = reasons_by_exercise(audit)
    volume_mode = _text(run["volume_mode"]) or _text(header.get("template_volume_mode"))
    if volume_mode == "percent_budget" and orcamentos:
        limitations.append(BUDGET_MODE_LIMITATION)

    plan_row_por_item = {
        _int(row.get("plan_exercise_id")): row for row in plan_rows if _int(row.get("plan_exercise_id")) is not None
    }
    livres_por_item = free_items_by_plan_row(conn, plan_rows, template_rows)
    usados: set[int] = set()
    itens: list[dict[str, Any]] = []
    sem_progressao = False
    for base in base_items:
        item_id = _int(base.get("plan_exercise_id"))
        plan_row = plan_row_por_item.get(item_id or -1, {})
        exercise_id = _int(plan_row.get("mapping_id"))

        planejado = match_planned(
            exercise_id=exercise_id,
            template_rows=template_rows,
            used_template_items=usados,
            free_template_item=livres_por_item.get(item_id) if item_id is not None else None,
        )
        if planejado["source"] is EnginePlannedSource.UNAVAILABLE:
            grupo = _grupo_do_item(exercise_id, orcamento_por_grupo, razoes)
            if grupo is not None:
                planejado = _planned_from_budget(orcamento_por_grupo[grupo])

        item_reasons = list(razoes.get(exercise_id, [])) if exercise_id is not None else []
        # Exercicio EXTRA nao e item da ficha: ele nao tem prescricao nem
        # decisao de progressao a que faltar, entao a ressalva nao se aplica.
        e_item_da_ficha = item_id is not None
        progressao = progressoes.get(item_id) if e_item_da_ficha else None
        if progressao is not None:
            item_reasons.append(
                {
                    "kind": EngineReasonKind.PROGRESSION,
                    "code": progressao["decision"],
                    "detail": None,
                    "data": {
                        "phase_before": progressao["phase_before"],
                        "phase_after": progressao["phase_after"],
                        "engine_version": progressao["engine_version"],
                    },
                }
            )

        item_limitations = list(base.get("limitations") or [])
        if e_item_da_ficha and item_id in cobertos:
            item_limitations.append(COVERED_BY_OTHER_SLOT_LIMITATION)
        if e_item_da_ficha and progressao is None:
            item_limitations.append(NO_PROGRESSION_DECISION_LIMITATION)
            sem_progressao = True

        itens.append(
            {
                "plan_exercise_id": item_id,
                "plan_sequence": base.get("plan_sequence"),
                "status": base["status"],
                "planned": planejado,
                "generated_name": base.get("planned_name"),
                "generated_exercise_id": exercise_id,
                "generated": base["prescribed"],
                "executed": base.get("performed"),
                "missing_sets": base.get("missing_sets"),
                "substitution": base.get("substitution"),
                "reasons": item_reasons,
                "progression": progressao,
                "limitations": list(dict.fromkeys(item_limitations)),
            }
        )

    if sem_progressao and itens:
        limitations.append(NO_PROGRESSION_DECISION_LIMITATION)

    recusados = rejections(audit)
    faltando_nome = sorted(
        {
            item["exercise_id"]
            for item in recusados
            if item["exercise_id"] is not None and not item["exercise_name"]
        }
    )
    nomes = _exercise_names(conn, faltando_nome)
    for item in recusados:
        if not item["exercise_name"]:
            item["exercise_name"] = nomes.get(item["exercise_id"] or -1)

    log_event(
        "dashboard.computed",
        screen="engine_plan",
        user_id=owner,
        plan_id=int(plan_id),
        audit_source=source.value,
        items=len(itens),
        sessions=len(sessions),
    )

    return {
        "window": window,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": _plan_quality(source, itens),
        "limitations": list(dict.fromkeys(limitations)),
        "run": run,
        "plan_id": int(plan_id),
        "plan_name": _text(header.get("plan_name")),
        "plan_date": to_date(header.get("plan_date")),
        "template_id": _int(header.get("template_id")),
        "template_name": _text(header.get("template_name")),
        "template_slot_sequence": _int(header.get("template_slot_sequence")),
        "template_slot_name": _text(header.get("slot_name")),
        "template_version_number": _int(header.get("template_version_number")),
        "volume_mode": volume_mode,
        "items": itens,
        "item_status_counts": count_item_statuses(itens),
        "group_budgets": orcamentos,
        "rejections": recusados,
        "plan_level_reasons": plan_level_reasons(audit),
        "sessions_count": len(sessions),
    }


def _grupo_do_item(
    exercise_id: Optional[int],
    orcamento_por_grupo: Mapping[int, Mapping[str, Any]],
    razoes: Mapping[int, Sequence[Mapping[str, Any]]],
) -> Optional[int]:
    """Grupo cujo ORCAMENTO pagou por este exercicio, a partir da propria
    auditoria (`selection[].muscle_group_id`).

    De proposito nao se consulta `exercise_muscle_contributions`: o grupo que
    interessa aqui e aquele a cuja COTA o motor debitou o exercicio - que
    pode nao ser o primario dele - e essa informacao ja esta na auditoria.
    Sem ela, o item fica sem coluna planejada, DECLARADO."""
    if exercise_id is None:
        return None
    for reason in razoes.get(exercise_id, ()):  # type: ignore[arg-type]
        grupo = _int(dict(reason.get("data") or {}).get("muscle_group_id"))
        if grupo is not None and grupo in orcamento_por_grupo:
            return grupo
    return None


def _plan_quality(source: EngineAuditSource, itens: Sequence[Mapping[str, Any]]) -> DataQuality:
    """Confianca da RESPOSTA.

    Sem auditoria a tela ainda mostra gerado x executado, mas nao responde o
    "por que" - e `INSUFFICIENT` e o que diz isso sem apagar o resto."""
    if source is EngineAuditSource.UNAVAILABLE:
        return DataQuality.INSUFFICIENT
    if not itens:
        return DataQuality.INSUFFICIENT
    if source is EngineAuditSource.PLAN_RAW_PAYLOAD:
        return DataQuality.MEDIUM
    return DataQuality.HIGH


__all__ = [
    "AUDIT_DRAFT_NOT_SAVED_LIMITATION",
    "AUDIT_FROM_RAW_PAYLOAD_LIMITATION",
    "AUDIT_UNAVAILABLE_LIMITATION",
    "BUDGET_MODE_LIMITATION",
    "COVERED_BY_OTHER_SLOT_LIMITATION",
    "DEFAULT_RUNS_LIMIT",
    "MAX_REJECTIONS",
    "MAX_RUNS_LIMIT",
    "NOT_EXECUTED_LIMITATION",
    "NO_PROGRESSION_DECISION_LIMITATION",
    "NO_RUNS_IN_WINDOW_LIMITATION",
    "NO_TEMPLATE_LIMITATION",
    "build_engine_plan",
    "build_engine_runs",
    "covered_by_another_slot",
    "group_budgets",
    "match_planned",
    "parse_audit",
    "plan_level_reasons",
    "reasons_by_exercise",
    "rejections",
]

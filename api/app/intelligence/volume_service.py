from __future__ import annotations

import copy
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from .. import request_memo
from ..domain import e1rm
from ..domain.technique_accounting import planned_equivalent, realized_recovery_factor
from ..domain.deload import deload_source_label, deload_source_value
from ..domain.training_goal import goal_value, goals_in, sql_goal_is
from ..load_conventions import normalize_load_convention, supports_e1rm
from ..domain.set_measures import dose_reps_for_measure, is_rep_based, sql_if_rep_based
from ..set_types import is_working_set, sql_not_preparatory, sql_set_confirmed
from ..template_versions import plan_version_expr
from .base import IntelligenceServiceBase
from .muscle_contribution_resolver import DEFAULT_WRITE_WEIGHTS
from .technique_volume import (
    annotate_set_units,
    apply_application_units,
    new_catalog,
    planned_item_sets,
    series_number,
    sql_application_rows,
)

logger = logging.getLogger(__name__)

# Todas as leituras de series excluem aquecimento/ajuste - inclusive as
# series a series (_fetch_window_sets/_fetch_full_history_sets), que ate a
# Fase 5 (PR 4) traziam tudo e aplicavam o fator de recuperacao por set_type.
# Aplicacoes de tecnica avancada entram em todas, mas nos numeros de SERIES
# EQUIVALENTES (diretas/indiretas/estimulo/fadiga/ativacao) cada aplicacao
# vale a sua fatia do bloco da ancora (`set_units`, regra unica de
# `domain/technique_accounting.py`); contagens BRUTAS (`sets_count`) seguem
# contando uma linha por serie.
_ES_NOT_PREPARATORY = sql_not_preparatory("es.set_type")
_ES2_NOT_PREPARATORY = sql_not_preparatory("es2.set_type")


# Slot do Modelo de cada sessao (comparativo por slot de `/volume-grupamentos`):
# chave `wp.template_id` + `wp.template_slot_sequence`; id e nome do slot vem da
# versao que governa a ficha (gravada nela ou vigente no `plan_date`).
_ACTIVITY_SLOTS_SQL = f"""
SELECT
  a.id                      AS activity_id,
  wp.template_id            AS template_id,
  wp.template_slot_sequence AS slot_sequence,
  wpt.name                  AS template_name,
  wpts.id                   AS slot_id,
  wpts.name                 AS slot_name
FROM activities a
JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
LEFT JOIN workout_plan_templates wpt ON wpt.id = wp.template_id
LEFT JOIN workout_plan_template_slots wpts
       ON wpts.version_id = {plan_version_expr("wp", by_date=True)}
      AND wpts.sequence = wp.template_slot_sequence
WHERE a.user_id = %s
  AND a.local_date >= %s
  AND wp.template_id IS NOT NULL
  AND wp.template_slot_sequence IS NOT NULL
"""
_NOT_PREPARATORY = sql_not_preparatory("set_type")

OUTROS_GROUP = "Outros"

# Atributos de grupo muscular que ANTES vinham de subconsultas correlacionadas
# dentro da query de series (uma avaliacao por serie) e agora sao resolvidos
# uma vez por exercicio do catalogo (ver
# `VolumeService._fetch_group_attributes_by_exercise`). As chaves publicas
# abaixo sao exatamente as colunas que a query antiga devolvia por linha.
_GROUP_ATTRIBUTE_KEYS = (
    "primary_group",
    "secondary_group",
    "primary_group_id",
    "secondary_group_id",
    "secondary_participation",
)
_EMPTY_GROUP_ATTRIBUTES: dict[str, Any] = {
    "primary_group": None,
    "secondary_group": None,
    "primary_group_id": None,
    "secondary_group_id": None,
    "secondary_participation": None,
    # Marcas internas do "primeiro por `id`" (o antigo `ORDER BY emc.id ASC
    # LIMIT 1` de cada subconsulta); nunca copiadas para a linha de serie.
    "_primary_seen": False,
    "_secondary_seen": False,
}
DENSITY_WINDOW_HOURS = 72.0
LOAD_REFERENCE_WINDOW_DAYS = 28
# Nao confiar em referencia de e1RM baseada em 1 unica sessao anterior: uma
# unica observacao pode ser atipica (aquecimento mal registrado, sessao ruim,
# etc.). Desde 2026-10-06 esta referencia so alimenta o `load_spike_factor`
# da dose: o `performance_ratio` saiu dela e passou para a regra de
# comparabilidade (`exercise_performance`).
MIN_REFERENCE_OBSERVATIONS = 2
NEW_EXERCISE_OCCURRENCE_THRESHOLD = 3

# Queda de repeticoes entre series da MESMA sessao (sinal EXCESSIVE_REP_DROP da
# recuperacao). Compara a 1a e a ultima serie de TRABALHO (`is_working_set`:
# sem aquecimento/ajuste, backoff nem aplicacao de tecnica) na MESMA carga e
# marca queda quando `1 - ultima/primeira > REP_DROP_MIN_FRACTION`.
# Lote 1/P2 da investigacao RPE 10 (2026-10-11): com a ultima serie de trabalho
# em RPE >= REP_DROP_IGNORE_FROM_RPE a queda e a esperada de quem treina perto
# da falha e NAO e sinal; RPE abaixo disso (ou sem RPE) continua sinal. A regua
# contra a sessao anterior (PERFORMANCE_DROP) nao muda.
REP_DROP_MIN_FRACTION = 0.30
REP_DROP_IGNORE_FROM_RPE = 9

# Tolerancia de "bateu o alvo" para classify_weekly_volume_status abaixo:
# +-5% do target_weekly_sets conta como ON_TARGET em vez de exigir
# igualdade exata. Motivo: series equivalentes (`stimulus_equivalent_sets`)
# sao numeros ponderados/fracionarios (Sigma sets_count * stimulus_weight),
# quase nunca batem o alvo cadastrado dígito a dígito mesmo quando a
# aderencia é, na pratica, perfeita - sem essa faixa, qualquer desvio
# residual de arredondamento marcaria "abaixo"/"acima" da meta
# incorretamente. 5% foi escolhido por consistencia com a mesma ordem de
# grandeza dos incrementos de carga que `progression_service.suggest_load_and_reps`
# ja usa (2,5% a 5%), nao por nenhuma medicao especifica - ajustavel se a
# Fase 2/3 do dashboard mostrar que precisa ser mais/menos permissivo.
ON_TARGET_TOLERANCE_PCT = 0.05

# Fallback generico de meta semanal quando nao ha nenhum `training_type`
# resolvido (nem explicito via query param, nem via template atribuido) -
# MESMOS limiares de MuscleActivationTable.tsx/activationAssessment (<6
# series equivalentes = vermelho/baixo, <9 = amarelo/moderado, >=9 = verde/
# bom). Definido aqui (em vez de em `analytics.py`, onde nasceu na Fase 1) a
# partir da Fase 4 para nao duplicar os 3 numeros em dois arquivos -
# `analytics.py` e `muscle_summary_service.py` importam estas
# constantes em vez de redefini-las. max = target * 3 (27) como teto
# generoso so para dar um numero plausivel ao 5o estado (ABOVE_MAX) sem
# inventar um criterio novo de "muito acima" - decisao de design ajustavel
# se o uso real mostrar que precisa ser mais/menos permissivo.
DASHBOARD_FALLBACK_MIN_SETS = 6.0
DASHBOARD_FALLBACK_TARGET_SETS = 9.0
DASHBOARD_FALLBACK_MAX_SETS = 27.0

# Os 5 estados devolvidos por `classify_weekly_volume_status` abaixo, na ordem
# crescente de volume. Unica lista nominal (o Novo Dashboard a reexporta em
# `intelligence/dashboard/metrics_registry.py` em vez de redigitar os nomes);
# `tests/unit/test_dashboard_metrics_registry.py` garante que a funcao nunca
# devolve um valor fora dela.
WEEKLY_VOLUME_STATUSES: tuple[str, ...] = (
    "BELOW_MIN",
    "BETWEEN_MIN_TARGET",
    "ON_TARGET",
    "BETWEEN_TARGET_MAX",
    "ABOVE_MAX",
)


def _counted_by_group_name(contributions: Any, mode: Any, block_of: Any = None) -> dict[str, Any]:
    """`{nome do grupo: GroupContribution}` de UM exercicio no modo de
    contagem, com a regra do motor (`methodology_volume.counted_contributions`:
    papeis do modo, peso do modo, mesmo grupo em dois papeis).

    Com `block_of` (grupo -> bloco da metodologia, so fora de `primary_only`):
    fatia + pai do MESMO item contam uma vez no bloco, com o maior peso
    (`block_contributions_by_member`, a juncao do motor do Modelo) - o
    realizado soma os membros e chega ao numero do planejado. Grupo fora do
    cadastro vira bloco proprio, como no realizado. Em `primary_only` (sem
    `block_of`) a juncao e por grupo, como antes do Lote 4 (paridade).

    As linhas do resolver sao por NOME (a contribuicao-fallback "Outros" nao
    tem `muscle_group_id`): grupo com id usa o id; sem id, uma chave local
    negativa, que nunca colide com um id."""
    from .methodology_volume import ContributionRow, block_contributions_by_member, counted_contributions

    names: dict[int, str] = {}
    keys: dict[str, int] = {}
    rows: list[Any] = []
    for contribution in contributions or ():
        name = contribution.get("muscle_group_name")
        if not name:
            continue
        if name not in keys:
            group_id = contribution.get("muscle_group_id") if block_of is not None else None
            keys[name] = int(group_id) if group_id is not None else -(len(keys) + 1)
            names[keys[name]] = name
        raw = contribution.get("stimulus_weight")
        rows.append(
            ContributionRow(
                muscle_group_id=keys[name],
                role=str(contribution.get("role") or ""),
                stimulus_weight=float(raw) if raw is not None else None,
            )
        )
    if block_of is None:
        counted = counted_contributions(rows, mode)
    else:
        own_blocks = {key: block_of.get(key, key) for key in names}
        counted = block_contributions_by_member(rows, mode, own_blocks)
    return {names[key]: weight for key, weight in counted.items()}


def classify_weekly_volume_status(
    actual: float, min_sets: float, target_sets: float, max_sets: float
) -> str:
    """Classifica o volume semanal REALIZADO (`actual`, series equivalentes)
    contra a meta (min/target/max_weekly_sets de um `training_type`) em um
    dos 5 estados de aderencia usados pelo dashboard (`/dashboard/musculos`,
    `dashboard-muscle-status`): `BELOW_MIN`, `BETWEEN_MIN_TARGET`, `ON_TARGET`,
    `BETWEEN_TARGET_MAX`, `ABOVE_MAX`.

    Funcao pura (sem acesso a banco), reutilizavel por qualquer endpoint que
    precise da mesma classificacao - unica fonte da logica de corte, para
    nao divergir entre telas (todas chamam esta funcao, nunca reimplementam
    os limiares).

    `ON_TARGET` cobre uma faixa de tolerancia de +-`ON_TARGET_TOLERANCE_PCT`
    ao redor de `target_sets` (ver constante acima). Fora dessa faixa, cai
    para `BETWEEN_MIN_TARGET`/`BETWEEN_TARGET_MAX` conforme o lado; abaixo de
    `min_sets` ou acima de `max_sets` sempre vence (bordas absolutas).

    Assume `0 <= min_sets <= target_sets <= max_sets` (mesma validacao ja
    aplicada em `training_types._validate_training_type_payload` para quem
    cadastra a meta) - nao revalida aqui."""
    tolerance = target_sets * ON_TARGET_TOLERANCE_PCT
    target_low = target_sets - tolerance
    target_high = target_sets + tolerance

    if actual < min_sets:
        return "BELOW_MIN"
    if actual < target_low:
        return "BETWEEN_MIN_TARGET"
    if actual <= target_high:
        return "ON_TARGET"
    if actual <= max_sets:
        return "BETWEEN_TARGET_MAX"
    return "ABOVE_MAX"


# PLANEJADO de `/dashboard/musculos` a partir da prescricao das fichas salvas
# (`get_planned_effective_sets`). Uma linha por (ficha, grupo, tecnica,
# aplicacoes) - nunca por exercicio: `planned_sets` = soma de
# `sets_count x stimulus_weight` e `planned_weight` = soma dos pesos dos
# exercicios com series, para o Python somar `planned_equivalent(tecnica) x
# peso` pela regra unica (`technique_accounting`), como em
# `TEMPLATE_PLAN_ACTIVATION_SQL` do Modelo. Mesmo id canonico e mesmo filtro
# de contribuicao ativa do realizado. `sets_count` e so trabalho: aquecimento
# (`warmup_sets_count`) e ajuste (`feeler_set_sets_count`) ficam de fora.
PLAN_EXERCISES_ACTIVATION_SQL = """
SELECT
  wpe.plan_id                               AS plan_id,
  emc.muscle_group_id                       AS muscle_group_id,
  mg.name                                   AS muscle_group_name,
  wpe.advanced_technique                    AS advanced_technique,
  wpe.advanced_technique_applications       AS advanced_technique_applications,
  SUM(wpe.sets_count * emc.stimulus_weight) AS planned_sets,
  SUM(emc.stimulus_weight)                  AS planned_weight
FROM workout_plans wp
JOIN workout_plan_exercises wpe ON wpe.plan_id = wp.id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = wpe.mapping_id
JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = COALESCE(elm.canonical_exercise_id, wpe.mapping_id)
      AND emc.is_active = 1
LEFT JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
WHERE {where}
  AND wpe.mapping_id IS NOT NULL
  AND wpe.sets_count > 0
GROUP BY wpe.plan_id, emc.muscle_group_id, mg.name,
         wpe.advanced_technique, wpe.advanced_technique_applications
"""


def _planned_plans_where(alias: str, user_id: int | None) -> str:
    clauses = [f"COALESCE({alias}.plan_date, DATE({alias}.created_at)) BETWEEN %s AND %s"]
    if user_id is not None:
        clauses.append(f"{alias}.user_id = %s")
    return " AND ".join(clauses)


def _planned_plans_params(user_id: int | None, date_from: date, date_to: date) -> tuple[Any, ...]:
    return (date_from, date_to) if user_id is None else (date_from, date_to, user_id)


def aggregate_planned_effective_sets(
    prescription_rows: Any,
    fallback_payloads: Any = (),
    *,
    catalog: Any = None,
) -> list[dict[str, Any]]:
    """Funcao pura do PLANEJADO por grupo (`get_planned_effective_sets`).

    - `prescription_rows`: linhas de `PLAN_EXERCISES_ACTIVATION_SQL`. Cada
      uma soma `planned_sets + planned_equivalent(tecnica, aplicacoes) x
      planned_weight` ao grupo - a conta de `resolve_template_plan` do Modelo.
    - `fallback_payloads`: `raw_payload` (ja decodificado) das fichas SEM
      prescricao aproveitavel; soma `template_slot_targets[].sets_count`,
      exatamente como antes de 2026-10-02.

    `plans_count` conta FICHAS distintas que tocaram o grupo. Grupo com soma
    zero nao aparece. Ordem: maior planejado primeiro."""
    totals: dict[Any, dict[str, Any]] = {}
    plans_by_key: dict[Any, set[Any]] = defaultdict(set)

    def _bucket(key: Any, group_id: Any, group_name: Any) -> dict[str, Any]:
        return totals.setdefault(
            key,
            {
                "muscle_group_id": group_id,
                "muscle_group_name": group_name or OUTROS_GROUP,
                "planned_effective_sets": 0.0,
                "plans_count": 0,
            },
        )

    for row in prescription_rows or ():
        group_id = row.get("muscle_group_id")
        if group_id is None:
            continue
        technique = planned_equivalent(
            row.get("advanced_technique"), row.get("advanced_technique_applications"), catalog
        )
        planned = float(row.get("planned_sets") or 0.0) + technique * float(row.get("planned_weight") or 0.0)
        if planned <= 0.0:
            continue
        key = int(group_id)
        _bucket(key, key, row.get("muscle_group_name"))["planned_effective_sets"] += planned
        plans_by_key[key].add(("plan", row.get("plan_id")))

    for index, payload in enumerate(fallback_payloads or ()):
        targets = payload.get("template_slot_targets") if isinstance(payload, dict) else None
        if not isinstance(targets, list):
            continue
        for target in targets:
            if not isinstance(target, dict) or target.get("sets_count") is None:
                continue
            group_id = target.get("muscle_group_id")
            group_name = target.get("muscle_group_name") or OUTROS_GROUP
            key = int(group_id) if group_id is not None else ("name", group_name)
            _bucket(key, group_id, group_name)["planned_effective_sets"] += float(target["sets_count"])
            plans_by_key[key].add(("payload", index))

    results = []
    for key, item in totals.items():
        if item["planned_effective_sets"] <= 0.0:
            continue
        item["planned_effective_sets"] = round(item["planned_effective_sets"], 2)
        item["plans_count"] = len(plans_by_key[key])
        results.append(item)
    results.sort(key=lambda r: r["planned_effective_sets"], reverse=True)
    return results


def template_cycle_length_days(slots_count: int) -> int:
    """Duracao, em dias, de UMA volta do ciclo de um Modelo: um slot = um dia.

    E a convencao do motor de geracao (`generation/activation_window.py`,
    "1 slot por dia de treino; slot de descanso ocupa o dia e contribui
    zero") e da metodologia (`methodology_volume.cycle_days = len(slots)`). O
    proprio Modelo a declara: slots de descanso so existem porque cada slot e
    um dia ("PPL + UL 5 dias com 2 descansos" tem 7 slots = uma semana).

    Ate 2026-10-02 a meta semanal de `/dashboard/musculos` usava `7 x nº de
    slots` ("uma sessao por slot a cada 7 dias"): um Modelo de 8 slots virava
    um ciclo de 56 dias e a meta semanal saia 7x menor que o planejado - todo
    grupo aparecia ACIMA do planejado. `0`/negativo devolve `0` (sem ciclo)."""
    return max(int(slots_count or 0), 0)


# Os 3 estados de `classify_planned_volume_status` abaixo. Existem porque um
# Modelo `equipment_series` (equipamento + series fixas) nao tem faixa
# min/max: a especificacao do proprio Modelo e a meta, e compara-la com o
# realizado so tem tres respostas honestas - abaixo, dentro da tolerancia ou
# acima do que foi planejado. `BETWEEN_MIN_TARGET`/`BETWEEN_TARGET_MAX` nao
# fazem sentido sem minimo e maximo, por isso NAO sao reaproveitados.
PLANNED_VOLUME_STATUSES: tuple[str, ...] = (
    "BELOW_TARGET",
    "ON_TARGET",
    "ABOVE_TARGET",
)


def classify_planned_volume_status(actual: float, planned_sets: float) -> str:
    """Classifica o volume semanal REALIZADO contra o PLANEJADO pelo Modelo
    `equipment_series` (ambos em series efetivas por semana, mesma unidade)
    em `BELOW_TARGET`, `ON_TARGET` ou `ABOVE_TARGET`.

    Fica AO LADO de `classify_weekly_volume_status` (nao dentro dela): aquela
    e compartilhada por telas que tem min/target/max reais e nao muda aqui.
    A tolerancia e a MESMA (`ON_TARGET_TOLERANCE_PCT`, +-5% do alvo) - nunca
    uma segunda constante.

    Planejado `0` (grupo que o Modelo nao trabalha): a tolerancia e zero, entao
    realizado `0` e `ON_TARGET` e qualquer realizado positivo e `ABOVE_TARGET`
    - trabalho EXTRA em relacao ao plano, nunca deficit. Quem monta o item
    declara a limitacao "grupo fora do Modelo" para a tela nao ler isso como
    excesso.

    O nivel "muito abaixo" (< `FAR_BELOW_PLANNED_RATIO` do planejado) NAO e
    um quarto estado: e a gravidade aditiva `planned_volume_severity`."""
    planned = max(float(planned_sets or 0.0), 0.0)
    tolerance = planned * ON_TARGET_TOLERANCE_PCT
    if actual < planned - tolerance:
        return "BELOW_TARGET"
    if actual <= planned + tolerance:
        return "ON_TARGET"
    return "ABOVE_TARGET"


# Gravidade ADITIVA do `BELOW_TARGET` de `classify_planned_volume_status`
# (decisao do usuario 2026-10-07): realizado abaixo de METADE do planejado e
# "muito abaixo do planejado". O status continua `BELOW_TARGET` (contrato dos
# 3 estados intacto); quem quiser o nivel extra le `planned_volume_severity`.
# Substitui o 0,5 que `MuscleActivationTable.tsx` tinha ate o M76 Lote 4b.
FAR_BELOW_PLANNED_RATIO = 0.5
PLANNED_SEVERITY_FAR_BELOW = "FAR_BELOW"
PLANNED_VOLUME_SEVERITIES: tuple[str, ...] = (PLANNED_SEVERITY_FAR_BELOW,)


def planned_volume_severity(actual: float, planned_sets: float) -> str | None:
    """Gravidade do desvio do realizado contra o planejado, ao lado de
    `classify_planned_volume_status`: `FAR_BELOW` quando o realizado fica
    abaixo de `FAR_BELOW_PLANNED_RATIO` do planejado; `None` nos demais casos
    (inclusive planejado `0`, que nunca e deficit). So existe dentro de
    `BELOW_TARGET`: como 0,5 < 1 - `ON_TARGET_TOLERANCE_PCT`, todo `FAR_BELOW`
    ja e `BELOW_TARGET`."""
    planned = max(float(planned_sets or 0.0), 0.0)
    if planned <= 0:
        return None
    if float(actual or 0.0) < planned * FAR_BELOW_PLANNED_RATIO:
        return PLANNED_SEVERITY_FAR_BELOW
    return None


# M76 E4: avaliacao da tabela "Ativacao muscular" de
# `/registro-exercicios/[activityId]` (antes em `MuscleActivationTable.tsx`, com
# limiares proprios 0,9/0,5 do alvo e 6/9 sem alvo). Origem do alvo -> regra:
# - `template_slot`: alvo do SLOT gravado pela geracao (`template_slot_targets`,
#   series equivalentes planejadas). Plano fixo, sem minimo: os 3 estados de
#   `classify_planned_volume_status` + `severity` (`planned_volume_severity`,
#   "muito abaixo do planejado"); passou do teto do slot (`effective_max_sets`,
#   quando houver) -> `ABOVE_MAX`.
# - `severity` so e preenchida no dominio `plannedVolume` (alvo do slot); nas
#   metas do tipo de treino e generica e sempre `None` (decisao 2026-10-07).
# - `training_type`: meta min/alvo/max do tipo de treino -> os 5 estados de
#   `classify_weekly_volume_status`.
# - `generic_fallback`: sem alvo -> `classify_weekly_volume_status` contra a
#   meta generica `DASHBOARD_FALLBACK_*_SETS` (os mesmos 6/9 de antes).
ACTIVATION_TARGET_TEMPLATE_SLOT = "template_slot"
ACTIVATION_TARGET_TRAINING_TYPE = "training_type"
ACTIVATION_TARGET_GENERIC_FALLBACK = "generic_fallback"
# Dominio de status (vocabulario de `frontend/src/lib/dashboard-status.ts`).
STATUS_DOMAIN_WEEKLY = "weeklyVolume"
STATUS_DOMAIN_PLANNED = "plannedVolume"


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def assess_activation_against_target(actual: float, target: dict[str, Any] | None) -> dict[str, Any]:
    """Status de UM grupo da ativacao de uma sessao contra o alvo resolvido
    por `get_training_type_weekly_targets_for_activity` (ou nenhum). Funcao
    pura; o frontend so desenha `status` no `status_domain` devolvido.

    `max_weekly_sets <= 0` conta como sem teto (o Modo B grava 0 no
    `slot_max` do resumo, ver `_apply_effective_max_sets_fallback`)."""
    actual = float(actual or 0.0)
    target_sets = _positive((target or {}).get("sets_count"))
    max_sets = _positive((target or {}).get("max_weekly_sets"))
    source = (target or {}).get("target_source") or ACTIVATION_TARGET_TEMPLATE_SLOT
    if target_sets is None:
        return {
            "status": classify_weekly_volume_status(
                actual, DASHBOARD_FALLBACK_MIN_SETS, DASHBOARD_FALLBACK_TARGET_SETS, DASHBOARD_FALLBACK_MAX_SETS
            ),
            "status_domain": STATUS_DOMAIN_WEEKLY,
            "target_source": ACTIVATION_TARGET_GENERIC_FALLBACK,
            "target_sets": None,
            "max_sets": None,
            "severity": None,
        }
    min_sets = (target or {}).get("min_weekly_sets")
    severity: str | None = None
    if source == ACTIVATION_TARGET_TRAINING_TYPE and min_sets is not None:
        ceiling = max_sets if max_sets is not None and max_sets >= target_sets else target_sets
        status = classify_weekly_volume_status(actual, min(float(min_sets), target_sets), target_sets, ceiling)
        domain = STATUS_DOMAIN_WEEKLY
    elif max_sets is not None and actual > max_sets:
        status, domain = "ABOVE_MAX", STATUS_DOMAIN_WEEKLY
    else:
        status, domain = classify_planned_volume_status(actual, target_sets), STATUS_DOMAIN_PLANNED
        severity = planned_volume_severity(actual, target_sets)
    return {
        "status": status,
        "status_domain": domain,
        "target_source": source,
        "target_sets": round(target_sets, 2),
        "max_sets": round(max_sets, 2) if max_sets is not None else None,
        "severity": severity,
    }


def assess_activation_rows(rows: list[dict[str, Any]], targets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Anexa `assessment` (ver `assess_activation_against_target`) a cada
    linha de `get_muscle_activation_by_activity`, casando o alvo pelo nome do
    grupo (a mesma chave que a tabela usava)."""
    by_name = {str(t.get("muscle_group_name")): t for t in (targets or []) if t.get("muscle_group_name")}
    return [
        {**row, "assessment": assess_activation_against_target(row.get("total_equivalent"), by_name.get(row.get("muscle_group")))}
        for row in rows
    ]


def _hypertrophy_volume_units(units: float, training_goal: Any) -> float:
    """Ponte para o dono `methodology_volume.hypertrophy_volume_units` (M97
    F2). Import tardio (uma vez): `methodology_volume` importa
    `metrics_registry`, que importa este modulo."""
    global _HYPERTROPHY_VOLUME_UNITS
    if _HYPERTROPHY_VOLUME_UNITS is None:
        from .methodology_volume import hypertrophy_volume_units

        _HYPERTROPHY_VOLUME_UNITS = hypertrophy_volume_units
    return _HYPERTROPHY_VOLUME_UNITS(units, training_goal)


_HYPERTROPHY_VOLUME_UNITS: Optional[Callable[[float, Any], float]] = None


class VolumeService(IntelligenceServiceBase):
    # ------------------------------------------------------------------
    # Leitura de series (janela e historico completo)
    #
    # Ate 2026-09-22 as duas leituras eram UMA query por serie que carregava,
    # repetido em cada linha, tudo que na verdade e constante por atividade
    # (data/nome/horario) ou por exercicio (nome original, dores, grupo
    # muscular primario/secundario). Pior: grupo primario, grupo secundario e
    # `secondary_participation` vinham de SUBCONSULTAS CORRELACIONADAS, que o
    # MySQL reavalia uma vez POR SERIE - dezenas de milhares de vezes num
    # historico real, sempre para devolver o mesmo valor, ja que dependem
    # exclusivamente do exercicio canonico.
    #
    # Evidencia de producao (2026-09-22): `GET /analytics/dashboard-muscle-drilldown`
    # respondia `{"message": "Endpoint request timed out"}` aos 29s do API
    # Gateway mesmo com `days=7` (o custo nao vinha da janela, vinha do
    # historico completo).
    #
    # Agora sao 3 leituras baratas, combinadas em Python:
    #   1. atividades da janela (ou todas, no historico completo) - so os ids;
    #   2. exercicios dessas atividades, com as colunas constantes por
    #      exercicio + os atributos de grupo resolvidos UMA vez por exercicio
    #      do catalogo (`_fetch_group_attributes_by_exercise`);
    #   3. as series desses exercicios (so as colunas de serie).
    # As paginas por atividade/exercicio tambem mantem cada resposta pequena,
    # o que importa no backend de producao: a RDS Data API recusa resultado
    # acima de 1 MB (`UnsupportedResultException`, ver app/db_dataapi.py).
    #
    # O resultado - uma lista de dicts, uma linha por serie, com as MESMAS
    # chaves e valores de antes, na mesma ordem (`exercise_id`, `set_number`) -
    # e identico ao da query antiga; `tests/integration/dataapi/
    # test_dashboard_drilldown_performance.py` compara os dois lado a lado no
    # mesmo banco, nos dois backends.
    # ------------------------------------------------------------------

    # Atividades por pagina nas leituras de exercicios e de series, e
    # exercicios do catalogo por pagina na leitura de atributos de grupo.
    # Valores conservadores: com ~10 exercicios e ~40 series por treino, cada
    # pagina fica na casa de milhares de linhas curtas, com ampla folga para o
    # teto de 1 MB da Data API.
    ACTIVITY_PAGE_SIZE = 100
    EXERCISE_PAGE_SIZE = 400

    # `gym_equipment`/`equipment_exercises`: equipamento CANONICO e convencao
    # de carga do exercicio - a chave de comparabilidade que o sinal
    # "Desempenho recente" usa (`exercise_performance`), lidas como no
    # `exposure_service`. LEFT JOINs que nunca duplicam linha
    # (`gym_equipment.id` e PK; `equipment_exercises` tem UNIQUE
    # (equipment_id, exercise_id)).
    _EXERCISE_SCOPE_FROM = """
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN exercise_catalog zm ON zm.id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
            LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
            LEFT JOIN equipment_exercises eqex
              ON eqex.equipment_id = ge.equipment_id
             AND eqex.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
             AND eqex.is_active = 1
    """

    # Colunas constantes por exercicio da JANELA. `mapping_id` continua sendo
    # `zm.id` (NULL quando o exercicio nao esta no catalogo), diferente do
    # historico completo abaixo - comportamento preservado de proposito.
    # `activity_name`/`activity_type`/`duration_seconds` sao aditivos (Fase 2
    # do dashboard novo, ver get_workout_sessions_timeline).
    _WINDOW_EXERCISE_SELECT = """
              e.id AS exercise_id,
              a.id AS activity_id,
              a.local_date,
              a.name AS activity_name,
              a.activity_type,
              a.duration_seconds,
              {trained_at} AS trained_at,
              zm.id AS mapping_id,
              e.training_goal AS training_goal,
              a.deload_source AS deload_source,
              CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS canonical_exercise_id
    """

    _FULL_HISTORY_EXERCISE_SELECT = """
              e.id AS exercise_id,
              a.id AS activity_id,
              a.local_date,
              {trained_at} AS trained_at,
              CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
              e.original_name,
              e.muscle_pain,
              e.joint_pain,
              ge.equipment_id AS equipment_id,
              eqex.load_convention AS load_convention,
              e.training_goal AS training_goal,
              a.deload_source AS deload_source,
              CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS canonical_exercise_id
    """

    def __init__(self) -> None:
        # Cache de INSTANCIA (nao de processo): toda rota constroi um
        # `VolumeService()` dentro do proprio handler, entao o cache vive o
        # tempo de uma requisicao e nunca serve dado velho para a requisicao
        # seguinte. Existe porque uma unica tela do dashboard chega a pedir o
        # MESMO overview varias vezes (o drill-down pedia duas: uma direta e
        # outra por dentro de `get_recovery_overview`; a extinta `dashboard-alerts`
        # pedia quatro), e cada pedido relia o historico inteiro do usuario.
        self._window_sets_cache: dict[tuple[int, int | None], list[dict[str, Any]]] = {}
        self._full_history_cache: dict[tuple[int | None, int | None], list[dict[str, Any]]] = {}
        # Chave `(user_id, history_days)`: `history_days=None` (historico
        # completo) e `history_days=180` sao datasets diferentes e nao podem
        # compartilhar cache.
        self._dose_context_cache: dict[tuple[int | None, int | None], dict[str, Any]] = {}
        self._group_attributes_cache: dict[int, dict[str, Any]] = {}
        # Overview COMPLETO (antes do corte por `limit`), por (days, user_id):
        # `limit` sempre foi um truncamento do mesmo ranking ordenado, entao
        # guardar a lista inteira e fatiar atende qualquer `limit` sem
        # recalcular. Devolvido em copia profunda - o chamador pode alterar o
        # que recebe sem contaminar o cache.
        self._overview_cache: dict[tuple[int, int | None, int | None], list[dict[str, Any]]] = {}
        # Ranking COMPLETO de exercicios por grupo (antes do corte por
        # `limit`), por (days, user_id) - M19. Mesmo raciocinio do
        # `_overview_cache`: `limit` so trunca um ranking ja ordenado.
        self._top_exercises_cache: dict[tuple[int, int | None], dict[Any, list[dict[str, Any]]]] = {}
        # M19: `exercise_muscle_contributions` e `muscle_groups` sao lidas
        # varias vezes na MESMA requisicao (overview, tendencia semanal e
        # ranking de exercicios pedem o mesmo conjunto). Ambas sao caches de
        # INSTANCIA, como todos os acima - vivem o tempo de uma requisicao.
        self._contributions_cache: dict[int, list[dict]] = {}
        self._contributions_fetched: set[int] = set()
        self._muscle_group_names_cache: dict[Any, str] | None = None
        # Fase 5 (PR 4): catalogo de tecnicas da requisicao, lido so se alguma
        # leitura trouxer aplicacao de tecnica.
        self._technique_catalog = new_catalog()
        # Fuso do usuario (`users.timezone`) por user_id, lido no maximo uma
        # vez por requisicao - ver `IntelligenceServiceBase._user_now`.
        self._timezone_cache: dict[int | None, str] = {}

    def technique_catalog(self):
        catalog = getattr(self, "_technique_catalog", None)
        if catalog is None:
            catalog = self._technique_catalog = new_catalog()
        return catalog

    def _ensure_set_units(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Garante `set_units` (series equivalentes da linha) em linhas serie
        a serie. Os produtores ja gravam; isto cobre linhas montadas por fora
        (testes, datasets injetados)."""
        if any("set_units" not in row for row in rows):
            annotate_set_units(rows, self.technique_catalog())
        return rows

    def _chunks(self, values: list[Any], size: int) -> list[list[Any]]:
        return [values[start : start + size] for start in range(0, len(values), size)]

    def _fetch_group_attributes_by_exercise(self, exercise_ids: set[int]) -> dict[int, dict[str, Any]]:
        """Grupo primario/secundario (nome + id) e `secondary_participation` de
        cada exercicio do CATALOGO (id canonico, ja resolvido por
        `exercise_legacy_id_map`).

        Mesmo criterio das subconsultas correlacionadas que estavam embutidas
        na query de series: entre as contribuicoes ativas
        (`exercise_muscle_contributions.is_active = 1`) do exercicio, a de
        MENOR `id` com aquele `role` (`ORDER BY emc.id ASC LIMIT 1`). O nome
        e o id do grupo saem de `muscle_groups` por LEFT JOIN - continuam
        `None` quando a linha de `muscle_groups` nao existe -, e o nome vazio
        vira `None` (o antigo `NULLIF(pg.name, '')`). A comparacao de `role`
        e feita sem diferenciar maiusculas, como a collation
        `utf8mb4_0900_ai_ci` do MySQL ja fazia.
        """
        missing = {int(value) for value in exercise_ids if value} - self._group_attributes_cache.keys()
        for page in self._chunks(sorted(missing), self.EXERCISE_PAGE_SIZE):
            placeholders = ",".join(["%s"] * len(page))
            rows = self.fetch_all(
                f"""
                SELECT
                  emc.exercise_id,
                  emc.role,
                  emc.stimulus_weight,
                  mg.id AS muscle_group_id,
                  mg.name AS muscle_group_name
                FROM exercise_muscle_contributions emc
                LEFT JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
                WHERE emc.exercise_id IN ({placeholders})
                  AND emc.is_active = 1
                  AND emc.role IN ('PRIMARY', 'SECONDARY')
                ORDER BY emc.id ASC
                """,
                tuple(page),
            )
            for exercise_id in page:
                self._group_attributes_cache.setdefault(exercise_id, dict(_EMPTY_GROUP_ATTRIBUTES))
            for row in rows:
                attributes = self._group_attributes_cache[int(row["exercise_id"])]
                role = (row.get("role") or "").strip().upper()
                name = row.get("muscle_group_name") or None
                if role == "PRIMARY" and attributes["_primary_seen"] is False:
                    attributes["_primary_seen"] = True
                    attributes["primary_group"] = name
                    attributes["primary_group_id"] = row.get("muscle_group_id")
                elif role == "SECONDARY" and attributes["_secondary_seen"] is False:
                    attributes["_secondary_seen"] = True
                    attributes["secondary_group"] = name
                    attributes["secondary_group_id"] = row.get("muscle_group_id")
                    attributes["secondary_participation"] = row.get("stimulus_weight")
        return self._group_attributes_cache

    def _fetch_set_rows(self, activity_ids: list[int]) -> list[dict[str, Any]]:
        """Series (so as colunas de serie) das atividades informadas.

        Filtra por `e.activity_id`, nao por uma lista de `es.exercise_id`:
        com a lista de exercicios o `EXPLAIN` mostrava `type=ALL` sobre
        `exercise_sets` a cada pagina (uma pagina cobre uma fracao grande da
        tabela, e o otimizador prefere varrer). Entrando por
        `idx_exercises_activity_sequence` e saindo pela chave UNIQUE
        (`exercise_id`, `set_number`), o acesso fica todo por indice. As
        linhas devolvidas sao as mesmas."""
        placeholders = ",".join(["%s"] * len(activity_ids))
        return self.fetch_all(
            f"""
            SELECT
              es.id AS set_id,
              es.exercise_id,
              es.set_number,
              es.reps,
              es.weight_kg,
              es.rpe,
              es.set_type,
              es.technique_code,
              es.anchor_set_number,
              es.measure
            FROM exercises e
            JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
            WHERE e.activity_id IN ({placeholders})
              AND {_ES_NOT_PREPARATORY}
            """,
            tuple(activity_ids),
        )

    def _fetch_sets_dataset(
        self, exercise_select: str, activity_where: str, params: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        """Monta a lista "uma linha por serie" a partir das 3 leituras
        descritas no bloco de comentario acima. `activity_where` e a condicao
        sobre `activities a` (janela de dias e/ou usuario)."""
        activity_rows = self.fetch_all(
            f"SELECT a.id AS id FROM activities a WHERE {activity_where} ORDER BY a.id ASC",
            params,
        )
        activity_ids = [int(row["id"]) for row in activity_rows]
        if not activity_ids:
            return []

        by_exercise: dict[int, dict[str, Any]] = {}
        canonical_ids: set[int] = set()
        for page in self._chunks(activity_ids, self.ACTIVITY_PAGE_SIZE):
            placeholders = ",".join(["%s"] * len(page))
            for row in self.fetch_all(
                f"""
                SELECT
                {exercise_select}
                {self._EXERCISE_SCOPE_FROM}
                WHERE a.id IN ({placeholders})
                """,
                tuple(page),
            ):
                exercise_row = dict(row)
                canonical_id = exercise_row.pop("canonical_exercise_id")
                if canonical_id:
                    canonical_ids.add(int(canonical_id))
                exercise_row["_canonical_exercise_id"] = canonical_id
                by_exercise[int(exercise_row["exercise_id"])] = exercise_row
        if not by_exercise:
            return []

        attributes = self._fetch_group_attributes_by_exercise(canonical_ids)
        for exercise_row in by_exercise.values():
            canonical_id = exercise_row.pop("_canonical_exercise_id")
            resolved = attributes.get(int(canonical_id)) if canonical_id else None
            for key in _GROUP_ATTRIBUTE_KEYS:
                exercise_row[key] = (resolved or _EMPTY_GROUP_ATTRIBUTES)[key]

        composed: list[dict[str, Any]] = []
        for page in self._chunks(activity_ids, self.ACTIVITY_PAGE_SIZE):
            for set_row in self._fetch_set_rows(page):
                exercise_row = by_exercise.get(int(set_row["exercise_id"]))
                if exercise_row is None:
                    continue
                composed.append({**set_row, **exercise_row})
        # MESMA ordem da query antiga (`ORDER BY es.exercise_id, es.set_number`),
        # que a chave UNIQUE (exercise_id, set_number) torna total.
        composed.sort(key=lambda row: (int(row["exercise_id"]), int(row["set_number"] or 0)))
        annotate_set_units(composed, self.technique_catalog())
        return composed

    def _fetch_window_sets(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        # user_id opcional (None preserva o comportamento historico sem
        # filtro, usado pelos consumidores existentes de get_muscle_group_overview
        # - mesma lacuna documentada em IntelligenceServiceBase.get_latest_intervals_wellness).
        # Os endpoints novos do dashboard (Fase 1, ver analytics.py
        # dashboard-muscle-status/dashboard-muscle-drilldown)
        # sempre propagam identity.user_id.
        cache_key = (days, user_id)
        cached = self._window_sets_cache.get(cache_key)
        if cached is not None:
            return cached

        activity_where = "a.local_date >= %s"
        params: list[Any] = [self._days_ago(user_id, days)]
        if user_id is not None:
            activity_where += " AND a.user_id = %s"
            params.append(user_id)

        rows = self._fetch_sets_dataset(
            self._WINDOW_EXERCISE_SELECT.format(trained_at=self._trained_at_sql(user_id)),
            activity_where,
            tuple(params),
        )
        self._window_sets_cache[cache_key] = rows
        # Copia rasa da LISTA: o chamador pode ordenar/filtrar o que recebe sem
        # embaralhar o cache (as linhas em si sempre foram tratadas como
        # somente leitura por todos os consumidores).
        return list(rows)

    def _fetch_full_history_sets(
        self, user_id: int | None = None, history_days: int | None = None
    ) -> list[dict[str, Any]]:
        """Dataset completo (sem filtro de dias), usado para calcular is_new
        (contagem de ocorrencias historicas de um exercicio) e a referencia de
        e1RM (mediana dos ultimos 28 dias) do modelo v2 de recuperacao.

        `user_id` opcional, mesma ressalva de `_fetch_window_sets` acima.

        `history_days` (F1.2, ADITIVO - default `None` = comportamento
        historico, inalterado para TODOS os chamadores existentes): limita a
        leitura aos ultimos N dias. Serve ao `muscle_summary_service`, que
        precisa da prontidao mas nao pode pagar a leitura de 3 anos de series
        (medicao: 8,3s so nesta leitura, no shim da Data API, com 400 sessoes
        / 16.000 series - ver `tests/integration/dataapi/
        test_dashboard_drilldown_performance.py`). O que muda com o limite:
        a referencia de e1RM (mediana dos ultimos 28 dias) e preservada
        integralmente enquanto `history_days >= 28`; so a contagem de
        ocorrencias de `is_new` passa a olhar a janela limitada - um
        exercicio ausente ha mais de `history_days` volta a contar como novo.
        Por isso o consumidor DECLARA essa limitacao no payload em vez de
        escondê-la."""
        cache_key = (user_id, history_days)
        cached = self._full_history_cache.get(cache_key)
        if cached is not None:
            return cached

        clauses: list[str] = []
        params: list[Any] = []
        if history_days is not None:
            clauses.append("a.local_date >= %s")
            params.append(self._days_ago(user_id, int(history_days)))
        if user_id is not None:
            clauses.append("a.user_id = %s")
            params.append(user_id)
        activity_where = " AND ".join(clauses) if clauses else "1 = 1"

        rows = self._fetch_sets_dataset(
            self._FULL_HISTORY_EXERCISE_SELECT.format(trained_at=self._trained_at_sql(user_id)),
            activity_where,
            tuple(params),
        )
        self._full_history_cache[cache_key] = rows
        return list(rows)

    @staticmethod
    def _exercise_identity(row: dict[str, Any]) -> tuple[str, Any]:
        mapping_id = row.get("mapping_id")
        if mapping_id:
            return ("mapping", mapping_id)
        return ("name", (row.get("original_name") or "").strip().lower())

    @staticmethod
    def _group_contributions(
        primary: str | None,
        secondary: str | None,
        primary_id: int | None = None,
        secondary_id: int | None = None,
    ) -> list[tuple[str, str, int | None]]:
        """Retorna [(grupo, papel, muscle_group_id)] para uma serie. A
        participacao numerica (quanto aquele papel conta) nao e resolvida
        aqui: vem do peso da contribuicao (`exercise_muscle_contributions`).

        muscle_group_id (FK para muscle_groups) e carregado junto do nome
        para os consumidores agruparem por id em vez de por texto; fica None
        para o fallback 'Outros' (nao existe linha em muscle_groups para
        ele).

        Regra: uma serie conta como 'primary' para o grupo primario do
        exercicio e, quando existe um grupo secundario DISTINTO, tambem como
        'secondary' para ele. Quando so ha secondary_group cadastrado (sem
        primary), mantem o fallback historico e conta como 'primary' para
        ele. Sem nenhum grupo mapeado, cai em 'Outros'. Desde a migracao
        para muscle_groups (FK), secondary_group so pode conter UM nome —
        nao ha mais o que "splitar" por virgula (era o bug real que motivou
        a mudanca de schema)."""
        primary_name = (primary or "").strip() or None
        secondary_name = (secondary or "").strip() or None
        if secondary_name == primary_name:
            secondary_name = None
            secondary_id = None

        if primary_name:
            contributions: list[tuple[str, str, int | None]] = [(primary_name, "primary", primary_id)]
            if secondary_name:
                contributions.append((secondary_name, "secondary", secondary_id))
            return contributions
        if secondary_name:
            return [(secondary_name, "primary", secondary_id)]
        return [(OUTROS_GROUP, "primary", None)]

    def muscle_group_names_by_id(self) -> dict[Any, str]:
        """Lookup `muscle_groups.id -> name`, UMA vez por requisicao (M19).

        A tabela e minuscula, mas a MESMA requisicao a pedia ate tres vezes
        (`get_muscle_group_overview`, `get_rolling_weekly_trend_by_muscle_group`
        e o extinto alerta de volume de `dashboard-alerts`) - e no shim da RDS
        Data API o que custa e a quantidade de idas ao banco, nao o tamanho da
        resposta. Cache de INSTANCIA, como os demais: morre com a requisicao,
        entao nunca serve nome velho para a requisicao seguinte."""
        if self._muscle_group_names_cache is None:
            # Alem do cache de instancia: um memo por REQUISICAO (PERF-01) para
            # as outras leituras da mesma tabela na mesma chamada.
            rows = request_memo.memoize(
                ("muscle_groups", "id_name"), lambda: self.fetch_all("SELECT id, name FROM muscle_groups")
            )
            self._muscle_group_names_cache = {row["id"]: row["name"] for row in rows}
        return dict(self._muscle_group_names_cache)

    def _fetch_contributions_by_mapping(self, mapping_ids: set[int]) -> dict[int, list[dict]]:
        """Busca em uma unica query por conjunto novo de ids (evita N+1) as contribuicoes explicitas
        (role + pesos) cadastradas em exercise_muscle_contributions (Fase 1
        da reforma de volume) para o conjunto de mapping_ids observado na
        janela de dados. Fonte primaria de peso para
        sets_count/volume_kg/dose ponderados; ver _resolve_contributions."""
        if not mapping_ids:
            return {}
        # Cache de INSTANCIA por exercise_id (M19): a mesma requisicao pede
        # este conjunto ate tres vezes (overview, tendencia semanal e ranking
        # de exercicios), sempre com o mesmo conjunto de ids da janela. So os
        # ids AINDA NAO consultados vao ao banco; o resto sai da memoria.
        # `_contributions_fetched` guarda os ids JA CONSULTADOS (inclusive os
        # que nao tem nenhuma linha) para nao reconsultar a cada chamada - e
        # so uma memoria de "ja perguntei nesta requisicao", nao de ausencia
        # permanente: a instancia morre com a requisicao.
        faltando = {int(value) for value in mapping_ids if value} - self._contributions_fetched
        if faltando:
            placeholders = ",".join(["%s"] * len(faltando))
            rows = self.fetch_all(
                f"""
                SELECT exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight
                FROM exercise_muscle_contributions
                WHERE exercise_id IN ({placeholders}) AND is_active = 1
                ORDER BY id ASC
                """,
                tuple(sorted(faltando)),
            )
            for row in rows:
                self._contributions_cache.setdefault(int(row["exercise_id"]), []).append(row)
            self._contributions_fetched |= faltando

        # MESMA forma de antes: so os ids QUE TEM linha aparecem no dicionario
        # (varios chamadores usam a verdade/falsidade do dicionario inteiro).
        result: dict[int, list[dict]] = defaultdict(list)
        for value in mapping_ids:
            if not value:
                continue
            cached = self._contributions_cache.get(int(value))
            if cached:
                # Copia da lista: o chamador nunca mexe no cache.
                result[int(value)] = list(cached)
        return result

    @staticmethod
    def _resolve_contributions(
        mapping_id: int | None,
        contributions_by_mapping: dict[int, list[dict]],
        primary_name: str | None,
        secondary_name: str | None,
        primary_id: int | None,
        secondary_id: int | None,
        secondary_participation: float | None,
    ) -> list[dict[str, Any]]:
        """Resolve as contribuicoes (role + pesos) de uma serie para um dado
        exercicio. Fonte primaria: linhas explicitas em
        exercise_muscle_contributions para o mapping_id (via
        contributions_by_mapping, ja carregado em bulk). Fallback (quando o
        mapping nao tem nenhuma linha explicita na tabela nova - ainda deve
        acontecer raramente apos o backfill da Fase 1, mas cobre mappings criados
        depois sem contribuicao cadastrada): reaproveita a logica estrutural de
        _group_contributions (NAO alterada) + a mesma formula de peso ja usada
        pelo antigo MuscleRecoveryService.participation (removido no M76 Lote
        2c). Os pesos default por papel vem de
        `muscle_contribution_resolver.DEFAULT_WRITE_WEIGHTS` (dono unico, M76
        Lote 3c). Sem nenhum grupo, a serie cai em "Outros" como PRIMARY -
        politica unica, aplicada tambem por
        `_resolve_muscle_contributions_for_mappings`."""
        if mapping_id and mapping_id in contributions_by_mapping:
            return [
                {
                    "muscle_group_id": row["muscle_group_id"],
                    "role": row["role"],
                    "stimulus_weight": float(row["stimulus_weight"]),
                    "fatigue_weight": float(row["fatigue_weight"]),
                    # Fonte nova so guarda muscle_group_id, nao o nome; quem
                    # consome resolve o nome via lookup bulk em
                    # muscle_groups (ver get_muscle_group_overview).
                    "muscle_group_name": None,
                }
                for row in contributions_by_mapping[mapping_id]
            ]

        legacy = VolumeService._group_contributions(primary_name, secondary_name, primary_id, secondary_id)
        resolved: list[dict[str, Any]] = []
        for name, role, group_id in legacy:
            role_upper = role.upper()
            if role_upper == "PRIMARY":
                weight = DEFAULT_WRITE_WEIGHTS["PRIMARY"]
            elif secondary_participation is not None:
                weight = float(secondary_participation)
            else:
                weight = DEFAULT_WRITE_WEIGHTS["SECONDARY"]
            resolved.append({
                "muscle_group_id": group_id,
                "role": role_upper,
                "stimulus_weight": weight,
                "fatigue_weight": weight,
                # Fallback legado ja resolve o nome via texto (nao precisa
                # de lookup por id/DB extra).
                "muscle_group_name": name,
            })
        return resolved

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _session_sort_key(row: dict[str, Any]) -> datetime:
        trained_at = VolumeService._parse_datetime(row.get("trained_at"))
        if trained_at:
            return trained_at
        local_date = row.get("local_date")
        try:
            return datetime.combine(date.fromisoformat(str(local_date)[:10]), datetime.min.time())
        except ValueError:
            return datetime.min

    # ------------------------------------------------------------------
    # Grao intermediario do historico: UM item por (exercicio, sessao)
    # (M20)
    #
    # O modelo de recuperacao nunca precisou de uma linha por SERIE do
    # historico inteiro: tudo que ele consome - dose da sessao, e1RM da
    # sessao, queda de repeticoes, dor, "exercicio novo" - e por
    # (exercise_id, activity_id). Isolar esse grao permite produzi-lo de
    # duas formas EQUIVALENTES:
    #
    #   1. `_history_items_from_sets`: a partir das series lidas uma a uma
    #      pelo caminho antigo (1+N+N consultas paginadas);
    #   2. `intelligence/recovery_read_model.py`: as MESMAS series, so que
    #      empacotadas por sessao numa unica consulta.
    #
    # Dai para baixo o codigo e o MESMO nos dois caminhos - inclusive a ordem
    # em que a dose e somada, que e o que torna os dois resultados identicos
    # ate o ultimo bit. Nenhuma formula do modelo `fullbody-no-reps-v1` foi
    # duplicada nem reescrita.
    # ------------------------------------------------------------------

    @staticmethod
    def _rep_drop_from_sets(rows_sorted: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Queda expressiva de repeticoes (> `REP_DROP_MIN_FRACTION`) entre a
        PRIMEIRA e a ULTIMA serie de TRABALHO de um exercicio, com a MESMA
        carga. `None` quando nao ha queda; senao as reps 1a -> ultima.

        So series `working` entram (Lote 1/P2: ajuste e backoff na mesma carga
        entravam por engano) e a queda com a ultima serie em RPE >=
        `REP_DROP_IGNORE_FROM_RPE` nao conta (perto da falha a queda e
        esperada).

        Lote T3 (medidas de serie): so series de REPETICOES
        (`set_measures.is_rep_based`; ausente = reps) - o maximo de reps nao
        e a serie de trabalho com alvo de reps e a serie por tempo nao tem reps."""
        working = [row for row in rows_sorted if is_working_set(row) and is_rep_based(row.get("measure"))]
        if len(working) < 2:
            return None
        first_set, last_set = working[0], working[-1]
        if (
            first_set.get("reps") is None
            or last_set.get("reps") is None
            or first_set.get("weight_kg") is None
            or last_set.get("weight_kg") is None
            or float(first_set["weight_kg"]) != float(last_set["weight_kg"])
            or float(first_set["reps"]) <= 0
        ):
            return None
        last_rpe = last_set.get("rpe")
        if last_rpe is not None and float(last_rpe) >= REP_DROP_IGNORE_FROM_RPE:
            return None
        drop = 1.0 - (float(last_set["reps"]) / float(first_set["reps"]))
        if drop <= REP_DROP_MIN_FRACTION:
            return None
        return {"first_reps": int(first_set["reps"]), "last_reps": int(last_set["reps"])}

    @classmethod
    def _excessive_rep_drop_from_sets(cls, rows_sorted: list[dict[str, Any]]) -> bool:
        """Predicado do sinal (ver `_rep_drop_from_sets`)."""
        return cls._rep_drop_from_sets(rows_sorted) is not None

    def _history_items_from_sets(self, all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Caminho LEGADO: agrupa as series ja lidas em itens por
        (exercise_id, activity_id), guardando as proprias series em
        `set_rows` para que a dose continue sendo somada serie a serie, na
        ordem original."""
        rows_by_exercise_activity: dict[tuple[Any, int], list[dict[str, Any]]] = defaultdict(list)
        for row in all_rows:
            rows_by_exercise_activity[(row["exercise_id"], row["activity_id"])].append(row)

        items: list[dict[str, Any]] = []
        for (exercise_id, activity_id), rows in rows_by_exercise_activity.items():
            first_row = rows[0]
            item = {
                "exercise_id": exercise_id,
                "activity_id": activity_id,
                "mapping_id": first_row.get("mapping_id"),
                "original_name": first_row.get("original_name"),
                "local_date": first_row.get("local_date"),
                "trained_at": first_row.get("trained_at"),
                "muscle_pain": first_row.get("muscle_pain"),
                "joint_pain": first_row.get("joint_pain"),
                # Chave de comparabilidade do sinal "Desempenho recente".
                "equipment_id": first_row.get("equipment_id"),
                "load_convention": first_row.get("load_convention"),
                # M97 F1: objetivo da execucao - so separa a comparacao do
                # sinal "Desempenho recente" (troca de objetivo = sem par
                # comparavel); a dose de recuperacao conta tudo.
                "training_goal": first_row.get("training_goal"),
                # M97 D: sessao de deload - conta na dose, mas fica fora do
                # e1RM de referencia e do "Desempenho recente".
                "deload_source": first_row.get("deload_source"),
                "set_rows": rows,
            }
            for key in _GROUP_ATTRIBUTE_KEYS:
                item[key] = first_row.get(key)
            items.append(self._complete_history_item(item))
        return items

    @classmethod
    def _complete_history_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        """Ordena as series do item e calcula o que o modelo deriva delas -
        melhor e1RM da sessao e queda expressiva de repeticoes. UNICO lugar
        onde isso e feito, nos DOIS caminhos de leitura: o que o agregado
        entrega e sempre a serie crua, nunca um numero ja calculado.

        O melhor e1RM (base do `load_spike_factor`) so olha series de
        TRABALHO (`is_working_set`: sem aquecimento/ajuste, backoff nem
        aplicacao de tecnica) e so exercicios cuja convencao de carga tem e1RM
        (`supports_e1rm`: assistido/peso corporal nao) - M76, Lote 3a. Antes,
        um drop ou a carga de ASSISTENCIA do graviton entravam no "pico de
        carga" da sessao. e1RM pelo dono (`domain/e1rm`), sem RPE -> sem e1RM."""
        rows_sorted = sorted(item.get("set_rows") or [], key=lambda r: r.get("set_number") or 0)
        item["set_rows"] = rows_sorted

        best_e1rm: float | None = None
        if supports_e1rm(normalize_load_convention(item.get("load_convention"))):
            for row in rows_sorted:
                # Lote T3: e1RM so de serie de repeticoes.
                if not is_working_set(row) or not is_rep_based(row.get("measure")):
                    continue
                value = e1rm.estimated_1rm(row.get("weight_kg"), row.get("reps"), row.get("rpe"))
                if value is not None and value > (best_e1rm if best_e1rm is not None else 0.0):
                    best_e1rm = value

        item["best_e1rm"] = best_e1rm
        # Queda de repeticoes compara so series de TRABALHO (`is_working_set`):
        # um mini-bloco de rest-pause (mesma carga, poucas reps) e a propria
        # tecnica (Fase 5, PR 4); ajuste/backoff nao sao a serie de trabalho
        # (Lote 1/P2). O detalhe (exercicio e reps) vai para o blocking_signal.
        drop = cls._rep_drop_from_sets(rows_sorted)
        item["excessive_rep_drop"] = drop is not None
        item["rep_drop_detail"] = (
            {
                "exercise_id": item.get("mapping_id"),
                "exercise_name": item.get("original_name"),
                **drop,
            }
            if drop is not None
            else None
        )
        return item

    @staticmethod
    def _session_dose_for_item(
        item: dict[str, Any],
        fatigue_weight: float,
        load_spike_f: float,
        exercise_f: float,
        catalog: Any = None,
    ) -> float:
        """Dose de UM exercicio numa sessao para UM grupo muscular: soma
        serie a serie, na mesma ordem de multiplicacao de sempre.

        Os dois caminhos de leitura (serie a serie e empacotado) chegam aqui
        com as MESMAS series, entao a soma - que em ponto flutuante depende
        da ordem - da exatamente o mesmo numero.

        Fator por tipo de serie (Fase 5, PR 4): `recovery_factor` da tecnica
        da linha (`advanced_techniques.recovery_cost_factor`), com fallback no
        fator fixo por `set_type` - a mesma regra para o dashboard, a geracao
        de ficha e o Modo Treino, que leem todos este overview."""
        from .muscle_recovery_service import MuscleRecoveryService

        dose = 0.0
        for row in item.get("set_rows") or []:
            # Lote T3: reps da dose pela medida (`dose_reps_for_measure`: serie
            # por tempo -> sem reps -> `DOSE_REPS_WHEN_MISSING`); conta 1 serie.
            reps_for_dose, rpe_for_dose = e1rm.dose_inputs(
                dose_reps_for_measure(row.get("measure"), row.get("reps")), row.get("rpe")
            )

            effort_f = MuscleRecoveryService.effort_points(rpe_for_dose)
            rep_f = MuscleRecoveryService.repetition_factor(reps_for_dose)
            type_f = realized_recovery_factor(row, catalog)

            dose += effort_f * fatigue_weight * rep_f * type_f * load_spike_f * exercise_f
        return dose

    def _compute_session_dose_context(
        self, history_items: list[dict[str, Any]], user_id: int | None = None, history_days: int | None = None
    ) -> dict[str, Any]:
        """Pre-processa o historico (grao `(exercicio, sessao)`) para dar
        suporte ao modelo v2: ranking de ocorrencias por exercicio (is_new) e
        e1RM de referencia (mediana dos ultimos 28 dias) por exercicio.

        `user_id` e so a chave do cache de instancia (o calculo continua
        saindo exclusivamente de `history_items`, que ja vem filtrado por
        usuario); quando informado, duas chamadas na mesma requisicao
        reaproveitam o mesmo resultado em vez de reprocessar o historico
        inteiro.
        """
        cache_key = (user_id, history_days)
        cached = self._dose_context_cache.get(cache_key) if user_id is not None else None
        if cached is not None:
            return cached

        # 1) Ranking de ocorrencias por identidade de exercicio (para is_new)
        sessions_by_identity: dict[tuple[str, Any], dict[int, datetime]] = defaultdict(dict)
        for item in history_items:
            identity = self._exercise_identity(item)
            activity_id = item["activity_id"]
            if activity_id not in sessions_by_identity[identity]:
                sessions_by_identity[identity][activity_id] = self._session_sort_key(item)

        occurrence_rank: dict[tuple[tuple[str, Any], int], int] = {}
        for identity, activities in sessions_by_identity.items():
            ordered = sorted(activities.items(), key=lambda kv: (kv[1], kv[0]))
            for rank, (activity_id, _sort_key) in enumerate(ordered, start=1):
                occurrence_rank[(identity, activity_id)] = rank

        # 2) e1RM por (identidade, sessao): melhor valor entre as series
        #    daquele exercicio naquela sessao (o item ja traz o melhor da
        #    sua propria linha de exercicio; o maximo aqui cobre o caso de o
        #    MESMO exercicio aparecer duas vezes na mesma sessao).
        best_e1rm_by_identity_session: dict[tuple[tuple[str, Any], int], float] = {}
        session_dates_by_identity: dict[tuple[str, Any], dict[int, datetime]] = defaultdict(dict)
        for item in history_items:
            identity = self._exercise_identity(item)
            activity_id = item["activity_id"]
            session_dates_by_identity[identity][activity_id] = self._session_sort_key(item)

            session_e1rm = item.get("best_e1rm")
            if session_e1rm is None:
                continue
            key = (identity, activity_id)
            if float(session_e1rm) > best_e1rm_by_identity_session.get(key, 0.0):
                best_e1rm_by_identity_session[key] = float(session_e1rm)

        # M97 D: sessao de deload (carga reduzida de proposito) nao entra no
        # e1RM de REFERENCIA das outras sessoes (dono `domain/deload.py`); a
        # dose dela continua contando.
        from ..domain.deload import is_deload_session

        deload_activity_ids = {
            item["activity_id"] for item in history_items if is_deload_session(item.get("deload_source"))
        }
        reference_e1rm_by_identity_session: dict[tuple[tuple[str, Any], int], float | None] = {}
        for identity, activities in session_dates_by_identity.items():
            ordered = sorted(activities.items(), key=lambda kv: kv[1])
            for activity_id, session_date in ordered:
                window_start = session_date - timedelta(days=LOAD_REFERENCE_WINDOW_DAYS)
                priors = [
                    best_e1rm_by_identity_session[(identity, other_id)]
                    for other_id, other_date in ordered
                    if other_id != activity_id
                    and other_id not in deload_activity_ids
                    and window_start <= other_date < session_date
                    and (identity, other_id) in best_e1rm_by_identity_session
                ]
                reference_e1rm_by_identity_session[(identity, activity_id)] = (
                    statistics.median(priors)
                    if len(priors) >= MIN_REFERENCE_OBSERVATIONS
                    else None
                )

        # O antigo passo 3 (rpe_target do plano por atividade, usado so por
        # `rpe_anticipated`) foi removido em 2026-09-14: RPE acima do alvo do
        # plano deixou de ser sinal de recuperacao.

        # 3) `performance_ratio` ("Desempenho recente") da ULTIMA sessao de
        #    cada exercicio, pela fonte unica de progresso
        #    (`exercise_performance` -> `dashboard/comparability`): so dentro
        #    da chave exercicio+equipamento+convencao, queda = REGRESSING
        #    contra a sessao anterior comparavel. A referencia de 28 dias
        #    acima continua existindo SO para o `load_spike_factor` da dose.
        from .exercise_performance import latest_performance_ratios

        performance_ratio_by_session = latest_performance_ratios(
            history_items, identity=self._exercise_identity, sort_key=self._session_sort_key
        )

        context = {
            "occurrence_rank": occurrence_rank,
            "best_e1rm_by_identity_session": best_e1rm_by_identity_session,
            "reference_e1rm_by_identity_session": reference_e1rm_by_identity_session,
            "performance_ratio_by_session": performance_ratio_by_session,
        }
        if user_id is not None:
            self._dose_context_cache[cache_key] = context
        return context

    def get_muscle_group_overview(
        self,
        days: int = 14,
        limit: int = 8,
        user_id: int | None = None,
        history_days: int | None = None,
        datasets: dict[str, Any] | None = None,
        now_local: datetime | None = None,
    ) -> list[dict[str, Any]]:
        # Import tardio: muscle_recovery_service importa VolumeService no
        # topo do modulo, entao um import de topo aqui criaria um ciclo.
        from .muscle_recovery_service import MuscleRecoveryService

        # user_id opcional (None preserva o comportamento historico sem
        # filtro - dashboard_aggregator_service/get_volume_breakdown/
        # get_recovery_overview continuam chamando sem propagar identidade,
        # mesma lacuna ja documentada em IntelligenceServiceBase). Os
        # endpoints novos do dashboard (analytics.py, Fase 1) sempre
        # propagam identity.user_id para nunca vazar dado de outro usuario.
        #
        # Cache de instancia (ver `__init__`): a mesma tela do dashboard pede
        # este overview mais de uma vez por requisicao. `limit` so trunca o
        # ranking ja ordenado, entao o cache guarda a lista COMPLETA e fatia.
        #
        # `history_days` (ADITIVO, default `None` = historico completo, como
        # sempre foi) limita SO a leitura de historico - ver
        # `_fetch_full_history_sets`.
        cache_key = (days, user_id, history_days)
        cached = self._overview_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached[:limit])

        if datasets is not None:
            # Caminho AGREGADO (M20): os quatro insumos ja vieram prontos de
            # `intelligence/recovery_read_model.py`, em 3 consultas que
            # agregam no banco. Nada abaixo muda - so a origem dos dados.
            raw_sets = datasets["window_sets"]
            history_items = [self._complete_history_item(item) for item in datasets["history_items"]]
            contributions_by_mapping = datasets["contributions_by_mapping"]
            muscle_group_names_by_id = datasets["muscle_group_names_by_id"]
        else:
            raw_sets = self._fetch_window_sets(days, user_id=user_id)
            all_rows = self._fetch_full_history_sets(user_id=user_id, history_days=history_days)
            history_items = self._history_items_from_sets(all_rows)

            # Contribuicoes explicitas por mapping (Fase 2 da reforma de
            # volume): uma unica query bulk, reusada pelos dois loops abaixo
            # (Loop 1 - sets_count/volume_kg legados + campos ponderados novos;
            # Loop 2 - dose por sessao usada pela recuperacao muscular).
            mapping_ids = {row["mapping_id"] for row in all_rows if row.get("mapping_id")}
            contributions_by_mapping = self._fetch_contributions_by_mapping(mapping_ids)
            # Lookup id -> nome de muscle_groups, usado SOMENTE para resolver o
            # nome de contribuicoes vindas da tabela nova
            # (exercise_muscle_contributions so guarda o id, nao o nome). So
            # busca no banco quando ha de fato alguma linha explicita na tabela
            # nova (contributions_by_mapping nao vazio) - o fallback legado ja
            # resolve o nome via texto, sem precisar dessa query.
            muscle_group_names_by_id = (
                self.muscle_group_names_by_id()
                if contributions_by_mapping
                else {}
            )

        context = self._compute_session_dose_context(
            history_items, user_id=user_id, history_days=history_days
        )

        self._ensure_set_units(raw_sets)
        catalog = self.technique_catalog()

        sets_by_exercise: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for row in raw_sets:
            sets_by_exercise[row["exercise_id"]].append(row)

        # Deteccao (so para explicabilidade/auditoria): ultima serie de um
        # exercicio em RPE 10 enquanto as series anteriores estavam dentro do
        # RPE planejado (<=9). Isso NUNCA penaliza a dose nem dispara nenhuma
        # reducao automatica de carga - e so anotado no retorno.
        isolated_rpe10_set_ids: set[Any] = set()
        for sets in sets_by_exercise.values():
            if len(sets) < 2:
                continue
            last = sets[-1]
            previous = sets[:-1]
            last_rpe = last.get("rpe")
            if last_rpe is not None and float(last_rpe) >= 10.0:
                if all((s.get("rpe") is None) or (float(s["rpe"]) <= 9.0) for s in previous):
                    isolated_rpe10_set_ids.add(last["set_id"])

        groups: dict[str, dict[str, Any]] = {}

        def _ensure_group(name: str) -> dict[str, Any]:
            if name not in groups:
                groups[name] = {
                    "activity_ids": set(),
                    "sets_count": 0,
                    "reps_total": 0,
                    "volume_kg": 0.0,
                    "weight_sum": 0.0,
                    "weight_n": 0,
                    "rpe_sum": 0.0,
                    "rpe_n": 0,
                    "last_trained_at": None,
                    "last_trained_at_ts": None,
                    "recent_sets": [],
                    # dose por sessao (activity_id -> dose acumulada) para o
                    # modelo v2; separado das estatisticas legadas acima.
                    "dose_by_activity": defaultdict(float),
                    "max_participation_by_activity": defaultdict(float),
                    "performance_ratios_by_activity": defaultdict(list),
                    "rep_drop_by_activity": defaultdict(bool),
                    "rep_drop_details_by_activity": defaultdict(list),
                    "muscle_pain_by_activity": defaultdict(lambda: None),
                    "joint_pain_by_activity": defaultdict(bool),
                    "activity_sort_key": {},
                    "muscle_group_id": None,
                    # ------------------------------------------------------
                    # Campos NOVOS (Fase 2 da reforma de volume): ponderados
                    # por role/peso de exercise_muscle_contributions, em vez
                    # dos legados acima (sets_count/volume_kg/reps_total),
                    # que continuam BRUTOS (1 serie = 1 unidade, sem peso).
                    # ------------------------------------------------------
                    "direct_sets": 0.0,
                    "indirect_sets": 0.0,
                    "stabilizer_sets": 0.0,
                    "stimulus_equivalent_sets": 0.0,
                    "fatigue_equivalent_sets": 0.0,
                    "direct_sessions_activity_ids": set(),
                    "participation_sessions_activity_ids": set(),
                    "last_direct_training_at": None,
                    "last_participation_at": None,
                    "direct_rpe_sum": 0.0,
                    "direct_rpe_n": 0,
                    "indirect_rpe_sum": 0.0,
                    "indirect_rpe_n": 0,
                }
            return groups[name]

        for row in raw_sets:
            contributions = self._resolve_contributions(
                row.get("mapping_id"),
                contributions_by_mapping,
                row.get("primary_group"),
                row.get("secondary_group"),
                row.get("primary_group_id"),
                row.get("secondary_group_id"),
                row.get("secondary_participation"),
            )

            reps_raw = row.get("reps")
            rpe_raw = row.get("rpe")
            # Lote T3 (medidas de serie): reps da dose pela medida
            # (`dose_reps_for_measure`); a serie conta 1 unidade em qualquer
            # medida; `volume_kg` (reps x kg) so de serie de repeticoes.
            reps_for_dose, rpe_for_dose = e1rm.dose_inputs(
                dose_reps_for_measure(row.get("measure"), reps_raw), rpe_raw
            )
            rep_based = is_rep_based(row.get("measure"))
            set_type = row.get("set_type") or "working"
            weight_kg = float(row["weight_kg"]) if row.get("weight_kg") is not None else 0.0
            # Series equivalentes da linha: 1 para trabalho, a fatia do bloco
            # da ancora para aplicacao de tecnica (Fase 5, PR 4).
            units = float(row["set_units"])
            # M97 F2: no volume HIPERTROFICO (series diretas/indiretas e de
            # estimulo) a serie de forca vale o peso do dono
            # (`methodology_volume.hypertrophy_volume_units`); a fadiga e a
            # dose abaixo seguem com `units` cheias.
            hyp_units = _hypertrophy_volume_units(units, row.get("training_goal"))

            effort_f = MuscleRecoveryService.effort_points(rpe_for_dose)
            rep_f = MuscleRecoveryService.repetition_factor(reps_for_dose)
            type_f = realized_recovery_factor(row, catalog)

            local_date = row.get("local_date")
            trained_at_dt = self._parse_datetime(row.get("trained_at"))

            for contrib in contributions:
                role = contrib["role"]
                group_id = contrib["muscle_group_id"]
                stimulus_weight = contrib["stimulus_weight"]
                fatigue_weight = contrib["fatigue_weight"]
                # Nome: fallback legado ja traz o nome resolvido via texto
                # (muscle_group_name); contribuicoes da tabela nova so tem
                # o id, entao resolvem via lookup bulk de muscle_groups.
                group_name = contrib.get("muscle_group_name") or (
                    muscle_group_names_by_id.get(group_id, OUTROS_GROUP) if group_id is not None else OUTROS_GROUP
                )
                bucket = _ensure_group(group_name)
                if bucket["muscle_group_id"] is None and group_id is not None:
                    bucket["muscle_group_id"] = group_id
                bucket["activity_ids"].add(row["activity_id"])
                bucket["sets_count"] += 1
                bucket["reps_total"] += int(reps_raw or 0)
                if rep_based:
                    bucket["volume_kg"] += float(reps_raw or 0) * weight_kg
                if row.get("weight_kg") is not None:
                    bucket["weight_sum"] += weight_kg
                    bucket["weight_n"] += 1
                if rpe_raw is not None:
                    bucket["rpe_sum"] += float(rpe_raw)
                    bucket["rpe_n"] += 1

                if bucket["last_trained_at"] is None or (local_date and str(local_date) > str(bucket["last_trained_at"])):
                    bucket["last_trained_at"] = local_date
                if trained_at_dt and (bucket["last_trained_at_ts"] is None or trained_at_dt > bucket["last_trained_at_ts"]):
                    bucket["last_trained_at_ts"] = trained_at_dt

                # ----------------------------------------------------------
                # Campos NOVOS (Fase 2): sets_count/volume_kg acima
                # continuam BRUTOS (1 serie = 1 unidade); os campos abaixo
                # sao ponderados pelo role/peso resolvido acima.
                # ----------------------------------------------------------
                role_upper = (role or "").strip().upper()
                if role_upper == "PRIMARY":
                    bucket["direct_sets"] += hyp_units
                    bucket["direct_sessions_activity_ids"].add(row["activity_id"])
                    if bucket["last_direct_training_at"] is None or (
                        local_date and str(local_date) > str(bucket["last_direct_training_at"])
                    ):
                        bucket["last_direct_training_at"] = local_date
                    if rpe_raw is not None:
                        bucket["direct_rpe_sum"] += float(rpe_raw)
                        bucket["direct_rpe_n"] += 1
                elif role_upper == "SECONDARY":
                    bucket["indirect_sets"] += hyp_units
                    if rpe_raw is not None:
                        bucket["indirect_rpe_sum"] += float(rpe_raw)
                        bucket["indirect_rpe_n"] += 1
                elif role_upper == "STABILIZER":
                    bucket["stabilizer_sets"] += hyp_units
                    if rpe_raw is not None:
                        bucket["indirect_rpe_sum"] += float(rpe_raw)
                        bucket["indirect_rpe_n"] += 1

                bucket["stimulus_equivalent_sets"] += stimulus_weight * hyp_units
                bucket["fatigue_equivalent_sets"] += fatigue_weight * units
                bucket["participation_sessions_activity_ids"].add(row["activity_id"])
                if bucket["last_participation_at"] is None or (
                    local_date and str(local_date) > str(bucket["last_participation_at"])
                ):
                    bucket["last_participation_at"] = local_date

                participation_f = fatigue_weight

                bucket["recent_sets"].append(
                    {
                        "exercise_id": row["exercise_id"],
                        "trained_at": row.get("trained_at"),
                        "reps": reps_raw,
                        "rpe": rpe_raw,
                        "set_type": set_type,
                        "role": role,
                        "participation": participation_f,
                        "effort_points": round(effort_f, 3),
                        "repetition_factor": round(rep_f, 3),
                        "set_type_factor": round(type_f, 3),
                        "rpe10_isolated_last_set": row["set_id"] in isolated_rpe10_set_ids,
                    }
                )

        # ------------------------------------------------------------
        # Dose por sessao (modelo v2), calculada a partir do dataset
        # COMPLETO (all_rows), independente da janela `days`: a sessao mais
        # recente e a densidade de 72h precisam refletir a realidade mesmo
        # se `days` for pequeno.
        # ------------------------------------------------------------
        # Hora LOCAL do usuario (naive), a mesma convencao dos horarios de
        # treino gravados - nunca o relogio UTC do servidor.
        now = self._user_now(user_id, now_local)

        for item in history_items:
            activity_id = item["activity_id"]
            identity = self._exercise_identity(item)
            is_new = context["occurrence_rank"].get((identity, activity_id), 1) <= NEW_EXERCISE_OCCURRENCE_THRESHOLD
            current_e1rm = context["best_e1rm_by_identity_session"].get((identity, activity_id))
            reference_e1rm = context["reference_e1rm_by_identity_session"].get((identity, activity_id))
            load_spike_f = MuscleRecoveryService.load_spike_factor(current_e1rm, reference_e1rm)
            exercise_f = MuscleRecoveryService.exercise_factor(is_new)
            # Desempenho recente do exercicio nesta sessao: `None` sem par
            # comparavel (outro equipamento/convencao, primeira exposicao) -
            # e entao o exercicio nao entra na media do grupo.
            performance_ratio = context["performance_ratio_by_session"].get(
                (item.get("exercise_id"), activity_id)
            )

            excessive_rep_drop = bool(item.get("excessive_rep_drop"))

            # Sem `rpe_anticipated` (decisao do usuario, 2026-09-14, apos a D4
            # revisada): RPE alto - acima do alvo do plano ou em RPE 10, em
            # qualquer serie - nao e sinal negativo de recuperacao.

            muscle_pain = item.get("muscle_pain")
            joint_pain = bool(item.get("joint_pain"))

            contributions = self._resolve_contributions(
                item.get("mapping_id"),
                contributions_by_mapping,
                item.get("primary_group"),
                item.get("secondary_group"),
                item.get("primary_group_id"),
                item.get("secondary_group_id"),
                item.get("secondary_participation"),
            )

            for contrib in contributions:
                role = contrib["role"]
                group_id = contrib["muscle_group_id"]
                fatigue_weight = contrib["fatigue_weight"]
                role_is_primary = (role or "").strip().upper() == "PRIMARY"
                group_name = contrib.get("muscle_group_name") or (
                    muscle_group_names_by_id.get(group_id, OUTROS_GROUP) if group_id is not None else OUTROS_GROUP
                )
                bucket = groups.get(group_name)
                if bucket is None:
                    # Grupo so aparece fora da janela `days` legada: nao
                    # exibido no overview (comportamento preservado).
                    continue
                if bucket["muscle_group_id"] is None and group_id is not None:
                    bucket["muscle_group_id"] = group_id

                # fatigue_weight substitui o antigo participation_f (agora
                # resolvido via _resolve_contributions - fonte primaria:
                # exercise_muscle_contributions; fallback: papel estrutural,
                # ver docstring do metodo). Mesmo papel nos calculos abaixo.
                dose_here = self._session_dose_for_item(
                    item, fatigue_weight, load_spike_f, exercise_f, catalog
                )

                # Sem acrescimo por series em RPE 10 (antiga failure_penalty,
                # removida na D4 revisada de 2026-09-14): o esforco de cada
                # serie ja entra via effort_points(rpe).

                bucket["dose_by_activity"][activity_id] += dose_here
                bucket["max_participation_by_activity"][activity_id] = max(
                    bucket["max_participation_by_activity"][activity_id], fatigue_weight
                )
                bucket["activity_sort_key"][activity_id] = self._session_sort_key(item)

                if performance_ratio is not None and role_is_primary:
                    bucket["performance_ratios_by_activity"][activity_id].append(performance_ratio)
                if excessive_rep_drop and role_is_primary:
                    bucket["rep_drop_by_activity"][activity_id] = True
                    if item.get("rep_drop_detail"):
                        bucket["rep_drop_details_by_activity"][activity_id].append(dict(item["rep_drop_detail"]))
                if muscle_pain is not None and role_is_primary:
                    current_max = bucket["muscle_pain_by_activity"][activity_id]
                    if current_max is None or muscle_pain > current_max:
                        bucket["muscle_pain_by_activity"][activity_id] = muscle_pain
                if joint_pain and role_is_primary:
                    bucket["joint_pain_by_activity"][activity_id] = True

        today = now.date()
        results: list[dict[str, Any]] = []

        for name, bucket in groups.items():
            bucket["recent_sets"].sort(key=lambda s: str(s["trained_at"] or ""), reverse=True)

            last_trained_at = bucket["last_trained_at"]
            last_trained_at_ts = bucket["last_trained_at_ts"]

            days_since_trained = 0
            if last_trained_at:
                try:
                    days_since_trained = (today - date.fromisoformat(str(last_trained_at)[:10])).days
                except ValueError:
                    days_since_trained = 0

            hours_since_trained = None
            if last_trained_at_ts:
                hours_since_trained = max((now - last_trained_at_ts).total_seconds() / 3600.0, 0.0)

            sets_last_day = 0
            last_set_rpe = None
            if bucket["recent_sets"]:
                last_day_key = str(bucket["recent_sets"][0]["trained_at"] or "")[:10]
                sets_last_day = sum(
                    1 for s in bucket["recent_sets"] if str(s["trained_at"] or "")[:10] == last_day_key
                )
                candidate_rpe = bucket["recent_sets"][0].get("rpe")
                last_set_rpe = float(candidate_rpe) if candidate_rpe is not None else None

            avg_rpe = round(bucket["rpe_sum"] / bucket["rpe_n"], 2) if bucket["rpe_n"] else None
            avg_weight_kg = round(bucket["weight_sum"] / bucket["weight_n"], 2) if bucket["weight_n"] else None

            recent_sets_for_display = bucket["recent_sets"][:5]
            has_isolated_rpe10_note = any(s.get("rpe10_isolated_last_set") for s in recent_sets_for_display)

            # Sessao mais recente (para session_dose/elapsed) e densidade de
            # 72h (soma de doses de sessoes recentes) - dois numeros
            # diferentes, calculados a partir do dataset completo.
            activity_sort_keys = bucket["activity_sort_key"]
            session_dose = 0.0
            session_participation = 1.0
            dose_last_72h = 0.0
            performance_ratio_value: float | None = None
            excessive_rep_drop_value = False
            rep_drop_details_value: list[dict[str, Any]] = []
            muscle_pain_value = None
            joint_pain_value = False
            last_session_at_value: str | None = None

            if activity_sort_keys:
                most_recent_activity_id = max(activity_sort_keys, key=lambda aid: activity_sort_keys[aid])
                most_recent_sort_key = activity_sort_keys[most_recent_activity_id]
                last_session_at_value = most_recent_sort_key.isoformat() if most_recent_sort_key else None
                session_dose = bucket["dose_by_activity"].get(most_recent_activity_id, 0.0)
                session_participation = bucket["max_participation_by_activity"].get(
                    most_recent_activity_id, 1.0
                )

                # Media das razoes dos exercicios PRIMARY da sessao que
                # tinham par comparavel (os sem par nem entram na lista).
                ratios = bucket["performance_ratios_by_activity"].get(most_recent_activity_id) or []
                if ratios:
                    performance_ratio_value = sum(ratios) / len(ratios)
                excessive_rep_drop_value = bool(bucket["rep_drop_by_activity"].get(most_recent_activity_id))
                rep_drop_details_value = list(bucket["rep_drop_details_by_activity"].get(most_recent_activity_id) or [])
                muscle_pain_value = bucket["muscle_pain_by_activity"].get(most_recent_activity_id)
                joint_pain_value = bool(bucket["joint_pain_by_activity"].get(most_recent_activity_id))

                density_cutoff = now - timedelta(hours=DENSITY_WINDOW_HOURS)
                for activity_id, sort_key in activity_sort_keys.items():
                    if sort_key >= density_cutoff:
                        dose_last_72h += bucket["dose_by_activity"].get(activity_id, 0.0)

            # Historico de dose por sessao (activity_id), ordenado
            # cronologicamente (mais antigo primeiro). Campo ADITIVO: nao
            # remove/renomeia nada do que ja existia; usado por
            # MuscleRecoveryService.get_consecutive_day_recovery_checks para
            # avaliar pares de dias consecutivos com a mesma logica de
            # recuperacao (dose -> horas) ja calibrada, em vez do booleano
            # simples "ativado no dia" usado anteriormente na tela /treinos.
            activity_dose_history = [
                {
                    "activity_id": activity_id,
                    "trained_at": sort_key.isoformat() if sort_key else None,
                    "local_date": sort_key.date().isoformat() if sort_key else None,
                    "dose": round(bucket["dose_by_activity"].get(activity_id, 0.0), 3),
                    "participation": round(
                        bucket["max_participation_by_activity"].get(activity_id, 1.0), 3
                    ),
                }
                for activity_id, sort_key in sorted(activity_sort_keys.items(), key=lambda kv: kv[1])
            ]

            factors: dict[str, Any] = {
                "session_dose": round(session_dose, 3),
                "dose_last_72h": round(dose_last_72h, 3),
                "avg_rpe_used": avg_rpe,
                "recent_sets": recent_sets_for_display,
            }
            if has_isolated_rpe10_note:
                factors["rpe10_isolated_note"] = (
                    "RPE 10 na ultima serie de pelo menos um exercicio deste grupo, "
                    "isoladamente (series anteriores <=9): isso NAO e tratado como "
                    "sinal automatico de excesso nem gera penalidade na dose."
                )

            results.append(
                {
                    "muscle_group": name,
                    "muscle_group_id": bucket.get("muscle_group_id"),
                    "sessions": len(bucket["activity_ids"]),
                    "sets_count": bucket["sets_count"],
                    "reps_total": bucket["reps_total"],
                    "volume_kg": round(bucket["volume_kg"], 2),
                    "avg_weight_kg": avg_weight_kg,
                    "avg_rpe": avg_rpe,
                    "last_trained_at": last_trained_at,
                    "last_trained_at_ts": last_trained_at_ts.isoformat() if last_trained_at_ts else None,
                    "hours_since_trained": round(hours_since_trained, 2) if hours_since_trained is not None else None,
                    "days_since_trained": days_since_trained,
                    "sets_last_day": sets_last_day,
                    "last_set_rpe": last_set_rpe,
                    "session_dose": session_dose,
                    "session_participation": round(session_participation, 3),
                    "dose_last_72h": dose_last_72h,
                    "performance_ratio": performance_ratio_value,
                    # DEPRECADO (2026-09-14): sempre False. Mantido por uma
                    # janela so por compatibilidade de contrato (frontend
                    # `workouts-analysis-types.ts` e consumidores externos);
                    # nao alimenta prontidao, blocking_signals nem alertas.
                    "rpe_anticipated": False,
                    "excessive_rep_drop": excessive_rep_drop_value,
                    # Lote 1/P2: exercicio(s) e reps 1a -> ultima da queda da
                    # sessao mais recente (insumo do blocking_signal).
                    "rep_drop_details": rep_drop_details_value,
                    "muscle_pain": muscle_pain_value,
                    "joint_pain": joint_pain_value,
                    # PR-09: instante da sessao mais recente do grupo (a
                    # mesma que origina dor/sinais) - source_session_at dos
                    # blocking_signals.
                    "last_session_at": last_session_at_value,
                    "factors": factors,
                    "activity_dose_history": activity_dose_history,
                    # ----------------------------------------------------
                    # Campos NOVOS (Fase 2 da reforma de volume): sets_count/
                    # volume_kg/reps_total acima continuam BRUTOS (sem peso);
                    # os campos abaixo sao ponderados por role/peso de
                    # exercise_muscle_contributions (ver _resolve_contributions).
                    # ----------------------------------------------------
                    "direct_sets": series_number(bucket["direct_sets"]),
                    "indirect_sets": series_number(bucket["indirect_sets"]),
                    "stabilizer_sets": series_number(bucket["stabilizer_sets"]),
                    "stimulus_equivalent_sets": round(bucket["stimulus_equivalent_sets"], 2),
                    "fatigue_equivalent_sets": round(bucket["fatigue_equivalent_sets"], 2),
                    "direct_sessions": len(bucket["direct_sessions_activity_ids"]),
                    "participation_sessions": len(bucket["participation_sessions_activity_ids"]),
                    "last_direct_training_at": bucket["last_direct_training_at"],
                    "last_participation_at": bucket["last_participation_at"],
                    "direct_rpe_avg": round(bucket["direct_rpe_sum"] / bucket["direct_rpe_n"], 2)
                    if bucket["direct_rpe_n"]
                    else None,
                    "indirect_rpe_avg": round(bucket["indirect_rpe_sum"] / bucket["indirect_rpe_n"], 2)
                    if bucket["indirect_rpe_n"]
                    else None,
                }
            )

        results.sort(key=lambda r: (r["volume_kg"], r["sets_count"]), reverse=True)
        self._overview_cache[cache_key] = results
        return copy.deepcopy(results[:limit])

    def get_equivalent_sets_by_activity(
        self, limit: int = 400, user_id: int | None = None, training_goal: str | None = None
    ) -> list[dict[str, Any]]:
        """Series EQUIVALENTES ponderadas (mesma formula de
        `stimulus_weight` usada por `abc_volume_reinforcement.py`: PRIMARY =
        peso 1.0, SECONDARY/STABILIZER = stimulus_weight real de
        `exercise_muscle_contributions`, com fallback legado), mas agregadas
        POR ATIVIDADE (`activity_id`) em vez de por periodo inteiro (ao
        contrario de `get_muscle_group_overview`, cujo
        `stimulus_equivalent_sets` e um total acumulado sem breakdown por
        sessao). Usada pela tabela "Series equivalentes executadas - ultimos
        4 treinos" de `/volume-grupamentos`.

        Reaproveita a MESMA fonte de exercicios de `services.list_exercise_logs`
        (activity_id + mapping_id + sets_count por exercicio, sem precisar
        de reps/weight/rpe individuais - o calculo e so `sets_count *
        stimulus_weight`) e a mesma resolucao de contribuicoes usada pelo
        reforco de volume do ABC
        (`intelligence.muscle_contribution_resolver._resolve_muscle_contributions_for_mappings`),
        sem duplicar a formula de peso.

        Retorna um item por combinacao (activity_id, muscle_group) com
        contribuicao > 0: {"activity_id", "local_date", "muscle_group",
        "equivalent_sets"}."""
        from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

        where = []
        params: list[Any] = []
        if user_id is not None:
            where.append("a.user_id = %s")
            params.append(user_id)
        if training_goal is not None:
            # M97 F4: filtro opcional de objetivo, pelo predicado do dono.
            where.append(sql_goal_is("e"))
            params.append(training_goal)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        exercise_rows = self.fetch_all(
            f"""
            SELECT
                e.id AS exercise_id,
                a.id AS activity_id,
                a.local_date AS local_date,
                CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
                e.training_goal AS training_goal,
                COUNT(es.id) AS sets_count,
                {sql_application_rows()} AS technique_rows
            FROM exercises e
            JOIN activities a ON a.id = e.activity_id
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            {where_clause}
            GROUP BY e.id, a.id, a.local_date, e.mapping_id, elm.canonical_exercise_id, e.training_goal
            ORDER BY a.local_date DESC, a.id DESC
            LIMIT %s
            """,
            (*params, limit),
        )
        # Fase 5 (PR 4): aplicacao de tecnica vale a sua fatia do bloco, nao
        # uma serie cheia (leitura extra so se houver aplicacao).
        apply_application_units(exercise_rows, self.fetch_all, self.technique_catalog())

        mapping_ids = {row["mapping_id"] for row in exercise_rows if row.get("mapping_id")}
        contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(mapping_ids)

        # Chave de agregacao (activity_id, muscle_group_name) - mesmo padrao
        # de acumulo de `_accumulate_exercise_contribution`
        # (sets_count * stimulus_weight), mas por atividade em vez de global.
        totals: dict[tuple[int, str], float] = defaultdict(float)
        local_date_by_activity: dict[int, Any] = {}

        for row in exercise_rows:
            activity_id = row["activity_id"]
            local_date_by_activity[activity_id] = row.get("local_date")

            mapping_id = row.get("mapping_id")
            sets_count = row.get("sets_count") or 0
            if not mapping_id or not sets_count:
                continue
            # M97 F4: serie de forca no volume hipertrofico pelo peso do dono.
            sets_count = _hypertrophy_volume_units(float(sets_count), row.get("training_goal"))
            for contribution in contributions_by_mapping_id.get(mapping_id, []):
                group_name = contribution.get("muscle_group_name")
                if not group_name:
                    continue
                totals[(activity_id, group_name)] += sets_count * float(contribution.get("stimulus_weight") or 0.0)

        results: list[dict[str, Any]] = []
        for (activity_id, muscle_group), equivalent_sets in totals.items():
            if equivalent_sets <= 0:
                continue
            results.append(
                {
                    "activity_id": activity_id,
                    "local_date": local_date_by_activity.get(activity_id),
                    "muscle_group": muscle_group,
                    "equivalent_sets": round(equivalent_sets, 2),
                }
            )
        return results

    def get_muscle_activation_by_activity(
        self, activity_id: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """"Ativacoes" musculares de UMA atividade especifica, separadas por
        role (PRIMARY, SECONDARY e STABILIZER), para a tabela "Ativacao
        muscular" de `/registro-exercicios/[activityId]`. Mesma formula
        universal do projeto (`sets_count * stimulus_weight`, resolvida via
        `_resolve_muscle_contributions_for_mappings` - identica fonte de
        `get_equivalent_sets_by_activity` acima), mas: (a) filtrada por um
        unico `activity_id` em vez de agregada globalmente, (b) mantem
        primary/secondary/stabilizer em colunas separadas em vez de somar
        tudo, (c) tambem calcula o peso medio (`weight_kg`) das series
        realmente logadas em `exercise_sets`, usado so para exibicao (a
        media pondera apenas series com peso registrado -
        `sets_with_weight` -, nunca zera por causa de series sem peso).

        Retorna um item por grupo muscular com contribuicao > 0 em pelo
        menos um dos tres roles, ordenado por `total_equivalent`
        decrescente (grupos mais ativados primeiro)."""
        from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

        where = ["a.id = %s", "e.mapping_id IS NOT NULL"]
        params: list[Any] = [activity_id]
        if user_id is not None:
            where.append("a.user_id = %s")
            params.append(user_id)
        where_clause = f"WHERE {' AND '.join(where)}"

        exercise_rows = self.fetch_all(
            f"""
            SELECT
                e.id AS exercise_id,
                CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
                e.sets_count AS sets_count,
                e.training_goal AS training_goal,
                (
                  SELECT {sql_application_rows("tq.set_type")}
                  FROM exercise_sets tq
                  WHERE tq.exercise_id = e.id AND {sql_set_confirmed('tq.reps_pending')}
                ) AS technique_rows
            FROM exercises e
            JOIN activities a ON a.id = e.activity_id
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            {where_clause}
            """,
            tuple(params),
        )
        # `exercises.sets_count` conta cada aplicacao de tecnica como uma
        # serie; a ativacao usa trabalho + fatia do bloco (Fase 5, PR 4).
        apply_application_units(exercise_rows, self.fetch_all, self.technique_catalog())

        exercise_ids = [row["exercise_id"] for row in exercise_rows]
        avg_weight_by_exercise: dict[int, dict[str, Any]] = {}
        if exercise_ids:
            placeholders = ",".join(["%s"] * len(exercise_ids))
            weight_rows = self.fetch_all(
                f"""
                SELECT exercise_id, AVG(weight_kg) AS avg_weight_kg, COUNT(*) AS sets_logged
                FROM exercise_sets
                WHERE exercise_id IN ({placeholders}) AND weight_kg IS NOT NULL AND {_NOT_PREPARATORY}
                  AND {sql_set_confirmed('reps_pending')}
                GROUP BY exercise_id
                """,
                tuple(exercise_ids),
            )
            avg_weight_by_exercise = {row["exercise_id"]: row for row in weight_rows}

        mapping_ids = {row["mapping_id"] for row in exercise_rows if row.get("mapping_id")}
        contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(mapping_ids)

        activation_rows = [
            {
                "mapping_id": row.get("mapping_id"),
                "sets_count": row.get("sets_count") or 0,
                # M97 F4: series/ativacao (estimulo) com a serie de forca
                # ponderada pelo dono do volume hipertrofico; a media de carga
                # segue pelas series cheias.
                "hypertrophy_units": _hypertrophy_volume_units(
                    float(row.get("sets_count") or 0), row.get("training_goal")
                ),
                "avg_weight_kg": (
                    float(avg_weight_by_exercise[row["exercise_id"]]["avg_weight_kg"])
                    if avg_weight_by_exercise.get(row["exercise_id"])
                    and avg_weight_by_exercise[row["exercise_id"]].get("avg_weight_kg") is not None
                    else None
                ),
            }
            for row in exercise_rows
        ]

        return self._build_muscle_activation_rows(activation_rows, contributions_by_mapping_id)

    def get_training_type_weekly_targets_for_activity(
        self, activity_id: int, user_id: int | None = None
    ) -> list[dict[str, Any]] | None:
        """Resolve o alvo por grupo muscular para a tabela "Ativação
        muscular" de `/registro-exercicios/[activityId]`, no MESMO formato de
        `template_slot_targets` (`[{muscle_group_id, muscle_group_name,
        sets_count, max_weekly_sets}]`, ver `WorkoutPlansClient.tsx`).

        Prioridade de resolução (read-time, sem depender de
        `activities.training_type_id` estar preenchido — o que NÃO acontece
        quando a activity vem da execução de uma ficha gerada por Modelo de
        Treino):

        1. Se a activity tem `workout_plan_id` e o plano vinculado tem
           `raw_payload.template_slot_targets` (motor v2,
           `generation/mode_a_legacy.py`), reaproveita ESSE mesmo
           alvo — a fração de séries daquele SLOT específico (ex.: Bíceps
           `sets_count=5.6`), não a meta semanal completa. Garante que esta
           tela e `/planos-treino` comparem exatamente a mesma coisa para o
           mesmo treino, já que ambas leem a mesma origem
           (`template_slot_targets`). `effective_max_sets` é mapeado para
           `max_weekly_sets` (nome que o frontend desta tela espera) —
           aplica o mesmo fallback de compatibilidade retroativa de
           `_apply_effective_max_sets_fallback` (ver `services.py`) para
           fichas salvas antes desse campo existir.
        2. Sem `workout_plan_id` (treino avulso), ou com `workout_plan_id`
           mas sem `template_slot_targets` no plano (ex.: ABC legado v1,
           Full Body sem modelo): fallback para `activities.training_type_id`
           quando presente, resolvendo a meta SEMANAL completa do training
           type via `training_types._get_training_type` (comportamento
           antigo, preservado para não regredir nenhum caso hoje coberto).
        3. Sem nenhuma das duas fontes: retorna `None` (comportamento
           genérico de limiares fixos em `MuscleActivationTable.tsx`)."""
        where = ["a.id = %s"]
        params: list[Any] = [activity_id]
        if user_id is not None:
            where.append("a.user_id = %s")
            params.append(user_id)
        where_clause = f"WHERE {' AND '.join(where)}"

        row = self.fetch_one(
            f"SELECT workout_plan_id, training_type_id FROM activities a {where_clause}",
            tuple(params),
        )
        workout_plan_id = row.get("workout_plan_id") if row else None
        training_type_id = row.get("training_type_id") if row else None

        if workout_plan_id:
            plan_targets = self._resolve_targets_from_plan_template_slots(workout_plan_id)
            if plan_targets:
                return plan_targets

        if not training_type_id:
            return None

        from ..training_types import _get_training_type

        training_type = _get_training_type(training_type_id)
        if not training_type:
            return None

        return [
            {
                "muscle_group_id": target["muscle_group_id"],
                "muscle_group_name": target["muscle_group_name"],
                "sets_count": float(target["target_weekly_sets"]),
                "max_weekly_sets": (
                    float(target["max_weekly_sets"]) if target.get("max_weekly_sets") is not None else None
                ),
                # M76 E4 (aditivos): o status da tabela sai do backend.
                "min_weekly_sets": (
                    float(target["min_weekly_sets"]) if target.get("min_weekly_sets") is not None else None
                ),
                "target_source": ACTIVATION_TARGET_TRAINING_TYPE,
            }
            for target in training_type.get("targets", [])
        ]

    def _resolve_targets_from_plan_template_slots(self, workout_plan_id: int) -> list[dict[str, Any]] | None:
        """Le `workout_plans.raw_payload.template_slot_targets` do plano
        vinculado e converte para o formato esperado por
        `get_training_type_weekly_targets_for_activity`
        (`effective_max_sets` -> `max_weekly_sets`). Retorna `None` quando o
        plano nao existe ou nao tem `template_slot_targets` salvo (planos
        legados/ABC v1/Full Body sem modelo), para o chamador cair no
        fallback de `training_type_id`."""
        from ..services import _apply_effective_max_sets_fallback, from_json_dict

        plan_row = self.fetch_one(
            "SELECT raw_payload FROM workout_plans WHERE id = %s",
            (workout_plan_id,),
        )
        if not plan_row:
            return None

        raw_payload = from_json_dict(plan_row.get("raw_payload"))
        if not isinstance(raw_payload, dict):
            return None

        try:
            _apply_effective_max_sets_fallback(raw_payload)
        except Exception:
            # Mesma filosofia defensiva de get_workout_plan (services.py):
            # nunca deixar um raw_payload malformado quebrar esta leitura.
            logger.warning(
                "_apply_effective_max_sets_fallback falhou (plano %s); segue sem o fallback",
                workout_plan_id,
                exc_info=True,
            )

        template_slot_targets = raw_payload.get("template_slot_targets")
        if not isinstance(template_slot_targets, list) or not template_slot_targets:
            return None

        targets: list[dict[str, Any]] = []
        for target in template_slot_targets:
            if not isinstance(target, dict):
                continue
            if target.get("muscle_group_id") is None or target.get("sets_count") is None:
                continue
            effective_max_sets = target.get("effective_max_sets")
            targets.append(
                {
                    "muscle_group_id": target["muscle_group_id"],
                    "muscle_group_name": target.get("muscle_group_name"),
                    "sets_count": float(target["sets_count"]),
                    "max_weekly_sets": float(effective_max_sets) if effective_max_sets is not None else None,
                    "target_source": ACTIVATION_TARGET_TEMPLATE_SLOT,
                }
            )

        return targets or None

    def get_muscle_activation_for_exercises(
        self, exercises: list[dict[str, Any]], catalog: Any = None
    ) -> list[dict[str, Any]]:
        """Mesmo calculo de `get_muscle_activation_by_activity`, mas para uma
        lista de exercicios fornecida diretamente (ex.: rascunho em memoria
        de `/planos-treino`, ainda sem `activity_id`/persistencia no banco).

        Cada item de `exercises` deve ter `mapping_id` e `sets_count`; itens
        sem `mapping_id` ou sem `sets_count` valido sao ignorados
        silenciosamente (mesmo comportamento de dados incompletos que
        `get_muscle_activation_by_activity` ja tem hoje). Como nao ha series
        logadas num rascunho, `avg_weight_kg` e sempre `None` - os campos
        `*_avg_weight_kg` do resultado tambem virao sempre `None`.

        Fase 5 (PR 5): item com `advanced_technique` (+
        `advanced_technique_applications`) soma o equivalente PLANEJADO da
        tecnica as suas series (`technique_volume.planned_item_sets`), que
        entra x `stimulus_weight` como qualquer serie. `catalog` e o catalogo
        de tecnicas da requisicao; sem ele, um preguicoso e criado (item sem
        tecnica nao le nada)."""
        from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

        if catalog is None:
            catalog = new_catalog()
        activation_rows: list[dict[str, Any]] = []
        mapping_ids: set[int] = set()
        for item in exercises:
            mapping_id = item.get("mapping_id")
            sets_count = planned_item_sets(item, catalog)
            if not mapping_id or not sets_count:
                continue
            activation_rows.append(
                {
                    "mapping_id": mapping_id,
                    "sets_count": sets_count,
                    "avg_weight_kg": None,
                }
            )
            mapping_ids.add(mapping_id)

        contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(mapping_ids)
        return self._build_muscle_activation_rows(activation_rows, contributions_by_mapping_id)

    def get_muscle_activation_breakdown_by_exercise(
        self, exercises: list[dict[str, Any]], catalog: Any = None
    ) -> list[dict[str, Any]]:
        """Mesmo dado de `get_muscle_activation_for_exercises`, mas
        preservando a granularidade POR EXERCICIO em vez de agregar em
        buckets por `muscle_group_name` do plano inteiro. Devolve um item de
        saida por item de entrada, na MESMA ORDEM/TAMANHO de `exercises`
        (inclusive itens com `mapping_id`/`sets_count` invalidos, que voltam
        com ativacao zerada) - isso permite ao frontend parear por indice de
        volta a linha original do rascunho em edicao em `/planos-treino`, sem
        depender de `mapping_id` ser unico na ficha (podem existir dois
        exercicios diferentes na ficha resolvendo pro mesmo `mapping_id`, ou
        nenhum)."""
        from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

        # M76 E3: mesmo payload de `get_muscle_activation_for_exercises` - a
        # tecnica do item soma o equivalente PLANEJADO pelo dono da Fase 5.
        if catalog is None:
            catalog = new_catalog()
        item_sets = [planned_item_sets(item, catalog) for item in exercises]
        mapping_ids: set[int] = set()
        for item, sets_count in zip(exercises, item_sets):
            mapping_id = item.get("mapping_id")
            if mapping_id and sets_count:
                mapping_ids.add(mapping_id)

        contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(mapping_ids)

        results: list[dict[str, Any]] = []
        for item, sets_count in zip(exercises, item_sets):
            mapping_id = item.get("mapping_id")
            if not mapping_id or not sets_count:
                results.append(
                    {
                        "mapping_id": mapping_id,
                        "primary_activation": 0.0,
                        "secondary": [],
                        "stabilizer": [],
                    }
                )
                continue

            primary_activation = 0.0
            secondary_totals: dict[str, float] = defaultdict(float)
            stabilizer_totals: dict[str, float] = defaultdict(float)

            for contribution in contributions_by_mapping_id.get(mapping_id, []):
                group_name = contribution.get("muscle_group_name")
                if not group_name:
                    continue
                role = (contribution.get("role") or "").upper()
                stimulus_weight = float(contribution.get("stimulus_weight") or 0.0)
                activation = sets_count * stimulus_weight
                if role == "PRIMARY":
                    primary_activation += activation
                elif role == "STABILIZER":
                    stabilizer_totals[group_name] += activation
                else:
                    secondary_totals[group_name] += activation

            results.append(
                {
                    "mapping_id": mapping_id,
                    "primary_activation": round(primary_activation, 2),
                    "secondary": [
                        {"muscle_group_name": name, "activation": round(value, 2)}
                        for name, value in sorted(
                            secondary_totals.items(), key=lambda kv: kv[1], reverse=True
                        )
                    ],
                    "stabilizer": [
                        {"muscle_group_name": name, "activation": round(value, 2)}
                        for name, value in sorted(
                            stabilizer_totals.items(), key=lambda kv: kv[1], reverse=True
                        )
                    ],
                }
            )

        return results

    def _build_muscle_activation_rows(
        self,
        exercise_rows: list[dict[str, Any]],
        contributions_by_mapping_id: dict[int, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Calculo puro (sem acesso a banco) usado tanto por
        `get_muscle_activation_by_activity` quanto por
        `get_muscle_activation_for_exercises`. `exercise_rows` e uma lista de
        `{"mapping_id": int, "sets_count": int, "avg_weight_kg": float | None}`."""

        def _new_bucket() -> dict[str, float]:
            return {
                "primary_series": 0.0,
                "primary_weighted": 0.0,
                "primary_weight_sum": 0.0,
                "primary_sets_with_weight": 0.0,
                "secondary_series": 0.0,
                "secondary_weighted": 0.0,
                "secondary_weight_sum": 0.0,
                "secondary_sets_with_weight": 0.0,
                "stabilizer_series": 0.0,
                "stabilizer_weighted": 0.0,
                "stabilizer_weight_sum": 0.0,
                "stabilizer_sets_with_weight": 0.0,
            }

        buckets: dict[str, dict[str, float]] = defaultdict(_new_bucket)

        for row in exercise_rows:
            mapping_id = row.get("mapping_id")
            sets_count = row.get("sets_count") or 0
            if not mapping_id or not sets_count:
                continue

            avg_weight_kg = row.get("avg_weight_kg")

            for contribution in contributions_by_mapping_id.get(mapping_id, []):
                group_name = contribution.get("muscle_group_name")
                if not group_name:
                    continue
                role = (contribution.get("role") or "").upper()
                stimulus_weight = float(contribution.get("stimulus_weight") or 0.0)
                prefix = (
                    "primary"
                    if role == "PRIMARY"
                    else "stabilizer" if role == "STABILIZER" else "secondary"
                )

                bucket = buckets[group_name]
                sets_count = float(sets_count)
                # M97 F4: `hypertrophy_units` (opcional) = series no volume
                # hipertrofico (forca pelo peso do dono); ausente = as series.
                units = float(row.get("hypertrophy_units", sets_count))
                bucket[f"{prefix}_series"] += units
                bucket[f"{prefix}_weighted"] += units * stimulus_weight
                if avg_weight_kg is not None:
                    bucket[f"{prefix}_weight_sum"] += avg_weight_kg * sets_count
                    bucket[f"{prefix}_sets_with_weight"] += sets_count

        results: list[dict[str, Any]] = []
        for group_name, bucket in buckets.items():
            primary_equivalent = bucket["primary_weighted"]
            secondary_equivalent = bucket["secondary_weighted"]
            stabilizer_equivalent = bucket["stabilizer_weighted"]
            results.append(
                {
                    "muscle_group": group_name,
                    "primary_series": series_number(bucket["primary_series"]),
                    "primary_avg_weight_kg": (
                        round(bucket["primary_weight_sum"] / bucket["primary_sets_with_weight"], 2)
                        if bucket["primary_sets_with_weight"]
                        else None
                    ),
                    "primary_equivalent": round(primary_equivalent, 2),
                    "secondary_series": series_number(bucket["secondary_series"]),
                    "secondary_avg_weight_kg": (
                        round(bucket["secondary_weight_sum"] / bucket["secondary_sets_with_weight"], 2)
                        if bucket["secondary_sets_with_weight"]
                        else None
                    ),
                    "secondary_equivalent": round(secondary_equivalent, 2),
                    "stabilizer_series": series_number(bucket["stabilizer_series"]),
                    "stabilizer_avg_weight_kg": (
                        round(bucket["stabilizer_weight_sum"] / bucket["stabilizer_sets_with_weight"], 2)
                        if bucket["stabilizer_sets_with_weight"]
                        else None
                    ),
                    "stabilizer_equivalent": round(stabilizer_equivalent, 2),
                    "total_equivalent": round(
                        primary_equivalent + secondary_equivalent + stabilizer_equivalent, 2
                    ),
                }
            )

        results.sort(key=lambda r: r["total_equivalent"], reverse=True)
        return results

    def get_recent_exercise_volume(
        self,
        user_id: int | None = None,
        limit: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        training_goal: str | None = None,
    ) -> list[dict[str, Any]]:
        """Volume BRUTO recente por (activity_id, muscle_group, equipment_name),
        usado pelas 4 tabelas client-side de `/volume-grupamentos`
        (comparativo dos ultimos 4 treinos, comparativo das ultimas 4
        semanas, comparativo por equipamento e mapa de calor) - substitui o
        calculo que hoje e feito em JS a partir de `/exercises/logs`,
        centralizando a agregacao no backend (motor unico de volume).

        Reaproveita o MESMO padrao de query de `get_equivalent_sets_by_activity`
        (junta exercises+activities+exercise_sets, agregado por exercicio) e a
        MESMA convencao de `muscle_group` de `get_volume_trend`
        (COALESCE(primary_group, secondary_group, 'Outros')) - corrigindo o
        bug do calculo antigo em JS, que usava so `exercise.muscle_primary`
        (exercicio com `muscle_primary` nulo e `muscle_secondary` preenchido
        caia incorretamente em "Outros").

        Cada linha e agregada primeiro POR EXERCICIO (sets_count/volume_kg via
        SUM(reps*weight_kg) - mesma formula legada de
        `get_muscle_group_overview`/`get_volume_trend` -, avg_rpe e
        `last_weight_kg` - peso da serie de maior `set_number` daquele
        exercicio, via subquery correlacionada) e depois somada por
        (activity_id, muscle_group, equipment_name): normalmente 1 exercicio
        por combinacao, mas quando mais de um exercicio da mesma atividade cai
        no mesmo grupo/equipamento, `sets_count`/`volume_kg` sao somados,
        `avg_rpe` e a media ponderada por `sets_count` e `last_weight_kg` fica
        com o exercicio de maior `id` (o executado por ultimo).

        `limit` (modo "ultimas N linhas") e `date_from`/`date_to` (modo
        "intervalo de data", usado pelo mapa de calor) sao MUTUAMENTE
        EXCLUSIVOS: quando `date_from`/`date_to` sao informados, `limit` e
        ignorado e um teto de seguranca de 5000 exercicios e aplicado no
        lugar, para nunca rodar uma consulta sem limite nenhum. O filtro de
        `limit` (LIMIT no SQL, ordenado por `local_date`/`activity_id` desc)
        e aplicado no nivel de EXERCICIO, antes da agregacao final - mesmo
        padrao de `get_equivalent_sets_by_activity`/`list_exercise_logs`
        (default historico do frontend: 400)."""
        where = []
        params: list[Any] = []
        if user_id is not None:
            where.append("a.user_id = %s")
            params.append(user_id)

        use_date_range = date_from is not None or date_to is not None
        if date_from is not None:
            where.append("a.local_date >= %s")
            params.append(date_from)
        if date_to is not None:
            where.append("a.local_date <= %s")
            params.append(date_to)
        if training_goal is not None:
            # M97 F4: filtro opcional de objetivo, pelo predicado do dono.
            where.append(sql_goal_is("e"))
            params.append(training_goal)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        effective_limit = 5000 if use_date_range else int(limit or 400)

        exercise_rows = self.fetch_all(
            f"""
            SELECT
                e.id AS exercise_id,
                a.id AS activity_id,
                a.local_date AS local_date,
                COALESCE(NULLIF(pg.name, ''), NULLIF(sg.name, ''), 'Outros') AS muscle_group,
                eq.name AS equipment_name,
                COUNT(es.id) AS sets_count,
                COALESCE(SUM({sql_if_rep_based('COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0)')}), 0) AS volume_kg,
                ROUND(AVG(es.rpe), 2) AS avg_rpe,
                (
                    SELECT es2.weight_kg
                    FROM exercise_sets es2
                    WHERE es2.exercise_id = e.id AND {_ES2_NOT_PREPARATORY}
                      AND {sql_set_confirmed('es2.reps_pending')}
                    ORDER BY es2.set_number DESC
                    LIMIT 1
                ) AS last_weight_kg
            FROM exercises e
            JOIN activities a ON a.id = e.activity_id
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN muscle_groups pg ON pg.id = (
              SELECT emc_pri.muscle_group_id
              FROM exercise_muscle_contributions emc_pri
              WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
              ORDER BY emc_pri.id ASC LIMIT 1
            )
            LEFT JOIN muscle_groups sg ON sg.id = (
              SELECT emc_sec.muscle_group_id
              FROM exercise_muscle_contributions emc_sec
              WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
              ORDER BY emc_sec.id ASC LIMIT 1
            )
            LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
            LEFT JOIN equipment eq ON eq.id = ge.equipment_id
            {where_clause}
            GROUP BY e.id, a.id, a.local_date, muscle_group, eq.name
            ORDER BY a.local_date DESC, a.id DESC
            LIMIT %s
            """,
            (*params, effective_limit),
        )

        # Agrega por (activity_id, muscle_group, equipment_name): na maioria
        # dos casos ha so 1 exercicio por combinacao (agregacao vira
        # passthrough); quando ha mais de um, soma sets_count/volume_kg,
        # calcula avg_rpe ponderado por sets_count e mantem last_weight_kg do
        # exercicio de maior id (executado por ultimo).
        totals: dict[tuple[int, str, str | None], dict[str, Any]] = {}
        for row in exercise_rows:
            key = (row["activity_id"], row["muscle_group"], row.get("equipment_name"))
            sets_count = int(row.get("sets_count") or 0)
            volume_kg = float(row.get("volume_kg") or 0.0)
            avg_rpe = row.get("avg_rpe")
            exercise_id = row["exercise_id"]

            bucket = totals.get(key)
            if bucket is None:
                totals[key] = {
                    "activity_id": row["activity_id"],
                    "local_date": row.get("local_date"),
                    "muscle_group": row["muscle_group"],
                    "equipment_name": row.get("equipment_name"),
                    "sets_count": sets_count,
                    "volume_kg": volume_kg,
                    "rpe_weighted_sum": float(avg_rpe) * sets_count if avg_rpe is not None else 0.0,
                    "rpe_weighted_n": sets_count if avg_rpe is not None else 0,
                    "last_weight_kg": row.get("last_weight_kg"),
                    "_last_exercise_id": exercise_id,
                }
                continue

            bucket["sets_count"] += sets_count
            bucket["volume_kg"] += volume_kg
            if avg_rpe is not None:
                bucket["rpe_weighted_sum"] += float(avg_rpe) * sets_count
                bucket["rpe_weighted_n"] += sets_count
            if exercise_id > bucket["_last_exercise_id"]:
                bucket["_last_exercise_id"] = exercise_id
                bucket["last_weight_kg"] = row.get("last_weight_kg")

        results: list[dict[str, Any]] = []
        for bucket in totals.values():
            avg_rpe = (
                round(bucket["rpe_weighted_sum"] / bucket["rpe_weighted_n"], 2)
                if bucket["rpe_weighted_n"]
                else None
            )
            last_weight_kg = bucket["last_weight_kg"]
            results.append(
                {
                    "activity_id": bucket["activity_id"],
                    "local_date": bucket["local_date"],
                    "muscle_group": bucket["muscle_group"],
                    "equipment_name": bucket["equipment_name"],
                    "sets_count": bucket["sets_count"],
                    "volume_kg": round(bucket["volume_kg"], 2),
                    "avg_rpe": avg_rpe,
                    "last_weight_kg": float(last_weight_kg) if last_weight_kg is not None else None,
                }
            )

        results.sort(
            key=lambda item: (str(item["local_date"] or ""), item["activity_id"], item["muscle_group"]),
            reverse=True,
        )
        return results

    def get_activity_slots(self, user_id: int, date_from: Any) -> dict[int, Any]:
        """Slot do Modelo de cada sessao do usuario a partir de `date_from`
        (`activity_id -> SessionSlot`), pelo vinculo `activities.workout_plan_id`
        -> `workout_plans.template_id` + `template_slot_sequence`. Sessao sem
        ficha, ficha sem Modelo ou sem posicao fica fora (= sem slot). O nome e
        o id do slot sao os da versao que governa a ficha (`plan_version_expr`)
        e servem so para exibir; a chave e `(raiz da linhagem do Modelo,
        slot_sequence)` (`slot_session_comparison`). Dois statements (slots +
        linhagem)."""
        from .slot_session_comparison import session_slot_from_row
        from .template_lineage_reader import load_lineage_roots

        rows = self.fetch_all(_ACTIVITY_SLOTS_SQL, (user_id, date_from))
        # Copia de Modelo (090) conta como o Modelo de origem: raiz pelo dono.
        lineage_roots = load_lineage_roots(self.fetch_all, (row.get("template_id") for row in rows))
        slots: dict[int, Any] = {}
        for row in rows:
            slot = session_slot_from_row(row, lineage_roots)
            if slot is not None:
                slots[int(row["activity_id"])] = slot
        return slots

    def get_slot_session_comparison(
        self, user_id: int, limit: int = 400, training_goal: str | None = None
    ) -> dict[str, Any]:
        """Comparativo T-0..T-3 pelo MESMO slot do Modelo, nos tres niveis do
        "Agrupar por", para `/volume-grupamentos` (regra no dono
        `slot_session_comparison`). Le as mesmas fontes que a tela ja usava
        (`get_recent_exercise_volume` e `get_equivalent_sets_by_activity`, com o
        mesmo `limit`), anota a hierarquia do grupo pelo dono e o slot de cada
        sessao; nenhum numero muda, so quais sessoes entram em cada coluna."""
        from .methodology_volume_service import annotate_group_hierarchy, load_group_hierarchy
        from .slot_session_comparison import build_slot_comparison

        volume_rows = self.get_recent_exercise_volume(user_id=user_id, limit=limit, training_goal=training_goal)
        equivalent_rows = self.get_equivalent_sets_by_activity(
            limit=limit, user_id=user_id, training_goal=training_goal
        )
        hierarchy = load_group_hierarchy()
        annotate_group_hierarchy(volume_rows, hierarchy)
        annotate_group_hierarchy(equivalent_rows, hierarchy)

        dates = [row.get("local_date") for row in (*volume_rows, *equivalent_rows) if row.get("local_date")]
        slots = self.get_activity_slots(user_id, min(str(d) for d in dates)) if dates else {}
        payload = build_slot_comparison(volume_rows, equivalent_rows, slots)
        payload["limit"] = limit
        return payload

    def get_daily_muscle_activation(
        self,
        days: int = 7,
        user_id: int | None = None,
        *,
        start_date: Any = None,
        end_date: Any = None,
        counting_mode: Any = None,
        block_of: Any = None,
    ) -> list[dict[str, Any]]:
        """Ativacao muscular DIARIA (agregada por `(local_date, muscle_group)`,
        somando todas as atividades do mesmo dia no mesmo ponto), usada pela
        tabela "Ativacao muscular (ultimos 7 dias)" de `/treinos` - substitui
        o par de endpoints legados de `muscle_groups_mapped.py`
        (`/analytics/muscle-groups-mapped-trend` +
        `/analytics/muscle-groups-mapped-health`), que rodava SQL propria
        desalinhada deste motor unico de volume e nem selecionava `rpe`.

        Reaproveita o MESMO padrao de `get_muscle_activation_by_activity`
        (fonte de verdade: `exercise_muscle_contributions`/`stimulus_weight`,
        resolvida via `_resolve_muscle_contributions_for_mappings`), mas: (a)
        agrega por `(local_date, muscle_group)` em vez de por
        `activity_id`, somando todas as atividades do mesmo dia; (b) NAO
        separa por role - soma PRIMARY+SECONDARY+STABILIZER juntos, pois a
        tabela de `/treinos` so precisa do total; (c) aplica a formula
        universal `sets_count * stimulus_weight` (`equivalent_sets`, mesma
        formula de `get_equivalent_sets_by_activity`) em vez de contar
        series brutas - um exercicio com contribuicao fracionaria (ex.
        SECONDARY a 0.3) agora soma proporcionalmente ao grupo, em vez de 1
        serie inteira por grupo.

        Retorna um item por combinacao `(local_date, muscle_group)` com
        contribuicao > 0: {"local_date", "muscle_group", "muscle_group_id",
        "sets_count", "direct_sets", "had_rpe_9_10"}. `direct_sets` e
        `muscle_group_id` sao aditivos (tabela de 7 dias de /dashboard/musculos
        x macro-grupos de volume): `direct_sets` soma `sets_count` SO das
        contribuicoes PRIMARY, sem peso - a unidade das faixas da metodologia
        em `primary_only` (o default). Lote 4 do modo de contagem: com
        `counting_mode` o peso de cada contribuicao em `direct_sets` e o do
        motor (`methodology_volume.counted_contributions`, nunca um filtro de
        papel aqui); fora de `primary_only` a linha ganha
        `direct_secondary_sets` (a parte de `direct_sets` vinda de SECONDARY,
        que decide a frequencia pelo motor) e, com `block_of` (grupo -> bloco),
        fatia + pai do mesmo exercicio contam uma vez no bloco com o maior peso
        (`block_contributions_by_member`, a juncao do motor do Modelo). Em
        `primary_only` a linha e bit a bit a de antes. O campo `sets_count` continua com esse nome por
        compatibilidade de contrato com o frontend, mas agora carrega
        series EQUIVALENTES (`equivalent_sets`, pode ser fracionario) em vez
        de contagem bruta. `had_rpe_9_10` e o OR logico entre todos os
        exercicios que contribuiram pro grupo naquele dia (se qualquer um
        teve uma serie em RPE 9-10, o grupo "teve" RPE 9-10 naquele dia).
        NAO aplica o limiar de 9 series nem exige `had_rpe_9_10=True` aqui -
        isso e responsabilidade de quem consome (hoje: o frontend de
        `/treinos`), este metodo so calcula e devolve os dois numeros
        brutos. Exercicios sem nenhuma contribuicao resolvida (mapping sem
        grupo cadastrado) sao ignorados silenciosamente."""
        from .methodology_volume import counting_mode_or_default, counts_secondary
        from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

        mode = counting_mode_or_default(counting_mode)
        with_secondary = counts_secondary(mode)

        # `start_date`/`end_date` (PR-13, Modo A determinístico, §6.1 passo 1):
        # janela MÓVEL fechada, em vez dos últimos `days` dias contados de
        # CURDATE(). A janela de ativação do Modo A termina no dia do SLOT, que
        # pode ser hoje ou uma data futura — `CURDATE()` não serve de âncora
        # nesse caso. Sem os dois parâmetros o comportamento histórico (usado
        # por `/treinos`) é byte a byte o mesmo.
        if start_date is not None and end_date is not None:
            where = ["a.local_date BETWEEN %s AND %s", "e.mapping_id IS NOT NULL"]
            params: list[Any] = [str(start_date), str(end_date)]
        else:
            where = ["a.local_date >= %s", "e.mapping_id IS NOT NULL"]
            params = [self._days_ago(user_id, days)]
        if user_id is not None:
            where.append("a.user_id = %s")
            params.append(user_id)
        where_clause = f"WHERE {' AND '.join(where)}"

        exercise_rows = self.fetch_all(
            f"""
            SELECT
                e.id AS exercise_id,
                CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
                e.sets_count AS sets_count,
                e.training_goal AS training_goal,
                a.local_date AS local_date,
                MAX(CASE WHEN es.rpe BETWEEN 9 AND 10 THEN 1 ELSE 0 END) AS had_rpe_9_10,
                {sql_application_rows()} AS technique_rows
            FROM exercises e
            JOIN activities a ON a.id = e.activity_id
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            {where_clause}
            GROUP BY e.id, e.mapping_id, elm.canonical_exercise_id, e.sets_count, e.training_goal, a.local_date
            """,
            tuple(params),
        )
        # Janela de ativacao (Fase 7) e tabela de /treinos: aplicacao de
        # tecnica conta a sua fatia do bloco (Fase 5, PR 4).
        apply_application_units(exercise_rows, self.fetch_all, self.technique_catalog())

        mapping_ids = {row["mapping_id"] for row in exercise_rows if row.get("mapping_id")}
        contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(mapping_ids)
        counted_by_mapping_id = {
            mapping_id: _counted_by_group_name(contributions, mode, block_of if with_secondary else None)
            for mapping_id, contributions in contributions_by_mapping_id.items()
        }

        def _new_bucket() -> dict[str, Any]:
            return {
                "equivalent_sets": 0.0,
                "direct_sets": 0.0,
                "direct_secondary_sets": 0.0,
                "had_rpe_9_10": False,
                "muscle_group_id": None,
            }

        buckets: dict[tuple[Any, str], dict[str, Any]] = defaultdict(_new_bucket)

        for row in exercise_rows:
            mapping_id = row.get("mapping_id")
            sets_count = row.get("sets_count") or 0
            if not mapping_id or not sets_count:
                continue

            # M97 F2: ativacao/metodologia do realizado = volume HIPERTROFICO
            # (serie de forca no peso do dono `methodology_volume`).
            sets_count = _hypertrophy_volume_units(sets_count, row.get("training_goal"))
            local_date = row.get("local_date")
            had_rpe_9_10 = bool(row.get("had_rpe_9_10"))

            counted = counted_by_mapping_id.get(mapping_id, {})
            for contribution in contributions_by_mapping_id.get(mapping_id, []):
                group_name = contribution.get("muscle_group_name")
                if not group_name:
                    continue
                stimulus_weight = float(contribution.get("stimulus_weight") or 0.0)

                bucket = buckets[(local_date, group_name)]
                bucket["equivalent_sets"] += sets_count * stimulus_weight
                # `direct_sets`: mesma unidade do motor da metodologia, NO
                # MODO pedido - o peso e o do motor (`counted_contributions`):
                # em `primary_only`, series dos itens com contribuicao PRIMARY
                # no grupo, SEM `stimulus_weight`. E o numero comparavel com
                # as faixas dos macro-grupos de volume do modo (tabela de
                # ativacao de 7 dias de /dashboard/musculos). Um grupo conta
                # uma vez por exercicio (as linhas do mesmo grupo ja vem
                # juntas do motor).
                weight = counted.get(group_name)
                if weight is not None:
                    bucket["direct_sets"] += float(sets_count) * weight.weight
                    if not weight.is_primary:
                        bucket["direct_secondary_sets"] += float(sets_count) * weight.weight
                if bucket["muscle_group_id"] is None and contribution.get("muscle_group_id") is not None:
                    bucket["muscle_group_id"] = int(contribution["muscle_group_id"])
                bucket["had_rpe_9_10"] = bucket["had_rpe_9_10"] or had_rpe_9_10

        results: list[dict[str, Any]] = []
        for (local_date, group_name), bucket in buckets.items():
            if bucket["equivalent_sets"] <= 0:
                continue
            item = {
                "local_date": local_date,
                "muscle_group": group_name,
                "muscle_group_id": bucket["muscle_group_id"],
                "sets_count": round(bucket["equivalent_sets"], 2),
                "direct_sets": round(bucket["direct_sets"], 2),
                "had_rpe_9_10": bool(bucket["had_rpe_9_10"]),
            }
            if with_secondary:
                item["direct_secondary_sets"] = round(bucket["direct_secondary_sets"], 2)
            results.append(item)

        results.sort(key=lambda r: (str(r["local_date"] or ""), r["muscle_group"]))
        return results

    @staticmethod
    def _resolve_user_weekly_targets(user_id: int | None) -> tuple[int | None, dict[int, dict[str, float]]]:
        """Metas de Ativacao (`training_type_muscle_group_targets`) aplicaveis
        a uma janela de periodo do usuario: MESMA resolucao de
        `analytics._build_muscle_status_items` (dashboard-muscle-status/
        overview) - `training_type_id` do template ativo mais recentemente
        atribuido (`resolve_active_training_type_for_user`) e suas metas
        semanais por grupo. Nao usa `get_training_type_weekly_targets_for_activity`
        porque aquela resolve a meta de UMA activity (fracao do slot do
        plano), e o breakdown agrega um periodo de varias sessoes.

        Sem user_id ou sem training_type resolvido -> sem metas (sem o
        fallback generico: "excesso" exige meta cadastrada)."""
        if user_id is None:
            return None, {}
        from ..training_types import get_training_type_muscle_group_targets, resolve_active_training_type_for_user

        training_type_id = resolve_active_training_type_for_user(user_id)
        if training_type_id is None:
            return None, {}
        return training_type_id, get_training_type_muscle_group_targets(training_type_id)

    def get_volume_breakdown(self, days: int = 30, user_id: int | None = None) -> dict[str, Any]:
        # Sem `estimated_recovery_hours` por grupo (M76 Lote 2c, B6): era a
        # dose -> horas SEM coeficiente/TSB/sinais, com o mesmo nome do campo
        # oficial de `MuscleRecoveryService.get_recovery_overview`, e nenhuma
        # tela o renderizava. Recuperacao vem so do modelo de recuperacao.
        rows = self.get_muscle_group_overview(days=days, limit=32, user_id=user_id)
        training_type_id, targets_by_group_id = self._resolve_user_weekly_targets(user_id)
        from .methodology_volume import calendar_weekly_rate
        for row in rows:
            # volume_status pela Meta de Ativacao aplicavel (excesso: #73;
            # subtreino: 2026-09-14). Volume SEMANAL do grupo em series
            # equivalentes (`stimulus_equivalent_sets` da janela em taxa
            # semanal pelo dono, `methodology_volume.calendar_weekly_rate` -
            # mesma unidade e normalizacao de dashboard-muscle-status),
            # classificado por `classify_weekly_volume_status`, sem regra
            # paralela:
            # - BELOW_MIN (< min_weekly_sets)            -> "subtreino"
            # - ABOVE_MAX (> max_weekly_sets)            -> "excesso"
            # - demais estados (min..max, inclusivos)    -> "ideal"
            # Sem meta aplicavel (sem user_id/training_type, grupo sem meta
            # ou sem id) -> "ideal" (neutro), sem fallback para numeros fixos.
            # RPE nao entra. `days_since_trained` (recencia) continua no
            # payload, mas nao define volume_status (antes: subtreino se
            # sets_count <= 6 ou days_since_trained >= 10).
            group_id = row.get("muscle_group_id")
            target = targets_by_group_id.get(group_id) if group_id is not None else None
            in_window = float(row.get("stimulus_equivalent_sets") or 0.0)
            weekly_equivalent_sets = calendar_weekly_rate(in_window, days) if days else in_window
            row["weekly_equivalent_sets"] = round(weekly_equivalent_sets, 2)
            row["min_weekly_sets"] = target["min"] if target else None
            row["max_weekly_sets"] = target["max"] if target else None
            row["volume_target_training_type_id"] = training_type_id if target else None

            row["volume_status"] = "ideal"
            if target:
                adherence = classify_weekly_volume_status(
                    weekly_equivalent_sets, target["min"], target["target"], target["max"]
                )
                if adherence == "BELOW_MIN":
                    row["volume_status"] = "subtreino"
                elif adherence == "ABOVE_MAX":
                    row["volume_status"] = "excesso"
        return {
            "days": days,
            "items": rows,
        }

    def get_volume_by_split_day(self, days: int = 180, user_id: int | None = None) -> list[dict[str, Any]]:
        """Volume total por sessao (activity), agregado por letra do ciclo
        ABC (A/B/C), para o grafico comparativo de evolucao em `/treinos`.

        So entram activities vinculadas a um workout_plan com
        `plan_type='abc'` e `split_day` em A/B/C (via
        `activities.workout_plan_id`, FK real - ver
        `workout_sessions.py`/`services.py`). Treinos sem plano vinculado
        (import legado, avulso) ou de outros plan_type ficam de fora, por
        design.

        Agregacao por SEMANA (nao mais por data exata): `period` e a
        segunda-feira da semana em que a sessao ocorreu
        (`DATE_SUB(a.local_date, INTERVAL WEEKDAY(a.local_date) DAY)`), o
        que agrupa corretamente por semana civil (segunda a domingo) sem a
        ambiguidade de virada de ano que `YEARWEEK` teria para computar o
        `period` de exibicao. Multiplas sessoes do mesmo split_day na mesma
        semana somam no `SUM`/`GROUP BY`.

        Formula de volume identica a demais pontos do sistema:
        peso * reps * rpe, com fallback de RPE `DOSE_RPE_WHEN_MISSING`
        (`domain/e1rm`, 7,0) quando nulo - o mesmo default da dose
        (`e1rm.dose_inputs`), interpolado do dono.

        `user_id` opcional (None preserva o comportamento historico sem
        filtro, mesma ressalva de `_fetch_window_sets` acima). Corrigido na
        Fase 1 do vazamento multiusuario (ver plano
        mighty-coalescing-pond.md)."""
        days = int(self.clamp(days, 7, 180))
        user_filter = "AND a.user_id = %s" if user_id is not None else ""
        params: list[Any] = [self._days_ago(user_id, days)]
        if user_id is not None:
            params.append(user_id)
        rows = self.fetch_all(
            f"""
            SELECT
              DATE_FORMAT(
                DATE_SUB(a.local_date, INTERVAL WEEKDAY(a.local_date) DAY),
                '%%Y-%%m-%%d'
              ) AS period,
              wp.split_day AS split_day,
              SUM({sql_if_rep_based(f'COALESCE(es.weight_kg, 0) * COALESCE(es.reps, 0) * COALESCE(es.rpe, {float(e1rm.DOSE_RPE_WHEN_MISSING)})')}) AS volume_kg
            FROM activities a
            JOIN workout_plans wp ON wp.id = a.workout_plan_id
            JOIN exercises e ON e.activity_id = a.id
            JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            WHERE wp.plan_type = 'abc'
              AND wp.split_day IN ('A', 'B', 'C')
              AND a.local_date >= %s
              {user_filter}
            GROUP BY period, wp.split_day
            ORDER BY period ASC, wp.split_day ASC
            """,
            tuple(params),
        )
        for row in rows:
            row["volume_kg"] = round(float(row["volume_kg"] or 0), 2)
        return rows

    def get_volume_trend(self, days: int = 90, user_id: int | None = None) -> dict[str, Any]:
        """Serie temporal RAW/legada de volume por DIA e por grupo muscular
        (`sets_count`/`volume_kg`/`reps_total`/`avg_weight_kg`/`avg_rpe`, 1
        grupo por serie via COALESCE(primary, secondary, 'Outros') - mesma
        convencao legada documentada em `get_muscle_group_overview`).

        Esta e a implementacao oficial e UNICA desse formato especifico de
        serie temporal por dia/periodo - nao e uma duplicacao a ser
        eliminada, e sim uma variacao legitima e complementar a:
        - `get_muscle_group_overview`: totais agregados por JANELA (sem
          quebra temporal), com campos ponderados (contribuicoes
          primary/secondary/stabilizer);
        - `get_rolling_weekly_trend_by_muscle_group`: serie SEMANAL (janela
          movel de 7 dias ancorada em hoje) com campos ponderados
          (`direct_sets`/`indirect_sets`/`stimulus_equivalent_sets`) alem
          dos legados.

        Cada um serve um consumidor diferente: este metodo alimenta o
        grafico de evolucao diaria de `/volume-grupamentos`.

        `user_id` opcional (None preserva o comportamento historico sem
        filtro, mesma ressalva de `_fetch_window_sets` acima). Corrigido na
        Fase 1 do vazamento multiusuario (ver plano
        mighty-coalescing-pond.md)."""
        user_filter = "AND a.user_id = %s" if user_id is not None else ""
        params: list[Any] = [self._days_ago(user_id, days)]
        if user_id is not None:
            params.append(user_id)
        rows = self.fetch_all(
            f"""
            SELECT
              DATE_FORMAT(a.local_date, '%%Y-%%m-%%d') AS period,
              COALESCE(NULLIF(pg.name, ''), NULLIF(sg.name, ''), 'Outros') AS muscle_group,
              COUNT(es.id) AS sets_count,
                            COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total,
              COALESCE(SUM({sql_if_rep_based('COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0)')}), 0) AS volume_kg,
              ROUND(AVG(es.weight_kg), 2) AS avg_weight_kg,
              ROUND(AVG(es.rpe), 2) AS avg_rpe
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN muscle_groups pg ON pg.id = (
              SELECT emc_pri.muscle_group_id
              FROM exercise_muscle_contributions emc_pri
              WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
              ORDER BY emc_pri.id ASC LIMIT 1
            )
            LEFT JOIN muscle_groups sg ON sg.id = (
              SELECT emc_sec.muscle_group_id
              FROM exercise_muscle_contributions emc_sec
              WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
              ORDER BY emc_sec.id ASC LIMIT 1
            )
            WHERE a.local_date >= %s
              {user_filter}
            GROUP BY DATE_FORMAT(a.local_date, '%%Y-%%m-%%d'), COALESCE(NULLIF(pg.name, ''), NULLIF(sg.name, ''), 'Outros')
            ORDER BY period ASC, muscle_group ASC
            """,
            tuple(params),
        )
        return {"days": days, "items": rows}

    def get_rolling_weekly_trend_by_muscle_group(
        self, days: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Mesma janela movel de 7 dias de `compute_rolling_weekly_trend`
        (`analytics.py`), mas quebrada por grupo muscular (via
        `exercise_zepp_mappings`/`muscle_groups`, mesma taxonomia usada em
        `get_volume_trend`), usada exclusivamente pelo campo
        `weekly_trend_by_muscle_group` de
        `DashboardAggregatorService.build_training_evolution_payload`
        (contexto da IA em /treinos). Aditivo: NAO substitui `weekly_trend`,
        que continua agregado globalmente.

        Fase 3 da reforma de volume: alem dos campos legados BRUTOS
        (`sets_count`/`reps_total`/`volume_kg`/`avg_rpe`, calculados exatamente
        como antes - 1 grupo por serie, via
        COALESCE(primary_group, secondary_group, 'Outros')), agora tambem
        resolve as contribuicoes ponderadas (role + peso) de cada serie via
        `self._resolve_contributions` (mesma logica de
        `get_muscle_group_overview`), agregando
        `direct_sets`/`indirect_sets`/`stimulus_equivalent_sets` por
        (bucket_semanal, muscle_group). Como uma serie pode contribuir para MAIS
        de um grupo agora (primario + secundario), um grupo pode aparecer com
        campos novos preenchidos mesmo sem nenhum registro nos campos legados
        (ex.: um grupo que so participa indiretamente nessa janela) - isso e
        aditivo e intencional, nao um bug.

        `user_id` opcional (None preserva o comportamento historico sem
        filtro, usado por `DashboardAggregatorService`); o drilldown novo do
        dashboard (`GET /analytics/dashboard-muscle-drilldown`) sempre
        propaga `identity.user_id`.
        """
        user_filter = "AND a.user_id = %s" if user_id is not None else ""
        today = self._user_today(user_id)
        params: list[Any] = [today, today - timedelta(days=int(days))]
        if user_id is not None:
            params.append(user_id)
        rows = self.fetch_all(
            f"""
            SELECT
                FLOOR(DATEDIFF(%s, a.local_date) / 7) AS bucket_index,
                a.local_date AS local_date,
                e.id AS exercise_row_id,
                es.id AS set_id,
                es.set_number AS set_number,
                es.set_type AS set_type,
                es.technique_code AS technique_code,
                es.anchor_set_number AS anchor_set_number,
                es.reps AS reps,
                es.weight_kg AS weight_kg,
                es.rpe AS rpe,
                es.measure AS measure,
                NULLIF(pg.name, '') AS primary_group,
                NULLIF(sg.name, '') AS secondary_group,
                pg.id AS primary_group_id,
                sg.id AS secondary_group_id,
                zm.id AS mapping_id,
                e.training_goal AS training_goal,
                (
                  SELECT emc_sec.stimulus_weight
                  FROM exercise_muscle_contributions emc_sec
                  WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                    AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
                  ORDER BY emc_sec.id ASC LIMIT 1
                ) AS secondary_participation
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN exercise_catalog zm ON zm.id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
            LEFT JOIN muscle_groups pg ON pg.id = (
              SELECT emc_pri.muscle_group_id
              FROM exercise_muscle_contributions emc_pri
              WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
              ORDER BY emc_pri.id ASC LIMIT 1
            )
            LEFT JOIN muscle_groups sg ON sg.id = (
              SELECT emc_sec.muscle_group_id
              FROM exercise_muscle_contributions emc_sec
              WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
              ORDER BY emc_sec.id ASC LIMIT 1
            )
            WHERE a.local_date >= %s
              AND (
                LOWER(a.activity_type) LIKE '%%muscul%%'
                OR LOWER(a.activity_type) LIKE '%%força%%'
                OR LOWER(a.activity_type) LIKE '%%strength%%'
              )
              {user_filter}
            ORDER BY e.id ASC, es.set_number ASC
            """,
            tuple(params),
        )

        # Series equivalentes de cada linha (Fase 5, PR 4) - so as que tem
        # serie (o LEFT JOIN traz exercicio sem serie com `set_id` nulo).
        annotate_set_units(
            [row for row in rows if row.get("set_id") is not None],
            self.technique_catalog(),
            exercise_key="exercise_row_id",
        )

        mapping_ids = {row["mapping_id"] for row in rows if row.get("mapping_id")}
        contributions_by_mapping = self._fetch_contributions_by_mapping(mapping_ids)
        # So busca o lookup de nomes quando ha de fato alguma linha explicita na
        # tabela nova (mesma otimizacao de `get_muscle_group_overview`) - o
        # fallback legado ja resolve o nome via texto, sem query extra.
        muscle_group_names_by_id: dict[int, str] = (
            self.muscle_group_names_by_id()
            if contributions_by_mapping
            else {}
        )

        buckets: dict[tuple, dict[str, Any]] = {}

        def _ensure_bucket(bucket_index: int, muscle_group: str) -> dict[str, Any]:
            key = (bucket_index, muscle_group)
            if key not in buckets:
                buckets[key] = {
                    "period_start": None,
                    "period_end": None,
                    "sets_count": 0,
                    "reps_total": 0,
                    "volume_kg": 0.0,
                    "rpe_sum": 0.0,
                    "rpe_n": 0,
                    "direct_sets": 0.0,
                    "indirect_sets": 0.0,
                    "stimulus_equivalent_sets": 0.0,
                }
            return buckets[key]

        def _touch_period(bucket: dict[str, Any], local_date: Any) -> None:
            if local_date is None:
                return
            if bucket["period_start"] is None or str(local_date) < str(bucket["period_start"]):
                bucket["period_start"] = local_date
            if bucket["period_end"] is None or str(local_date) > str(bucket["period_end"]):
                bucket["period_end"] = local_date

        for row in rows:
            bucket_index = int(row["bucket_index"])
            local_date = row.get("local_date")
            has_set = row.get("set_id") is not None

            # Campos legados: exatamente a mesma logica de antes (1 grupo por
            # serie, via COALESCE(primary, secondary, 'Outros')), calculados a
            # partir da MESMA fonte de dados (sem quebrar nenhum consumidor
            # existente de `weekly_trend_by_muscle_group`).
            primary_name = row.get("primary_group")
            secondary_name = row.get("secondary_group")
            legacy_group = primary_name or secondary_name or "Outros"
            legacy_bucket = _ensure_bucket(bucket_index, legacy_group)
            _touch_period(legacy_bucket, local_date)

            if has_set:
                reps = row.get("reps") or 0
                weight_kg = row.get("weight_kg") or 0
                legacy_bucket["sets_count"] += 1
                legacy_bucket["reps_total"] += reps
                # Lote T3: reps x kg so de serie de repeticoes.
                if is_rep_based(row.get("measure")):
                    legacy_bucket["volume_kg"] += reps * weight_kg
                if row.get("rpe") is not None:
                    legacy_bucket["rpe_sum"] += row["rpe"]
                    legacy_bucket["rpe_n"] += 1

                # Campos novos (Fase 2/3): contribuicoes ponderadas por
                # role/peso, resolvidas serie a serie - podem gerar mais de um
                # grupo por serie (ex.: primario + secundario), diferente da
                # logica legada de grupo unico acima.
                contributions = self._resolve_contributions(
                    row.get("mapping_id"),
                    contributions_by_mapping,
                    primary_name,
                    secondary_name,
                    row.get("primary_group_id"),
                    row.get("secondary_group_id"),
                    row.get("secondary_participation"),
                )
                for contrib in contributions:
                    group_id = contrib["muscle_group_id"]
                    group_name = contrib.get("muscle_group_name") or (
                        muscle_group_names_by_id.get(group_id, "Outros") if group_id is not None else "Outros"
                    )
                    contrib_bucket = _ensure_bucket(bucket_index, group_name)
                    _touch_period(contrib_bucket, local_date)

                    # M97 F4: series diretas/indiretas/de estimulo (volume
                    # hipertrofico) com a serie de forca pelo peso do dono;
                    # os campos legados brutos acima seguem cheios.
                    units = _hypertrophy_volume_units(float(row["set_units"]), row.get("training_goal"))
                    role_upper = (contrib.get("role") or "").strip().upper()
                    if role_upper == "PRIMARY":
                        contrib_bucket["direct_sets"] += units
                    elif role_upper in ("SECONDARY", "STABILIZER"):
                        contrib_bucket["indirect_sets"] += units
                    contrib_bucket["stimulus_equivalent_sets"] += contrib["stimulus_weight"] * units

        result = []
        for (bucket_index, muscle_group), bucket in sorted(buckets.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
            period = f"D-{bucket_index * 7} a D-{bucket_index * 7 + 6}"
            avg_rpe = round(bucket["rpe_sum"] / bucket["rpe_n"], 2) if bucket["rpe_n"] else None
            result.append({
                "period": period,
                "period_start": bucket["period_start"],
                "period_end": bucket["period_end"],
                "muscle_group": muscle_group,
                "sets_count": bucket["sets_count"],
                "reps_total": bucket["reps_total"],
                "volume_kg": round(bucket["volume_kg"], 2),
                "avg_rpe": avg_rpe,
                "direct_sets": series_number(bucket["direct_sets"]),
                "indirect_sets": series_number(bucket["indirect_sets"]),
                "stimulus_equivalent_sets": round(bucket["stimulus_equivalent_sets"], 2),
            })

        return result

    # =========================================================================
    # Dashboard novo (hub paralelo /dashboard/*, Fase 1 - continuacao desta
    # sessao). Ver plano completo em
    # C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
    # =========================================================================

    def get_planned_effective_sets(
        self, user_id: int | None, date_from: date, date_to: date
    ) -> list[dict[str, Any]]:
        """PLANEJADO por grupo muscular das fichas (`workout_plans`) salvas em
        `[date_from, date_to]` (`COALESCE(plan_date, DATE(created_at))`).
        Fonte da coluna "Planejado" de `/dashboard/musculos`.

        Fonte primaria (desde 2026-10-02): a PRESCRICAO da propria ficha,
        `workout_plan_exercises` - `sets_count x stimulus_weight` por
        contribuicao ativa do exercicio (`PLAN_EXERCISES_ACTIVATION_SQL`), com
        o equivalente planejado da tecnica avancada. E a mesma unidade e a
        mesma regra de ativacao do realizado (`ROLE_TOTALS_SQL`: id canonico
        via `exercise_legacy_id_map`, `emc.is_active = 1`, peso de estimulo)
        e do Modelo por equipamento (`TEMPLATE_PLAN_ACTIVATION_SQL`).
        Aquecimento e serie de ajuste nao entram: moram em colunas proprias
        (`warmup_sets_count`, `feeler_set_sets_count`) e `sets_count` e so
        trabalho.

        Alternativa, por ficha: `raw_payload.template_slot_targets` - so para
        a ficha que nao tem nenhum exercicio com contribuicao muscular (fichas
        antigas do motor v2). Ate 2026-10-02 esta chave era a UNICA fonte, e a
        geracao `equipment_series` nunca a grava: o Planejado ficava vazio
        para todo Modelo por equipamento. Uma ficha nunca soma pelas duas
        fontes."""
        from .. import services

        where = _planned_plans_where("wp", user_id)
        params = _planned_plans_params(user_id, date_from, date_to)
        rows = self.fetch_all(PLAN_EXERCISES_ACTIVATION_SQL.format(where=where), params)
        plans_with_prescription = {row.get("plan_id") for row in rows if row.get("plan_id") is not None}

        plan_rows = self.fetch_all(f"SELECT wp.id AS id FROM workout_plans wp WHERE {where}", params)
        fallback_ids = sorted(
            {row["id"] for row in plan_rows if row.get("id") is not None} - plans_with_prescription
        )
        # `raw_payload` so das fichas SEM prescricao aproveitavel: e a coluna
        # pesada da tabela (contexto de geracao inteiro) e a RDS Data API
        # devolve no maximo 1 MB por resposta.
        payloads: list[Any] = []
        if fallback_ids:
            placeholders = ",".join(["%s"] * len(fallback_ids))
            payloads = [
                services.from_json_dict(row.get("raw_payload"))
                for row in self.fetch_all(
                    f"SELECT id, raw_payload FROM workout_plans WHERE id IN ({placeholders})",
                    tuple(fallback_ids),
                )
            ]
        return aggregate_planned_effective_sets(rows, payloads, catalog=new_catalog())

    def get_top_exercises_for_muscle_group(
        self, muscle_group_id: int, days: int, user_id: int | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Principais exercicios (por series equivalentes) de UM grupo
        muscular na janela `days`, para `GET /analytics/dashboard-muscle-drilldown`
        (Fase 1 do dashboard novo).

        Fatia de `get_top_exercises_by_muscle_group` (M19): o ranking de TODOS
        os grupos sai de uma unica passada/duas consultas, e este metodo so
        devolve a entrada do grupo pedido. O resultado e identico ao da versao
        anterior (que refazia a passada por grupo) - a prova campo a campo esta
        em `tests/integration/dataapi/test_top_exercicios_por_grupo_performance.py`."""
        return self.get_top_exercises_by_muscle_group(days=days, user_id=user_id, limit=limit).get(
            muscle_group_id, []
        )

    def get_top_exercises_by_muscle_group(
        self, days: int, user_id: int | None = None, limit: int = 5
    ) -> dict[Any, list[dict[str, Any]]]:
        """Ranking de exercicios por series equivalentes de CADA grupo
        muscular da janela `days`, em UMA passada.

        Por que existe (M19): o alerta de estagnacao de `dashboard-alerts`
        (removido no M76 Lote 2c) chamava `get_top_exercises_for_muscle_group` uma vez POR GRUPO, e cada
        chamada reemitia as MESMAS duas consultas (contribuicoes explicitas de
        todos os mapping_ids da janela + nomes do catalogo) e refazia a
        resolucao de contribuicao das ~16.000 series. Com 12 grupos na janela
        isso eram 24 statements e 12 passadas para produzir 12 fatias de um
        unico ranking. Aqui e 1 passada e 2 statements, cacheados por
        `(days, user_id)` na instancia - o custo que manda no shim da RDS Data
        API e a CONTAGEM de statements, nao o volume lido (licao do M20).

        Reaproveita `_fetch_window_sets` + `_fetch_contributions_by_mapping` +
        `_resolve_contributions` - os MESMOS blocos usados por
        `get_muscle_group_overview`, sem duplicar JOIN nem formula de peso -,
        com um lookup bulk SEPARADO (so `exercise_catalog`/
        `exercise_external_mappings`, pequeno) para exibir o nome do exercicio,
        ausente em `_fetch_window_sets`.

        A ordem de desempate e preservada: a passada percorre `raw_sets` na
        MESMA ordem de antes e o `sort` do Python e estavel, entao dentro de
        cada grupo a ordem de insercao (e o resultado do `sort`) e a mesma que
        a versao por grupo produzia."""
        cache_key = (days, user_id)
        cached = self._top_exercises_cache.get(cache_key)
        if cached is None:
            cached = self._compute_top_exercises_by_muscle_group(days, user_id)
            self._top_exercises_cache[cache_key] = cached
        # Copia profunda: o chamador pode alterar o que recebe sem contaminar o
        # cache (mesma politica de `_overview_cache`).
        return {group_id: copy.deepcopy(items[:limit]) for group_id, items in cached.items()}

    def _compute_top_exercises_by_muscle_group(
        self, days: int, user_id: int | None
    ) -> dict[Any, list[dict[str, Any]]]:
        raw_sets = self._ensure_set_units(self._fetch_window_sets(days, user_id=user_id))
        mapping_ids_all = {row["mapping_id"] for row in raw_sets if row.get("mapping_id")}
        contributions_by_mapping = self._fetch_contributions_by_mapping(mapping_ids_all)

        totals_by_group: dict[Any, dict[Any, dict[str, Any]]] = {}
        for row in raw_sets:
            contributions = self._resolve_contributions(
                row.get("mapping_id"),
                contributions_by_mapping,
                row.get("primary_group"),
                row.get("secondary_group"),
                row.get("primary_group_id"),
                row.get("secondary_group_id"),
                row.get("secondary_participation"),
            )
            for contrib in contributions:
                totals = totals_by_group.setdefault(contrib["muscle_group_id"], {})
                key = row.get("mapping_id") or ("exercise", row["exercise_id"])
                bucket = totals.setdefault(
                    key,
                    {
                        "mapping_id": row.get("mapping_id"),
                        "sets_count": 0,
                        "stimulus_equivalent_sets": 0.0,
                        "last_trained_at": None,
                    },
                )
                bucket["sets_count"] += 1
                # M97 F4: estimulo com a serie de forca pelo peso do dono.
                bucket["stimulus_equivalent_sets"] += contrib["stimulus_weight"] * _hypertrophy_volume_units(
                    float(row["set_units"]), row.get("training_goal")
                )
                trained_at = row.get("trained_at")
                if trained_at and (
                    bucket["last_trained_at"] is None or str(trained_at) > str(bucket["last_trained_at"])
                ):
                    bucket["last_trained_at"] = trained_at

        mapping_ids = {
            bucket["mapping_id"]
            for totals in totals_by_group.values()
            for bucket in totals.values()
            if bucket.get("mapping_id")
        }
        names_by_mapping: dict[int, str] = {}
        if mapping_ids:
            placeholders = ",".join(["%s"] * len(mapping_ids))
            name_rows = self.fetch_all(
                f"""
                SELECT ec.id, COALESCE(ec.name, (
                    SELECT exm.external_name
                    FROM exercise_external_mappings exm
                    WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                    ORDER BY exm.id ASC LIMIT 1
                ), 'Exercício sem nome') AS name
                FROM exercise_catalog ec
                WHERE ec.id IN ({placeholders})
                """,
                tuple(mapping_ids),
            )
            names_by_mapping = {row["id"]: row["name"] for row in name_rows}

        ranking: dict[Any, list[dict[str, Any]]] = {}
        for group_id, totals in totals_by_group.items():
            results: list[dict[str, Any]] = []
            for bucket in totals.values():
                mapping_id = bucket["mapping_id"]
                results.append(
                    {
                        "mapping_id": mapping_id,
                        "exercise_name": names_by_mapping.get(mapping_id, "Exercício não mapeado")
                        if mapping_id
                        else "Exercício não mapeado",
                        "sets_count": bucket["sets_count"],
                        "stimulus_equivalent_sets": round(bucket["stimulus_equivalent_sets"], 2),
                        "last_trained_at": bucket["last_trained_at"],
                    }
                )
            results.sort(key=lambda r: r["stimulus_equivalent_sets"], reverse=True)
            ranking[group_id] = results
        return ranking

    # =========================================================================
    # Dashboard novo (hub paralelo /dashboard/*, Fase 2). Ver plano completo em
    # C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
    # =========================================================================

    def get_workout_sessions_timeline(
        self, user_id: int | None, days: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Timeline de sessoes (`activities`) na janela `days`, com series
        FISICAS (brutas) e series EQUIVALENTES de estimulo/fadiga somadas por
        sessao (soma de TODAS as contribuicoes musculares de TODAS as series
        da sessao, independente de grupo). Gap real confirmado por leitura de
        `WorkoutsClient.tsx`/`app/analytics.py` (Fase 2) - nenhum endpoint
        existente expunha uma timeline por SESSAO individual (data/nome/
        exercicios/series/RPE/volume equivalente/duracao) nem permitia
        comparar sessoes do mesmo "slot" ao longo do tempo. Usado por
        `/dashboard/treinos` (TreinosHistoryClient, hub novo) e por
        `WorkoutComparisonTable` (agrupamento por `name` feito no FRONTEND -
        nao ha vinculo formal `workout_plans`<->`activities` no schema hoje,
        ver plano "Achados-chave da auditoria"; `name` e o unico identificador
        estavel de "qual treino da rotina foi este" disponivel sem inventar
        schema novo).

        Reaproveita `_fetch_window_sets` + `_fetch_contributions_by_mapping` +
        `_resolve_contributions` - OS MESMOS blocos ja usados por
        `get_muscle_group_overview`/`get_top_exercises_for_muscle_group` -,
        so agregando por `activity_id` em vez de `muscle_group_id`. Nenhuma
        formula nova de estimulo/fadiga."""
        raw_sets = self._ensure_set_units(self._fetch_window_sets(days, user_id=user_id))
        mapping_ids_all = {row["mapping_id"] for row in raw_sets if row.get("mapping_id")}
        contributions_by_mapping = self._fetch_contributions_by_mapping(mapping_ids_all)

        sessions: dict[Any, dict[str, Any]] = {}
        for row in raw_sets:
            activity_id = row["activity_id"]
            bucket = sessions.setdefault(
                activity_id,
                {
                    "activity_id": activity_id,
                    "local_date": row.get("local_date"),
                    "name": row.get("activity_name"),
                    "activity_type": row.get("activity_type"),
                    "duration_seconds": row.get("duration_seconds"),
                    "trained_at": row.get("trained_at"),
                    "deload_source": deload_source_value(row.get("deload_source")),
                    "training_goals": set(),
                    "exercise_ids": set(),
                    "sets_count": 0,
                    "rpe_sum": 0.0,
                    "rpe_n": 0,
                    "stimulus_equivalent_sets": 0.0,
                    "fatigue_equivalent_sets": 0.0,
                },
            )
            bucket["exercise_ids"].add(row["exercise_id"])
            bucket["training_goals"].add(goal_value(row.get("training_goal")))
            bucket["sets_count"] += 1
            if row.get("rpe") is not None:
                bucket["rpe_sum"] += float(row["rpe"])
                bucket["rpe_n"] += 1

            contributions = self._resolve_contributions(
                row.get("mapping_id"),
                contributions_by_mapping,
                row.get("primary_group"),
                row.get("secondary_group"),
                row.get("primary_group_id"),
                row.get("secondary_group_id"),
                row.get("secondary_participation"),
            )
            units = float(row["set_units"])
            # M97 F4: estimulo (volume hipertrofico) com a serie de forca pelo
            # peso do dono; a fadiga segue cheia.
            stimulus_units = _hypertrophy_volume_units(units, row.get("training_goal"))
            for contrib in contributions:
                bucket["stimulus_equivalent_sets"] += contrib["stimulus_weight"] * stimulus_units
                bucket["fatigue_equivalent_sets"] += contrib["fatigue_weight"] * units

        results: list[dict[str, Any]] = []
        for bucket in sessions.values():
            results.append(
                {
                    "activity_id": bucket["activity_id"],
                    "local_date": bucket["local_date"],
                    "name": bucket["name"],
                    "activity_type": bucket["activity_type"],
                    "duration_seconds": bucket["duration_seconds"],
                    "trained_at": bucket["trained_at"],
                    "exercises_count": len(bucket["exercise_ids"]),
                    "sets_count": bucket["sets_count"],
                    "avg_rpe": round(bucket["rpe_sum"] / bucket["rpe_n"], 2) if bucket["rpe_n"] else None,
                    "stimulus_equivalent_sets": round(bucket["stimulus_equivalent_sets"], 2),
                    "fatigue_equivalent_sets": round(bucket["fatigue_equivalent_sets"], 2),
                    # M97 F4 (aditivos): sessao de deload marcada (dono
                    # `deload`) e objetivos executados, na ordem do dono.
                    "deload_source": bucket["deload_source"],
                    "deload_label": deload_source_label(bucket["deload_source"]),
                    "training_goals": list(goals_in(bucket["training_goals"])),
                }
            )

        results.sort(key=lambda r: (str(r["local_date"]), r["activity_id"]), reverse=True)
        return results[:limit]

    # =========================================================================
    # Dashboard novo (hub paralelo /dashboard/*, Fase 4). Ver plano completo em
    # C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
    # =========================================================================

    def get_stimulus_fatigue_ratio_by_exercise(
        self,
        days: int = 90,
        user_id: int | None = None,
        muscle_group_id: int | None = None,
        activity_id: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Relacao Sigma(sets x stimulus_weight) / Sigma(sets x fatigue_weight)
        por exercicio (`mapping_id`) - reaproveita `_fetch_window_sets` +
        `_fetch_contributions_by_mapping` + `_resolve_contributions`, OS
        MESMOS blocos ja usados por `get_top_exercises_for_muscle_group`/
        `get_workout_sessions_timeline` (nunca duplica JOIN nem formula de
        peso), so agregando por `mapping_id` em vez de `muscle_group_id`/
        `activity_id`.

        Escopo (`activity_id` tem prioridade sobre `days`/`muscle_group_id`
        quando informado, mesmo padrao mutuamente exclusivo de
        `get_recent_exercise_volume`):
        - `activity_id`: restringe a series de UMA sessao especifica (audita
          o "custo" de fadiga daquele treino ja registrado) - usa o dataset
          COMPLETO (`_fetch_full_history_sets`, sem filtro de `days`) e
          filtra em Python, mesma logica de custo aceitavel ja documentada
          em `_fetch_full_history_sets` (dataset pessoal, pequeno).
        - sem `activity_id`: usa a janela `days` (`_fetch_window_sets`,
          mesma convencao de `get_top_exercises_for_muscle_group`), com
          `muscle_group_id` opcional para restringir a exercicios que
          contribuem para aquele grupo - uso principal: tabela
          Exercicio|Stimulus|Fatigue|Ratio do drill-down de
          `/dashboard/musculos` (`MuscleStatusClient.tsx`).

        Retorna, por exercicio: `sets_count`, `stimulus_equivalent_sets`,
        `fatigue_equivalent_sets`, `ratio` (`None` quando fatigue=0, para
        nunca dividir por zero), ordenado por `stimulus_equivalent_sets`
        decrescente (exercicios mais usados primeiro, nao pelo ratio - ratio
        alto com 1 unica serie e ruido estatistico, nao destaque).

        IMPORTANTE: metrica AUXILIAR, nunca "melhor exercicio" sozinha - um
        ratio alto pode significar tanto otimo custo-beneficio quanto
        `fatigue_weight` artificialmente baixo (exercicio raramente
        cadastrado com contribuicao de fadiga completa na tabela
        `exercise_muscle_contributions`). Sempre exibir ao lado de
        `sets_count`/`stimulus_equivalent_sets` brutos, nunca como criterio
        unico de escolha de exercicio."""
        if activity_id is not None:
            raw_sets = [
                row for row in self._fetch_full_history_sets(user_id=user_id)
                if row.get("activity_id") == activity_id
            ]
        else:
            raw_sets = self._fetch_window_sets(days, user_id=user_id)
        self._ensure_set_units(raw_sets)

        mapping_ids_all = {row["mapping_id"] for row in raw_sets if row.get("mapping_id")}
        contributions_by_mapping = self._fetch_contributions_by_mapping(mapping_ids_all)

        totals: dict[int, dict[str, Any]] = {}
        for row in raw_sets:
            mapping_id = row.get("mapping_id")
            if not mapping_id:
                # Sem mapping_id nao ha como resolver contribuicoes
                # explicitas nem calcular stimulus/fatigue - mesmo
                # comportamento de exclusao silenciosa de dados incompletos
                # ja usado por `get_muscle_activation_for_exercises`.
                continue
            contributions = self._resolve_contributions(
                mapping_id,
                contributions_by_mapping,
                row.get("primary_group"),
                row.get("secondary_group"),
                row.get("primary_group_id"),
                row.get("secondary_group_id"),
                row.get("secondary_participation"),
            )
            matched = (
                [c for c in contributions if c["muscle_group_id"] == muscle_group_id]
                if muscle_group_id is not None
                else contributions
            )
            if not matched:
                continue

            bucket = totals.setdefault(
                mapping_id,
                {"mapping_id": mapping_id, "sets_count": 0, "stimulus_equivalent_sets": 0.0, "fatigue_equivalent_sets": 0.0},
            )
            bucket["sets_count"] += 1
            units = float(row["set_units"])
            # M97 F4: estimulo com a serie de forca pelo peso do dono; a
            # fadiga segue cheia.
            stimulus_units = _hypertrophy_volume_units(units, row.get("training_goal"))
            for contrib in matched:
                bucket["stimulus_equivalent_sets"] += contrib["stimulus_weight"] * stimulus_units
                bucket["fatigue_equivalent_sets"] += contrib["fatigue_weight"] * units

        names_by_mapping: dict[int, str] = {}
        if totals:
            placeholders = ",".join(["%s"] * len(totals))
            name_rows = self.fetch_all(
                f"""
                SELECT ec.id, COALESCE(ec.name, (
                    SELECT exm.external_name
                    FROM exercise_external_mappings exm
                    WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                    ORDER BY exm.id ASC LIMIT 1
                ), 'Exercício sem nome') AS name
                FROM exercise_catalog ec
                WHERE ec.id IN ({placeholders})
                """,
                tuple(totals.keys()),
            )
            names_by_mapping = {row["id"]: row["name"] for row in name_rows}

        results: list[dict[str, Any]] = []
        for mapping_id, bucket in totals.items():
            stimulus = round(bucket["stimulus_equivalent_sets"], 2)
            fatigue = round(bucket["fatigue_equivalent_sets"], 2)
            results.append(
                {
                    "mapping_id": mapping_id,
                    "exercise_name": names_by_mapping.get(mapping_id, "Exercício não mapeado"),
                    "sets_count": bucket["sets_count"],
                    "stimulus_equivalent_sets": stimulus,
                    "fatigue_equivalent_sets": fatigue,
                    "ratio": round(stimulus / fatigue, 3) if fatigue else None,
                }
            )
        results.sort(key=lambda r: r["stimulus_equivalent_sets"], reverse=True)
        return results[:limit]

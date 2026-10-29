"""Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2) — Fase 2: planner
determinístico de orçamento de volume.

Monta `TemplateCycleContext` (Fase 1) a partir do banco (template completo +
metas do `training_type` + recuperação projetada) e calcula, para um slot
específico, o orçamento nominal/efetivo de cada grupo muscular presente nele
(`MuscleSlotBudget`). Nenhuma decisão de exercício acontece aqui — só
matemática determinística (seções 3, 4, 12, 13, 18, 30 do documento de
especificação).

Reaproveita por IMPORT DIRETO (nunca duplica a fórmula):
- `project_recovery_for_date` (`workout_plan_ai.py`) +
  `MuscleRecoveryService.get_recovery_overview` para a recuperação bruta —
  mesma fonte usada pelo motor v1/ABC.
- `MuscleRecoveryService.volume_multiplier_for(status)` (regra única da dose
  reduzida, M76 B2) como multiplicador de dose por status.
- `training_types.get_training_type_muscle_group_targets` para as metas
  semanais.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from .template_volume_domain import (
    OVER_ALLOCATION_THRESHOLD_PCT,
    UNDER_ALLOCATION_THRESHOLD_PCT,
    MuscleSlotBudget,
    SlotMuscleAllocation,
    TemplateCycleContext,
    WeeklyMuscleTarget,
)


class TemplateVolumeEngineNotApplicable(Exception):
    """Levantada quando o template não tem `training_type_id` — o motor v2
    não tem meta semanal para calcular orçamento algum; o chamador
    (`workout_plans.py`, sob a flag) deve cair no engine v1 legado neste
    caso, nunca propagar 500."""


def _load_muscle_group_names(muscle_group_ids: set[int]) -> dict[int, str]:
    if not muscle_group_ids:
        return {}
    from ..db import transaction

    with transaction() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(muscle_group_ids))
            cur.execute(
                f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})",
                tuple(muscle_group_ids),
            )
            return {row["id"]: row["name"] for row in cur.fetchall()}


def _load_projected_recovery(
    plan_date: Optional[str], plan_time: Optional[str] = None, user_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """Recuperação projetada para `plan_date` — mesma fonte de dados do motor
    v1 (`gather_ficha_context`/`project_recovery_for_date`), sem os demais
    campos pesados (histórico, catálogo, gym) que `gather_ficha_context`
    também carregaria e que o planner de volume não usa.

    `plan_time` repassado adiante — ver docstring de `project_recovery_for_date`
    (sem isso, a projeção sempre assumia meia-noite de `plan_date`).

    PR-09 (G26a): antes buscava `/analytics/muscle-recovery` via
    `_internal_get`, que autentica SEMPRE com o token mestre — resolvido em
    `security.resolve_identity` como o usuário admin, independente de quem
    pediu a ficha. O motor v2 de qualquer usuário usava a recuperação do
    admin. Agora chama o serviço diretamente filtrando por `user_id` (mesma
    correção já aplicada em `gather_ficha_context`).

    SEG-04 (2026-10-09): o caminho sem `user_id` (que ainda caía no self-call
    HTTP com o token mestre, ou seja, na recuperação do admin) foi removido.
    Todo chamador real passa o `user_id` de quem pediu a ficha (o handler da
    `musclefit-plan-generator` recebe o `user_id` do evento); sem ele a
    geração falha de forma explícita, como já faz o Modo A."""
    from ..workout_plan_ai import WorkoutPlanGenerationError, project_recovery_for_date

    if user_id is None:
        raise WorkoutPlanGenerationError(
            "Geração a partir do modelo exige um usuário: a recuperação muscular projetada é a dele."
        )

    from .muscle_recovery_service import MuscleRecoveryService

    # Mesmo "agora" (hora local do usuario) no retrato e na projecao.
    service = MuscleRecoveryService()
    now_local = service.user_now(user_id)
    # Janela e limite: os do modelo (M76 B3), os mesmos da tela e do Modo B.
    raw_items = service.get_recovery_overview(user_id=user_id, now_local=now_local)
    return project_recovery_for_date(raw_items, plan_date, plan_time, user_id=user_id, now=now_local)


def _recovery_state_from_projection(projected_items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    from .muscle_recovery_service import MuscleRecoveryService

    state: dict[int, dict[str, Any]] = {}
    for item in projected_items:
        group_id = item.get("muscle_group_id")
        if group_id is None:
            continue
        status = item.get("projected_readiness_status") or "READY"
        if status not in MuscleRecoveryService._RECOMMENDATIONS:
            status = "READY"
        state[group_id] = {
            "status": status,
            # Mesmo multiplicador da regra única de dose reduzida (M76 B2).
            "multiplier": MuscleRecoveryService.volume_multiplier_for(status),
        }
    return state


def build_template_cycle_context(
    template: dict[str, Any],
    user_id: Optional[int] = None,
    gym_id: Optional[int] = None,
    plan_date: Optional[str] = None,
    plan_time: Optional[str] = None,
) -> TemplateCycleContext:
    """Monta o contexto do CICLO INTEIRO (seção 6) — não só do slot que será
    gerado agora. `user_id`/`gym_id` aceitos por assinatura (mesmo contrato
    do documento, seção 8: "qualquer resolução de slot deve considerar
    user_id/template_id/gym_id") mas não usados na Fase 2 — a recuperação já
    é isolada por usuário na origem (`_load_projected_recovery`, filtrada
    pelo `user_id`); reservado para uma Fase futura que
    precise diferenciar histórico por academia.

    Levanta `TemplateVolumeEngineNotApplicable` se o template não tiver
    `training_type_id` — o chamador deve tratar isso como "usar o engine v1
    legado", nunca como erro do usuário."""
    training_type_id = template.get("training_type_id")
    if not training_type_id:
        raise TemplateVolumeEngineNotApplicable(
            f"Template {template.get('id')} não tem training_type_id — motor v2 não aplicável."
        )

    from ..training_types import get_training_type_muscle_group_targets

    raw_targets = get_training_type_muscle_group_targets(training_type_id)

    muscle_group_ids: set[int] = set(raw_targets.keys())
    slots = sorted(template.get("slots") or [], key=lambda s: s["sequence"])
    for slot in slots:
        for item in slot.get("items") or []:
            if item.get("item_kind") == "muscle_group" and item.get("muscle_group_id"):
                muscle_group_ids.add(item["muscle_group_id"])

    names_by_id = _load_muscle_group_names(muscle_group_ids)

    muscle_targets: dict[int, WeeklyMuscleTarget] = {}
    for group_id, values in raw_targets.items():
        name = names_by_id.get(group_id)
        if not name:
            continue
        muscle_targets[group_id] = WeeklyMuscleTarget(
            muscle_group_id=group_id,
            muscle_group_name=name,
            weekly_min=values["min"],
            weekly_target=values["target"],
            weekly_max=values["max"],
        )

    muscle_slot_allocations: dict[int, list[SlotMuscleAllocation]] = {}
    for slot in slots:
        for item in sorted(slot.get("items") or [], key=lambda i: i.get("item_order") or 0):
            if item.get("item_kind") != "muscle_group" or not item.get("muscle_group_id"):
                continue
            group_id = item["muscle_group_id"]
            allocation = SlotMuscleAllocation(
                slot_id=slot["id"],
                slot_sequence=slot["sequence"],
                slot_name=slot["name"],
                muscle_group_id=group_id,
                priority_min_pct=item.get("priority_min_pct"),
                priority_max_pct=item.get("priority_max_pct"),
                reps_target=item.get("reps_target"),
                rpe_target=item.get("rpe_target"),
                prioritize=bool(item.get("prioritize")),
            )
            muscle_slot_allocations.setdefault(group_id, []).append(allocation)

    projected_recovery = _load_projected_recovery(plan_date, plan_time, user_id=user_id)
    recovery_state = _recovery_state_from_projection(projected_recovery)

    return TemplateCycleContext(
        template_id=template["id"],
        training_type_id=training_type_id,
        slots=slots,
        muscle_targets=muscle_targets,
        muscle_slot_allocations=muscle_slot_allocations,
        recovery_state=recovery_state,
    )


def _item_field(item: Any, name: str) -> Any:
    """Campo de um item do Modelo, seja dict (`_get_template`/geração) ou
    Pydantic (`WorkoutPlanTemplateSlotItemIn`, payload do preview)."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


@dataclass(frozen=True)
class MuscleItemBudget:
    """Projeção do orçamento de UM item `muscle_group` de um slot: a faixa %
    efetiva (configurada ou do fallback de divisão igual) e as séries que ela
    representa sobre a meta semanal (`weekly_target`) do tipo de treino.
    `index` é a posição do item na sequência recebida; sem meta para o grupo,
    `weekly_target`/`sets_min`/`sets_max` ficam `None`."""

    index: int
    item: Any
    muscle_group_id: int
    pct_min: float
    pct_max: float
    weekly_target: Optional[float]
    sets_min: Optional[float]
    sets_max: Optional[float]

    @property
    def has_target(self) -> bool:
        return self.weekly_target is not None

    @property
    def pct_min_fraction(self) -> float:
        return self.pct_min / 100.0

    @property
    def pct_max_fraction(self) -> float:
        return self.pct_max / 100.0

    @property
    def sets_mid(self) -> Optional[float]:
        if self.sets_min is None or self.sets_max is None:
            return None
        return (self.sets_min + self.sets_max) / 2.0


def project_muscle_item_budgets(
    items: Sequence[Any], weekly_targets: Mapping[int, float]
) -> list[MuscleItemBudget]:
    """Função PURA: faixa % e séries projetadas de cada item `muscle_group`
    (com `muscle_group_id`) de UM slot, na ordem recebida. Fonte única da
    regra usada por `build_template_volume_plan` (geração v2) e pelo preview
    de ativação do builder (`workout_plan_templates.preview_activation`).

    `weekly_targets`: `muscle_group_id -> weekly_target` do tipo de treino.
    Grupo fora do mapa = sem meta (`has_target=False`), mas o item continua
    contando na divisão do fallback.

    BUGFIX (achado com dados reais do template 17/slot 298): itens sem
    priority_min_pct/priority_max_pct configurados NÃO podem virar
    orçamento 0/0 — isso fazia `deviation_status` classificar como
    BALANCED silenciosamente (0 < 0 é False) e o grupo nunca recebia
    séries no reparo/prompt da IA, mesmo com meta semanal > 0. Em vez
    disso, distribuímos igualmente entre os itens não configurados o
    percentual do slot que sobrou depois de somar os itens já
    configurados (ex.: 1 item com 60-70%, 3 sem config → cada um fica
    com (100-70)/3 ≈ 10%). Guardrail com max(0, ...) cobre o caso dos
    itens configurados já somarem >=100% sozinhos."""
    muscle_items = [
        (index, item)
        for index, item in enumerate(items)
        if _item_field(item, "item_kind") == "muscle_group" and _item_field(item, "muscle_group_id")
    ]

    configured_min_sum = sum(
        _item_field(i, "priority_min_pct") for _, i in muscle_items if _item_field(i, "priority_min_pct") is not None
    )
    configured_max_sum = sum(
        _item_field(i, "priority_max_pct") for _, i in muscle_items if _item_field(i, "priority_max_pct") is not None
    )
    unconfigured_count = sum(
        1
        for _, i in muscle_items
        if _item_field(i, "priority_min_pct") is None or _item_field(i, "priority_max_pct") is None
    )
    fallback_min_pct = (
        max(0.0, 100.0 - configured_min_sum) / unconfigured_count if unconfigured_count else 0.0
    )
    fallback_max_pct = (
        max(0.0, 100.0 - configured_max_sum) / unconfigured_count if unconfigured_count else 0.0
    )

    projections: list[MuscleItemBudget] = []
    for index, item in muscle_items:
        group_id = _item_field(item, "muscle_group_id")
        min_pct = _item_field(item, "priority_min_pct")
        max_pct = _item_field(item, "priority_max_pct")
        if min_pct is None:
            min_pct = fallback_min_pct
        if max_pct is None:
            max_pct = fallback_max_pct

        weekly_target = weekly_targets.get(group_id)
        sets_min = sets_max = None
        if weekly_target is not None:
            # Mesma ordem de operações de sempre (fração primeiro) para o
            # resultado de ponto flutuante não mudar no planner.
            sets_min = weekly_target * (min_pct / 100.0)
            sets_max = weekly_target * (max_pct / 100.0)

        projections.append(
            MuscleItemBudget(
                index=index,
                item=item,
                muscle_group_id=group_id,
                pct_min=min_pct,
                pct_max=max_pct,
                weekly_target=weekly_target,
                sets_min=sets_min,
                sets_max=sets_max,
            )
        )
    return projections


def build_template_volume_plan(context: TemplateCycleContext, slot_id: int) -> list[MuscleSlotBudget]:
    """Orçamento (nominal + efetivo pós-recuperação) de cada grupo muscular
    presente no slot `slot_id`. Só `weekly_target` alimenta o orçamento
    nominal (seção 13 — `weekly_min`/`weekly_max` ficam como guardrail
    global, não multiplicados pelo percentual do slot)."""
    slot = context.slot_by_id(slot_id)
    if slot is None:
        return []

    slot_items = sorted(slot.get("items") or [], key=lambda i: i.get("item_order") or 0)
    weekly_targets = {group_id: target.weekly_target for group_id, target in context.muscle_targets.items()}

    budgets: list[MuscleSlotBudget] = []
    for projection in project_muscle_item_budgets(slot_items, weekly_targets):
        if not projection.has_target:
            # Grupo presente no slot mas sem meta semanal cadastrada no
            # training_type — não há base para orçamento; o chamador
            # (validator, Fase 5) deve tratar isso como grupo sem
            # orçamento algum (0/0), nunca inventar um alvo.
            continue
        item = projection.item
        group_id = projection.muscle_group_id
        target = context.muscle_targets[group_id]
        budgets.append(
            MuscleSlotBudget(
                muscle_group_id=group_id,
                muscle_group_name=target.muscle_group_name,
                weekly_min=target.weekly_min,
                weekly_target=target.weekly_target,
                weekly_max=target.weekly_max,
                slot_pct_min=projection.pct_min_fraction,
                slot_pct_max=projection.pct_max_fraction,
                nominal_slot_min=projection.sets_min,
                nominal_slot_max=projection.sets_max,
                recovery_status=context.recovery_status_for(group_id),
                recovery_multiplier=context.recovery_multiplier_for(group_id),
                reps_target=item.get("reps_target"),
                rpe_target=item.get("rpe_target"),
                prioritize=bool(item.get("prioritize")),
            )
        )

    return budgets


def validate_template_volume_coverage(template: dict[str, Any]) -> list[dict[str, Any]]:
    """Seção 11: para cada grupo muscular presente em algum slot com faixa
    configurada, soma `priority_min_pct`/`priority_max_pct` em todos os
    slots onde aparece e classifica UNDER_ALLOCATED/BALANCED/OVER_ALLOCATED
    contra os thresholds 90%/110%. Não bloqueia cadastro — só warning
    (chamado por `workout_plan_templates.py` no POST/PUT, engolindo
    qualquer exceção). Retorna lista vazia se o template não tiver
    `training_type_id` (nada para validar sem meta semanal)."""
    training_type_id = template.get("training_type_id")
    if not training_type_id:
        return []

    from ..training_types import get_training_type_muscle_group_targets

    raw_targets = get_training_type_muscle_group_targets(training_type_id)

    sums: dict[int, dict[str, float]] = {}
    for slot in template.get("slots") or []:
        for item in slot.get("items") or []:
            if item.get("item_kind") != "muscle_group" or not item.get("muscle_group_id"):
                continue
            min_pct = item.get("priority_min_pct")
            max_pct = item.get("priority_max_pct")
            if min_pct is None or max_pct is None:
                continue
            group_id = item["muscle_group_id"]
            bucket = sums.setdefault(group_id, {"min": 0.0, "max": 0.0})
            bucket["min"] += min_pct
            bucket["max"] += max_pct

    if not sums:
        return []

    names_by_id = _load_muscle_group_names(set(sums.keys()))

    warnings: list[dict[str, Any]] = []
    for group_id, bucket in sums.items():
        if group_id not in raw_targets:
            # Grupo configurado no template mas sem meta no training_type —
            # não há como validar percentual de um alvo inexistente.
            continue
        sum_min, sum_max = bucket["min"], bucket["max"]
        if sum_max < UNDER_ALLOCATION_THRESHOLD_PCT:
            status = "UNDER_ALLOCATED"
        elif sum_min > OVER_ALLOCATION_THRESHOLD_PCT:
            status = "OVER_ALLOCATED"
        else:
            status = "BALANCED"
        warnings.append(
            {
                "muscle_group_id": group_id,
                "muscle_group_name": names_by_id.get(group_id, str(group_id)),
                "sum_pct_min": sum_min,
                "sum_pct_max": sum_max,
                "status": status,
            }
        )

    return warnings

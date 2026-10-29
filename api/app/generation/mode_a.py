"""Modo A DETERMINISTICO - `volume_mode='percent_budget'` (PR-13;
PLANO_20260912.md 6.1, 4, 5.8 e decisoes D6, D7).

Antes deste PR o Modo A era o motor v2 com LLM: um prompt recebia a matriz de
candidatos e o orcamento e DEVOLVIA a ficha, que depois passava por
reparo/split/piso para consertar a matematica. Aquele arquivo virou
`generation/mode_a_legacy.py`, intocado, e so roda quando
`GENERATION_ENGINE=legacy` (o default do codigo quando a env falta; producao
roda `deterministic` + `PROGRESSION_ENGINE=v2`, ver `generation/__init__.py` e
`infra/lambda_motores_deterministicos.tf`).
Aqui nao ha IA, nao ha RAG e nao ha reparo: os nove passos da secao 6.1
decidem tudo, e a mesma entrada produz a mesma ficha.

Os nove passos, e onde cada um mora:

| passo | o que decide | onde |
|---|---|---|
| 1 | janela movel de 7 dias (realizado + planejado) | `activation_window.py` |
| 2 | `alvo_slot` por grupo | `activation_window.build_group_budgets` |
| 3 | candidatos do grupo | `candidate_exercise_provider.build_ranked_candidate_matrix` |
| 4 | filtro de SEGURANCA (exclui) | `_filter_candidates` |
| 5 | ranking (ordena o que sobrou) | `_rank_key` + `domain/equipment_ranking.py` |
| 6 | alocacao (quantos exercicios) | `_allocate_group` |
| 7 | deficit reportado | `GroupBudget.deficit` + `volume_summary` |
| 8 | ordenacao por fadiga | `template_volume_validator.order_exercises_by_fatigue` |
| 9 | prescricao congelada + auditoria | `deterministic_prescription` + `generation_audit` |

Tres coisas que este motor NAO faz, de proposito:

1. **Nao decide series pelo orcamento.** Series sao da PROGRESSAO (D6); o
   orcamento de ativacao decide quantos EXERCICIOS o grupo recebe. A unica
   excecao e a reducao para nao estourar o teto de um grupo secundario
   (passo 6), que e limite, nao prescricao.
2. **Nao corrige subativacao com tecnica nem com pico** (D7): o que sobra e
   reportado como `deficit`, na auditoria e na UI.
3. **Nao deixa preferencia passar na frente da seguranca.** O passo 4 roda
   ANTES do ranking: a seguranca FILTRA, a preferencia so ORDENA o que
   sobrou. Um equipamento 5 estrelas incompativel com a tecnica exigida pela
   progressao perde para um 1 estrela compativel - e o motivo fica na
   auditoria.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from ..domain import cardio_prescription
from ..domain import equipment_ranking as ranking
from ..domain import training_safety as ts
from ..domain.training_goal import goal_by_group, resolved_goal_value
from ..domain.progression_engine import APPLY_TECHNIQUE, SKIP, track_technique_in_force
from .activation_window import (
    GroupBudget,
    build_group_budgets,
    planned_activation,
    projected_slot_ids,
    realized_activation,
    resolve_window,
)
from .deterministic_prescription import (
    MODE_A_ENGINE_VERSION,
    PairPrescription,
    PairRequest,
    prescribe_pairs,
    readiness_by_group,
)
from .fallback_log import log_generation_fallbacks
from ..template_versions import current_version_id_of
from ..domain.prescription_defaults import (
    DEFAULT_MAX_REST_SECONDS,
    DEFAULT_MIN_REST_SECONDS,
    goal_adjustments_text,
    item_reps_target,
    item_rest_seconds,
    normalize_item_for_goal,
)
from ..periodization.limits import MODE_A_MAX_EXERCISES_PER_GROUP

logger = logging.getLogger("fitness.generation")

ENGINE_VERSION = MODE_A_ENGINE_VERSION

# Passo 6: "Maximo de N exercicios por grupo" - dono em `periodization/limits.py`
# (M76, Lote 4c, E9).
MAX_EXERCISES_PER_GROUP = MODE_A_MAX_EXERCISES_PER_GROUP

# `excluded[].reason` do passo 4 (os dois primeiros sao os mesmos codigos do
# recomendador, `domain/equipment_ranking.py` - um codigo, um significado).
REASON_TECHNIQUE_INCOMPATIBLE = ranking.TECHNIQUE_INCOMPATIBLE
REASON_FAILURE_REQUIRED_NOT_ALLOWED = ranking.FAILURE_REQUIRED_NOT_ALLOWED
REASON_UNAVAILABLE = "UNAVAILABLE"
# Motivos de recusa do passo 6 (nao sao seguranca: sao orcamento).
REASON_SECONDARY_BUDGET_EXCEEDED = "SECONDARY_BUDGET_EXCEEDED"
REASON_NO_DIRECT_WORK = "NO_DIRECT_WORK"

# `fallbacks[]` da auditoria (secao 14). O motor nunca "inventa" saida: quando
# nao da, ele registra por que nao deu.
FALLBACK_NO_EQUIPMENT_FOR_GROUP = "NO_EQUIPMENT_FOR_GROUP"
FALLBACK_ALL_CANDIDATES_EXCLUDED = "ALL_CANDIDATES_EXCLUDED"
FALLBACK_DEFICIT = "ACTIVATION_DEFICIT"
FALLBACK_SETS_REDUCED = "SETS_REDUCED_FOR_SECONDARY_BUDGET"


# ---------------------------------------------------------------------------
# Estruturas internas
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """Um par exercicio x equipamento da academia, ja com o que o passo 4/5 le."""

    exercise_id: int
    equipment_id: Optional[int]
    gym_equipment_id: Optional[int]
    exercise_name: Optional[str]
    amazfit_name: Optional[str]
    equipment_name: Optional[str]
    stars: Optional[int]
    last_used_by_user_at: Optional[str]
    stimulus: dict[int, float] = field(default_factory=dict)
    fatigue: dict[int, float] = field(default_factory=dict)
    is_compound: bool = False

    @property
    def key(self) -> tuple[int, Optional[int]]:
        return (self.exercise_id, self.equipment_id)

    def ranking_item(self) -> dict[str, Any]:
        """Item no formato que `domain/equipment_ranking.ranking_key` le."""
        return {
            "user_preference_stars": self.stars,
            "last_used_by_user_at": self.last_used_by_user_at,
            "equipment_name": self.equipment_name,
            "id": self.gym_equipment_id or 0,
        }

    def audit_entry(self, **extra: Any) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "exercise_name": self.exercise_name,
            "equipment_id": self.equipment_id,
            "equipment_name": self.equipment_name,
            "gym_equipment_id": self.gym_equipment_id,
            "stars": ranking.effective_stars(self.stars),
            "last_used": self.last_used_by_user_at,
            **extra,
        }


def _candidates_from_matrix(
    matrix: Mapping[int, Sequence[Mapping[str, Any]]],
    is_compound_by_exercise: Mapping[int, bool],
) -> dict[int, list[Candidate]]:
    """Matriz crua -> `Candidate` por grupo, na ordem em que veio (o ranking do
    passo 5 reordena depois; nunca dependemos da ordem de um dict)."""
    out: dict[int, list[Candidate]] = {}
    for group_id in sorted(matrix):
        candidates: list[Candidate] = []
        for raw in matrix[group_id]:
            exercise_id = raw.get("mapping_id")
            if not exercise_id:
                continue
            stimulus: dict[int, float] = {}
            fatigue: dict[int, float] = {}
            for contribution in raw.get("muscle_contributions") or ():
                contribution_group = contribution.get("muscle_group_id")
                if contribution_group is None:
                    continue
                contribution_group = int(contribution_group)
                stimulus[contribution_group] = float(contribution.get("stimulus_weight") or 0.0)
                fatigue[contribution_group] = float(contribution.get("fatigue_weight") or 0.0)
            candidates.append(
                Candidate(
                    exercise_id=int(exercise_id),
                    equipment_id=int(raw["equipment_id"]) if raw.get("equipment_id") else None,
                    gym_equipment_id=int(raw["gym_equipment_id"]) if raw.get("gym_equipment_id") else None,
                    exercise_name=raw.get("exercise_name"),
                    amazfit_name=raw.get("amazfit_name"),
                    equipment_name=raw.get("equipment_name"),
                    stars=raw.get("user_preference_stars"),
                    last_used_by_user_at=raw.get("last_used_by_user_at"),
                    stimulus=stimulus,
                    fatigue=fatigue,
                    is_compound=bool(is_compound_by_exercise.get(int(exercise_id))),
                )
            )
        out[int(group_id)] = candidates
    return out


# ---------------------------------------------------------------------------
# Passo 4 - seguranca FILTRA (antes do ranking)
# ---------------------------------------------------------------------------


def required_technique_for_group(group_state: Any, payload_key: str = "technique_code") -> Optional[str]:
    """Tecnica EXIGIDA pela progressao para este grupo, ou `None`.

    Ela existe quando o grupo tem `pending_action=APPLY_TECHNIQUE` (F4) com a
    tecnica ja escolhida no `pending_payload`. Sem acao pendente de tecnica nao
    ha exigencia nenhuma e o filtro do passo 4 nao exclui ninguem por tecnica -
    coerente com a D3, em que a seguranca so filtra quando alguem pede
    explicitamente uma tecnica.

    A exigencia vale para o GRUPO (D2: a fase e do grupo), e nao so para a
    trilha alvo do payload: o exercicio que vai receber a tecnica sai desta
    selecao, e escolher um par incapaz de executa-la so para descobrir isso
    depois seria escolher errado de proposito.
    """
    if group_state is None or getattr(group_state, "pending_action", None) != APPLY_TECHNIQUE:
        return None
    payload = getattr(group_state, "pending_payload", None) or {}
    code = payload.get(payload_key)
    return str(code) if code else None


def _filter_candidates(
    candidates: Sequence[Candidate],
    safety_by_pair: Mapping[tuple[int, int], ts.TrainingSafety],
    *,
    required_technique: Optional[str],
    track_technique_by_pair: Mapping[tuple[int, int], Optional[str]],
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Passo 4. Devolve `(elegiveis, excluidos)`; `excluidos` ja no formato da
    auditoria, com o `reason` que explica a exclusao.

    Tres exclusoes, na ordem da secao 6.1:

    - tecnica requerida incompativel com o par -> `TECHNIQUE_INCOMPATIBLE`;
    - tecnica que exige falha num par onde a falha e proibida ->
      `FAILURE_REQUIRED_NOT_ALLOWED`;
    - par sem equipamento resolvido/ativo -> `UNAVAILABLE`.

    A tecnica conferida e a requerida pelo grupo OU a que a trilha daquele par
    ja usa - as duas precisam ser executaveis no par para ele ser elegivel.
    """
    eligible: list[Candidate] = []
    excluded: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.equipment_id is None or candidate.gym_equipment_id is None:
            excluded.append(candidate.audit_entry(selected=False, reason=REASON_UNAVAILABLE))
            continue
        safety = safety_by_pair.get((candidate.exercise_id, candidate.equipment_id))
        if safety is None:
            excluded.append(candidate.audit_entry(selected=False, reason=REASON_UNAVAILABLE))
            continue
        techniques = [
            technique
            for technique in (required_technique, track_technique_by_pair.get(candidate.key))
            if technique
        ]
        reason = next(
            (
                found
                for found in (ranking.exclusion_reason(safety, technique) for technique in techniques)
                if found is not None
            ),
            None,
        )
        if reason is not None:
            excluded.append(candidate.audit_entry(selected=False, reason=reason))
            continue
        eligible.append(candidate)
    return eligible, excluded


# ---------------------------------------------------------------------------
# Passo 5 - ranking deterministico
# ---------------------------------------------------------------------------


def _rank_key(candidate: Candidate, used_exercise_ids: frozenset[int]) -> tuple:
    """`(stars DESC, last_used ASC NULLS FIRST, exercise_already_in_session ASC,
    equipment.name ASC, gym_equipment.id ASC)`.

    Os quatro criterios que nao dependem da sessao vem de
    `domain/equipment_ranking.ranking_key` - a ordem do recomendador e do
    motor e a MESMA funcao, nunca duas definicoes. O criterio de sessao
    (`exercise_already_in_session`) entra no meio, como a secao 6.1 manda.
    """
    base = ranking.ranking_key(candidate.ranking_item())
    in_session = 1 if candidate.exercise_id in used_exercise_ids else 0
    return base[:3] + (in_session,) + base[3:]


def rank_candidates(candidates: Sequence[Candidate], used_exercise_ids: frozenset[int]) -> list[Candidate]:
    return sorted(candidates, key=lambda candidate: _rank_key(candidate, used_exercise_ids))


# ---------------------------------------------------------------------------
# Passo 6 - alocacao
# ---------------------------------------------------------------------------


@dataclass
class ChosenExercise:
    candidate: Candidate
    prescription: PairPrescription
    sets_count: int
    muscle_group_id: int
    sets_reduced_from: Optional[int] = None

    def activation(self, group_id: int) -> float:
        return self.sets_count * float(self.candidate.stimulus.get(group_id, 0.0))


def _headroom_violation(
    candidate: Candidate,
    sets_count: int,
    group_id: int,
    session_activation: Mapping[int, float],
    headroom_by_group: Mapping[int, float],
) -> Optional[int]:
    """`None` = cabe. Senao, o id do grupo SECUNDARIO que estouraria o
    `max_weekly` projetado da janela (passo 6).

    Só grupos secundarios: o grupo que esta sendo preenchido tem o proprio
    `alvo_slot` como limite (o `clamp` do passo 2 garante o piso do modelo),
    e o passo 6 para nele. Grupo sem meta semanal cadastrada no Tipo de
    Treinamento nao tem teto a estourar.
    """
    for other_group, weight in sorted(candidate.stimulus.items()):
        if other_group == group_id or weight <= 0:
            continue
        headroom = headroom_by_group.get(other_group)
        if headroom is None:
            # Grupo sem meta semanal no Tipo de Treinamento: nao ha teto.
            continue
        projected = session_activation.get(other_group, 0.0) + sets_count * weight
        if projected > headroom + 1e-9:
            return other_group
    return None


def _allocate_group(
    budget: GroupBudget,
    candidates: Sequence[Candidate],
    prescriptions: Mapping[tuple[int, Optional[int]], PairPrescription],
    *,
    session_activation: dict[int, float],
    used_exercise_ids: set[int],
    headroom_by_group: Mapping[int, float],
    selection_audit: list[dict[str, Any]],
) -> list[ChosenExercise]:
    """Passo 6 para UM grupo. Para quando a ativacao do grupo alcanca
    `target_min`, quando ja tem 3 exercicios, ou quando acabam os candidatos.

    Duas passadas, nesta ordem: primeiro tenta cada candidato com as series
    que a PROGRESSAO prescreveu; so se nenhum couber, tenta de novo reduzindo
    series (ate 1). Reduzir series e o ultimo recurso justamente porque series
    sao decisao da progressao, nao do orcamento.
    """
    chosen: list[ChosenExercise] = []
    if budget.is_excluded:
        return chosen

    rank = 0
    while (
        session_activation.get(budget.muscle_group_id, 0.0) < budget.target_min - 1e-9
        and len(chosen) < MAX_EXERCISES_PER_GROUP
    ):
        pick: Optional[ChosenExercise] = None
        for allow_reduction in (False, True):
            for candidate in rank_candidates(candidates, frozenset(used_exercise_ids)):
                if candidate.exercise_id in used_exercise_ids:
                    continue
                prescription = prescriptions.get(candidate.key)
                if prescription is None:
                    continue
                if prescription.decision.decision == SKIP:
                    # NO_DIRECT_WORK chega aqui pelo contrato de recuperacao
                    # (5.8): a trilha nao prescreve nada.
                    if not allow_reduction:
                        rank += 1
                        selection_audit.append(
                            candidate.audit_entry(rank=rank, selected=False, reason=REASON_NO_DIRECT_WORK)
                        )
                    continue
                prescribed_sets = int(prescription.decision.suggested_sets or 1)
                if allow_reduction and prescribed_sets <= 1:
                    # Nao ha o que reduzir: 1 serie ja e o piso. A recusa deste
                    # candidato ja foi registrada na primeira passada.
                    continue
                sets_range = (
                    range(prescribed_sets - 1, 0, -1)
                    if allow_reduction
                    else range(prescribed_sets, prescribed_sets - 1, -1)
                )
                violation: Optional[int] = None
                for sets_count in sets_range:
                    violation = _headroom_violation(
                        candidate,
                        sets_count,
                        budget.muscle_group_id,
                        session_activation,
                        headroom_by_group,
                    )
                    if violation is None:
                        pick = ChosenExercise(
                            candidate=candidate,
                            prescription=prescription,
                            sets_count=sets_count,
                            muscle_group_id=budget.muscle_group_id,
                            sets_reduced_from=prescribed_sets if sets_count != prescribed_sets else None,
                        )
                        break
                if pick is not None:
                    break
                rank += 1
                selection_audit.append(
                    candidate.audit_entry(
                        rank=rank,
                        selected=False,
                        reason=REASON_SECONDARY_BUDGET_EXCEEDED,
                        blocking_muscle_group_id=violation,
                    )
                )
            if pick is not None:
                break
        if pick is None:
            break

        rank += 1
        selection_audit.append(
            pick.candidate.audit_entry(
                rank=rank,
                selected=True,
                reason=None,
                sets_count=pick.sets_count,
                sets_prescribed_by_progression=int(pick.prescription.decision.suggested_sets or 0),
            )
        )
        chosen.append(pick)
        used_exercise_ids.add(pick.candidate.exercise_id)
        for group_id, weight in pick.candidate.stimulus.items():
            if weight <= 0:
                continue
            session_activation[group_id] = session_activation.get(group_id, 0.0) + pick.sets_count * weight
    return chosen


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


def _slot_date(plan_date: Optional[str], today: date) -> date:
    """Dia do slot: `plan_date` ou, sem ele, o "hoje" do usuario (M76 Lote 2b;
    era `date.today()`, o dia UTC da Lambda)."""
    if plan_date:
        try:
            return date.fromisoformat(plan_date)
        except ValueError:
            pass
    return today


def _user_today(user_id: int) -> date:
    """"Hoje" do usuario no fuso do perfil (`time_windows`, dono)."""
    from ..db import transaction
    from ..intelligence.dashboard.time_windows import user_today_for

    with transaction() as conn:
        return user_today_for(conn, user_id)


def generate_plan_draft_from_template_a(
    template: dict[str, Any],
    slot: dict[str, Any],
    gym_id: Optional[int],
    gym_name: Optional[str],
    plan_date: Optional[str],
    user_id: Optional[int] = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
    plan_time: Optional[str] = None,
) -> dict[str, Any]:
    """Rascunho do Modo A. Mesmo contrato de dict do motor v2 (o restante do
    pipeline de `POST /workout-plans/generate` nao muda), com `llm_used=false`
    e a auditoria da secao 14.

    Com a flag desligada, delega ao motor legado - `GENERATION_ENGINE=legacy`
    (default do codigo; producao roda `deterministic`) e byte-a-byte o
    comportamento anterior.
    """
    from . import deterministic_enabled

    if not deterministic_enabled():
        from .mode_a_legacy import generate_plan_draft_from_template_v2 as legacy

        return legacy(
            template=template,
            slot=slot,
            gym_id=gym_id,
            gym_name=gym_name,
            plan_date=plan_date,
            user_id=user_id,
            min_rest_seconds=min_rest_seconds,
            max_rest_seconds=max_rest_seconds,
            plan_time=plan_time,
        )

    from .. import equipment_recommender, progression_state as ps
    from ..db import transaction
    from ..intelligence.candidate_exercise_provider import build_ranked_candidate_matrix
    from ..intelligence.template_volume_domain import GenerationVolumeSummary
    from ..intelligence.template_volume_planner import (
        build_template_cycle_context,
        build_template_volume_plan,
    )
    from ..intelligence.template_volume_validator import order_exercises_by_fatigue
    from ..workout_plan_ai import (
        WorkoutPlanGenerationError,
        assign_warmup_flags,
        gather_ficha_context,
        normalize_load_target,
        project_recovery_for_date,
        reconcile_with_gym_catalog,
        resolve_gym_name,
    )
    from ..workout_plan_templates_engine import _empty_rest_day_draft

    if slot.get("slot_type") == "descanso":
        return _empty_rest_day_draft(template, slot, gym_id, gym_name, plan_date)


    if user_id is None:
        # O motor deterministico e por usuario de ponta a ponta: trilhas,
        # estrelas, recencia e recuperacao. Sem `user_id` ele nao prescreveria
        # nada e devolveria uma ficha VAZIA - erro explicito em vez disso.
        raise WorkoutPlanGenerationError(
            "Geração determinística do modo 'percent_budget' exige um usuário: a prescrição vem das "
            "trilhas de progressão, das preferências e da recuperação dele."
        )

    try:
        # ---- passo 1/2: janela e orcamento -----------------------------
        context = build_template_cycle_context(
            template, user_id=user_id, gym_id=gym_id, plan_date=plan_date, plan_time=plan_time
        )
        slot_budgets = build_template_volume_plan(context, slot["id"])
        if not slot_budgets:
            raise WorkoutPlanGenerationError(
                "Nenhum grupo muscular com meta semanal disponível para montar esta ficha a partir do modelo "
                "(slot sem itens de grupo muscular, ou grupos sem meta cadastrada no Tipo de Treinamento)."
            )

        names_by_id = {
            **{group_id: target.muscle_group_name for group_id, target in context.muscle_targets.items()},
            **{budget.muscle_group_id: budget.muscle_group_name for budget in slot_budgets},
        }
        today = _user_today(int(user_id))
        slot_date = _slot_date(plan_date, today)
        window = resolve_window(slot_date, len(context.slots))
        realized = realized_activation(user_id, window, names_by_id, today=today)
        projected = projected_slot_ids(context, slot, slot_date, today=today)
        planned = planned_activation(context, projected)
        group_budgets = build_group_budgets(slot_budgets, window, realized, planned)
        budgets_by_group = {budget.muscle_group_id: budget for budget in group_budgets}

        # Teto projetado de TODO grupo com meta semanal - inclusive os que nao
        # estao neste slot, porque e justamente o trabalho secundario deles que
        # o passo 6 tem de respeitar.
        headroom_by_group = {
            group_id: max(
                0.0,
                target.weekly_max - float(realized.get(group_id, 0.0)) - float(planned.get(group_id, 0.0)),
            )
            for group_id, target in context.muscle_targets.items()
        }

        excluded_due_to_recovery = [
            {
                "muscle_primary": budget.muscle_group_name,
                "muscle_group_id": budget.muscle_group_id,
                "reason": "NO_DIRECT_WORK",
            }
            for budget in group_budgets
            if budget.recovery_status == "NO_DIRECT_WORK"
        ]
        active_budgets = [budget for budget in group_budgets if not budget.is_excluded]
        reduced_dose_groups = sorted(
            {budget.muscle_group_name for budget in active_budgets if budget.recovery_status == "REDUCED_DOSE"}
        )
        if not active_budgets:
            raise WorkoutPlanGenerationError(
                "Todos os grupos musculares deste slot estão indisponíveis por recuperação insuficiente "
                "(NO_DIRECT_WORK)."
            )

        # ---- passo 3: candidatos ---------------------------------------
        ficha_context = gather_ficha_context(gym_id, user_id)
        gym_name = resolve_gym_name(gym_id, gym_name, ficha_context.get("gyms") or [])
        projected_recovery = project_recovery_for_date(
            ficha_context.get("muscle_recovery") or [], plan_date, plan_time, user_id=user_id
        )
        readiness = readiness_by_group(projected_recovery)

        recommendation: dict[str, Any] = {}
        if gym_id:
            with transaction() as conn:
                # Recomendador COMPLETO (com estrelas/recencia/seguranca), nao o
                # enxugado que o prompt do motor legado consumia.
                recommendation = equipment_recommender.recommend_all(conn, gym_id=gym_id, user_id=user_id)

        # Objetivo de treino (M97): o do item do grupo > Modelo > default, pelo
        # dono. Gravado na ficha (F0) e, desde a F1, o objetivo da fase e da
        # trilha que a progressao le para o grupo.
        goals_by_group = goal_by_group(slot.get("items") or [], template.get("training_goal"))
        template_goal = resolved_goal_value(None, template.get("training_goal"))

        with transaction() as conn:
            matrix = build_ranked_candidate_matrix(sorted(budgets_by_group), recommendation)
            exercise_ids_all: set[int] = {
                int(raw["mapping_id"]) for group in matrix.values() for raw in group if raw.get("mapping_id")
            }
            is_compound = ps.load_exercise_flags(conn, sorted(exercise_ids_all)) if exercise_ids_all else {}
            candidates_by_group = _candidates_from_matrix(matrix, is_compound)

            pairs = sorted(
                {
                    (candidate.exercise_id, candidate.equipment_id)
                    for candidates in candidates_by_group.values()
                    for candidate in candidates
                    if candidate.equipment_id is not None
                }
            )
            safety_by_pair = ts.resolve_training_safety_bulk(conn, pairs) if pairs else {}
            # M97 F1: estados de todos os objetivos; cada grupo le os do SEU
            # objetivo (o do item do grupo > Modelo > default, pelo dono).
            group_states = ps.load_all_group_states(conn, int(user_id))
            tracks = ps.load_tracks(conn, int(user_id))

            # ---- passo 9 (fonte): a progressao prescreve cada par --------
            requests = [
                PairRequest(
                    exercise_id=candidate.exercise_id,
                    equipment_id=candidate.equipment_id,
                    muscle_group_id=group_id,
                    sequence=index,
                    training_goal=goals_by_group.get(group_id, template_goal),
                )
                for index, (group_id, candidate) in enumerate(
                    (
                        (group_id, candidate)
                        for group_id in sorted(candidates_by_group)
                        for candidate in candidates_by_group[group_id]
                    ),
                    start=1,
                )
                if candidate.equipment_id is not None
            ]
            prescriptions = (
                prescribe_pairs(conn, int(user_id), requests, readiness=readiness, model_fixed_sets=False)
                if requests
                else {}
            )

        # so a tecnica que a progressao ainda prescreve (F4 efetivada); a que
        # foi apenas executada nao filtra candidato nenhum
        # Por PAR (as trilhas sao por par + contexto de fadiga; a tecnica da
        # F4 e do par em qualquer contexto): vale a de qualquer trilha do par.
        track_technique_by_pair: dict[tuple[int, int], Optional[str]] = {}
        for track in tracks.values():
            # so a trilha do objetivo do grupo dela neste slot (M97 F1)
            if track.training_goal != goals_by_group.get(track.muscle_group_id, template_goal):
                continue
            code = track_technique_in_force(track, group_states.get((track.muscle_group_id, track.training_goal)))
            if code or track.pair not in track_technique_by_pair:
                track_technique_by_pair[track.pair] = code or track_technique_by_pair.get(track.pair)

        # ---- passos 4/5/6 ----------------------------------------------
        session_activation: dict[int, float] = {}
        used_exercise_ids: set[int] = set()
        selection_audit: dict[int, list[dict[str, Any]]] = {}
        fallbacks: list[dict[str, Any]] = []
        chosen_by_group: dict[int, list[ChosenExercise]] = {}

        for budget in active_budgets:
            raw_candidates = candidates_by_group.get(budget.muscle_group_id, [])
            group_state = group_states.get(
                (budget.muscle_group_id, goals_by_group.get(budget.muscle_group_id, template_goal))
            )
            required_technique = required_technique_for_group(group_state)
            eligible, excluded = _filter_candidates(
                raw_candidates,
                safety_by_pair,
                required_technique=required_technique,
                track_technique_by_pair=track_technique_by_pair,
            )
            audit = list(excluded)
            selection_audit[budget.muscle_group_id] = audit
            if not raw_candidates:
                fallbacks.append(
                    {
                        "muscle_group_id": budget.muscle_group_id,
                        "muscle_group_name": budget.muscle_group_name,
                        "reason": FALLBACK_NO_EQUIPMENT_FOR_GROUP,
                    }
                )
                continue
            if not eligible:
                fallbacks.append(
                    {
                        "muscle_group_id": budget.muscle_group_id,
                        "muscle_group_name": budget.muscle_group_name,
                        "reason": FALLBACK_ALL_CANDIDATES_EXCLUDED,
                        "required_technique": required_technique,
                    }
                )
                continue
            chosen_by_group[budget.muscle_group_id] = _allocate_group(
                budget,
                eligible,
                prescriptions,
                session_activation=session_activation,
                used_exercise_ids=used_exercise_ids,
                headroom_by_group=headroom_by_group,
                selection_audit=audit,
            )

        # ---- passo 9 (saida) -------------------------------------------
        exercises: list[dict[str, Any]] = []
        safety_clamps: list[dict[str, Any]] = []
        goal_adjustments: list[dict[str, Any]] = []
        goal_adjustment_warnings: list[str] = []
        for budget in active_budgets:
            for chosen in chosen_by_group.get(budget.muscle_group_id, []):
                candidate = chosen.candidate
                decision = chosen.prescription.decision
                if chosen.sets_reduced_from is not None:
                    fallbacks.append(
                        {
                            "muscle_group_id": budget.muscle_group_id,
                            "exercise_id": candidate.exercise_id,
                            "reason": FALLBACK_SETS_REDUCED,
                            "from": chosen.sets_reduced_from,
                            "to": chosen.sets_count,
                        }
                    )
                for clamp in decision.evidence.get("safety_clamps") or ():
                    safety_clamps.append(
                        {"exercise_id": candidate.exercise_id, "equipment_id": candidate.equipment_id, **dict(clamp)}
                    )
                # M97 F2: descanso e alvo de reps default do OBJETIVO do grupo
                # (dono `prescription_defaults`; hipertrofia = os de antes).
                group_goal = goals_by_group.get(budget.muscle_group_id, template_goal)
                row = (
                    {
                        "exercise_name": candidate.exercise_name,
                        "amazfit_name": candidate.amazfit_name,
                        "muscle_primary": budget.muscle_group_name,
                        "muscle_group_id": budget.muscle_group_id,
                        "muscle_secondary": None,
                        "equipment_name": candidate.equipment_name,
                        "equipment_id": candidate.equipment_id,
                        "preferred_equipment_id": candidate.gym_equipment_id,
                        "mapping_id": candidate.exercise_id,
                        "primary_group_id": budget.muscle_group_id,
                        "is_compound": candidate.is_compound,
                        "muscle_fatigue": dict(candidate.fatigue),
                        "sets_count": chosen.sets_count,
                        "rest_seconds": item_rest_seconds(group_goal, min_rest_seconds, max_rest_seconds),
                        "reps_target": chosen.prescription.reps_target
                        or budget.reps_target
                        or item_reps_target(group_goal),
                        "load_target": normalize_load_target(chosen.prescription.suggested_load_kg),
                        "suggested_load_kg": chosen.prescription.suggested_load_kg,
                        "suggested_reps": decision.suggested_reps_max,
                        "rpe_target": chosen.prescription.rpe_target or budget.rpe_target,
                        "advanced_technique": chosen.prescription.technique,
                        "progression_note": decision.reason,
                        "progression": None,
                        "requires_user_choice": False,
                        "recovery_status": budget.recovery_status,
                        "warmup": False,
                        "substitution_options": [],
                        "notes": "",
                        "training_goal": group_goal,
                    }
                )
                # Fix M97 (2026-10-26): a mesma normalizacao ao objetivo do Modo
                # B (dono `prescription_defaults`); no Modo A a progressao ja
                # prescreve no perfil, entao so o fallback do grupo (reps/RPE do
                # Modelo) pode precisar de ajuste. Hipertrofia: o mesmo dict.
                row, row_adjustments = normalize_item_for_goal(row, group_goal)
                if row_adjustments:
                    goal_adjustment_warnings.append(goal_adjustments_text(candidate.exercise_name, row_adjustments))
                    goal_adjustments.append(
                        {
                            "exercise_id": candidate.exercise_id,
                            "training_goal": group_goal,
                            "adjustments": [adjustment.to_dict() for adjustment in row_adjustments],
                        }
                    )
                exercises.append(dict(row))

        # ---- passo 8: ordenacao por fadiga ------------------------------
        prioritized_group_ids = {budget.muscle_group_id for budget in active_budgets if budget.prioritize}
        exercises = order_exercises_by_fatigue(exercises, prioritized_group_ids)

        # Reconciliacao com o catalogo da academia. Aqui ela e, por construcao,
        # um NO-OP: todo exercicio saiu do proprio payload do recomendador, com
        # o nome canonico do catalogo e o equipamento que ela recomendaria. Ela
        # fica no caminho como GUARDA (se algum dia a origem divergir, a
        # divergencia aparece em `adjusted_due_to_catalog_mismatch` em vez de
        # ir para a ficha em silencio) e porque a secao 6.1 manda reaproveita-la.
        final_exercises, adjusted_due_to_catalog_mismatch, excluded_due_to_no_equipment = (
            reconcile_with_gym_catalog(exercises, recommendation or None)
        )
        assign_warmup_flags(final_exercises)
        for index, exercise in enumerate(final_exercises, start=1):
            exercise["sequence"] = index

        # ---- passo 7: deficit ------------------------------------------
        volume_summary = GenerationVolumeSummary()
        activation_audit: dict[int, dict[str, Any]] = {}
        insufficient: list[dict[str, Any]] = []
        for budget in group_budgets:
            result = session_activation.get(budget.muscle_group_id, 0.0)
            activation_audit[budget.muscle_group_id] = budget.as_dict(result)
            deficit = budget.deficit(result)
            volume_summary.add_entry(
                GenerationVolumeSummary.Entry(
                    muscle_group_id=budget.muscle_group_id,
                    muscle_group_name=budget.muscle_group_name,
                    weekly_target=budget.target_weekly,
                    slot_min=budget.target_min,
                    slot_max=budget.target_max,
                    planned_activation=result,
                    deviation_status=(
                        "UNDER_ALLOCATED"
                        if deficit > 0
                        else ("OVER_ALLOCATED" if result > budget.target_max + 1e-9 else "BALANCED")
                    ),
                    recovery_status=budget.recovery_status,
                    cycle_planned_total=budget.realized + budget.planned + result,
                    weekly_volume_deficit=deficit,
                    note=(
                        f"Janela de {window.days} dias ({window.start.isoformat()} a {window.end.isoformat()}): "
                        f"realizado {budget.realized:g}, planejado {budget.planned:g}, "
                        f"meta da janela {budget.target_weekly:g}."
                    ),
                )
            )
            if deficit > 0 and not budget.is_excluded:
                fallbacks.append(
                    {
                        "muscle_group_id": budget.muscle_group_id,
                        "muscle_group_name": budget.muscle_group_name,
                        "reason": FALLBACK_DEFICIT,
                        "deficit": deficit,
                    }
                )
                # Contrato de `insufficient_target_volume_groups` (o mesmo que os
                # motores legados sempre emitiram, consumido por
                # GenerationWarnings.tsx): `equivalent_sets_achieved` /
                # `equivalent_sets_target` / `exercises_added` sao
                # OBRIGATORIOS. `deficit_equivalent_sets` e o extra do motor
                # deterministico e vale exatamente `target - achieved`.
                insufficient.append(
                    {
                        "muscle_primary": budget.muscle_group_name,
                        "muscle_group_id": budget.muscle_group_id,
                        "equivalent_sets_achieved": round(result, 2),
                        "equivalent_sets_target": round(budget.target_min, 2),
                        "exercises_added": len(chosen_by_group.get(budget.muscle_group_id, [])),
                        "reason": "deficit_ativacao",
                        "deficit_equivalent_sets": deficit,
                    }
                )
                volume_summary.warnings.append(
                    f"{budget.muscle_group_name}: déficit de {deficit:g} séries equivalentes — o catálogo da "
                    "academia, o limite de 3 exercícios por grupo ou o teto de outro grupo não permitiram "
                    "alcançar o alvo do slot. Técnica avançada e pico de contração NÃO são usados para "
                    "compensar subativação (D7)."
                )
        volume_summary.warnings.extend(goal_adjustment_warnings)
        for group in excluded_due_to_recovery:
            volume_summary.warnings.append(
                f"{group['muscle_primary']}: excluído por recuperação (NO_DIRECT_WORK)."
            )

        # Secao 14: cada entrada de `fallbacks[]` e um caminho alternativo ao
        # pretendido (grupo sem candidato, todos excluidos, deficit de ativacao
        # ou reducao de series por orcamento). Ja ia para a auditoria do plano;
        # aqui vira tambem linha de log, para a metrica "esperado 0" existir sem
        # depender de consulta ao banco. Uma linha por entrada, nunca por
        # exercicio: o volume e o numero de grupos do slot.
        log_generation_fallbacks(
            fallbacks,
            user_id=user_id,
            template_id=template.get("id"),
            engine_version=ENGINE_VERSION,
            volume_mode="percent_budget",
        )

        logger.info(
            json.dumps(
                {
                    "event": "generation.completed",
                    "user_id": int(user_id),
                    "template_id": template.get("id"),
                    "engine_version": ENGINE_VERSION,
                    "volume_mode": "percent_budget",
                    "exercises": len(final_exercises),
                    "llm_used": False,
                },
                ensure_ascii=False,
            )
        )

        return {
            "name": f"{template.get('name')} — {slot.get('name')}",
            "plan_type": template.get("template_type") or "full_body",
            "goal": template.get("description"),
            "plan_date": plan_date,
            "gym_id": gym_id,
            "gym_name": gym_name,
            "template_id": template.get("id"),
            "template_slot_id": slot.get("id"),
            # 066: versao do Modelo que gerou o rascunho (congela no save).
            "template_version_id": current_version_id_of(template),
            "template_slot_sequence": slot.get("sequence"),
            "template_slot_targets": [
                {
                    "muscle_group_id": budget.muscle_group_id,
                    "muscle_group_name": budget.muscle_group_name,
                    "sets_count": round(budget.target, 2),
                    "effective_max_sets": round(budget.target_max, 2),
                }
                for budget in group_budgets
            ],
            "is_rest_day": False,
            # M98 C0: o cardio do slot chega ao rascunho (dono `cardio_prescription`).
            **cardio_prescription.draft_cardio(slot.get("items")),
            "exercises": final_exercises,
            "excluded_due_to_recovery": excluded_due_to_recovery,
            "excluded_due_to_duplicate": [],
            "adjusted_due_to_catalog_mismatch": adjusted_due_to_catalog_mismatch,
            "excluded_due_to_no_equipment": excluded_due_to_no_equipment,
            "reduced_dose_groups": reduced_dose_groups,
            "insufficient_target_volume_groups": insufficient,
            "generation_summary": (
                f"Ficha gerada pelo motor determinístico do modo 'percent_budget' a partir do modelo "
                f"'{template.get('name')}', slot '{slot.get('name')}'. Orçamento de ativação numa janela de "
                f"{window.days} dias (realizado + planejado do ciclo); seleção por segurança, estrelas e "
                f"recência; repetições, carga e séries pela progressão v2; ordenação por fadiga compartilhada. "
                f"Sem IA."
            ),
            "raw_payload": {
                "template_id": template.get("id"),
                "slot_id": slot.get("id"),
                "slot_sequence": slot.get("sequence"),
                "engine_version": ENGINE_VERSION,
                "used_ai": False,
                "repair_log": [],
                "volume_planning": volume_summary.as_dict(),
                # Auditoria da secao 14.
                "generation_audit": {
                    "template_id": template.get("id"),
                    "slot_sequence": slot.get("sequence"),
                    "volume_mode": "percent_budget",
                    "engine_version": ENGINE_VERSION,
                    "window": {
                        **window.as_dict(),
                        "projected_slots": [
                            {"date": day.isoformat(), "slot_id": slot_id} for day, slot_id in projected
                        ],
                    },
                    "activation": {str(key): value for key, value in sorted(activation_audit.items())},
                    "selection": {
                        str(key): value for key, value in sorted(selection_audit.items())
                    },
                    "safety_clamps": safety_clamps,
                    "substitutions": [],
                    "fallbacks": fallbacks,
                    "llm_used": False,
                    **({"goal_adjustments": goal_adjustments} if goal_adjustments else {}),
                },
            },
        }
    except WorkoutPlanGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - erro irrecuperavel vira 502 no chamador
        # SEG-12: a excecao interna (ex.: SQL do driver) vai para o log; a
        # mensagem vira o `detail` do 502 e o erro do job assincrono.
        logger.exception("Falha inesperada na geracao 'percent_budget'")
        raise WorkoutPlanGenerationError(
            "Falha ao gerar ficha via modo 'percent_budget' (motor determinístico)."
        ) from exc

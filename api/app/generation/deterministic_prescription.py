"""Ponte entre o motor de progressao v2 e a geracao de ficha (PR-12).

Pergunta que este modulo responde: **para este par exercicio x equipamento,
deste usuario, neste instante, o que a progressao v2 manda prescrever** - ja
composto com a seguranca do par e o contrato de recuperacao projetado.

Ele existe porque a mesma resposta e necessaria em DOIS momentos, e as duas
tem de bater exatamente:

1. na GERACAO do rascunho (`generation/mode_b.py`), que mostra reps/carga ao
   usuario antes de ele salvar;
2. no CONGELAMENTO (`services._freeze_plan_prescriptions`), que grava
   `workout_plan_exercises.prescription_json`.

Nada aqui e recalculado na leitura da ficha: a leitura continua sendo o
snapshot do PR-11.

Divisao de responsabilidades (secao 6.2 do plano), que este modulo NAO pode
violar:

- **o modelo manda** em grupo, exercicio, equipamento, `sets_count`,
  descanso, tecnica (fixa) e parametros, aquecimento, ajuste e pico;
- **a progressao manda** em reps e carga;
- `rpe_target` do modelo e **intencao**: passa por `training_safety.compose`,
  que so corta para baixo (clamp auditado), e nunca bloqueia a geracao;
- **series sao fixas pelo modelo**: a F5 vira recomendacao de edicao ao PT
  (`blocked_by=MODEL_FIXED_SETS`) e o grupo segue para F6 (D9).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from ..domain.prescription import WARNING_MODEL_TECHNIQUE_INVALID, parse_rpe
from ..domain.progression_engine import (
    DEFAULT_FATIGUE_CONTEXT,
    EVIDENCE_STRENGTH_E1RM_LOAD,
    READY_CONTRACT,
    ProgressionDecision,
    ReadinessContract,
    TrackState,
    empty_group_state,
    new_group_state,
    prescribe,
    resolve_track,
    uses_e1rm_load,
)
from ..domain.training_goal import goal_value
from ..observability import log_event
from ..domain.training_safety import TrainingSafety, build_training_safety, resolve_training_safety_bulk

# `engine_version` gravado no snapshot pelo Modo B deterministico. O contrato
# do snapshot (`domain/prescription.py`) nao muda - so a ORIGEM.
MODE_B_ENGINE_VERSION = "mode-b-deterministic-v1"

# `engine_version` do Modo A deterministico (PR-13), que usa as MESMAS funcoes
# puras deste modulo com uma diferenca de contrato: no Modo A as series vem da
# PROGRESSAO (`model_fixed_sets=False`), porque o modelo so declara grupos e
# percentuais - nao ha `sets_count` de modelo para respeitar.
MODE_A_ENGINE_VERSION = "mode-a-deterministic-v1"


@dataclass(frozen=True)
class PairRequest:
    """Um item do modelo ja resolvido contra a academia."""

    exercise_id: int
    equipment_id: Optional[int]  # `equipment.id`; None = par nao resolvido
    muscle_group_id: Optional[int]
    model_sets_count: Optional[int] = None
    model_technique: Optional[str] = None
    model_rpe_target: Optional[str] = None
    sequence: int = 0
    # Fase 5, PR 6: parametros da tecnica do Modelo, para
    # `advanced_technique_parameters` da decisao.
    model_technique_applications: Optional[int] = None
    model_technique_duration_seconds: Optional[int] = None
    # Contexto de fadiga previa da posicao PLANEJADA (`PriorFatigueBand.value`).
    # `None` = sem posicao planejada: vale a trilha executada mais
    # recentemente do par (`progression_engine.resolve_track`).
    # `prescribe_pairs(session_order=True)` preenche a partir da ordem dos
    # pedidos.
    fatigue_context: Optional[str] = None
    # M97 F1: objetivo de treino do item (VALOR do dono `domain/training_goal`;
    # `None` = default). A trilha e a fase lidas sao SO as desse objetivo.
    training_goal: Optional[str] = None

    @property
    def key(self) -> tuple[int, Optional[int]]:
        return (int(self.exercise_id), None if self.equipment_id is None else int(self.equipment_id))


@dataclass(frozen=True)
class PairPrescription:
    """Saida por item: a decisao + o que a ficha precisa exibir dela."""

    decision: ProgressionDecision
    safety: Optional[TrainingSafety]
    technique: Optional[str]
    warnings: tuple[str, ...] = ()

    @property
    def reps_target(self) -> Optional[str]:
        low, high = self.decision.suggested_reps_min, self.decision.suggested_reps_max
        if low is None or high is None:
            return None
        return f"{low}-{high}"

    @property
    def suggested_load_kg(self) -> Optional[float]:
        return self.decision.suggested_load_kg

    @property
    def rpe_target(self) -> Optional[str]:
        target = self.decision.effective_rpe_target
        return None if target is None else str(target)

    @property
    def strength_load(self) -> Optional[dict[str, Any]]:
        """Faixa de carga por %1RM da decisao (forca com e1RM; senao None)."""
        value = (self.decision.evidence or {}).get(EVIDENCE_STRENGTH_E1RM_LOAD)
        return dict(value) if value else None


def readiness_by_group(projected_recovery: Sequence[Mapping[str, Any]]) -> dict[int, ReadinessContract]:
    """Contrato de recuperacao por grupo, a partir da projecao ja calculada
    por `workout_plan_ai.project_recovery_for_date` (que embute
    `MuscleRecoveryService.build_readiness_contract`). Grupo ausente da
    projecao = sem dose registrada = READY."""
    contracts: dict[int, ReadinessContract] = {}
    for item in projected_recovery or ():
        group_id = item.get("muscle_group_id")
        if group_id is None:
            continue
        contracts[int(group_id)] = ReadinessContract.from_projection(item)
    return contracts


def _track_for(
    request: PairRequest,
    tracks: Mapping[tuple, TrackState],
    *,
    muscle_group_id: Optional[int],
    stimulus_weight: float,
    is_compound: bool,
    load_step: Optional[Decimal],
) -> TrackState:
    """Trilha persistida do par NO CONTEXTO DE FADIGA pedido
    (`progression_engine.resolve_track`: sem historico no contexto, parte da
    trilha do par executada mais recentemente em qualquer contexto, sem
    desconto), ou uma trilha NOVA em calibracao (secao 5.10). Em qualquer dos
    casos o `sets_count` vem do MODELO: no Modo B as series sao do modelo,
    nunca da trilha.

    Este e o UNICO ponto onde `sets_count` pode passar de `MAX_SETS_COUNT`, e
    de proposito: aqui a trilha e so o transporte da prescricao do modelo para
    `prescribe` (D9 - o modelo manda nas series da FICHA), e esta trilha nunca
    e persistida. O teto de 4 (`clamp_sets_count`) vale para o ESTADO da
    progressao - `progression_evaluator.track_for`/`update_track_execution` e
    `progression_state.load_tracks`/`save_track`."""
    sets_count = int(request.model_sets_count) if request.model_sets_count else None
    existing, _own_history = resolve_track(
        tracks, request.exercise_id, request.equipment_id, request.fatigue_context, request.training_goal
    )
    if existing is None:
        existing = TrackState(
            exercise_id=int(request.exercise_id),
            equipment_id=int(request.equipment_id) if request.equipment_id is not None else 0,
            muscle_group_id=muscle_group_id,
            fatigue_context=request.fatigue_context or DEFAULT_FATIGUE_CONTEXT,
            training_goal=goal_value(request.training_goal),
        )
    return replace(
        existing,
        muscle_group_id=muscle_group_id if muscle_group_id is not None else existing.muscle_group_id,
        sets_count=sets_count if sets_count else existing.sets_count,
        stimulus_weight=stimulus_weight,
        is_compound=is_compound,
        load_increment_kg=load_step,
        sequence=request.sequence,
    )


def _fatigue_evidence(request: PairRequest, tracks: Mapping[tuple, TrackState]) -> Optional[dict[str, Any]]:
    """Auditoria (secao 14): em que contexto de fadiga a sessao foi prescrita
    e se ele ja tinha historico proprio (senao, a carga partiu da trilha de
    outro contexto)."""
    if request.fatigue_context is None or request.equipment_id is None:
        return None
    _track, own_history = resolve_track(
        tracks, request.exercise_id, request.equipment_id, request.fatigue_context, request.training_goal
    )
    return {"fatigue_context": request.fatigue_context, "fatigue_context_own_history": own_history}


def planned_fatigue_contexts(conn: Any, requests: Sequence[PairRequest]) -> list[PairRequest]:
    """Os mesmos pedidos (mesma ordem), cada um com o `fatigue_context` da sua
    posicao PLANEJADA: fadiga previa do dono `domain/prior_fatigue.py` sobre
    os itens anteriores da sessao, ordenados por `sequence`, com as series
    planejadas de `technique_volume.planned_item_sets` (as mesmas do preview
    do Modelo) e as contribuicoes cadastradas.

    Falha de leitura nao derruba a geracao: os pedidos voltam sem contexto
    (trilha executada mais recentemente do par, o comportamento anterior) e a
    falha vai para o log."""
    from ..domain.session_fatigue import planned_contexts
    from ..intelligence.session_fatigue_reader import load_contributions
    from ..intelligence.technique_volume import new_catalog, planned_item_sets

    try:
        ordered = sorted(range(len(requests)), key=lambda index: (int(requests[index].sequence or 0), index))
        catalog = new_catalog()
        items = [
            (
                planned_item_sets(
                    {
                        "sets_count": requests[index].model_sets_count,
                        "advanced_technique": requests[index].model_technique,
                        "advanced_technique_applications": requests[index].model_technique_applications,
                    },
                    catalog,
                ),
                int(requests[index].exercise_id),
            )
            for index in ordered
        ]
        contributions = load_contributions(conn, (exercise_id for _, exercise_id in items))
        bands = planned_contexts(items, contributions)
    except Exception as exc:  # noqa: BLE001 - contexto e refinamento, nunca derruba a geracao
        log_event("prescription.fatigue_context_unavailable", error=str(exc), requests=len(requests))
        return list(requests)
    result = list(requests)
    for index, band in zip(ordered, bands):
        result[index] = replace(result[index], fatigue_context=band.value)
    return result


def current_e1rm_for_requests(
    conn: Any, user_id: int, requests: Sequence[PairRequest], now: datetime
) -> dict[tuple[int, Optional[int]], float]:
    """e1RM atual (dono da leitura: `exposure_service.current_e1rm_by_pair`)
    dos pedidos cujo objetivo prescreve carga por %1RM
    (`progression_engine.uses_e1rm_load`). Nenhum pedido assim = nenhuma
    leitura. Falha de leitura nao derruba a geracao: sem e1RM, a progressao
    dupla decide a carga (e a falha vai para o log)."""
    pairs = sorted(
        {
            request.key
            for request in requests
            if request.equipment_id is not None and uses_e1rm_load(request.training_goal)
        }
    )
    if not pairs:
        return {}
    from ..intelligence.dashboard.exposure_service import current_e1rm_by_pair

    try:
        return dict(current_e1rm_by_pair(conn, user_id, pairs, today=now.date()))
    except Exception as exc:  # noqa: BLE001 - e1RM e refinamento, nunca derruba a geracao
        log_event("prescription.current_e1rm_unavailable", error=str(exc), pairs=len(pairs))
        return {}


def prescribe_pairs(
    conn: Any,
    user_id: int,
    requests: Sequence[PairRequest],
    *,
    readiness: Optional[Mapping[int, ReadinessContract]] = None,
    now: Optional[datetime] = None,
    model_fixed_sets: bool = True,
    session_order: bool = False,
) -> dict[tuple[int, Optional[int]], PairPrescription]:
    """Uma `PairPrescription` por item, em numero FIXO de queries.

    `session_order=True`: os pedidos SAO os itens de UMA sessao planejada (o
    slot do Modelo / a ficha), na ordem de `sequence`. O contexto de fadiga
    previa de cada item vem da posicao planejada (`planned_fatigue_contexts`,
    dono `domain/prior_fatigue.py`), e a trilha prescrita e a daquele
    contexto.

    `model_fixed_sets` e a UNICA diferenca entre os dois modos aqui:

    - Modo B (`True`, default): o modelo declara `sets_count` por item, a F5
      nunca altera a ficha e vira recomendacao ao PT (D9);
    - Modo A (`False`, PR-13): o modelo declara grupos e percentuais, nao
      series - quem decide series e a progressao (ate 4), e o orcamento de
      ativacao decide quantos EXERCICIOS, nao quantas series.

    Determinismo: para o mesmo estado persistido, as mesmas entradas e o mesmo
    `now`, a saida e identica - nao ha LLM, aleatoriedade nem ordenacao
    dependente de dicionario em nenhum ponto do caminho.
    """
    from .. import progression_state as ps

    if now is None:
        # Parede do USUARIO (M76 Lote 2b), mesma base de `last_executed_at`
        # (hora local das sessoes) - `datetime.now()` era o relogio UTC da
        # Lambda, 3h adiantado nas contas de dias da progressao.
        from ..intelligence.dashboard.time_windows import user_now, user_timezone

        now = user_now(user_timezone(conn, user_id))
    readiness = dict(readiness or {})
    requests = list(requests)
    if not requests:
        return {}
    # M100: exercicio laboral nao tem progressao nem prescricao do motor - o
    # padrao do catalogo (`domain/laboral.py`) e congelado a parte. O par
    # laboral fica FORA do resultado (e do contexto de fadiga planejada).
    laboral_ids = ps.load_laboral_ids(conn, (request.exercise_id for request in requests))
    if laboral_ids:
        requests = [request for request in requests if int(request.exercise_id) not in laboral_ids]
        if not requests:
            return {}
    if session_order:
        requests = planned_fatigue_contexts(conn, requests)

    pairs = sorted({request.key for request in requests if request.equipment_id is not None})
    exercise_ids = sorted({int(request.exercise_id) for request in requests})
    equipment_ids = sorted({int(request.equipment_id) for request in requests if request.equipment_id is not None})

    safety_by_pair = resolve_training_safety_bulk(conn, pairs) if pairs else {}
    catalog = ps.load_technique_catalog(conn)
    primary_group, stimulus_weight = ps.load_primary_groups(conn, exercise_ids)
    is_compound = ps.load_exercise_flags(conn, exercise_ids)
    load_steps = ps.load_equipment_steps(conn, equipment_ids)
    is_advanced_user = ps.load_user_flags(conn, user_id)
    # M97 F1: estados de TODOS os objetivos numa leitura; cada pedido le a
    # fase e a trilha do SEU objetivo.
    group_states = ps.load_all_group_states(conn, user_id)
    tracks = ps.load_tracks(conn, user_id)
    # Fix M97 (2026-10-26): e1RM atual SO dos pares de objetivo com carga por
    # %1RM (forca); hipertrofia nao faz leitura nova.
    e1rm_by_pair = current_e1rm_for_requests(conn, user_id, requests, now)

    result: dict[tuple[int, Optional[int]], PairPrescription] = {}
    for request in requests:
        exercise_id = int(request.exercise_id)
        # A fase e do grupo PRIMARY do exercicio (D2). Sem contribuicao
        # PRIMARY cadastrada, cai no grupo que o proprio modelo declarou.
        group_id = primary_group.get(exercise_id) or request.muscle_group_id
        goal = goal_value(request.training_goal)
        group = group_states.get((int(group_id), goal)) if group_id is not None else None
        if group is None:
            group = (
                new_group_state(int(group_id), now, goal)
                if group_id is not None
                else empty_group_state(goal)
            )

        safety = safety_by_pair.get(request.key) if request.equipment_id is not None else None
        # Par nao resolvido (equipamento indisponivel, D13 caminho b): a
        # seguranca cai no default conservador D3 (teto 9, sem falha).
        effective_safety = safety or build_training_safety(exercise_id=exercise_id)

        track = _track_for(
            request,
            tracks,
            muscle_group_id=group_id,
            stimulus_weight=float(stimulus_weight.get(exercise_id, 0.0)),
            is_compound=bool(is_compound.get(exercise_id)),
            load_step=load_steps.get(int(request.equipment_id)) if request.equipment_id is not None else None,
        )

        decision = prescribe(
            track,
            group,
            effective_safety,
            # Grupo fora da projecao = sem dose registrada = READY.
            (readiness.get(int(group_id)) if group_id is not None else None) or READY_CONTRACT,
            now=now,
            catalog=catalog,
            is_advanced_user=is_advanced_user,
            model_technique=request.model_technique or None,
            # Secao 6.2 / D9: no Modo B as series sao SEMPRE do modelo. No Modo
            # A (6.1 passo 6) elas vem da progressao.
            model_fixed_sets=model_fixed_sets,
            legacy_rpe_target=parse_rpe(request.model_rpe_target),
            model_technique_applications=request.model_technique_applications,
            model_technique_duration_seconds=request.model_technique_duration_seconds,
            evidence=_fatigue_evidence(request, tracks),
            current_e1rm_kg=e1rm_by_pair.get(request.key),
        )
        # Series: o modelo manda. A progressao nunca as altera aqui (a F5 ja
        # devolve HOLD + MODEL_FIXED_SETS), mas a dose reduzida da
        # recuperacao pode te-las cortado - e isso e limite de seguranca, nao
        # prescricao de volume, entao permanece.
        warnings: list[str] = []
        if request.model_technique and decision.advanced_technique != request.model_technique:
            # Tecnica fixa do modelo removida pela seguranca (secao 5.9): o
            # modelo continua salvo; a ficha avisa.
            warnings.append(WARNING_MODEL_TECHNIQUE_INVALID)

        result[request.key] = PairPrescription(
            decision=decision,
            safety=safety,
            technique=decision.advanced_technique,
            warnings=tuple(warnings),
        )
    return result

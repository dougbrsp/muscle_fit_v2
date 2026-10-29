"""Prescricao CONGELADA da ficha (PR-11; PLANO_20260912.md secoes 6.3, 9 e 12).

Pergunta que este modulo responde: o que foi prescrito para este exercicio no
momento em que a ficha foi criada - e o que a seguranca de HOJE ainda permite
exibir disso.

Duas regras, e so duas:

1. **Congelar.** `build_prescription()` monta o snapshot que vai para
   `workout_plan_exercises.prescription_json`. Abrir a ficha nao recalcula
   nada: `get_workout_plan` apenas LE este snapshot. Abrir o mesmo plano duas
   vezes devolve exatamente a mesma prescricao.
2. **Overlay de seguranca ao vivo.** `apply_safety_overlay()` compara o
   snapshot com o teto de seguranca de HOJE. So RESTRINGE (nunca amplia,
   nunca sobe o alvo, nunca "descongela") e o que restringiu fica registrado
   em `overlay.clamps`, auditavel.

Fonte da prescricao HOJE: o caminho legado de geracao (`engine_version =
LEGACY_ENGINE_VERSION`). Os motores determinísticos (Modo A/B) entram nos
PRs 12/13 e so trocam a ORIGEM - o contrato do snapshot nao muda, por isso
`engine_version` e `source` vao gravados em cada snapshot.

Modulo PURO: sem banco, sem FastAPI. O acesso a dados fica em
`app/services.py`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from .recovery_signal_summary import RECOVERY_SIGNALS_KEY
from .set_measures import normalize_measure
from .training_goal import goal_value
from .training_history import parse_rpe_ramp
from .training_safety import (
    DEFAULT_MAX_SAFE_RPE,
    FAILURE_DENIED_BY_PAIR,
    FAILURE_DENIED_BY_RECOVERY,
    READY,
    REASON_EQUIPMENT_SAFETY,
    Clamp,
    Readiness,
    TrainingSafety,
    build_training_safety,
    safety_for_goal,
    compose,
    failure_denial_origin,
    readiness_for_status,
    validate_technique,
)

# Par exercicio x equipamento desconhecido: default conservador D3 (teto 9,
# sem falha), sem tocar o banco.
UNKNOWN_PAIR_SAFETY = build_training_safety(equipment_max_safe_rpe=DEFAULT_MAX_SAFE_RPE)

# Versao do FORMATO do snapshot. Um leitor sempre sabe com o que esta lidando;
# mudanca incompativel de formato incrementa este numero (e nunca reescreve
# snapshots ja gravados - eles sao o registro do que foi prescrito).
SCHEMA_VERSION = 1

# `engine_version` do motor que produziu a prescricao. Hoje so existe o
# caminho legado; PR-12 (Modo B) e PR-13 (Modo A) gravam o seu proprio.
LEGACY_ENGINE_VERSION = "legacy-generation-v1"

# `source` do snapshot.
SOURCE_PLAN_CREATED = "PLAN_CREATED"  # congelado ao salvar a ficha
SOURCE_REFRESH = "REFRESH"  # POST /workout-plans/{id}/refresh-prescription

# `warnings[]` (a UI mostra um aviso por codigo; ver ExercisesTable.tsx)
WARNING_MODEL_TECHNIQUE_INVALID = "MODEL_TECHNIQUE_INVALID"
WARNING_EQUIPMENT_UNRESOLVED = "EQUIPMENT_UNRESOLVED"
# Produzido pela geracao do Modo B (PR-12), que substitui equipamento, e pelo
# M6, que substitui o EXERCICIO por outro do mesmo grupo; o bloco
# `substitution` do snapshot diz qual dos dois (`kind`) e por que (`reason`).
WARNING_EQUIPMENT_SUBSTITUTED = "EQUIPMENT_SUBSTITUTED"
# M101b: o Modelo deixou o EXERCICIO livre (item so com o grupo) e a geracao
# do Modo B escolheu o par (`generation/mode_b._resolve_free_exercise`). Nao e
# substituicao.
WARNING_EXERCISE_AUTO_CHOSEN = "EXERCISE_AUTO_CHOSEN"
# Fix M97 (2026-10-26): a geracao ajustou valores do Modelo incompativeis com o
# objetivo do item (descanso, reps, RPE, series, tecnica); o detalhe auditado
# fica em `goal_adjustments` (dono `prescription_defaults.normalize_item_for_goal`).
WARNING_GOAL_NORMALIZED = "GOAL_NORMALIZED"

# `prescription_status` devolvido pelo GET da ficha.
STATUS_FROZEN = "FROZEN"
STATUS_LEGACY = "LEGACY"  # ficha anterior ao PR-11: sem `prescription_json`

_FIRST_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def parse_rpe(value: Any) -> Optional[int]:
    """Alvo de RPE de um `rpe_target` (texto ou numero) -> inteiro, ou `None`.

    M76 (C4): deriva do parser UNICO `training_history.parse_rpe_ramp` - o
    texto e uma RAMPA por serie e o alvo e o ULTIMO degrau (decisao D4 de
    2026-09-18: e na ultima serie que o esforco e cobrado). `"9"` -> 9,
    `"8-9"` -> 9, `"8-9-10"` -> 10 (o teto do contrato corta depois, em
    `training_safety.compose`). Fracoes arredondam para BAIXO (`"8,5"` -> 8):
    na duvida, cobra-se menos. Lixo/vazio -> `None`."""
    if value is None or isinstance(value, bool):
        return None
    ramp = parse_rpe_ramp(value)
    if not ramp:
        return None
    return int(ramp[-1])


def _optional_int(value: Any) -> Optional[int]:
    """Inteiro da coluna (Data API devolve `str`; pymysql, `int`); None quando
    ausente ou ilegivel."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    match = _FIRST_NUMBER_RE.search(str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def failure_denied_by_of(prescription: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Quem negou a falha nesta prescricao congelada (Lote 1/P1).

    Snapshot com o campo `failure_denied_by` (congelado a partir do Lote 1):
    vale o gravado. Snapshot antigo, sem o campo: deriva pela MESMA regra de
    `training_safety.compose` (`failure_denial_origin`) a partir do teto do
    par congelado (`safety_constraints.max_safe_rpe`): teto 10 com falha
    negada -> RECOVERY; senao PAIR. Falha permitida -> None."""
    if not prescription:
        return None
    if prescription.get("failure_allowed"):
        return None
    stored = prescription.get("failure_denied_by")
    if stored in (FAILURE_DENIED_BY_PAIR, FAILURE_DENIED_BY_RECOVERY):
        return stored
    constraints = prescription.get("safety_constraints") or {}
    pair_max = constraints.get("max_safe_rpe") if isinstance(constraints, Mapping) else None
    return failure_denial_origin(False, pair_max)


def _utc_stamp() -> datetime:
    """Carimbo de auditoria do congelamento: um INSTANTE no relogio UTC (o
    mesmo do banco e da Lambda), naive como os demais `frozen_at`. Explicito
    em vez de `datetime.now()` do processo (M76 Lote 2b) - nao e "dia" de
    nada, so registra quando a prescricao foi congelada."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_prescription(
    exercise: Mapping[str, Any],
    *,
    safety: Optional[TrainingSafety] = None,
    source: str = SOURCE_PLAN_CREATED,
    engine_version: str = LEGACY_ENGINE_VERSION,
    frozen_at: Optional[datetime] = None,
    decision_id: Optional[int] = None,
    decision: Optional[Mapping[str, Any]] = None,
    warnings: Sequence[str] = (),
    requires_user_choice: bool = False,
    substitution: Optional[Mapping[str, Any]] = None,
    readiness: Optional[Readiness] = None,
    recovery_signals: Optional[Sequence[Mapping[str, Any]]] = None,
    goal_adjustments: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Snapshot do que esta sendo prescrito AGORA para este exercicio.

    Fix M97 (2026-10-26), campos ADITIVOS so quando existem (snapshot de
    hipertrofia sai identico ao de antes): `strength_load` (faixa de carga por
    %1RM da decisao de forca, evidencia `strength_e1rm_load` do motor) e
    `goal_adjustments` (ajustes da geracao ao objetivo, com
    `WARNING_GOAL_NORMALIZED` em `warnings[]`).

    `exercise` e a linha da ficha ja resolvida (o que foi efetivamente
    prescrito), acrescida dos indicadores do motor legado
    (`progression`, `progression_note`, `suggested_load_kg`,
    `suggested_reps`) - hoje a unica fonte de prescricao.

    `safety` ausente = par exercicio x equipamento desconhecido: cai no
    default conservador D3 (teto 9, sem falha) e ganha
    `WARNING_EQUIPMENT_UNRESOLVED`.

    `decision` e a `ProgressionDecision.to_dict()` quando ela existir. Ela e
    a FONTE da intensidade: teto, alvo, falha e clamps ja compostos por ela
    (seguranca do par + contrato de recuperacao PROJETADO para o dia do
    treino) prevalecem.

    `readiness` (M76, B1): sem decisao, a composicao usa o contrato PROJETADO
    do grupo (`ReadinessContract.readiness`) que o chamador informa - nunca
    `READY` fixo quando o grupo e conhecido. `None` = grupo desconhecido (linha
    sem exercicio do catalogo): so a seguranca do par limita.

    `requires_user_choice` chega True quando a GERACAO ja decidiu que este
    item exige escolha do usuario (D13: nenhum equipamento seguro na
    academia). Ele e somado - nunca substitui - a deteccao local de tecnica
    invalida.

    `substitution` e o bloco auditavel de uma troca (M6: outro exercicio do
    mesmo grupo; D13 (a)/M8: outro equipamento do mesmo exercicio), no
    formato de `generation/exercise_substitution.py` (`kind`, `reason`,
    from/to, `rejected[]`). Presente, garante `WARNING_EQUIPMENT_SUBSTITUTED`
    em `warnings[]`; ausente, a chave sai `None` (campo aditivo - o
    `schema_version` nao muda).

    `recovery_signals` (RPE 10 Lote 6/P9): resumo dos sinais de recuperacao
    que cortaram teto/dose (`recovery_signal_summary.summarize_signals`). Com
    decisao, vem da evidencia dela (`prescribe` monta a partir do
    `ReadinessContract`); sem decisao, o chamador informa. Lista vazia = nada
    a explicar (campo aditivo).
    """
    warnings_out = list(warnings)
    if goal_adjustments:
        warnings_out.append(WARNING_GOAL_NORMALIZED)
    if substitution:
        warnings_out.append(WARNING_EQUIPMENT_SUBSTITUTED)
    prescribed_rpe = parse_rpe(exercise.get("rpe_target"))
    readiness = readiness or readiness_for_status(READY)
    # M97 F2: politica do OBJETIVO da linha (teto/alvo e tecnicas, dono
    # `training_safety`); hipertrofia = o par como sempre (mesmo objeto).
    training_goal = exercise.get("training_goal")

    if safety is None:
        warnings_out.append(WARNING_EQUIPMENT_UNRESOLVED)
        composition = compose(UNKNOWN_PAIR_SAFETY, readiness, prescribed_rpe, training_goal=training_goal)
        safety_constraints: dict[str, Any] = {"max_safe_rpe": UNKNOWN_PAIR_SAFETY.max_safe_rpe, "source": None}
    else:
        safety = safety_for_goal(safety, training_goal)
        composition = compose(safety, readiness, prescribed_rpe, training_goal=training_goal)
        safety_constraints = {"max_safe_rpe": safety.max_safe_rpe, "source": safety.safety_source}

    clamps = [clamp.to_dict() for clamp in composition.clamps]
    effective_target = composition.target
    effective_ceiling = composition.ceiling
    failure_allowed = composition.failure_allowed
    if decision:
        effective_target = decision.get("effective_rpe_target", effective_target)
        effective_ceiling = decision.get("effective_rpe_ceiling", effective_ceiling)
        failure_allowed = bool(decision.get("failure_allowed", failure_allowed))
        clamps = list((decision.get("evidence") or {}).get("safety_clamps") or clamps)
        safety_constraints = dict(decision.get("safety_constraints") or safety_constraints)
    # Lote 1/P1: origem da negacao da falha congelada junto. Com decisao, a
    # composicao e a dela (teto do par em `safety_constraints`); mesma regra.
    failure_denied_by = (
        failure_denial_origin(failure_allowed, safety_constraints.get("max_safe_rpe"))
        if decision
        else composition.failure_denied_by
    )

    if recovery_signals is None and decision:
        recovery_signals = (decision.get("evidence") or {}).get(RECOVERY_SIGNALS_KEY)

    technique = exercise.get("advanced_technique") or None
    if technique and safety is not None:
        validation = validate_technique(technique, safety, failure_allowed=composition.failure_allowed)
        if not validation.valid:
            # A tecnica prescrita nao e valida neste par: quem decide o que
            # fazer e o usuario (trocar equipamento ou tirar a tecnica), nunca
            # o servidor silenciosamente.
            warnings_out.append(WARNING_MODEL_TECHNIQUE_INVALID)
            requires_user_choice = True

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": engine_version,
        "source": source,
        "frozen_at": (frozen_at or _utc_stamp()).replace(microsecond=0).isoformat(),
        "decision_id": decision_id,
        "phase": (decision or {}).get("phase_after"),
        "decision": (decision or {}).get("decision"),
        "sets_count": exercise.get("sets_count"),
        # Medida da serie (Lote T2, aditivo; `schema_version` nao muda): a da
        # linha da ficha pelo dono (`set_measures.normalize_measure`; ausente =
        # reps) e os alvos de tempo dela. Snapshot antigo sem a chave = reps.
        "measure": normalize_measure(exercise.get("measure")).value,
        "duration_seconds_target": _optional_int(exercise.get("duration_seconds_target")),
        "time_window_seconds": _optional_int(exercise.get("time_window_seconds")),
        # Objetivo de treino (M97 F0, aditivo; `schema_version` nao muda): o da
        # linha da ficha pela leitura do dono (`training_goal.goal_value`;
        # ausente = default). Snapshot antigo sem a chave = hipertrofia.
        "training_goal": goal_value(exercise.get("training_goal")),
        "reps_target": exercise.get("reps_target"),
        "load_target": exercise.get("load_target"),
        "rpe_target": exercise.get("rpe_target"),
        "effective_rpe_target": effective_target,
        "effective_rpe_ceiling": effective_ceiling,
        "failure_allowed": failure_allowed,
        # Lote 1/P1 (aditivo; `schema_version` nao muda): None | PAIR | RECOVERY.
        # Snapshot antigo sem o campo: `failure_denied_by_of` deriva.
        "failure_denied_by": failure_denied_by,
        # RPE 10 Lote 6/P9 (aditivo): `[{code, text, source_session_date}]`, o
        # que o banner e o briefing mostram como motivo do corte do teto.
        RECOVERY_SIGNALS_KEY: [dict(signal) for signal in recovery_signals or []],
        "advanced_technique": technique,
        # Fase 5, PR 6 (aditivo; `schema_version` nao muda): parametros da
        # tecnica prescrita pelo motor (aplicacoes, pausa, carga de cada drop,
        # equivalente planejado). Sem decisao do motor para a tecnica da linha
        # (ficha manual, refresh), saem da propria linha pelo mesmo dono
        # (`progression_engine.technique_parameters`). None so sem tecnica.
        "advanced_technique_parameters": _technique_parameters(
            decision, technique, exercise, safety_constraints.get("max_safe_rpe")
        ),
        "requires_user_choice": requires_user_choice,
        "safety_constraints": safety_constraints,
        "clamps": clamps,
        "warnings": sorted(set(warnings_out)),
        "substitution": dict(substitution) if substitution else None,
        # Indicadores do motor legado, congelados junto: sem eles a ficha
        # perderia a indicacao de progressao que hoje e recalculada na
        # leitura (o que este PR justamente elimina).
        "progression": exercise.get("progression"),
        "progression_note": exercise.get("progression_note"),
        "suggested_load_kg": _float(exercise.get("suggested_load_kg")),
        "suggested_reps": exercise.get("suggested_reps"),
        "reason": (decision or {}).get("reason") or exercise.get("progression_note") or "",
    }
    from .progression_engine import EVIDENCE_STRENGTH_E1RM_LOAD

    strength_load = ((decision or {}).get("evidence") or {}).get(EVIDENCE_STRENGTH_E1RM_LOAD)
    if strength_load:
        snapshot["strength_load"] = dict(strength_load)
    if goal_adjustments:
        snapshot["goal_adjustments"] = [dict(item) for item in goal_adjustments]
    return snapshot


def _technique_parameters(
    decision: Optional[Mapping[str, Any]],
    technique: Optional[str],
    exercise: Mapping[str, Any],
    pair_max_safe_rpe: Any,
) -> Optional[dict[str, Any]]:
    """Parametros da tecnica da linha. Com decisao do motor para ESTA tecnica,
    os dela. Sem ela (linha manual, `refresh-prescription`, decisao sem
    tecnica ou com outra), os da propria linha da ficha pelo dono
    (`progression_engine.technique_parameters`): aplicacoes/pausa da ficha e
    `target_rpe` do protocolo pelo teto do par congelado - o MESMO numero que
    o POST da serie grava (`training_safety.effective_protocol_target_rpe`).
    A carga de cada etapa continua com `session_loads` na leitura."""
    if not technique:
        return None
    params = (decision or {}).get("advanced_technique_parameters")
    if params and params.get("technique_code") == technique:
        return dict(params)
    # Import tardio: o motor de progressao e pesado (mesmo padrao de
    # `progression_engine._reduced_sets`).
    from .progression_engine import TECHNIQUE_SOURCE_PLAN, technique_parameters

    return technique_parameters(
        technique,
        source=TECHNIQUE_SOURCE_PLAN,
        applications=_optional_int(exercise.get("advanced_technique_applications")),
        pause_seconds=_optional_int(exercise.get("advanced_technique_duration_seconds")),
        pair_max_safe_rpe=pair_max_safe_rpe,
    )


def with_substitution(snapshot: Mapping[str, Any], substitution: Mapping[str, Any]) -> dict[str, Any]:
    """Copia do snapshot com o bloco `substitution` (M8: troca deliberada de
    equipamento na ficha) e `WARNING_EQUIPMENT_SUBSTITUTED` garantido em
    `warnings[]`. Substitui um bloco anterior (a decisao do usuario e a mais
    recente e a que vale); nada mais do snapshot e tocado."""
    result = dict(snapshot)
    result["substitution"] = dict(substitution)
    result["warnings"] = sorted(set(list(result.get("warnings") or []) + [WARNING_EQUIPMENT_SUBSTITUTED]))
    return result


def apply_safety_overlay(
    prescription: Optional[Mapping[str, Any]], safety: Optional[TrainingSafety]
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Overlay de seguranca ao vivo (secao 6.3): compara o snapshot com o teto
    de HOJE. Devolve `(prescricao exibida, overlay)`.

    So RESTRINGE. Se o teto de hoje ficou mais baixo que o alvo congelado, o
    alvo exibido cai e o corte entra em `overlay["clamps"]`; se o teto subiu,
    nada muda - a prescricao congelada continua valendo como foi congelada.
    `overlay` e `None` quando nao ha par de seguranca conhecido.

    M76 (B1/C5): o teto e a falha do OVERLAY tambem nunca passam dos do
    snapshot. O snapshot ja compos o par com o contrato de recuperacao
    projetado; o overlay so traz a mudanca de seguranca do PAR desde entao.
    Antes, o overlay saia composto com READY (teto 10 num par com falha
    permitida) e o Modo Treino, que le `overlay ?? prescricao`, oferecia RPE 10
    num grupo em dose reduzida cujo briefing dizia "teto 9"."""
    if safety is None:
        return (dict(prescription) if prescription else None), None

    composition = compose(
        safety, readiness_for_status(READY), training_goal=(prescription or {}).get("training_goal")
    )
    ceiling = composition.ceiling
    failure_allowed = composition.failure_allowed
    frozen_ceiling = (prescription or {}).get("effective_rpe_ceiling")
    if isinstance(frozen_ceiling, int) and not isinstance(frozen_ceiling, bool) and frozen_ceiling < ceiling:
        ceiling = frozen_ceiling
    if prescription and not prescription.get("failure_allowed"):
        failure_allowed = False
    failure_allowed = failure_allowed and ceiling == composition.ceiling
    overlay = {
        "effective_rpe_ceiling": ceiling,
        "failure_allowed": failure_allowed,
        "failure_denied_by": _overlay_denial(failure_allowed, composition.failure_denied_by, prescription),
        "max_safe_rpe": safety.max_safe_rpe,
        "safety_source": safety.safety_source,
        "clamps": [],
    }
    if not prescription:
        return None, overlay

    shown = dict(prescription)
    frozen_target = shown.get("effective_rpe_target")
    if isinstance(frozen_target, int) and frozen_target > composition.ceiling:
        overlay["clamps"] = [
            Clamp("rpe_target", frozen_target, composition.ceiling, REASON_EQUIPMENT_SAFETY).to_dict()
        ]
        shown["effective_rpe_target"] = composition.ceiling
    if shown.get("effective_rpe_ceiling") is None or (
        isinstance(shown.get("effective_rpe_ceiling"), int) and shown["effective_rpe_ceiling"] > composition.ceiling
    ):
        shown["effective_rpe_ceiling"] = composition.ceiling
    if shown.get("failure_allowed") and not composition.failure_allowed:
        shown["failure_allowed"] = False
    shown["failure_denied_by"] = _overlay_denial(
        bool(shown.get("failure_allowed")), composition.failure_denied_by, prescription
    )
    return shown, overlay


def _overlay_denial(
    failure_allowed: bool, today_pair_denial: Optional[str], prescription: Optional[Mapping[str, Any]]
) -> Optional[str]:
    """Origem da negacao depois do overlay (Lote 1/P1): o par de HOJE proibindo
    a falha -> PAIR (vence, como no empate de `compose`); senao a origem
    congelada (`failure_denied_by_of`); sem snapshot, PAIR."""
    if failure_allowed:
        return None
    if today_pair_denial == FAILURE_DENIED_BY_PAIR:
        return FAILURE_DENIED_BY_PAIR
    return failure_denied_by_of(prescription) or FAILURE_DENIED_BY_PAIR

"""Intensidade EFETIVA de uma serie em execucao (PR-14; PLANO_20260912.md
secoes 4, 5.7 e 14).

Pergunta que este modulo responde, para uma serie que esta sendo gravada ou
avaliada AGORA: qual e o alvo de RPE que vale nela, qual o teto seguro, a
falha e permitida - e o que precisa ser registrado quando o RPE gravado passa
do teto.

Tres fontes, nesta ordem de confianca:

1. **Prescricao congelada** (`workout_plan_exercises.prescription_json`, PR-11)
   com o overlay de seguranca de HOJE (`prescription.apply_safety_overlay`):
   o alvo exibido no Modo Treino e exatamente este.
2. **Linha legada da ficha** (ficha anterior ao PR-11, sem snapshot): o
   `rpe_target` gravado e tratado como INTENCAO e passa por
   `training_safety.compose` ao vivo - `rpe_target=10` num par onde a falha e
   proibida e cortado aqui, com clamp `LEGACY_PLAN`.
3. **Sem ficha** (treino livre / app do relogio): so seguranca do par. Nao ha
   alvo exibido, entao `rpe_target_effective` fica NULL; o teto continua
   valendo para o evento de seguranca.

Regra permanente da secao 4: a composicao so RESTRINGE. Nada aqui eleva alvo,
teto ou permissao de falha - nem quando o cliente manda um numero maior.

`is_advanced_user` nao entra em nenhuma funcao deste modulo (D11).

Organizacao: tudo acima de "Acesso a dados" e PURO. O acesso a dados le
`exercises` + `workout_plan_exercises` + a matriz de seguranca, sempre por
SELECT.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from ..set_types import is_working_set
from . import prescription as prescription_domain
from .set_measures import is_rep_based
from .training_goal import goal_value
from .training_history import parse_reps_range
from .progression_engine import (
    INTENSITY_ABOVE_SAFE_CEILING,
    SAFETY_EVENT_RPE_ABOVE_CEILING,
    SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
    intensity_status_for,
    resolved_failure_denial,
    safety_event_type_for,
    set_effort_target,
    set_intensity_status,
    set_reps_position,
)
from .training_safety import (
    DEFAULT_MAX_SAFE_RPE,
    FAILURE_DENIED_BY_RECOVERY,
    REASON_EQUIPMENT_SAFETY,
    FAILURE_RPE,
    READY,
    REASON_LEGACY_PLAN,
    REDUCED_DOSE,
    Clamp,
    IntensityComposition,
    TrainingSafety,
    compose,
    effective_protocol_target_rpe,
    readiness_for_status,
)

# `source` da intensidade efetiva (auditoria/log; nao vai para o banco).
SOURCE_PRESCRIPTION = "PRESCRIPTION"
SOURCE_LEGACY_PLAN = "LEGACY_PLAN"
SOURCE_SAFETY_ONLY = "SAFETY_ONLY"
SOURCE_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EffectiveIntensity:
    """O que vale para a serie que esta sendo executada agora."""

    rpe_target_effective: Optional[int]
    ceiling: Optional[int]
    failure_allowed: bool
    clamps: tuple[dict[str, Any], ...] = ()
    source: str = SOURCE_UNKNOWN
    # Lote 1/P1: quem negou a falha (None | PAIR | RECOVERY); decide o tipo do
    # `safety_event` (seguranca x informativo).
    failure_denied_by: Optional[str] = None

    @property
    def known(self) -> bool:
        """True quando existe teto de seguranca resolvido para o par."""
        return self.ceiling is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rpe_target_effective": self.rpe_target_effective,
            "effective_rpe_ceiling": self.ceiling,
            "failure_allowed": self.failure_allowed,
            "clamps": [dict(clamp) for clamp in self.clamps],
            "source": self.source,
            "failure_denied_by": self.failure_denied_by,
        }


UNKNOWN_INTENSITY = EffectiveIntensity(None, None, False, (), SOURCE_UNKNOWN)


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def from_prescription(
    frozen: Optional[Mapping[str, Any]], safety: Optional[TrainingSafety]
) -> Optional[EffectiveIntensity]:
    """Snapshot congelado + overlay de seguranca de hoje (secao 6.3).

    `None` quando nao ha snapshot - o chamador cai na fonte 2 ou 3."""
    if not frozen:
        return None
    shown, overlay = prescription_domain.apply_safety_overlay(frozen, safety)
    shown = shown or {}
    ceiling = _int_or_none(shown.get("effective_rpe_ceiling"))
    failure_allowed = bool(shown.get("failure_allowed")) and ceiling == FAILURE_RPE
    return EffectiveIntensity(
        rpe_target_effective=_int_or_none(shown.get("effective_rpe_target")),
        ceiling=ceiling,
        failure_allowed=failure_allowed,
        clamps=tuple(dict(clamp) for clamp in ((overlay or {}).get("clamps") or [])),
        source=SOURCE_PRESCRIPTION,
        failure_denied_by=resolved_failure_denial(failure_allowed, prescription_domain.failure_denied_by_of(shown)),
    )


def from_legacy_plan(plan_rpe_target: Any, safety: Optional[TrainingSafety]) -> EffectiveIntensity:
    """Ficha sem snapshot: o `rpe_target` gravado e INTENCAO e e cortado ao
    vivo pelo teto do par. O corte fica registrado como `LEGACY_PLAN`."""
    if safety is None:
        return EffectiveIntensity(
            rpe_target_effective=prescription_domain.parse_rpe(plan_rpe_target),
            ceiling=None,
            failure_allowed=False,
            clamps=(),
            source=SOURCE_LEGACY_PLAN,
        )
    prescribed = prescription_domain.parse_rpe(plan_rpe_target)
    composition = compose(safety, readiness_for_status(READY), prescribed)
    clamps = tuple(
        Clamp(clamp.field, clamp.from_value, clamp.to_value, REASON_LEGACY_PLAN).to_dict()
        for clamp in composition.clamps
    )
    return EffectiveIntensity(
        rpe_target_effective=composition.target,
        ceiling=composition.ceiling,
        failure_allowed=composition.failure_allowed,
        clamps=clamps,
        source=SOURCE_LEGACY_PLAN,
        failure_denied_by=composition.failure_denied_by,
    )


def from_safety(safety: Optional[TrainingSafety]) -> EffectiveIntensity:
    """Sem ficha (treino livre): so o teto do par. Sem alvo exibido, sem
    `rpe_target_effective` - o teto continua valendo para o evento."""
    if safety is None:
        return UNKNOWN_INTENSITY
    composition = compose(safety, readiness_for_status(READY))
    return EffectiveIntensity(
        rpe_target_effective=None,
        ceiling=composition.ceiling,
        failure_allowed=composition.failure_allowed,
        clamps=(),
        source=SOURCE_SAFETY_ONLY,
        failure_denied_by=composition.failure_denied_by,
    )


def composition_of(intensity: EffectiveIntensity) -> IntensityComposition:
    """`IntensityComposition` equivalente, para reusar
    `training_safety.clamp_ai_suggestion` sem duplicar regra."""
    ceiling = intensity.ceiling if intensity.ceiling is not None else DEFAULT_MAX_SAFE_RPE
    return IntensityComposition(
        ceiling=ceiling,
        failure_allowed=intensity.failure_allowed,
        target=intensity.rpe_target_effective if intensity.rpe_target_effective is not None else ceiling,
        # Lote 6/P9: a origem da negacao segue junto (o prompt da IA diz "pela
        # recuperacao" em vez de "neste par" quando foi o dia que negou).
        failure_denied_by=resolved_failure_denial(intensity.failure_allowed, intensity.failure_denied_by),
    )


def restrict_ceiling(intensity: EffectiveIntensity, requested_ceiling: Any) -> EffectiveIntensity:
    """Aplica um teto vindo do CLIENTE. So restringe (secao 4): um numero
    maior que o teto do servidor e ignorado."""
    requested = _int_or_none(requested_ceiling)
    if requested is None or intensity.ceiling is None or requested >= intensity.ceiling:
        return intensity
    target = intensity.rpe_target_effective
    failure_allowed = intensity.failure_allowed and requested == FAILURE_RPE
    # O teto do cliente e o do dia (recuperacao): se foi ele que tirou a
    # falha, a origem e RECOVERY; a negacao que ja existia fica como estava.
    denied_by = intensity.failure_denied_by
    if not failure_allowed and denied_by is None:
        denied_by = FAILURE_DENIED_BY_RECOVERY
    return EffectiveIntensity(
        rpe_target_effective=min(target, requested) if target is not None else None,
        ceiling=requested,
        failure_allowed=failure_allowed,
        clamps=intensity.clamps,
        source=intensity.source,
        failure_denied_by=denied_by,
    )


def safety_event_for(set_type: Any, rpe: Any, intensity: EffectiveIntensity) -> Optional[str]:
    """`exercise_sets.safety_event` desta serie (secao 5.7).

    Uma serie `working` com RPE >= 10 quando a falha NAO e permitida:
    - negada pelo PAR (teto do equipamento < 10): `RPE_ABOVE_SAFE_CEILING`,
      evento de seguranca (laranja, conta em metrica);
    - negada so pela RECUPERACAO do dia (par com falha permitida):
      `RPE_ABOVE_RECOVERY_TARGET`, informativo (Lote 1/P1, decisao do usuario
      de 2026-10-11).
    Nos dois casos vale como intensidade atingida (quem decide isso e o motor
    de progressao) e nunca eleva o alvo futuro.

    Nada e registrado quando o par e desconhecido (sem teto resolvido): sem
    regra conhecida nao existe violacao a registrar."""
    if not intensity.known or intensity.failure_allowed:
        return None
    if not is_working_set(set_type):
        return None
    value = None if rpe is None or isinstance(rpe, bool) else _float(rpe)
    if value is None or value < FAILURE_RPE:
        return None
    return safety_event_type_for(resolved_failure_denial(intensity.failure_allowed, intensity.failure_denied_by))


def _json_dict(value: Any) -> Optional[Mapping[str, Any]]:
    """Coluna MySQL JSON lida via PyMySQL (string crua) -> dict. Mesmo
    contrato de `services.from_json_dict`, sem importar `services` (que
    importa este dominio)."""
    if value is None or isinstance(value, Mapping):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def session_intensity_status(
    sets: Iterable[Mapping[str, Any]], *, failure_allowed: bool, failure_denied_by: Optional[str] = None
) -> Optional[str]:
    """`intensity_status` da sessao (secao 5.7), para analytics e dashboards.

    Le o que ficou GRAVADO nas series (`rpe_target_effective`, `safety_event`)
    - nao recalcula prescricao historica - e delega a classificacao para
    `progression_engine.intensity_status_for`, a mesma funcao que o motor de
    progressao usa. So series `working` entram.

    `safety_event` gravado vence: ele e o registro historico de que a serie
    passou do teto daquele dia, mesmo que a matriz de seguranca tenha mudado
    depois. Sem nenhum alvo efetivo gravado (series anteriores ao PR-14) o
    status e `None` - a secao 12 manda exibir o historico como esta, sem
    inventar alvo retroativo.

    Lote 1/P1: so o evento do PAR vence. O informativo gravado
    (`RPE_ABOVE_RECOVERY_TARGET`) diz que a falha foi negada so pela
    recuperacao; sem `failure_denied_by` explicito, e ele que define a origem
    (senao PAIR, o comportamento anterior).
    """
    working = [item for item in sets if is_working_set(item)]
    if not working:
        return None
    events = {str(item.get("safety_event") or "") for item in working}
    if SAFETY_EVENT_RPE_ABOVE_CEILING in events:
        return INTENSITY_ABOVE_SAFE_CEILING
    if failure_denied_by is None and SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET in events:
        failure_denied_by = FAILURE_DENIED_BY_RECOVERY

    target = None
    for item in working:
        value = _int_or_none(item.get("rpe_target_effective"))
        if value is not None:
            target = value
    if target is None:
        return None

    # O alvo e cobrado na ULTIMA serie `working` (D4 revisada em 2026-09-18);
    # `highest` deixa o RPE 10 de qualquer serie visivel para a regra de teto.
    reported = [rpe for rpe in (_decimal(item.get("rpe")) for item in working) if rpe is not None]
    last_rpe = _decimal(working[-1].get("rpe"))
    return intensity_status_for(
        last_rpe, target, failure_allowed, highest=max(reported, default=None), failure_denied_by=failure_denied_by
    )


@dataclass(frozen=True)
class SetIntensityReading:
    """O que o motor diz de UMA serie recem-salva (RPE 10 Lote 6/P9): o
    status (`progression_engine.set_intensity_status`), o evento gravado e o
    alvo/teto que valeram. Vai para a resposta do salvamento e para o prompt
    da IA por serie - os dois leem o MESMO status."""

    status: Optional[str]
    safety_event: Optional[str]
    rpe: Optional[float]
    # Alvo COBRADO nesta serie (M96): o efetivo, restringido pelo degrau da
    # rampa planejada (`progression_engine.set_effort_target`).
    target: Optional[Union[int, float]]
    ceiling: Optional[int]
    failure_allowed: bool
    failure_denied_by: Optional[str] = None
    # set-eval-v5 (2026-10-12): reps da serie contra a faixa
    # (`progression_engine.set_reps_position`); None sem faixa/reps ou em
    # serie que nao e de trabalho.
    reps: Optional[int] = None
    reps_min: Optional[int] = None
    reps_max: Optional[int] = None
    reps_position: Optional[str] = None


def _target_number(value: Optional[Decimal]) -> Optional[Union[int, float]]:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def with_protocol_target(intensity: EffectiveIntensity, protocol_target: Any) -> EffectiveIntensity:
    """Intensidade de uma serie do Modo Intensivo com `rpe_target_source=
    'protocol'` (M96): o alvo cobrado e o do PROTOCOLO da tecnica (o gravado
    em `rpe_target_effective` e o que a tela mostra), nao o da prescricao.
    Teto, falha e origem da negacao nao mudam. Sem alvo de protocolo: a
    intensidade como veio."""
    target = _int_or_none(protocol_target)
    if target is None:
        return intensity
    return replace(intensity, rpe_target_effective=target)


def frozen_snapshot(prescription_json: Any) -> Mapping[str, Any]:
    """Prescricao CONGELADA do exercicio lancado ja decodificada (`{}` sem
    snapshot ou JSON invalido). Leitura, sem regra."""
    return _json_dict(prescription_json) or {}


def plan_reps_range(prescription_json: Any, plan_reps_target: Any) -> tuple[Optional[int], Optional[int]]:
    """Faixa de reps da ficha do exercicio lancado (set-eval-v5): a da
    prescricao CONGELADA (`prescription_json.reps_target`, a que o Modo Treino
    mostrou), senao a coluna legada `workout_plan_exercises.reps_target`.
    Sem faixa -> (None, None)."""
    frozen = _json_dict(prescription_json) or {}
    return parse_reps_range(frozen.get("reps_target") or plan_reps_target)


def plan_training_goal(prescription_json: Any) -> str:
    """Objetivo de treino do exercicio lancado (M97 F2): o da prescricao
    CONGELADA (`prescription_json.training_goal`, o mesmo carimbado em
    `exercises.training_goal`), pela leitura tolerante do dono
    `training_goal.goal_value` (sem ficha/snapshot antigo = hipertrofia)."""
    frozen = _json_dict(prescription_json) or {}
    return goal_value(frozen.get("training_goal"))


def plan_item_goal(prescription_json: Any, column_goal: Any = None) -> str:
    """Objetivo RESOLVIDO do item da ficha: o da prescricao CONGELADA quando o
    snapshot o traz, senao a coluna `workout_plan_exercises.training_goal`
    (093), pela leitura tolerante do dono `training_goal.goal_value`."""
    frozen = _json_dict(prescription_json) or {}
    return goal_value(frozen.get("training_goal") or column_goal)


def plan_rpe_target(prescription_json: Any, plan_rpe_target: Any) -> Any:
    """`rpe_target` da ficha do exercicio lancado (rampa planejada da regra da
    proxima serie): o da prescricao CONGELADA (`prescription_json.rpe_target`),
    senao a coluna legada `workout_plan_exercises.rpe_target`. Sem ficha: None."""
    frozen = _json_dict(prescription_json) or {}
    return frozen.get("rpe_target") or plan_rpe_target


def set_intensity_reading(
    set_type: Any,
    rpe: Any,
    intensity: EffectiveIntensity,
    *,
    reps: Any = None,
    reps_range: tuple[Optional[int], Optional[int]] = (None, None),
    measure: Any = None,
    planned_rpe: Any = None,
) -> SetIntensityReading:
    """Status e evento de UMA serie contra a intensidade efetiva resolvida,
    e a posicao das reps contra a faixa (`reps_range` = piso/teto da
    decisao/ficha). Serie preparatoria/aplicacao de tecnica nao tem alvo a
    cobrar: status e posicao `None` (o evento segue a regra de
    `safety_event_for`).

    Medidas de serie (Lote T3): serie por tempo/maximo de reps
    (`set_measures.is_rep_based` falso; ausente = reps) nao tem posicao na
    faixa de reps da ficha - `reps_position` `None`; o status pelo RPE fica.

    M96 (veredito unico): `planned_rpe` e o degrau da rampa planejada desta
    serie (`next_set_load.planned_ramp_rpe`, o MESMO que a carga da proxima
    serie usa); o alvo cobrado e `progression_engine.set_effort_target` (so
    restringe). Sem rampa: o alvo efetivo, como antes."""
    value = _decimal(rpe)
    working = is_working_set(set_type)
    charged_target = set_effort_target(intensity.rpe_target_effective, planned_rpe)
    status = (
        set_intensity_status(
            value,
            charged_target,
            intensity.failure_allowed,
            failure_denied_by=intensity.failure_denied_by,
        )
        if working
        else None
    )
    reps_min, reps_max = reps_range
    position = set_reps_position(reps, reps_min, reps_max) if working and is_rep_based(measure) else None
    return SetIntensityReading(
        status=status,
        safety_event=safety_event_for(set_type, rpe, intensity),
        rpe=float(value) if value is not None else None,
        target=_target_number(charged_target),
        ceiling=intensity.ceiling,
        failure_allowed=intensity.failure_allowed,
        failure_denied_by=intensity.failure_denied_by,
        reps=int(reps) if position is not None else None,
        reps_min=reps_min if position is not None else None,
        reps_max=reps_max if position is not None else None,
        reps_position=position,
    )


def _format_rpe(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def clamp_rpe_text(text: Any, ceiling: Optional[int]) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Corte UNICO de um `rpe_target` TEXTUAL pelo teto (M76, C4).

    O texto e uma RAMPA por serie (`training_history.parse_rpe_ramp`, o parser
    unico): `"8-9-10"` = 8 na 1a serie, 9 na 2a, 10 da 3a em diante. Cada
    degrau e cortado por `min(degrau, teto)` e os degraus repetidos no FIM
    colapsam (a rampa mais curta ja repete o ultimo degrau): `"8-9-10"` com teto
    9 vira `"8-9"`; `"9-10"` vira `"9"`; `"8-10"` vira `"8-9"`. O alvo que o
    resto do sistema le (`prescription.parse_rpe`, ultimo degrau) passa a ser
    exatamente o teto.

    `ceiling` e o teto JA COMPOSTO pelo chamador (par + recuperacao: e o que
    `periodization/validation.py`, `template_safety_validation.py` e a geracao
    legada com dose reduzida passam). Sem teto ou sem numero no texto, devolve
    o texto como veio e nenhum clamp."""
    from .training_history import parse_rpe_ramp

    if ceiling is None or text is None:
        return (None if text is None else str(text)), []
    original = str(text)
    ramp = list(parse_rpe_ramp(original))
    if not ramp:
        return original, []
    teto = Decimal(int(ceiling))
    clamped = [min(step, teto) for step in ramp]
    if clamped == ramp:
        return original, []
    while len(clamped) > 1 and clamped[-1] == clamped[-2]:
        clamped.pop()
    result = "-".join(_format_rpe(step) for step in clamped)
    return result, [Clamp("rpe_target", original, result, REASON_EQUIPMENT_SAFETY).to_dict()]


def clamp_reduced_dose_rpe_targets(exercises: Sequence[dict[str, Any]], reduced_dose_groups: Iterable[str]) -> None:
    """Aplica `clamp_rpe_text` (in place) no `rpe_target` dos exercicios cujo
    `muscle_primary` esta em `reduced_dose_groups`, com o teto do contrato
    REDUCED_DOSE (`training_safety.readiness_for_status`, que le o dono
    `MuscleRecoveryService._RECOMMENDATIONS`). Substitui
    `workout_plan_ai.cap_rpe_target_for_reduced_dose` (9 escrito a mao, M76 C4)
    nos caminhos legados de geracao."""
    wanted = {str(name).strip().lower() for name in reduced_dose_groups or () if name}
    if not wanted:
        return
    ceiling = readiness_for_status(REDUCED_DOSE).max_rpe
    for exercise in exercises:
        if str(exercise.get("muscle_primary") or "").strip().lower() not in wanted:
            continue
        if not exercise.get("rpe_target"):
            continue
        exercise["rpe_target"], _ = clamp_rpe_text(exercise.get("rpe_target"), ceiling)


# ---------------------------------------------------------------------------
# Acesso a dados (somente SELECT)
# ---------------------------------------------------------------------------


def equipment_id_of_gym_equipment(conn: Any, gym_equipment_id: Any) -> Optional[int]:
    """`gym_equipment.id` (equipamento da academia) -> `equipment.id` (catalogo
    global), que e o lado que a matriz de seguranca conhece."""
    value = _int_or_none(gym_equipment_id)
    if value is None:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT equipment_id FROM gym_equipment WHERE id = %s", (value,))
        row = cur.fetchone()
    return _int_or_none(row.get("equipment_id")) if row else None


def resolve_pair_safety(conn: Any, exercise_id: Any, gym_equipment_id: Any) -> Optional[TrainingSafety]:
    """Seguranca do par `(exercise_id do catalogo, gym_equipment.id)`.

    `None` quando falta um dos lados - o chamador trata como par desconhecido
    (e NAO inventa um teto permissivo)."""
    from .training_safety import resolve_training_safety

    exercise = _int_or_none(exercise_id)
    equipment_id = equipment_id_of_gym_equipment(conn, gym_equipment_id)
    if exercise is None or equipment_id is None:
        return None
    return resolve_training_safety(exercise, equipment_id, conn=conn)


def frozen_protocol_target_rpe(prescription_json: Any) -> Any:
    """`advanced_technique_parameters.target_rpe` da prescricao congelada
    (Modo Intensivo); None em snapshot antigo/sem tecnica."""
    frozen = _json_dict(prescription_json) or {}
    params = frozen.get("advanced_technique_parameters")
    return params.get("target_rpe") if isinstance(params, Mapping) else None


def protocol_target_for_logged_exercise(
    conn: Any, mapping_id: Any, gym_equipment_id: Any, prescription_json: Any = None
) -> int:
    """RPE-alvo do PROTOCOLO da tecnica para uma serie do Modo Intensivo:
    teto do par AO VIVO (sem recuperacao) cortado pelo congelado, pela regra
    unica do dono (`training_safety.effective_protocol_target_rpe`). Par
    desconhecido -> teto desconhecido -> 9."""
    safety = resolve_pair_safety(conn, mapping_id, gym_equipment_id)
    return effective_protocol_target_rpe(
        safety.max_safe_rpe if safety is not None else None, frozen_protocol_target_rpe(prescription_json)
    )


_EXERCISE_SQL = """
SELECT e.id, e.mapping_id, e.equipment_id, e.workout_plan_exercise_id,
       wpe.rpe_target AS plan_rpe_target, wpe.prescription_json
FROM exercises e
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE e.id = %s
"""


def intensity_for_row(row: Mapping[str, Any], safety: Optional[TrainingSafety]) -> EffectiveIntensity:
    """Ordem de confianca do topo do modulo para UMA linha de `exercises`
    (com `workout_plan_exercise_id`, `plan_rpe_target`, `prescription_json`).
    Regra UNICA usada pela gravacao da serie (`safety_event`) e pelos
    analytics (`failure_allowed`/`intensity_status`) - M76 C6."""
    if row.get("workout_plan_exercise_id") is None:
        return from_safety(safety)
    frozen = _json_dict(row.get("prescription_json"))
    return from_prescription(frozen, safety) or from_legacy_plan(row.get("plan_rpe_target"), safety)


def resolve_for_logged_exercise(conn: Any, exercise_id: int) -> EffectiveIntensity:
    """Intensidade efetiva do exercicio JA LANCADO (`exercises.id`), na ordem
    de confianca do topo do modulo. Nunca levanta por dado ausente: sem par
    conhecido devolve `UNKNOWN_INTENSITY`."""
    with conn.cursor() as cur:
        cur.execute(_EXERCISE_SQL, (int(exercise_id),))
        row = cur.fetchone()
    if not row:
        return UNKNOWN_INTENSITY

    safety = resolve_pair_safety(conn, row.get("mapping_id"), row.get("equipment_id"))
    return intensity_for_row(row, safety)


_EXERCISES_BULK_SQL = """
SELECT e.id, e.mapping_id, ge.equipment_id AS catalog_equipment_id, e.workout_plan_exercise_id,
       wpe.rpe_target AS plan_rpe_target, wpe.prescription_json
FROM exercises e
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE e.id IN ({placeholders})
"""


def failure_allowed_by_logged_exercise(conn: Any, exercise_row_ids: Sequence[Any]) -> dict[int, bool]:
    """`exercises.id` -> a falha concentrica era permitida naquele exercicio
    lancado? Mesma regra da gravacao da serie (`intensity_for_row`): a
    prescricao congelada - que ja compos o par com o contrato de recuperacao
    projetado (B1) - com o overlay de seguranca; sem ficha, so o par (como o
    avaliador do motor, que compoe com READY quando nao recebe contrato).
    M76 (C6): antes, os analytics olhavam SO o equipamento. Leitura em LOTE
    (duas queries), sem N+1. Par desconhecido -> `False` (D3)."""
    from .training_safety import resolve_training_safety_bulk

    ids = sorted({value for value in (_int_or_none(item) for item in exercise_row_ids) if value is not None})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(_EXERCISES_BULK_SQL.format(placeholders=placeholders), tuple(ids))
        rows = cur.fetchall() or []
    pairs = {
        (int(row["mapping_id"]), int(row["catalog_equipment_id"]))
        for row in rows
        if _int_or_none(row.get("mapping_id")) and _int_or_none(row.get("catalog_equipment_id"))
    }
    safety = resolve_training_safety_bulk(conn, pairs) if pairs else {}
    result: dict[int, bool] = {}
    for row in rows:
        key = (_int_or_none(row.get("mapping_id")), _int_or_none(row.get("catalog_equipment_id")))
        intensity = intensity_for_row(row, safety.get(key) if None not in key else None)
        result[int(row["id"])] = bool(intensity.known and intensity.failure_allowed)
    return result

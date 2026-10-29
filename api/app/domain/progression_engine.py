"""Motor de Progressao v2 (PR-10; PLANO_20260912.md secoes 5.3 a 5.11;
decisoes D1, D2, D4, D5, D8, D9, D10, D11 e D12).

Unidade de estado (D2): a FASE e do GRUPO MUSCULAR (`GroupState`, grupo
PRIMARY do exercicio); carga, series e tecnica sao da TRILHA
(`TrackState` = usuario x exercicio canonico x equipamento canonico). Carga
nunca e compartilhada entre trilhas; trocar de exercicio dentro do grupo
preserva a fase e coloca a trilha nova em calibracao.

D2 revista no Lote 4/P6 (2026-10-11, decisao do usuario): o PLATO que sobe a
carga e da TRILHA (2 sessoes QUALIFY dela em >= 7 dias), sem esperar os outros
exercicios do grupo; a fase continua do grupo e a F2 -> F3 sai quando a
maioria dos EXERCICIOS (Lote 7/D2) efetivou o aumento
(`group_load_phase_complete`). As
acoes de fase sem carga (faixa, tecnica, serie) seguem pelo plato do grupo.

Fronteiras que este modulo NAO atravessa:

- **Intensidade** sai so de `domain/training_safety.py` (`compose`,
  `validate_technique`). Aqui nao existe clamp nem ceiling proprio.
- **Recuperacao** entra so pelo contrato da secao 5.8 (`ReadinessContract`,
  montado a partir de `intelligence/recovery_projection.py`). Nenhum sinal
  bruto de dor/performance e lido: o que existe vai para `evidence`.
- **`is_advanced_user`** entra APENAS na elegibilidade de tecnica (D11), nunca
  em seguranca.
- **`set_type`** e classificado so por `app/set_types.py` (o `dropset` legado
  nunca foi normalizado no banco).

Tudo acima de "Avaliador" e funcao PURA: sem banco, sem FastAPI, sem relogio
implicito (`now` sempre e parametro).

Diferencas de interpretacao em relacao ao texto do plano, registradas tambem
em docs/refatoracao-motores/PLANO_20260912.md:

1. O pseudocodigo da 5.11 guarda a regressao (D10) em
   `track.pending_load_action`, mas a secao 8.2 nao cria essa coluna em
   `progression_tracks`. Aqui a regressao vira
   `GroupState.pending_action = DECREASE_LOAD` com
   `pending_payload = {"track_id": ...}` (a fase NAO muda, como a 5.6 exige),
   o que cabe no schema aprovado.
2. O teto de ativacao semanal do grupo (`blocked_by=ACTIVATION_MAX`, F6) e um
   PARAMETRO de entrada (`activation_max_reached`). Desde a Fase 5 (PR 6) o
   avaliador o calcula por sessao (`intelligence/progression_activation.py`):
   series equivalentes REALIZADAS na janela de 7 dias (trabalho + fatias de
   tecnica, `technique_accounting`) contra o TETO da faixa do macro-grupo
   (`methodology_volume`). Sem o calculo (chamador que nao o entrega) ele
   segue `False`, como antes. Pelo mesmo calculo, grupo ja DENTRO da faixa
   (`volume_in_range`) nao recebe `ADD_SET`: vai para a F6 com
   `blocked_by=VOLUME_IN_RANGE` (a F5 so roda abaixo da faixa).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..set_types import (
    BACKOFF,
    is_linked_application,
    is_preparatory_set,
    is_technique_application,
    is_working_set,
    normalize_set_type,
)
from .technique_accounting import (
    SET_TYPE_BY_CODE,
    TechniqueCoefficients,
    is_sets_only_technique,
    planned_equivalent,
)
from .training_goal import DEFAULT_GOAL, TrainingGoal, goal_value, read_goal
from .session_loads import (
    DEFAULT_LOAD_STEP_KG,
    DROP_SET_LOAD_FACTOR,
    load_step,
    round_to_step,
    technique_application_loads,
)
from .prior_fatigue import PriorFatigueBand
from .recovery_signal_summary import RECOVERY_SIGNALS_KEY, summarize_signals
from .set_measures import REASON_MEASURE_NOT_REP_BASED as _REASON_MEASURE_NOT_REP_BASED
from .set_measures import has_non_rep_based
from .training_history import ExerciseExecution, SetRecord
from .training_safety import (
    DEFAULT_TARGET_RPE,
    FAILURE_DENIED_BY_PAIR,
    FAILURE_DENIED_BY_RECOVERY,
    NO_DIRECT_WORK,
    READY,
    Readiness,
    TrainingSafety,
    compose,
    protocol_target_rpe,
    safety_for_goal,
    validate_technique,
)

ENGINE_VERSION = "progression-v2.0.0"

# --- fases -----------------------------------------------------------------
F1, F2, F3, F4, F5, F6 = "F1", "F2", "F3", "F4", "F5", "F6"
PHASES = (F1, F2, F3, F4, F5, F6)
# Fases em que a tecnica escolhida pela F4 continua valendo (secao 5.6: F4
# aplica, F5 e F6 seguem "com tecnica").
TECHNIQUE_PHASES = (F4, F5, F6)

PHASE_ACTIVE = "ACTIVE"
PHASE_SKIPPED_NO_ELIGIBLE_TECHNIQUE = "SKIPPED_NO_ELIGIBLE_TECHNIQUE"
PHASE_SKIPPED_LOAD_NOT_APPLICABLE = "SKIPPED_LOAD_NOT_APPLICABLE"
PHASE_BLOCKED_ACTIVATION_MAX = "BLOCKED_ACTIVATION_MAX"

# --- acoes / decisoes ------------------------------------------------------
SET_REP_RANGE_10_12 = "SET_REP_RANGE_10_12"
INCREASE_LOAD = "INCREASE_LOAD"
APPLY_TECHNIQUE = "APPLY_TECHNIQUE"
ADD_SET = "ADD_SET"
DECREASE_LOAD = "DECREASE_LOAD"
HOLD = "HOLD"
SKIP = "SKIP"

# --- blocked_by ------------------------------------------------------------
BLOCKED_LOAD_NOT_APPLICABLE = "LOAD_NOT_APPLICABLE"
BLOCKED_NO_ELIGIBLE_TECHNIQUE = "NO_ELIGIBLE_TECHNIQUE"
BLOCKED_MODEL_FIXED_SETS = "MODEL_FIXED_SETS"
BLOCKED_ACTIVATION_MAX = "ACTIVATION_MAX"
# Fase 5, PR 6: a F5 so soma serie a grupo ABAIXO da faixa (trabalho +
# equivalentes de tecnica realizados); ja dentro dela a progressao segue pela
# carga (F6).
BLOCKED_VOLUME_IN_RANGE = "VOLUME_IN_RANGE"
BLOCKED_NEXT_ELIGIBLE_AT = "NEXT_ELIGIBLE_PROGRESSION_AT"
BLOCKED_NO_DIRECT_WORK = "NO_DIRECT_WORK"
BLOCKED_MAX_SETS = "MAX_SETS_REACHED"

# --- status de carga da trilha ---------------------------------------------
CALIBRATION = "CALIBRATION"
ACTIVE = "ACTIVE"
LOAD_NOT_APPLICABLE = "LOAD_NOT_APPLICABLE"

# --- resultado de uma sessao ----------------------------------------------
QUALIFY = "QUALIFY"
NEUTRAL = "NEUTRAL"
RESET = "RESET"

# --- evidencias / eventos --------------------------------------------------
REASON_TRACK_IN_CALIBRATION = "track_in_calibration"
REASON_PAIN_REPORTED = "pain_reported"
REASON_NO_WORKING_SETS = "no_working_sets"
# Lote T3 (medidas de serie): alguma serie de trabalho por tempo ou por
# maximo de reps - a sessao e NEUTRA (reexportado do dono
# `domain/set_measures.py`).
REASON_MEASURE_NOT_REP_BASED = _REASON_MEASURE_NOT_REP_BASED
REASON_RPE_MISSING = "rpe_missing"
REASON_SETS_BELOW_DOSE = "sets_below_prescribed_dose"
REASON_LOAD_BELOW_TRACK = "load_below_track"
# "ceiling" aqui era o TOPO DA FAIXA DE REPS (reps_max), nunca o teto de RPE:
# o nome antigo (`reps_below_ceiling`) colidia com o teto de seguranca.
REASON_REPS_BELOW_MAX = "reps_below_reps_max"
# Lote 3/P4 (2026-10-11): alguma serie de trabalho abaixo do PISO da faixa. O
# topo (`reps_below_reps_max`) passou a ser cobrado so da MELHOR serie na carga
# de topo; o piso continua valendo para TODAS.
REASON_REPS_BELOW_MIN = "reps_below_reps_min"
# antes `rpe_below_ceiling`, quando a exigencia era o teto de seguranca
REASON_RPE_BELOW_TARGET = "rpe_below_target"
# Lote 2/P7 (2026-10-11): a ficha congelada daquele dia mandou NAO treinar o
# item (decisao `SKIP`, grupo sem trabalho direto pela recuperacao, ou 0
# series) e ele foi treinado mesmo assim: a sessao nao e avaliavel contra a
# ficha e fica NEUTRA.
REASON_PRESCRIPTION_SKIP = "prescription_skip"

EVIDENCE_UNKNOWN_SET_TYPE = "unknown_set_type"
# a trilha adotou a carga que a pessoa de fato executou (secao 5.3 + 5.10):
# o motor nunca prescreve menos do que ela acabou de levantar
EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION = "load_adopted_from_execution"
# origem da carga adotada, gravada na evidencia (Lote 3/P5: a carga de TOPO
# que cumpriu o piso, `session_working_load`; antes "working_sets_min")
LOAD_ADOPTION_SOURCE = "top_working_set_meeting_floor"
EVIDENCE_INCREMENT_EXCEEDS_RULE = "increment_exceeds_rule"
EVIDENCE_TECHNIQUE_APPLIED = "technique_applied"
EVIDENCE_TRANSITION_DEFERRED = "transition_deferred"
# RPE 10 Lote 6/P9: resumo dos sinais de recuperacao (`recovery_signal_summary`)
# que a prescricao congela em `prescription_json.recovery_signals`.
EVIDENCE_RECOVERY_SIGNALS = RECOVERY_SIGNALS_KEY
EVIDENCE_TECHNIQUE_OVERRIDDEN_BY_MODEL = "TECHNIQUE_OVERRIDDEN_BY_MODEL"
# Lote 3/P4 (2026-10-11): a trilha subiu um passo de carga pela regra de
# excesso de repeticoes (`EXCESS_REPS_FAST_TRACK`), sem esperar o plato do grupo
EVIDENCE_EXCESS_REPS_FAST_TRACK = "excess_reps_fast_track"
# Lote 4/P6 (2026-10-11): aumento de carga POR PLATO DA TRILHA - armado,
# executado ou expirado nesta sessao (mesmo formato da regra de excesso)
EVIDENCE_TRACK_PLATEAU_LOAD = "track_plateau_load_increase"
# Lote 4/P6: plato de cada trilha executada na sessao (fase de carga), para a
# auditoria da secao 14 ("por que subiu / por que nao subiu")
EVIDENCE_TRACK_PLATEAUS = "track_plateaus"
# Lote 4/P6: `INCREASE_LOAD` DO GRUPO gravado antes do Lote 4 sai da fila sem
# promover a fase - o aumento agora e por trilha (o plato da trilha rearma)
EVIDENCE_GROUP_INCREASE_BY_TRACK = "group_increase_load_moved_to_tracks"
# Lote 4/P6: F2 -> F3 pela maioria (quantos efetivaram / de quantos; unidade =
# exercicio desde o Lote 7/D2)
EVIDENCE_GROUP_PHASE_ADVANCE = "group_phase_advance_by_tracks"
SAFETY_EVENT_RPE_ABOVE_CEILING = "RPE_ABOVE_SAFE_CEILING"
# Lote 1/P1 (investigacao RPE 10, 2026-10-11): RPE 10 num par que PERMITE a
# falha, quando so a recuperacao do dia a negou. INFORMATIVO: nao e laranja,
# nao vence os outros status. Cabe no VARCHAR(40) de `exercise_sets.safety_event`.
SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET = "RPE_ABOVE_RECOVERY_TARGET"
# Eventos que sao alerta de SEGURANCA (os demais sao informativos).
SAFETY_ALERT_EVENTS = frozenset({SAFETY_EVENT_RPE_ABOVE_CEILING})

# --- intensity_status (secao 5.7) -----------------------------------------
INTENSITY_REACHED = "REACHED"
INTENSITY_BELOW_TARGET = "BELOW_TARGET"
INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING = "FAILURE_NOT_ALLOWED_REACHED_CEILING"
INTENSITY_ABOVE_SAFE_CEILING = "ABOVE_SAFE_CEILING"

# --- posicao das reps de UMA serie contra a faixa (set-eval-v5, 2026-10-12) --
# O status de intensidade acima avalia SO o esforco (RPE x alvo). A posicao
# das repeticoes contra a faixa da ficha/decisao vai ao lado, para nenhum
# consumidor (prompt da IA por serie, card do Modo Treino) ler "esforco
# atingido" como "alvo geral atingido" (`set_reps_position`).
REPS_BELOW_FLOOR = "BELOW_FLOOR"  # reps < reps_min
REPS_IN_RANGE = "IN_RANGE"  # reps_min <= reps < reps_max
REPS_AT_TOP = "AT_TOP"  # reps == reps_max
REPS_ABOVE_TOP = "ABOVE_TOP"  # reps > reps_max

# --- constantes numericas -------------------------------------------------
F1_REPS = (6, 8)
PROGRESSED_REPS = (10, 12)  # D1: a partir da F2 a faixa NAO volta a mudar
DEFAULT_SETS_COUNT = 3
MAX_SETS_COUNT = 4
PLATEAU_MIN_SESSIONS = 2
PLATEAU_MIN_SPAN_DAYS = 7
F6_LOAD_INTERVAL_DAYS = 14
GROUP_EFFECTIVATION_DAYS = 14
REGRESSION_SESSIONS = 2
# M77 (2026-10-11): adocao da carga executada com TODAS as `working` abaixo de
# `reps_min` (ver `adopted_working_load`). Adota quando a MELHOR serie de
# trabalho chegou perto do piso - no maximo `..._MAX_SHORTFALL_REPS` abaixo
# dele e nunca menos que `..._MIN_REPS` repeticoes. Abaixo disso a sessao e
# teste de carga (1-4 reps, territorio de 1RM) ou carga claramente atipica
# para a faixa, e a trilha continua na carga dela.
BELOW_FLOOR_ADOPTION_MAX_SHORTFALL_REPS = 3
BELOW_FLOOR_ADOPTION_MIN_REPS = 5
BLOCKING_MUSCLE_PAIN = 4  # D5: >= 4 bloqueia
# Lote 3/P4 (2026-10-11, decisao do usuario): regra de EXCESSO de repeticoes.
# Sessao qualificante cuja MELHOR serie na carga de topo fez
# `reps_max + EXCESS_REPS_FAST_TRACK` ou mais, no RPE alvo (RPE dessa serie >=
# alvo efetivo da serie cobrada), arma um aumento de carga DA TRILHA (um passo,
# `load_increment`) para a proxima ficha, sem esperar o plato do grupo; com a
# carga nova, a faixa volta ao piso. Nao promove a fase do grupo.
EXCESS_REPS_FAST_TRACK = 3
# Situacao de uma acao de carga pendente da TRILHA depois de uma execucao
# (`track_pending_load_status`).
PENDING_LOAD_APPLIED = "applied"
PENDING_LOAD_EXPIRED = "expired"
# Lote 4/P6 (2026-10-11, decisao do usuario, reabre a D2): o PLATO que sobe a
# carga e POR TRILHA. Nas fases de carga (`TRACK_LOAD_PHASES`), cada trilha
# com `PLATEAU_MIN_SESSIONS` sessoes QUALIFY consecutivas DELA em
# >= `PLATEAU_MIN_SPAN_DAYS` arma o proprio aumento (`arm_track_plateau_load`),
# sem esperar os outros exercicios do grupo; NEUTRAL/RESET de um exercicio nao
# zera a sequencia dos outros. O aumento usa o MESMO mecanismo da regra de
# excesso (`pending_load_kg`, migracao 071), com a origem em
# `pending_load_source` (migracao 072).
TRACK_LOAD_PHASES = (F2, F6)
# Origem do aumento pendente da trilha. NULL (linha anterior a 072) = excesso,
# a unica origem que existia.
PENDING_LOAD_SOURCE_EXCESS = "excess_reps"
PENDING_LOAD_SOURCE_PLATEAU = "track_plateau"
# A FASE continua do grupo: F2 -> F3 quando MAIS DA METADE (estritamente) das
# exercicios com trilha ativa de carga do grupo efetivou o aumento POR PLATO
# desde a entrada na fase (`group_load_phase_complete`). 1 exercicio: 1/1; 2: os dois;
# 3: duas; 4: tres. O aumento pela regra de excesso nao conta (Lote 3: ele nao
# promove a fase) - so o aumento por plato da trilha.
GROUP_PHASE_ADVANCE_TRACK_SHARE = 0.5
# Lote 7/D2 (2026-10-11, decisao do usuario): a unidade da maioria acima e o
# EXERCICIO (`TrackState.exercise_id`), nao a trilha. Contextos de fadiga e
# equipamentos do mesmo exercicio contam UMA vez: o exercicio conta como
# efetivado quando QUALQUER trilha dele efetivou o aumento por plato na fase.
# Lote 7/D1 (2026-10-11, decisao do usuario): a carga da sessao exige que a
# carga de TOPO se REPITA. Carga L so vale se as series de trabalho com carga
# >= L forem pelo menos esta fracao (arredondada para cima) das series de
# trabalho com carga - 4 series: 2; 3 series: 2 - e ao menos uma delas cumpriu
# o piso (`session_working_load`). Uma serie pesada isolada nao vira a carga
# da trilha.
TOP_LOAD_MIN_SHARE_OF_WORKING_SETS = 0.5
# Dose reduzida: dono e `MuscleRecoveryService.reduced_sets` (M76 B2).
# DEFAULT_LOAD_STEP_KG: dono e `domain/session_loads.py` (importado acima).
COMPOUND_BASE_INCREMENT_KG = Decimal("5")
ISOLATION_BASE_INCREMENT_KG = Decimal("2.5")
INCREMENT_RULE_MAX_KG = Decimal("5")
FAILURE_RPE_VALUE = Decimal("10")  # RPE 10 = falha concentrica

# --- exigencia de esforco do platô (D4 revisada em 2026-09-18) -------------
# `equipment.max_safe_rpe` e TETO DE SEGURANCA: corta o alvo para baixo e nunca
# exige nada. A exigencia e o RPE ALVO PRESCRITO, depois do clamp de
# `training_safety.compose` (`alvo_efetivo = min(alvo_prescrito, ceiling)`).
# Fonte UNICA do valor: `training_safety.DEFAULT_TARGET_RPE`, o mesmo default
# que `compose(prescribed=None)` usa - sem ele os dois modulos divergiriam
# (o alvo da ficha viraria o teto e a cobranca continuaria em 8).
DEFAULT_EFFORT_TARGET_RPE = Decimal(DEFAULT_TARGET_RPE)  # sessao sem alvo prescrito
EFFORT_SCOPE_LAST_WORKING_SET = "LAST_WORKING_SET"
EFFORT_SCOPE_ALL_WORKING_SETS = "ALL_WORKING_SETS"
# Trocar para `EFFORT_SCOPE_ALL_WORKING_SETS` e a UNICA linha necessaria para
# voltar a exigir o alvo em todas as series de trabalho.
EFFORT_TARGET_SCOPE = EFFORT_SCOPE_LAST_WORKING_SET

# Ordem deterministica da F4 (secao 5.9): rest_pause vence drop_set.
TECHNIQUE_PRIORITY = ("rest_pause", "drop_set")
EXPERIENCE_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


# ---------------------------------------------------------------------------
# Escada por OBJETIVO de treino (M97 F2, 2026-10-25; mapa §66.2)
# ---------------------------------------------------------------------------
# HIPERTROFIA (default) e a escada de sempre (F1 6-8 -> F2 10-12 com carga por
# trilha -> F3/F4 tecnica -> F5 serie -> F6), com as constantes acima: nenhuma
# linha do ramo muda. FORCA e uma escada nova, de UMA fase de carga (RAG de
# forca, consolidado no plano do M97: 3-5 reps, RIR 1-3, 2-3 series, tecnicas
# metabolicas desaconselhadas):
#
# - o grupo nasce e fica na fase de carga (`STRENGTH_LOAD_PHASE`) com a faixa
#   de forca `STRENGTH_REPS`; nao ha troca de faixa (F1 -> F2), tecnica
#   (F3/F4) nem serie extra (F5): a progressao e DUPLA dentro da faixa - a
#   sessao QUALIFICA quando todas as series de trabalho batem o piso, a melhor
#   na carga de topo bate o teto e a serie cobrada fica com RPE <= alvo
#   (sobrou reserva); `STRENGTH_PLATEAU_MIN_SESSIONS` sessoes assim (em
#   >= `STRENGTH_PLATEAU_MIN_SPAN_DAYS` dias) armam o aumento DA TRILHA com os
#   incrementos de sempre (`load_increment`: composto 5 kg, isolador 2,5 kg);
# - platô de forca (ESTAGNACAO): `STRENGTH_STALL_SESSIONS` sessoes avaliadas
#   seguidas sem qualificar NA MESMA carga da trilha -> a trilha recua para
#   `STRENGTH_PLATEAU_RESET_FACTOR` x carga (arredondada ao passo do equipamento,
#   ao menos um passo abaixo) e reconstroi a partir dali
#   (`apply_strength_plateau_reset`). Substitui a regressao D10 no ramo de forca.
#   E o RECUO DE PLATO (`plateau_reset`) da trilha, NAO o deload (periodo/sessao
#   de deload, dono futuro `domain/deload.py`);
# - series: no Modo A (series da progressao) o teto e
#   `STRENGTH_MAX_SETS_COUNT`; no Modo B o Modelo manda (D9).
#
# Valores a calibrar pelo usuario (PR da F2, secao "Valores a calibrar").
STRENGTH_LADDER_VERSION = "strength-v1"
# `engine_version` das decisoes do ramo de forca (VARCHAR(40) em
# `progression_decisions`); o de hipertrofia continua `ENGINE_VERSION`.
STRENGTH_ENGINE_VERSION = f"{ENGINE_VERSION}+{STRENGTH_LADDER_VERSION}"
STRENGTH_REPS = (3, 5)
STRENGTH_LOAD_PHASE = F2
STRENGTH_MAX_SETS_COUNT = 3
STRENGTH_PLATEAU_MIN_SESSIONS = 1
STRENGTH_PLATEAU_MIN_SPAN_DAYS = 0
STRENGTH_STALL_SESSIONS = 3
STRENGTH_PLATEAU_RESET_FACTOR = Decimal("0.90")
# Carga de FORCA por %1RM (decisao do usuario, 2026-10-26; mapa §66.5):
# com e1RM CONFIAVEL e ATUAL do par (chave fisica, todos os objetivos, sem
# deload: `exposure_service.current_e1rm_by_pair`), toda prescricao de forca
# usa a faixa `STRENGTH_LOAD_PCT_1RM_MIN`-`STRENGTH_LOAD_PCT_1RM_MAX` do e1RM,
# no passo do equipamento (piso arredondado PARA CIMA e teto PARA BAIXO: a
# faixa exibida nunca sai dos percentuais), com UM valor sugerido: o ponto da
# faixa em `STRENGTH_LOAD_SUGGESTED_FRACTION` (0,5 = ponto medio) arredondado
# PARA BAIXO ao passo e mantido dentro da faixa. O e1RM sobe com o usuario e a
# carga acompanha. Sem e1RM confiavel, a progressao DUPLA acima decide a carga
# (carga da trilha). Plato nesse regime: o recuo `STRENGTH_PLATEAU_RESET_FACTOR`
# continua gravado NA TRILHA pelo avaliador (estado da progressao dupla), mas
# so decide a carga quando nao ha e1RM; no regime %1RM um e1RM estagnado
# mantem a carga (o mesmo percentual do mesmo e1RM) e a queda do e1RM a reduz.
# Valores a calibrar pelo usuario (PR do fix M97, "Valores a calibrar").
# Percentuais do e1RM (inteiros, como o usuario os le: "80-85% 1RM").
STRENGTH_LOAD_PCT_1RM_MIN = 80
STRENGTH_LOAD_PCT_1RM_MAX = 85
STRENGTH_LOAD_SUGGESTED_FRACTION = Decimal("0.5")
# Regime de carga gravado na evidencia das decisoes de forca.
LOAD_REGIME_PCT_1RM = "pct_1rm"
LOAD_REGIME_DOUBLE_PROGRESSION = "double_progression"
EVIDENCE_LOAD_REGIME = "load_regime"
EVIDENCE_STRENGTH_E1RM_LOAD = "strength_e1rm_load"
# Evidencia/motivo/bloqueio do ramo de forca.
EVIDENCE_LADDER = "ladder"
EVIDENCE_STRENGTH_PLATEAU_RESET = "strength_plateau_reset"
REASON_RPE_ABOVE_TARGET = "rpe_above_target"
BLOCKED_STRENGTH_LADDER = "STRENGTH_LADDER"


@dataclass(frozen=True)
class GoalLadder:
    """Parametros da escada de progressao de UM objetivo (`ladder_for`)."""

    training_goal: str
    engine_version: str
    initial_phase: str
    initial_reps: tuple[int, int]
    # faixa das fases depois da F1 (hipertrofia: 10-12; forca: a propria)
    progressed_reps: tuple[int, int]
    # F2 -> F3 (tecnica/serie) existe?
    advances_past_load_phase: bool
    max_sets_count: int
    plateau_min_sessions: int
    plateau_min_span_days: int
    # True: a serie cobrada qualifica com RPE <= alvo (forca, sobrou reserva);
    # False: com RPE >= alvo (hipertrofia, D4).
    qualifies_at_or_below_target: bool
    # platô de forca por estagnacao (`apply_strength_plateau_reset`) no lugar da D10
    stall_plateau_reset: bool
    # Faixa de carga em PERCENTUAL do e1RM (piso, teto) quando ha e1RM
    # confiavel; None = a carga e sempre a da trilha (hipertrofia, como sempre).
    load_pct_1rm: Optional[tuple[int, int]] = None

    @property
    def is_default(self) -> bool:
        return self.training_goal == DEFAULT_GOAL.value

    def evidence(self) -> dict[str, Any]:
        """Marca do ramo nas decisoes: so FORA do default (hipertrofia grava a
        evidencia de sempre, byte a byte)."""
        return {} if self.is_default else {EVIDENCE_LADDER: STRENGTH_LADDER_VERSION}


HYPERTROPHY_LADDER = GoalLadder(
    training_goal=TrainingGoal.HYPERTROPHY.value,
    engine_version=ENGINE_VERSION,
    initial_phase=F1,
    initial_reps=F1_REPS,
    progressed_reps=PROGRESSED_REPS,
    advances_past_load_phase=True,
    max_sets_count=MAX_SETS_COUNT,
    plateau_min_sessions=PLATEAU_MIN_SESSIONS,
    plateau_min_span_days=PLATEAU_MIN_SPAN_DAYS,
    qualifies_at_or_below_target=False,
    stall_plateau_reset=False,
)

STRENGTH_LADDER = GoalLadder(
    training_goal=TrainingGoal.STRENGTH.value,
    engine_version=STRENGTH_ENGINE_VERSION,
    initial_phase=STRENGTH_LOAD_PHASE,
    initial_reps=STRENGTH_REPS,
    progressed_reps=STRENGTH_REPS,
    advances_past_load_phase=False,
    max_sets_count=STRENGTH_MAX_SETS_COUNT,
    plateau_min_sessions=STRENGTH_PLATEAU_MIN_SESSIONS,
    plateau_min_span_days=STRENGTH_PLATEAU_MIN_SPAN_DAYS,
    qualifies_at_or_below_target=True,
    stall_plateau_reset=True,
    load_pct_1rm=(STRENGTH_LOAD_PCT_1RM_MIN, STRENGTH_LOAD_PCT_1RM_MAX),
)

_LADDER_BY_GOAL = {TrainingGoal.HYPERTROPHY: HYPERTROPHY_LADDER, TrainingGoal.STRENGTH: STRENGTH_LADDER}


def ladder_for(training_goal: Any = None) -> GoalLadder:
    """Escada do objetivo (leitura tolerante: ausente = hipertrofia)."""
    return _LADDER_BY_GOAL[read_goal(training_goal)]


def uses_e1rm_load(training_goal: Any = None) -> bool:
    """A carga do objetivo sai do e1RM quando ele existe (forca) ou e sempre a
    da trilha (hipertrofia)? Quem le o e1RM so o le quando isto e True."""
    return ladder_for(training_goal).load_pct_1rm is not None


@dataclass(frozen=True)
class E1rmLoadRange:
    """Faixa de carga por %1RM de UMA prescricao (`e1rm_load_range`)."""

    e1rm_kg: float
    # percentuais do e1RM (80 = 80%)
    pct_min: float
    pct_max: float
    load_min_kg: float
    load_max_kg: float
    suggested_kg: float
    step_kg: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "e1rm_kg": self.e1rm_kg,
            "pct_min": self.pct_min,
            "pct_max": self.pct_max,
            "load_min_kg": self.load_min_kg,
            "load_max_kg": self.load_max_kg,
            "suggested_kg": self.suggested_kg,
            "step_kg": self.step_kg,
        }


def e1rm_load_suggested_kg(strength_load: Any) -> Optional[float]:
    """Carga sugerida de um bloco `strength_load` congelado
    (`E1rmLoadRange.to_dict`, no snapshot da ficha); None sem bloco/valor."""
    if not isinstance(strength_load, Mapping):
        return None
    value = strength_load.get("suggested_kg")
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (value / step).to_integral_value(rounding=rounding) * step


def e1rm_load_range(e1rm_kg: Any, step_kg: Any = None, training_goal: Any = None) -> Optional[E1rmLoadRange]:
    """Faixa de carga do objetivo sobre o e1RM (forca: 80-85%), no passo do
    equipamento: piso arredondado PARA CIMA, teto PARA BAIXO (a faixa nunca sai
    dos percentuais); se o passo nao cabe na faixa, piso = teto = o multiplo
    do passo logo ABAIXO do ponto da faixa (nunca acima do teto; minimo um
    passo). Sugerido = ponto em
    `STRENGTH_LOAD_SUGGESTED_FRACTION` da faixa crua, PARA BAIXO ao passo,
    dentro da faixa. None sem e1RM positivo ou num objetivo sem regime %1RM."""
    pct = ladder_for(training_goal).load_pct_1rm
    if pct is None or e1rm_kg is None or isinstance(e1rm_kg, bool):
        return None
    try:
        e1rm_value = Decimal(str(e1rm_kg))
    except (InvalidOperation, ValueError):
        return None
    if not e1rm_value.is_finite() or e1rm_value <= 0:
        return None
    step = load_step(step_kg)
    pct_min, pct_max = pct
    raw_min = e1rm_value * Decimal(pct_min) / 100
    raw_max = e1rm_value * Decimal(pct_max) / 100
    raw_point = raw_min + (raw_max - raw_min) * STRENGTH_LOAD_SUGGESTED_FRACTION
    low = _to_step(raw_min, step, ROUND_CEILING)
    high = _to_step(raw_max, step, ROUND_FLOOR)
    if low > high:
        low = high = max(_to_step(raw_point, step, ROUND_FLOOR), step)
    suggested = min(max(_to_step(raw_point, step, ROUND_FLOOR), low), high)
    if suggested <= 0:
        return None
    return E1rmLoadRange(
        e1rm_kg=round(float(e1rm_value), 2),
        pct_min=float(pct_min),
        pct_max=float(pct_max),
        load_min_kg=float(low),
        load_max_kg=float(high),
        suggested_kg=float(suggested),
        step_kg=float(step),
    )


def reps_for_phase(phase: str, training_goal: Any = None) -> tuple[int, int]:
    """Faixa de repeticoes de uma fase no objetivo. Hipertrofia: 6-8 na F1,
    10-12 depois (D1). Forca: a faixa de forca em qualquer fase."""
    ladder = ladder_for(training_goal)
    return ladder.initial_reps if phase == F1 else ladder.progressed_reps


def experience_level(is_advanced_user: Any) -> str:
    """D11: `is_advanced_user` -> nivel de experiencia. Nada mais usa a flag."""
    return "advanced" if bool(is_advanced_user) else "intermediate"


def clamp_sets_count(value: Any, *, default: int = DEFAULT_SETS_COUNT) -> int:
    """Teto de `MAX_SETS_COUNT` para o `sets_count` DA TRILHA, independente da
    ORIGEM do numero: execucao, prescricao da ficha ou linha ja persistida.

    Por que a origem nao importa: `sets_count` e ESTADO DA PROGRESSAO e a
    progressao opera ate 4 series (a F5 para em 4, secao 5.6). O predicado da
    secao 5.4 exige `nº de working >= S`; com `S = 9` (prescricao legada de
    agosto/2026, quando a geracao passava por LLM e o orcamento de grupo do
    modelo virou series de um exercicio) nenhum treino normal de 3-4 series
    qualifica: a trilha vira NEUTRA por `sets_below_prescribed_dose` para
    sempre, sem qualificar, sem zerar sequencia e sem regredir pela D10 - o
    congelamento permanente que o teto da execucao ja tinha resolvido em
    2026-09-19 apenas para o caminho da execucao.

    O excedente da ficha continua sendo volume extra legitimo: a qualificacao
    passa a exigir as 4 series da trilha e series ALEM disso nao atrapalham.
    Nao viola a D9 - o modelo continua mandando em quantas series a FICHA
    prescreve (Modo B monta a prescricao com o `sets_count` do modelo, nao com
    o da trilha); o que este teto limita e so o estado interno da progressao.
    """
    try:
        count = int(value)
    except (TypeError, ValueError):
        return int(default)
    if count < 1:
        return int(default)
    return min(count, MAX_SETS_COUNT)


# ---------------------------------------------------------------------------
# Contratos de entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadinessContract:
    """Contrato de Recuperacao da secao 5.8. A progressao consome SO isto."""

    muscle_group_id: Optional[int] = None
    status: str = READY
    readiness_pct: Optional[float] = None
    max_rpe: int = 10
    allow_failure: bool = True
    volume_multiplier: float = 1.0
    blocking_signals: tuple[Mapping[str, Any], ...] = ()
    projected_for: Optional[str] = None
    signals_policy: Optional[str] = None

    @property
    def readiness(self) -> Readiness:
        """A parte que `training_safety.compose` consome."""
        return Readiness(self.status, int(self.max_rpe), bool(self.allow_failure))

    @property
    def has_blocking_pain(self) -> bool:
        """Dor bloqueante reportada (D5). NULL nao e dor."""
        for signal in self.blocking_signals:
            code = str(signal.get("code") or "").upper()
            if code == "JOINT_PAIN":
                return True
            if code == "MUSCLE_PAIN":
                value = signal.get("value")
                if value is not None and float(value) >= BLOCKING_MUSCLE_PAIN:
                    return True
        return False

    @classmethod
    def from_projection(cls, item: Mapping[str, Any]) -> "ReadinessContract":
        """Monta o contrato a partir de um item de
        `intelligence/recovery_projection.project_recovery_state` (que ja
        chama `MuscleRecoveryService.build_readiness_contract`)."""
        status = str(item.get("projected_readiness_status") or item.get("readiness_status") or READY)
        return cls(
            muscle_group_id=item.get("muscle_group_id"),
            status=status,
            readiness_pct=item.get("projected_recovery_pct", item.get("recovery_pct")),
            max_rpe=int(item.get("max_rpe") or 10),
            allow_failure=bool(item.get("allow_failure")),
            volume_multiplier=float(item.get("volume_multiplier") or 1.0),
            blocking_signals=tuple(item.get("blocking_signals") or ()),
            projected_for=item.get("projected_for"),
            signals_policy=item.get("signals_policy"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "muscle_group_id": self.muscle_group_id,
            "status": self.status,
            "readiness_pct": self.readiness_pct,
            "max_rpe": self.max_rpe,
            "allow_failure": self.allow_failure,
            "volume_multiplier": self.volume_multiplier,
            "blocking_signals": [dict(signal) for signal in self.blocking_signals],
            "projected_for": self.projected_for,
        }


READY_CONTRACT = ReadinessContract()


@dataclass(frozen=True)
class TechniqueCatalogEntry:
    """Linha de `advanced_techniques` que a elegibilidade da F4 usa."""

    code: str
    failure_requirement: str = "failure_not_required"
    auto_applicable: bool = False
    min_experience: str = "intermediate"
    set_type: Optional[str] = None
    is_active: bool = True
    # Coeficientes da 060 (Fase 5): so para o equivalente PLANEJADO que vai
    # em `advanced_technique_parameters`. None = linha nao calibrada.
    activation_set_equivalent: Optional[float] = None
    max_equivalent_per_anchor: Optional[float] = None
    default_sets_count: Optional[int] = None

    def coefficients(self) -> TechniqueCoefficients:
        """A mesma leitura de `technique_accounting.coefficients_for`."""
        cap = self.max_equivalent_per_anchor
        return TechniqueCoefficients(
            code=self.code,
            activation_set_equivalent=float(self.activation_set_equivalent or 0.0),
            max_equivalent_per_anchor=1.0 if cap is None else float(cap),
            default_sets_count=self.default_sets_count,
            set_type=self.set_type or SET_TYPE_BY_CODE.get(self.code),
        )


# ---------------------------------------------------------------------------
# Contexto de fadiga da trilha (PR 2 da fadiga previa, 2026-10-07)
# ---------------------------------------------------------------------------
#
# A trilha e por (exercicio, equipamento, CONTEXTO DE FADIGA): o supino feito
# 1o na segunda (descansado) e o feito 5o na quinta (pre-fatigado) tem
# historico, carga e decisao proprios. O contexto e a faixa do dono
# `domain/prior_fatigue.py` (`context_band`) - sobre a sessao REAL na
# avaliacao (`progression_state` monta) e sobre a posicao PLANEJADA na
# prescricao (`deterministic_prescription`). Nada aqui recalcula fadiga.
#
# Trilha sem historico no contexto pedido: a prescricao parte da trilha do
# MESMO par executada mais recentemente em QUALQUER contexto
# (`resolve_track`, o comportamento de antes), sem fator de desconto; a
# execucao cria a trilha do contexto em CALIBRACAO, que e NEUTRA (secao 5.10)
# - entao essa primeira sessao nao conta para regressao (D10), deload nem
# plato. Da segunda em diante o contexto tem historico proprio.
DEFAULT_FATIGUE_CONTEXT = PriorFatigueBand.RESTED.value


def track_key(
    exercise_id: Any,
    equipment_id: Any,
    fatigue_context: Optional[str] = None,
    training_goal: Any = None,
) -> tuple[int, int, str, str]:
    """Identidade da trilha: `(exercicio, equipamento, contexto de fadiga,
    objetivo)`. M97 F1: o objetivo (`domain/training_goal.py`, leitura
    tolerante: ausente = default) separa a trilha de forca da de hipertrofia
    do mesmo par e contexto."""
    return (
        int(exercise_id),
        int(equipment_id),
        str(fatigue_context or DEFAULT_FATIGUE_CONTEXT),
        goal_value(training_goal),
    )


def track_label(key: Sequence[Any]) -> str:
    """Rotulo da trilha na evidencia (`progression_decisions.evidence_json`):
    `"ex:eq"` no contexto descansado (o formato de sempre), `"ex:eq:ctx"`
    nos outros e, fora do objetivo default (M97 F1), `"ex:eq:ctx:objetivo"`.
    Trilha do objetivo default tem o MESMO rotulo de antes."""
    base = f"{key[0]}:{key[1]}"
    context = key[2] if len(key) > 2 else DEFAULT_FATIGUE_CONTEXT
    goal = goal_value(key[3] if len(key) > 3 else None)
    if goal != DEFAULT_GOAL.value:
        return f"{base}:{context}:{goal}"
    return base if context == DEFAULT_FATIGUE_CONTEXT else f"{base}:{context}"


def track_key_payload(key: Optional[Sequence[Any]]) -> Optional[list[Any]]:
    """Alvo gravado em `pending_payload.track_key`: `[ex, eq, ctx]` no
    objetivo default (o formato de sempre - o estado gravado de hipertrofia
    nao muda) e `[ex, eq, ctx, objetivo]` nos outros (M97 F1)."""
    if not key:
        return None
    values = list(key)
    if len(values) > 3 and goal_value(values[3]) == DEFAULT_GOAL.value:
        values = values[:3]
    return values


def target_matches(raw_target: Any, track: "TrackState") -> bool:
    """O alvo de uma acao pendente (`pending_payload.track_key`) e esta
    trilha? Alvo com 3 ou 4 posicoes compara a trilha inteira (com contexto;
    3 posicoes = objetivo default); alvo legado com 2 (gravado antes do
    contexto) compara so o par."""
    if not isinstance(raw_target, (list, tuple)) or len(raw_target) < 2:
        return False
    try:
        if len(raw_target) >= 3:
            goal = raw_target[3] if len(raw_target) >= 4 else None
            return track_key(raw_target[0], raw_target[1], raw_target[2], goal) == track.key
        return (int(raw_target[0]), int(raw_target[1])) == track.pair
    except (TypeError, ValueError):
        return False


def resolve_track(
    tracks: Mapping[Any, "TrackState"],
    exercise_id: Any,
    equipment_id: Any,
    fatigue_context: Optional[str] = None,
    training_goal: Any = None,
) -> tuple[Optional["TrackState"], bool]:
    """Trilha para PRESCREVER o par no contexto pedido, SO entre as trilhas
    do objetivo pedido (M97 F1; ausente = default): a trilha de forca nunca
    alimenta a prescricao de hipertrofia, nem o contrario.

    - Existe trilha no contexto -> `(trilha, True)` (historico proprio).
    - Nao existe, mas o par tem trilha em outro contexto -> a executada mais
      recentemente, com o contexto PEDIDO e sem `id` -> `(trilha, False)`:
      a prescricao parte da ultima carga de qualquer contexto (decisao do
      usuario, sem fator de desconto) e acoes pendentes com alvo em outro
      contexto nao se aplicam a ela.
    - `fatigue_context=None` (quem prescreve sem posicao planejada: troca de
      equipamento, treino livre, card do dashboard) -> a trilha executada mais
      recentemente do par, no contexto DELA.
    - Par sem trilha (no objetivo) -> `(None, False)`."""
    if equipment_id is None:
        return None, False
    goal = goal_value(training_goal)
    if fatigue_context is not None:
        exact = tracks.get(track_key(exercise_id, equipment_id, fatigue_context, goal))
        if exact is not None:
            return exact, True
    same_pair = [
        track
        for track in tracks.values()
        if track.exercise_id == int(exercise_id)
        and track.equipment_id == int(equipment_id)
        and goal_value(track.training_goal) == goal
    ]
    if not same_pair:
        return None, False
    latest = max(same_pair, key=lambda track: (track.last_executed_at or datetime.min, track.id or 0))
    if fatigue_context is None:
        return latest, True
    # acao pendente da trilha de OUTRO contexto nao se aplica ao contexto pedido
    return (
        replace(
            latest,
            fatigue_context=str(fatigue_context),
            id=None,
            pending_load_kg=None,
            pending_load_since=None,
            pending_load_source=None,
        ),
        False,
    )


@dataclass(frozen=True)
class TrackState:
    """Estado da trilha (`progression_tracks`): o que e fisico do par, POR
    contexto de fadiga previa (`fatigue_context`, ver acima)."""

    exercise_id: int
    equipment_id: int
    muscle_group_id: Optional[int] = None
    id: Optional[int] = None
    load_status: str = CALIBRATION
    working_load_kg: Optional[Decimal] = None
    sets_count: int = DEFAULT_SETS_COUNT
    technique_code: Optional[str] = None
    pending_action_applied_at: Optional[datetime] = None
    last_load_change_at: Optional[datetime] = None
    last_sets_change_at: Optional[datetime] = None
    last_executed_at: Optional[datetime] = None
    # metadados do catalogo, nao persistidos na trilha
    stimulus_weight: float = 0.0
    is_compound: bool = False
    load_increment_kg: Optional[Decimal] = None
    sequence: int = 0
    # faixa de `domain/prior_fatigue.py` (persistida, migracao 068)
    fatigue_context: str = DEFAULT_FATIGUE_CONTEXT
    # instante da sessao que CALIBROU a trilha (migracao 068): ela e as
    # anteriores nao contam no historico de regressao/plato da trilha
    # (`progression_evaluator.counts_for_history`). NULL = trilha anterior a
    # 068 ainda nao reprocessada (comportamento anterior).
    calibrated_at: Optional[datetime] = None
    # Lote 3/P4 (migracao 071): aumento de carga pendente DA TRILHA (regra de
    # excesso de repeticoes): carga alvo e instante em que foi armado. Sai
    # quando a pessoa executa a carga alvo ou depois de
    # `GROUP_EFFECTIVATION_DAYS` sem subir (`track_pending_load_status`).
    pending_load_kg: Optional[Decimal] = None
    pending_load_since: Optional[datetime] = None
    # Lote 4/P6 (migracao 072): ORIGEM do aumento pendente
    # (`PENDING_LOAD_SOURCE_*`; NULL = excesso, legado da 071) e o instante em
    # que a trilha EXECUTOU o ultimo aumento por plato dela - a marca que a
    # fase do grupo conta (`group_load_phase_complete`).
    pending_load_source: Optional[str] = None
    plateau_load_applied_at: Optional[datetime] = None
    # M97 F1 (migracao 093): objetivo de treino da trilha (VALOR de
    # `domain/training_goal.TrainingGoal`; persistido, na chave unica).
    training_goal: str = DEFAULT_GOAL.value

    @property
    def key(self) -> tuple[int, int, str, str]:
        """Identidade da trilha: `(exercise_id, equipment_id, fatigue_context,
        training_goal)` - a mesma de `track_key`."""
        return track_key(self.exercise_id, self.equipment_id, self.fatigue_context, self.training_goal)

    @property
    def pair(self) -> tuple[int, int]:
        """O par fisico (seguranca, alvo da tecnica da F4, equipamento)."""
        return (self.exercise_id, self.equipment_id)

    @property
    def load_applicable(self) -> bool:
        return self.load_status != LOAD_NOT_APPLICABLE

    @property
    def in_calibration(self) -> bool:
        return self.load_status == CALIBRATION


@dataclass(frozen=True)
class GroupState:
    """Estado de fase do grupo (`progression_group_states`), D2."""

    muscle_group_id: int
    id: Optional[int] = None
    phase: str = F1
    phase_status: str = PHASE_ACTIVE
    reps_min: int = F1_REPS[0]
    reps_max: int = F1_REPS[1]
    pending_action: Optional[str] = None
    pending_payload: Optional[Mapping[str, Any]] = None
    phase_entered_at: Optional[datetime] = None
    last_rep_range_change_at: Optional[datetime] = None
    last_load_change_at: Optional[datetime] = None
    last_sets_change_at: Optional[datetime] = None
    technique_applied_at: Optional[datetime] = None
    next_eligible_progression_at: Optional[datetime] = None
    # M55 (migracao 063): par (exercise_id, equipment_id) da trilha que a F4
    # escolheu como ALVO do `APPLY_TECHNIQUE`, gravado na efetivacao. NULL =
    # grupo que nunca efetivou a F4, ou que a efetivou antes da 063 (legado:
    # sem alvo conhecido, vale o comportamento anterior).
    technique_target_key: Optional[tuple[int, int]] = None
    # M97 F1 (migracao 093): objetivo de treino do estado do grupo (VALOR de
    # `domain/training_goal.TrainingGoal`; na chave unica `uk_pgs_user_group`).
    # A fase de um objetivo nunca e avaliada com sessoes do outro
    # (`progression_evaluator.evaluate_session_by_goal`).
    training_goal: str = DEFAULT_GOAL.value

    @property
    def reps_range(self) -> tuple[int, int]:
        return (self.reps_min, self.reps_max)

    @property
    def key(self) -> tuple[int, str]:
        """Identidade do estado: `(muscle_group_id, training_goal)`."""
        return (int(self.muscle_group_id), goal_value(self.training_goal))


def new_group_state(muscle_group_id: int, now: datetime, training_goal: Any = None) -> GroupState:
    """Grupo sem estado nasce em F1 com faixa 6-8 (secao 5.10), no objetivo
    pedido (ausente = default). M97 F2: forca nasce na fase de carga da escada
    de forca, com a faixa de forca (`ladder_for`)."""
    ladder = ladder_for(training_goal)
    return GroupState(
        muscle_group_id=muscle_group_id,
        phase=ladder.initial_phase,
        reps_min=ladder.initial_reps[0],
        reps_max=ladder.initial_reps[1],
        phase_entered_at=now,
        training_goal=goal_value(training_goal),
    )


def empty_group_state(training_goal: Any = None) -> GroupState:
    """Estado de grupo sem grupo conhecido (par sem grupo PRIMARY nem grupo do
    Modelo), na fase/faixa inicial do objetivo. Hipertrofia = o
    `GroupState(muscle_group_id=0)` de sempre."""
    ladder = ladder_for(training_goal)
    return GroupState(
        muscle_group_id=0,
        phase=ladder.initial_phase,
        reps_min=ladder.initial_reps[0],
        reps_max=ladder.initial_reps[1],
        training_goal=goal_value(training_goal),
    )


# ---------------------------------------------------------------------------
# 5.3 Classificacao de series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetClassification:
    working: tuple[SetRecord, ...] = ()
    technique: tuple[SetRecord, ...] = ()
    preparatory: tuple[SetRecord, ...] = ()
    unknown: tuple[SetRecord, ...] = ()
    # Fase 5, PR 6: `backoff` e serie de TRABALHO reduzida (vocabulario de
    # `set_types`). Conta como trabalho (volume, dor), mas fica fora de
    # `working`: carga base, reps, esforco e dose seguem so nas series de
    # trabalho na carga da trilha - uma backoff mais leve nao pode derrubar o
    # minimo da sessao nem zerar a sequencia do platô.
    backoff: tuple[SetRecord, ...] = ()

    @property
    def has_unknown(self) -> bool:
        return bool(self.unknown)

    @property
    def work(self) -> tuple[SetRecord, ...]:
        """Todas as series de trabalho: `working` + `backoff`."""
        return self.working + self.backoff


def classify_sets(sets: Iterable[SetRecord]) -> SetClassification:
    """Secao 5.3. `working` conta; `warmup`/`feeler` ignora; aplicacoes de
    tecnica (`rest_pause`/`drop_set`/`forced_rep`, incluindo o `dropset`
    legado, e - Fase 5 - qualquer linha com `technique_code` + ancora) sao
    evidencia de tecnica e NUNCA carga base (bug G2); `backoff` e trabalho
    reduzido (bucket proprio); o resto e desconhecido e gera evidencia."""
    working: list[SetRecord] = []
    technique: list[SetRecord] = []
    preparatory: list[SetRecord] = []
    unknown: list[SetRecord] = []
    backoff: list[SetRecord] = []
    for item in sets or ():
        set_type = normalize_set_type(item.set_type)
        # o registro devolvido ja sai normalizado: nenhum consumidor volta a
        # ver `dropset` (ou variantes de caixa) depois desta funcao
        item = item if item.set_type == set_type else replace(item, set_type=set_type)
        if is_preparatory_set(set_type):
            preparatory.append(item)
        elif is_technique_application(set_type) or is_linked_application(item):
            technique.append(item)
        elif is_working_set(set_type):
            working.append(item)
        elif set_type == BACKOFF:
            backoff.append(item)
        else:
            unknown.append(item)
    return SetClassification(tuple(working), tuple(technique), tuple(preparatory), tuple(unknown), tuple(backoff))


def top_load_min_sets(working_sets: int) -> int:
    """Lote 7/D1: quantas series de trabalho precisam estar na carga de topo
    (ou acima) para ela valer como carga da sessao:
    `ceil(n * TOP_LOAD_MIN_SHARE_OF_WORKING_SETS)`, no minimo 1."""
    return max(1, math.ceil(int(working_sets) * TOP_LOAD_MIN_SHARE_OF_WORKING_SETS))


def session_working_load(working: Sequence[SetRecord], reps_min: Optional[int] = None) -> Optional[Decimal]:
    """Carga executada da sessao: a MAIOR carga L tal que (Lote 7/D1,
    2026-10-11, decisao do usuario)

    (a) as series de trabalho com carga >= L sao pelo menos
        `top_load_min_sets(n)` das `n` series de trabalho com carga - a carga
        de topo se REPETE (uma serie pesada isolada nao vira a carga); e
    (b) ao menos uma serie com carga >= L cumpriu o piso (`reps >= reps_min`)
        - Lote 3/P5: a carga que a pessoa de fato sustenta na faixa.

    As duas condicoes sao monotonas (L menor so inclui mais series), entao L
    e o MENOR entre a maior carga que cumpriu o piso (b) e a k-esima maior
    carga, k = `top_load_min_sets(n)` (a). Back-off mais leve nao puxa a carga
    para baixo. Ex. (piso 10): 16x50, 13x55, 9x55, 6x50 -> 55;
    12x35, 12x35, 12x45 -> 35; 10x40, 10x40, 8x45, 8x45 -> 40.

    Fallback (nenhuma serie cumpriu o piso, ou `reps_min` desconhecido): o
    MINIMO das cargas (o conservador da secao 5.3, anterior ao Lote 3); a
    adocao desse caso continua guardada pelo M77
    (`below_floor_load_is_working`). Series sem carga nao entram."""
    loaded = [item for item in working if item.weight_kg is not None]
    if not loaded:
        return None
    if reps_min is not None:
        floor_met = [item.weight_kg for item in loaded if item.reps is not None and int(item.reps) >= int(reps_min)]
        if floor_met:
            ranked = sorted((Decimal(item.weight_kg) for item in loaded), reverse=True)
            repeated = ranked[top_load_min_sets(len(ranked)) - 1]
            return min(Decimal(max(floor_met)), repeated)
    return min(item.weight_kg for item in loaded)


def top_load_sets(working: Sequence[SetRecord], load: Optional[Decimal]) -> tuple[SetRecord, ...]:
    """Series na carga de TOPO da sessao (`session_working_load`) ou acima -
    as que provam a carga (Lote 3/P5). Sem carga (peso corporal): todas."""
    if load is None:
        return tuple(working)
    return tuple(item for item in working if item.weight_kg is not None and Decimal(item.weight_kg) >= Decimal(load))


def best_set(sets: Sequence[SetRecord]) -> Optional[SetRecord]:
    """MELHOR serie = a de mais repeticoes (empate: a primeira executada)."""
    best: Optional[SetRecord] = None
    for item in sets:
        if item.reps is not None and (best is None or int(item.reps) > int(best.reps)):
            best = item
    return best


def excess_reps_reached(best: Optional[SetRecord], *, reps_max: int, target: Decimal) -> bool:
    """Regra de excesso (Lote 3/P4): a melhor serie fez `reps_max +
    EXCESS_REPS_FAST_TRACK` ou mais NO RPE alvo (RPE da propria serie >= alvo
    efetivo da serie cobrada). Sem RPE na melhor serie: nao."""
    if best is None or best.reps is None or best.rpe is None:
        return False
    return int(best.reps) >= int(reps_max) + EXCESS_REPS_FAST_TRACK and Decimal(best.rpe) >= Decimal(target)


def all_below_reps_min(working: Sequence[SetRecord], reps_min: Optional[int]) -> bool:
    """TODAS as `working` abaixo de `reps_min` - o territorio da D10."""
    if reps_min is None or not working:
        return False
    return all(item.reps is not None and int(item.reps) < int(reps_min) for item in working)


def below_floor_load_is_working(working: Sequence[SetRecord], reps_min: Optional[int]) -> bool:
    """Sessao com TODAS as `working` abaixo de `reps_min`: a carga executada e
    carga de TRABALHO (adotavel) ou teste/carga atipica (ignorada)?

    E carga de trabalho quando a melhor serie `working` ficou a no maximo
    `BELOW_FLOOR_ADOPTION_MAX_SHORTFALL_REPS` do piso e fez pelo menos
    `BELOW_FLOOR_ADOPTION_MIN_REPS` repeticoes (80 kg x 8 na faixa 10-12: sim;
    100 kg x 3 na faixa 10-12: nao)."""
    if reps_min is None:
        return False
    reps = [int(item.reps) for item in working if item.reps is not None]
    if not reps:
        return False
    floor = max(int(reps_min) - BELOW_FLOOR_ADOPTION_MAX_SHORTFALL_REPS, BELOW_FLOOR_ADOPTION_MIN_REPS)
    return max(reps) >= floor


def adopted_working_load(
    track: TrackState, working: Sequence[SetRecord], *, reps_min: Optional[int] = None
) -> Optional[Decimal]:
    """Carga executada que a trilha deve ADOTAR nesta sessao, ou None.

    O motor nunca prescreve menos do que a pessoa acabou de levantar sem um
    motivo explicito e auditavel. Quando a carga executada da sessao
    (`session_working_load`: a MAIOR carga de trabalho que cumpriu o piso e
    se repetiu em pelo menos metade das series - Lote 3/P5 e Lote 7/D1; antes
    o MINIMO das `working`) e maior que a carga da trilha, a
    trilha adota a carga executada e registra
    `EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION` na decisao.

    Simetria deliberadamente ausente: **reducao nunca vem da execucao**. Cair
    de carga continua sendo decisao da progressao (`DECREASE_LOAD`, D10, duas
    sessoes consecutivas abaixo de `reps_min`), justamente para nao derrubar a
    carga por um dia ruim.

    TODAS as `working` abaixo de `reps_min` (M77, 2026-10-11): antes a trilha
    nunca adotava ("adotar consolidaria uma carga alta demais"), e isso a
    prendia numa carga antiga muito menor - 80 kg x 8 na faixa 10-12 deixava a
    trilha em 30 kg, e a D10 ainda a REDUZIA a partir dali. Agora a carga
    executada e adotada quando e carga de trabalho
    (`below_floor_load_is_working`); a carga alta demais continua tratada,
    mas a partir da carga REAL: a prescricao seguinte mantem a carga para
    buscar o piso (HOLD) e a D10 reduz um incremento depois de duas sessoes
    abaixo do piso. Teste de carga (poucas repeticoes, longe do piso) segue
    ignorado.

    Nao adota quando:
    - a trilha nao tem carga aplicavel (peso corporal) ou esta em calibracao
      (a calibracao ja define L = minimo das `working`, secao 5.10);
    - a sessao nao tem carga em nenhuma `working` (series de tecnica NUNCA
      viram carga base - bug G2 - porque `working` ja as exclui; aquecimento e
      ajuste sao preparatorias e tambem ficam fora);
    - TODAS as `working` ficaram abaixo de `reps_min` e a melhor delas ficou
      longe do piso (`below_floor_load_is_working` falso): teste ou carga
      atipica.
    """
    if track.in_calibration or not track.load_applicable or track.working_load_kg is None:
        return None
    executed = session_working_load(working, reps_min)
    if executed is None or Decimal(executed) <= Decimal(track.working_load_kg):
        return None
    if all_below_reps_min(working, reps_min) and not below_floor_load_is_working(working, reps_min):
        return None
    return Decimal(executed)


# ---------------------------------------------------------------------------
# 5.4 / 5.7 Sessao qualificante
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackSessionOutcome:
    """Resultado de uma sessao para UMA trilha."""

    key: tuple  # TrackState.key: (exercise_id, equipment_id, fatigue_context, training_goal)
    outcome: str  # QUALIFY | NEUTRAL | RESET
    reasons: tuple[str, ...] = ()
    safety_events: tuple[Mapping[str, Any], ...] = ()
    working_load_kg: Optional[Decimal] = None
    working_sets: int = 0
    intensity_status: Optional[str] = None
    below_reps_min: bool = False
    effort_target_rpe: Optional[Decimal] = None
    # Lote 3/P4: sessao QUALIFY que tambem cumpriu a regra de excesso
    # (`excess_reps_reached`): o avaliador arma o aumento de carga da trilha
    excess_reps: bool = False

    @property
    def reason(self) -> Optional[str]:
        """Primeiro motivo: o que de fato decidiu a sessao (secao 14)."""
        return self.reasons[0] if self.reasons else None

    def to_evidence(self) -> dict[str, Any]:
        """O que vai para `progression_decisions.evidence_json` por trilha:
        veredito E motivo (a secao 14 exige responder "por que bloqueou")."""
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "intensity_status": self.intensity_status,
            "effort_target_rpe": None if self.effort_target_rpe is None else float(self.effort_target_rpe),
            "excess_reps": self.excess_reps,
        }


def effort_target_rpe(
    prescribed_rpe_target: Optional[Any],
    ceiling: int,
    *,
    default: Decimal = DEFAULT_EFFORT_TARGET_RPE,
) -> Decimal:
    """Alvo de esforco efetivo da sessao (D4 revisada em 2026-09-18).

    `alvo_efetivo = min(alvo_prescrito ou DEFAULT_EFFORT_TARGET_RPE, ceiling)`.
    O teto de seguranca so corta para BAIXO: nunca eleva a exigencia."""
    base = default if prescribed_rpe_target is None else Decimal(str(prescribed_rpe_target))
    return min(base, Decimal(ceiling))


def effort_sets(working: Sequence[SetRecord], *, scope: str = "") -> tuple[SetRecord, ...]:
    """Series onde o alvo de RPE e COBRADO (D4 revisada em 2026-09-18).

    Por padrao, so a ULTIMA serie `working` na ordem de execucao: as series
    iniciais ficam abaixo de proposito e o esforco se mede onde ele acontece.
    Aquecimento, ajuste e aplicacoes de tecnica ja ficaram fora de `working`.
    """
    if not working:
        return ()
    if (scope or EFFORT_TARGET_SCOPE) == EFFORT_SCOPE_ALL_WORKING_SETS:
        return tuple(working)
    return (working[-1],)


def _pain_blocks(execution: ExerciseExecution, classification: SetClassification) -> bool:
    """D5: `joint_pain` bloqueia; `muscle_pain >= 4` bloqueia; NULL nao."""
    candidates = [(execution.muscle_pain, execution.joint_pain)]
    candidates += [(item.muscle_pain, item.joint_pain) for item in classification.work + classification.technique]
    for muscle_pain, joint_pain in candidates:
        if joint_pain:
            return True
        if muscle_pain is not None and int(muscle_pain) >= BLOCKING_MUSCLE_PAIN:
            return True
    return False


def safety_event_type_for(failure_denied_by: Optional[str]) -> str:
    """`safety_event` de um RPE 10 com a falha negada, pela ORIGEM da negacao
    (Lote 1/P1): recuperacao -> informativo; par (ou origem desconhecida, o
    lado conservador) -> evento de seguranca."""
    if failure_denied_by == FAILURE_DENIED_BY_RECOVERY:
        return SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET
    return SAFETY_EVENT_RPE_ABOVE_CEILING


def resolved_failure_denial(failure_allowed: bool, failure_denied_by: Optional[str]) -> Optional[str]:
    """Origem efetiva: None com falha permitida; sem origem informada, PAIR
    (comportamento anterior ao Lote 1, conservador)."""
    if failure_allowed:
        return None
    return failure_denied_by or FAILURE_DENIED_BY_PAIR


def intensity_status_for(
    rpe: Optional[Decimal],
    target: Any,
    failure_allowed: bool,
    *,
    highest: Optional[Decimal] = None,
    failure_denied_by: Optional[str] = None,
) -> str:
    """Secao 5.7. `rpe` e o MENOR RPE entre as series cobradas (por padrao, so
    a ultima `working`); `highest`, o maior de todas as `working`. `target` e o
    alvo de esforco efetivo (`effort_target_rpe`), nao o teto de seguranca.

    RPE 10 registrado num par sem falha permitida vale como intensidade
    atingida (clamp LOGICO) e e reportado como `ABOVE_SAFE_CEILING`, que vence
    os outros status: e o que a UI mostra e a metrica conta. Nunca eleva o
    alvo futuro.

    Lote 1/P1 (2026-10-11): so a negacao pelo PAR (`failure_denied_by` PAIR ou
    ausente) gera `ABOVE_SAFE_CEILING`. Negada so pela recuperacao (par com
    falha permitida), o RPE 10 e informativo e a classificacao segue a normal:
    na serie cobrada, RPE >= alvo -> `FAILURE_NOT_ALLOWED_REACHED_CEILING`
    (alvo atingido; a falha nao estava liberada naquele dia), abaixo ->
    `BELOW_TARGET`.
    """
    status = _intensity_status_core(
        rpe, rpe if highest is None else highest, target, failure_allowed, failure_denied_by
    )
    return status if status is not None else INTENSITY_BELOW_TARGET


def _intensity_status_core(
    charged: Optional[Decimal],
    top: Optional[Decimal],
    target: Any,
    failure_allowed: bool,
    failure_denied_by: Optional[str],
) -> Optional[str]:
    """Nucleo UNICO da secao 5.7, usado pela sessao (`intensity_status_for`)
    e pela serie (`set_intensity_status`). `charged` e o RPE cobrado contra o
    alvo; `top`, o maior RPE visto (regra do teto do par). Sem alvo efetivo
    (`target` None: treino livre, so o teto do par) so o evento do par se
    aplica; fora isso, `None` (nao ha alvo a cobrar)."""
    denied_by = resolved_failure_denial(failure_allowed, failure_denied_by)
    if denied_by == FAILURE_DENIED_BY_PAIR and top is not None and Decimal(top) >= FAILURE_RPE_VALUE:
        return INTENSITY_ABOVE_SAFE_CEILING
    if target is None:
        return None
    if charged is None:
        return INTENSITY_BELOW_TARGET
    if Decimal(charged) >= Decimal(str(target)):
        return INTENSITY_REACHED if failure_allowed else INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    return INTENSITY_BELOW_TARGET


def set_intensity_status(
    rpe: Optional[Decimal],
    target: Any,
    failure_allowed: bool,
    *,
    failure_denied_by: Optional[str] = None,
) -> Optional[str]:
    """Status de UMA serie de trabalho contra o alvo efetivo/teto/falha/origem
    (RPE 10 Lote 6/P9, "uma voz no Modo Treino", 2026-10-11).

    Mesma regra da sessao (`_intensity_status_core`), com a serie sendo ao
    mesmo tempo a cobrada e a mais alta: RPE 10 com a falha negada pelo PAR ->
    `ABOVE_SAFE_CEILING` (laranja); negada so pela recuperacao -> status normal
    (o evento informativo fica no `safety_event`); RPE >= alvo -> alvo
    atingido; abaixo -> `BELOW_TARGET`. Sem alvo efetivo (treino livre) e sem
    evento do par -> `None`.

    A sessao continua cobrando o alvo na ULTIMA serie `working` (D4); o status
    por serie e o retrato de cada serie no momento em que foi salva."""
    value = None if rpe is None else Decimal(str(rpe))
    return _intensity_status_core(value, value, target, failure_allowed, failure_denied_by)


def set_effort_target(target: Any, planned_rpe: Any = None) -> Optional[Decimal]:
    """Alvo de esforco cobrado em UMA serie (M96, "veredito unico" no card da
    serie, 2026-10-25): o alvo efetivo do exercicio, restringido pelo degrau
    PLANEJADO da serie quando a ficha e uma rampa (`"8-9-10"`: 8 na 1a, 9 na
    2a, 10 na 3a; degrau por `next_set_load.planned_ramp_rpe`, o mesmo que a
    carga da proxima serie usa). So restringe: o degrau nunca eleva o alvo.

    Antes, a serie 1 de "8-9-10" em RPE 8 saia "Esforco abaixo do alvo" (alvo
    10) ao lado de "RPE dentro da rampa planejada (alvo 8 nesta serie)". A
    sessao continua cobrando o alvo cheio na ULTIMA serie `working` (D4).
    Sem alvo -> None (treino livre)."""
    if target is None:
        return None
    value = Decimal(str(target))
    if planned_rpe is None:
        return value
    try:
        planned = Decimal(str(planned_rpe))
    except (InvalidOperation, ValueError):
        return value
    return min(value, planned)


def set_reps_position(reps: Any, reps_min: Any, reps_max: Any) -> Optional[str]:
    """Onde as repeticoes de UMA serie caem na faixa (set-eval-v5, 2026-10-12):
    `BELOW_FLOOR` (abaixo do piso), `IN_RANGE` (do piso ate antes do teto),
    `AT_TOP` (exatamente no teto) ou `ABOVE_TOP` (acima do teto). Faixa de um
    numero so (`12`) -> piso = teto: abaixo, no teto ou acima.

    Sem reps, sem faixa ou faixa invalida (piso > teto) -> `None` (o
    consumidor omite a informacao). Complementa `set_intensity_status`, que
    olha so o esforco."""
    try:
        value = int(reps)
        low = int(reps_min)
        high = int(reps_max)
    except (TypeError, ValueError):
        return None
    if isinstance(reps, bool) or low > high:
        return None
    if value < low:
        return REPS_BELOW_FLOOR
    if value > high:
        return REPS_ABOVE_TOP
    if value == high:
        return REPS_AT_TOP
    return REPS_IN_RANGE


def prescription_skipped(execution: ExerciseExecution) -> bool:
    """A ficha CONGELADA daquele dia mandou nao treinar o item (Lote 2/P7):
    decisao `SKIP` (grupo sem trabalho direto, `prescribe` com
    `NO_DIRECT_WORK`) ou 0 series. Sem ficha congelada: False."""
    frozen = execution.prescribed.frozen
    if frozen is None:
        return False
    return frozen.decision == SKIP or (frozen.sets_count is not None and int(frozen.sets_count) <= 0)


def required_dose_sets(execution: ExerciseExecution, track: TrackState) -> int:
    """Dose exigida pelo predicado da secao 5.4 (Lote 2/P3, 2026-10-11): as
    series da ficha CONGELADA daquele dia - inclusive a dose reduzida pela
    recuperacao -, limitadas ao teto da trilha (`clamp_sets_count`, a F5 vai
    ate 4). Sem ficha congelada (treino livre, ficha legada), o `sets_count`
    da trilha. Executar >= a dose cumpre; series alem dela nao atrapalham."""
    frozen = execution.prescribed.frozen
    if frozen is not None and frozen.sets_count is not None and int(frozen.sets_count) > 0:
        return clamp_sets_count(frozen.sets_count)
    return int(track.sets_count)


def prescribed_effort_rpe(execution: ExerciseExecution) -> Optional[Any]:
    """Alvo de esforco PRESCRITO da sessao, antes do corte pelo teto (Lote
    2/P7): o alvo efetivo da ficha congelada (ja composto no congelamento com
    seguranca do par e recuperacao projetada - o mesmo que o Modo Treino
    exibiu); sem ficha congelada, o topo da rampa da linha da ficha."""
    frozen = execution.prescribed.frozen
    if frozen is not None and frozen.effective_rpe_target is not None:
        return frozen.effective_rpe_target
    return execution.prescribed.rpe_target


def working_has_non_rep_sets(working: Iterable[SetRecord]) -> bool:
    """Alguma serie de trabalho fora de `reps` (`set_measures.has_non_rep_based`;
    Lote T3). Gatilho unico da sessao NEUTRA (`evaluate_track_session`) e da
    trilha sem adocao/calibracao (`progression_evaluator.update_track_execution`)."""
    return has_non_rep_based(item.measure for item in working)


def evaluate_track_session(
    execution: ExerciseExecution,
    track: TrackState,
    *,
    reps_min: int,
    reps_max: int,
    ceiling: int,
    failure_allowed: bool,
    readiness_status: str = READY,
    effort_scope: str = "",
    failure_denied_by: Optional[str] = None,
) -> TrackSessionOutcome:
    """Predicado unico da secao 5.4, nivel trilha.

    Qualifica quando, na sessao: nº de `working` >= a dose exigida
    (`required_dose_sets`: series da ficha congelada do dia, fallback
    `sets_count` da trilha - Lote 2/P3); a carga de TOPO da sessao
    (`session_working_load`: maior carga que cumpriu o piso e se repetiu -
    Lote 7/D1) >=
    `working_load_kg` - back-off mais leve nao zera (Lote 3/P5; trilha sem
    carga ignora); **todas** as `working` com `reps >= reps_min`
    (`reps_below_reps_min`) e a MELHOR serie na carga de topo com
    `reps >= reps_max` (`reps_below_reps_max`) - Lote 3/P4, decisao do usuario;
    e a exigencia de ESFORCO satisfeita na ULTIMA serie `working`
    (`effort_sets`): `RPE >= min(alvo prescrito, ceiling)`.

    Regra de excesso (Lote 3/P4): QUALIFY cuja melhor serie fez
    `reps_max + EXCESS_REPS_FAST_TRACK` no RPE alvo sai com `excess_reps=True`
    (trilha com carga aplicavel); o avaliador arma o aumento da trilha
    (`arm_excess_reps_fast_track`).

    `ceiling` (teto seguro efetivo do par) NAO e exigencia: so corta o alvo
    para baixo (D4 revisada em 2026-09-18). As series anteriores a ultima nao
    precisam atingir o alvo de RPE - e assim que se treina.

    RPE 10 em qualquer serie, INCLUSIVE antes da ultima, nao bloqueia nem
    penaliza (D4 revisada em 2026-09-14). Em par onde a falha e proibida ele
    conta como intensidade atingida e gera `safety_event`, sem elevar o alvo.

    Lote 2/P7 (2026-10-11): com ficha congelada, o alvo prescrito e o alvo
    efetivo da ficha (`prescribed_effort_rpe`) e `ceiling`/`failure_allowed`
    chegam da ficha (o avaliador ja aplicou o overlay de seguranca de hoje,
    como o Modo Treino); item que a ficha mandou pular fica NEUTRO
    (`REASON_PRESCRIPTION_SKIP`).
    `failure_denied_by` (Lote 1/P1, `compose().failure_denied_by`) define o
    tipo do evento: negada so pela recuperacao -> informativo
    (`RPE_ABOVE_RECOVERY_TARGET`); pelo par ou sem origem -> seguranca.
    """
    classification = classify_sets(execution.sets)
    denied_by = resolved_failure_denial(failure_allowed, failure_denied_by)
    working = classification.working
    reasons: list[str] = []
    safety_events: list[Mapping[str, Any]] = []

    if classification.has_unknown:
        reasons.append(EVIDENCE_UNKNOWN_SET_TYPE)
    if not failure_allowed:
        for item in working:
            if item.rpe is not None and Decimal(item.rpe) >= 10:
                safety_events.append(
                    {
                        "type": safety_event_type_for(denied_by),
                        "set_number": item.set_number,
                        "rpe": float(item.rpe),
                        "ceiling": ceiling,
                        "failure_denied_by": denied_by,
                    }
                )

    load = session_working_load(working, reps_min)
    target = effort_target_rpe(prescribed_effort_rpe(execution), ceiling)
    graded = effort_sets(working, scope=effort_scope)
    reported = [item.rpe for item in working if item.rpe is not None]
    graded_rpe = [item.rpe for item in graded if item.rpe is not None]
    worst_rpe = min(graded_rpe, default=None)
    status = intensity_status_for(
        worst_rpe, target, failure_allowed, highest=max(reported, default=None), failure_denied_by=denied_by
    )
    below_reps_min = all_below_reps_min(working, reps_min)

    def outcome(value: str, reason: Optional[str] = None, *, excess: bool = False) -> TrackSessionOutcome:
        if reason:
            reasons.append(reason)
        return TrackSessionOutcome(
            key=track.key,
            outcome=value,
            reasons=tuple(reasons),
            safety_events=tuple(safety_events),
            working_load_kg=load,
            working_sets=len(working),
            intensity_status=status,
            below_reps_min=below_reps_min,
            effort_target_rpe=target,
            excess_reps=excess,
        )

    # --- sessoes NEUTRAS (nao qualificam e nao zeram a sequencia) ----------
    # Lote T3 (medidas de serie): serie de trabalho por tempo/maximo de reps
    # nao responde a pergunta "bateu reps x carga da trilha?" - NEUTRA antes
    # de qualquer RESET (e antes da calibracao: o avaliador nao adota carga).
    if working_has_non_rep_sets(working):
        return outcome(NEUTRAL, REASON_MEASURE_NOT_REP_BASED)
    if track.in_calibration:
        return outcome(NEUTRAL, REASON_TRACK_IN_CALIBRATION)
    if prescription_skipped(execution):
        return outcome(NEUTRAL, REASON_PRESCRIPTION_SKIP)
    if readiness_status != READY:
        return outcome(NEUTRAL, f"readiness_{readiness_status.lower()}")
    if _pain_blocks(execution, classification):
        return outcome(NEUTRAL, REASON_PAIN_REPORTED)
    if not working:
        return outcome(NEUTRAL, REASON_NO_WORKING_SETS)
    # RPE so e exigido onde ele e COBRADO: uma serie intermediaria sem RPE nao
    # torna a sessao inavaliavel (D4 revisada em 2026-09-18).
    if any(item.rpe is None for item in graded):
        return outcome(NEUTRAL, REASON_RPE_MISSING)
    if len(working) < required_dose_sets(execution, track):
        return outcome(NEUTRAL, REASON_SETS_BELOW_DOSE)

    # --- qualifica? --------------------------------------------------------
    # carga: so a carga de TOPO da sessao conta (Lote 3/P5) - back-off mais
    # leve depois das series na carga da trilha nao zera
    if track.working_load_kg is not None and (load is None or Decimal(load) < Decimal(track.working_load_kg)):
        return outcome(RESET, REASON_LOAD_BELOW_TRACK)
    # reps (Lote 3/P4): piso em TODAS as series; topo so na MELHOR serie na
    # carga de topo
    if any(item.reps is None or int(item.reps) < int(reps_min) for item in working):
        return outcome(RESET, REASON_REPS_BELOW_MIN)
    best = best_set(top_load_sets(working, load))
    if best is None or int(best.reps) < reps_max:
        return outcome(RESET, REASON_REPS_BELOW_MAX)
    # esforco: so nas series cobradas (por padrao, a ultima `working`)
    if ladder_for(track.training_goal).qualifies_at_or_below_target:
        # M97 F2, forca: bateu o topo com RESERVA (RPE <= alvo) - a carga sobe;
        # sem reserva a carga fica. Sem regra de excesso (a progressao dupla de
        # forca ja sobe na sessao que qualifica).
        if any(Decimal(item.rpe) > target for item in graded):
            return outcome(RESET, REASON_RPE_ABOVE_TARGET)
        return outcome(QUALIFY)
    if any(Decimal(item.rpe) < target for item in graded):
        return outcome(RESET, REASON_RPE_BELOW_TARGET)
    excess = (
        track.load_applicable
        and track.working_load_kg is not None
        and excess_reps_reached(best, reps_max=reps_max, target=target)
    )
    return outcome(QUALIFY, excess=excess)


def evaluate_group_session(outcomes: Sequence[TrackSessionOutcome]) -> str:
    """Secao 5.4, nivel grupo (D2): qualifica se TODAS as trilhas nao neutras
    qualificam e existe >= 1 nao neutra; neutra se todas sao neutras; zera se
    qualquer trilha nao neutra deixa de qualificar.

    Lote 4/P6 (2026-10-11): o veredito de GRUPO so decide as acoes de fase que
    nao sao de carga (F1 faixa, F3 tecnica, F4/F5 serie). Nas fases de carga
    (`TRACK_LOAD_PHASES`) o plato e por trilha (`arm_track_plateau_load`) e o
    RESET de um exercicio nao zera a sequencia dos outros."""
    non_neutral = [item for item in outcomes if item.outcome != NEUTRAL]
    if not non_neutral:
        return NEUTRAL
    if any(item.outcome != QUALIFY for item in non_neutral):
        return RESET
    return QUALIFY


# ---------------------------------------------------------------------------
# 5.5 Platô confirmado
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plateau:
    confirmed: bool = False
    qualifying_dates: tuple[date, ...] = ()
    span_days: int = 0


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def plateau_confirmed(
    history: Sequence[tuple[Any, str]],
    *,
    min_sessions: int = PLATEAU_MIN_SESSIONS,
    min_span_days: int = PLATEAU_MIN_SPAN_DAYS,
) -> Plateau:
    """Secao 5.5: >= 2 sessoes qualificantes consecutivas (neutras ignoradas)
    e `data(ultima) - data(primeira) >= 7 dias`.

    A MESMA regra roda sobre dois historicos (Lote 4/P6, 2026-10-11): o da
    TRILHA (`progression_evaluator.track_plateau_history`), que decide o
    aumento de carga nas fases de carga, e o do GRUPO
    (`group_session_history`), que decide as acoes de fase sem carga.

    Duas sessoes no MESMO dia dao span 0 e nao confirmam platô - e o ponto da
    regra, que funciona com qualquer frequencia semanal.
    """
    run: list[date] = []
    for raw_date, outcome in history:
        if outcome == NEUTRAL:
            continue
        if outcome == QUALIFY:
            moment = _as_date(raw_date)
            if moment is not None:
                run.append(moment)
        else:
            run = []
    # M97 F2: `min_sessions`/`min_span_days` da escada do objetivo
    # (`GoalLadder`); o default e a regra de hipertrofia acima.
    if len(run) < min_sessions:
        return Plateau(False, tuple(run), (run[-1] - run[0]).days if len(run) > 1 else 0)
    span = (run[-1] - run[0]).days
    return Plateau(span >= min_span_days, tuple(run), span)


# ---------------------------------------------------------------------------
# 5.6 Incremento de carga
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadIncrement:
    delta_kg: Decimal
    step_kg: Decimal
    evidence: tuple[str, ...] = ()


def load_increment(*, is_compound: bool, load_increment_kg: Any = None) -> LoadIncrement:
    """Secao 5.6 (G) e D12 - tudo em carga TOTAL, nunca por lado:

        base = 5 kg se composto, senao 2,5 kg
        step = equipment.load_increment_kg (NULL -> 2,5)
        delta = max(base, step) arredondado para MULTIPLO de step (para cima)

    `delta > 5` gera evidencia `increment_exceeds_rule` (nao impede).
    """
    step = Decimal(str(load_increment_kg)) if load_increment_kg not in (None, "") else DEFAULT_LOAD_STEP_KG
    if step <= 0:
        step = DEFAULT_LOAD_STEP_KG
    base = COMPOUND_BASE_INCREMENT_KG if is_compound else ISOLATION_BASE_INCREMENT_KG
    raw = max(base, step)
    multiples = (raw / step).to_integral_value(rounding=ROUND_HALF_UP)
    delta = step * (multiples if step * multiples >= raw else multiples + 1)
    evidence = (EVIDENCE_INCREMENT_EXCEEDS_RULE,) if delta > INCREMENT_RULE_MAX_KG else ()
    return LoadIncrement(delta_kg=delta, step_kg=step, evidence=evidence)


def next_load(track: TrackState, *, direction: int = 1) -> tuple[Optional[Decimal], LoadIncrement]:
    """Carga da trilha + (ou -) um incremento. Trilha sem carga -> None."""
    increment = load_increment(is_compound=track.is_compound, load_increment_kg=track.load_increment_kg)
    if track.working_load_kg is None:
        return None, increment
    value = Decimal(track.working_load_kg) + (increment.delta_kg * direction)
    return (value if value > 0 else Decimal(track.working_load_kg)), increment


# ---------------------------------------------------------------------------
# 5.6 Regressao (D10)
# ---------------------------------------------------------------------------


def regression_triggered(outcomes: Sequence[TrackSessionOutcome]) -> bool:
    """D10: 2 sessoes `working` consecutivas nao qualificantes abaixo de
    `reps_min` com a carga L e sem dor (as sessoes com dor sao NEUTRAS e nao
    entram aqui). Substitui o -5% por RPE 10 precoce, removido em 2026-09-14."""
    considered = [item for item in outcomes if item.outcome != NEUTRAL]
    if len(considered) < REGRESSION_SESSIONS:
        return False
    tail = considered[-REGRESSION_SESSIONS:]
    return all(item.outcome != QUALIFY and item.below_reps_min for item in tail)


# ---------------------------------------------------------------------------
# 5.9 Tecnicas
# ---------------------------------------------------------------------------


def eligible_techniques(
    safety: TrainingSafety,
    catalog: Mapping[str, TechniqueCatalogEntry],
    *,
    readiness: ReadinessContract = READY_CONTRACT,
    is_advanced_user: bool = False,
    composed_failure_allowed: Optional[bool] = None,
    ignore_fixed: bool = False,
    track: Optional[TrackState] = None,
) -> tuple[str, ...]:
    """Secao 5.9. Interseccao de: tecnicas permitidas no par x
    `failure_requirement` compativel com a falha efetiva x `auto_applicable`
    x readiness READY e sem dor x `experience >= min_experience` x sem outra
    tecnica ja fixada na trilha. Ordem deterministica: rest_pause > drop_set.

    A validacao TERMINAL e sempre `training_safety.validate_technique` - aqui
    nao se reimplementa nenhuma regra de seguranca.
    """
    if readiness.status != READY or readiness.has_blocking_pain:
        return ()
    if not ignore_fixed and track is not None and track.technique_code:
        return ()
    level = EXPERIENCE_ORDER[experience_level(is_advanced_user)]
    ordered = list(TECHNIQUE_PRIORITY) + sorted(set(catalog) - set(TECHNIQUE_PRIORITY))
    result: list[str] = []
    for code in ordered:
        entry = catalog.get(code)
        if entry is None or not entry.is_active or not entry.auto_applicable:
            continue
        if EXPERIENCE_ORDER.get(entry.min_experience, 1) > level:
            continue
        if not validate_technique(code, safety, failure_allowed=composed_failure_allowed).valid:
            continue
        result.append(code)
    return tuple(result)


def choose_technique(*args, **kwargs) -> Optional[str]:
    """Primeira tecnica elegivel na ordem da secao 5.9, ou None."""
    codes = eligible_techniques(*args, **kwargs)
    return codes[0] if codes else None


def track_technique_in_force(track: TrackState, group: Optional[GroupState]) -> Optional[str]:
    """Tecnica da TRILHA que ainda vale como prescricao da progressao.

    `progression_tracks.technique_code` e gravado pela EXECUCAO
    (`update_track_execution`): e assim que a tecnica da F4 persiste depois da
    efetivacao do `APPLY_TECHNIQUE`, mas o mesmo campo tambem guarda o que foi
    apenas executado - tecnica fixa de outro Modelo, rest-pause feito por
    conta propria. O que so foi EXECUTADO nao vira prescricao: a tecnica da
    trilha so vale quando o grupo efetivou uma tecnica da F4
    (`technique_applied_at`) e continua numa fase com tecnica (F4-F6).

    M55: e vale SO na trilha que foi o alvo da F4
    (`group.technique_target_key`). As outras trilhas do grupo com
    `technique_code` gravado por execucao continuam sem tecnica. Alvo
    desconhecido (`None`: F4 efetivada antes da migracao 063) mantem o
    comportamento anterior - qualquer trilha do grupo com tecnica gravada.
    Fora disso devolve None, sem apagar o dado gravado."""
    code = (track.technique_code or "").strip()
    if not code or group is None:
        return None
    if group.technique_applied_at is None or group.phase not in TECHNIQUE_PHASES:
        return None
    target = group.technique_target_key
    # O alvo da F4 e o PAR (migracao 063): a tecnica vale para o par em todos
    # os contextos de fadiga.
    if target is not None and tuple(target)[:2] != track.pair:
        return None
    return code


def technique_target_from_payload(payload: Optional[Mapping[str, Any]]) -> Optional[tuple[int, int]]:
    """Par alvo de um `pending_payload` do `APPLY_TECHNIQUE`
    (`{"track_key": [exercise_id, equipment_id(, fatigue_context)], ...}`), ou
    None se ausente ou malformado. O alvo persistido (063) e o PAR."""
    raw = (payload or {}).get("track_key")
    if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3, 4):
        return None
    try:
        return (int(raw[0]), int(raw[1]))
    except (TypeError, ValueError):
        return None


def resolve_technique_authority(
    *,
    model_technique: Optional[str] = None,
    progression_technique: Optional[str] = None,
    safety: Optional[TrainingSafety] = None,
    composed_failure_allowed: Optional[bool] = None,
) -> tuple[Optional[str], tuple[str, ...]]:
    """Hierarquia da secao 5.9: Modelo (Modo B, tecnica fixa) > Progressao >
    nenhuma. Tecnica fixa invalida pela seguranca e REMOVIDA da prescricao.
    Devolve (codigo, evidencias)."""
    evidence: list[str] = []
    if model_technique:
        if safety is not None and not validate_technique(
            model_technique, safety, failure_allowed=composed_failure_allowed
        ).valid:
            evidence.append(f"MODEL_TECHNIQUE_REMOVED_BY_SAFETY:{model_technique}")
        else:
            if progression_technique and progression_technique != model_technique:
                evidence.append(EVIDENCE_TECHNIQUE_OVERRIDDEN_BY_MODEL)
            return model_technique, tuple(evidence)
    return (progression_technique or None), tuple(evidence)


# ---------------------------------------------------------------------------
# 5.6 Maquina de fases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseStep:
    """O degrau que o platô confirmado faz o grupo subir."""

    pending_action: Optional[str]
    phase_after: str
    phase_status: str = PHASE_ACTIVE
    reps_min: int = PROGRESSED_REPS[0]
    reps_max: int = PROGRESSED_REPS[1]
    target_track_key: Optional[tuple[int, int]] = None
    technique_code: Optional[str] = None
    blocked_by: tuple[str, ...] = ()
    next_eligible_progression_at: Optional[datetime] = None
    recommendation: Optional[str] = None


def active_tracks(tracks: Sequence[TrackState]) -> list[TrackState]:
    """Trilha ativa do grupo = executada nas ultimas 21 sessoes (secao 5.6).
    A janela ja e a do historico carregado, entao "ativa" = presente aqui."""
    return [track for track in tracks if not track.in_calibration]


def _technique_target(
    tracks: Sequence[TrackState],
    eligible_by_track: Mapping[tuple[int, int], Sequence[str]],
) -> tuple[Optional[TrackState], Optional[str]]:
    """F4: a ULTIMA trilha do grupo na ordem da sessao com interseccao de
    tecnicas nao vazia (RAG: tecnica no ultimo exercicio do grupo)."""
    for track in sorted(tracks, key=lambda item: (item.sequence, item.exercise_id), reverse=True):
        codes = eligible_by_track.get(track.key) or ()
        if codes:
            return track, codes[0]
    return None, None


def _add_set_target(tracks: Sequence[TrackState]) -> Optional[TrackState]:
    """F5: a trilha com maior `stimulus_weight` PRIMARY e `sets_count < 4`;
    empate pela ordem na sessao."""
    candidates = [track for track in tracks if int(track.sets_count) < MAX_SETS_COUNT]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (-float(item.stimulus_weight), item.sequence, item.exercise_id))


def next_step(
    group: GroupState,
    tracks: Sequence[TrackState],
    *,
    now: datetime,
    eligible_by_track: Optional[Mapping[tuple[int, int], Sequence[str]]] = None,
    model_fixed_sets: bool = False,
    activation_max_reached: bool = False,
    volume_in_range: bool = False,
) -> PhaseStep:
    """Tabela de fases da secao 5.6. Os "pulos" (D8 tecnica, D9 series fixas
    do modelo, carga nao aplicavel) cascateiam: a fase avança um degrau por
    platô, e o degrau pulado nao consome um platô.

    Lote 4/P6 (2026-10-11): o degrau de CARGA (F2 e F6) com trilha de carga
    aplicavel nao e mais do grupo - o avaliador nao chama esta funcao nesse
    caso (`load_phase_by_track`): cada trilha arma o proprio aumento pelo
    plato dela (`arm_track_plateau_load`) e a F2 -> F3 sai pela maioria das
    trilhas (`group_load_phase_complete`). A tabela continua valendo para os
    degraus sem carga e para o grupo so de peso corporal."""
    eligible_by_track = eligible_by_track or {}
    candidates = active_tracks(tracks) or list(tracks)
    ladder = ladder_for(group.training_goal)
    if not ladder.advances_past_load_phase:
        # M97 F2, forca: escada de UMA fase. So chega aqui o grupo sem trilha de
        # carga aplicavel (peso corporal) - nao ha faixa, tecnica nem serie a
        # subir: o grupo fica na fase e na faixa de forca.
        return PhaseStep(
            None,
            group.phase,
            reps_min=group.reps_min,
            reps_max=group.reps_max,
            blocked_by=(BLOCKED_STRENGTH_LADDER,),
        )

    def load_step() -> PhaseStep:
        if not any(track.load_applicable for track in candidates):
            return replace(
                technique_step(),
                phase_status=PHASE_SKIPPED_LOAD_NOT_APPLICABLE,
                blocked_by=(BLOCKED_LOAD_NOT_APPLICABLE,) + technique_step().blocked_by,
            )
        return PhaseStep(INCREASE_LOAD, F3)

    def technique_step() -> PhaseStep:
        target, code = _technique_target(candidates, eligible_by_track)
        if target is None:
            # D8: interseccao vazia -> F4 pulada, segue para a F5
            step = add_set_step()
            return replace(
                step,
                phase_status=PHASE_SKIPPED_NO_ELIGIBLE_TECHNIQUE,
                blocked_by=(BLOCKED_NO_ELIGIBLE_TECHNIQUE,) + step.blocked_by,
            )
        return PhaseStep(APPLY_TECHNIQUE, F4, target_track_key=target.key, technique_code=code)

    def add_set_step() -> PhaseStep:
        if activation_max_reached:
            return PhaseStep(
                None,
                F6,
                phase_status=PHASE_BLOCKED_ACTIVATION_MAX,
                blocked_by=(BLOCKED_ACTIVATION_MAX,),
            )
        if volume_in_range:
            # Fase 5, PR 6: series equivalentes realizadas (trabalho + tecnica)
            # ja dentro da faixa do grupo - mais uma serie nao e o degrau.
            return PhaseStep(None, F6, blocked_by=(BLOCKED_VOLUME_IN_RANGE,))
        if model_fixed_sets:
            # D9: Modo B nao muda series do modelo; vira recomendacao ao PT
            return PhaseStep(
                None,
                F6,
                blocked_by=(BLOCKED_MODEL_FIXED_SETS,),
                recommendation="Series fixas pelo modelo: avalie editar o modelo para +1 serie.",
            )
        target = _add_set_target(candidates)
        if target is None:
            return PhaseStep(None, F6, blocked_by=(BLOCKED_MAX_SETS,))
        return PhaseStep(ADD_SET, F5, target_track_key=target.key)

    if group.phase == F1:
        return PhaseStep(SET_REP_RANGE_10_12, F2)
    if group.phase == F2:
        return load_step()
    if group.phase == F3:
        return technique_step()
    if group.phase == F4:
        return add_set_step()
    if group.phase == F5:
        step = add_set_step()
        return step if step.pending_action == ADD_SET else PhaseStep(None, F6, blocked_by=step.blocked_by)
    # F6: carga de novo, mas so a cada 14 dias (por trilha desde o Lote 4/P6;
    # aqui so chega o grupo sem carga aplicavel, que nao tem o que subir)
    if not any(track.load_applicable for track in candidates):
        return PhaseStep(None, F6, blocked_by=(BLOCKED_LOAD_NOT_APPLICABLE,))
    eligible_at = (group.last_load_change_at + timedelta(days=F6_LOAD_INTERVAL_DAYS)) if group.last_load_change_at else None
    if eligible_at is not None and now < eligible_at:
        return PhaseStep(None, F6, blocked_by=(BLOCKED_NEXT_ELIGIBLE_AT,), next_eligible_progression_at=eligible_at)
    return PhaseStep(INCREASE_LOAD, F6)


# ---------------------------------------------------------------------------
# M97 F2: platô de FORCA (estagnacao) -> recuo de carga da trilha
# ---------------------------------------------------------------------------


def strength_stall_triggered(outcomes: Sequence[TrackSessionOutcome], track: TrackState) -> bool:
    """Platô de forca: as ultimas `STRENGTH_STALL_SESSIONS` sessoes avaliadas
    (neutras ignoradas) da trilha nao qualificaram e foram TODAS na carga atual
    da trilha (`working_load_kg`). Sessoes numa carga diferente (antes de um
    aumento ou de um recuo) nao contam: depois do recuo a contagem recomeca na
    carga nova. So vale na escada de forca (`GoalLadder.stall_plateau_reset`)."""
    if not ladder_for(track.training_goal).stall_plateau_reset:
        return False
    if track.in_calibration or not track.load_applicable or track.working_load_kg is None:
        return False
    considered = [item for item in outcomes if item.outcome != NEUTRAL]
    if len(considered) < STRENGTH_STALL_SESSIONS:
        return False
    tail = considered[-STRENGTH_STALL_SESSIONS:]
    current = Decimal(track.working_load_kg)
    return all(
        item.outcome != QUALIFY and item.working_load_kg is not None and Decimal(item.working_load_kg) == current
        for item in tail
    )


def strength_plateau_reset_load(track: TrackState) -> Optional[Decimal]:
    """Carga do recuo de forca: `STRENGTH_PLATEAU_RESET_FACTOR` x carga da trilha,
    arredondada PARA BAIXO ao passo do equipamento e ao menos um passo abaixo
    da carga atual. None quando nao ha carga positiva abaixo (nada a recuar)."""
    if track.working_load_kg is None:
        return None
    current = Decimal(track.working_load_kg)
    step = load_step(track.load_increment_kg)
    raw = current * STRENGTH_PLATEAU_RESET_FACTOR
    multiples = (raw / step).to_integral_value(rounding=ROUND_FLOOR)
    target = min(step * multiples, current - step)
    return target if target > 0 else None


def apply_strength_plateau_reset(track: TrackState, *, now: datetime) -> tuple[TrackState, Optional[dict[str, Any]]]:
    """Recuo de forca aplicado NA TRILHA (a carga base passa a ser a do recuo,
    como a calibracao: a reducao nunca vem da execucao). Devolve a trilha e a
    evidencia `{from_kg, to_kg, factor, sessions}`; sem carga a recuar, a
    trilha intocada e None. Um aumento pendente da trilha e descartado."""
    target = strength_plateau_reset_load(track)
    if target is None:
        return track, None
    evidence = {
        "from_kg": float(track.working_load_kg),
        "to_kg": float(target),
        "factor": float(STRENGTH_PLATEAU_RESET_FACTOR),
        "sessions": STRENGTH_STALL_SESSIONS,
    }
    updated = replace(
        track,
        working_load_kg=target,
        last_load_change_at=now,
        pending_load_kg=None,
        pending_load_since=None,
        pending_load_source=None,
    )
    return updated, evidence


# ---------------------------------------------------------------------------
# 5.6 Acoes de CARGA: aumento DA TRILHA (Lote 3/P4-P5 e Lote 4/P6, 2026-10-11)
# ---------------------------------------------------------------------------

# Chave do `pending_payload` com o instante em que um `INCREASE_LOAD` DO GRUPO
# foi armado (ISO 8601). Desde o Lote 4/P6 o grupo nao arma mais
# `INCREASE_LOAD` (o aumento e por trilha); a chave so serve para ler as acoes
# legadas que ainda estejam gravadas.
PENDING_SINCE_KEY = "since"


def group_pending_since(group: GroupState) -> Optional[datetime]:
    """Instante em que a acao pendente do grupo foi armada
    (`pending_payload["since"]`). Acao legada sem a marca: `phase_entered_at`."""
    raw = (group.pending_payload or {}).get(PENDING_SINCE_KEY)
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return group.phase_entered_at


def track_applied_group_load_action(track: TrackState, group: GroupState) -> bool:
    """LEGADO (antes do Lote 4): a trilha ja efetivou o `INCREASE_LOAD` do
    grupo - marca `pending_action_applied_at` ESTRITAMENTE posterior ao
    armamento. So `prescribe` le, para nao dar um segundo aumento numa trilha
    que ja subiu enquanto uma acao legada ainda estiver gravada."""
    if track.pending_action_applied_at is None:
        return False
    since = group_pending_since(group)
    return since is None or track.pending_action_applied_at > since


def execution_applies_pending(
    track: TrackState, group: Optional[GroupState], executed_load: Optional[Decimal]
) -> bool:
    """A execucao desta sessao efetiva, NESTA trilha, a acao pendente do grupo?

    `INCREASE_LOAD` do grupo (LEGADO, Lote 3/P5) so conta com a carga
    executada >= carga alvo (`next_load`). As demais acoes (faixa, tecnica,
    serie, D10): efetiva na execucao."""
    if group is None or group.pending_action != INCREASE_LOAD:
        return True
    if track.in_calibration or not track.load_applicable:
        return True
    target, _ = next_load(track, direction=1)
    if target is None:
        return True
    return executed_load is not None and Decimal(executed_load) >= target


def load_phase_by_track(group: GroupState, tracks: Sequence[TrackState]) -> bool:
    """Lote 4/P6: o grupo esta numa fase de CARGA em que o plato que sobe a
    carga e o da TRILHA? Sim em `TRACK_LOAD_PHASES` com >= 1 trilha (ativa)
    com carga aplicavel. Sem nenhuma (so peso corporal), a F2 segue a escada
    do grupo (`next_step` pula a carga para a tecnica) e a F6 nao tem carga a
    subir."""
    if group.phase not in TRACK_LOAD_PHASES:
        return False
    candidates = active_tracks(tracks) or list(tracks)
    return any(track.load_applicable for track in candidates)


def pending_load_source(track: TrackState) -> Optional[str]:
    """Origem do aumento pendente da trilha; None sem aumento. Linha anterior a
    072 (sem origem) = excesso, a unica origem que existia."""
    if track.pending_load_kg is None:
        return None
    return track.pending_load_source or PENDING_LOAD_SOURCE_EXCESS


def arm_track_load_increase(track: TrackState, *, now: datetime, source: str) -> TrackState:
    """Mecanismo UNICO do aumento de carga da trilha (Lote 3/P4 + Lote 4/P6):
    um passo (`next_load`) a partir da carga da trilha (ja com a adocao da
    sessao). A proxima ficha sai `INCREASE_LOAD` nessa trilha (`prescribe`);
    sai quando a carga alvo e executada ou expira em
    `GROUP_EFFECTIVATION_DAYS` (`track_pending_load_status`).

    Rearmar a MESMA carga alvo mantem o instante original (a janela de
    expiracao nao se renova sozinha). Na mesma carga alvo, o PLATO vence o
    excesso como origem: e o aumento que conta para a fase do grupo."""
    if track.in_calibration or not track.load_applicable or track.working_load_kg is None:
        return track
    target, _ = next_load(track, direction=1)
    if target is None or target <= Decimal(track.working_load_kg):
        return track
    same = (
        track.pending_load_kg is not None
        and Decimal(track.pending_load_kg) == target
        and track.pending_load_since is not None
    )
    since = track.pending_load_since if same else now
    if same and pending_load_source(track) == PENDING_LOAD_SOURCE_PLATEAU:
        source = PENDING_LOAD_SOURCE_PLATEAU
    return replace(track, pending_load_kg=target, pending_load_since=since, pending_load_source=source)


def arm_excess_reps_fast_track(track: TrackState, outcome: TrackSessionOutcome, *, now: datetime) -> TrackState:
    """Regra de excesso (Lote 3/P4): sessao QUALIFY com `excess_reps` arma o
    aumento de carga da trilha (`arm_track_load_increase`, origem
    `PENDING_LOAD_SOURCE_EXCESS`), sem esperar o plato. A faixa volta ao piso
    na carga nova. Nao conta para a fase do grupo."""
    if outcome.outcome != QUALIFY or not outcome.excess_reps:
        return track
    return arm_track_load_increase(track, now=now, source=PENDING_LOAD_SOURCE_EXCESS)


def track_plateau_load_due(track: TrackState, group: GroupState, plateau: Plateau, *, now: datetime) -> bool:
    """Lote 4/P6: o plato DESTA trilha pede aumento de carga agora?

    - plato da trilha confirmado (`plateau_confirmed` sobre o historico dela);
    - o grupo numa fase de carga (`TRACK_LOAD_PHASES`);
    - trilha ativa, com carga aplicavel e carga conhecida;
    - na F6, `F6_LOAD_INTERVAL_DAYS` desde a ultima mudanca de carga DA
      TRILHA (antes, o intervalo era do grupo)."""
    if not plateau.confirmed or group.phase not in TRACK_LOAD_PHASES:
        return False
    if track.in_calibration or not track.load_applicable or track.working_load_kg is None:
        return False
    if group.phase == F6 and track.last_load_change_at is not None:
        return now >= track.last_load_change_at + timedelta(days=F6_LOAD_INTERVAL_DAYS)
    return True


def arm_track_plateau_load(track: TrackState, group: GroupState, plateau: Plateau, *, now: datetime) -> TrackState:
    """Lote 4/P6: plato da trilha confirmado na fase de carga arma o aumento
    DA TRILHA (`arm_track_load_increase`, origem `PENDING_LOAD_SOURCE_PLATEAU`),
    sem esperar os outros exercicios do grupo."""
    if not track_plateau_load_due(track, group, plateau, now=now):
        return track
    return arm_track_load_increase(track, now=now, source=PENDING_LOAD_SOURCE_PLATEAU)


def track_pending_load_status(
    track: TrackState, executed_load: Optional[Decimal], *, now: datetime
) -> Optional[str]:
    """Situacao do aumento pendente da trilha depois de uma execucao:
    `PENDING_LOAD_APPLIED` (carga executada >= alvo), `PENDING_LOAD_EXPIRED`
    (`GROUP_EFFECTIVATION_DAYS` sem subir) ou None (segue pendente / nao ha)."""
    if track.pending_load_kg is None:
        return None
    if executed_load is not None and Decimal(executed_load) >= Decimal(track.pending_load_kg):
        return PENDING_LOAD_APPLIED
    since = track.pending_load_since
    if since is None or now >= since + timedelta(days=GROUP_EFFECTIVATION_DAYS):
        return PENDING_LOAD_EXPIRED
    return None


def track_pending_load(track: TrackState, *, now: Optional[datetime] = None) -> Optional[Decimal]:
    """Carga que o aumento pendente DA TRILHA (plato ou excesso) manda
    prescrever, ou None (sem aumento, trilha sem carga, alvo ja alcancado ou
    expirado)."""
    if track.pending_load_kg is None or not track.load_applicable:
        return None
    if track.working_load_kg is not None and Decimal(track.pending_load_kg) <= Decimal(track.working_load_kg):
        return None
    since = track.pending_load_since
    if now is not None and since is not None and now >= since + timedelta(days=GROUP_EFFECTIVATION_DAYS):
        return None
    return Decimal(track.pending_load_kg)


def track_applied_plateau_load(track: TrackState, group: GroupState) -> bool:
    """Lote 4/P6: a trilha EXECUTOU um aumento por plato dela desde a entrada
    do grupo na fase (`plateau_load_applied_at` estritamente posterior a
    `phase_entered_at`)."""
    marker = track.plateau_load_applied_at
    if marker is None:
        return False
    return group.phase_entered_at is None or marker > group.phase_entered_at


@dataclass(frozen=True)
class LoadPhaseProgress:
    """Quantos EXERCICIOS com trilha ativa de carga no grupo ja efetivaram o
    aumento por plato na fase (`applied`) de quantos (`total`) - Lote 7/D2."""

    applied: int = 0
    total: int = 0

    @property
    def complete(self) -> bool:
        """Maioria ESTRITA: `applied / total > GROUP_PHASE_ADVANCE_TRACK_SHARE`."""
        return self.total > 0 and self.applied / self.total > GROUP_PHASE_ADVANCE_TRACK_SHARE

    def to_evidence(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "total": self.total,
            "share": GROUP_PHASE_ADVANCE_TRACK_SHARE,
            "unit": "exercise",
        }


def group_load_phase_progress(group: GroupState, tracks: Sequence[TrackState]) -> LoadPhaseProgress:
    """Progresso da fase de carga do grupo (Lote 4/P6; unidade revista no
    Lote 7/D2). Base: trilhas ATIVAS (fora de calibracao) com carga aplicavel;
    o chamador passa as trilhas do grupo na janela de historico (secao 5.6:
    ativa = executada nas ultimas 21 sessoes).

    A unidade e o EXERCICIO (`exercise_id`): denominador = exercicios
    distintos com trilha ativa de carga; numerador = exercicios em que pelo
    menos uma trilha (qualquer equipamento/contexto de fadiga) efetivou o
    aumento por plato desde a entrada na fase. O aumento por excesso nao
    conta."""
    candidates = [track for track in active_tracks(tracks) if track.load_applicable]
    exercises = {track.exercise_id for track in candidates}
    applied = {track.exercise_id for track in candidates if track_applied_plateau_load(track, group)}
    return LoadPhaseProgress(applied=len(applied), total=len(exercises))


def group_load_phase_complete(group: GroupState, tracks: Sequence[TrackState]) -> bool:
    """Lote 4/P6: F2 -> F3 quando a MAIORIA (estrita,
    `GROUP_PHASE_ADVANCE_TRACK_SHARE`) dos EXERCICIOS com trilha ativa de
    carga do grupo efetivou o aumento por plato desde a entrada na fase
    (unidade = exercicio desde o Lote 7/D2)."""
    return group_load_phase_progress(group, tracks).complete


def group_pending_effective(group: GroupState, tracks: Sequence[TrackState], *, now: datetime) -> bool:
    """Secao 5.6: quando a acao pendente do grupo vira efetiva. Faixa, tecnica
    e serie: na primeira execucao.

    `INCREASE_LOAD` - Lote 4/P6 (2026-10-11): POR TRILHA. O grupo nao arma
    mais `INCREASE_LOAD` (cada trilha arma o proprio aumento pelo plato dela);
    uma acao legada ainda gravada so e efetiva pela maioria das trilhas
    (`group_load_phase_complete`) - senao o avaliador a retira da fila sem
    promover a fase. `DECREASE_LOAD` (D10) segue a regra anterior: todas as
    trilhas executaram ou 14 dias desde a entrada na fase."""
    if not group.pending_action:
        return False
    marker = group.phase_entered_at
    if group.pending_action == INCREASE_LOAD:
        return group_load_phase_complete(group, tracks)
    if group.pending_action == DECREASE_LOAD:
        candidates = [track for track in active_tracks(tracks) if track.load_applicable]
        if not candidates:
            return True
        if all(track.pending_action_applied_at is not None for track in candidates):
            return True
        return marker is not None and now >= marker + timedelta(days=GROUP_EFFECTIVATION_DAYS)
    return any(track.pending_action_applied_at is not None for track in tracks)


def apply_effective(group: GroupState, step_phase: str, *, now: datetime, reps: tuple[int, int]) -> GroupState:
    """Grava as datas da transicao efetivada e move a fase do grupo."""
    changes: dict[str, Any] = {
        "phase": step_phase,
        "phase_entered_at": now,
        "pending_action": None,
        "pending_payload": None,
        "reps_min": reps[0],
        "reps_max": reps[1],
    }
    action = group.pending_action
    if action == SET_REP_RANGE_10_12:
        changes["last_rep_range_change_at"] = now
    elif action in (INCREASE_LOAD, DECREASE_LOAD):
        changes["last_load_change_at"] = now
        if action == DECREASE_LOAD and step_phase == group.phase and group.phase in TRACK_LOAD_PHASES:
            # Lote 4/P6: a regressao (D10) e de UMA trilha e nao muda a fase;
            # numa fase de carga ela nao reinicia a fase - senao zeraria a
            # sequencia de plato e os aumentos ja efetivados das OUTRAS trilhas
            changes["phase_entered_at"] = group.phase_entered_at
    elif action == ADD_SET:
        changes["last_sets_change_at"] = now
    elif action == APPLY_TECHNIQUE:
        changes["technique_applied_at"] = now
        # M55: o payload pendente e apagado acima; o alvo da F4 sobrevive aqui
        # para `track_technique_in_force` nao estender a tecnica as outras
        # trilhas do grupo com tecnica so executada.
        changes["technique_target_key"] = technique_target_from_payload(group.pending_payload)
    return replace(group, **changes)


def advance_load_phase(group: GroupState, *, now: datetime) -> GroupState:
    """Lote 4/P6: F2 -> F3 pela maioria dos exercicios (`group_load_phase_complete`).
    Grava como a efetivacao de uma acao de carga (`last_load_change_at`), com a
    faixa da fase destino."""
    return apply_effective(
        replace(group, pending_action=INCREASE_LOAD, pending_payload=None), F3, now=now, reps=PROGRESSED_REPS
    )


# ---------------------------------------------------------------------------
# 5.11 Contrato de saida
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressionDecision:
    """Contrato de saida da secao 5.11, campo a campo."""

    group_state_id: Optional[int]
    muscle_group_id: Optional[int]
    track_id: Optional[int]
    engine_version: str = ENGINE_VERSION
    phase_before: Optional[str] = None
    phase_after: Optional[str] = None
    decision: str = HOLD
    suggested_sets: Optional[int] = None
    suggested_reps_min: Optional[int] = None
    suggested_reps_max: Optional[int] = None
    suggested_load_kg: Optional[float] = None
    advanced_technique: Optional[str] = None
    advanced_technique_parameters: Optional[Mapping[str, Any]] = None
    failure_allowed: bool = False
    effective_rpe_target: Optional[int] = None
    effective_rpe_ceiling: Optional[int] = None
    safety_constraints: Mapping[str, Any] = field(default_factory=dict)
    can_progress: bool = False
    blocked_by: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    next_eligible_progression_at: Optional[datetime] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_state_id": self.group_state_id,
            "muscle_group_id": self.muscle_group_id,
            "track_id": self.track_id,
            "engine_version": self.engine_version,
            "phase_before": self.phase_before,
            "phase_after": self.phase_after,
            "decision": self.decision,
            "suggested_sets": self.suggested_sets,
            "suggested_reps_min": self.suggested_reps_min,
            "suggested_reps_max": self.suggested_reps_max,
            "suggested_load_kg": self.suggested_load_kg,
            "advanced_technique": self.advanced_technique,
            "advanced_technique_parameters": dict(self.advanced_technique_parameters)
            if self.advanced_technique_parameters
            else None,
            "failure_allowed": self.failure_allowed,
            "effective_rpe_target": self.effective_rpe_target,
            "effective_rpe_ceiling": self.effective_rpe_ceiling,
            "safety_constraints": dict(self.safety_constraints),
            "can_progress": self.can_progress,
            "blocked_by": list(self.blocked_by),
            "evidence": dict(self.evidence),
            "next_eligible_progression_at": self.next_eligible_progression_at.isoformat()
            if isinstance(self.next_eligible_progression_at, datetime)
            else self.next_eligible_progression_at,
            "reason": self.reason,
        }


# --- parametros da tecnica prescrita (Fase 5, PR 6) ------------------------
# Tecnica escolhida pela PROGRESSAO (F4) nao traz aplicacoes do Modelo: uma
# aplicacao depois da ultima serie de trabalho (o minimo que a F4 pede; o
# realizado conta pelas reps que a pessoa fizer).
PROGRESSION_TECHNIQUE_APPLICATIONS = 1
# Carga de cada aplicacao (drop x0,8; demais = carga de trabalho): dono e
# `domain/session_loads.py` (M76 Lote 1a), arredondada ao passo do EQUIPAMENTO.
def round_to_equipment_step(value: Any, step_kg: Any = None) -> Optional[float]:
    """Carga arredondada ao multiplo do passo do equipamento (meio arredonda
    para cima). `None`/nao numerico/nao positivo -> `None`. Helper PUBLICO
    unico (M76 Lote 1a): a regra mora em `session_loads.round_to_step`; o
    `engine_decision_context` (Lote 1c) e o Modo Treino (1b) importam daqui."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except Exception:  # noqa: BLE001 - texto sujo = sem carga
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    step = load_step(step_kg)
    rounded = round_to_step(parsed, step)
    return rounded if rounded > 0 else float(step)


TECHNIQUE_ANCHOR_LAST_WORKING_SET = "LAST_WORKING_SET"
TECHNIQUE_SOURCE_MODEL = "model"
TECHNIQUE_SOURCE_PROGRESSION = "progression"
# Sem decisao do motor para a tecnica da linha (ficha manual, refresh): os
# parametros saem da propria linha da ficha (`prescription._technique_parameters`).
TECHNIQUE_SOURCE_PLAN = "plan"

# Pausa padrao do rest-pause (decisao do usuario, 2026-10-25): vale so quando a
# linha da ficha ou do Modelo NAO define `advanced_technique_duration_seconds`.
# A duracao da linha sempre vence; drop set e as demais tecnicas nao ganham
# padrao (sem duracao = sem pausa).
REST_PAUSE_TECHNIQUE_CODE = "rest_pause"
REST_PAUSE_DEFAULT_PAUSE_SECONDS = 15


def technique_pause_seconds(code: Optional[str], duration_seconds: Any) -> Optional[int]:
    """Pausa entre as aplicacoes da tecnica: a duracao da linha (> 0) ou, so no
    rest-pause sem duracao, `REST_PAUSE_DEFAULT_PAUSE_SECONDS`. None = sem
    pausa. Dono unico: `technique_parameters` e a estimativa de tempo do
    Modelo (`periodization/time_estimate.py`) consomem daqui."""
    if duration_seconds and int(duration_seconds) > 0:
        return int(duration_seconds)
    if (code or "").strip() == REST_PAUSE_TECHNIQUE_CODE:
        return REST_PAUSE_DEFAULT_PAUSE_SECONDS
    return None


def technique_parameters(
    code: Optional[str],
    *,
    source: str,
    catalog: Optional[Mapping[str, TechniqueCatalogEntry]] = None,
    applications: Any = None,
    pause_seconds: Any = None,
    working_load_kg: Optional[Decimal] = None,
    load_step_kg: Any = None,
    pair_max_safe_rpe: Any = None,
) -> Optional[dict[str, Any]]:
    """`advanced_technique_parameters` da prescricao: o que o Modo Treino
    precisa para abrir as etapas da tecnica depois da ULTIMA serie de trabalho
    (#278) - aplicacoes, pausa, carga de cada drop - e o equivalente PLANEJADO
    (`technique_accounting.planned_equivalent`, a mesma regra do Modelo).

    Os nomes espelham as colunas da ficha (`advanced_technique_applications`,
    `advanced_technique_duration_seconds`). FST-7/GVT so declaram o numero de
    series: sem aplicacoes, pausa nem equivalente. Sem tecnica -> None.

    Modo Intensivo (2026-10-24): `target_rpe` = RPE-alvo do PROTOCOLO da
    tecnica pelo teto efetivo do par (`training_safety.protocol_target_rpe`:
    10 so com falha liberada, senao 9; nunca rebaixado pela recuperacao)."""
    code = (code or "").strip()
    if not code:
        return None
    target_rpe = protocol_target_rpe(pair_max_safe_rpe)
    entry = (catalog or {}).get(code)
    coefficients = entry.coefficients() if entry is not None else None
    coef_catalog = {code: coefficients} if coefficients is not None else None
    if is_sets_only_technique(code, coef_catalog):
        return {
            "technique_code": code,
            "source": source,
            "sets_only": True,
            "default_sets_count": coefficients.default_sets_count if coefficients else None,
            "applications": None,
            "pause_seconds": None,
            "planned_equivalent": 0.0,
            "target_rpe": target_rpe,
        }
    count = int(applications) if applications and int(applications) > 0 else PROGRESSION_TECHNIQUE_APPLICATIONS
    pause = technique_pause_seconds(code, pause_seconds)
    params: dict[str, Any] = {
        "technique_code": code,
        "source": source,
        "sets_only": False,
        "set_type": (coefficients.set_type if coefficients else None) or SET_TYPE_BY_CODE.get(code),
        "applications": count,
        "pause_seconds": pause,
        "anchor": TECHNIQUE_ANCHOR_LAST_WORKING_SET,
        "planned_equivalent": round(planned_equivalent(code, count, coef_catalog), 2),
        "target_rpe": target_rpe,
    }
    if code == "drop_set":
        params["load_factor_per_application"] = float(DROP_SET_LOAD_FACTOR)
    if code in ("drop_set", "rest_pause") and working_load_kg is not None:
        params["application_loads_kg"] = technique_application_loads(code, working_load_kg, count, load_step_kg)
    return params


def _apply_e1rm_load_regime(
    track: TrackState,
    ladder: GoalLadder,
    current_e1rm_kg: Optional[float],
    load: Optional[Decimal],
    decision: str,
    evidence_out: dict[str, Any],
    reasons: list[str],
    blocked: list[str],
) -> tuple[Optional[Decimal], str]:
    """Regime de carga do objetivo com %1RM (forca). Com e1RM confiavel e carga
    aplicavel: carga = sugerido da faixa; decisao de carga = comparacao com a
    carga da trilha (acima = INCREASE_LOAD, abaixo = DECREASE_LOAD, igual ou
    trilha sem carga = HOLD), sem o bloqueio de janela (o e1RM e medido, nao um
    passo da escada). Sem e1RM: a progressao dupla (carga da trilha), marcada."""
    load_range = (
        e1rm_load_range(current_e1rm_kg, track.load_increment_kg, ladder.training_goal)
        if track.load_applicable
        else None
    )
    if load_range is None:
        evidence_out[EVIDENCE_LOAD_REGIME] = LOAD_REGIME_DOUBLE_PROGRESSION
        return load, decision
    evidence_out[EVIDENCE_LOAD_REGIME] = LOAD_REGIME_PCT_1RM
    evidence_out[EVIDENCE_STRENGTH_E1RM_LOAD] = load_range.to_dict()
    evidence_out.pop(EVIDENCE_TRACK_PLATEAU_LOAD, None)
    evidence_out.pop(EVIDENCE_EXCESS_REPS_FAST_TRACK, None)
    new_load = Decimal(str(load_range.suggested_kg))
    base = Decimal(track.working_load_kg) if track.working_load_kg is not None else None
    if base is None or new_load == base:
        new_decision = HOLD
        evidence_out.pop("load_increment_kg", None)
    else:
        new_decision = INCREASE_LOAD if new_load > base else DECREASE_LOAD
        evidence_out["load_increment_kg"] = float(new_load - base)
    while BLOCKED_NEXT_ELIGIBLE_AT in blocked:
        blocked.remove(BLOCKED_NEXT_ELIGIBLE_AT)
    reasons.clear()
    reasons.append(
        f"Carga de forca por %1RM: {load_range.load_min_kg:g}-{load_range.load_max_kg:g} kg "
        f"({load_range.pct_min:g}-{load_range.pct_max:g}% do e1RM {load_range.e1rm_kg:g} kg), "
        f"sugerido {load_range.suggested_kg:g} kg."
    )
    return new_load, new_decision


def _reduced_sets(sets_count: int, readiness_status: str) -> int:
    """Series da dose reduzida: a regra UNICA do modelo de recuperacao
    (`MuscleRecoveryService.reduced_sets`, M76 B2 - `ceil(S x 0,75)`, minimo
    1). Import tardio: o pacote `intelligence` e pesado e importa o `domain`
    (mesmo padrao de `training_safety._readiness_table`)."""
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService

    return MuscleRecoveryService.reduced_sets(sets_count, readiness_status)


def prescribe(
    track: TrackState,
    group: GroupState,
    safety: TrainingSafety,
    readiness: ReadinessContract = READY_CONTRACT,
    *,
    now: Optional[datetime] = None,
    catalog: Optional[Mapping[str, TechniqueCatalogEntry]] = None,
    is_advanced_user: bool = False,
    model_technique: Optional[str] = None,
    model_fixed_sets: bool = False,
    legacy_rpe_target: Optional[int] = None,
    evidence: Optional[Mapping[str, Any]] = None,
    model_technique_applications: Optional[int] = None,
    model_technique_duration_seconds: Optional[int] = None,
    current_e1rm_kg: Optional[float] = None,
) -> ProgressionDecision:
    """Pseudocodigo `prescribe` da secao 5.11.

    `current_e1rm_kg` (fix M97, 2026-10-26): e1RM confiavel e atual do par
    (chave fisica). So o objetivo com regime %1RM (`uses_e1rm_load`: forca) o
    usa: com ele a carga e o sugerido da faixa (`e1rm_load_range`) e a decisao
    de carga sai da comparacao com a carga da trilha; sem ele, a progressao
    dupla de sempre. Hipertrofia ignora o argumento.

    `legacy_rpe_target` e o `rpe_target` congelado de um plano legado: passa
    por `training_safety.compose`, que registra o clamp (overlay de seguranca
    da secao 5.10 - so restringe, nunca amplia).
    """
    catalog = catalog or {}
    blocked: list[str] = []
    evidence_out: dict[str, Any] = dict(evidence or {})
    evidence_out.setdefault("readiness", readiness.status)
    # M97 F2: escada e politica do OBJETIVO da trilha (hipertrofia: tudo como
    # sempre - mesma seguranca, composicao e evidencia).
    ladder = ladder_for(track.training_goal)
    evidence_out.update(ladder.evidence())
    safety = safety_for_goal(safety, track.training_goal)
    # RPE 10 Lote 6/P9: os sinais que cortaram o teto/dose, prontos para a
    # ficha congelada dizer "teto 9 hoje pela recuperacao: <sinal> em <data>".
    recovery_signals = summarize_signals(readiness.blocking_signals)
    if recovery_signals:
        evidence_out.setdefault(EVIDENCE_RECOVERY_SIGNALS, recovery_signals)

    composition = compose(safety, readiness.readiness, legacy_rpe_target, training_goal=track.training_goal)
    if composition.clamps:
        evidence_out["safety_clamps"] = [clamp.to_dict() for clamp in composition.clamps]
    base_safety = {"max_safe_rpe": safety.max_safe_rpe, "source": safety.safety_source}

    if readiness.status == NO_DIRECT_WORK:
        return ProgressionDecision(
            group_state_id=group.id,
            muscle_group_id=group.muscle_group_id,
            track_id=track.id,
            engine_version=ladder.engine_version,
            phase_before=group.phase,
            phase_after=group.phase,
            decision=SKIP,
            failure_allowed=composition.failure_allowed,
            effective_rpe_target=composition.target,
            effective_rpe_ceiling=composition.ceiling,
            safety_constraints=base_safety,
            can_progress=False,
            blocked_by=(BLOCKED_NO_DIRECT_WORK,),
            evidence=evidence_out,
            reason="Grupo sem trabalho direto pela recuperacao: nenhuma prescricao.",
        )

    sets_count = int(track.sets_count)
    if not model_fixed_sets:
        # Modo A (series da progressao): teto de series do objetivo (forca 3;
        # hipertrofia 4, o mesmo de `clamp_sets_count` - nada muda).
        sets_count = min(sets_count, ladder.max_sets_count)
    reps_min, reps_max = group.reps_range
    load = Decimal(track.working_load_kg) if track.working_load_kg is not None else None
    technique_from_progression = track_technique_in_force(track, group)
    decision = HOLD
    reasons: list[str] = []

    dose_reduced = readiness.status != READY or readiness.has_blocking_pain
    if dose_reduced:
        # REDUCED_DOSE: prescreve a acao ANTERIOR (transition_deferred)
        sets_count = _reduced_sets(sets_count, readiness.status)
        technique_from_progression = None
        evidence_out[EVIDENCE_TRANSITION_DEFERRED] = True
        reasons.append("Dose reduzida pela recuperacao: acao pendente diferida, sem tecnica.")
    elif group.pending_action:
        decision = group.pending_action
        payload = dict(group.pending_payload or {})
        raw_target = payload.get("track_key")
        applies_to_track = not raw_target or target_matches(raw_target, track)
        if decision == SET_REP_RANGE_10_12:
            reps_min, reps_max = PROGRESSED_REPS
            reasons.append("Faixa de repeticoes passa a 10-12 para todas as trilhas do grupo.")
        elif decision == INCREASE_LOAD:
            if not track.load_applicable:
                blocked.append(BLOCKED_LOAD_NOT_APPLICABLE)
                decision = HOLD
                reasons.append("Trilha sem carga aplicavel: aumento de carga nao se aplica.")
            elif track_applied_group_load_action(track, group):
                # LEGADO (acao de grupo gravada antes do Lote 4/P6, que o
                # avaliador retira na proxima sessao): a trilha ja levantou a
                # carga nova - sem segundo aumento
                decision = HOLD
                reasons.append("Aumento de carga ja executado nesta trilha: aguarda as outras trilhas do grupo.")
            else:
                new_load, increment = next_load(track, direction=1)
                load = new_load if new_load is not None else load
                evidence_out["load_increment_kg"] = float(increment.delta_kg)
                evidence_out["load_step_kg"] = float(increment.step_kg)
                for item in increment.evidence:
                    evidence_out[item] = True
                reasons.append(f"Carga +{increment.delta_kg} kg (step {increment.step_kg} kg, total).")
        elif decision == DECREASE_LOAD:
            if applies_to_track and track.load_applicable:
                new_load, increment = next_load(track, direction=-1)
                load = new_load if new_load is not None else load
                evidence_out["load_increment_kg"] = float(-increment.delta_kg)
                reasons.append(f"Regressao: carga -{increment.delta_kg} kg (D10).")
            else:
                decision = HOLD
        elif decision == APPLY_TECHNIQUE:
            if applies_to_track:
                technique_from_progression = payload.get("technique_code") or choose_technique(
                    safety,
                    catalog,
                    readiness=readiness,
                    is_advanced_user=is_advanced_user,
                    composed_failure_allowed=composition.failure_allowed,
                    ignore_fixed=True,
                    track=track,
                )
                if technique_from_progression is None:
                    blocked.append(BLOCKED_NO_ELIGIBLE_TECHNIQUE)
                    decision = HOLD
                else:
                    evidence_out[EVIDENCE_TECHNIQUE_APPLIED] = technique_from_progression
                    reasons.append(f"Tecnica {technique_from_progression} na ultima serie.")
            else:
                decision = HOLD
        elif decision == ADD_SET:
            if model_fixed_sets:
                blocked.append(BLOCKED_MODEL_FIXED_SETS)
                decision = HOLD
                reasons.append("Series fixas pelo modelo (Modo B): recomendacao de edicao ao PT (D9).")
            elif applies_to_track and sets_count < MAX_SETS_COUNT:
                sets_count += 1
                reasons.append(f"+1 serie na trilha (agora {sets_count}).")
            else:
                decision = HOLD
                if sets_count >= MAX_SETS_COUNT:
                    blocked.append(BLOCKED_MAX_SETS)

    technique, technique_evidence = resolve_technique_authority(
        model_technique=model_technique,
        progression_technique=technique_from_progression,
        safety=safety,
        composed_failure_allowed=composition.failure_allowed,
    )
    if technique_evidence:
        evidence_out["technique_authority"] = list(technique_evidence)

    if group.next_eligible_progression_at is not None and now is not None and now < group.next_eligible_progression_at:
        if decision != HOLD:
            blocked.append(BLOCKED_NEXT_ELIGIBLE_AT)
            decision = HOLD

    # Aumento de carga DA TRILHA (mecanismo unico): plato da propria trilha
    # (Lote 4/P6) ou regra de excesso de repeticoes (Lote 3/P4), sem esperar
    # os outros exercicios do grupo (nao muda a fase). Dose reduzida difere,
    # como as acoes do grupo. Vale sobre HOLD/DECREASE_LOAD; com outra acao do
    # grupo na mesma ficha, a carga sobe e a acao do grupo e mantida.
    track_load = None if dose_reduced else track_pending_load(track, now=now)
    if track_load is not None and (load is None or track_load > load):
        base = Decimal(track.working_load_kg) if track.working_load_kg is not None else track_load
        load = track_load
        if decision in (HOLD, DECREASE_LOAD):
            decision = INCREASE_LOAD
        detail = {
            "target_kg": float(track_load),
            "since": track.pending_load_since.isoformat() if track.pending_load_since else None,
        }
        evidence_out["load_increment_kg"] = float(track_load - base)
        if pending_load_source(track) == PENDING_LOAD_SOURCE_PLATEAU:
            evidence_out[EVIDENCE_TRACK_PLATEAU_LOAD] = detail
            reasons.append(
                f"Plato do exercicio confirmado ({ladder.plateau_min_sessions} sessoes qualificantes em "
                f">= {ladder.plateau_min_span_days} dias): carga da trilha sobe para {track_load} kg."
            )
        else:
            evidence_out[EVIDENCE_EXCESS_REPS_FAST_TRACK] = detail
            reasons.append(
                f"Excesso de repeticoes (melhor serie >= reps_max + {EXCESS_REPS_FAST_TRACK} no RPE alvo): "
                f"carga da trilha sobe para {track_load} kg e a faixa volta ao piso."
            )

    if ladder.load_pct_1rm is not None:
        load, decision = _apply_e1rm_load_regime(
            track, ladder, current_e1rm_kg, load, decision, evidence_out, reasons, blocked
        )

    if not reasons:
        reasons.append("Criterios nao satisfeitos: mantem a prescricao (HOLD).")

    # Fase 5, PR 6: parametros da tecnica que SAIU da autoridade (a do Modelo
    # leva as aplicacoes/pausa do Modelo; a da progressao, o default da F4).
    from_model = bool(technique) and technique == model_technique
    technique_params = technique_parameters(
        technique,
        source=TECHNIQUE_SOURCE_MODEL if from_model else TECHNIQUE_SOURCE_PROGRESSION,
        catalog=catalog,
        applications=model_technique_applications if from_model else None,
        pause_seconds=model_technique_duration_seconds if from_model else None,
        working_load_kg=load,
        load_step_kg=track.load_increment_kg,
        pair_max_safe_rpe=safety.max_safe_rpe,
    )

    return ProgressionDecision(
        group_state_id=group.id,
        muscle_group_id=group.muscle_group_id,
        track_id=track.id,
        engine_version=ladder.engine_version,
        phase_before=group.phase,
        phase_after=group.phase,
        decision=decision,
        suggested_sets=sets_count,
        suggested_reps_min=reps_min,
        suggested_reps_max=reps_max,
        suggested_load_kg=float(load) if load is not None else None,
        advanced_technique=technique,
        advanced_technique_parameters=technique_params,
        failure_allowed=composition.failure_allowed,
        effective_rpe_target=composition.target,
        effective_rpe_ceiling=composition.ceiling,
        safety_constraints=base_safety,
        can_progress=decision not in (HOLD, SKIP),
        blocked_by=tuple(blocked),
        evidence=evidence_out,
        next_eligible_progression_at=group.next_eligible_progression_at,
        reason=" ".join(reasons),
    )

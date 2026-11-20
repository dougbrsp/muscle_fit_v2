"""Comparabilidade entre exposicoes de um exercicio (plano §8.1 e §8.2).

Responde a UMA pergunta: "estas duas execucoes do mesmo exercicio podem ser
comparadas e, se sim, houve progresso?".

Tudo aqui e FUNCAO PURA - nenhuma consulta, nenhum relogio. Quem le o banco e
o `exposure_service`; quem decide o que significa e este modulo, e por isso os
oito casos do §16.1 do plano sao testaveis sem MySQL.

Regras nao negociaveis:

- Comparar so dentro da mesma `comparability_key`
  `(canonical_exercise_id, equipment_id, load_convention, fatigue_context)`.
  Maquina diferente nao e "piora": e `NOT_COMPARABLE`.
- Contexto de fadiga (PR 2 da fadiga previa, 2026-10-07): a faixa do dono
  `domain/prior_fatigue.py` (descansado / pre-fatigado / muito fatigado) sobre
  a sessao REAL. Supino 1o na segunda e 5o na quinta nao sao a mesma condicao:
  a sequencia comparavel e a do contexto da ULTIMA exposicao, e exposicoes de
  OUTRO contexto intercaladas sao PULADAS (nao interrompem a sequencia) -
  diferente da troca de equipamento/convencao, que continua INTERROMPENDO
  (`_trailing_comparable_window`). Recorde e e1RM continuam absolutos
  (`exposure_service._records` compara so a chave fisica).
- Objetivo de treino (M97 F1, dono `domain/training_goal.py`): progressao e
  plato sao SEPARADOS por objetivo. A exposicao de OUTRO objetivo e PULADA
  (como a de outro contexto de fadiga): ela nao entra na sequencia do
  objetivo da ultima nem a interrompe. A chave FISICA
  (`ComparabilityKey.physical`) nao tem objetivo - recorde e e1RM continuam
  unicos.
- Academia (issue #188, dono `domain/equipment_context.py`): MAQUINA tem a
  academia do vinculo na chave FISICA (`ComparabilityKey.gym_id`); peso livre
  tem `gym_id = None` (tudo como antes). A exposicao que difere SO na
  academia e PULADA (como a de outro contexto de fadiga, contada a parte).
  DEPOIS de uma troca de aparelho ou academia (local ao segmento atual), o
  contexto de maquina so e classificado a partir da N-esima sessao comparavel
  (`in_equipment_calibration`); sem troca, o minimo de sempre. Recorde e e1RM continuam globais: comparam a
  chave fisica SEM a academia (`ComparabilityKey.physical_any_gym`).
- `assisted` inverte o sinal da carga - MENOS quilos de assistencia e
  progresso. Nunca aplicar a mesma leitura de "carga subiu = progresso".
- `bodyweight` compara so repeticoes (o peso corporal nao esta na serie).
- `per_side`/`per_dumbbell` NAO convertem valor nenhum: o numero registrado
  continua sendo o que foi registrado. A convencao existe para SEPARAR chaves
  (halter de 20 kg e barra de 20 kg nao sao a mesma carga), nunca para
  multiplicar por dois - multiplicar inventaria dado que ninguem mediu.
- Sem RPE nao ha RIR, e sem RIR nao ha e1RM: `e1rm = None`. Nunca `0`, nunca
  "assume RIR 0" (plano §6, linha `e1rm`).
- Reps emparelhadas ajustadas pelo esforco (M78, 2026-10-11): na mesma carga
  de topo, cada serie emparelhada vale `reps + RIR` (`e1rm.rir_from_rpe`, a
  mesma convencao do e1RM) quando TODAS as series emparelhadas dos DOIS lados
  tem RPE; faltando RPE em qualquer uma, os dois lados usam reps cruas (nunca
  misturar). 11 reps a RPE 9 vale o mesmo que 12 reps a RPE 10.
- Mesmas reps com RPE maior (P8 da investigacao RPE 10, 2026-10-11): mesma
  carga de topo e as MESMAS reps cruas emparelhadas, com o RPE subindo, NAO e
  regressao - e estabilidade (`REASON_SAME_REPS_HIGHER_RPE`). Nem o e1RM com
  RIR nem `reps + RIR` decidem esse caso (12x35@8 -> 12x35@10 e Estavel). O
  RIR continua valendo quando as reps cruas mudam.

Os limiares vem todos de `metrics_registry.THRESHOLDS` - este modulo nao
define constante numerica propria.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional, Sequence

from ...domain import e1rm as _e1rm
from ...domain.equipment_context import context_gym_id, context_in_calibration
from ...domain.set_measures import REASON_MEASURE_NOT_REP_BASED as _REASON_MEASURE_NOT_REP_BASED
from ...domain.training_goal import goal_value
from ...load_conventions import (
    DEFAULT_LOAD_CONVENTION,
    LOAD_CONVENTION_LABELS,
    LoadConvention,
    compares_reps_only,
    inverts_load_direction,
    normalize_load_convention,
    supports_e1rm,
)
from .metrics_registry import GROUP_E1RM_OUTLIER_CHANGE_PCT, THRESHOLDS, ExerciseProgressionStatus

# ---------------------------------------------------------------------------
# Convencao de carga: vocabulario e regras vivem em `app/load_conventions.py`
# (modulo neutro, compartilhado com a Lambda api-core, que valida o cadastro).
# Reexportado aqui para quem le comparabilidade nao precisar saber de onde vem.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Chave de comparabilidade (§8.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparabilityKey:
    """`(canonical_exercise_id, equipment_id, load_convention)` (§8.1).

    `equipment_agnostic` NAO faz parte da identidade conceitual da chave: e a
    marca do unico caso em que o §8.1 permite comparar sem equipamento -
    quando TODAS as exposicoes daquele exercicio estao sem equipamento
    resolvido (historico antigo, treino sem `gym_equipment`). Sem essa marca,
    duas exposicoes com `equipment_id = None` seriam indistinguiveis de duas
    exposicoes feitas em maquinas diferentes e nao cadastradas - que NAO sao
    comparaveis.
    """

    canonical_exercise_id: Optional[int]
    equipment_id: Optional[int]
    load_convention: LoadConvention
    equipment_agnostic: bool = False
    # Faixa de fadiga previa da exposicao (`PriorFatigueBand.value`, dono
    # `domain/prior_fatigue.py`). `None` = contexto nao medido (leitura sem
    # as sessoes, ex.: sinal de desempenho da recuperacao): so casa com `None`,
    # que e o comportamento anterior.
    fatigue_context: Optional[str] = None
    # Objetivo de treino da exposicao (M97 F1; VALOR do dono
    # `domain/training_goal.py`). `None` = nao lido (so casa com `None`). Fica
    # FORA de `to_dict` (o contrato da API nao muda nesta fase): as leituras
    # do dashboard ja chegam filtradas por um objetivo.
    training_goal: Optional[str] = None
    # Academia do contexto de MAQUINA (issue #188; `equipment_context.context_gym_id`).
    # Faz parte da chave FISICA. `None` = peso livre/tipo ausente: como antes.
    gym_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_exercise_id": self.canonical_exercise_id,
            "equipment_id": self.equipment_id,
            "load_convention": self.load_convention.value,
            "equipment_agnostic": self.equipment_agnostic,
            "fatigue_context": self.fatigue_context,
            "gym_id": self.gym_id,
        }

    @property
    def physical(self) -> "ComparabilityKey":
        """A chave SEM o contexto de fadiga e SEM o objetivo: exercicio +
        equipamento + convencao (o que decide recorde/e1RM, unicos entre
        objetivos, e o que INTERROMPE a sequencia)."""
        if self.fatigue_context is None and self.training_goal is None:
            return self
        return replace(self, fatigue_context=None, training_goal=None)

    @property
    def physical_any_gym(self) -> "ComparabilityKey":
        """A chave FISICA sem a academia: exercicio + equipamento + convencao.
        E o que decide recorde e linha de e1RM (globais entre academias)."""
        physical = self.physical
        return physical if physical.gym_id is None else replace(physical, gym_id=None)


def build_key(
    canonical_exercise_id: Optional[int],
    equipment_id: Optional[int],
    load_convention: Any = None,
    *,
    equipment_agnostic: bool = False,
    fatigue_context: Any = None,
    training_goal: Any = None,
    gym_id: Optional[int] = None,
) -> ComparabilityKey:
    """Monta a chave. `equipment_agnostic=True` descarta o equipamento (caso
    do §8.1 em que nenhuma exposicao tem equipamento). `fatigue_context` =
    faixa do dono `prior_fatigue` (`PriorFatigueBand` ou o seu valor).
    `training_goal` = objetivo da exposicao (`None` = nao lido; senao o VALOR
    do dono `training_goal`, leitura tolerante). `gym_id` = academia do
    contexto de maquina (`equipment_context.context_gym_id`; `None` = peso
    livre, como antes)."""
    convention = normalize_load_convention(load_convention)
    return ComparabilityKey(
        canonical_exercise_id=None if canonical_exercise_id is None else int(canonical_exercise_id),
        equipment_id=None if (equipment_agnostic or equipment_id is None) else int(equipment_id),
        load_convention=convention,
        equipment_agnostic=bool(equipment_agnostic),
        fatigue_context=None if fatigue_context is None else str(getattr(fatigue_context, "value", fatigue_context)),
        training_goal=None if training_goal is None else goal_value(training_goal),
        gym_id=None if gym_id is None else int(gym_id),
    )


def keys_match(a: Optional[ComparabilityKey], b: Optional[ComparabilityKey]) -> bool:
    """Duas exposicoes sao comparaveis entre si?

    Igualdade de todos os campos (inclusive o contexto de fadiga) E
    equipamento resolvido. Chave com `equipment_id is None` so e comparavel
    quando marcada como `equipment_agnostic` (§8.1)."""
    if a is None or b is None:
        return False
    if a != b:
        return False
    return a.equipment_agnostic or a.equipment_id is not None


def calibration_physical_key(key: Optional[ComparabilityKey]) -> Optional[ComparabilityKey]:
    """Chave fisica que a regra unica da calibracao (`equipment_context.context_in_calibration`)
    recebe por exposicao: exercicio + aparelho + convencao, sem academia; `None` quando o
    aparelho nao esta resolvido (`keys_match`). Ponto unico para a comparabilidade e o
    avaliador do motor de carga (`progression_evaluator.context_calibrating`), issue #188."""
    if key is None:
        return None
    physical = key.physical_any_gym
    return physical if keys_match(physical, physical) else None


def physical_keys_match(a: Optional[ComparabilityKey], b: Optional[ComparabilityKey]) -> bool:
    """`keys_match` IGNORANDO o contexto de fadiga: mesmo exercicio,
    equipamento e convencao. E o que decide se a sequencia continua (troca
    fisica interrompe; troca de contexto so filtra)."""
    if a is None or b is None:
        return False
    return keys_match(a.physical, b.physical)


def physical_keys_match_any_gym(a: Optional[ComparabilityKey], b: Optional[ComparabilityKey]) -> bool:
    """`physical_keys_match` IGNORANDO tambem a academia (issue #188)."""
    if a is None or b is None:
        return False
    return keys_match(a.physical_any_gym, b.physical_any_gym)


def only_gym_differs(a: Optional[ComparabilityKey], b: Optional[ComparabilityKey]) -> bool:
    """Mesma chave fisica, OUTRA academia (contexto de maquina, issue #188):
    a exposicao e pulada, nao interrompe a sequencia."""
    return a is not None and b is not None and a.gym_id != b.gym_id and physical_keys_match_any_gym(a, b)


# Por que uma exposicao NAO e comparavel com a chave atual (M76 Lote 3c, C13).
# Mesmos codigos que o frontend ja usava para o selo da tabela
# (`ComparabilityMismatch` em `frontend/src/lib/exercise-progress.ts`); antes o
# FE os deduzia reimplementando `keys_match` - agora recebe pronto da API.
MISMATCH_LOAD_CONVENTION = "load_convention"
MISMATCH_EQUIPMENT_UNKNOWN = "equipment_unknown"
MISMATCH_EQUIPMENT = "equipment"
# Mesmo equipamento e convencao, OUTRO contexto de fadiga previa (PR 2 da
# fadiga previa): a exposicao nao entra na comparacao do contexto atual.
MISMATCH_FATIGUE_CONTEXT = "fatigue_context_changed"
# Mesmo equipamento e convencao, OUTRO objetivo de treino (M97 F1). As
# leituras do dashboard ja chegam filtradas por objetivo, entao a resposta
# da API nao o emite nesta fase.
MISMATCH_TRAINING_GOAL = "training_goal_changed"
# Mesma maquina e convencao, OUTRA academia (issue #188): o contexto de maquina
# atual nao a compara.
MISMATCH_GYM = "gym_changed"


def goals_match(a: Optional[ComparabilityKey], b: Optional[ComparabilityKey]) -> bool:
    """As duas exposicoes sao do MESMO objetivo de treino (M97 F1)?"""
    if a is None or b is None:
        return False
    return a.training_goal == b.training_goal


def mismatch_with_current(key: ComparabilityKey, current: ComparabilityKey) -> Optional[str]:
    """`None` quando `keys_match(key, current)`; senao, o motivo para a tela:
    convencao de carga diferente, equipamento nao resolvido (sem a marca
    `equipment_agnostic`) ou outro equipamento."""
    if keys_match(key, current):
        return None
    if key.load_convention != current.load_convention:
        return MISMATCH_LOAD_CONVENTION
    if not current.equipment_agnostic and key.equipment_id is None:
        return MISMATCH_EQUIPMENT_UNKNOWN
    if only_gym_differs(key, current):
        return MISMATCH_GYM
    if physical_keys_match(key, current):
        if not goals_match(key, current):
            return MISMATCH_TRAINING_GOAL
        return MISMATCH_FATIGUE_CONTEXT
    return MISMATCH_EQUIPMENT


def comparison_with_current(key: ComparabilityKey, current: Optional[ComparabilityKey]) -> dict[str, Any]:
    """Campos publicos de cada exposicao do drill-down: comparavel com a chave
    atual (a da ultima exposicao) e, se nao, por que. Sem chave atual (sem
    exposicoes) nada e marcado."""
    mismatch = mismatch_with_current(key, current) if current is not None else None
    return {"comparable_with_current": mismatch is None, "mismatch_with_current": mismatch}


def all_equipment_missing(equipment_ids: Sequence[Optional[int]]) -> bool:
    """`True` quando existe ao menos uma exposicao e NENHUMA tem equipamento
    resolvido - a excecao do §8.1 que libera a chave sem equipamento (com
    qualidade MEDIUM, decidida por quem monta a resposta)."""
    values = list(equipment_ids)
    return bool(values) and all(value is None for value in values)


# ---------------------------------------------------------------------------
# Chave do RECORDE (session_records): chave fisica GLOBAL por aparelho -
# exercicio + equipamento + convencao, sem academia, fadiga nem objetivo
# (decisao do usuario). Unico ponto que o recorde usa (issue #188).
# ---------------------------------------------------------------------------


def record_equipment_agnostic(equipment_ids: Sequence[Optional[int]]) -> bool:
    """Excecao do §8.1 para o recorde: nenhuma exposicao do historico do
    exercicio tem equipamento resolvido."""
    return all_equipment_missing(equipment_ids)


def record_key(
    exercise_id: int,
    equipment_id: Optional[int],
    load_convention: Any,
    *,
    equipment_agnostic: bool,
) -> ComparabilityKey:
    """Chave fisica do recorde de uma exposicao (convencao normalizada)."""
    return build_key(
        int(exercise_id),
        equipment_id,
        normalize_load_convention(load_convention),
        equipment_agnostic=equipment_agnostic,
    )


def record_key_comparable(key: ComparabilityKey) -> bool:
    """A chave do recorde pode ser comparada com alguma outra? (aparelho
    resolvido, ou a excecao do §8.1)."""
    return keys_match(key, key)


# ---------------------------------------------------------------------------
# Exposicao (entrada das regras) - DTO minimo, sem nada do banco
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkingSet:
    """Uma serie `working` da exposicao: so o que as regras de reps por serie
    precisam. `None` = nao registrado (nunca zero)."""

    reps: Optional[int] = None
    weight_kg: Optional[float] = None
    rpe: Optional[float] = None

    @property
    def effort_reps(self) -> Optional[float]:
        """`reps + RIR` (RIR = `e1rm.rir_from_rpe`, a convencao do e1RM): as
        reps que a serie "valia" naquela carga. `None` sem reps ou sem RPE -
        nunca assume RIR 0 aqui."""
        if self.reps is None or self.rpe is None:
            return None
        return _clean_number(int(self.reps) + _e1rm.rir_from_rpe(self.rpe))


def _clean_number(value: float) -> float:
    """Inteiro quando o valor e inteiro (12.0 -> 12), senao 2 casas: o numero
    sai assim para a API/IA sem ruido de ponto flutuante."""
    rounded = round(float(value), 6)
    return int(rounded) if rounded.is_integer() else round(rounded, 2)


@dataclass(frozen=True)
class ExposureSummary:
    """O que uma exposicao precisa ter para ser classificada (§8.2).

    `None` em qualquer campo significa "nao observado" - nunca zero. Quem
    monta (o `exposure_service`) ja aplicou o filtro de series `working`.
    """

    key: ComparabilityKey
    total_reps: Optional[int] = None  # soma das reps das series working
    best_load_kg: Optional[float] = None  # maior carga working da exposicao
    avg_rpe: Optional[float] = None  # media dos RPE informados (None se nenhum)
    e1rm: Optional[float] = None  # None sem RPE, e None onde nao se aplica
    e1rm_reps: Optional[int] = None  # reps da serie que produziu o e1RM
    prescribed_reps_min: Optional[int] = None
    prescribed_reps_max: Optional[int] = None
    # Series working NA ORDEM em que foram feitas. Vazio = exposicao montada
    # sem detalhe por serie (so agregados): as regras a tratam como UMA serie
    # com `total_reps` na `best_load_kg` - ver `_set_list`.
    sets: tuple[WorkingSet, ...] = ()
    # Lote T3 (medidas de serie): a exposicao teve serie por tempo ou por
    # maximo de reps (`set_measures.is_rep_based` falso). Essas series ja
    # ficaram fora de `sets` e dos agregados; a exposicao nao e comparavel.
    has_non_rep_sets: bool = False

    @property
    def load_convention(self) -> LoadConvention:
        return self.key.load_convention

    @property
    def directional_load(self) -> Optional[float]:
        """Carga na DIRECAO do progresso: `assisted` inverte o sinal, entao
        "subiu" sempre quer dizer "melhorou" (§8.2)."""
        if self.best_load_kg is None:
            return None
        return -float(self.best_load_kg) if inverts_load_direction(self.load_convention) else float(self.best_load_kg)


# Codigos de `reasons` (o frontend traduz; nenhum texto livre aqui).
REASON_SAME_LOAD_MORE_REPS = "same_load_more_reps"
REASON_LOAD_UP_REPS_IN_RANGE = "load_up_reps_in_prescribed_range"
REASON_SAME_PERFORMANCE_LOWER_RPE = "same_performance_lower_rpe"
REASON_E1RM_UP = "e1rm_up"
REASON_E1RM_DOWN = "e1rm_down"
# P8 (2026-10-11): mesma carga de topo, mesmas reps cruas emparelhadas e o RPE
# subiu -> sem progresso nem regressao (o chamador decide STABLE/plato). O
# e1RM com RIR e o `reps + RIR` nao decidem este caso.
REASON_SAME_REPS_HIGHER_RPE = "same_reps_higher_rpe"
REASON_SAME_LOAD_FEWER_REPS = "same_load_fewer_reps"
REASON_MORE_LOAD_FEWER_REPS = "more_load_fewer_reps_inconclusive"
REASON_NO_CHANGE = "no_change"
REASON_KEY_CHANGED = "comparability_key_changed"
REASON_NOT_ENOUGH_EXPOSURES = "not_enough_comparable_exposures"
REASON_EQUIPMENT_UNKNOWN = "equipment_not_resolved"
REASON_NO_E1RM_WITHOUT_RPE = "e1rm_unavailable_without_rpe"
REASON_BODYWEIGHT_REPS_ONLY = "bodyweight_reps_only"
REASON_PLATEAU_RUN = "no_progress_nor_regression_in_run"
# Contexto de fadiga (PR 2 da fadiga previa). Informativos, nunca mudam o
# status por si:
# - a sequencia do contexto atual PULOU exposicoes de outro contexto;
REASON_OTHER_CONTEXT_SKIPPED = "other_fatigue_context_exposures_skipped"
# - a ultima exposicao e a primeira do seu contexto (ha exposicoes do mesmo
#   equipamento, mas em outro contexto): sem historico proprio ainda, o
#   resultado nao conta como progresso/regressao/plato.
REASON_FIRST_IN_CONTEXT = "first_exposure_in_fatigue_context"
# Informativo (M78): as reps emparelhadas foram comparadas como `reps + RIR`
# (todas as series emparelhadas dos dois lados tinham RPE). Nunca muda o
# status por si - explica por que "11 reps" empatou com "12 reps".
REASON_PAIRED_REPS_EFFORT_ADJUSTED = "paired_reps_effort_adjusted"
# Contexto de maquina (issue #188, dono `domain/equipment_context.py`):
# - em calibracao (menos sessoes comparaveis que o minimo do dono): o status e
#   INSUFFICIENT_DATA, nunca progresso/regressao/plato;
REASON_EQUIPMENT_CALIBRATING = "equipment_context_in_calibration"
# - informativo: a sequencia PULOU exposicoes da mesma maquina em outra academia.
REASON_OTHER_GYM_SKIPPED = "other_gym_exposures_skipped"
# Informativo: a exposicao atual teve MENOS series working que a anterior.
# Nunca muda o status - fazer menos series e decisao de volume, nao perda de
# desempenho (as reps sao comparadas por serie emparelhada).
REASON_FEWER_SETS = "fewer_working_sets"
# Lote T3 (medidas de serie): reexportado do dono `domain/set_measures.py`.
# Serie por tempo/maximo de reps numa das duas exposicoes: "maximo de reps
# em 30 s" nao e a mesma pergunta que "8-12 reps a RPE 8" - NOT_COMPARABLE.
REASON_MEASURE_NOT_REP_BASED = _REASON_MEASURE_NOT_REP_BASED


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def _same_load(current: Optional[float], previous: Optional[float], tolerance: float) -> bool:
    if current is None or previous is None:
        return False
    if previous == 0:
        return current == 0
    return abs(current - previous) / abs(previous) <= tolerance


def _set_list(exposure: ExposureSummary) -> tuple[WorkingSet, ...]:
    """As series da exposicao. Sem detalhe por serie (DTO montado so com
    agregados), a exposicao vale como UMA serie de `total_reps` na
    `best_load_kg` - o mesmo numero que existia antes, sem inventar divisao."""
    if exposure.sets:
        return exposure.sets
    if exposure.total_reps is None:
        return ()
    return (WorkingSet(reps=exposure.total_reps, weight_kg=exposure.best_load_kg),)


def _working_set_count(exposure: ExposureSummary) -> int:
    return len(_set_list(exposure))


def top_load_sets(exposure: ExposureSummary, tolerance: float) -> list[WorkingSet]:
    """As series feitas na carga de TOPO da exposicao (dentro da tolerancia de
    "mesma carga" sobre `best_load_kg`), da melhor para a pior: mais reps
    primeiro e, no empate, a de maior `reps + RIR` (sem RPE por ultimo).

    `bodyweight` nao tem carga na serie: todas as series contam. Em `assisted`
    a carga de topo e a MENOR (o `best_load_kg` ja vem assim)."""
    sets = [item for item in _set_list(exposure) if item.reps is not None]
    if not compares_reps_only(exposure.load_convention):
        best = exposure.best_load_kg
        if best is None:
            return []
        sets = [item for item in sets if item.weight_kg is not None and _same_load(item.weight_kg, best, tolerance)]
    return sorted(
        sets,
        key=lambda item: (int(item.reps), -1.0 if item.effort_reps is None else float(item.effort_reps)),
        reverse=True,
    )


def top_load_reps(exposure: ExposureSummary, tolerance: float) -> list[int]:
    """Reps CRUAS das series de topo (`top_load_sets`), em ordem decrescente.
    E o que foi registrado (faixa prescrita, "reps das series de topo" da
    IA); a comparacao entre exposicoes usa `paired_top_reps`."""
    return [int(item.reps) for item in top_load_sets(exposure, tolerance)]


@dataclass(frozen=True)
class PairedTopReps:
    """O que a regra de reps compara: as N series de topo emparelhadas.

    `previous`/`current` = o VALOR comparado de cada serie (maior para menor):
    `reps + RIR` quando `effort_adjusted`, senao as reps cruas.
    `previous_reps`/`current_reps` = as reps cruas das mesmas series, na
    mesma ordem de emparelhamento."""

    previous: tuple[float, ...]
    current: tuple[float, ...]
    previous_reps: tuple[int, ...]
    current_reps: tuple[int, ...]
    effort_adjusted: bool

    @property
    def pairs(self) -> int:
        return len(self.previous)

    @property
    def previous_sum(self) -> float:
        return _clean_number(sum(self.previous))

    @property
    def current_sum(self) -> float:
        return _clean_number(sum(self.current))

    @property
    def delta(self) -> float:
        """Soma atual - soma anterior dos valores comparados."""
        return _clean_number(sum(self.current) - sum(self.previous))


def paired_top_reps(previous: ExposureSummary, current: ExposureSummary, tolerance: float) -> Optional[PairedTopReps]:
    """As N melhores series de topo de cada exposicao (maior para menor),
    N = min(series de topo anterior, series de topo atual).

    Emparelhar (em vez de somar tudo) e o que impede "fiz uma serie a menos"
    de virar regressao: 11+10+10 contra 14+13 compara 11+10 com 14+13.

    Esforco (M78): com RPE em TODAS as 2N series emparelhadas, cada serie vale
    `reps + RIR` (`WorkingSet.effort_reps`): 11@RPE9 + 11@RPE10 (12+11=23)
    empata com 12@RPE10 + 11@RPE10 (23). Faltando RPE em qualquer uma, os
    dois lados usam reps cruas - nunca um lado ajustado e o outro nao. A
    escolha das N series nao muda (mais reps cruas primeiro).

    `None` quando um dos lados nao tem serie de topo com reps. Publica: a
    magnitude do sinal de recuperacao (`paired_reps_epley_ratio`) e o drill-down
    (`exposure_service.paired_top_reps`) emparelham por aqui, nunca por copia."""
    prev_sets = top_load_sets(previous, tolerance)
    curr_sets = top_load_sets(current, tolerance)
    pairs = min(len(prev_sets), len(curr_sets))
    if pairs == 0:
        return None
    prev_sets, curr_sets = prev_sets[:pairs], curr_sets[:pairs]
    prev_reps = tuple(int(item.reps) for item in prev_sets)
    curr_reps = tuple(int(item.reps) for item in curr_sets)
    if not all(item.effort_reps is not None for item in (*prev_sets, *curr_sets)):
        return PairedTopReps(prev_reps, curr_reps, prev_reps, curr_reps, effort_adjusted=False)
    return PairedTopReps(
        previous=tuple(item.effort_reps for item in prev_sets),
        current=tuple(item.effort_reps for item in curr_sets),
        previous_reps=prev_reps,
        current_reps=curr_reps,
        effort_adjusted=True,
    )


def _reps_in_prescribed_range(exposure: ExposureSummary, tolerance: float) -> bool:
    """TODAS as series de topo de carga dentro da faixa prescrita (que e POR
    SERIE, ex.: "8-12"). Sem faixa prescrita a condicao NAO e satisfeita (nao
    ha contra o que comparar) - o criterio de e1RM continua disponivel."""
    low, high = exposure.prescribed_reps_min, exposure.prescribed_reps_max
    if low is None and high is None:
        return False
    reps = top_load_reps(exposure, tolerance)
    if not reps:
        return False
    if low is not None and min(reps) < low:
        return False
    if high is not None and max(reps) > high:
        return False
    return True


def e1rm_is_decisive(previous: ExposureSummary, current: ExposureSummary, max_reps: int) -> bool:
    """O e1RM so decide sozinho quando a serie que o gerou tem ate `max_reps`
    reps nos DOIS lados (limite de confiabilidade do Epley). A trava vale
    igual para progresso e para regressao."""
    for exposure in (previous, current):
        if exposure.e1rm is None or exposure.e1rm_reps is None or exposure.e1rm_reps > max_reps:
            return False
    return True


def compare_exposures(
    previous: ExposureSummary,
    current: ExposureSummary,
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> tuple[Optional[ExerciseProgressionStatus], list[str]]:
    """Compara DUAS exposicoes consecutivas e comparaveis (§8.2).

    Devolve `(PROGRESSING | REGRESSING | None, reasons)`. `None` = nem
    progresso nem regressao (o chamador decide entre `STABLE` e
    `PLATEAU_SUSPECTED` olhando a serie inteira).

    Quando a atual tem menos series working que a anterior, `reasons` ganha
    `REASON_FEWER_SETS` no fim - informativo, nunca muda o status.

    Lote T3: exposicao com serie por tempo/maximo de reps -> `(None,
    [REASON_MEASURE_NOT_REP_BASED])` (nem progresso nem regressao).
    """
    if previous.has_non_rep_sets or current.has_non_rep_sets:
        return None, [REASON_MEASURE_NOT_REP_BASED]
    status, reasons = _compare(previous, current, thresholds)
    if _working_set_count(current) < _working_set_count(previous):
        reasons.append(REASON_FEWER_SETS)
    return status, reasons


def _same_reps_rpe_change(
    previous: ExposureSummary,
    current: ExposureSummary,
    paired: Optional[PairedTopReps],
) -> Optional[float]:
    """Quanto o RPE SUBIU (positivo = mais dificil, 0 = igual) quando as reps
    cruas emparelhadas sao IGUAIS serie a serie; `None` quando as reps cruas
    diferem ou o RPE nao e conhecido (P8).

    Com RPE em todas as series emparelhadas (`effort_adjusted`), mede pelas
    proprias series: reps iguais, entao a queda de `reps + RIR` e a alta do
    RPE. Senao, pelo RPE medio das duas exposicoes (os dois precisam existir)."""
    if paired is None or paired.previous_reps != paired.current_reps:
        return None
    if paired.effort_adjusted:
        return _clean_number(-paired.delta)
    if current.avg_rpe is None or previous.avg_rpe is None:
        return None
    return _clean_number(float(current.avg_rpe) - float(previous.avg_rpe))


def _compare(
    previous: ExposureSummary,
    current: ExposureSummary,
    thresholds: Mapping[str, Any],
) -> tuple[Optional[ExerciseProgressionStatus], list[str]]:
    reasons: list[str] = []
    tolerance = float(thresholds["same_load_tolerance_pct"])
    e1rm_pct = float(thresholds["e1rm_progress_pct"])
    e1rm_max_reps = int(thresholds["e1rm_max_reliable_reps"])
    rpe_delta = float(thresholds["rpe_improvement_delta"])

    convention = current.load_convention
    reps_only = compares_reps_only(convention)

    previous_load = previous.directional_load
    current_load = current.directional_load
    same_load = True if reps_only else _same_load(current_load, previous_load, tolerance)
    # "carga subiu" = fora da tolerancia de "mesma carga" E para cima. Em
    # `assisted` a direcao ja veio invertida por `directional_load`.
    load_up = (
        not reps_only
        and not same_load
        and current_load is not None
        and previous_load is not None
        and current_load > previous_load
    )

    # Reps POR SERIE emparelhada nas cargas de topo (nunca a soma de todas as
    # series: menos series nao e menos desempenho), como `reps + RIR` quando
    # todas as emparelhadas tem RPE (M78).
    paired = paired_top_reps(previous, current, tolerance)
    reps_delta = None if paired is None else paired.delta
    effort_adjusted = paired is not None and paired.effort_adjusted
    reps_up = reps_delta is not None and reps_delta > 0
    reps_down = reps_delta is not None and reps_delta < 0
    reps_equal = reps_delta == 0

    if reps_only:
        reasons.append(REASON_BODYWEIGHT_REPS_ONLY)

    # --- Mesmas reps, RPE maior (P8) ----------------------------------------
    # Mesma carga de topo e as MESMAS reps cruas emparelhadas: o RPE mais alto
    # nao vira regressao (nem pelo e1RM com RIR, nem por `reps + RIR`). RPE
    # igual ou menor segue as regras abaixo sem mudanca (igual = mesmo e1RM =
    # sem mudanca; menor = progresso).
    rpe_change = _same_reps_rpe_change(previous, current, paired) if same_load else None
    if rpe_change is not None and rpe_change > 0:
        reasons.append(REASON_SAME_REPS_HIGHER_RPE)
        return None, reasons

    # --- e1RM: criterio principal, simetrico --------------------------------
    # So decide quando a serie do e1RM tem ate `e1rm_max_reliable_reps` reps
    # nos dois lados; senao cai nas regras de reps por serie abaixo.
    e1rm_delta = _pct_change(current.e1rm, previous.e1rm)
    if e1rm_delta is not None and e1rm_is_decisive(previous, current, e1rm_max_reps):
        if e1rm_delta >= e1rm_pct:
            reasons.append(REASON_E1RM_UP)
            return ExerciseProgressionStatus.PROGRESSING, reasons
        if e1rm_delta <= -e1rm_pct:
            reasons.append(REASON_E1RM_DOWN)
            return ExerciseProgressionStatus.REGRESSING, reasons

    # O ajuste so explica a leitura das reps na MESMA carga (M78).
    if same_load and effort_adjusted:
        reasons.append(REASON_PAIRED_REPS_EFFORT_ADJUSTED)

    # --- PROGRESSING -------------------------------------------------------
    if same_load and reps_up:
        reasons.append(REASON_SAME_LOAD_MORE_REPS)
        return ExerciseProgressionStatus.PROGRESSING, reasons

    if load_up and _reps_in_prescribed_range(current, tolerance):
        reasons.append(REASON_LOAD_UP_REPS_IN_RANGE)
        return ExerciseProgressionStatus.PROGRESSING, reasons

    # Com reps + RIR o esforco ja esta na comparacao: o RPE medio menor nao
    # pode transformar um empate (11@9 contra 12@10) em progresso.
    if (
        same_load
        and reps_equal
        and not effort_adjusted
        and current.avg_rpe is not None
        and previous.avg_rpe is not None
        and (current.avg_rpe - previous.avg_rpe) <= -rpe_delta
    ):
        reasons.append(REASON_SAME_PERFORMANCE_LOWER_RPE)
        return ExerciseProgressionStatus.PROGRESSING, reasons

    # --- REGRESSING --------------------------------------------------------
    if same_load and reps_down:
        reasons.append(REASON_SAME_LOAD_FEWER_REPS)
        return ExerciseProgressionStatus.REGRESSING, reasons

    # --- Inconclusivo ------------------------------------------------------
    # Mais carga com menos reps NAO e progresso nem regressao por si: so o
    # e1RM decide, e ele ja foi avaliado acima (ou e `None` por falta de RPE
    # ou nao e confiavel acima do teto de reps).
    if load_up and reps_down:
        reasons.append(REASON_MORE_LOAD_FEWER_REPS)
        if current.e1rm is None or previous.e1rm is None:
            reasons.append(REASON_NO_E1RM_WITHOUT_RPE)
        return None, reasons

    if current.e1rm is None or previous.e1rm is None:
        reasons.append(REASON_NO_E1RM_WITHOUT_RPE)
    if all(code in (REASON_BODYWEIGHT_REPS_ONLY, REASON_PAIRED_REPS_EFFORT_ADJUSTED) for code in reasons):
        reasons.append(REASON_NO_CHANGE)
    return None, reasons


@dataclass(frozen=True)
class ComparableWindow:
    """Sequencia comparavel final (`_trailing_comparable_window`)."""

    indices: list[int]
    skipped: int  # outro contexto de fadiga (mesma chave fisica)
    skipped_other_gym: int  # mesma maquina em OUTRA academia (issue #188)


def _trailing_comparable_window(exposures: Sequence[ExposureSummary]) -> ComparableWindow:
    """Indices (ordem cronologica) da sequencia comparavel final e quantas
    exposicoes foram puladas dentro dela.

    Para tras a partir da ultima:
    - chave FISICA diferente (exercicio/equipamento/convencao) INTERROMPE: uma
      troca de maquina no meio do historico corta a serie comparavel em vez
      de ser saltada silenciosamente;
    - OUTRO objetivo de treino (M97 F1) e PULADO sem contar: a exposicao de
      forca nao entra na sequencia de hipertrofia nem a corta (as leituras do
      dashboard ja chegam filtradas);
    - mesma maquina em OUTRA academia (issue #188) e PULADA e contada em
      `skipped_other_gym`: voltar a academia reaproveita a sequencia dela;
    - mesma chave fisica e OUTRO contexto de fadiga e PULADA e contada em
      `skipped`: o supino feito 5o na quinta nao interrompe a sequencia do
      supino feito 1o na segunda - so nao entra nela (e tem a sua propria)."""
    if not exposures:
        return ComparableWindow([], 0, 0)
    last = exposures[-1]
    indices: list[int] = []
    skipped = 0
    skipped_other_gym = 0
    for index in range(len(exposures) - 1, -1, -1):
        exposure = exposures[index]
        if not physical_keys_match_any_gym(exposure.key, last.key):
            break
        if not goals_match(exposure.key, last.key):
            continue
        if exposure.key.gym_id != last.key.gym_id:
            skipped_other_gym += 1
            continue
        if not keys_match(exposure.key, last.key):
            skipped += 1
            continue
        indices.append(index)
    indices.reverse()
    return ComparableWindow(indices, skipped, skipped_other_gym)


def in_equipment_calibration(history: Sequence[Any]) -> bool:
    """O contexto de MAQUINA da ultima exposicao de `history` (exposicoes ate
    ela, inclusive) esta calibrando (issue #188, decisao do usuario de
    2026-11-16)? A troca e LOCAL ao segmento atual, nunca procurada no
    historico todo: a decisao nao muda com o tamanho da leitura.

    Segmento = exposicoes consecutivas, andando para tras a partir da ultima,
    com a mesma chave fisica e a mesma academia (qualquer contexto de fadiga
    ou objetivo), pulando as de OUTRA academia. Calibra quando: a ultima e de
    maquina (`gym_id`), o segmento tem menos sessoes que o minimo do dono
    `equipment_context` e foi ABERTO por uma troca - a exposicao que o
    interrompeu tem aparelho identificado e e de OUTRO aparelho, ou houve
    exposicao de outra academia pulada. Sem aparelho identificado, outra
    convencao no mesmo aparelho ou o comeco do historico lido nao sao troca."""
    # regra unica no dono (`equipment_context.context_in_calibration`); a chave
    # fisica sem academia so casa quando o aparelho esta resolvido (`keys_match`)
    return context_in_calibration(
        [
            (
                calibration_physical_key(item.key),
                item.key.equipment_id,
                item.key.gym_id,
            )
            for item in history
        ]
    )


@dataclass(frozen=True)
class ComparableRun:
    """A sequencia comparavel que a classificacao USA (issue #188): a janela
    bruta (`_trailing_comparable_window`) mais a calibracao do contexto de
    maquina. Em calibracao nenhuma exposicao foi comparada: `indices` vazio.
    A janela crua (`_window`) e interna: consumidores leem `indices` ou
    `run_flags`, nunca montam sequencia por conta propria."""

    _window: ComparableWindow
    in_calibration: bool

    @property
    def indices(self) -> list[int]:
        return [] if self.in_calibration else self._window.indices


#: Janela do veredito (decisao do usuario de 2026-11-16, "veredito sobre 8 em
#: tudo"): toda leitura decide sobre as ULTIMAS N exposicoes do exercicio
#: (inclusive a atual) - sequencia comparavel, par ("anterior"), plato,
#: contagem de comparaveis e calibracao de maquina. Painel, detalhe, tabela,
#: seta, briefing e Modo Treino decidem sobre o MESMO sufixo; o historico
#: exibido continua inteiro. Nenhuma leitura pode trazer menos exposicoes que
#: isto (`exposure_service.MAX_BATCH_EXPOSURES_PER_EXERCISE` deriva daqui).
VERDICT_LOOKBACK_EXPOSURES = 8


def comparable_run(exposures: Sequence[ExposureSummary]) -> ComparableRun:
    """UNICO ponto que decide a sequencia comparavel e a calibracao, e UNICO
    corte da janela do veredito: so as ultimas `VERDICT_LOOKBACK_EXPOSURES`
    de `exposures` sao lidas, entao o resultado nao depende de quantas
    exposicoes a leitura trouxe. Os indices devolvidos sao posicoes na lista
    ORIGINAL `exposures` (o deslocamento do corte e somado)."""
    offset = max(0, len(exposures) - VERDICT_LOOKBACK_EXPOSURES)
    recent = exposures[offset:]
    window = _trailing_comparable_window(recent)
    window = ComparableWindow([offset + index for index in window.indices], window.skipped, window.skipped_other_gym)
    return ComparableRun(window, in_equipment_calibration(recent))


def physical_switch_reasons(previous: ComparabilityKey, current: ComparabilityKey) -> list[str]:
    """Motivos de troca FISICA (equipamento/convencao) da atual em relacao a
    IMEDIATAMENTE anterior; vazio quando nao trocou. So outra academia
    (issue #188), outro contexto de fadiga ou objetivo nao e troca fisica: a
    sequencia do contexto atual e filtrada em `comparable_run`."""
    if physical_keys_match(previous, current) or only_gym_differs(previous, current):
        return []
    reasons = [REASON_KEY_CHANGED]
    if current.equipment_id is None and not current.equipment_agnostic:
        reasons.append(REASON_EQUIPMENT_UNKNOWN)
    return reasons


@dataclass(frozen=True)
class ComparablePair:
    """O par "anterior vs atual" da ULTIMA exposicao (`latest_comparable_pair`).

    `previous_index`/`current_index` = posicoes em `exposures`. Sem par,
    `previous_index` e `None` e `reasons` sao os MESMOS motivos do veredito
    (troca fisica, serie nao baseada em reps, calibracao, poucas exposicoes,
    primeira do contexto). Com par, `reasons` e vazio. Decidido so em
    `_decide`, junto com o veredito."""

    previous_index: Optional[int]
    current_index: Optional[int]
    reasons: list[str]
    #: Resultado da TRANSICAO deste par (seta por sessao): `PROGRESSING`,
    #: `REGRESSING` ou `STABLE` com par; `NOT_COMPARABLE` sem par. O veredito
    #: do exercicio pode ser plato; a transicao nao.
    transition_status: Optional[ExerciseProgressionStatus] = None
    #: Motivos da transicao: os do veredito sem o de plato (sem par: `reasons`).
    transition_reasons: list[str] = field(default_factory=list)
    #: O e1RM decidiu sozinho neste par (`e1rm_is_decisive`, teto
    #: `e1rm_max_reliable_reps`)? Sempre `False` sem par.
    e1rm_decisive: bool = False

    @property
    def comparable(self) -> bool:
        return self.previous_index is not None


def latest_comparable_pair(
    exposures: Sequence[ExposureSummary],
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> ComparablePair:
    """A ANTERIOR comparavel da ultima exposicao - a MESMA que o veredito
    compara (`progression_outcome(...).pair`)."""
    return progression_outcome(exposures, thresholds=thresholds).pair


# Direcao "melhor quando" de cada metrica do par (o frontend so pinta):
# `up` = subir e melhora, `down` = descer e melhora, `neutral` = informativo.
BETTER_UP = "up"
BETTER_DOWN = "down"
BETTER_NEUTRAL = "neutral"
#: Metricas do par "anterior vs atual" e a direcao padrao de cada uma.
PAIR_METRIC_BETTER_WHEN: dict[str, str] = {
    "best_load_kg": BETTER_UP,
    "paired_reps": BETTER_UP,
    # Volume, nao desempenho: menos series nao e regressao.
    "total_reps": BETTER_NEUTRAL,
    "avg_rpe": BETTER_DOWN,
    "e1rm": BETTER_UP,
    "tonnage": BETTER_NEUTRAL,
}


def metric_better_when(code: str, load_convention: Any, *, e1rm_decisive: bool) -> str:
    """Direcao de `code` no par: carga segue a convencao (`assisted` inverte,
    `bodyweight` so compara reps), e1RM nao decisivo e informativo."""
    if code == "best_load_kg":
        convention = normalize_load_convention(load_convention)
        if compares_reps_only(convention):
            return BETTER_NEUTRAL
        return BETTER_DOWN if inverts_load_direction(convention) else BETTER_UP
    if code == "e1rm" and not e1rm_decisive:
        return BETTER_NEUTRAL
    return PAIR_METRIC_BETTER_WHEN[code]


#: Pontos com valor que uma metrica precisa para virar a linha de tendencia.
TREND_MIN_POINTS = 2


def trend_metric(run: Sequence[Mapping[str, Any]], load_convention: Any) -> Optional[str]:
    """Metrica da linha de tendencia do exercicio (o frontend so le).

    `run` = exposicoes da sequencia do contexto atual (`in_context_run`);
    `load_convention` = a da chave atual. Peso corporal (`compares_reps_only`)
    prefere `total_reps`; os demais preferem `e1rm`; depois `best_load_kg`,
    depois `total_reps`. Uma metrica entra com `TREND_MIN_POINTS` valores
    presentes; `None` quando nenhuma tem."""
    reps_only = compares_reps_only(normalize_load_convention(load_convention))

    def enough(code: str) -> bool:
        return sum(1 for item in run if item.get(code) is not None) >= TREND_MIN_POINTS

    if not reps_only and enough("e1rm"):
        return "e1rm"
    if reps_only and enough("total_reps"):
        return "total_reps"
    for code in ("best_load_kg", "total_reps"):
        if enough(code):
            return code
    return None


def metric_change(previous: Optional[float], current: Optional[float]) -> dict[str, Optional[float]]:
    """Variacao absoluta (atual - anterior) e percentual (pontos, `_pct_change`
    x 100); `None` quando falta um lado ou o anterior e 0."""
    if previous is None or current is None:
        return {"delta": None, "delta_pct": None}
    pct = _pct_change(float(current), float(previous))
    return {
        "delta": _clean_number(float(current) - float(previous)),
        "delta_pct": None if pct is None else pct * 100,
    }


@dataclass(frozen=True)
class ProgressionOutcome:
    """Resultado UNICO da classificacao de um exercicio: o que painel em lote,
    drill-down, tela de progressao e briefing expoem (issue #188)."""

    status: ExerciseProgressionStatus
    reasons: list[str]
    run: ComparableRun
    #: Par "anterior vs atual" que o veredito comparou (sem anterior: os
    #: mesmos motivos de `reasons`).
    pair: ComparablePair

    @property
    def in_calibration(self) -> bool:
        return self.run.in_calibration

    @property
    def run_indices(self) -> list[int]:
        """Posicoes (em `exposures`) da sequencia comparavel; vazia em calibracao."""
        return self.run.indices

    @property
    def comparable_count(self) -> int:
        """Exposicoes comparaveis: o tamanho de `run_indices` (0 em calibracao)."""
        return len(self.run.indices)


def progression_outcome(
    exposures: Sequence[ExposureSummary],
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> ProgressionOutcome:
    """Status, motivos, sequencia comparavel, calibracao e par de uma vez -
    ver `classify_exercise_progression` (status) e `comparable_run`.
    `exposures` = historico do exercicio em ordem cronologica; a janela do
    veredito (`VERDICT_LOOKBACK_EXPOSURES`) e aplicada uma vez, em
    `comparable_run`, e `_decide` so le as posicoes que ela devolve (indices
    da lista original)."""
    run = comparable_run(exposures)
    status, reasons, pair = _decide(exposures, run, thresholds)
    return ProgressionOutcome(status, reasons, run, pair)


# ---------------------------------------------------------------------------
# Razao de desempenho do par (sinal "Desempenho recente" da recuperacao).
# Vinda de `exercise_performance.py` (issue #188) SEM mudar a formula: a
# recuperacao so classifica a razao (limiares 0,95/0,90 do dono dela).
# ---------------------------------------------------------------------------


def paired_reps_ratio(
    previous: ExposureSummary,
    current: ExposureSummary,
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> Optional[float]:
    """Razao BRUTA das reps emparelhadas (soma atual / soma anterior).
    Informativa: NAO e a magnitude do sinal (ver `paired_reps_epley_ratio`)."""
    paired = paired_top_reps(previous, current, float(thresholds["same_load_tolerance_pct"]))
    if paired is None:
        return None
    reference = sum(paired.previous_reps)
    if reference <= 0:
        return None
    return sum(paired.current_reps) / reference


def paired_reps_epley_ratio(
    previous: ExposureSummary,
    current: ExposureSummary,
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> Optional[float]:
    """Magnitude quando o e1RM NAO e decisivo: equivalente Epley (dono
    `domain/e1rm.epley_multiplier`) na MESMA carga das reps emparelhadas de
    topo (`paired_top_reps`):

        ratio = epley(valor_atual_med) / epley(valor_anterior_med)

    `valor_*_med` = media, por serie, do valor que a regra de reps compara
    (`reps + RIR` quando todas as emparelhadas tem RPE, senao reps cruas nos
    dois lados). A razao bruta de reps seria severa demais para limiares
    calibrados em e1RM."""
    paired = paired_top_reps(previous, current, float(thresholds["same_load_tolerance_pct"]))
    if paired is None:
        return None
    prev_mean = sum(paired.previous) / paired.pairs
    curr_mean = sum(paired.current) / paired.pairs
    return _e1rm.epley_multiplier(curr_mean, 0.0) / _e1rm.epley_multiplier(prev_mean, 0.0)


def performance_ratio_of(
    exposures: Sequence[ExposureSummary],
    *,
    outcome: Optional[ProgressionOutcome] = None,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> Optional[float]:
    """Razao atual / anterior do PAR que o veredito comparou
    (`progression_outcome(...).pair`). `None` sem par (troca fisica, serie
    nao baseada em reps, calibracao, poucas exposicoes, outro contexto).

    Magnitude: razao de e1RM quando o e1RM e decisivo no par, senao
    `paired_reps_epley_ratio`. Transicao `REGRESSING` -> magnitude com teto
    1,0 (`None` sem magnitude); qualquer outra -> piso 1,0 (o que a regra de
    progresso nao chama de queda nunca vira sinal negativo)."""
    if outcome is None:
        outcome = progression_outcome(exposures, thresholds=thresholds)
    pair = outcome.pair
    if pair.previous_index is None or pair.current_index is None:
        return None
    previous, current = exposures[pair.previous_index], exposures[pair.current_index]
    magnitude: Optional[float] = None
    if pair.e1rm_decisive and previous.e1rm:
        magnitude = float(current.e1rm) / float(previous.e1rm)
    if magnitude is None:
        magnitude = paired_reps_epley_ratio(previous, current, thresholds=thresholds)
    if pair.transition_status is ExerciseProgressionStatus.REGRESSING:
        return None if magnitude is None else min(magnitude, 1.0)
    return 1.0 if magnitude is None else max(magnitude, 1.0)


def latest_session_performance_ratios(
    exposures: Sequence[ExposureSummary],
    session_ids: Sequence[Any],
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> dict[int, float]:
    """Razao (`performance_ratio_of`) de CADA passagem da ultima sessao contra
    o historico ate a ultima exposicao de OUTRA sessao: `{indice: razao}`.

    Regra da recuperacao: duas passagens pelo mesmo exercicio na mesma sessao
    medem fadiga intra-sessao, nao recuperacao - a anterior nunca e da propria
    sessao. Sem exposicao de outra sessao -> `{}`. `session_ids[i]` identifica
    a sessao de `exposures[i]` (ordem cronologica). As telas de progressao NAO
    usam isto: la duas passagens seguem sendo duas exposicoes."""
    if not exposures or len(session_ids) != len(exposures):
        return {}
    last_session = session_ids[-1]
    previous_index = next(
        (index for index in range(len(exposures) - 1, -1, -1) if session_ids[index] != last_session), None
    )
    if previous_index is None:
        return {}
    history = list(exposures[: previous_index + 1])
    ratios: dict[int, float] = {}
    for index in range(previous_index + 1, len(exposures)):
        ratio = performance_ratio_of([*history, exposures[index]], thresholds=thresholds)
        if ratio is not None:
            ratios[index] = ratio
    return ratios


#: Desfecho de uma metrica do par (o frontend so pinta): melhorou, piorou,
#: igual ou informativo (sem variacao ou `better_when` neutro).
OUTCOME_IMPROVED = "improved"
OUTCOME_WORSENED = "worsened"
OUTCOME_FLAT = "flat"
OUTCOME_NEUTRAL = "neutral"


def metric_outcome(delta: Optional[float], better_when: str) -> str:
    """Desfecho de uma metrica a partir da variacao (`metric_change`) e da
    direcao (`metric_better_when`): sem delta ou direcao neutra -> `neutral`;
    delta zero -> `flat`; senao `improved`/`worsened` conforme a direcao."""
    if delta is None or better_when == BETTER_NEUTRAL:
        return OUTCOME_NEUTRAL
    if delta == 0:
        return OUTCOME_FLAT
    subiu = delta > 0
    return OUTCOME_IMPROVED if subiu == (better_when == BETTER_UP) else OUTCOME_WORSENED


def run_flags(outcome: ProgressionOutcome, size: int) -> list[dict[str, bool]]:
    """Duas marcacoes por exposicao (mesma ordem de `exposures`, tamanho
    `size`), as duas do dono:
    - `in_context_run`: na sequencia do contexto atual (mesmo exercicio,
      aparelho, convencao, academia, objetivo e contexto de fadiga, cortada
      por troca fisica) - inclusive em calibracao; e o que o grafico plota;
    - `in_verdict_run`: usada pelo veredito (`run_indices`; vazia em
      calibracao); e o que o grafico destaca."""
    contexto = set(outcome.run._window.indices)
    veredito = set(outcome.run_indices)
    return [
        {"in_context_run": index in contexto, "in_verdict_run": index in veredito}
        for index in range(size)
    ]


def classify_exercise_progression(
    exposures: Sequence[ExposureSummary],
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> tuple[ExerciseProgressionStatus, list[str]]:
    """Status do exercicio a partir das exposicoes JA filtradas pela janela de
    `exposure_lookback_days` e ordenadas em ordem CRONOLOGICA (mais antiga
    primeiro). Implementa o §8.2 na ordem em que o plano o escreve.
    Casca de `progression_outcome`.
    """
    outcome = progression_outcome(exposures, thresholds=thresholds)
    return outcome.status, outcome.reasons


def _decide(
    exposures: Sequence[ExposureSummary],
    comparable: ComparableRun,
    thresholds: Mapping[str, Any],
) -> tuple[ExerciseProgressionStatus, list[str], ComparablePair]:
    """UNICA decisao do veredito e do par, nesta ordem: troca fisica -> serie
    nao baseada em reps -> calibracao -> poucas exposicoes/primeira do
    contexto -> par comparado. Sem par, o par leva os motivos do veredito."""
    current = len(exposures) - 1 if exposures else None

    def sem_par(status: ExerciseProgressionStatus, reasons: list[str]):
        return status, reasons, ComparablePair(
            None,
            current,
            list(reasons),
            ExerciseProgressionStatus.NOT_COMPARABLE,
            list(reasons),
        )

    # Um par exige duas exposicoes, qualquer que seja o minimo configurado.
    min_comparable = max(int(thresholds["min_comparable_exposures"]), 2)
    plateau_min = int(thresholds["plateau_min_exposures"])
    calibrating = comparable.in_calibration

    if len(exposures) < min_comparable:
        return sem_par(
            ExerciseProgressionStatus.INSUFFICIENT_DATA,
            [REASON_EQUIPMENT_CALIBRATING if calibrating else REASON_NOT_ENOUGH_EXPOSURES],
        )

    last, previous = exposures[-1], exposures[-2]
    # Troca FISICA (equipamento/convencao) em relacao a imediatamente
    # anterior: NOT_COMPARABLE, como sempre. Troca so de contexto de fadiga
    # ou so de academia (issue #188) nao: a sequencia do contexto atual e
    # filtrada abaixo.
    switch = physical_switch_reasons(previous.key, last.key)
    if switch:
        return sem_par(ExerciseProgressionStatus.NOT_COMPARABLE, switch)
    window = comparable._window
    # Lote T3 (medidas de serie): serie por tempo/maximo de reps na ultima
    # exposicao ou na anterior COMPARADA (a da sequencia, nao a pulada por
    # outro contexto/academia) -> NOT_COMPARABLE (a carga/reps dessa serie
    # nao respondem a pergunta de progressao de carga).
    compared = exposures[window.indices[-2]] if len(window.indices) >= 2 else None
    if last.has_non_rep_sets or (compared is not None and compared.has_non_rep_sets):
        return sem_par(ExerciseProgressionStatus.NOT_COMPARABLE, [REASON_MEASURE_NOT_REP_BASED])

    skipped = window.skipped
    run = [exposures[index] for index in window.indices]
    gym_reasons = [REASON_OTHER_GYM_SKIPPED] if window.skipped_other_gym else []
    # Contexto de maquina em calibracao depois de uma troca (issue #188):
    # nunca progresso, regressao nem plato, mesmo com reps/carga mudando.
    if calibrating or len(run) < min_comparable:
        reasons = [REASON_EQUIPMENT_CALIBRATING if calibrating else REASON_NOT_ENOUGH_EXPOSURES]
        if skipped and len(run) == 1:
            reasons.append(REASON_FIRST_IN_CONTEXT)
        return sem_par(ExerciseProgressionStatus.INSUFFICIENT_DATA, [*reasons, *gym_reasons])

    previous_index = window.indices[-2]
    status, reasons = compare_exposures(exposures[previous_index], last, thresholds=thresholds)
    if skipped:
        reasons = [*reasons, REASON_OTHER_CONTEXT_SKIPPED]
    reasons = [*reasons, *gym_reasons]
    pair = ComparablePair(
        previous_index,
        current,
        [],
        status or ExerciseProgressionStatus.STABLE,
        list(reasons),
        e1rm_is_decisive(exposures[previous_index], last, int(thresholds["e1rm_max_reliable_reps"])),
    )
    if status is not None:
        return status, reasons, pair

    # Sem progresso nem regressao na ultima transicao: so e "suspeita de
    # plato" se NENHUMA transicao do run comparavel tiver progredido ou
    # regredido, com pelo menos `plateau_min` exposicoes.
    if len(run) >= plateau_min:
        movimento = any(
            compare_exposures(run[index - 1], run[index], thresholds=thresholds)[0] is not None
            for index in range(1, len(run))
        )
        if not movimento:
            return ExerciseProgressionStatus.PLATEAU_SUSPECTED, [*reasons, REASON_PLATEAU_RUN], pair

    return ExerciseProgressionStatus.STABLE, reasons, pair


# ---------------------------------------------------------------------------
# Contagem e agregacao por grupo (2026-11-01, "contagem unica de progressao")
#
# A home ("Estou evoluindo?") e `/dashboard/progressao` contam a MESMA coisa:
# o status de cada EXERCICIO (`classify_exercise_progression`). O grupo muscular
# e uma agregacao PONDERADA desses status, nunca uma classificacao propria
# (antes: banda de +-5% sobre o e1RM em `muscle_group_performance._status`).
# ---------------------------------------------------------------------------

#: Status que contam como "comparaveis" (denominador da frase da home).
COMPARABLE_PROGRESSION_STATUSES: tuple[ExerciseProgressionStatus, ...] = (
    ExerciseProgressionStatus.PROGRESSING,
    ExerciseProgressionStatus.STABLE,
    ExerciseProgressionStatus.PLATEAU_SUSPECTED,
    ExerciseProgressionStatus.REGRESSING,
)

#: Status de "progresso ok" (progredindo ou estavel): numerador da fracao da
#: recomposicao (`progression_share`). Plato e regressao ficam fora.
PROGRESS_OK_STATUSES: tuple[ExerciseProgressionStatus, ...] = (
    ExerciseProgressionStatus.PROGRESSING,
    ExerciseProgressionStatus.STABLE,
)
_COMPARABLE_VALUES = frozenset(status.value for status in COMPARABLE_PROGRESSION_STATUSES)
_PROGRESS_OK_VALUES = frozenset(status.value for status in PROGRESS_OK_STATUSES)


def _status_value(status: Any) -> Any:
    return getattr(status, "value", status)


def is_comparable_status(status: Any) -> bool:
    """O status (enum ou codigo) e comparavel (`COMPARABLE_PROGRESSION_STATUSES`)?"""
    return _status_value(status) in _COMPARABLE_VALUES


def is_progress_ok_status(status: Any) -> bool:
    """O status (enum ou codigo) e progredindo ou estavel (`PROGRESS_OK_STATUSES`)?"""
    return _status_value(status) in _PROGRESS_OK_VALUES


#: Menos exercicios comparaveis do que isto e a leitura geral e fraca (§7).
MIN_COMPARABLE_FOR_RELIABLE_ANSWER = 3

#: Peso minimo (soma do `stimulus_weight` dos exercicios COMPARAVEIS do grupo)
#: para o grupo receber status. Abaixo: INSUFFICIENT_DATA / LOW_ACTIVATION.
#: Mesmo valor que ja valia na tela por grupo (antes `MIN_ACTIVATION_SUM`).
GROUP_MIN_COMPARABLE_WEIGHT = 0.5

#: Motivos de um grupo sem status (None quando ha status).
GROUP_REASON_NO_COMPARABLE_EXERCISE = "NO_COMPARABLE_EXERCISE"
GROUP_REASON_LOW_ACTIVATION = "LOW_ACTIVATION"


#: Chave de cada status em `progression_counts` (quem lista por status le a
#: contagem por aqui, sem contar de novo).
PROGRESSION_COUNT_KEYS: dict[ExerciseProgressionStatus, str] = {
    ExerciseProgressionStatus.PROGRESSING: "progressing",
    ExerciseProgressionStatus.STABLE: "stable",
    ExerciseProgressionStatus.PLATEAU_SUSPECTED: "plateau_suspected",
    ExerciseProgressionStatus.REGRESSING: "regressing",
    ExerciseProgressionStatus.NOT_COMPARABLE: "not_comparable",
    ExerciseProgressionStatus.INSUFFICIENT_DATA: "insufficient_data",
}


#: Tendencia de desempenho (briefing) de cada status comparavel: o plato e
#: "mantendo" como o estavel. Fora daqui ninguem reduz o status.
TREND_PROGRESSING = "progressing"
TREND_MAINTAINING = "maintaining"
TREND_REGRESSING = "regressing"
PERFORMANCE_TREND: dict[ExerciseProgressionStatus, str] = {
    ExerciseProgressionStatus.PROGRESSING: TREND_PROGRESSING,
    ExerciseProgressionStatus.STABLE: TREND_MAINTAINING,
    ExerciseProgressionStatus.PLATEAU_SUSPECTED: TREND_MAINTAINING,
    ExerciseProgressionStatus.REGRESSING: TREND_REGRESSING,
}


def performance_trend(status: Any) -> Optional[str]:
    """Tendencia (`TREND_*`) do status (enum ou codigo); `None` quando o
    status nao e comparavel ou e desconhecido."""
    try:
        return PERFORMANCE_TREND.get(ExerciseProgressionStatus(status))
    except ValueError:
        return None


@dataclass(frozen=True)
class WindowTrend:
    """Tendencia da JANELA do exercicio (issue #176): segunda leitura, ao lado
    de `classify_exercise_progression` (ultima vs. anterior), que ela nao
    substitui. `status` e do mesmo enum do veredito (rotulos ja existentes)."""

    status: ExerciseProgressionStatus
    reasons: list[str]
    change_pct: Optional[float] = None  # fracao: 0.05 = +5%
    initial_mean: Optional[float] = None
    final_mean: Optional[float] = None
    exposures_used: int = 0


def window_trend(
    exposures: Sequence[ExposureSummary],
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> WindowTrend:
    """Media do e1RM da metade inicial vs. da metade final das exposicoes da
    janela com a MESMA chave de comparabilidade da ultima (`keys_match`) e e1RM
    confiavel (<= `e1rm_max_reliable_reps` reps, como `e1rm_is_decisive`).
    `exposures` em ordem cronologica; com N impar a do meio fica de fora.
    PROGRESSING/REGRESSING a partir de `e1rm_progress_pct`; abaixo de
    `window_trend_min_exposures` ou em calibracao de maquina: INSUFFICIENT_DATA."""
    minimo = int(thresholds["window_trend_min_exposures"])
    max_reps = int(thresholds["e1rm_max_reliable_reps"])
    last = exposures[-1] if exposures else None
    valid = (
        []
        if last is None or in_equipment_calibration(exposures)
        else [
            item.e1rm
            for item in exposures
            if keys_match(item.key, last.key)
            and not item.has_non_rep_sets
            and item.e1rm is not None
            and item.e1rm_reps is not None
            and item.e1rm_reps <= max_reps
        ]
    )
    if len(valid) < minimo or len(valid) < 2:
        reason = (
            REASON_EQUIPMENT_CALIBRATING
            if last is not None and in_equipment_calibration(exposures)
            else REASON_NOT_ENOUGH_EXPOSURES
        )
        return WindowTrend(ExerciseProgressionStatus.INSUFFICIENT_DATA, [reason], exposures_used=len(valid))
    half = len(valid) // 2
    initial = sum(valid[:half]) / half
    final = sum(valid[-half:]) / half
    if initial <= 0:
        return WindowTrend(ExerciseProgressionStatus.INSUFFICIENT_DATA, [REASON_NO_E1RM_WITHOUT_RPE], exposures_used=len(valid))
    change = (final - initial) / initial
    limit = float(thresholds["e1rm_progress_pct"])
    if change >= limit:
        status, reason = ExerciseProgressionStatus.PROGRESSING, REASON_E1RM_UP
    elif change <= -limit:
        status, reason = ExerciseProgressionStatus.REGRESSING, REASON_E1RM_DOWN
    else:
        status, reason = ExerciseProgressionStatus.STABLE, REASON_NO_CHANGE
    return WindowTrend(status, [reason], change, initial, final, len(valid))


def progression_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Contagem por status dos itens de `get_exercises_progression_batch`
    (`item["status"]`). Fonte unica das contagens da home e de
    `/dashboard/progressao`. `comparable_total` = progredindo + estavel +
    suspeita de platô + regredindo."""
    tally = {status.value: 0 for status in ExerciseProgressionStatus}
    for item in items:
        status = str(item["status"])
        if status in tally:
            tally[status] += 1
    counts = {key: tally[status.value] for status, key in PROGRESSION_COUNT_KEYS.items()}
    counts["comparable_total"] = sum(tally[status.value] for status in COMPARABLE_PROGRESSION_STATUSES)
    return counts


def progression_share(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Resumo pronto da recomposicao (insight e corpo): sobre os itens do lote
    (`item["status"]`), `comparable` = quantos tem status comparavel,
    `progressing_or_stable` = quantos desses progridem ou estao estaveis e
    `share` = `progressing_or_stable / comparable` (`None` sem comparavel).
    Comparar `comparable` com `insight_recomp_min_comparable_exercises` e
    `share` com `insight_recomp_progress_share` e de quem consome."""
    comparable = [item for item in items if is_comparable_status(item.get("status"))]
    ok = sum(1 for item in comparable if is_progress_ok_status(item.get("status")))
    return {
        "comparable": len(comparable),
        "progressing_or_stable": ok,
        "share": ok / len(comparable) if comparable else None,
    }


def progression_total(counts: Mapping[str, int]) -> int:
    """Exercicios no periodo (todos os status)."""
    return int(counts["comparable_total"]) + int(counts["not_comparable"]) + int(counts["insufficient_data"])


def progression_answer(counts: Mapping[str, int]) -> str:
    """A frase de "Estou evoluindo?" (home e `/dashboard/progressao`)."""
    total = progression_total(counts)
    comparable = int(counts["comparable_total"])
    if total == 0:
        return "Nenhum exercício com série de trabalho no período."
    if comparable == 0:
        return f"{total} exercícios no período, nenhum com duas exposições comparáveis ainda."
    return (
        f"{counts['progressing']} de {comparable} exercícios comparáveis progredindo; "
        f"{counts['plateau_suspected']} com suspeita de platô e {counts['regressing']} regredindo."
    )


def exercise_progression_summary(
    items: Sequence[Mapping[str, Any]], *, window_days: int, period_start: Any, period_end: Any, truncated: bool = False
) -> dict[str, Any]:
    """Bloco `exercise_progression` da tela por grupo: contagem + frase do
    dono sobre os MESMOS itens que a home recebe do lote
    (`get_exercises_progression_batch`) para o mesmo periodo."""
    counts = progression_counts(items)
    return {
        "window_days": int(window_days),
        "period_start": period_start,
        "period_end": period_end,
        "counts": counts,
        "answer": progression_answer(counts),
        "truncated": bool(truncated),
    }


def aggregate_group_progression(entries: Iterable[tuple[Any, float]]) -> dict[str, Any]:
    """Status de um GRUPO muscular a partir dos status dos seus exercicios.

    `entries`: `(status do exercicio, peso)` - o status e o do dono
    (`classify_exercise_progression`) e o peso e o `stimulus_weight` do
    exercicio naquele grupo (`exercise_muscle_contributions`), sem peso novo.

    Regra: so exercicios COMPARAVEIS entram (nao comparavel / dados
    insuficientes ficam fora). Soma-se o peso de cada balde - progredindo,
    regredindo e "estavel" (estavel + suspeita de platô) - e vence o balde de
    MAIOR peso; empate no topo = ESTAVEL (na duvida, nao ha evidencia de
    mudanca). Peso comparavel total abaixo de `GROUP_MIN_COMPARABLE_WEIGHT` =
    INSUFFICIENT_DATA / `LOW_ACTIVATION`; nenhum comparavel =
    INSUFFICIENT_DATA / `NO_COMPARABLE_EXERCISE`.

    Devolve `status` (PROGRESSING | STABLE | REGRESSING | INSUFFICIENT_DATA),
    `reason`, `comparable_weight`, `counts` (por status de exercicio, mesmas
    chaves de `progression_counts`) e `weights` (progressing/stable/regressing)."""
    counts = {
        "progressing": 0, "stable": 0, "plateau_suspected": 0, "regressing": 0,
        "comparable_total": 0, "not_comparable": 0, "insufficient_data": 0,
    }
    weights = {"progressing": 0.0, "stable": 0.0, "regressing": 0.0}
    key_by_status = {
        ExerciseProgressionStatus.PROGRESSING.value: ("progressing", "progressing"),
        ExerciseProgressionStatus.STABLE.value: ("stable", "stable"),
        ExerciseProgressionStatus.PLATEAU_SUSPECTED.value: ("plateau_suspected", "stable"),
        ExerciseProgressionStatus.REGRESSING.value: ("regressing", "regressing"),
        ExerciseProgressionStatus.NOT_COMPARABLE.value: ("not_comparable", None),
        ExerciseProgressionStatus.INSUFFICIENT_DATA.value: ("insufficient_data", None),
    }
    for status, weight in entries:
        mapped = key_by_status.get(getattr(status, "value", status))
        if mapped is None:
            continue
        count_key, bucket = mapped
        counts[count_key] += 1
        if bucket is not None:
            counts["comparable_total"] += 1
            weights[bucket] += max(float(weight or 0.0), 0.0)
    # Arredonda para nao perder 0,2 + 0,3 = 0,49999... no limiar.
    comparable_weight = round(sum(weights.values()), 6)

    if counts["comparable_total"] == 0:
        status, reason = ExerciseProgressionStatus.INSUFFICIENT_DATA, GROUP_REASON_NO_COMPARABLE_EXERCISE
    elif comparable_weight < GROUP_MIN_COMPARABLE_WEIGHT:
        status, reason = ExerciseProgressionStatus.INSUFFICIENT_DATA, GROUP_REASON_LOW_ACTIVATION
    else:
        reason = None
        top = max(weights.values())
        winners = [name for name, value in weights.items() if round(value - top, 6) == 0]
        if len(winners) > 1:
            status = ExerciseProgressionStatus.STABLE
        else:
            status = {
                "progressing": ExerciseProgressionStatus.PROGRESSING,
                "stable": ExerciseProgressionStatus.STABLE,
                "regressing": ExerciseProgressionStatus.REGRESSING,
            }[winners[0]]
    return {
        "status": status,
        "reason": reason,
        "comparable_weight": comparable_weight,
        "counts": counts,
        "weights": {key: round(value, 6) for key, value in weights.items()},
    }


# ---------------------------------------------------------------------------
# Variacao de e1RM por grupo (INFORMATIVA; nunca gera status). Antes calculada
# em `muscle_group_performance.build_group_performance`; mesma formula e mesmo
# limiar, agora com a academia na chave para maquina (issue #188).
# ---------------------------------------------------------------------------

#: Motivo de exclusao da media informativa (variacao atipica).
GROUP_E1RM_OUTLIER_REASON = "OUTLIER_CHANGE"


def group_e1rm_changes(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Variacao do melhor e1RM entre as duas janelas, por (grupo, chave do dono).

    `rows` = linhas de `analytics._fetch_group_e1rm_rows`: `period`
    (`current`/`previous`), `exercise_id`, `exercise_name`, `equipment_id`,
    `load_convention` (bruta), `gym_id`, `equipment_type_code`,
    `muscle_group_id`, `stimulus_weight`, `best_e1rm_kg`. A chave e
    `build_key` (sem fadiga nem objetivo; academia so para maquina via
    `equipment_context.context_gym_id`); excecao do §8.1 (nenhum equipamento)
    decidida por exercicio nas duas janelas. Devolve uma entrada por par com as
    duas janelas: `muscle_group_id`, `exercise_id`, `exercise_name`,
    `equipment_id`, `load_convention`, `gym_id`, `previous_e1rm_kg`,
    `current_e1rm_kg` (1 casa), `change_pct` (1 casa), `stimulus_weight`
    (3 casas), `outlier` (`|change| > GROUP_E1RM_OUTLIER_CHANGE_PCT`),
    `excluded_reason` e `_change` (variacao sem arredondar, para a media)."""
    rows = list(rows)
    equipment_by_exercise: dict[int, list[Optional[int]]] = {}
    for row in rows:
        if row.get("exercise_id") is None:
            continue
        equipment_by_exercise.setdefault(int(row["exercise_id"]), []).append(
            None if row.get("equipment_id") is None else int(row["equipment_id"])
        )
    agnostic = {ex: all_equipment_missing(ids) for ex, ids in equipment_by_exercise.items()}

    pairs: dict[tuple[int, ComparabilityKey], dict[str, Any]] = {}
    for row in rows:
        best = _optional_float(row.get("best_e1rm_kg"))
        weight = _optional_float(row.get("stimulus_weight")) or 0.0
        if row.get("exercise_id") is None or best is None or best <= 0 or weight <= 0:
            continue
        convention = normalize_load_convention(row.get("load_convention"))
        if not supports_e1rm(convention):
            continue
        exercise_id = int(row["exercise_id"])
        key = build_key(
            exercise_id,
            row.get("equipment_id"),
            convention,
            equipment_agnostic=agnostic[exercise_id],
            gym_id=context_gym_id(row.get("equipment_type_code"), row.get("gym_id")),
        )
        if not keys_match(key, key):  # equipamento nao resolvido fora da excecao
            continue
        entry = pairs.setdefault(
            (int(row["muscle_group_id"]), key),
            {"exercise_name": row.get("exercise_name"), "weight": weight, "current": None, "previous": None},
        )
        period = "current" if row["period"] == "current" else "previous"
        # `None` e `total` normalizam para a mesma chave: fica o maior.
        entry[period] = best if entry[period] is None else max(entry[period], best)
        entry["weight"] = max(entry["weight"], weight)
        entry["exercise_name"] = entry["exercise_name"] or row.get("exercise_name")

    out: list[dict[str, Any]] = []
    for (group_id, key), entry in pairs.items():
        if entry["current"] is None or entry["previous"] is None:
            continue
        change = (entry["current"] - entry["previous"]) / entry["previous"] * 100.0
        outlier = abs(change) > GROUP_E1RM_OUTLIER_CHANGE_PCT
        out.append(
            {
                "muscle_group_id": group_id,
                "exercise_id": key.canonical_exercise_id,
                "exercise_name": entry["exercise_name"],
                "equipment_id": key.equipment_id,
                "load_convention": key.load_convention.value,
                "gym_id": key.gym_id,
                "previous_e1rm_kg": round(entry["previous"], 1),
                "current_e1rm_kg": round(entry["current"], 1),
                "change_pct": round(change, 1),
                "stimulus_weight": round(entry["weight"], 3),
                "outlier": outlier,
                "excluded_reason": GROUP_E1RM_OUTLIER_REASON if outlier else None,
                "_change": change,
            }
        )
    return out


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "PERFORMANCE_TREND",
    "TREND_MAINTAINING",
    "TREND_PROGRESSING",
    "TREND_REGRESSING",
    "in_equipment_calibration",
    "VERDICT_LOOKBACK_EXPOSURES",
    "performance_trend",
    "WindowTrend",
    "window_trend",
    "COMPARABLE_PROGRESSION_STATUSES",
    "GROUP_E1RM_OUTLIER_REASON",
    "PROGRESS_OK_STATUSES",
    "group_e1rm_changes",
    "is_comparable_status",
    "is_progress_ok_status",
    "progression_share",
    "ComparabilityKey",
    "ComparablePair",
    "ComparableRun",
    "PAIR_METRIC_BETTER_WHEN",
    "PROGRESSION_COUNT_KEYS",
    "latest_comparable_pair",
    "metric_better_when",
    "metric_change",
    "TREND_MIN_POINTS",
    "trend_metric",
    "metric_outcome",
    "run_flags",
    "OUTCOME_FLAT",
    "OUTCOME_IMPROVED",
    "OUTCOME_NEUTRAL",
    "OUTCOME_WORSENED",
    "ProgressionOutcome",
    "comparable_run",
    "progression_outcome",
    "paired_reps_epley_ratio",
    "paired_reps_ratio",
    "performance_ratio_of",
    "latest_session_performance_ratios",
    "DEFAULT_LOAD_CONVENTION",
    "GROUP_MIN_COMPARABLE_WEIGHT",
    "GROUP_REASON_LOW_ACTIVATION",
    "GROUP_REASON_NO_COMPARABLE_EXERCISE",
    "MIN_COMPARABLE_FOR_RELIABLE_ANSWER",
    "ExposureSummary",
    "LOAD_CONVENTION_LABELS",
    "LoadConvention",
    "PairedTopReps",
    "aggregate_group_progression",
    "exercise_progression_summary",
    "build_key",
    "calibration_physical_key",
    "classify_exercise_progression",
    "compare_exposures",
    "compares_reps_only",
    "goals_match",
    "inverts_load_direction",
    "normalize_load_convention",
    "record_equipment_agnostic",
    "record_key",
    "record_key_comparable",
    "paired_top_reps",
    "physical_keys_match",
    "progression_answer",
    "progression_counts",
    "progression_total",
    "top_load_reps",
    "top_load_sets",
    "supports_e1rm",
    "WorkingSet",
]

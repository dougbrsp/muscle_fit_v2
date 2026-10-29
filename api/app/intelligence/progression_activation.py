"""Teto de ativacao do grupo para o Motor de Progressao (Fase 5, PR 6).

Pergunta que este modulo responde, POR SESSAO avaliada: **o grupo ja atingiu
o teto da faixa com as series equivalentes que a pessoa REALIZOU na janela de
avaliacao?** E o `activation_max_reached` que a maquina de fases le antes de
prescrever `ADD_SET` (F5): com o teto atingido, mais uma serie passaria da
faixa e o grupo vai para a F6 (`blocked_by=ACTIVATION_MAX`).

Nenhuma formula nova - so costura de duas regras que ja existem:

- **realizado** (Fase 5, PR 4): `technique_accounting.realized_set_units` da a
  unidade de cada serie (trabalho = 1, aplicacao de tecnica = a sua fatia do
  bloco da ancora, aquecimento/ajuste = 0), a mesma que o volume por grupo usa;
- **faixa** (Fases 4/6/7): `methodology_volume.evaluate_methodology_volume`
  com as faixas do cadastro de macro-grupos, a frequencia da janela de 7 dias
  e as fatias somadas no pai. As sessoes realizadas entram como slots: um slot
  por DIA da janela de 7 dias que termina na sessao avaliada (dia sem treino =
  descanso), com um item por exercicio executado:
  `sets_count` = series de trabalho, `technique_equivalent` = soma das fatias.

"Teto atingido" = o valor comparado de um eixo de SERIES (media por sessao com
trabalho direto, ou a soma semanal) >= o maximo da faixa do eixo. O eixo de
exercicios nao entra: ele nao mede series.

"Dentro da faixa" (`in_range`) = teto nao atingido e TODOS os eixos de series
comparados em WITHIN_RANGE: a F5 so soma serie a grupo que segue ABAIXO da
faixa considerando os equivalentes (plano da Fase 5); dentro dela o grupo
progride pela carga (F6, `blocked_by=VOLUME_IN_RANGE`). Grupo sem macro-grupo
ou sem faixa (NOT_EVALUABLE) nao bloqueia nada - o comportamento anterior.

Modo de contagem (Lote 4): a janela e avaliada no modo do Modelo que originou
a sessao avaliada (`ActivationCeiling.mode_for`), com as faixas desse modo; o
modo vai para `evidence_json.activation.counting_mode`.

Puro: as entradas (contribuicoes cruas dos papeis dos modos presentes, modo por
sessao, grupos, macro-grupos por modo, catalogo de tecnicas) chegam prontas, lidas UMA vez por `progression_state` em numero fixo
de statements - nada aqui consulta o banco por grupo ou exercicio. O catalogo
pode ser o `LazyCatalog` do PR 4: sem aplicacao de tecnica na janela ele nem e
lido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping, Optional, Sequence

from ..domain.technique_accounting import realized_set_units
from ..domain.training_history import SessionRecord
from ..set_types import PREPARATORY_SET_TYPES, TECHNIQUE_APPLICATION_SET_TYPES, normalize_set_type
from .methodology_volume import (
    DEFAULT_VOLUME_COUNTING_MODE,
    EVALUATION_WINDOW_DAYS,
    AxisEvaluation,
    GroupEvaluation,
    MethodologyGroupInput,
    MethodologyItemInput,
    MethodologySlotInput,
    MethodologyVolumeStatus,
    VolumeClassSpec,
    VolumeContributions,
    VolumeCountingMode,
    counting_mode_for_window,
    evaluate_methodology_volume,
)

logger = logging.getLogger("fitness.progression")

_COMPARED = frozenset(
    {
        MethodologyVolumeStatus.BELOW_RANGE,
        MethodologyVolumeStatus.WITHIN_RANGE,
        MethodologyVolumeStatus.ABOVE_RANGE,
    }
)
TRAINING_SLOT = "normal"
REST_SLOT = "descanso"


@dataclass(frozen=True)
class GroupActivation:
    """Leitura do bloco (grupo pai) de um grupo na janela da sessao."""

    muscle_group_id: int  # bloco avaliado (fatias somadas no pai)
    reached: bool
    status: str
    # Todos os eixos de SERIES comparados estao dentro da faixa, abaixo do
    # teto: a F5 nao soma serie (`BLOCKED_VOLUME_IN_RANGE`).
    in_range: bool = False
    direct_sets_per_session: Optional[float] = None
    sets_range_max: Optional[float] = None
    weekly_sets: Optional[float] = None
    weekly_sets_max: Optional[float] = None
    technique_equivalent: float = 0.0
    # Lote 4 do modo de contagem: regua com que a janela foi avaliada (o modo
    # do Modelo da sessao). Vai para `evidence_json.activation.counting_mode`;
    # decisoes gravadas antes nao tem o campo (regua da epoca: primary_only).
    counting_mode: VolumeCountingMode = DEFAULT_VOLUME_COUNTING_MODE

    def to_evidence(self) -> dict[str, Any]:
        return {
            "block_group_id": self.muscle_group_id,
            "reached": self.reached,
            "in_range": self.in_range,
            "status": self.status,
            "direct_sets_per_session": self.direct_sets_per_session,
            "sets_range_max": self.sets_range_max,
            "weekly_sets": self.weekly_sets,
            "weekly_sets_max": self.weekly_sets_max,
            "technique_equivalent": self.technique_equivalent,
            "counting_mode": self.counting_mode.value,
        }


def _at_ceiling(axis: AxisEvaluation, value: Optional[float]) -> bool:
    if axis.status not in _COMPARED or value is None or axis.range_max is None:
        return False
    return float(value) >= float(axis.range_max)


def group_activation(
    evaluation: GroupEvaluation, counting_mode: VolumeCountingMode = DEFAULT_VOLUME_COUNTING_MODE
) -> GroupActivation:
    sets_axis = evaluation.direct_sets_axis
    weekly_axis = evaluation.weekly_sets_axis
    reached = _at_ceiling(sets_axis, sets_axis.value_per_session) or _at_ceiling(weekly_axis, weekly_axis.value_weekly)
    compared = [axis for axis in (sets_axis, weekly_axis) if axis.status in _COMPARED]
    in_range = (
        not reached
        and bool(compared)
        and all(axis.status is MethodologyVolumeStatus.WITHIN_RANGE for axis in compared)
    )
    return GroupActivation(
        muscle_group_id=evaluation.muscle_group_id,
        reached=reached,
        status=evaluation.status.value,
        in_range=in_range,
        direct_sets_per_session=sets_axis.value_per_session,
        sets_range_max=sets_axis.range_max,
        weekly_sets=weekly_axis.value_weekly,
        weekly_sets_max=weekly_axis.range_max,
        technique_equivalent=float(evaluation.technique_equivalent_cycle_total or 0.0),
        counting_mode=counting_mode,
    )


def _set_rows(session: SessionRecord) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for execution in session.exercises:
        for item in execution.sets:
            rows.append(
                {
                    "exercise_row_id": execution.exercise_row_id,
                    "set_number": item.set_number,
                    "set_type": item.set_type,
                    "reps": item.reps,
                    "rpe": item.rpe,
                    "technique_code": item.technique_code,
                    "anchor_set_number": item.anchor_set_number,
                }
            )
    return rows


def realized_items(session: SessionRecord, catalog: Optional[Mapping[str, Any]]) -> tuple[MethodologyItemInput, ...]:
    """Um item por exercicio executado: series de trabalho + fatias de tecnica
    (`realized_set_units`, a regra do volume realizado)."""
    rows = _set_rows(session)
    units = realized_set_units(rows, catalog=catalog, exercise_key="exercise_row_id")
    work: dict[int, float] = {}
    technique: dict[int, float] = {}
    for row, unit in zip(rows, units):
        kind = normalize_set_type(row["set_type"])
        if kind in PREPARATORY_SET_TYPES:
            continue
        key = int(row["exercise_row_id"])
        bucket = technique if kind in TECHNIQUE_APPLICATION_SET_TYPES else work
        bucket[key] = bucket.get(key, 0.0) + unit
    items: list[MethodologyItemInput] = []
    for execution in session.exercises:
        if execution.exercise_id is None:
            continue
        sets = int(round(work.get(execution.exercise_row_id, 0.0)))
        items.append(
            MethodologyItemInput(
                mapping_id=int(execution.exercise_id),
                canonical_exercise_id=int(execution.exercise_id),
                sets_count=sets,
                technique_equivalent=technique.get(execution.exercise_row_id, 0.0),
                # M97 F2: o motor de volume pondera a serie de forca
                training_goal=execution.training_goal,
            )
        )
    return tuple(items)


@dataclass(frozen=True)
class ActivationCeiling:
    """`(janela, sessao) -> {grupo: GroupActivation}` para o avaliador.

    `contributions` (`exercise_id -> linhas cruas do cadastro`, nos papeis de
    todos os modos presentes na janela) e pelo exercicio CANONICO (o
    `exercise_id` da trilha), como o motor de progressao ja le as
    contribuicoes; o peso de cada modo quem decide e o motor
    (`counted_contributions`).

    Modo (Lote 4): o do Modelo que originou a sessao (`session_modes`,
    `activity_id -> modo`, lido por
    `methodology_volume_service.load_session_counting_modes`). A janela inteira
    usa UM modo - o da sessao mais recente ate a avaliada
    (`counting_mode_for_window`), que e a propria sessao avaliada (a janela
    termina nela). Sessao fora do mapa = `counting_mode` (o default). As
    faixas do modo vem de `volume_classes_by_mode`; `volume_classes` e o
    cadastro de `counting_mode` (forma anterior, ainda aceita). Modo sem
    cadastro carregado = nada avaliado (sem teto), nunca a faixa de outro
    modo."""

    contributions: VolumeContributions = field(default_factory=dict)
    counting_mode: VolumeCountingMode = DEFAULT_VOLUME_COUNTING_MODE
    groups: tuple[MethodologyGroupInput, ...] = ()
    volume_classes: Mapping[int, VolumeClassSpec] = field(default_factory=dict)
    technique_catalog: Optional[Mapping[str, Any]] = None
    window_days: int = EVALUATION_WINDOW_DAYS
    session_modes: Mapping[int, VolumeCountingMode] = field(default_factory=dict)
    volume_classes_by_mode: Mapping[VolumeCountingMode, Mapping[int, VolumeClassSpec]] = field(default_factory=dict)

    def mode_for(self, window: Sequence[SessionRecord], session: SessionRecord) -> VolumeCountingMode:
        """Modo da janela que termina em `session`: o da sessao mais recente
        ate ela, em ordem cronologica (a janela do avaliador termina nela)."""
        upto = [item for item in window if item.local_date <= session.local_date and item is not session]
        ordered = [*upto, session]
        return counting_mode_for_window(self.session_modes.get(item.activity_id, self.counting_mode) for item in ordered)

    def classes_for(self, mode: VolumeCountingMode) -> Mapping[int, VolumeClassSpec]:
        if mode in self.volume_classes_by_mode:
            return self.volume_classes_by_mode[mode]
        return self.volume_classes if mode is self.counting_mode else {}

    def slots_for(self, window: Sequence[SessionRecord], session: SessionRecord) -> tuple[MethodologySlotInput, ...]:
        """Um slot por DIA da janela `[data - (dias-1), data]` da sessao."""
        end = session.local_date
        start = end - timedelta(days=self.window_days - 1)
        by_day: dict[Any, list[MethodologyItemInput]] = {}
        for item in window:
            if start <= item.local_date <= end:
                by_day.setdefault(item.local_date, []).extend(realized_items(item, self.technique_catalog))
        slots = []
        for offset in range(self.window_days):
            day = start + timedelta(days=offset)
            items = tuple(by_day.get(day, ()))
            slots.append(
                MethodologySlotInput(
                    sequence=offset + 1,
                    slot_type=TRAINING_SLOT if day in by_day else REST_SLOT,
                    name=day.isoformat(),
                    items=items,
                )
            )
        return tuple(slots)

    def evaluate(self, window: Sequence[SessionRecord], session: SessionRecord) -> dict[int, GroupActivation]:
        mode = self.mode_for(window, session)
        volume_classes = self.classes_for(mode)
        if not self.groups or not volume_classes:
            return {}
        evaluation = evaluate_methodology_volume(
            self.slots_for(window, session),
            self.contributions,
            self.groups,
            "equipment_series",
            volume_classes=volume_classes,
            counting_mode=mode,
        )
        result: dict[int, GroupActivation] = {}
        for block in evaluation.groups:
            activation = group_activation(block, mode)
            for member in block.member_group_ids:
                result[int(member)] = activation
        return result

    def __call__(self, window: Sequence[SessionRecord], session: SessionRecord) -> dict[int, GroupActivation]:
        """Falha aqui NUNCA derruba a avaliacao: sem a leitura o teto fica
        como antes da Fase 5 (nao atingido) e o motivo vai para o log."""
        try:
            return self.evaluate(window, session)
        except Exception as exc:  # noqa: BLE001 - teto e protecao, nao pre-requisito
            logger.warning('{"event": "progression.activation_failed", "error_type": "%s"}', type(exc).__name__)
            return {}


__all__ = ["ActivationCeiling", "GroupActivation", "group_activation", "realized_items"]

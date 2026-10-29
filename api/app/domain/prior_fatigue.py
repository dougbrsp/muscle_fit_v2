"""DONO do "contexto de fadiga prévia" de um exercício dentro da sessão.

Problema: o mesmo exercício feito em ordens diferentes (supino 1º na segunda,
5º na quinta depois de exercícios que já cansaram os mesmos músculos) não é a
mesma condição. Este módulo mede, para cada item de uma sequência ORDENADA
(itens de um slot do Modelo de Treino ou exercícios executados de uma sessão
registrada), quanto o músculo principal do item já foi trabalhado ANTES dele
na mesma sessão.

A conta (Python puro, sem I/O):

    músculo principal(item) = contribuição PRIMARY de maior `stimulus_weight`
                              (empate: menor `muscle_group_id`, depois nome;
                              sem PRIMARY, a contribuição de maior peso)
    fadiga prévia(item)     = Σ, nos itens ANTERIORES, séries(anterior) ×
                              `stimulus_weight` do músculo principal naquele
                              anterior (as séries do próprio item não contam)
    nível                   = min(1, fadiga prévia / PRIOR_FATIGUE_SATURATION_SETS)
    faixa                   = RESTED < PRE_FATIGUED_MIN_SETS ≤ PRE_FATIGUED
                              < HEAVILY_FATIGUED_MIN_SETS ≤ HEAVILY_FATIGUED

Unidade: "séries equivalentes" — a MESMA de ativação/volume (séries ×
`stimulus_weight`). Granularidade de músculo: o grupo muscular de
`exercise_muscle_contributions` (`muscle_group_id`), a mesma de toda a
ativação; contribuição sem grupo ("Outros", política única de
`VolumeService._resolve_contributions`) casa pelo nome.

Quem alimenta (não recalcula nada daqui):
- Modelo de Treino: séries = `technique_volume.planned_item_sets` (séries de
  trabalho + equivalente PLANEJADO da técnica, dono da contagem); pesos de
  `_resolve_muscle_contributions_for_mappings` (dono de leitura dos pesos por
  papel); exposto pelo preview do Modelo (`item_prior_fatigue[]`).
- Sessão registrada (PR 2: comparabilidade e prescrição): séries = séries
  não preparatórias executadas (`sql_not_preparatory`, cada aplicação de
  técnica pela fatia de `technique_accounting.realized_set_units` - a mesma
  unidade do volume realizado), na ordem de execução (`exercises.sequence`);
  mesmos pesos. Montagem em `domain/session_fatigue.py`.
- Contexto de fadiga (`context_band`): a faixa vira parte da chave de
  comparabilidade (`intelligence/dashboard/comparability.py`) e da trilha da
  progressão (`progression_engine.TrackState.fatigue_context`).

Cortes medidos em 2026-10-07 nos Modelos reais do usuário 1 (DEV com dados de
produção, versão vigente de cada Modelo `equipment_series`): ver o PR
`docs/motores/FONTES_DA_VERDADE.md` §21 (e a descrição do PR).

Duas leituras, o MESMO dono (M79, 2026-10-11):

1. CONTEXTO (`prior_fatigue_for_sequence`, `context_band`): pesos
   `stimulus_weight`, 3 faixas, granularidade por EXERCÍCIO. É a chave
   `fatigue_context` da comparabilidade e das trilhas da progressão - não
   muda com o indicador.
2. INDICADOR DE FADIGA DO GRUPO (`group_fatigue_for_sequence`): o que o Modo
   Treino mostra ao lado do recorde e das séries do treino anterior. Pesos
   `fatigue_weight` (editados em `/dominio-exercicios`; ausente -> cai no
   `stimulus_weight` da mesma contribuição, `fatigue_weight_of`), 4 faixas
   (`GroupFatigueBand`), granularidade por SÉRIE: a leitura de cada série é o
   acumulado de todas as ativações anteriores do músculo principal, incluindo
   as séries anteriores do MESMO exercício. Os cortes são os do contexto
   (`band_for`) com "Fatigado" inserido dentro de "pré-fatigado"
   (`GROUP_FATIGUE_FATIGUED_MIN_SETS`): com `fatigue_weight` = `stimulus_weight`
   a leitura nova é um REFINAMENTO da antiga, nunca a contradiz. O músculo
   principal é o mesmo do contexto (`principal_muscle`, por `stimulus_weight`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

# --- Constantes da regra (únicas; ninguém fora daqui reescreve) -------------

# A partir de quantas séries equivalentes prévias no músculo principal o item
# deixa de ser "descansado". Abaixo disso cabem os resíduos de papel
# secundário/estabilizador (ex.: 3 séries × 0,25 = 0,75; 3 × 0,5 = 1,5).
PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS: Final[float] = 2.0
# A partir de quantas séries equivalentes prévias o item está "muito fatigado"
# (~2 exercícios diretos de 3 séries do mesmo grupo antes dele).
PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS: Final[float] = 6.0
# Saturação do nível 0..1 (cor do editor): nível 1 = vermelho pleno.
PRIOR_FATIGUE_SATURATION_SETS: Final[float] = 9.0

PRIMARY_ROLE: Final[str] = "PRIMARY"

# Indicador de fadiga do grupo (M79): corte que separa "Pré-fatigado" de
# "Fatigado" DENTRO da faixa pré-fatigada do contexto (2 <= x < 6): o ponto
# médio da faixa, ~1 exercício direto de 4 séries já feito no grupo. Os demais
# cortes são os do contexto (`PRIOR_FATIGUE_*_MIN_SETS`), por construção
# (`group_band_for` parte de `band_for`). Medição em produção: PR do M79.
GROUP_FATIGUE_FATIGUED_MIN_SETS: Final[float] = 4.0


class PriorFatigueBand(str, Enum):
    RESTED = "rested"
    PRE_FATIGUED = "pre_fatigued"
    HEAVILY_FATIGUED = "heavily_fatigued"


PRIOR_FATIGUE_BAND_LABELS: Final[Mapping[PriorFatigueBand, str]] = {
    PriorFatigueBand.RESTED: "descansado",
    PriorFatigueBand.PRE_FATIGUED: "pré-fatigado",
    PriorFatigueBand.HEAVILY_FATIGUED: "muito fatigado",
}


class GroupFatigueBand(str, Enum):
    """Faixas do indicador de fadiga do grupo (Modo Treino, M79)."""

    RESTED = "rested"
    PRE_FATIGUED = "pre_fatigued"
    FATIGUED = "fatigued"
    HEAVILY_FATIGUED = "heavily_fatigued"


GROUP_FATIGUE_BAND_LABELS: Final[Mapping[GroupFatigueBand, str]] = {
    GroupFatigueBand.RESTED: "Descansado",
    GroupFatigueBand.PRE_FATIGUED: "Pré-fatigado",
    GroupFatigueBand.FATIGUED: "Fatigado",
    GroupFatigueBand.HEAVILY_FATIGUED: "Muito fatigado",
}


# --- Tipos de entrada/saída --------------------------------------------------


@dataclass(frozen=True)
class MuscleContribution:
    """Uma linha de `exercise_muscle_contributions` já resolvida."""

    muscle_group_id: Optional[int]
    muscle_group_name: Optional[str]
    role: str
    stimulus_weight: float
    # `exercise_muscle_contributions.fatigue_weight` (M79). Só o indicador de
    # fadiga do grupo lê; o contexto continua no `stimulus_weight`. None =
    # não informado (cai no `stimulus_weight`, `fatigue_weight_of`).
    fatigue_weight: Optional[float] = None


@dataclass(frozen=True)
class FatigueItem:
    """Um item da sequência: séries (planejadas ou executadas, já com o
    equivalente de técnica do dono da contagem) + contribuições musculares."""

    sets: float
    contributions: tuple[MuscleContribution, ...]


@dataclass(frozen=True)
class PriorFatigue:
    muscle_group_id: Optional[int]
    muscle_group_name: Optional[str]
    prior_sets: float  # séries equivalentes prévias no músculo principal
    level: float  # 0..1
    band: PriorFatigueBand

    @property
    def band_label(self) -> str:
        return PRIOR_FATIGUE_BAND_LABELS[self.band]

    def as_dict(self) -> dict[str, Any]:
        return {
            "muscle_group_id": self.muscle_group_id,
            "muscle_group_name": self.muscle_group_name,
            "prior_sets": round(self.prior_sets, 2),
            "level": round(self.level, 4),
            "band": self.band.value,
            "band_label": self.band_label,
        }


# --- Regra -------------------------------------------------------------------


def contribution_from_mapping(row: Mapping[str, Any]) -> MuscleContribution:
    """Converte a linha de `_resolve_muscle_contributions_for_mappings` /
    `VolumeService._resolve_contributions` (dicionário) no tipo do motor."""
    group_id = row.get("muscle_group_id")
    fatigue_weight = row.get("fatigue_weight")
    return MuscleContribution(
        muscle_group_id=int(group_id) if group_id is not None else None,
        muscle_group_name=row.get("muscle_group_name") or row.get("muscle_group"),
        role=str(row.get("role") or ""),
        stimulus_weight=float(row.get("stimulus_weight") or 0.0),
        fatigue_weight=float(fatigue_weight) if fatigue_weight is not None else None,
    )


def _muscle_key(contribution: MuscleContribution) -> tuple[str, Any]:
    if contribution.muscle_group_id is not None:
        return ("id", contribution.muscle_group_id)
    return ("name", contribution.muscle_group_name or "")


def _tie_break(contribution: MuscleContribution) -> tuple[Any, ...]:
    return (
        -contribution.stimulus_weight,
        contribution.muscle_group_id is None,
        contribution.muscle_group_id if contribution.muscle_group_id is not None else 0,
        contribution.muscle_group_name or "",
    )


def principal_muscle(contributions: Sequence[MuscleContribution]) -> Optional[MuscleContribution]:
    """PRIMARY de maior `stimulus_weight` (empate determinístico); sem PRIMARY,
    a contribuição de maior peso; sem contribuição com peso, None."""
    weighted = [c for c in contributions if c.stimulus_weight > 0]
    if not weighted:
        return None
    primaries = [c for c in weighted if c.role.upper() == PRIMARY_ROLE]
    return min(primaries or weighted, key=_tie_break)


def band_for(prior_sets: float) -> PriorFatigueBand:
    if prior_sets >= PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS:
        return PriorFatigueBand.HEAVILY_FATIGUED
    if prior_sets >= PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS:
        return PriorFatigueBand.PRE_FATIGUED
    return PriorFatigueBand.RESTED


def level_for(prior_sets: float) -> float:
    if prior_sets <= 0:
        return 0.0
    return min(1.0, prior_sets / PRIOR_FATIGUE_SATURATION_SETS)


def context_band(prior: Optional[PriorFatigue]) -> PriorFatigueBand:
    """CONTEXTO de fadiga de um item (comparabilidade e trilha da progressao,
    PR 2): a faixa do dono. Item sem musculo principal (nenhuma contribuicao
    com peso) nao tem como estar pre-fatigado: vale `RESTED`."""
    return prior.band if prior is not None else PriorFatigueBand.RESTED


def band_label(value: Any) -> Optional[str]:
    """Rotulo pt-BR de uma faixa (`PriorFatigueBand` ou o seu valor); `None`
    para valor desconhecido/ausente. A tela nunca escreve o rotulo."""
    if value is None:
        return None
    try:
        return PRIOR_FATIGUE_BAND_LABELS[PriorFatigueBand(value)]
    except ValueError:
        return None


def prior_fatigue_for_sequence(items: Sequence[FatigueItem]) -> list[Optional[PriorFatigue]]:
    """Uma entrada por item, na MESMA ordem. None quando o item não tem
    músculo principal (sem contribuição com peso). Séries ≤ 0 não fatigam."""
    accumulated: dict[tuple[str, Any], float] = {}
    result: list[Optional[PriorFatigue]] = []
    for item in items:
        principal = principal_muscle(item.contributions)
        if principal is None:
            result.append(None)
        else:
            prior_sets = accumulated.get(_muscle_key(principal), 0.0)
            result.append(
                PriorFatigue(
                    muscle_group_id=principal.muscle_group_id,
                    muscle_group_name=principal.muscle_group_name,
                    prior_sets=prior_sets,
                    level=level_for(prior_sets),
                    band=band_for(prior_sets),
                )
            )
        sets = max(0.0, float(item.sets or 0.0))
        if sets <= 0:
            continue
        for contribution in item.contributions:
            if contribution.stimulus_weight <= 0:
                continue
            key = _muscle_key(contribution)
            accumulated[key] = accumulated.get(key, 0.0) + sets * contribution.stimulus_weight
    return result


# --- Indicador de fadiga do grupo (Modo Treino, M79) ------------------------


@dataclass(frozen=True)
class GroupFatigueItem:
    """Um exercício da sequência com a unidade de CADA série, na ordem de
    execução (`technique_accounting.realized_set_units`: 1 por série de
    trabalho/backoff, a fatia do bloco por aplicação de técnica)."""

    set_units: tuple[float, ...]
    contributions: tuple[MuscleContribution, ...]


@dataclass(frozen=True)
class GroupFatigue:
    muscle_group_id: Optional[int]
    muscle_group_name: Optional[str]
    prior_sets: float  # séries equivalentes prévias × `fatigue_weight`
    level: float  # 0..1 (`level_for`)
    band: GroupFatigueBand

    @property
    def band_label(self) -> str:
        return GROUP_FATIGUE_BAND_LABELS[self.band]

    def as_dict(self) -> dict[str, Any]:
        return {
            "muscle_group_id": self.muscle_group_id,
            "muscle_group_name": self.muscle_group_name,
            "prior_sets": round(self.prior_sets, 2),
            "level": round(self.level, 4),
            "band": self.band.value,
            "band_label": self.band_label,
        }


@dataclass(frozen=True)
class GroupFatigueTimeline:
    """Leitura de um exercício: `before` = antes da 1ª série (o "bloco");
    `per_set[i]` = antes da série `i` (mesma ordem de `set_units`). None =
    exercício sem músculo principal."""

    before: Optional[GroupFatigue]
    per_set: tuple[Optional[GroupFatigue], ...]


def fatigue_weight_of(contribution: MuscleContribution) -> float:
    """Peso de FADIGA da contribuição: o `fatigue_weight` cadastrado; ausente
    (None) cai no `stimulus_weight` da mesma linha (o default de escrita é o
    mesmo para os dois, `DEFAULT_WRITE_WEIGHTS`)."""
    if contribution.fatigue_weight is not None:
        return float(contribution.fatigue_weight)
    return float(contribution.stimulus_weight)


def group_band_for(prior_sets: float) -> GroupFatigueBand:
    """4 faixas = as 3 do contexto (`band_for`) com "Fatigado" a partir de
    `GROUP_FATIGUE_FATIGUED_MIN_SETS` dentro de "pré-fatigado"."""
    band = band_for(prior_sets)
    if band is PriorFatigueBand.HEAVILY_FATIGUED:
        return GroupFatigueBand.HEAVILY_FATIGUED
    if band is PriorFatigueBand.PRE_FATIGUED:
        if prior_sets >= GROUP_FATIGUE_FATIGUED_MIN_SETS:
            return GroupFatigueBand.FATIGUED
        return GroupFatigueBand.PRE_FATIGUED
    return GroupFatigueBand.RESTED


def group_band_label(value: Any) -> Optional[str]:
    """Rótulo pt-BR de uma faixa do indicador; `None` para desconhecida."""
    if value is None:
        return None
    try:
        return GROUP_FATIGUE_BAND_LABELS[GroupFatigueBand(value)]
    except ValueError:
        return None


def _group_reading(
    principal: Optional[MuscleContribution], accumulated: Mapping[tuple[str, Any], float]
) -> Optional[GroupFatigue]:
    if principal is None:
        return None
    prior_sets = accumulated.get(_muscle_key(principal), 0.0)
    return GroupFatigue(
        muscle_group_id=principal.muscle_group_id,
        muscle_group_name=principal.muscle_group_name,
        prior_sets=prior_sets,
        level=level_for(prior_sets),
        band=group_band_for(prior_sets),
    )


def group_fatigue_for_sequence(items: Sequence[GroupFatigueItem]) -> list[GroupFatigueTimeline]:
    """Uma linha do tempo por item, na MESMA ordem. A fadiga de cada série é o
    acumulado de unidade × `fatigue_weight_of` de TODAS as séries anteriores
    (dos itens anteriores e do próprio item) no músculo principal do item.
    Unidades <= 0 não fatigam."""
    accumulated: dict[tuple[str, Any], float] = {}
    result: list[GroupFatigueTimeline] = []
    for item in items:
        principal = principal_muscle(item.contributions)
        before = _group_reading(principal, accumulated)
        per_set: list[Optional[GroupFatigue]] = []
        for unit in item.set_units:
            per_set.append(_group_reading(principal, accumulated))
            value = max(0.0, float(unit or 0.0))
            if value <= 0:
                continue
            for contribution in item.contributions:
                weight = fatigue_weight_of(contribution)
                if weight <= 0:
                    continue
                key = _muscle_key(contribution)
                accumulated[key] = accumulated.get(key, 0.0) + value * weight
        result.append(GroupFatigueTimeline(before=before, per_set=tuple(per_set)))
    return result

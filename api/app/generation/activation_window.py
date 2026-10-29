"""Janela de ativacao e orcamento do slot (PR-13; PLANO_20260912.md 6.1,
passos 1 e 2).

Pergunta que este modulo responde: **quanta ativacao este grupo muscular
ainda precisa NESTE slot**, dado o que o usuario ja fez de verdade e o que o
proprio ciclo do modelo ainda vai entregar.

Tres coisas, e so tres:

1. **A janela** (passo 1). Movel, de 7 dias, terminando no dia do slot. Os
   dias ja decorridos entram com ativacao REAL
   (`VolumeService.get_daily_muscle_activation`); os dias que faltam entre
   hoje e o dia do slot entram com o PLANEJADO do proprio ciclo, projetado 1
   slot por dia de treino (slot de descanso ocupa o dia e contribui zero).
   M76 Lote 4a (E1): a janela tem SEMPRE os dias da semana do dono
   (`methodology_volume.weekly_window_days`, 7) e a meta semanal entra sem
   escala, qualquer que seja o ciclo - a mesma regra do builder, do preview
   de ativacao e do dashboard. Ate aqui um ciclo de 8 slots alargava a
   janela para 8 dias e cobrava `meta x 8/7`.

2. **O orcamento** (passo 2).

       necessidade = target_weekly - realizado - planejado_restante
       alvo_slot   = clamp(necessidade, nominal_min, nominal_max) x recovery.volume_multiplier

   `NO_DIRECT_WORK` -> 0 (multiplicador 0, o grupo sai do slot).

   M76 B2: o multiplicador é o da regra única da dose reduzida
   (`MuscleRecoveryService.volume_multiplier_for`, via o planner). Aqui ele
   escala o ORÇAMENTO de ativação do grupo (séries equivalentes, contínuo -
   decide quantos exercícios); as séries de CADA exercício escolhido saem do
   motor, que aplica `MuscleRecoveryService.reduced_sets` (`ceil(S × 0,75)`,
   mínimo 1). Sem as duas, o grupo compensaria a redução com mais exercícios.

3. **O teto projetado** de cada grupo, que o passo 6 usa para recusar um
   candidato cujo trabalho SECUNDARIO estouraria o `max_weekly` do grupo.

Sem historico nenhum na janela, `realizado = 0` e a necessidade fica >=
`nominal_max`: o `clamp` devolve o envelope do modelo. E exatamente o "sem
historico: so o modelo" do passo 1.

Modulo com UMA leitura de banco (a ativacao real). Escala nenhuma de decisao
de exercicio acontece aqui.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Optional, Sequence

from ..intelligence.methodology_volume import weekly_window_days
from ..intelligence.template_volume_domain import MuscleSlotBudget, TemplateCycleContext

# A meta cadastrada no Tipo de Treinamento e SEMANAL. O numero de dias vem do
# dono da normalizacao semanal (M76 Lote 4a), nunca de uma copia local.
WEEKLY_WINDOW_DAYS = weekly_window_days()


def _normalize_name(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "").strip().casefold())


@dataclass(frozen=True)
class ActivationWindow:
    """A janela em si: onde comeca e onde termina (sempre uma semana)."""

    start: date
    end: date
    cycle_length: int

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def target_scale(self) -> float:
        """Sempre 1.0 desde o M76 Lote 4a: a janela ja e a semana, entao a
        meta semanal entra como esta. Mantido so pelo contrato da auditoria
        (`generation_audit.window.target_scale`)."""
        return 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "cycle_length": self.cycle_length,
            "target_scale": round(self.target_scale, 4),
        }


def resolve_window(slot_date: date, cycle_length: int) -> ActivationWindow:
    """Janela movel de uma semana terminando NO DIA DO SLOT, qualquer que
    seja o ciclo (`cycle_length` so descreve o Modelo na auditoria): ciclo
    maior que a semana nao alarga a janela, ciclo menor da a volta dentro
    dela (`projected_slot_ids` anda pelo ciclo com wraparound)."""
    days = WEEKLY_WINDOW_DAYS
    return ActivationWindow(
        start=slot_date - timedelta(days=days - 1), end=slot_date, cycle_length=int(cycle_length or 0)
    )


@dataclass(frozen=True)
class GroupBudget:
    """Orcamento final de UM grupo neste slot - o que o passo 6 consome."""

    muscle_group_id: int
    muscle_group_name: str
    target_weekly: float  # meta semanal (a janela e sempre uma semana)
    max_weekly: float  # teto semanal
    nominal_min: float
    nominal_max: float
    realized: float
    planned: float
    need: float
    volume_multiplier: float
    recovery_status: str
    target: float  # alvo_slot
    target_min: float  # piso que o passo 6 tenta alcancar
    target_max: float  # teto do envelope do slot, pos-recuperacao
    projected_headroom: float  # quanto ainda cabe no `max_weekly` da janela
    prioritize: bool = False
    reps_target: Optional[str] = None
    rpe_target: Optional[str] = None

    @property
    def is_excluded(self) -> bool:
        return self.recovery_status == "NO_DIRECT_WORK" or self.target <= 0

    def deficit(self, activation: float) -> float:
        """Subativacao restante (passo 7). Nunca negativa."""
        return max(0.0, round(self.target_min - activation, 4))

    def as_dict(self, activation: float) -> dict[str, Any]:
        return {
            "muscle_group_id": self.muscle_group_id,
            "muscle_group_name": self.muscle_group_name,
            "target_weekly": round(self.target_weekly, 2),
            "max_weekly": round(self.max_weekly, 2),
            "target_min": round(self.target_min, 2),
            "target_max": round(self.target_max, 2),
            "target": round(self.target, 2),
            "nominal_min": round(self.nominal_min, 2),
            "nominal_max": round(self.nominal_max, 2),
            "realized": round(self.realized, 2),
            "planned": round(self.planned, 2),
            "need": round(self.need, 2),
            "recovery_status": self.recovery_status,
            "volume_multiplier": self.volume_multiplier,
            "result": round(activation, 2),
            "deficit": self.deficit(activation),
        }


def _clamp(value: float, low: float, high: float) -> float:
    if high < low:
        low, high = high, low
    return max(low, min(high, value))


def realized_activation(
    user_id: Optional[int],
    window: ActivationWindow,
    muscle_group_names_by_id: Mapping[int, str],
    *,
    today: Optional[date] = None,
) -> dict[int, float]:
    """Ativacao REAL do usuario nos dias JA DECORRIDOS da janela, por grupo.

    "Ja decorridos" = de `window.start` ate `min(hoje, dia do slot)`: uma
    ficha de amanha nao tem como ter historico de amanha, e uma ficha de hoje
    conta o que ja foi treinado hoje. Fonte unica do calculo
    `sets x stimulus_weight`: `VolumeService.get_daily_muscle_activation`
    (que soma PRIMARY+SECONDARY+STABILIZER, que e exatamente a ativacao total
    do passo 6).
    """
    if user_id is None:
        return {}
    from ..intelligence.volume_service import VolumeService

    service = VolumeService()
    # "Hoje" do USUARIO (M76 Lote 2b), nunca `date.today()` do processo (UTC
    # na Lambda): entre 21h e 24h de Brasilia o treino da noite ficava de fora.
    elapsed_end = min(today or service._user_today(int(user_id)), window.end)
    if elapsed_end < window.start:
        return {}

    rows = service.get_daily_muscle_activation(
        user_id=int(user_id), start_date=window.start.isoformat(), end_date=elapsed_end.isoformat()
    )
    ids_by_name = {_normalize_name(name): group_id for group_id, name in muscle_group_names_by_id.items()}
    totals: dict[int, float] = {}
    for row in rows:
        group_id = ids_by_name.get(_normalize_name(row.get("muscle_group")))
        if group_id is None:
            continue
        totals[group_id] = totals.get(group_id, 0.0) + float(row.get("sets_count") or 0.0)
    return totals


def projected_slot_ids(
    context: TemplateCycleContext,
    slot: Mapping[str, Any],
    slot_date: date,
    *,
    today: date,
) -> list[tuple[date, int]]:
    """Os slots do ciclo que ainda vao acontecer DENTRO da janela, antes do
    slot que esta sendo gerado - um por dia, andando do dia do slot para tras
    (passo 1: "1 slot por dia de treino").

    Os dias entre hoje e o dia do slot sao os unicos dias da janela que ainda
    nao aconteceram, e o ciclo diz exatamente qual slot cai em cada um deles:
    o slot anterior no ciclo cai no dia anterior, com wraparound. Slot de
    descanso entra na lista (ocupa o dia) e contribui zero no calculo do
    planejado.

    Ficha de hoje (ou de uma data passada): lista vazia - nao ha nada
    planejado entre hoje e hoje, e a janela e 100% historico real.

    `today` e o "hoje" do usuario (`time_windows.user_today`), obrigatorio
    desde o M76 Lote 2b: a funcao nao consulta relogio nenhum.
    """
    if slot_date <= today:
        return []
    ordered = sorted(context.slots, key=lambda item: item["sequence"])
    if not ordered:
        return []
    position = next(
        (index for index, item in enumerate(ordered) if item.get("id") == slot.get("id")),
        None,
    )
    if position is None:
        return []

    projected: list[tuple[date, int]] = []
    day = slot_date - timedelta(days=1)
    offset = 1
    while day > today:
        previous = ordered[(position - offset) % len(ordered)]
        projected.append((day, int(previous["id"])))
        day -= timedelta(days=1)
        offset += 1
    # Ordem cronologica (auditoria legivel); o total nao depende dela.
    projected.reverse()
    return projected


def planned_activation(
    context: TemplateCycleContext,
    projected: Sequence[tuple[date, int]],
) -> dict[int, float]:
    """Ativacao PLANEJADA dos slots projetados, por grupo.

    Cada slot projetado vale o PONTO MEDIO nominal do orcamento dele
    (`MuscleSlotBudget.nominal_slot_mid`, do mesmo planner que o slot atual
    usa) - nao ha exercicio escolhido para um slot futuro, e inventar um seria
    pior que usar o proprio envelope do modelo. Slot de descanso nao tem item
    de grupo muscular e contribui zero naturalmente.
    """
    from ..intelligence.template_volume_planner import build_template_volume_plan

    totals: dict[int, float] = {}
    for _day, slot_id in projected:
        for budget in build_template_volume_plan(context, slot_id):
            totals[budget.muscle_group_id] = totals.get(budget.muscle_group_id, 0.0) + budget.nominal_slot_mid
    return totals


def build_group_budgets(
    budgets: Sequence[MuscleSlotBudget],
    window: ActivationWindow,
    realized: Mapping[int, float],
    planned: Mapping[int, float],
) -> list[GroupBudget]:
    """Passo 2, para cada grupo do slot, na ordem de `muscle_group_id`
    (determinismo: nunca a ordem de um dict)."""
    result: list[GroupBudget] = []
    for budget in sorted(budgets, key=lambda item: item.muscle_group_id):
        # Meta semanal sem escala: a janela ja e a semana (M76 Lote 4a).
        target_weekly = budget.weekly_target
        max_weekly = budget.weekly_max
        realized_value = float(realized.get(budget.muscle_group_id, 0.0))
        planned_value = float(planned.get(budget.muscle_group_id, 0.0))
        need = target_weekly - realized_value - planned_value
        multiplier = float(budget.recovery_multiplier)
        target = _clamp(need, budget.nominal_slot_min, budget.nominal_slot_max) * multiplier
        if budget.is_hard_excluded:
            target = 0.0
        result.append(
            GroupBudget(
                muscle_group_id=budget.muscle_group_id,
                muscle_group_name=budget.muscle_group_name,
                target_weekly=target_weekly,
                max_weekly=max_weekly,
                nominal_min=budget.nominal_slot_min,
                nominal_max=budget.nominal_slot_max,
                realized=realized_value,
                planned=planned_value,
                need=need,
                volume_multiplier=multiplier,
                recovery_status=budget.recovery_status,
                target=target,
                # O piso nunca passa do alvo: com `necessidade` abaixo do
                # `nominal_min` (grupo praticamente coberto pela janela) o
                # proprio alvo e o piso, e o passo 6 para antes de estourar.
                target_min=min(budget.nominal_slot_min * multiplier, target),
                target_max=budget.nominal_slot_max * multiplier,
                projected_headroom=max(0.0, max_weekly - realized_value - planned_value),
                prioritize=budget.prioritize,
                reps_target=budget.reps_target,
                rpe_target=budget.rpe_target,
            )
        )
    return result

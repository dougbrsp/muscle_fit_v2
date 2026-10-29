"""Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2) — Fase 1: domain model.

Dataclasses PURAS (sem I/O, sem import de `db`/pymysql) representando o
vocabulário de "orçamento semanal de estímulo" descrito em
MELHORIA_MOTOR_FICHA.md (seções 3, 4, 6, 12, 23, 79) e resumido no plano
aprovado (`template_volume_domain.py`, Fase 1). Servem de contrato entre
`template_volume_planner.py` (Fase 2, monta estas estruturas a partir do
banco), `candidate_exercise_provider.py`/`template_ai_selector_v2.py`
(Fases 3-4, consomem os budgets) e `template_volume_validator.py`
(Fase 5, calcula ativação e repara contra estas mesmas estruturas).

Regra não-negociável (seção 36 do documento): `prioritize` NUNCA aparece
aqui como peso/score — só existe como booleano informativo em
`SlotMuscleAllocation.prioritize`, consumido exclusivamente por
`order_exercises_for_session` (Fase 5), nunca por nenhum cálculo de volume
deste módulo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Papéis de contribuição muscular aceitos — STABILIZER aceito no tipo mesmo
# sem uso real nos dados hoje (achado da auditoria: `exercise_muscle_contributions.role`
# só tem PRIMARY/SECONDARY em uso), nunca pressuposto presente.
MUSCLE_ROLES = ("PRIMARY", "SECONDARY", "STABILIZER")

# Thresholds de validação de cobertura do envelope percentual do template
# (seção 11 do documento: "sum_pct_min/sum_pct_max... 90-110% é válido").
UNDER_ALLOCATION_THRESHOLD_PCT = 90
OVER_ALLOCATION_THRESHOLD_PCT = 110


@dataclass(frozen=True)
class WeeklyMuscleTarget:
    """Meta semanal (séries equivalentes) de UM grupo muscular, vinda do
    `training_type` associado ao template (seção 3 do documento).
    `weekly_min`/`weekly_max` são guardrails globais — só `weekly_target` é
    usado para calcular o orçamento nominal de cada slot (seção 13)."""

    muscle_group_id: int
    muscle_group_name: str
    weekly_min: float
    weekly_target: float
    weekly_max: float

    def __post_init__(self) -> None:
        if not (0 <= self.weekly_min <= self.weekly_target <= self.weekly_max):
            raise ValueError(
                f"WeeklyMuscleTarget inválido para grupo {self.muscle_group_id}: "
                f"exige 0 <= min <= target <= max (min={self.weekly_min}, "
                f"target={self.weekly_target}, max={self.weekly_max})"
            )


@dataclass(frozen=True)
class SlotMuscleAllocation:
    """Participação de UM grupo muscular em UM slot do template — a faixa
    percentual (`priority_min_pct`/`priority_max_pct`, seção 4) que aquele
    slot deve entregar da meta semanal do grupo, mais reps/RPE prescritos e o
    booleano `prioritize` (seção 36 — só ordenação, nunca volume)."""

    slot_id: int
    slot_sequence: int
    slot_name: str
    muscle_group_id: int
    # None quando o item ainda não foi configurado para o motor v2 (template
    # editado só pela UI legada, que grava percentual no nível de slot que
    # nunca existiu no v2) — build_template_volume_plan NUNCA trata isso
    # como orçamento 0/0 (viraria BALANCED silencioso e o grupo ficaria
    # sem séries): distribui igualmente o percentual remanescente do slot
    # entre os itens sem configuração.
    priority_min_pct: Optional[int]
    priority_max_pct: Optional[int]
    reps_target: Optional[str] = None
    rpe_target: Optional[str] = None
    # Só ordenação final da sessão (seção 36.1) — NUNCA usado em nenhum
    # cálculo de volume/target/tolerância/score de solver deste domínio.
    prioritize: bool = False

    def has_configured_range(self) -> bool:
        return self.priority_min_pct is not None and self.priority_max_pct is not None


@dataclass
class MuscleActivationVector:
    """Ativação acumulada de um conjunto de exercícios, por grupo muscular —
    resultado de `Σ(sets × stimulus_weight)` (seção 3), já quebrado por papel
    (PRIMARY/SECONDARY/STABILIZER) e também disponível como total. Estrutura
    pura de acumulação — o cálculo real de `sets × stimulus_weight` a partir
    do catálogo é feito por `VolumeService`/`_resolve_muscle_contributions_for_mappings`
    (Fase 5/`template_volume_validator.py`), nunca duplicado aqui."""

    # muscle_group_id -> {"PRIMARY": float, "SECONDARY": float, "STABILIZER": float}
    by_role: dict[int, dict[str, float]] = field(default_factory=dict)

    def add(self, muscle_group_id: int, role: str, amount: float) -> None:
        role = role.upper()
        if role not in MUSCLE_ROLES:
            role = "SECONDARY"
        bucket = self.by_role.setdefault(muscle_group_id, {r: 0.0 for r in MUSCLE_ROLES})
        bucket[role] += amount

    def total(self, muscle_group_id: int) -> float:
        bucket = self.by_role.get(muscle_group_id)
        if not bucket:
            return 0.0
        return sum(bucket.values())

    def muscle_group_ids(self) -> list[int]:
        return list(self.by_role.keys())

    @classmethod
    def empty(cls) -> "MuscleActivationVector":
        return cls()


@dataclass
class MuscleSlotBudget:
    """Orçamento de UM grupo muscular para UM slot específico — já com o
    envelope nominal calculado (`nominal_slot_min/max/mid`, seção 12) e o
    envelope EFETIVO após multiplicador de recuperação (seção 30:
    `effective budget ≈ nominal × recovery_multiplier`). `recovery_status`
    segue o vocabulário de `MuscleRecoveryService.classify_readiness`
    (READY/REDUCED_DOSE/NO_DIRECT_WORK)."""

    muscle_group_id: int
    muscle_group_name: str
    weekly_min: float
    weekly_target: float
    weekly_max: float
    slot_pct_min: Optional[float]  # fração 0-1 (ex.: 0.60), não 0-100
    slot_pct_max: Optional[float]
    nominal_slot_min: float
    nominal_slot_max: float
    recovery_status: str
    recovery_multiplier: float
    reps_target: Optional[str] = None
    rpe_target: Optional[str] = None
    prioritize: bool = False

    @property
    def nominal_slot_mid(self) -> float:
        return (self.nominal_slot_min + self.nominal_slot_max) / 2

    @property
    def effective_slot_min(self) -> float:
        """NO_DIRECT_WORK (multiplier=0.0) sempre resulta em 0 — hard
        constraint (seção 18): o motor nunca tenta atingir um mínimo de
        trabalho direto para um grupo vetado pela recuperação."""
        return self.nominal_slot_min * self.recovery_multiplier

    @property
    def effective_slot_max(self) -> float:
        """REDUCED_DOSE reduz o TETO do envelope (seção 18/30) — o reparo
        (Fase 5) nunca pode devolver o volume removido aqui ultrapassando
        este valor."""
        return self.nominal_slot_max * self.recovery_multiplier

    @property
    def is_hard_excluded(self) -> bool:
        return self.recovery_status == "NO_DIRECT_WORK"

    def deviation_status(self, activation_total: float) -> str:
        """UNDER_ALLOCATED / BALANCED / OVER_ALLOCATED contra o envelope
        EFETIVO (pós-recuperação) — usado por `detect_deviations` (Fase 5)."""
        if self.is_hard_excluded:
            return "BALANCED" if activation_total <= 0 else "OVER_ALLOCATED"
        if activation_total < self.effective_slot_min:
            return "UNDER_ALLOCATED"
        if activation_total > self.effective_slot_max:
            return "OVER_ALLOCATED"
        return "BALANCED"


@dataclass
class GenerationVolumeSummary:
    """Resumo de auditoria de uma geração (seção 79 — persistido no
    `raw_payload`/`generation_summary["volume_planning"]` do draft, sem
    migration nova). Uma entrada por grupo muscular presente no slot
    gerado, com o que o CICLO INTEIRO já tem planejado/executado para aquele
    grupo — não só o slot atual (exemplo da seção 74: `ciclo = 12.5`
    somando A=8.0 + A'=4.5, considerando ativação indireta, não só séries
    diretas)."""

    @dataclass
    class Entry:
        muscle_group_id: int
        muscle_group_name: str
        weekly_target: float
        slot_min: float
        slot_max: float
        planned_activation: float
        deviation_status: str
        recovery_status: str
        cycle_planned_total: float = 0.0
        weekly_volume_deficit: float = 0.0
        note: Optional[str] = None

    entries: list["GenerationVolumeSummary.Entry"] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_entry(self, entry: "GenerationVolumeSummary.Entry") -> None:
        self.entries.append(entry)

    def as_dict(self) -> dict:
        return {
            "entries": [
                {
                    "muscle_group_id": e.muscle_group_id,
                    "muscle_group_name": e.muscle_group_name,
                    "weekly_target": e.weekly_target,
                    "slot_min": round(e.slot_min, 2),
                    "slot_max": round(e.slot_max, 2),
                    "planned_activation": round(e.planned_activation, 2),
                    "deviation_status": e.deviation_status,
                    "recovery_status": e.recovery_status,
                    "cycle_planned_total": round(e.cycle_planned_total, 2),
                    "weekly_volume_deficit": round(e.weekly_volume_deficit, 2),
                    "note": e.note,
                }
                for e in self.entries
            ],
            "warnings": list(self.warnings),
        }


@dataclass
class TemplateCycleContext:
    """Contexto do CICLO inteiro (seção 6 do documento) — montado uma vez por
    `build_template_cycle_context` (Fase 2) e reaproveitado para gerar
    qualquer slot dele, para que o motor sempre "conheça o template inteiro"
    em vez de raciocinar slot a slot isoladamente."""

    template_id: int
    training_type_id: int
    # slots ordenados por sequence (formato bruto de _get_template — dict
    # com id/sequence/name/slot_type/items)
    slots: list[dict] = field(default_factory=list)
    # muscle_group_id -> WeeklyMuscleTarget
    muscle_targets: dict[int, WeeklyMuscleTarget] = field(default_factory=dict)
    # muscle_group_id -> lista de SlotMuscleAllocation (uma por slot em que o
    # grupo aparece), ordenada por slot_sequence
    muscle_slot_allocations: dict[int, list[SlotMuscleAllocation]] = field(default_factory=dict)
    # muscle_group_id -> {"status": str, "multiplier": float} projetado para
    # plan_date (seção 18/30 — vem de project_recovery_for_date +
    # MuscleRecoveryService._RECOMMENDATIONS)
    recovery_state: dict[int, dict] = field(default_factory=dict)

    def slot_by_id(self, slot_id: int) -> Optional[dict]:
        return next((s for s in self.slots if s.get("id") == slot_id), None)

    def ordered_slot_ids(self) -> list[int]:
        return [s["id"] for s in sorted(self.slots, key=lambda s: s["sequence"])]

    def allocations_for_group(self, muscle_group_id: int) -> list[SlotMuscleAllocation]:
        return self.muscle_slot_allocations.get(muscle_group_id, [])

    def recovery_status_for(self, muscle_group_id: int) -> str:
        return self.recovery_state.get(muscle_group_id, {}).get("status", "READY")

    def recovery_multiplier_for(self, muscle_group_id: int) -> float:
        return self.recovery_state.get(muscle_group_id, {}).get("multiplier", 1.0)

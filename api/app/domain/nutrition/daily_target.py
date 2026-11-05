"""Orquestrador puro da meta diária: TMB -> kcal do treino -> TDEE -> alvo
calórico -> macros -> ciclo de carbo / fase.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §7 (E2: a api-core materializa
chamando SÓ `compute_daily_target`; a meta de um dia passado fica congelada).

`ENGINE_VERSION` nasce aqui e é reexportada pelo pacote: cada meta/refeição
materializada grava a versão com que foi calculada (E4).

Fases: `DIET_BREAK` e `REFEED` levam o alvo calórico à manutenção (mesma
proteína e gordura do plano; o carbo absorve a diferença). Ciclo de carbo (N2, D5): com
`PlanParams.cycle_kinds` (os 7 tipos de dia que o usuário escolheu) o dia recebe o
multiplicador do seu tipo sobre a BASE do plano (a meta sem o treino do dia), então a
soma semanal do que o ciclo move é zero sem conhecer os outros dias
(`carb_cycling.apply_cycle_over_base`). Prioridade de macros e gramas manuais (N2) também
vêm do `PlanParams`; o padrão é o comportamento anterior.

Materialização (Lote L2b, mapa §47):
- `TARGET_FREEZE_LAG_DAYS` / `is_target_frozen`: a meta de um dia CONGELA
  quando `hoje >= dia + TARGET_FREEZE_LAG_DAYS` (hoje = dia civil no fuso do
  usuário, `time_windows.user_today`). O valor 2 vem do sync do Intervals.icu,
  que preenche a kcal do treino na madrugada do dia SEGUINTE (cron 05:45 BRT,
  `intervals_sync.py`): ontem ainda pode receber a kcal observada e precisa ser
  recalculável; anteontem já não muda mais.
- `day_context_for`: o contexto do dia a partir das sessões CONCLUÍDAS. Até
  hoje, treino e kcal/duração vêm das sessões (`activities.calories_kcal`,
  qualquer que seja a proveniência; dono da proveniência:
  `domain/calories_source.py`). Dia futuro: treino desconhecido
  (`LIMIT_TRAINING_UNKNOWN_FUTURE_DAY`). Treinos planejados pela atribuição de
  Modelo e o ciclo de carbo entram no L8.
- `missing_subject_data`: quais limitações são DADO DO SUJEITO faltando
  (altura, sexo, peso, nascimento, nível de atividade, %G/MLG) quando a meta
  não é calculável; o preview responde 422 com elas.
- Janelas (`TargetWindow`): `publication_window` (de hoje até o maior entre o
  fim da semana e a última meta já materializada: nenhuma meta futura não
  congelada fica com o plano antigo), `recompute_window` (a partir de
  `first_recomputable_day`, o mesmo critério de `is_target_frozen`) e
  `week_window`. Meta EXISTENTE é atualizada em qualquer dia não congelado da
  janela; dia SEM meta só é criado até `create_until`.
  `TARGET_RECOMPUTE_MAX_DAYS` limita só a CRIAÇÃO pedida no recálculo
  (`range_within_cap`), nunca a atualização de metas já existentes.
  `days_to_materialize` aplica as duas regras (congelamento e criação).

Diário (Lote L4, mapa §52; risco R8 do plano): o L2b só materializa quando
alguém publica, recalcula ou abre o plano, e dia congelado nunca é escrito —
um dia podia congelar sem meta. O diário decide por dia (`diary_target_action`):
- dia NÃO congelado: materializa/atualiza pelo fluxo normal (`MATERIALIZE`),
  criando só até o fim da semana atual (`diary_window`); além dela, sem meta,
  não cria (`SKIP`, `target_future_beyond_week`);
- dia congelado COM meta: só lê (`READ`) — o congelamento não muda;
- dia congelado SEM meta: calcula com os dados disponíveis e grava UMA vez
  (`BACKFILL`), marcado com `target_backfilled` nas limitações e
  `materialization` nas entradas (`backfill_marker`). Depois disso é uma meta
  congelada como as outras. Sem job agendado: a visita ao diário (hoje e
  ontem ainda recalculáveis) mantém as metas em dia.

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio (o "hoje" é argumento).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Iterable, Optional, Sequence

from . import bmr as bmr_mod
from . import carb_cycling
from . import energy_expenditure as ee
from . import macros as macros_mod
from . import manual_targets
from . import strategy as strategy_mod
from .anthropometrics import ActivityLevel, Subject, positive_number
from .body_composition import resolved_fat_mass_kg, resolved_ffm_kg
from .phases import MAINTENANCE_KCAL_PHASES, PhaseKind
from .weekly_budget import week_days

ENGINE_VERSION: Final[str] = "nutrition-v1"

LIMIT_WEEK_FLAGS_REQUIRED_FOR_CYCLE = "week_training_days_required_for_carb_cycle"
# Dia futuro: o treino ainda não aconteceu e a atribuição de Modelo só entra no
# L8. A meta sai como dia sem treino e avisa (no modo explícito o treino do dia
# entra no TDEE quando acontecer, até o dia congelar).
LIMIT_TRAINING_UNKNOWN_FUTURE_DAY = "training_unknown_future_day"
# Gramas manuais em dia de pausa/refeed: a fase leva a estratégia à manutenção, mas as gramas fixas do usuário não mudam.
LIMIT_MANUAL_TARGETS_IGNORE_PHASE = "manual_targets_ignore_phase"

# Congelamento da meta materializada (E2). 2 dias: o sync do Intervals.icu grava
# a kcal do treino na madrugada do dia seguinte (cron 05:45 BRT), então ONTEM
# ainda é recalculável e ANTEONTEM fica congelado. Default de desenho do L2b
# (Tech Lead, 2026-10-17), amarrado ao horário do cron.
TARGET_FREEZE_LAG_DAYS: Final[int] = 2

# Teto de dias que um RECÁLCULO pode pedir (criar metas novas). Default de
# desenho: um mês cobre o horizonte de prescrição do admin/PT e mantém a chamada
# dentro do orçamento de tempo da Lambda. Não limita a atualização de metas já
# materializadas (ver `TargetWindow`).
TARGET_RECOMPUTE_MAX_DAYS: Final[int] = 31

# Erros das janelas de materialização (estáveis; `labels.limitation_label` rotula).
ERR_DATE_FROM_FROZEN = "date_from_frozen"
ERR_DATE_TO_BEFORE_DATE_FROM = "date_to_before_date_from"
ERR_RANGE_TOO_LONG = "range_too_long"

SECONDS_PER_MINUTE: Final[float] = 60.0


@dataclass(frozen=True)
class PlanParams:
    """Parâmetros do plano publicado para um aluno."""

    strategy_params: strategy_mod.StrategyParams
    bmr_formula: bmr_mod.BmrFormula = bmr_mod.DEFAULT_BMR_FORMULA
    tdee_mode: ee.TdeeMode = ee.DEFAULT_TDEE_MODE
    # Modo automático: nível da rotina fora do exercício (None = `ee.ROUTINE_ACTIVITY_LEVEL_DEFAULT`).
    routine_level: Optional[ActivityLevel] = None
    carb_cycling_enabled: bool = False
    meals_per_day: Optional[int] = None
    ffm_age_days: Optional[int] = None
    # N2: prioridade de macros, gramas manuais (proteína, carboidrato, gordura) e os 7 tipos de
    # dia escolhidos (segunda primeiro). Padrões = comportamento anterior, bit a bit.
    macro_priority: strategy_mod.MacroPriority = strategy_mod.DEFAULT_MACRO_PRIORITY
    manual_macros: Optional[tuple[float, float, float]] = None
    manual_training_mode: manual_targets.ManualTrainingMode = manual_targets.DEFAULT_MANUAL_TRAINING_MODE
    cycle_kinds: Optional[tuple[carb_cycling.DayKind, ...]] = None

    def to_dict(self) -> dict[str, Any]:
        dados: dict[str, Any] = {
            "strategy_params": self.strategy_params.to_dict(),
            "bmr_formula": self.bmr_formula.value,
            "tdee_mode": self.tdee_mode.value,
            "carb_cycling_enabled": self.carb_cycling_enabled,
            "meals_per_day": self.meals_per_day,
            "ffm_age_days": self.ffm_age_days,
        }
        if self.routine_level is not None:
            dados["routine_level"] = self.routine_level.value
        if self.macro_priority != strategy_mod.DEFAULT_MACRO_PRIORITY:
            dados["macro_priority"] = self.macro_priority.value
        if self.manual_macros is not None:
            dados["manual_macros"] = list(self.manual_macros)
            if self.manual_training_mode != manual_targets.DEFAULT_MANUAL_TRAINING_MODE:
                dados["manual_training_mode"] = self.manual_training_mode.value
        if self.cycle_kinds is not None:
            dados["cycle_kinds"] = [k.value for k in self.cycle_kinds]
        return dados


def _is_mixed(sessions: Sequence[ee.SessionKcal]) -> bool:
    """Há sessão com kcal informada E sessão sem (o caso que antes derrubava o dia inteiro para MET)."""
    informadas = [positive_number(sessao.calories_kcal) is not None for sessao in sessions]
    return any(informadas) and not all(informadas)


@dataclass(frozen=True)
class DayContext:
    """O que o dia tem: treino (kcal informada / duração / tipo), fase e a
    semana de treino (7 flags, segunda primeiro) para o ciclo de carbo."""

    day: date
    is_training_day: bool = False
    training_kcal_reported: Optional[float] = None
    training_duration_min: Optional[float] = None
    session_kind: Optional[ee.SessionKind] = None
    phase_kind: Optional[PhaseKind] = None
    week_training_days: Optional[tuple[bool, ...]] = None
    # False = dia futuro: ainda não se sabe se haverá treino (vira limitação).
    training_known: bool = True
    # Atividades do Intervals que não são o treino de `activities` (cardio, caminhada...). Não mudam
    # `is_training_day` nem a regra de MET; só somam ao gasto (`ee.extra_activity_kcal`).
    extra_activities: tuple[ee.ExtraActivity, ...] = ()
    # Só no modo automático: Intervals conectado e sincronizado (`ee.intervals_in_sync`); None = não se aplica.
    intervals_in_sync: Optional[bool] = None
    # Sessões do dia (kcal e minutos de cada uma): o motor soma POR SESSÃO (`ee.resolve_sessions_kcal`). Vazio = o
    # contexto veio só com `training_kcal_reported`/`training_duration_min` (caminho antigo).
    training_sessions: tuple[ee.SessionKcal, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        extras = {"extra_activities": [item.to_dict() for item in self.extra_activities]} if self.extra_activities else {}
        if _is_mixed(self.training_sessions):  # só o dia misto entra no hash: os demais mantêm as entradas de antes
            extras["training_sessions"] = [sessao.to_dict() for sessao in self.training_sessions]
        if self.intervals_in_sync is not None:
            extras["intervals_in_sync"] = self.intervals_in_sync
        return {
            **extras,
            "day": self.day.isoformat(),
            "is_training_day": self.is_training_day,
            "training_known": self.training_known,
            "training_kcal_reported": self.training_kcal_reported,
            "training_duration_min": self.training_duration_min,
            "session_kind": self.session_kind.value if self.session_kind else None,
            "phase_kind": self.phase_kind.value if self.phase_kind else None,
            "week_training_days": list(self.week_training_days) if self.week_training_days is not None else None,
        }


@dataclass(frozen=True)
class DailyTarget:
    """Meta do dia, com cada etapa e as limitações agregadas."""

    day: date
    engine_version: str
    subject: Subject
    bmr: bmr_mod.BmrResult
    scale_comparison: Optional[bmr_mod.ScaleComparison]
    training_kcal: ee.TrainingKcal
    tdee: ee.TdeeResult
    kcal_target: strategy_mod.KcalTarget
    macros: macros_mod.MacroTargets
    day_kind: carb_cycling.DayKind
    phase_kind: Optional[PhaseKind]
    protein_per_meal_g: Optional[float]
    limitations: tuple[str, ...] = ()
    # Cardio/caminhada do Intervals somados ao gasto do dia (já dentro de `tdee.kcal`).
    extra_activity: ee.ExtraActivityKcal = ee.NO_EXTRA_ACTIVITY_KCAL

    @property
    def computable(self) -> bool:
        return self.macros.computable

    @property
    def kcal_delta(self) -> Optional[float]:
        """Diferença REAL entre a meta do dia e o gasto total (negativa = déficit): já com gramas manuais,
        ciclo e prioridade. `kcal_target.delta_kcal` é só o da estratégia, antes de tudo isso."""
        if self.macros.kcal is None or self.tdee.kcal is None:
            return None
        return float(self.macros.kcal) - float(self.tdee.kcal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "engine_version": self.engine_version,
            "subject": self.subject.to_dict(),
            "bmr": self.bmr.to_dict(),
            "scale_comparison": self.scale_comparison.to_dict() if self.scale_comparison else None,
            "training_kcal": self.training_kcal.to_dict(),
            "extra_activity": self.extra_activity.to_dict(),
            "tdee": self.tdee.to_dict(),
            "kcal_target": self.kcal_target.to_dict(),
            "macros": self.macros.to_dict(),
            "day_kind": self.day_kind.value,
            "phase_kind": self.phase_kind.value if self.phase_kind else None,
            "protein_per_meal_g": self.protein_per_meal_g,
            "limitations": list(self.limitations),
        }


def _unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
    vistos: list[str] = []
    for grupo in groups:
        for item in grupo:
            if item not in vistos:
                vistos.append(item)
    return tuple(vistos)


def manual_adds_training(plan_params: "PlanParams") -> bool:
    """As gramas manuais somam o treino do dia em carboidrato: modo `ADD_TRAINING` com o gasto do treino à parte
    (com o treino já no fator do gasto não há treino para somar)."""
    return (
        plan_params.manual_macros is not None
        and plan_params.manual_training_mode == manual_targets.ManualTrainingMode.ADD_TRAINING
        and plan_params.tdee_mode in (ee.TdeeMode.EXPLICIT_TRAINING, ee.TdeeMode.AUTO_INTERVALS)
    )


def _manual_targets(
    plan_params: PlanParams,
    params: strategy_mod.StrategyParams,
    subject: Subject,
    ffm_kg: Optional[float],
    target_kcal: Optional[float],
    bmr_kcal: Optional[float],
) -> tuple[macros_mod.MacroTargets, tuple[str, ...]]:
    """Metas do dia a partir das gramas manuais do plano, com os avisos do motor
    (`manual_targets.check_manual_targets`) como limitações."""
    proteina, carbo, gordura = plan_params.manual_macros or (0.0, 0.0, 0.0)
    checagem = manual_targets.check_manual_targets(
        proteina, carbo, gordura, params, subject.weight_kg, ffm_kg, target_kcal, bmr_kcal
    )
    kcal = macros_mod.kcal_from_macros(proteina, carbo, gordura)
    base_kg = ffm_kg if params.protein_basis == strategy_mod.ProteinBasis.FAT_FREE_MASS else subject.weight_kg
    metas = macros_mod.MacroTargets(
        kcal=kcal,
        protein_g=proteina,
        carb_g=carbo,
        fat_g=gordura,
        fiber_g=macros_mod.fiber_target_g(kcal),
        protein_basis=params.protein_basis,
        protein_basis_kg=base_kg,
        protein_g_per_kg=proteina / base_kg if base_kg else None,
        limitations=(),
    )
    avisos: list[str] = []
    for campo in checagem.fields:
        avisos.extend(campo.codes)
    avisos.extend(checagem.limitations)
    return metas, _unique(tuple(avisos))


def _cycle_base(
    plan_params: PlanParams,
    params: strategy_mod.StrategyParams,
    subject: Subject,
    ffm_kg: Optional[float],
    day_macros: macros_mod.MacroTargets,
    bmr_kcal: Optional[float],
    mode: ee.TdeeMode,
    level: Optional[ActivityLevel],
) -> macros_mod.MacroTargets:
    """Meta de base do plano para o ciclo: a do dia sem o treino explícito. Com gramas
    manuais ou gasto embutido no fator, a base é a própria meta do dia. `mode` e `level` são os EFETIVOS do dia."""
    if plan_params.manual_macros is not None or mode != ee.TdeeMode.EXPLICIT_TRAINING:
        return day_macros
    gasto_base = ee.tdee(bmr_kcal, level, mode, 0.0)
    alvo_base = strategy_mod.kcal_target(params, gasto_base.kcal, bmr_kcal, subject.weight_kg, resolved_fat_mass_kg(subject))
    return macros_mod.macro_split_for_priority(
        alvo_base.kcal, params, plan_params.macro_priority, subject.weight_kg, ffm_kg
    )


def compute_daily_target(
    subject: Subject,
    plan_params: PlanParams,
    day_context: DayContext,
    scale_bmr: Any = None,
) -> DailyTarget:
    """Encadeia os motores para UM dia. Nunca levanta por dado faltante: o que
    não dá para calcular vira None + código em `limitations`."""
    tmb = bmr_mod.bmr(subject, plan_params.bmr_formula, plan_params.ffm_age_days)
    comparacao = bmr_mod.compare_with_scale(tmb.kcal, scale_bmr) if scale_bmr is not None else None

    dia_de_treino = day_context.is_training_day or day_context.training_kcal_reported is not None
    if dia_de_treino:
        if day_context.training_sessions:
            treino = ee.resolve_sessions_kcal(
                day_context.training_sessions, subject.weight_kg, ee.met_for_session(day_context.session_kind)
            )
        else:
            treino = ee.resolve_training_kcal(
                reported_kcal=day_context.training_kcal_reported,
                weight_kg=subject.weight_kg,
                duration_min=day_context.training_duration_min,
                met=ee.met_for_session(day_context.session_kind),
            )
    else:
        treino = ee.NO_TRAINING_KCAL
    # Dia de treino sem kcal nem duração: o TDEE sai sem o treino e a limitação
    # precisa aparecer (no modo embutido o treino já está no fator; em dia de
    # descanso não há o que avisar).
    # Modo e nível que valem NESTE dia: nos clássicos os do plano; no automático, rotina (sincronizado) ou fator
    # com exercício do perfil (sem sincronização), este último ignora treino e extras como o embutido.
    modo_efetivo, nivel_efetivo = ee.effective_tdee_basis(
        plan_params.tdee_mode, subject.activity_level, plan_params.routine_level, day_context.intervals_in_sync
    )
    limitacoes_treino = treino.limitations if dia_de_treino and modo_efetivo == ee.TdeeMode.EXPLICIT_TRAINING else ()
    if not day_context.training_known:
        limitacoes_treino = (*limitacoes_treino, LIMIT_TRAINING_UNKNOWN_FUTURE_DAY)

    # Atividades extras (cardio, caminhada do Intervals) somam SÓ no modo automático sincronizado (os modos clássicos
    # ficam como sempre) e nunca em dia futuro.
    soma_extras = (
        plan_params.tdee_mode == ee.TdeeMode.AUTO_INTERVALS
        and modo_efetivo == ee.TdeeMode.EXPLICIT_TRAINING
        and day_context.training_known
    )
    extra = ee.extra_activity_kcal(day_context.extra_activities) if soma_extras else ee.NO_EXTRA_ACTIVITY_KCAL
    atividade_kcal = (treino.kcal or 0.0) + extra.kcal
    gasto = ee.tdee(tmb.kcal, nivel_efetivo, modo_efetivo, atividade_kcal)

    params = plan_params.strategy_params
    fase = day_context.phase_kind
    if fase is not None and fase in MAINTENANCE_KCAL_PHASES and params.strategy != strategy_mod.Strategy.MAINTENANCE:
        params = params.as_maintenance()
    massa_gorda = resolved_fat_mass_kg(subject)
    alvo = strategy_mod.kcal_target(params, gasto.kcal, tmb.kcal, subject.weight_kg, massa_gorda)

    mlg = resolved_ffm_kg(subject)
    limitacoes_manual: tuple[str, ...] = ()
    metas_repouso: Optional[macros_mod.MacroTargets] = None
    if plan_params.manual_macros is not None:
        # Gramas manuais (D6): o usuário fixou proteína, carboidrato e gordura; a kcal é a de Atwater das gramas. No
        # modo `FIXED` não acompanha o treino do dia; no `ADD_TRAINING` as gramas valem para o dia sem treino e o gasto do
        # treino do dia soma em carboidrato (4 kcal/g). Os avisos do motor viram limitações.
        soma_treino = manual_adds_training(plan_params) and modo_efetivo == ee.TdeeMode.EXPLICIT_TRAINING
        alvo_ref = alvo.kcal
        if soma_treino:  # a referência dos avisos de energia é a meta do dia SEM o treino
            gasto_repouso = ee.tdee(tmb.kcal, nivel_efetivo, modo_efetivo, 0.0)
            alvo_ref = strategy_mod.kcal_target(params, gasto_repouso.kcal, tmb.kcal, subject.weight_kg, massa_gorda).kcal
        metas, limitacoes_manual = _manual_targets(plan_params, params, subject, mlg, alvo_ref, tmb.kcal)
        metas_repouso = metas
        if soma_treino and atividade_kcal:
            carbo = float(metas.carb_g) + float(atividade_kcal) / macros_mod.KCAL_PER_G_CARB  # type: ignore[arg-type]
            energia = macros_mod.kcal_from_macros(metas.protein_g, carbo, metas.fat_g)
            metas = replace(metas, carb_g=carbo, kcal=energia, fiber_g=macros_mod.fiber_target_g(energia))
        if fase is not None and fase in MAINTENANCE_KCAL_PHASES:
            limitacoes_manual = _unique(limitacoes_manual, (LIMIT_MANUAL_TARGETS_IGNORE_PHASE,))
    else:
        metas = macros_mod.macro_split_for_priority(
            alvo.kcal, params, plan_params.macro_priority, subject.weight_kg, mlg
        )

    tipo_dia = carb_cycling.DayKind.NORMAL
    limitacoes_ciclo: tuple[str, ...] = ()
    if plan_params.cycle_kinds is not None:
        # Dias escolhidos (D5): o ciclo se aplica dia a dia sobre a BASE do plano (a meta do dia
        # sem o treino dele), então a média semanal se preserva sem conhecer os outros dias.
        tipo_dia = plan_params.cycle_kinds[day_context.day.weekday()]
        base = metas_repouso if metas_repouso is not None else _cycle_base(plan_params, params, subject, mlg, metas, tmb.kcal, modo_efetivo, nivel_efetivo)
        metas, limitacoes_ciclo = carb_cycling.apply_cycle_over_base(
            metas, base, tipo_dia, plan_params.cycle_kinds, subject.weight_kg
        )
    elif plan_params.carb_cycling_enabled:
        if day_context.week_training_days is None:
            limitacoes_ciclo = (LIMIT_WEEK_FLAGS_REQUIRED_FOR_CYCLE,)
        else:
            tipos = carb_cycling.day_kinds_for_week(day_context.week_training_days)
            tipo_dia = tipos[day_context.day.weekday()]
            metas, limitacoes_ciclo = carb_cycling.apply_cycle(metas, tipo_dia, tipos, subject.weight_kg)

    por_refeicao = macros_mod.protein_per_meal(metas.protein_g, plan_params.meals_per_day)

    return DailyTarget(
        day=day_context.day,
        engine_version=ENGINE_VERSION,
        subject=subject,
        bmr=tmb,
        scale_comparison=comparacao,
        training_kcal=treino,
        tdee=gasto,
        kcal_target=alvo,
        macros=metas,
        day_kind=tipo_dia,
        phase_kind=fase,
        protein_per_meal_g=por_refeicao,
        extra_activity=extra,
        limitations=_unique(
            tmb.limitations,
            limitacoes_treino,
            gasto.limitations,
            alvo.limitations,
            metas.limitations,
            limitacoes_manual,
            limitacoes_ciclo,
            # Faixas do plano PUBLICADO: na pausa/refeed a proteína do cutting
            # segue valendo e não é "fora da faixa" da manutenção.
            plan_params.strategy_params.out_of_range(),
        ),
    )


# ---------------------------------------------------------------------------
# Materialização (L2b): congelamento, contexto do dia e dado faltante
# ---------------------------------------------------------------------------


def is_target_frozen(day: date, today: date) -> bool:
    """A meta de `day` já não muda: `today >= day + TARGET_FREEZE_LAG_DAYS`.
    `today` é o dia civil do USUÁRIO (fuso do perfil), nunca o do servidor."""
    return today >= day + timedelta(days=TARGET_FREEZE_LAG_DAYS)


def first_recomputable_day(today: date) -> date:
    """Primeiro dia ainda NÃO congelado (o mesmo critério de `is_target_frozen`)."""
    return today - timedelta(days=TARGET_FREEZE_LAG_DAYS - 1)


def days_in_range(date_from: date, date_to: date) -> int:
    """Quantos dias tem `[date_from, date_to]` (inclusive); 0 se invertido."""
    return max((date_to - date_from).days + 1, 0)


def range_within_cap(date_from: date, date_to: date) -> bool:
    """O intervalo pedido cabe no teto de criação do recálculo."""
    return date_from <= date_to and days_in_range(date_from, date_to) <= TARGET_RECOMPUTE_MAX_DAYS


@dataclass(frozen=True)
class TargetWindow:
    """Dias que uma chamada pode (re)calcular. Meta EXISTENTE é atualizada em
    qualquer dia não congelado de `[date_from, date_to]`; dia SEM meta só é
    criado até `create_until`."""

    date_from: date
    date_to: date
    create_until: date

    def days(self) -> tuple[date, ...]:
        return tuple(self.date_from + timedelta(days=i) for i in range(days_in_range(self.date_from, self.date_to)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "create_until": self.create_until.isoformat(),
        }


@dataclass(frozen=True)
class WindowResult:
    """Janela pedida ou o código do erro (nunca os dois)."""

    window: Optional[TargetWindow] = None
    error: Optional[str] = None


def week_window(today: date) -> TargetWindow:
    """A semana de `today` (segunda a domingo, `weekly_budget.week_days`)."""
    dias = week_days(today)
    return TargetWindow(date_from=dias[0], date_to=dias[-1], create_until=dias[-1])


def publication_window(today: date, last_target_date: Optional[date]) -> TargetWindow:
    """Janela de uma publicação (o plano vale a partir de `today`): cria as
    metas até o fim da semana e ATUALIZA todas as já materializadas de hoje em
    diante, até a última — nenhuma meta futura não congelada fica com o plano
    antigo. O teto do recálculo não se aplica aqui (só atualiza o que existe)."""
    fim_da_semana = week_days(today)[-1]
    fim = max(fim_da_semana, last_target_date) if last_target_date is not None else fim_da_semana
    return TargetWindow(date_from=today, date_to=fim, create_until=fim_da_semana)


def recompute_window(date_from: date, date_to: Optional[date], today: date) -> WindowResult:
    """Janela do recálculo pedido pelo admin/PT. Começa no máximo em
    `first_recomputable_day` (dia congelado não muda); sem `date_to`, vai até o
    fim da semana de `date_from`; o intervalo inteiro cabe no teto de criação."""
    if date_from < first_recomputable_day(today):
        return WindowResult(error=ERR_DATE_FROM_FROZEN)
    fim = date_to if date_to is not None else week_days(date_from)[-1]
    if fim < date_from:
        return WindowResult(error=ERR_DATE_TO_BEFORE_DATE_FROM)
    if not range_within_cap(date_from, fim):
        return WindowResult(error=ERR_RANGE_TOO_LONG)
    return WindowResult(window=TargetWindow(date_from=date_from, date_to=fim, create_until=fim))


def days_to_materialize(window: TargetWindow, today: date, existing_days: Any) -> tuple[date, ...]:
    """Dias da janela que a materialização escreve: nunca um dia congelado; com
    meta existente, sempre; sem meta, só até `window.create_until`."""
    existentes = set(existing_days or ())
    return tuple(
        dia
        for dia in window.days()
        if not is_target_frozen(dia, today) and (dia in existentes or dia <= window.create_until)
    )


@dataclass(frozen=True)
class SessionFacts:
    """Uma sessão de treino CONCLUÍDA do dia, como a api-core a lê:
    `activities.calories_kcal` (qualquer proveniência) e a duração em segundos."""

    calories_kcal: Any = None
    duration_seconds: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "calories_kcal": positive_number(self.calories_kcal),
            "duration_seconds": positive_number(self.duration_seconds),
        }


def _sum_if_all(values: Sequence[Optional[float]]) -> Optional[float]:
    if not values or any(valor is None for valor in values):
        return None
    return float(sum(valor for valor in values if valor is not None))


def day_context_for(
    day: date,
    today: date,
    sessions: Sequence[SessionFacts] = (),
    extra_activities: Sequence[ee.ExtraActivity] = (),
    intervals_in_sync: Optional[bool] = None,
) -> DayContext:
    """Contexto do dia para a materialização.

    - Dia FUTURO (`day > today`): treino desconhecido -> dia sem treino com
      `training_known=False` (limitação `LIMIT_TRAINING_UNKNOWN_FUTURE_DAY`).
    - Até hoje: há treino quando há sessão concluída no dia. A kcal do dia é somada POR SESSÃO
      (`energy_expenditure.resolve_sessions_kcal`): informada onde houver e MET só da duração das sessões sem kcal
      (dia misto = parcialmente estimado). Os campos `training_kcal_reported`/`training_duration_min` seguem com a
      regra antiga (soma só quando TODAS têm) para quem ainda os lê.
    - `extra_activities` (cardio, caminhada do Intervals): vão no contexto sem mexer em `is_training_day` nem na
      kcal/duração do treino; só somam ao gasto (`compute_daily_target`). Dia futuro não as tem.
    """
    if day > today:
        return DayContext(day=day, is_training_day=False, training_known=False, intervals_in_sync=intervals_in_sync)
    extras = tuple(extra_activities)
    if not sessions:
        return DayContext(day=day, extra_activities=extras, intervals_in_sync=intervals_in_sync)
    kcal = _sum_if_all([positive_number(sessao.calories_kcal) for sessao in sessions])
    segundos = _sum_if_all([positive_number(sessao.duration_seconds) for sessao in sessions])
    por_sessao = tuple(
        ee.SessionKcal(
            calories_kcal=sessao.calories_kcal,
            duration_min=None if positive_number(sessao.duration_seconds) is None else positive_number(sessao.duration_seconds) / SECONDS_PER_MINUTE,
        )
        for sessao in sessions
    )
    return DayContext(
        day=day,
        is_training_day=True,
        training_sessions=por_sessao,
        training_kcal_reported=kcal,
        training_duration_min=None if segundos is None else segundos / SECONDS_PER_MINUTE,
        extra_activities=extras,
        intervals_in_sync=intervals_in_sync,
    )


# Limitações que são DADO DO SUJEITO faltando (o que o admin/PT ou o aluno
# precisam preencher). As derivadas (`missing_bmr`, `missing_tdee`,
# `missing_kcal`) ficam de fora: são consequência destas.
SUBJECT_DATA_LIMITATIONS: Final[tuple[str, ...]] = (
    bmr_mod.LIMIT_MISSING_SEX,
    bmr_mod.LIMIT_SEX_OTHER_REQUIRES_FFM,
    bmr_mod.LIMIT_MISSING_HEIGHT,
    bmr_mod.LIMIT_MISSING_WEIGHT,
    bmr_mod.LIMIT_MISSING_AGE,
    bmr_mod.LIMIT_MISSING_FFM,
    ee.LIMIT_MISSING_ACTIVITY_LEVEL,
    strategy_mod.LIMIT_MISSING_FAT_MASS,
    macros_mod.LIMIT_MISSING_PROTEIN_BASIS,
)


def missing_subject_data(target: DailyTarget) -> tuple[str, ...]:
    """Dados do sujeito que impedem a meta (vazio quando a meta é calculável).
    Meta não calculável sem nenhum código desta lista devolve as limitações
    todas: o 422 nunca sai sem motivo."""
    if target.computable:
        return ()
    faltas = tuple(codigo for codigo in SUBJECT_DATA_LIMITATIONS if codigo in target.limitations)
    return faltas or target.limitations


# ---------------------------------------------------------------------------
# Diário (L4): meta do dia lido pelo diário, inclusive o dia que congelou sem meta
# ---------------------------------------------------------------------------

LIMIT_TARGET_BACKFILLED = "target_backfilled"
LIMIT_TARGET_FUTURE_BEYOND_WEEK = "target_future_beyond_week"
BACKFILL_KIND: Final[str] = "backfill_after_freeze"


class TargetAction(str, Enum):
    """O que o diário faz com a meta de um dia (`diary_target_action`)."""

    READ = "read"  # congelada e gravada: só lê
    MATERIALIZE = "materialize"  # não congelada: fluxo normal (cria/atualiza)
    BACKFILL = "backfill"  # congelada SEM meta: calcula e grava uma vez
    SKIP = "skip"  # futura além da semana atual, sem meta: não cria


def diary_target_action(day: date, today: date, has_target: bool) -> TargetAction:
    """Decisão por dia (R8). `today` é o dia civil do ALUNO."""
    if is_target_frozen(day, today):
        return TargetAction.READ if has_target else TargetAction.BACKFILL
    if has_target or day <= week_days(today)[-1]:
        return TargetAction.MATERIALIZE
    return TargetAction.SKIP


def diary_window(days: Iterable[date], today: date) -> Optional[TargetWindow]:
    """Janela de materialização dos dias `MATERIALIZE` do diário: atualiza as
    metas existentes e cria só até o fim da semana atual (como `week_window`)."""
    dias = sorted(set(days))
    if not dias:
        return None
    fim_da_semana = week_days(today)[-1]
    return TargetWindow(date_from=dias[0], date_to=dias[-1], create_until=min(dias[-1], fim_da_semana))


def diary_target_notice(action: TargetAction) -> tuple[str, ...]:
    """Aviso do diário quando a meta do dia não é criada (dia além da semana)."""
    return (LIMIT_TARGET_FUTURE_BEYOND_WEEK,) if action == TargetAction.SKIP else ()


def backfill_limitations(target: DailyTarget) -> tuple[str, ...]:
    """Limitações gravadas numa meta calculada depois do congelamento."""
    return _unique(target.limitations, (LIMIT_TARGET_BACKFILLED,))


def backfill_marker(today: date) -> dict[str, Any]:
    """Marca da meta retroativa nas entradas gravadas (`inputs_json.materialization`)."""
    return {"kind": BACKFILL_KIND, "computed_on": today.isoformat()}


def was_backfilled(limitations: Any) -> bool:
    return LIMIT_TARGET_BACKFILLED in tuple(limitations or ())

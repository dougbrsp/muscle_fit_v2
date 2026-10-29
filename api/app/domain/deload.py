"""Deload (M97 D): dias em que o usuário reduz carga, RPE, reps e séries SEM
que o motor de progressão trate a sessão como regressão.

Dono ÚNICO (mapa `docs/motores/FONTES_DA_VERDADE.md` §67; guarda
`tests/unit/test_guarda_fontes_da_verdade.py`, padrões `deload_filtro_sql` e
`deload_fator`) de:

- vocabulário da origem (`DeloadSource`: `period` | `manual`) e coluna
  `activities.deload_source` (migração 094; NULL = sessão normal);
- quando uma sessão é de deload (`is_deload_session`) e qual origem se carimba
  no `start` (`session_deload_source`: período ativo na data da sessão vence a
  marca avulsa);
- prescrição de deload (`deload_prescription`): carga = `DELOAD_LOAD_FACTOR` x
  carga de trabalho da trilha (no passo do equipamento), RPE-alvo
  `DELOAD_TARGET_RPE`, reps = PISO da faixa do item (ou, sem faixa, do default
  do objetivo, dono `prescription_defaults`), séries = reduzidas em
  `DELOAD_SETS_REDUCTION_FRACTION` (mínimo `DELOAD_MIN_SETS`);
- carga da próxima série numa sessão de deload (`deload_next_set_load`);
- item da ficha como a sessão de deload o executa desde a 1ª série
  (`deload_plan_item`) e técnicas permitidas no deload
  (`DELOAD_ALLOWED_TECHNIQUES`: nenhuma; `deload_allows_technique`) e o
  par trocado no Modo Treino desde a 1ª série (`pair_deload_prescription`,
  `deload_pair_suggestion`);
- período ("semana de deload"): duração default/teto, validação, sobreposição
  e se um dia/janela cai em deload (`active_on`, `overlaps_window`);
- supressão de insights no deload (`suppresses_below_min_volume`,
  `suppresses_regression`);
- predicados SQL (`sql_is_deload`, `sql_not_deload`, `sql_period_overlaps`):
  ninguém escreve `deload_source IS NULL` à mão.

Contabilidade (decisão do usuário, 2026-10-25): a sessão de deload CONTA na
recuperação/fadiga e no volume semanal real; NÃO entra na progressão (nem
avança, nem regride, nem conta para platô), na comparabilidade/exposições, no
e1RM nem no recorde.

Python puro: sem banco, sem FastAPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from .prescription_defaults import defaults_for
from .training_goal import goal_value
from .training_history import parse_int, parse_reps_range

# ---------------------------------------------------------------------------
# Constantes (valores a calibrar no PR; decisão do usuário de 2026-10-25)
# ---------------------------------------------------------------------------

DELOAD_LOAD_FACTOR: Final[Decimal] = Decimal("0.60")
DELOAD_TARGET_RPE: Final[int] = 6
DELOAD_SETS_REDUCTION_FRACTION: Final[Fraction] = Fraction(1, 3)
DELOAD_MIN_SETS: Final[int] = 1

# Técnicas avançadas permitidas numa sessão de deload (decisão do Tech Lead,
# 2026-10-26): NENHUMA. A sessão de deload reduz o estímulo; drop set,
# rest-pause, FST-7 e afins vão na direção contrária.
DELOAD_ALLOWED_TECHNIQUES: Final[frozenset[str]] = frozenset()

# "Semana de deload": sem `ends_on`, o período dura uma semana a partir do
# início; o teto evita um "deload" que congele a progressão por meses.
DELOAD_PERIOD_DEFAULT_DAYS: Final[int] = 7
DELOAD_PERIOD_MAX_DAYS: Final[int] = 14

DELOAD_SOURCE_COLUMN: Final[str] = "deload_source"


class DeloadSource(str, Enum):
    PERIOD = "period"  # sessão iniciada num dia coberto por um período ativo
    MANUAL = "manual"  # sessão marcada como deload no início do Modo Treino


DELOAD_SOURCE_VALUES: Final[tuple[str, ...]] = tuple(source.value for source in DeloadSource)

# Motivo da decisão neutra da progressão (log/resumo do `finish`): a sessão
# de deload foi pulada.
SKIP_REASON_DELOAD: Final[str] = "deload"


class InvalidDeloadPeriod(ValueError):
    """Período fora das regras (a API devolve 422 com esta mensagem)."""


# ---------------------------------------------------------------------------
# Sessão de deload
# ---------------------------------------------------------------------------


def read_deload_source(value: Any) -> Optional[DeloadSource]:
    """Leitura tolerante da coluna: NULL/vazio/desconhecido = sessão normal."""
    if isinstance(value, DeloadSource):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    text = "" if value is None else str(value).strip().lower()
    try:
        return DeloadSource(text)
    except ValueError:
        return None


def deload_source_value(value: Any) -> Optional[str]:
    """VALOR a gravar na coluna (`None` = sessão normal)."""
    source = read_deload_source(value)
    return None if source is None else source.value


def is_deload_session(deload_source: Any = None, *, manual: bool = False, period_active: bool = False) -> bool:
    """A sessão é de deload? Pela origem gravada (`activities.deload_source`)
    ou, antes de gravar, pela marca avulsa / período ativo."""
    return read_deload_source(deload_source) is not None or bool(manual) or bool(period_active)


def session_deload_source(*, manual: bool, period_active: bool) -> Optional[str]:
    """Origem a carimbar no `start`: período ativo na data da sessão vence a
    marca avulsa (o período é o motivo); sem nenhum dos dois, `None`."""
    if period_active:
        return DeloadSource.PERIOD.value
    if manual:
        return DeloadSource.MANUAL.value
    return None


# ---------------------------------------------------------------------------
# Prescrição de deload
# ---------------------------------------------------------------------------


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def deload_sets(sets_count: Any) -> Optional[int]:
    """Séries reduzidas em ~1/3 (redução arredondada, meio para cima), nunca
    abaixo de `DELOAD_MIN_SETS`: 1 -> 1, 2 -> 1, 3 -> 2, 4 -> 3, 5 -> 3, 6 -> 4.
    Sem séries prescritas -> `None` (quem consome mantém o que tinha)."""
    sets = parse_int(sets_count)
    if sets is None or sets <= 0:
        return None
    reduction = (Decimal(sets) * Decimal(DELOAD_SETS_REDUCTION_FRACTION.numerator)
                 / Decimal(DELOAD_SETS_REDUCTION_FRACTION.denominator)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return max(DELOAD_MIN_SETS, sets - int(reduction))


def deload_reps(reps_target: Any = None, training_goal: Any = None) -> Optional[int]:
    """PISO da faixa de reps do item (`"10-12"` -> 10); sem faixa no item, o
    piso da faixa default do objetivo (dono `prescription_defaults`)."""
    floor, _ceiling = parse_reps_range(reps_target)
    if floor is None:
        floor, _ceiling = parse_reps_range(defaults_for(training_goal).reps_target)
    return floor


def deload_load_kg(working_load_kg: Any, step_kg: Any = None) -> Optional[float]:
    """`DELOAD_LOAD_FACTOR` x carga de trabalho, no passo do equipamento
    (helper único `progression_engine.round_to_equipment_step`). Sem carga de
    trabalho -> `None` (sem sugestão de carga)."""
    from .progression_engine import round_to_equipment_step

    working = _decimal(working_load_kg)
    if working is None:
        return None
    return round_to_equipment_step(working * DELOAD_LOAD_FACTOR, step_kg)


@dataclass(frozen=True)
class DeloadPrescription:
    """O que a sessão de deload prescreve para UM item."""

    training_goal: str
    working_load_kg: Optional[float]
    load_kg: Optional[float]
    target_rpe: int
    reps_target: Optional[int]
    sets_count: Optional[int]
    load_factor: float = float(DELOAD_LOAD_FACTOR)

    def to_dict(self) -> dict[str, Any]:
        return {
            "training_goal": self.training_goal,
            "working_load_kg": self.working_load_kg,
            "load_kg": self.load_kg,
            "load_factor": self.load_factor,
            "target_rpe": self.target_rpe,
            "reps_target": self.reps_target,
            "sets_count": self.sets_count,
        }


def deload_prescription(
    *,
    working_load_kg: Any,
    sets_count: Any = None,
    reps_target: Any = None,
    training_goal: Any = None,
    step_kg: Any = None,
) -> DeloadPrescription:
    """Prescrição de deload sobre a carga de trabalho dada (a da trilha;
    ver `item_deload_prescription` para a precedência das fontes)."""
    working = _decimal(working_load_kg)
    return DeloadPrescription(
        training_goal=goal_value(training_goal),
        working_load_kg=None if working is None else float(working),
        load_kg=deload_load_kg(working, step_kg),
        target_rpe=DELOAD_TARGET_RPE,
        reps_target=deload_reps(reps_target, training_goal),
        sets_count=deload_sets(sets_count),
    )


def _first(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def item_deload_prescription(
    *,
    track_working_load_kg: Any,
    snapshot: Optional[Mapping[str, Any]] = None,
    plan_row: Optional[Mapping[str, Any]] = None,
    step_kg: Any = None,
) -> DeloadPrescription:
    """Prescrição de deload de UM item da ficha, com a precedência das fontes:

    - carga de trabalho: em FORCA com e1RM confiavel, a sugerida pela faixa
      %1RM congelada (`snapshot.strength_load`, dono `progression_engine`;
      decisao do Tech Lead, 2026-10-26: "carga de forca sempre pelo %1RM");
      senao a da TRILHA; sem trilha, a sugerida congelada
      (`snapshot.suggested_load_kg`), senão a carga-alvo congelada
      (`snapshot.load_target`), senão a da linha da ficha (`plan_row.load_target`);
    - séries, faixa de reps e objetivo: os do snapshot congelado; sem eles, os
      da linha da ficha (`plan_row`)."""
    from .progression_engine import e1rm_load_suggested_kg

    snapshot = snapshot or {}
    plan_row = plan_row or {}
    working = _first(
        e1rm_load_suggested_kg(snapshot.get("strength_load")),
        track_working_load_kg,
        snapshot.get("suggested_load_kg"),
        snapshot.get("load_target"),
        plan_row.get("load_target"),
    )
    return deload_prescription(
        working_load_kg=working,
        sets_count=_first(snapshot.get("sets_count"), plan_row.get("sets_count")),
        reps_target=_first(snapshot.get("reps_target"), plan_row.get("reps_target")),
        training_goal=_first(snapshot.get("training_goal"), plan_row.get("training_goal")),
        step_kg=step_kg,
    )


def deload_allows_technique(technique_code: Any) -> bool:
    """A sessão de deload permite a técnica avançada `technique_code`? Só as
    de `DELOAD_ALLOWED_TECHNIQUES` (hoje nenhuma). Sem técnica -> `True` (nada
    a vetar)."""
    code = (str(technique_code) if technique_code is not None else "").strip()
    return not code or code in DELOAD_ALLOWED_TECHNIQUES


def deload_effort_target(protocol_target: Any = None, technique_code: Any = None) -> Any:
    """RPE-alvo cobrado (e gravado) numa série de sessão de deload: o alvo do
    PROTOCOLO da técnica só vale para uma técnica que o deload permite
    (`deload_allows_technique`; hoje nenhuma, então é sempre
    `DELOAD_TARGET_RPE`)."""
    if protocol_target is not None and technique_code and deload_allows_technique(technique_code):
        return protocol_target
    return DELOAD_TARGET_RPE


# Campos do item da ficha (e do snapshot exibido) que descrevem a técnica:
# saem do item quando o deload não a permite.
_TECHNIQUE_ITEM_FIELDS: Final[tuple[str, ...]] = (
    "advanced_technique",
    "advanced_technique_applications",
    "advanced_technique_duration_seconds",
)


def _without_technique(row: dict[str, Any], technique_code: Any) -> None:
    if deload_allows_technique(technique_code):
        return
    for field in _TECHNIQUE_ITEM_FIELDS:
        if field in row:
            row[field] = None
    if "advanced_technique_parameters" in row:
        row["advanced_technique_parameters"] = None
    if "application_loads_kg" in row:
        row["application_loads_kg"] = []


def deload_plan_item(
    item: Mapping[str, Any],
    prescription: DeloadPrescription,
    *,
    format_load: Any = None,
    session_load_plan: Any = None,
) -> dict[str, Any]:
    """Item da ficha COMO A SESSÃO DE DELOAD O EXECUTA (overlay de leitura; a
    ficha congelada não muda): desde a 1ª série, carga = `prescription.load_kg`,
    reps = o piso, séries = as reduzidas, RPE-alvo `DELOAD_TARGET_RPE` (nunca
    acima do teto exibido) e nenhuma técnica fora de
    `DELOAD_ALLOWED_TECHNIQUES`. Valor ausente na prescrição de deload (sem
    carga de base, sem séries) mantém o do item. Sem faixa %1RM (a carga é a de
    deload). `format_load(kg)` = texto de `load_target`; `session_load_plan(
    anchor, item)` = bloco das cargas de aquecimento/ajuste sobre a âncora de
    deload. O item ganha `deload` (a prescrição pronta)."""
    out = dict(item)
    technique = out.get("advanced_technique")
    shown_source = out.get("prescription")
    shown = dict(shown_source) if isinstance(shown_source, Mapping) else None
    if shown is not None and shown.get("advanced_technique"):
        technique = technique or shown.get("advanced_technique")

    load = prescription.load_kg
    reps = prescription.reps_target
    sets = prescription.sets_count
    target_rpe = DELOAD_TARGET_RPE
    updates: dict[str, Any] = {"rpe_target": str(target_rpe)}
    if load is not None:
        updates["suggested_load_kg"] = load
        updates["load_target"] = format_load(load) if format_load is not None else str(load)
    if reps is not None:
        updates["reps_target"] = str(reps)
        updates["suggested_reps"] = reps
    if sets is not None:
        updates["sets_count"] = sets
    out.update(updates)
    _without_technique(out, technique)

    if shown is not None:
        shown.update(updates)
        shown["effective_rpe_target"] = _rpe_target_under(shown.get("effective_rpe_ceiling"))
        shown["strength_load"] = None
        _without_technique(shown, technique)
        if session_load_plan is not None:
            anchor = load if load is not None else shown.get("load_anchor_kg")
            shown.update(session_load_plan(anchor, out))
        out["prescription"] = shown
    out["deload"] = prescription.to_dict()
    return out


def _rpe_target_under(ceiling: Any) -> int:
    """`DELOAD_TARGET_RPE`, nunca acima do teto exibido (inteiro)."""
    if isinstance(ceiling, int) and not isinstance(ceiling, bool):
        return min(DELOAD_TARGET_RPE, ceiling)
    return DELOAD_TARGET_RPE


def pair_deload_prescription(
    *,
    track_working_load_kg: Any,
    suggestion: Optional[Mapping[str, Any]],
    training_goal: Any = None,
) -> DeloadPrescription:
    """Prescrição de deload do PAR TROCADO no Modo Treino (M97-D2, 2026-10-26),
    sobre a sugestão do motor para o par novo (`GET
    /exercises/suggested-progression`): a MESMA precedência de
    `item_deload_prescription`, com a sugestão no lugar do snapshot congelado
    (faixa %1RM do par novo em força com e1RM > trilha do par novo > carga
    sugerida). Reps = piso da faixa sugerida (sem faixa, a do objetivo). Séries
    ficam fora (`sets_count` = None): a ficha da sessão já as traz reduzidas
    (`deload_plan_item`), e reduzir de novo dobraria o corte."""
    suggestion = suggestion or {}
    return item_deload_prescription(
        track_working_load_kg=track_working_load_kg,
        snapshot={
            "strength_load": suggestion.get("strength_load"),
            "suggested_load_kg": suggestion.get("suggested_load_kg"),
            "reps_target": suggestion.get("reps_target"),
            "training_goal": goal_value(training_goal),
        },
        step_kg=suggestion.get("load_increment_kg"),
    )


def pair_swapped(
    planned_mapping_id: Any, planned_gym_equipment_id: Any, logged_mapping_id: Any, logged_gym_equipment_id: Any
) -> bool:
    """O exercício lançado é de um PAR diferente do planejado no item da ficha
    (troca de exercício ou de equipamento no Modo Treino)? Então a base do
    deload é a do par novo (`pair_deload_prescription`), não o snapshot
    congelado do item (que é do par original). Sem item planejado -> False
    (nada a comparar)."""
    planned = (parse_int(planned_mapping_id), parse_int(planned_gym_equipment_id))
    if planned == (None, None):
        return False
    return planned != (parse_int(logged_mapping_id), parse_int(logged_gym_equipment_id))


def deload_pair_suggestion(
    suggestion: Mapping[str, Any], prescription: DeloadPrescription, *, format_load: Any = None
) -> dict[str, Any]:
    """Sugestão do par trocado COMO A SESSÃO DE DELOAD A EXECUTA desde a 1ª
    série (overlay de leitura, o par de `deload_plan_item`): carga
    (`load_target`/`suggested_load_kg`), reps no piso (`reps_target`/
    `suggested_reps`), RPE-alvo `DELOAD_TARGET_RPE` (nunca acima de
    `effective_rpe_ceiling`), sem faixa %1RM, nota = `deload_reason_label` e o
    bloco `deload` (a prescrição pronta). Valor ausente na prescrição mantém o
    da sugestão. Técnica: a sugestão não a traz; a do item já sai nula da ficha
    da sessão de deload."""
    out = dict(suggestion)
    out["rpe_target"] = str(_rpe_target_under(out.get("effective_rpe_ceiling")))
    if prescription.load_kg is not None:
        out["suggested_load_kg"] = prescription.load_kg
        out["load_target"] = format_load(prescription.load_kg) if format_load is not None else str(prescription.load_kg)
    if prescription.reps_target is not None:
        out["reps_target"] = str(prescription.reps_target)
        out["suggested_reps"] = prescription.reps_target
    out.pop("strength_load", None)
    out["progression_note"] = deload_reason_label()
    out["deload"] = prescription.to_dict()
    return out


def deload_next_set_load(
    executed_kg: Any, prescription: DeloadPrescription, step_kg: Any, *, set_type: Any = None, measure: Any = None
):
    """Carga da próxima série numa sessão de deload: a da prescrição de
    deload. Executada acima dela -> REDUZIR para ela; senão MANTER a
    executada (abaixo dos 60% não sobe: o deload não cobra carga). Só série
    de TRABALHO de repetições, como a regra normal; sem carga executada ->
    `None`."""
    from ..set_types import WORKING, normalize_set_type
    from .next_set_load import REASON_DELOAD, NextSetLoad
    from .progression_engine import DECREASE_LOAD, HOLD, load_step
    from .set_measures import is_rep_based

    if normalize_set_type(set_type or WORKING) != WORKING or not is_rep_based(measure):
        return None
    executed = _decimal(executed_kg)
    if executed is None:
        return None
    step = float(load_step(step_kg))
    target = prescription.load_kg
    if target is not None and float(executed) > float(target):
        return NextSetLoad(
            direction=DECREASE_LOAD, load_kg=float(target), reason=REASON_DELOAD,
            executed_kg=float(executed), step_kg=step, planned_rpe=float(DELOAD_TARGET_RPE),
        )
    return NextSetLoad(
        direction=HOLD, load_kg=float(executed), reason=REASON_DELOAD,
        executed_kg=float(executed), step_kg=step, planned_rpe=float(DELOAD_TARGET_RPE),
    )


# Rótulo da marca de deload nos gráficos e no relatório (M97 F4): a tela e o
# relatório recebem o texto pronto.
DELOAD_SOURCE_LABELS: Mapping[DeloadSource, str] = {
    DeloadSource.PERIOD: "Deload (período)",
    DeloadSource.MANUAL: "Deload (avulso)",
}


def deload_source_label(value: Any) -> Optional[str]:
    """Rótulo da origem do deload; `None` = sessão normal."""
    source = read_deload_source(value)
    return None if source is None else DELOAD_SOURCE_LABELS[source]


def deload_reason_label() -> str:
    """Rótulo do motivo da próxima série em deload (números interpolados)."""
    percent = int((DELOAD_LOAD_FACTOR * 100).to_integral_value())
    return f"Sessão de deload: {percent}% da carga de trabalho, RPE-alvo {DELOAD_TARGET_RPE}"


# ---------------------------------------------------------------------------
# Período ("semana de deload")
# ---------------------------------------------------------------------------


def default_ends_on(starts_on: date) -> date:
    return starts_on + timedelta(days=DELOAD_PERIOD_DEFAULT_DAYS - 1)


def validate_period(starts_on: date, ends_on: Optional[date], *, today: date) -> tuple[date, date]:
    """`(início, fim)` válidos ou `InvalidDeloadPeriod`. Sem fim = uma semana;
    fim antes do início, mais longo que `DELOAD_PERIOD_MAX_DAYS` ou já
    terminado (antes de `today`, no fuso do usuário) -> erro: a origem é
    carimbada no `start` da sessão, então um período passado não marcaria
    nada."""
    end = ends_on or default_ends_on(starts_on)
    if end < starts_on:
        raise InvalidDeloadPeriod("ends_on não pode ser antes de starts_on")
    if (end - starts_on).days + 1 > DELOAD_PERIOD_MAX_DAYS:
        raise InvalidDeloadPeriod(f"o deload dura no máximo {DELOAD_PERIOD_MAX_DAYS} dias")
    if end < today:
        raise InvalidDeloadPeriod("o período de deload já terminou")
    return starts_on, end


def periods_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


DeloadRange = tuple[date, date]


def active_on(ranges: Iterable[DeloadRange], day: date) -> bool:
    """`day` cai num período/dia de deload?"""
    return any(start <= day <= end for start, end in ranges)


def overlaps_window(ranges: Iterable[DeloadRange], window_start: date, window_end: date) -> bool:
    return any(periods_overlap(start, end, window_start, window_end) for start, end in ranges)


def deload_ranges(periods: Sequence[DeloadRange], session_dates: Iterable[date] = ()) -> tuple[DeloadRange, ...]:
    """Períodos ativos + os dias das sessões de deload (marca avulsa ou de um
    período depois cancelado), em ordem."""
    ranges = {(start, end) for start, end in periods}
    ranges.update((day, day) for day in session_dates)
    return tuple(sorted(ranges))


# ---------------------------------------------------------------------------
# Supressão de insights no deload
# ---------------------------------------------------------------------------


def suppresses_below_min_volume(ranges: Iterable[DeloadRange], window_start: date, window_end: date) -> bool:
    """"Volume abaixo da meta" fica em silêncio quando a régua (as semanas
    avaliadas) toca um deload: a redução é planejada, não déficit."""
    return overlaps_window(ranges, window_start, window_end)


def suppresses_regression(ranges: Iterable[DeloadRange], today: date) -> bool:
    """Alertas de regressão ficam em silêncio enquanto o deload está em curso."""
    return active_on(ranges, today)


# ---------------------------------------------------------------------------
# Ligar/desligar o deload numa sessão JÁ iniciada (M97 F3, decisão do Tech Lead)
# ---------------------------------------------------------------------------

# Até quantas séries gravadas a sessão ainda pode mudar de deload: nenhuma. A
# série salva já foi cobrada (status de esforço, `rpe_target_effective`, carga
# da próxima série) contra o alvo da sessão como ela era; trocar depois
# misturaria os dois regimes na mesma sessão.
DELOAD_TOGGLE_MAX_SAVED_SETS: Final[int] = 0


class DeloadToggleLocked(ValueError):
    """A sessão já tem série gravada: o deload não muda mais (a API devolve 409)."""


def can_toggle_deload(saved_sets: Any) -> bool:
    """A sessão ainda pode ligar/desligar o deload? Só sem série gravada."""
    return (parse_int(saved_sets) or 0) <= DELOAD_TOGGLE_MAX_SAVED_SETS


def toggled_deload_source(current_source: Any, *, requested: bool, saved_sets: Any, period_active: bool) -> Optional[str]:
    """Origem nova quando o usuário liga (`requested=True`) ou desliga o deload
    de uma sessão já iniciada (`PATCH /workout-sessions/{id}/meta`).

    - Pedido igual ao estado atual: a MESMA origem (idempotente, vale mesmo
      com série gravada).
    - Mudança só enquanto `can_toggle_deload(saved_sets)`; senão
      `DeloadToggleLocked`.
    - Ligar: a mesma regra do `start` (`session_deload_source`: período ativo
      na data da sessão vence a marca avulsa).
    - Desligar: `None`, inclusive numa sessão carimbada pelo período (o usuário
      decidiu treinar normal hoje; o período segue valendo para as próximas).
    """
    current = deload_source_value(current_source)
    if bool(requested) == (current is not None):
        return current
    if not can_toggle_deload(saved_sets):
        raise DeloadToggleLocked(
            "A sessão já tem série gravada: o deload só pode ser ligado ou desligado antes da primeira série."
        )
    if requested:
        return session_deload_source(manual=True, period_active=period_active)
    return None


# ---------------------------------------------------------------------------
# SQL (um lugar só)
# ---------------------------------------------------------------------------

_SQL_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _alias(alias: str) -> str:
    if not _SQL_ALIAS_RE.match(alias or ""):
        raise ValueError(f"Alias SQL invalido para deload: {alias!r}")
    return alias


def sql_is_deload(alias: str) -> str:
    """A linha `alias` de `activities` é sessão de deload."""
    return f"{_alias(alias)}.{DELOAD_SOURCE_COLUMN} IS NOT NULL"


def sql_not_deload(alias: str) -> str:
    """A linha `alias` de `activities` NÃO é sessão de deload (exposições,
    e1RM, recorde e sessão anterior do Modo Treino)."""
    return f"{_alias(alias)}.{DELOAD_SOURCE_COLUMN} IS NULL"


def sql_period_overlaps(alias: str) -> str:
    """Período `alias` de `deload_periods` NÃO cancelado que toca o
    intervalo dos parâmetros `(fim, início)`. Um dia só: `(dia, dia)`."""
    a = _alias(alias)
    return f"{a}.cancelled_at IS NULL AND {a}.starts_on <= %s AND {a}.ends_on >= %s"


__all__ = [
    "DELOAD_ALLOWED_TECHNIQUES",
    "DELOAD_LOAD_FACTOR",
    "DELOAD_MIN_SETS",
    "DELOAD_PERIOD_DEFAULT_DAYS",
    "DELOAD_PERIOD_MAX_DAYS",
    "DELOAD_SETS_REDUCTION_FRACTION",
    "DELOAD_TOGGLE_MAX_SAVED_SETS",
    "DELOAD_SOURCE_COLUMN",
    "DELOAD_SOURCE_VALUES",
    "DELOAD_TARGET_RPE",
    "DeloadPrescription",
    "DeloadSource",
    "DeloadToggleLocked",
    "InvalidDeloadPeriod",
    "SKIP_REASON_DELOAD",
    "active_on",
    "can_toggle_deload",
    "default_ends_on",
    "deload_allows_technique",
    "deload_effort_target",
    "deload_load_kg",
    "deload_next_set_load",
    "deload_plan_item",
    "deload_prescription",
    "deload_ranges",
    "deload_reason_label",
    "deload_reps",
    "deload_sets",
    "DELOAD_SOURCE_LABELS",
    "deload_source_label",
    "deload_source_value",
    "is_deload_session",
    "overlaps_window",
    "periods_overlap",
    "read_deload_source",
    "session_deload_source",
    "sql_is_deload",
    "sql_not_deload",
    "sql_period_overlaps",
    "suppresses_below_min_volume",
    "suppresses_regression",
    "toggled_deload_source",
    "validate_period",
]

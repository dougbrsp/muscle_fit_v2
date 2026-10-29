"""Desempenho recente por exercicio - a MESMA regra de progresso do dashboard.

Antes, o sinal "Desempenho recente" da recuperacao (`performance_ratio`) era
"melhor e1RM da sessao / mediana do e1RM dos ultimos 28 dias", com o
exercicio identificado so pelo `mapping_id`. Tres consequencias:

- trocar de maquina (ou de halter para barra) virava "queda de desempenho";
- serie de 15+ reps decidia pelo e1RM, que acima de 12 reps nao e confiavel;
- fazer uma serie a menos (ou sem RPE na serie boa) baixava o e1RM da sessao.

Agora a pergunta "o desempenho caiu?" e respondida pela fonte unica de
progresso por exercicio (`dashboard/comparability.py`), alimentada pelas
mesmas exposicoes montadas por `dashboard/exposure_service.py`
(`assemble_exposures` + `to_summaries`):

- so se compara dentro da chave exercicio canonico + equipamento + convencao
  de carga (`keys_match`); sem par comparavel, NAO ha sinal;
- a queda e o status `REGRESSING` de `compare_exposures` entre a ultima
  exposicao e a anterior (de outra sessao) - nada de banda propria;
- a MAGNITUDE (que separa sinal leve `< 0,95` de sinal forte `< 0,90` em
  `MuscleRecoveryService`) vem da razao de e1RM quando o e1RM e decisivo
  (serie do e1RM com ate `e1rm_max_reliable_reps` reps nos dois lados) e,
  quando nao e, do equivalente Epley na mesma carga das reps emparelhadas
  nas series de topo (as mesmas N melhores series que a regra de reps
  compara), com o RIR de cada serie emparelhada quando todas tem RPE (M78) -
  ver `paired_reps_epley_ratio`.

Este modulo nao le banco: recebe os itens de historico ja lidos pelo
`VolumeService` (um por exercicio de cada sessao, com `set_rows`,
`equipment_id` e `load_convention`) e devolve numeros. Tudo testavel sem
MySQL.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Hashable, Iterable, Mapping, Optional, Sequence

from ..domain import e1rm
from ..domain.deload import is_deload_session
from ..domain.training_history import parse_decimal, parse_int
from ..set_types import is_working_set
from .dashboard.comparability import (
    ExposureSummary,
    compare_exposures,
    e1rm_is_decisive,
    keys_match,
    paired_top_reps,
)
from .dashboard.exposure_service import assemble_exposures, to_summaries
from .dashboard.metrics_registry import THRESHOLDS, ExerciseProgressionStatus

# Chave de sessao de um exercicio: (linha `exercises.id`, `activities.id`).
SessionKey = tuple[Any, Any]

# ---------------------------------------------------------------------------
# Magnitude de UM par de exposicoes (funcao pura sobre ExposureSummary)
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
    """Magnitude quando o e1RM NAO e decisivo: equivalente Epley na MESMA
    carga das reps emparelhadas de topo.

        ratio = (1 + valor_atual_med / 30) / (1 + valor_anterior_med / 30)

    `valor_*_med` = media, por serie, do VALOR que a regra de reps compara
    nas N series emparelhadas (`comparability.paired_top_reps`): `reps + RIR`
    de cada serie quando todas as emparelhadas dos dois lados tem RPE (M78,
    mesma convencao do e1RM), senao reps cruas nos DOIS lados (RIR 0, nunca
    um lado com RIR e o outro sem). Ex.: 15 -> 13 reps sem RPE = 1,433 / 1,5
    = 0,956 (sinal leve, nao NO_DIRECT_WORK); 12 -> 7 = 1,233 / 1,4 = 0,881
    (sinal forte).

    A razao bruta de reps (15 -> 13 = 0,867) era severa demais para limiares
    calibrados em e1RM (0,95 / 0,90): duas reps a menos bloqueariam o grupo.

    Antes do M78 o RIR vinha do RPE MEDIO da exposicao inteira; agora e o
    da propria serie emparelhada - o mesmo numero que decide o status."""
    paired = paired_top_reps(previous, current, float(thresholds["same_load_tolerance_pct"]))
    if paired is None:
        return None
    prev_mean = sum(paired.previous) / paired.pairs
    curr_mean = sum(paired.current) / paired.pairs
    return e1rm.epley_multiplier(curr_mean, 0.0) / e1rm.epley_multiplier(prev_mean, 0.0)


def e1rm_ratio_if_decisive(
    previous: ExposureSummary,
    current: ExposureSummary,
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> Optional[float]:
    """e1RM atual / anterior SO quando o e1RM e decisivo: existe nos dois
    lados e a serie que o gerou tem ate `e1rm_max_reliable_reps` reps nos
    dois lados (o mesmo teto que `compare_exposures` aplica)."""
    if not e1rm_is_decisive(previous, current, int(thresholds["e1rm_max_reliable_reps"])):
        return None
    if not previous.e1rm:
        return None
    return float(current.e1rm) / float(previous.e1rm)


def performance_ratio(
    previous: ExposureSummary,
    current: ExposureSummary,
    *,
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> Optional[float]:
    """`performance_ratio` de UM exercicio: a ultima exposicao contra a
    anterior.

    - Chaves diferentes (outro equipamento/convencao) -> `None`: sem par
      comparavel nao ha sinal, nem positivo nem negativo.
    - Status `REGRESSING` (`compare_exposures`) -> a magnitude da queda,
      sempre `<= 1`: razao de e1RM quando decisivo, senao o equivalente Epley
      das reps emparelhadas na mesma carga (`paired_reps_epley_ratio`).
    - Qualquer outro status -> a magnitude com piso em `1,0`: o que a regra
      de progresso nao chama de queda nunca vira sinal negativo aqui (ex.: e1RM
      menor numa serie de 15 reps, mais carga com menos reps).
    """
    if not keys_match(previous.key, current.key):
        return None
    status, _reasons = compare_exposures(previous, current, thresholds=thresholds)
    magnitude = e1rm_ratio_if_decisive(previous, current, thresholds=thresholds)
    if magnitude is None:
        magnitude = paired_reps_epley_ratio(previous, current, thresholds=thresholds)

    if status is ExerciseProgressionStatus.REGRESSING:
        return None if magnitude is None else min(magnitude, 1.0)
    return 1.0 if magnitude is None else max(magnitude, 1.0)


# ---------------------------------------------------------------------------
# Itens de historico do VolumeService -> exposicoes do exposure_service
# ---------------------------------------------------------------------------


def _float(value: Any) -> Optional[float]:
    parsed = parse_decimal(value)
    return None if parsed is None else float(parsed)


def _raw_exposure(item: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Um item de historico (exercicio x sessao) no formato que
    `exposure_service.assemble_exposures` espera. So series `working` - a
    mesma selecao das exposicoes do dashboard. `None` quando nao sobra
    nenhuma serie de trabalho (aquecimento/tecnica nao sao exposicao)."""
    sets = [
        {
            "set_number": parse_int(row.get("set_number")),
            "reps": parse_int(row.get("reps")),
            "weight_kg": _float(row.get("weight_kg")),
            "rpe": _float(row.get("rpe")),
            "rest_seconds": None,
            "notes": None,
            # Lote T3 (medidas de serie): a exposicao tira as series fora de
            # `reps` dos agregados e fica nao comparavel (`exposure_service`).
            "measure": row.get("measure"),
        }
        for row in item.get("set_rows") or []
        if is_working_set(row)
    ]
    if not sets:
        return None
    return {
        "activity_id": item.get("activity_id"),
        "local_date": item.get("local_date"),
        "started_at": item.get("trained_at"),
        "position_in_session": None,
        "equipment_id": parse_int(item.get("equipment_id")),
        "equipment_name": None,
        "load_convention_raw": item.get("load_convention") or None,
        # M97 F1: objetivo na chave (troca de objetivo nao e queda de
        # desempenho: sem par comparavel, sem sinal). Ausente = nao lido.
        "training_goal": item.get("training_goal"),
        # O historico da recuperacao nao le a prescricao do plano: so a regra
        # de PROGRESSO "subiu carga com reps na faixa" depende dela, nunca a
        # de queda.
        "prescribed_reps_min": None,
        "prescribed_reps_max": None,
        "sets": sets,
    }


def _canonical_id(item: Mapping[str, Any]) -> Optional[int]:
    return parse_int(item.get("mapping_id"))


def _last_exposures_window(
    items: Iterable[Mapping[str, Any]],
    *,
    identity: Callable[[Mapping[str, Any]], Hashable],
    sort_key: Callable[[Mapping[str, Any]], datetime],
    thresholds: Mapping[str, Any],
) -> dict[Hashable, list[tuple[SessionKey, dict[str, Any]]]]:
    """Por exercicio (`identity`): as exposicoes da janela de
    `exposure_lookback_days` que TERMINA na ultima sessao do exercicio, em
    ordem cronologica, ja montadas por `assemble_exposures`.

    Montar so a janela (e nao o historico inteiro) e o que mantem a excecao
    do §8.1 igual a do dashboard: "todas sem equipamento" e decidido sobre as
    exposicoes da janela, como na leitura do `exposure_service`."""
    lookback = timedelta(days=int(thresholds["exposure_lookback_days"]))
    by_identity: dict[Hashable, list[tuple[datetime, Any, Any, Mapping[str, Any]]]] = defaultdict(list)
    for item in items:
        # M97 D: exposicao de deload e PULADA (carga reduzida de proposito
        # nao e queda de desempenho nem referencia), como no dashboard.
        if is_deload_session(item.get("deload_source")):
            continue
        by_identity[identity(item)].append((sort_key(item), item.get("activity_id"), item.get("exercise_id"), item))

    result: dict[Hashable, list[tuple[SessionKey, dict[str, Any]]]] = {}
    for key, entries in by_identity.items():
        entries.sort(key=lambda entry: (entry[0], _order(entry[1]), _order(entry[2])))
        last_at = entries[-1][0]
        window_start = last_at - lookback
        raws: list[dict[str, Any]] = []
        session_keys: list[SessionKey] = []
        for occurred_at, activity_id, exercise_id, item in entries:
            if occurred_at < window_start:
                continue
            raw = _raw_exposure(item)
            if raw is None:
                continue
            raws.append(raw)
            session_keys.append((exercise_id, activity_id))
        if not raws:
            continue
        assembled = assemble_exposures(raws, _canonical_id(entries[-1][3]))
        result[key] = list(zip(session_keys, assembled))
    return result


def _order(value: Any) -> tuple[int, Any]:
    """Desempate estavel para ids que podem vir `None`/str em dado legado."""
    parsed = parse_int(value)
    return (0, parsed) if parsed is not None else (1, str(value))


def latest_performance_ratios(
    items: Sequence[Mapping[str, Any]],
    *,
    identity: Callable[[Mapping[str, Any]], Hashable],
    sort_key: Callable[[Mapping[str, Any]], datetime],
    thresholds: Mapping[str, Any] = THRESHOLDS,
) -> dict[SessionKey, float]:
    """`performance_ratio` da ULTIMA sessao de cada exercicio, por
    `(exercise_id, activity_id)` do item de historico.

    So a ultima sessao importa: o overview le a razao da sessao MAIS RECENTE
    de cada grupo, e um exercicio PRIMARY de um grupo nao pode ter sessao
    posterior a mais recente do proprio grupo.

    A anterior e a ultima exposicao de OUTRA sessao (duas passagens pelo
    mesmo exercicio na mesma sessao medem fadiga intra-sessao, nao
    recuperacao). Sem anterior comparavel na janela -> sem entrada."""
    ratios: dict[SessionKey, float] = {}
    windows = _last_exposures_window(items, identity=identity, sort_key=sort_key, thresholds=thresholds)
    for exposures in windows.values():
        summaries = to_summaries([exposure for _, exposure in exposures])
        last_activity = exposures[-1][0][1]
        previous_index = next(
            (index for index in range(len(exposures) - 1, -1, -1) if exposures[index][0][1] != last_activity),
            None,
        )
        if previous_index is None:
            continue
        previous = summaries[previous_index]
        for index in range(previous_index + 1, len(exposures)):
            ratio = performance_ratio(previous, summaries[index], thresholds=thresholds)
            if ratio is not None:
                ratios[exposures[index][0]] = ratio
    return ratios


__all__ = [
    "e1rm_ratio_if_decisive",
    "latest_performance_ratios",
    "paired_reps_epley_ratio",
    "paired_reps_ratio",
    "performance_ratio",
]

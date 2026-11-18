"""Desempenho recente por exercicio - o sinal da recuperacao LIDO do dono
da progressao.

Este modulo so converte os itens de historico ja lidos pelo `VolumeService`
(um por exercicio de cada sessao, com `set_rows`, `equipment_id`, `gym_id`,
`equipment_type_code`, `load_convention` e `training_goal`) em exposicoes do
dono (`exposure_service.assemble_exposures` + `to_summaries`) e pede a razao
pronta a `dashboard/comparability.performance_ratio_of`: a anterior
comparavel, a janela do veredito, a calibracao de maquina e a magnitude
(e1RM decisivo ou equivalente Epley das reps emparelhadas, com piso/teto
1,0) sao do dono. A classificacao da razao (sinal leve/forte) e da
recuperacao (`MuscleRecoveryService`). Sem MySQL.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Hashable, Iterable, Mapping, Optional, Sequence

from ..domain.deload import is_deload_session
from ..domain.form_practice import FORM_PRACTICE_COLUMN, counts_for_progression
from ..domain.training_history import parse_decimal, parse_int
from ..set_types import is_working_set
from .dashboard.comparability import latest_session_performance_ratios
from .dashboard.exposure_service import assemble_exposures, to_summaries
from .dashboard.metrics_registry import THRESHOLDS

# Chave de sessao de um exercicio: (linha `exercises.id`, `activities.id`).
SessionKey = tuple[Any, Any]

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
        # Issue #188: academia e tipo do aparelho para o dono por a academia na
        # chave de maquina (`assemble_exposures` -> `context_gym_id`).
        "gym_id": parse_int(item.get("gym_id")),
        "equipment_type_code": item.get("equipment_type_code"),
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
        # #196 (ADR 0017): aprimoramento de tecnica tambem fica de fora.
        if not counts_for_progression(item.get(FORM_PRACTICE_COLUMN)):
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
    """Razao de desempenho da ULTIMA exposicao de cada exercicio, por
    `(exercise_id, activity_id)` do item de historico.

    A anterior (de OUTRA sessao), a sequencia comparavel, a calibracao e a
    razao sao do dono da progressao
    (`comparability.latest_session_performance_ratios`); este modulo so le e
    converte. Sem par -> sem entrada."""
    ratios: dict[SessionKey, float] = {}
    windows = _last_exposures_window(items, identity=identity, sort_key=sort_key, thresholds=thresholds)
    for exposures in windows.values():
        summaries = to_summaries([exposure for _, exposure in exposures])
        session_ids = [session_key[1] for session_key, _ in exposures]
        for index, ratio in latest_session_performance_ratios(summaries, session_ids, thresholds=thresholds).items():
            ratios[exposures[index][0]] = ratio
    return ratios


__all__ = ["latest_performance_ratios"]

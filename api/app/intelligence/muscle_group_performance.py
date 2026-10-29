"""Desempenho por GRUPO MUSCULAR em `/dashboard/progressao` - parte pura.

## Por que existe (2026-10-02)

O painel classificava cada grupo so pela TONELAGEM (volume). Com o Modelo novo
o doug reduziu o volume de proposito (de ~3-4,5 para ~2 series por exercicio)
enquanto ficava mais forte: o e1RM do mesmo exercicio subiu em 14 de 18 grupos
na janela de 28 dias, e a tela dizia "REGREDINDO" em 10. Queda de volume
planejada nao e regressao. Decisao do usuario: DUAS dimensoes separadas por
grupo - "Volume" (tonelagem, rotulos neutros, `muscle_group_progression`) e
"Desempenho" (este modulo).

## Metrica

Para cada exercicio presente nas DUAS janelas com a MESMA chave de
comparabilidade (`comparability.build_key`: exercicio canonico + equipamento +
convencao de carga), compara-se o MELHOR e1RM de cada janela. O e1RM e Epley
com RIR pelo dono (`domain/e1rm.reliable_e1rm`, politica
`MissingRpePolicy.RIR_ZERO`), por serie de trabalho com ate
`E1RM_MAX_RELIABLE_REPS` reps (`analytics._fetch_group_e1rm_rows`; ate o M76
Lote 3a a formula era reescrita em SQL). A variacao do grupo e a
media das variacoes dos exercicios ponderada pelo `stimulus_weight` do
exercicio naquele grupo - um supino pesa pouco no triceps, a extensao de
triceps pesa tudo.

Regras herdadas (nao negociaveis, ver `dashboard/comparability.py`):

- troca de maquina/convencao NAO e piora: chaves diferentes nao se comparam;
  equipamento ausente so compara quando TODAS as exposicoes do exercicio estao
  sem equipamento (excecao do §8.1, `all_equipment_missing`);
- `assisted` e `bodyweight` nao tem e1RM (`supports_e1rm`);
- so series de trabalho: aquecimento/ajuste ficam de fora, e tambem as
  aplicacoes de tecnica e o backoff - mini-blocos de rest-pause (5 reps no
  peso da serie) e drops nao medem forca maxima e so puxariam o e1RM para
  baixo de forma artificial.

Formula (decisao do usuario em 2026-10-06, meio-termo, substituindo a de
2026-10-02 so no teto de reps e no uso do RIR quando disponivel):
- RIR = max(0, 10 - RPE) quando a serie tem RPE (= `e1rm.estimated_1rm`);
- RIR = 0 quando a serie NAO tem RPE - as ~14% de series de trabalho sem RPE
  (medido em 2026-10-02) continuam contando, como antes;
- so series com ate 12 reps (`e1rm_max_reliable_reps`, o teto da regra unica):
  acima disso o Epley nao e confiavel.
A banda, as travas e a agregacao por grupo abaixo NAO mudaram.

Faixa de estabilidade: a MESMA do volume (`STABLE_BAND_PCT`, +-5%) - nenhum
limiar novo. Sem exercicio comparavel nas duas janelas: `DADOS_INSUFICIENTES`.

## Duas travas (aprovadas pelo usuario em 2026-10-02)

1. Variacao atipica (`OUTLIER_CHANGE_PCT`): exercicio cujo e1RM variou mais
   de +50% ou menos de -50% entre as janelas e DESCARTADO do calculo do
   grupo - quase sempre e registro errado (carga por lado x total, maquina
   trocada sem cadastro). Continua listado em `performance_exercises` com
   `outlier: true` / `excluded_reason: "OUTLIER_CHANGE"` para o usuario
   conferir. Caso real: remada articulada 46 -> 101 kg (+120%) inflava Costas
   (dorsal) para +30,5%. Exatamente +-50% ainda entra.
2. Ativacao minima (`MIN_ACTIVATION_SUM`): o grupo so e classificado quando a
   soma do `stimulus_weight` dos exercicios USADOS (sem os atipicos) e >= 0,5.
   Abaixo disso: `DADOS_INSUFICIENTES` com
   `performance_insufficient_reason: "LOW_ACTIVATION"`. Caso real: Adutores em
   7 dias com +100% vindo de um unico exercicio com 20% de ativacao.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .dashboard.comparability import ComparabilityKey, all_equipment_missing, build_key, keys_match
from ..load_conventions import normalize_load_convention, supports_e1rm
from .muscle_group_progression import (
    DADOS_INSUFICIENTES,
    ESTAVEL,
    PROGREDINDO,
    REGREDINDO,
    STABLE_BAND_PCT,
)

# Base da variacao de desempenho (ecoada no payload para o frontend explicar).
PERFORMANCE_BASIS = "best_e1rm_epley_same_exercise"

# Variacao de e1RM (em %, absoluta) acima da qual o exercicio e descartado do
# calculo do grupo como atipico ("conferir registro"). Estritamente maior:
# +-50% exatos ainda contam.
OUTLIER_CHANGE_PCT = 50.0
OUTLIER_CHANGE = "OUTLIER_CHANGE"

# Soma minima do `stimulus_weight` dos exercicios usados para o grupo receber
# PROGREDINDO/ESTAVEL/REGREDINDO. Abaixo: DADOS_INSUFICIENTES.
MIN_ACTIVATION_SUM = 0.5

# Motivos de `performance_insufficient_reason` (None quando ha classificacao).
NO_COMPARABLE_EXERCISE = "NO_COMPARABLE_EXERCISE"
ALL_OUTLIERS = "ALL_OUTLIERS"
LOW_ACTIVATION = "LOW_ACTIVATION"


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status(change_pct: float) -> str:
    if change_pct >= STABLE_BAND_PCT:
        return PROGREDINDO
    if change_pct <= -STABLE_BAND_PCT:
        return REGREDINDO
    return ESTAVEL


def insufficient() -> Dict[str, Any]:
    """Leitura de um grupo sem exercicio comparavel nas duas janelas."""
    return {
        "performance_status": DADOS_INSUFICIENTES,
        "performance_change_pct": None,
        "performance_exercises_compared": 0,
        "performance_exercises_excluded": 0,
        "performance_activation_sum": 0.0,
        "performance_insufficient_reason": NO_COMPARABLE_EXERCISE,
        "performance_exercises": [],
    }


def _is_outlier(change_pct: float) -> bool:
    return abs(change_pct) > OUTLIER_CHANGE_PCT


def build_group_performance(rows: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """`rows`: uma linha por (periodo, exercicio canonico, equipamento,
    convencao bruta, grupo) com `period` ('current'|'previous'),
    `exercise_id`, `exercise_name`, `equipment_id`, `load_convention` (bruta;
    `None` = sem cadastro = `total`), `muscle_group_id`, `stimulus_weight` e
    `best_e1rm_kg`. Devolve `{muscle_group_id: leitura}`; grupo ausente do
    dicionario = sem exercicio comparavel (quem monta usa `insufficient()`)."""
    rows = list(rows)

    # Excecao do §8.1: decidida por EXERCICIO, olhando as duas janelas juntas.
    equipment_by_exercise: Dict[int, List[Optional[int]]] = {}
    for row in rows:
        if row.get("exercise_id") is None:
            continue
        equipment_by_exercise.setdefault(int(row["exercise_id"]), []).append(
            None if row.get("equipment_id") is None else int(row["equipment_id"])
        )
    agnostic = {ex: all_equipment_missing(ids) for ex, ids in equipment_by_exercise.items()}

    # (grupo, chave) -> melhor e1RM por janela + peso + nome.
    pairs: Dict[Tuple[int, ComparabilityKey], Dict[str, Any]] = {}
    for row in rows:
        best = _float(row.get("best_e1rm_kg"))
        weight = _float(row.get("stimulus_weight")) or 0.0
        if row.get("exercise_id") is None or best is None or best <= 0 or weight <= 0:
            continue
        convention = normalize_load_convention(row.get("load_convention"))
        if not supports_e1rm(convention):
            continue
        exercise_id = int(row["exercise_id"])
        key = build_key(exercise_id, row.get("equipment_id"), convention, equipment_agnostic=agnostic[exercise_id])
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

    by_group: Dict[int, List[Dict[str, Any]]] = {}
    for (group_id, key), entry in pairs.items():
        if entry["current"] is None or entry["previous"] is None:
            continue
        change = (entry["current"] - entry["previous"]) / entry["previous"] * 100.0
        by_group.setdefault(group_id, []).append(
            {
                "exercise_id": key.canonical_exercise_id,
                "exercise_name": entry["exercise_name"],
                "equipment_id": key.equipment_id,
                "load_convention": key.load_convention.value,
                "previous_e1rm_kg": round(entry["previous"], 1),
                "current_e1rm_kg": round(entry["current"], 1),
                "change_pct": round(change, 1),
                "stimulus_weight": round(entry["weight"], 3),
                "outlier": _is_outlier(change),
                "excluded_reason": OUTLIER_CHANGE if _is_outlier(change) else None,
                "_change": change,
            }
        )

    result: Dict[int, Dict[str, Any]] = {}
    for group_id, exercises in by_group.items():
        used = [item for item in exercises if not item["outlier"]]
        # Arredonda para nao perder 0,2 + 0,3 = 0,49999... no limiar.
        total_weight = sum(item["stimulus_weight"] for item in used)
        activation = round(total_weight, 6)
        if not used:
            status, change_pct, reason = DADOS_INSUFICIENTES, None, ALL_OUTLIERS
        elif activation < MIN_ACTIVATION_SUM:
            status, change_pct, reason = DADOS_INSUFICIENTES, None, LOW_ACTIVATION
        else:
            weighted = sum(item["_change"] * item["stimulus_weight"] for item in used) / total_weight
            status, change_pct, reason = _status(weighted), round(weighted, 1), None
        exercises.sort(key=lambda i: (i["outlier"], -i["stimulus_weight"], i["exercise_name"] or ""))
        for item in exercises:
            del item["_change"]
        result[group_id] = {
            "performance_status": status,
            "performance_change_pct": change_pct,
            "performance_exercises_compared": len(used),
            "performance_exercises_excluded": len(exercises) - len(used),
            "performance_activation_sum": round(activation, 3),
            "performance_insufficient_reason": reason,
            "performance_exercises": exercises,
        }
    return result

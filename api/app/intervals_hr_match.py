"""Associacao entre um treino REALIZADO (`activities`) e a atividade
`WeightTraining` registrada no Intervals.icu (`intervals_activities`), pelo
HORARIO DE INICIO das duas.

Este modulo e PURO: nao conhece banco, nao faz rede, nao le variavel de
ambiente no meio do calculo. Recebe o horario do treino e a lista de
candidatos ja materializada, e devolve a decisao. Toda a persistencia fica em
`app/intervals_sync.py`, e o schema em
`api/db/migrations/041_intervals_hr_link.py`.

Regras de produto (decididas nesta entrega, documentadas no PR):

1. **Candidato elegivel**: linha de `intervals_activities` do MESMO usuario
   cujo `sport_type` normalizado e exatamente `weighttraining` (o tipo que o
   usuario pediu) e que tem `start_time` preenchido. Nada de casar por dia:
   dois treinos no mesmo dia precisam ser distinguidos.

2. **Janela de tolerancia**: `|activities.started_at - intervals.start_time|`
   <= `DEFAULT_TOLERANCE_SECONDS` (60 minutos). Os dois horarios sao LOCAIS e
   naive nos dois lados (`start_date_local` do Intervals; `started_at` do
   treino), entao a comparacao e direta. 60 min cobre o caso real de comecar o
   relogio antes do aquecimento ou lembrar de liga-lo depois da primeira serie,
   sem alcancar a sessao seguinte do mesmo dia.

3. **Mais de um candidato na janela** -> `MATCHED_AMBIGUOUS`: o treino E
   associado ao candidato de MENOR diferenca (desempate deterministico por
   `start_time` e depois `external_id`, para que duas execucoes escolham
   sempre o mesmo), e o numero de candidatos fica gravado em
   `candidates_count`. Ambiguidade e sinal de qualidade de dado, nao erro: o
   dado de FC ainda e util e a linha fica marcada para inspecao.

4. **Nenhum candidato na janela** -> `NO_CANDIDATE`: NENHUMA linha e gravada
   nem removida. O treino simplesmente segue sem FC do Intervals, e a proxima
   execucao tenta de novo (o usuario pode ter sincronizado o relogio depois).

5. **Treino sem `started_at`** (linhas legadas) -> `NO_START_TIME`: mesmo
   tratamento do item 4. Casar so por data produziria associacao errada no dia
   com dois treinos, e e exatamente o que esta entrega veio corrigir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional, Sequence

# Janela de tolerancia default, em segundos (60 minutos - ver regra 2).
DEFAULT_TOLERANCE_SECONDS = 3600
TOLERANCE_ENV = "INTERVALS_HR_MATCH_TOLERANCE_MINUTES"

# `sport_type` aceito, ja normalizado (minusculo, sem espaco/-/_).
WEIGHT_TRAINING_SPORT_TYPE = "weighttraining"

STATUS_MATCHED = "MATCHED"
STATUS_MATCHED_AMBIGUOUS = "MATCHED_AMBIGUOUS"
STATUS_NO_CANDIDATE = "NO_CANDIDATE"
STATUS_NO_START_TIME = "NO_START_TIME"

# Status que geram linha em `activity_intervals_link`.
PERSISTED_STATUSES = (STATUS_MATCHED, STATUS_MATCHED_AMBIGUOUS)


def tolerance_seconds(env: Optional[dict] = None) -> int:
    """Janela em segundos. `INTERVALS_HR_MATCH_TOLERANCE_MINUTES` permite
    ajustar sem deploy de codigo; valor invalido ou <= 0 cai no default (a
    associacao nunca fica sem janela por causa de config errada)."""
    raw = (env if env is not None else os.environ).get(TOLERANCE_ENV)
    try:
        minutes = int(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_TOLERANCE_SECONDS
    return minutes * 60 if minutes > 0 else DEFAULT_TOLERANCE_SECONDS


def normalize_sport_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    for char in (" ", "-", "_"):
        text = text.replace(char, "")
    return text


def is_weight_training(value: Any) -> bool:
    return normalize_sport_type(value) == WEIGHT_TRAINING_SPORT_TYPE


def parse_datetime(value: Any) -> Optional[datetime]:
    """Aceita o que os dois backends de banco devolvem (`datetime` no PyMySQL,
    string ISO no shim da Data API). `date` puro NAO vira meia-noite: sem hora
    nao ha casamento por horario (regra 5)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return None
    text = str(value).strip().replace("Z", "")
    if "T" in text:
        text = text.replace("T", " ")
    if len(text) > 19:
        text = text[:19]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class IntervalsCandidate:
    """Uma linha de `intervals_activities` ja normalizada."""

    intervals_activity_id: int
    external_id: str
    start_time: Optional[datetime]
    sport_type: Optional[str] = None
    moving_time_seconds: Optional[int] = None
    avg_hr_bpm: Optional[float] = None
    max_hr_bpm: Optional[float] = None
    calories_kcal: Optional[float] = None
    training_load: Optional[float] = None

    @classmethod
    def from_row(cls, row: dict) -> "IntervalsCandidate":
        return cls(
            intervals_activity_id=int(row["id"]),
            external_id=str(row.get("external_id") or ""),
            start_time=parse_datetime(row.get("start_time")),
            sport_type=row.get("sport_type"),
            moving_time_seconds=_as_int(row.get("moving_time_seconds")),
            avg_hr_bpm=_as_float(row.get("avg_hr_bpm")),
            max_hr_bpm=_as_float(row.get("max_hr_bpm")),
            calories_kcal=_as_float(row.get("calories_kcal")),
            training_load=_as_float(row.get("training_load")),
        )


@dataclass(frozen=True)
class MatchResult:
    status: str
    candidate: Optional[IntervalsCandidate] = None
    delta_seconds: Optional[int] = None
    candidates_count: int = 0

    @property
    def persisted(self) -> bool:
        return self.status in PERSISTED_STATUSES


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    parsed = _as_float(value)
    return None if parsed is None else int(parsed)


def _row_is_weight_training(row: dict) -> bool:
    return is_weight_training(row.get("sport_type"))


def eligible_candidates(
    rows: Iterable[dict], accept: Optional[Callable[[dict], bool]] = None
) -> list[IntervalsCandidate]:
    """Filtra `intervals_activities` por tipo e `start_time` preenchido
    (regra 1). Tipo default: `WeightTraining`. M98 C2: o casamento do cardio
    realizado com a sessão do dia usa ESTE mesmo critério de horário, só com
    outro tipo aceito (`accept`, que vem de `intervals_activity_kind`)."""
    accept = accept or _row_is_weight_training
    candidates = []
    for row in rows:
        if not accept(row):
            continue
        candidate = IntervalsCandidate.from_row(row)
        if candidate.start_time is None:
            continue
        candidates.append(candidate)
    return candidates


def _sort_key(candidate: IntervalsCandidate, started_at: datetime) -> tuple:
    """Desempate TOTAL e deterministico: menor diferenca, depois horario mais
    cedo, depois `external_id`. Sem isso, duas execucoes sobre o mesmo dado
    poderiam escolher candidatos diferentes e quebrar a idempotencia."""
    assert candidate.start_time is not None
    return (
        abs(int((candidate.start_time - started_at).total_seconds())),
        candidate.start_time,
        candidate.external_id,
    )


def match_session(
    started_at: Any,
    candidates: Sequence[IntervalsCandidate],
    *,
    tolerance: Optional[int] = None,
) -> MatchResult:
    """Decide a qual atividade `WeightTraining` este treino pertence.

    `candidates` deve vir de `eligible_candidates` (mesmo usuario, tipo certo,
    com horario)."""
    started = parse_datetime(started_at)
    if started is None:
        return MatchResult(STATUS_NO_START_TIME)

    window = tolerance_seconds() if tolerance is None else int(tolerance)
    in_window = [
        candidate
        for candidate in candidates
        if candidate.start_time is not None
        and abs(int((candidate.start_time - started).total_seconds())) <= window
    ]
    if not in_window:
        return MatchResult(STATUS_NO_CANDIDATE)

    in_window.sort(key=lambda candidate: _sort_key(candidate, started))
    best = in_window[0]
    delta = abs(int((best.start_time - started).total_seconds()))
    status = STATUS_MATCHED if len(in_window) == 1 else STATUS_MATCHED_AMBIGUOUS
    return MatchResult(status=status, candidate=best, delta_seconds=delta, candidates_count=len(in_window))


def _as_day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _day_sort_key(candidate: IntervalsCandidate) -> tuple:
    """Desempate TOTAL do casamento por dia: a atividade MAIS LONGA (o cardio
    do dia), depois a mais cedo, depois `external_id`."""
    assert candidate.start_time is not None
    return (-(candidate.moving_time_seconds or 0), candidate.start_time, candidate.external_id)


def match_local_day(day: Any, candidates: Sequence[IntervalsCandidate]) -> MatchResult:
    """Casamento por DIA LOCAL INTEIRO (M98 C4; ligado em 27/10/2026, decisão
    do usuário: consumidor `cardio_adherence_service`, para a ficha de
    descanso com cardio).

    Para o cardio prescrito num slot de DESCANSO: sem sessão de força não há
    horário-âncora para a regra 2, então a âncora é o dia local inteiro. Mora
    AQUI, no mesmo módulo do casamento por horário (nenhum terceiro critério
    fora dele):

    - candidatos: os de `eligible_candidates` (com `accept` de
      `intervals_activity_kind`) cujo `start_time` LOCAL cai em `day`;
    - um candidato -> `MATCHED`; mais de um -> `MATCHED_AMBIGUOUS` com o mais
      longo (`_day_sort_key`), e `candidates_count` registra quantos;
    - nenhum -> `NO_CANDIDATE`; dia ilegível -> `NO_START_TIME`.
      `delta_seconds` é sempre None (não há âncora de horário).

    Só vale para dia SEM sessão de força: no dia com sessão, a regra 2
    (`match_session`, âncora no fim da força) continua a única."""
    target = _as_day(day)
    if target is None:
        return MatchResult(STATUS_NO_START_TIME)
    same_day = [c for c in candidates if c.start_time is not None and c.start_time.date() == target]
    if not same_day:
        return MatchResult(STATUS_NO_CANDIDATE)
    same_day.sort(key=_day_sort_key)
    status = STATUS_MATCHED if len(same_day) == 1 else STATUS_MATCHED_AMBIGUOUS
    return MatchResult(status=status, candidate=same_day[0], delta_seconds=None, candidates_count=len(same_day))

"""Cardio do Novo Dashboard - `GET /analytics/dashboard/cardio`, a tela
`/dashboard/cardio` (plano §11, F2.3).

Duas perguntas, uma tela:

1. **"Quanto de cardio eu fiz?"** - sessoes, minutos, distancia, calorias,
   carga e FC das atividades de CARDIO, lidas de `intervals_activities` (o
   cache que o job agendado `musclefit-intervals-sync` mantem) e classificadas
   pelo modulo puro `app/intervals_activity_kind.py` - as MESMAS listas de
   padroes da tela `/cardio`, para as duas nunca divergirem.

2. **"Com que FC eu treinei forca?"** - a FC de cada SESSAO de treino, com a
   ORIGEM declarada (`hr_source`), porque ela tem duas e elas nao valem o
   mesmo:

   - `activity`: a FC gravada na propria sessao (`activities.avg_hr_bpm`,
     preenchida pelo import do treino vindo do relogio). E a MEDIDA daquele
     treino - nao depende de casamento nenhum, e por isso VENCE;
   - `intervals_link`: a FC da atividade `WeightTraining` que o job casou por
     HORARIO DE INICIO (janela de 60 minutos), lida de
     `activity_intervals_link` (migracao 041, M2). E uma ASSOCIACAO por
     proximidade, e vale quando a sessao nao trouxe FC propria.

   Aqui nada e recalculado: o casamento e do job (`app/intervals_sync.py` +
   `app/intervals_hr_match.py`), que ja gravou a FC media/maxima
   desnormalizada na linha do link exatamente para as telas lerem sem refazer
   o casamento. Refazer a associacao na leitura criaria uma SEGUNDA verdade
   sobre a mesma pergunta - e ela divergiria do que o job gravou assim que a
   janela de tolerancia mudasse.

**Cache, nao consulta ao vivo.** Este servico roda na Lambda `engines`, que
nao tem (nem deve ter) a chave do Intervals.icu do usuario: a tela `/cardio`
antiga chama a API externa a cada carregamento (`/integrations/intervals/
cardio-dashboard`), e por isso ela mostra o estado de agora e depende de a
API externa estar de pe. O hub le o cache e DECLARA a data da ultima
sincronizacao - dado velho declarado e melhor que dado novo instavel, e e a
unica forma de a tela responder em ordem de milissegundos.

**Limitacoes escolhidas pelo ESTADO** (mesmo padrao de
`insight_rules.context_limitation`): "sem Intervals configurado", "configurado
mas nada sincronizado", "sincronizado mas nada de cardio na janela" e "cardio
sem FC" sao quatro estados diferentes, e uma frase generica ("sem dados de
cardio") mentiria em tres deles. Limitacao que mente e pior do que nenhuma.

**Custo**: 4 consultas por chamada, todas indexadas e nenhuma por item -
perfil, atividades da janela (`user_id` + `activity_date`), sessoes da janela
com LEFT JOIN no link (`user_id` + `local_date`, e `uk_ail_activity` do lado
do link) e a data da ultima sincronizacao. Uma 5a (`LINKED_INTERVALS_IDS_SQL`,
M37) so e emitida quando a janela tem atividade de FORCA vinda do Intervals -
sem ela a diferenca e vazia por construcao. Nao ha N+1 possivel aqui: a
agregacao por modalidade e a diferenca de conjuntos do M37 sao feitas em
Python sobre as linhas ja lidas.

**M98 C4 - acompanhamento do cardio.** Mais seis blocos, todos prontos para
a tela (nenhuma regra no frontend): `weeks` (minutos por zona Z1-Z5 de
`hr_zones.app_zone_seconds` e continuo x intensa por
`cardio_envelope.is_high_intensity`, por semana), `adherence` (planejado x
realizado de `cardio_adherence_service`, status do dono `cardio_adherence`),
`wellness` (CTL/ATL/TSB, HRV, FC de repouso do cache `intervals_wellness`),
`pai` (PAI de 7 dias ESTIMADO do tempo em zona, motor `cardio_load` (M99), +
meta `cardio_policy.PAI_TARGET_MIN`), `next_session`
(ultima recomendacao `cardio-agent-v3`, `cardio_recommendation_v3.next_session`)
e `zones` (`athlete_hr_zones`, com a origem). Custo extra: ate 7 consultas
indexadas por `user_id` (aderencia 2, wellness 1, PAI 1, recomendacao 1, zonas
2; o fuso vem da rota), nenhuma por item. Cada bloco degrada sozinho
(`_optional_block`): falha vira ressalva, nunca 500. A tela `/cardio` e o
endpoint AO VIVO `/integrations/intervals/cardio-dashboard` sairam na C4: o
Intervals e lido so pelo cache.

**M37 - o lado do Intervals.** `strength_heart_rate` responde "quais das
minhas SESSOES ficaram sem FC"; `unmatched_strength_activities_total` responde
"quantas atividades WeightTraining que ja estao no Intervals nao viraram FC de
sessao nenhuma". A segunda aponta treino que nao foi registrado no app (ou
cujo horario ficou fora da tolerancia de 60 min) e nao aparece em lugar nenhum
do lado das sessoes. Ela e um SUBCONJUNTO de `strength_activities_total`, e
por isso herda o teto `MAX_CARDIO_SESSIONS`: numa janela truncada e um piso,
nao um total.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from ... import athlete_hr_zones, cardio_adherence_service, cardio_recommendation_v3, effort_estimates, intervals_store
from ...domain import cardio_adherence, cardio_envelope, cardio_load, cardio_policy, cardio_prescription, hr_zones
from ...intervals_activity_kind import CARDIO, STRENGTH, classify
from ...observability import log_event
from .coercion import to_date, to_datetime
from .contracts import DataNature, DataQuality, WindowInfo
from .identity import require_user_id
from .metrics_registry import metric_version
from .time_windows import user_today_for

logger = logging.getLogger(__name__)

# Tetos de linhas por janela. Existem pelo limite de 1 MB da resposta da Data
# API, nao por expectativa de volume: 365 dias de duas atividades por dia
# ainda cabem com folga.
MAX_CARDIO_SESSIONS = 200
MAX_STRENGTH_SESSIONS = 200

DRILLDOWN_HREF = "/dashboard/cardio"

# Teto de dias de wellness/PAI lidos (a janela mais longa do hub e 365 dias).
MAX_DAILY_POINTS = 400

METRIC_VERSIONS: Mapping[str, str] = {
    "cardio_sessions": metric_version("cardio_sessions"),
    "session_heart_rate": metric_version("session_heart_rate"),
    "cardio_weekly_zones": metric_version("cardio_weekly_zones"),
    "cardio_adherence": metric_version("cardio_adherence"),
}

# ---------------------------------------------------------------------------
# Limitacoes - uma frase por ESTADO, nunca uma frase generica
# ---------------------------------------------------------------------------

NOT_CONFIGURED_LIMITATION = (
    "O cardio do hub vem do Intervals.icu, que ainda não está configurado no seu perfil: "
    "nenhuma atividade de cardio e nenhuma FC de sessão podem ser lidas. Configure em "
    "/intervals-icu."
)

NEVER_SYNCED_LIMITATION = (
    "Intervals.icu está configurado, mas nenhuma atividade foi sincronizada ainda: a sincronização "
    "é agendada, e a primeira pode levar até algumas horas."
)

NO_CARDIO_IN_WINDOW_LIMITATION = (
    "Nenhuma atividade de cardio nesta janela: ausência de registro não é ausência de treino — só "
    "quer dizer que nada chegou do Intervals.icu neste período."
)

CACHE_LIMITATION_TEMPLATE = (
    "Os números vêm do CACHE do Intervals.icu (última sincronização em {quando}), não de uma "
    "consulta ao vivo: uma atividade registrada depois disso ainda não aparece aqui."
)

CLASSIFICATION_LIMITATION = (
    "A modalidade é classificada pelo tipo e pelo nome que vêm do Intervals.icu. O que não casa "
    "com cardio nem com força entra em \"outras\" e fica FORA dos totais — nada vira cardio por "
    "eliminação."
)

# Origem da FC de uma sessao. A ordem importa: a MEDIDA da propria sessao
# vence a ASSOCIADA por horario - a primeira e o que o relogio registrou
# naquele treino, a segunda e uma atividade vizinha no tempo.
HR_SOURCE_ACTIVITY = "activity"
HR_SOURCE_LINK = "intervals_link"

HR_FROM_ACTIVITY_LIMITATION_TEMPLATE = (
    "{n} sessão(ões) usam a FC MEDIDA na própria sessão (importada do relógio no registro do "
    "treino): essas não dependem do casamento com o Intervals.icu."
)

HR_MATCH_LIMITATION = (
    "A FC de uma sessão de força vem da atividade WeightTraining do Intervals.icu casada pelo "
    "HORÁRIO de início (tolerância de 60 minutos). Sessão sem horário de início registrado nunca "
    "casa, e a associação é por proximidade — não é uma medida da própria sessão."
)

NO_STRENGTH_SESSIONS_LIMITATION = (
    "Nenhuma sessão de treino registrada no MuscleFit nesta janela, então não há FC de sessão para mostrar."
)

AMBIGUOUS_LIMITATION_TEMPLATE = (
    "{n} sessão(ões) casaram com MAIS DE UMA atividade do Intervals.icu dentro da janela de "
    "tolerância: a FC mostrada é a da atividade mais próxima no tempo, e pode ser de outro treino."
)

# M37. Fala do lado do INTERVALS, nao do lado das sessoes: aqui sobrou
# atividade, la faltou FC. Nao e erro de calculo - e sinal de qualidade de
# dado (treino feito e nao registrado no app, ou horario de inicio fora da
# tolerancia de 60 minutos do casamento).
UNMATCHED_STRENGTH_LIMITATION_TEMPLATE = (
    "{n} de {total} atividade(s) de força (WeightTraining) do Intervals.icu nesta janela não "
    "casaram com nenhuma sessão registrada no MuscleFit: ou o treino não foi registrado aqui, ou o "
    "horário de início ficou fora da tolerância de 60 minutos do casamento."
)

CARDIO_WITHOUT_HR_LIMITATION_TEMPLATE = (
    "{n} de {total} atividades de cardio vieram SEM frequência cardíaca (relógio sem sensor, cinta "
    "desligada): elas contam nos minutos e na distância, mas ficam fora da FC média."
)


# --- M98 C4 ------------------------------------------------------------------

CARDIO_WITHOUT_ZONES_LIMITATION_TEMPLATE = (
    "{n} de {total} atividades de cardio vieram SEM tempo em zona do Intervals.icu: elas contam "
    "nos minutos da semana, mas ficam fora dos minutos por zona e sem classe (contínuo ou intensa)."
)

NO_ZONES_LIMITATION = (
    "Sem zonas de FC definidas (nem do Intervals.icu nem FCmáx no Perfil): o tempo em zona vem "
    "pronto do Intervals.icu, mas a faixa de bpm de cada zona não pode ser exibida."
)

NO_PLANNED_CARDIO_LIMITATION = (
    "Nenhuma sessão desta janela tinha cardio prescrito no Modelo: não há planejado × realizado "
    "para mostrar."
)

REST_DAY_ADHERENCE_LIMITATION = (
    "Cardio prescrito em dia de descanso só entra quando há ficha salva para a data: sem ficha, "
    "o dia não é avaliado."
)

BLOCK_UNAVAILABLE_TEMPLATE = "Não foi possível ler {bloco} agora; o restante da tela continua válido."

BLOCK_LABELS: Mapping[str, str] = {
    "adherence": "o planejado × realizado",
    "wellness": "a tendência de carga e recuperação",
    "pai": "o PAI",
    "next_session": "a próxima sessão recomendada",
    "zones": "as zonas de FC",
}


def hr_coverage_limitation(*, sessions_total: int, sessions_with_hr: int) -> Optional[str]:
    """A frase VERDADEIRA sobre a cobertura de FC das sessoes de forca.

    Tres estados, tres frases (e `None` quando nao ha ressalva a fazer):
    nenhuma sessao tem FC, parte tem, todas tem."""
    if sessions_total <= 0:
        return NO_STRENGTH_SESSIONS_LIMITATION
    if sessions_with_hr == 0:
        return (
            f"Nenhuma das {sessions_total} sessões de treino desta janela tem FC associada: o "
            "relógio precisa registrar a sessão como WeightTraining no Intervals.icu, no mesmo "
            "horário do treino."
        )
    if sessions_with_hr < sessions_total:
        return (
            f"{sessions_total - sessions_with_hr} de {sessions_total} sessões de treino estão "
            "sem FC associada: a média abaixo é só das que têm."
        )
    return None


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

INTERVALS_CONFIGURED_SQL = """
SELECT intervals_athlete_id, intervals_api_key_encrypted
FROM users
WHERE id = %s
"""

# Todas as atividades sincronizadas da janela. A CLASSIFICACAO e feita em
# Python (modulo puro, testavel sem banco) - um `LIKE` por padrao aqui seria a
# mesma heuristica escrita numa terceira linguagem.
INTERVALS_ACTIVITIES_SQL = """
SELECT id, external_id, activity_date, start_time, name, sport_type,
       moving_time_seconds, elapsed_time_seconds, distance_meters,
       calories_kcal, avg_hr_bpm, max_hr_bpm, training_load, intensity, hr_zone_seconds
FROM intervals_activities
WHERE user_id = %s AND activity_date BETWEEN %s AND %s
ORDER BY activity_date DESC, id DESC
LIMIT %s
"""

# Sessoes de treino da janela com as DUAS origens possiveis de FC (ver
# `normalize_session_hr_row`): a medida da propria sessao (`activities`,
# preenchida pelo import do relogio) e a associada pelo job
# (`activity_intervals_link`). LEFT JOIN: sessao sem link e o estado normal de
# quem nao usa relogio, nao um erro.
SESSION_HR_SQL = """
SELECT a.id AS activity_id, a.local_date, a.name, a.started_at, a.duration_seconds,
       a.avg_hr_bpm AS activity_avg_hr_bpm, a.max_hr_bpm AS activity_max_hr_bpm,
       l.avg_hr_bpm, l.max_hr_bpm, l.calories_kcal, l.training_load,
       l.moving_time_seconds, l.match_status, l.match_delta_seconds, l.candidates_count
FROM activities a
LEFT JOIN activity_intervals_link l ON l.activity_id = a.id AND l.user_id = a.user_id
WHERE a.user_id = %s AND a.local_date BETWEEN %s AND %s
ORDER BY a.local_date DESC, a.id DESC
LIMIT %s
"""

# M37 - o LADO DO INTERVALS do mesmo casamento. `SESSION_HR_SQL` responde
# "quais das minhas SESSOES ficaram sem FC"; esta responde "quais atividades
# WeightTraining que eu ja tenho no Intervals NAO viraram FC de sessao
# nenhuma". Sao perguntas diferentes: uma atividade sobrando aponta treino nao
# registrado no app (ou horario fora da tolerancia de 60 min), e isso nao
# aparece em lugar nenhum do lado das sessoes.
#
# So os IDS: a classificacao cardio/forca continua em Python (modulo puro),
# entao a diferenca e feita em memoria sobre as linhas que a janela ja leu -
# um `LIKE` de classificacao aqui seria a heuristica escrita numa terceira
# linguagem, exatamente o que `INTERVALS_ACTIVITIES_SQL` evita.
LINKED_INTERVALS_IDS_SQL = """
SELECT DISTINCT l.intervals_activity_id AS intervals_activity_id
FROM activity_intervals_link l
JOIN intervals_activities ia ON ia.id = l.intervals_activity_id
WHERE l.user_id = %s AND ia.activity_date BETWEEN %s AND %s
LIMIT %s
"""

# Frescor do cache. Fora da janela de proposito: "quando sincronizou pela
# ultima vez" nao e uma pergunta sobre o periodo escolhido.
LAST_SYNC_SQL = """
SELECT MAX(synced_at) AS last_synced_at
FROM intervals_activities
WHERE user_id = %s
"""

# M98 C4 - tendencia de carga e recuperacao (cache `intervals_wellness`, que
# tambem guarda a edicao manual de HRV/FC de repouso). Por dia, na janela.
WELLNESS_SQL = """
SELECT wellness_date, fitness_ctl, fatigue_atl, form_tsb, hrv_ms, resting_hr_bpm
FROM intervals_wellness
WHERE user_id = %s AND wellness_date BETWEEN %s AND %s
ORDER BY wellness_date ASC
LIMIT %s
"""

# Ultima recomendacao ESTRUTURADA (`cardio-agent-v3`, migracao 098). A
# recomendacao antiga (so markdown) nao tem sessao legivel por maquina.
LATEST_RECOMMENDATION_SQL = """
SELECT id, generated_at, prompt_version, recommendation_json
FROM cardio_recommendations
WHERE user_id = %s AND recommendation_json IS NOT NULL
ORDER BY generated_at DESC, id DESC
LIMIT 1
"""


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() or []


def _float(value: Any) -> Optional[float]:
    """`None` continua `None`. Ausencia NUNCA vira 0 (plano §8.4)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    parsed = _float(value)
    return None if parsed is None else int(parsed)


def _positive(value: Any) -> Optional[float]:
    """FC/carga/distancia so sao dado quando > 0: o Intervals.icu devolve 0
    para "nao medido" em varios campos, e somar esses zeros rebaixaria a
    media de quem de fato mediu."""
    parsed = _float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def _minutes(seconds: Any) -> Optional[int]:
    parsed = _int(seconds)
    return None if parsed is None else round(parsed / 60)


def normalize_intervals_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Uma linha de `intervals_activities` no formato da tela, com a classe
    ja decidida."""
    sport_type = row.get("sport_type")
    name = row.get("name")
    seconds = _int(row.get("moving_time_seconds")) or _int(row.get("elapsed_time_seconds"))
    distance = _positive(row.get("distance_meters"))
    # Tempo em zona Z1..Z5 do app: o mapeamento 7 -> 5 e do dono (`hr_zones`);
    # "intensa" e a regra do envelope (`cardio_envelope.is_high_intensity`).
    # Sem tempo em zona, a sessao fica sem classe (None), nunca "continua".
    zone_seconds = hr_zones.app_zone_seconds(intervals_store.json_list(row.get("hr_zone_seconds")))
    return {
        "intervals_activity_id": _int(row.get("id")),
        "external_id": str(row.get("external_id") or ""),
        "activity_date": to_date(row.get("activity_date")),
        "start_time": to_datetime(row.get("start_time")),
        "name": name,
        "sport_type": sport_type,
        "kind": classify(sport_type, name),
        "minutes": _minutes(seconds),
        "distance_km": None if distance is None else round(distance / 1000, 2),
        "calories_kcal": _positive(row.get("calories_kcal")),
        "avg_hr_bpm": _positive(row.get("avg_hr_bpm")),
        "max_hr_bpm": _positive(row.get("max_hr_bpm")),
        "training_load": _positive(row.get("training_load")),
        "zone_seconds": None if zone_seconds is None else list(zone_seconds),
        "zone_minutes": _zone_minutes(zone_seconds),
        "high_intensity": None if zone_seconds is None else cardio_envelope.is_high_intensity(zone_seconds),
    }


def _zone_minutes(zone_seconds: Optional[Sequence[int]]) -> Optional[dict[str, int]]:
    """`{"Z1": min, ..., "Z5": min}` (minutos inteiros do dono
    `cardio_prescription.minutes_from_seconds`); None sem tempo em zona."""
    if zone_seconds is None:
        return None
    return {
        hr_zones.zone_label(zone): cardio_prescription.minutes_from_seconds(seconds)
        for zone, seconds in zip(hr_zones.HR_ZONE_NUMBERS, zone_seconds)
    }


def summarize_weeks(
    sessions: Iterable[Mapping[str, Any]], *, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """Cardio por SEMANA (segunda a domingo, `cardio_adherence.week_start`),
    TODAS as semanas da janela (semana sem atividade = 0 sessões, observado).

    - `zone_minutes`: soma dos SEGUNDOS por zona das sessões com tempo em zona,
      convertida uma vez só (somar minutos já arredondados acumularia erro);
    - contínuo × intensa: a classe é a do envelope
      (`cardio_envelope.is_high_intensity`); sessão sem tempo em zona fica em
      `unclassified_*`, nunca é contada como contínua por eliminação;
    - `minutes`: a soma dos minutos das sessões (a mesma de `totals`)."""
    weeks: dict[date, dict[str, Any]] = {}
    start = cardio_adherence.week_start(date_from)
    while start <= date_to:
        weeks[start] = {
            "sessions": 0,
            "minutes": 0,
            "calories": [],
            "sessions_with_zones": 0,
            "zone_seconds": [0] * len(hr_zones.HR_ZONE_NUMBERS),
            "continuous_sessions": 0,
            "continuous_minutes": 0,
            "high_intensity_sessions": 0,
            "high_intensity_minutes": 0,
            "unclassified_sessions": 0,
            "unclassified_minutes": 0,
        }
        start += timedelta(days=7)

    for item in sessions:
        day = item.get("activity_date")
        if day is None:
            continue
        week = weeks.get(cardio_adherence.week_start(day))
        if week is None:
            continue
        minutes = int(item.get("minutes") or 0)
        week["sessions"] += 1
        week["minutes"] += minutes
        if item.get("calories_kcal") is not None:
            week["calories"].append(float(item["calories_kcal"]))
        zone_seconds = item.get("zone_seconds")
        if zone_seconds is not None:
            week["sessions_with_zones"] += 1
            week["zone_seconds"] = [a + int(b or 0) for a, b in zip(week["zone_seconds"], zone_seconds)]
        intense = item.get("high_intensity")
        prefix = "unclassified" if intense is None else ("high_intensity" if intense else "continuous")
        week[f"{prefix}_sessions"] += 1
        week[f"{prefix}_minutes"] += minutes

    out = []
    for start, week in sorted(weeks.items()):
        calories = week.pop("calories")
        zone_seconds = week.pop("zone_seconds")
        out.append(
            {
                "week_start": start,
                **week,
                "calories_kcal": round(sum(calories)) if calories else None,
                "zone_minutes": _zone_minutes(zone_seconds) if week["sessions_with_zones"] else None,
            }
        )
    return out


def summarize_totals(sessions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Totais da janela. Campo ausente nao entra na soma nem na media - e a
    razao de `avg_hr_bpm` poder ser `null` com `sessions > 0`."""
    minutes = [int(s["minutes"]) for s in sessions if s.get("minutes") is not None]
    distances = [float(s["distance_km"]) for s in sessions if s.get("distance_km") is not None]
    calories = [float(s["calories_kcal"]) for s in sessions if s.get("calories_kcal") is not None]
    loads = [float(s["training_load"]) for s in sessions if s.get("training_load") is not None]
    avg_hr = [float(s["avg_hr_bpm"]) for s in sessions if s.get("avg_hr_bpm") is not None]
    max_hr = [float(s["max_hr_bpm"]) for s in sessions if s.get("max_hr_bpm") is not None]
    return {
        "sessions": len(sessions),
        "sessions_with_hr": len(avg_hr),
        "minutes": sum(minutes) if minutes else 0,
        "distance_km": round(sum(distances), 2) if distances else None,
        "calories_kcal": round(sum(calories)) if calories else None,
        "training_load": round(sum(loads), 1) if loads else None,
        "avg_hr_bpm": _mean(avg_hr),
        "max_hr_bpm": max(max_hr) if max_hr else None,
    }


def summarize_by_sport(sessions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Agregado por modalidade, do maior volume de minutos para o menor
    (empate pelo nome, para a ordem ser total e reproduzivel)."""
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for item in sessions:
        buckets.setdefault(str(item.get("sport_type") or "Outro"), []).append(item)

    out: list[dict[str, Any]] = []
    for sport_type, items in buckets.items():
        totals = summarize_totals(items)
        out.append(
            {
                "sport_type": sport_type,
                "sessions": totals["sessions"],
                "minutes": totals["minutes"],
                "distance_km": totals["distance_km"],
                "calories_kcal": totals["calories_kcal"],
                "avg_hr_bpm": totals["avg_hr_bpm"],
                "max_hr_bpm": totals["max_hr_bpm"],
            }
        )
    out.sort(key=lambda item: (-int(item["minutes"] or 0), str(item["sport_type"])))
    return out


def normalize_session_hr_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Uma sessao de treino com (ou sem) FC, e com a ORIGEM dela declarada.

    Duas origens, e a ordem entre elas nao e arbitraria:

    1. `activity` - a FC gravada na propria sessao (`activities.avg_hr_bpm`,
       preenchida pelo import do treino vindo do relogio). E a MEDIDA daquele
       treino: nao depende de casamento nenhum, e por isso VENCE.
    2. `intervals_link` - a FC da atividade `WeightTraining` que o job casou
       por horario (`activity_intervals_link`). E uma ASSOCIACAO por
       proximidade; vale quando a sessao nao trouxe FC propria.

    Misturar as duas numa media sem dizer qual e qual seria apresentar medida
    e associacao como a mesma coisa - por isso `hr_source` e contrato.

    `has_heart_rate` e `avg_hr_bpm is not None`, nao "existe link": um link
    pode existir com FC nula (o relogio registrou a atividade mas nao mediu a
    FC), e dizer "tem FC" nesse caso seria falso."""
    atividade_avg = _positive(row.get("activity_avg_hr_bpm"))
    atividade_max = _positive(row.get("activity_max_hr_bpm"))
    link_avg = _positive(row.get("avg_hr_bpm"))
    link_max = _positive(row.get("max_hr_bpm"))

    if atividade_avg is not None or atividade_max is not None:
        hr_source, avg_hr, max_hr = HR_SOURCE_ACTIVITY, atividade_avg, atividade_max
    elif link_avg is not None or link_max is not None:
        hr_source, avg_hr, max_hr = HR_SOURCE_LINK, link_avg, link_max
    else:
        hr_source, avg_hr, max_hr = None, None, None

    match_status = row.get("match_status")
    return {
        "activity_id": _int(row.get("activity_id")),
        "local_date": to_date(row.get("local_date")),
        "name": row.get("name"),
        "started_at": to_datetime(row.get("started_at")),
        "minutes": _minutes(row.get("duration_seconds")) or _minutes(row.get("moving_time_seconds")),
        "avg_hr_bpm": avg_hr,
        "max_hr_bpm": max_hr,
        "hr_source": hr_source,
        "calories_kcal": _positive(row.get("calories_kcal")),
        "training_load": _positive(row.get("training_load")),
        "match_status": None if match_status is None else str(match_status),
        "match_delta_seconds": _int(row.get("match_delta_seconds")),
        "candidates_count": _int(row.get("candidates_count")),
        "has_heart_rate": avg_hr is not None,
    }


def summarize_session_hr(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Cobertura e FC media das sessoes de forca.

    `coverage_pct` e `null` (nao 0) quando nao houve sessao nenhuma: sem
    denominador nao ha percentual - dividir por zero e inventar."""
    with_hr = [float(i["avg_hr_bpm"]) for i in items if i.get("avg_hr_bpm") is not None]
    max_hr = [float(i["max_hr_bpm"]) for i in items if i.get("max_hr_bpm") is not None]
    ambiguous = sum(1 for i in items if i.get("match_status") == "MATCHED_AMBIGUOUS")
    # A ambiguidade so importa para quem DEPENDE do casamento: uma sessao com
    # FC propria nao fica menos confiavel porque o job achou dois candidatos.
    ambiguous_used = sum(
        1
        for i in items
        if i.get("match_status") == "MATCHED_AMBIGUOUS" and i.get("hr_source") == HR_SOURCE_LINK
    )
    total = len(items)
    return {
        "sessions_total": total,
        "sessions_with_hr": len(with_hr),
        "coverage_pct": round(100 * len(with_hr) / total, 1) if total else None,
        "ambiguous_total": ambiguous,
        "ambiguous_in_use_total": ambiguous_used,
        "from_activity_total": sum(
            1 for i in items if i.get("hr_source") == HR_SOURCE_ACTIVITY and i.get("has_heart_rate")
        ),
        "from_link_total": sum(
            1 for i in items if i.get("hr_source") == HR_SOURCE_LINK and i.get("has_heart_rate")
        ),
        "avg_hr_bpm": _mean(with_hr),
        "max_hr_bpm": max(max_hr) if max_hr else None,
        "items": list(items),
    }


def classify_quality(
    *,
    configured: bool,
    cardio_total: int,
    cardio_with_hr: int,
    strength_total: int,
    strength_with_hr: int,
    ambiguous_total: int,
) -> DataQuality:
    """Qualidade da RESPOSTA, em regra unica e declarada:

    - `INSUFFICIENT`: sem Intervals configurado, ou nada para mostrar dos dois
      lados (nem cardio na janela, nem FC de sessao);
    - `LOW`: ha casamento AMBIGUO - o numero existe, mas pode ser de outro
      treino, e essa duvida vale mais que a cobertura;
    - `MEDIUM`: algum lado incompleto (cardio sem FC, ou sessao sem FC);
    - `HIGH`: tudo que existe na janela tem FC.
    """
    if not configured:
        return DataQuality.INSUFFICIENT
    if cardio_total == 0 and strength_with_hr == 0:
        return DataQuality.INSUFFICIENT
    if ambiguous_total > 0:
        return DataQuality.LOW
    if cardio_with_hr < cardio_total or (strength_total and strength_with_hr < strength_total):
        return DataQuality.MEDIUM
    return DataQuality.HIGH


def _quando(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


# ---------------------------------------------------------------------------
# Leitura (I/O)
# ---------------------------------------------------------------------------


def load_intervals_configured(conn: Any, *, user_id: Any) -> bool:
    """O usuario tem Intervals.icu configurado? Mesma regra de
    `GET /integrations/intervals/status` (atleta E chave presentes). A chave
    NUNCA sai daqui - so o booleano."""
    rows = _rows(conn, INTERVALS_CONFIGURED_SQL, (require_user_id(user_id),))
    row = rows[0] if rows else {}
    return bool(row.get("intervals_athlete_id")) and bool(row.get("intervals_api_key_encrypted"))


def load_cardio_activities(
    conn: Any, *, user_id: Any, window: WindowInfo
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """`(cardio, forca, outras)` da janela, ja normalizadas e classificadas.

    As de FORCA sao devolvidas porque elas EXPLICAM a ausencia: "voce tem 12
    atividades no Intervals e nenhuma de cardio" e uma resposta diferente de
    "voce nao tem atividade nenhuma"."""
    owner = require_user_id(user_id)
    rows = _rows(
        conn,
        INTERVALS_ACTIVITIES_SQL,
        (owner, window.date_from, window.date_to, MAX_CARDIO_SESSIONS),
    )
    cardio: list[dict[str, Any]] = []
    strength: list[dict[str, Any]] = []
    other = 0
    for row in rows:
        item = normalize_intervals_row(row)
        if item["kind"] == CARDIO:
            cardio.append(item)
        elif item["kind"] == STRENGTH:
            strength.append(item)
        else:
            other += 1
    return cardio, strength, other


def load_session_heart_rate(conn: Any, *, user_id: Any, window: WindowInfo) -> list[dict[str, Any]]:
    """FC por sessao de treino, direto de `activity_intervals_link`."""
    owner = require_user_id(user_id)
    rows = _rows(
        conn, SESSION_HR_SQL, (owner, window.date_from, window.date_to, MAX_STRENGTH_SESSIONS)
    )
    return [normalize_session_hr_row(row) for row in rows]


def load_linked_intervals_ids(conn: Any, *, user_id: Any, window: WindowInfo) -> set[int]:
    """Ids de `intervals_activities` (da janela) que JA estao em
    `activity_intervals_link` (M37)."""
    owner = require_user_id(user_id)
    rows = _rows(
        conn,
        LINKED_INTERVALS_IDS_SQL,
        (owner, window.date_from, window.date_to, MAX_CARDIO_SESSIONS),
    )
    ids: set[int] = set()
    for row in rows:
        value = _int(row.get("intervals_activity_id"))
        if value is not None:
            ids.add(value)
    return ids


def count_unmatched_strength(
    activities: Sequence[Mapping[str, Any]], linked_ids: set[int]
) -> int:
    """Quantas atividades de forca do Intervals NAO viraram FC de sessao (M37).

    Funcao pura (testavel sem banco): a classificacao ja foi feita, aqui so
    sobra a diferenca de conjuntos. Atividade sem id nunca pode ser casada, e
    por isso conta como nao casada - assumir o contrario esconderia o caso."""
    return sum(
        1 for item in activities if _int(item.get("intervals_activity_id")) not in linked_ids
    )


def load_last_sync(conn: Any, *, user_id: Any) -> Optional[datetime]:
    rows = _rows(conn, LAST_SYNC_SQL, (require_user_id(user_id),))
    return to_datetime((rows[0] if rows else {}).get("last_synced_at"))


# --- M98 C4: blocos do acompanhamento ---------------------------------------


def _optional_block(name: str, fn: Callable[[], Any], default: Any, limitations: list[str]) -> Any:
    """Bloco acessorio (aderencia, tendencia, PAI, recomendacao, zonas): uma
    falha de leitura vira ressalva declarada, nunca 500 na tela inteira - o
    volume de cardio continua valendo sem eles."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - degrada o bloco, nao a tela
        logger.warning("dashboard cardio: bloco %s indisponivel", name, exc_info=True)
        limitations.append(BLOCK_UNAVAILABLE_TEMPLATE.format(bloco=BLOCK_LABELS.get(name, name)))
        return default


def _minutes_or_none(seconds: Any) -> Optional[int]:
    return None if seconds is None else cardio_prescription.minutes_from_seconds(seconds)


def adherence_view(data: Mapping[str, Any]) -> dict[str, Any]:
    """A aderencia do dono (`cardio_adherence_service.build_adherence`) com os
    rotulos e os minutos prontos para exibir. Nada e recalculado: status,
    percentuais e limiares vem do dono; aqui so se converte segundos em
    minutos (`cardio_prescription.minutes_from_seconds`) e se rotula."""
    sessions = []
    for session in data.get("sessions") or []:
        evaluation = session.get("adherence") or {}
        zones = evaluation.get("zones") or {}
        status = evaluation.get("status")
        realized = evaluation.get("realized") or {}
        sessions.append(
            {
                "activity_id": session.get("activity_id"),
                "workout_plan_id": session.get("workout_plan_id"),
                "anchor": session.get("anchor"),
                "local_date": session.get("local_date"),
                "mode": evaluation.get("mode"),
                "prescription_text": evaluation.get("prescription_text"),
                "planned_minutes": _minutes_or_none(evaluation.get("planned_seconds")),
                "realized_minutes": _minutes_or_none(evaluation.get("realized_seconds")),
                "time_completion_pct": evaluation.get("time_completion_pct"),
                "zone_completion_pct": zones.get(
                    "target_zone_completion_pct", zones.get("high_zone_completion_pct")
                ),
                "has_zone_data": bool(evaluation.get("has_zone_data")),
                "match_status": realized.get("match_status"),
                "status": status,
                "status_label": cardio_adherence.STATUS_LABELS.get(status, status),
            }
        )
    weeks = [
        {
            "week_start": week.get("week_start"),
            "planned_sessions": week.get("planned_sessions", 0),
            "done_sessions": week.get("done_sessions", 0),
            "planned_minutes": _minutes_or_none(week.get("planned_seconds")),
            "realized_minutes": _minutes_or_none(week.get("realized_seconds")),
            "time_completion_pct": week.get("time_completion_pct"),
            "by_status": dict(week.get("by_status") or {}),
        }
        for week in data.get("weeks") or []
    ]
    thresholds = data.get("thresholds") or {}
    return {
        # Ancora padrao (sessao de forca); a de cada sessao vem em
        # `sessions[].anchor` (`local_day` no dia de descanso, 27/10/2026).
        "anchor": cardio_adherence.ANCHOR_STRENGTH_END,
        "sessions": sessions,
        "weeks": weeks,
        "thresholds": dict(thresholds),
        "status_labels": dict(cardio_adherence.STATUS_LABELS),
    }


def load_adherence(conn: Any, *, user_id: Any, window: WindowInfo, today: Optional[date] = None) -> dict[str, Any]:
    """`today` = dia local do usuario; ausente, o fim da janela (as janelas
    terminam hoje) - sem statement extra para ler o fuso."""
    owner = require_user_id(user_id)
    with conn.cursor() as cursor:
        data = cardio_adherence_service.build_adherence(
            cursor, owner, window.date_from, window.date_to, today=today
        )
    return adherence_view(data)


def summarize_wellness(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """CTL/ATL/TSB, HRV e FC de repouso por dia, como gravados no cache. TSB
    ausente vem do dono do cache (`intervals_store.derive_form_tsb`). `latest`
    = o dia mais recente com cada valor (os valores chegam em dias diferentes)."""
    points = []
    for row in rows:
        day = to_date(row.get("wellness_date"))
        if day is None:
            continue
        ctl = _float(row.get("fitness_ctl"))
        atl = _float(row.get("fatigue_atl"))
        points.append(
            {
                "date": day,
                "ctl": ctl,
                "atl": atl,
                "tsb": intervals_store.derive_form_tsb(ctl, atl, _float(row.get("form_tsb"))),
                "hrv_ms": _positive(row.get("hrv_ms")),
                "resting_hr_bpm": _positive(row.get("resting_hr_bpm")),
            }
        )
    latest: dict[str, Any] = {}
    for key in ("ctl", "atl", "tsb", "hrv_ms", "resting_hr_bpm"):
        found = next((p for p in reversed(points) if p[key] is not None), None)
        latest[key] = None if found is None else found[key]
        latest[f"{key}_date"] = None if found is None else found["date"]
    return {"points": points, "latest": latest}


def load_wellness(conn: Any, *, user_id: Any, window: WindowInfo) -> dict[str, Any]:
    owner = require_user_id(user_id)
    rows = _rows(conn, WELLNESS_SQL, (owner, window.date_from, window.date_to, MAX_DAILY_POINTS))
    return summarize_wellness(rows)


def summarize_pai(estimates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """PAI de 7 dias ESTIMADO por dia (M99: `cardio_load`, via
    `effort_estimates`) e a meta do dono (`cardio_policy.PAI_TARGET_MIN`, que
    tambem e de 7 dias). `latest_below_target` compara o ULTIMO dia com valor
    com a meta; sem valor = None (nao "abaixo")."""
    points = [
        {"date": day, "pai": value}
        for row in estimates
        if (day := to_date(row.get("local_date"))) is not None
        and (value := _float(row.get("pai_7d_estimated"))) is not None
    ][-MAX_DAILY_POINTS:]
    latest = points[-1] if points else None
    return {
        "points": points,
        "target_min": cardio_policy.PAI_TARGET_MIN,
        "latest": None if latest is None else latest["pai"],
        "latest_date": None if latest is None else latest["date"],
        "latest_below_target": None if latest is None else latest["pai"] < cardio_policy.PAI_TARGET_MIN,
        "estimated": True,
        "model_version": cardio_load.CARDIO_LOAD_MODEL_VERSION,
    }


def load_pai(conn: Any, *, user_id: Any, window: WindowInfo) -> dict[str, Any]:
    owner = require_user_id(user_id)
    with conn.cursor() as cursor:
        estimates = effort_estimates.load_estimates(
            effort_estimates.cursor_fetch(cursor), owner, window.date_from, window.date_to
        )
    return summarize_pai([e.as_dict() for e in estimates])


def _json_object(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def load_next_session(conn: Any, *, user_id: Any, today: date) -> Optional[dict[str, Any]]:
    """Proxima sessao da ULTIMA recomendacao estruturada, se ainda valida
    (`cardio_recommendation_v3.next_session`: data >= hoje local). Uma
    recomendacao nova substitui a anterior inteira: sessao futura de uma
    recomendacao antiga nao reaparece quando a nova nao tem sessao futura."""
    rows = _rows(conn, LATEST_RECOMMENDATION_SQL, (require_user_id(user_id),))
    if not rows:
        return None
    row = rows[0]
    session = cardio_recommendation_v3.next_session(_json_object(row.get("recommendation_json")), today)
    if session is None:
        return None
    return {
        "recommendation_id": _int(row.get("id")),
        "generated_at": to_datetime(row.get("generated_at")),
        "prompt_version": row.get("prompt_version"),
        **session,
    }


def load_zones(conn: Any, *, user_id: Any) -> dict[str, Any]:
    """Zonas em uso e a origem (`athlete_hr_zones` -> dono `hr_zones`)."""
    owner = require_user_id(user_id)
    with conn.cursor() as cursor:
        return athlete_hr_zones.zones_payload(athlete_hr_zones.load_athlete_zones(cursor, owner))


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def build_cardio(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    today: Optional[date] = None,
    tracking: bool = True,
) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/cardio`.

    Sempre 200. Sem Intervals configurado a resposta EXISTE, vazia, com
    `INSUFFICIENT` e a frase que diz o que fazer - nunca um erro e nunca
    zeros que pareçam medida.

    `today` = dia local do usuario (a rota ja resolveu o fuso); ausente, e
    lido do perfil (`time_windows.user_today_for`). `tracking=False` (o
    relatorio em Markdown, que so le volume e FC) pula os blocos da C4 e as
    consultas deles: a resposta sai com os blocos vazios."""
    owner = require_user_id(user_id)
    limitations: list[str] = []

    configured = load_intervals_configured(conn, user_id=owner)

    if configured:
        cardio, strength_from_intervals, other_total = load_cardio_activities(
            conn, user_id=owner, window=window
        )
        last_synced_at = load_last_sync(conn, user_id=owner)
        # M37: a 5a consulta so existe quando ha atividade de forca do
        # Intervals na janela - sem ela a diferenca e vazia por construcao, e
        # pagar um statement para confirmar isso seria desperdicio.
        if strength_from_intervals:
            unmatched_strength_total = count_unmatched_strength(
                strength_from_intervals,
                load_linked_intervals_ids(conn, user_id=owner, window=window),
            )
        else:
            unmatched_strength_total = 0
    else:
        # Sem credencial nao existe atividade sincronizada: pular as duas
        # consultas e a resposta honesta E a mais barata.
        cardio, strength_from_intervals, other_total = [], [], 0
        last_synced_at = None
        unmatched_strength_total = 0
        limitations.append(NOT_CONFIGURED_LIMITATION)

    # A FC por sessao NAO depende de o Intervals estar configurado AGORA: o
    # link e historico, e continua valido se a chave for revogada depois.
    sessions = load_session_heart_rate(conn, user_id=owner, window=window)
    session_hr = summarize_session_hr(sessions)

    totals = summarize_totals(cardio)
    by_sport = summarize_by_sport(cardio)
    weeks = (
        summarize_weeks(cardio, date_from=window.date_from, date_to=window.date_to)
        if configured and tracking
        else []
    )

    # M98 C4 - blocos do acompanhamento. Aderencia so com o Intervals
    # configurado: sem o realizado, todo planejado viraria "nao feito" e
    # mentiria. Tendencia, PAI, zonas e recomendacao valem sem a chave (cache
    # historico do Intervals, FCmax do Perfil).
    adherence: Optional[dict[str, Any]] = None
    wellness = summarize_wellness([])
    pai = summarize_pai([])
    next_session: Optional[dict[str, Any]] = None
    zones: Optional[dict[str, Any]] = None
    if tracking:
        if configured:
            adherence = _optional_block(
                "adherence",
                lambda: load_adherence(conn, user_id=owner, window=window, today=today),
                None,
                limitations,
            )
        wellness = _optional_block(
            "wellness", lambda: load_wellness(conn, user_id=owner, window=window), wellness, limitations
        )
        pai = _optional_block("pai", lambda: load_pai(conn, user_id=owner, window=window), pai, limitations)
        next_session = _optional_block(
            "next_session",
            lambda: load_next_session(conn, user_id=owner, today=today or user_today_for(conn, owner)),
            None,
            limitations,
        )
        zones = _optional_block("zones", lambda: load_zones(conn, user_id=owner), None, limitations)

    if configured:
        if last_synced_at is None:
            limitations.append(NEVER_SYNCED_LIMITATION)
        else:
            limitations.append(CACHE_LIMITATION_TEMPLATE.format(quando=_quando(last_synced_at)))
            if not cardio:
                limitations.append(NO_CARDIO_IN_WINDOW_LIMITATION)
        if cardio:
            sem_fc = totals["sessions"] - totals["sessions_with_hr"]
            if sem_fc:
                limitations.append(
                    CARDIO_WITHOUT_HR_LIMITATION_TEMPLATE.format(n=sem_fc, total=totals["sessions"])
                )
            sem_zona = sum(1 for item in cardio if item.get("zone_seconds") is None)
            if sem_zona and tracking:
                limitations.append(
                    CARDIO_WITHOUT_ZONES_LIMITATION_TEMPLATE.format(n=sem_zona, total=totals["sessions"])
                )
            limitations.append(CLASSIFICATION_LIMITATION)
        if adherence is not None:
            if adherence["sessions"]:
                limitations.append(REST_DAY_ADHERENCE_LIMITATION)
            else:
                limitations.append(NO_PLANNED_CARDIO_LIMITATION)
        # M37: fora do `if cardio` de proposito - a sobra de atividade de
        # forca existe mesmo numa janela sem nenhum cardio, e e justamente ai
        # que ela explica a tela vazia.
        if unmatched_strength_total:
            limitations.append(
                UNMATCHED_STRENGTH_LIMITATION_TEMPLATE.format(
                    n=unmatched_strength_total, total=len(strength_from_intervals)
                )
            )

    cobertura = hr_coverage_limitation(
        sessions_total=session_hr["sessions_total"],
        sessions_with_hr=session_hr["sessions_with_hr"],
    )
    if cobertura:
        limitations.append(cobertura)
    if session_hr["ambiguous_in_use_total"]:
        limitations.append(
            AMBIGUOUS_LIMITATION_TEMPLATE.format(n=session_hr["ambiguous_in_use_total"])
        )
    if session_hr["from_activity_total"]:
        limitations.append(
            HR_FROM_ACTIVITY_LIMITATION_TEMPLATE.format(n=session_hr["from_activity_total"])
        )
    # A ressalva do casamento so vale para quem DEPENDE dele: declara-la numa
    # tela em que toda FC e medida na propria sessao seria ruido - e ruido que
    # parece ressalva ensina a ignorar ressalva.
    if session_hr["from_link_total"]:
        limitations.append(HR_MATCH_LIMITATION)
    if zones is not None and not zones.get("zones"):
        limitations.append(NO_ZONES_LIMITATION)

    quality = classify_quality(
        configured=configured,
        cardio_total=totals["sessions"],
        cardio_with_hr=totals["sessions_with_hr"],
        strength_total=session_hr["sessions_total"],
        strength_with_hr=session_hr["sessions_with_hr"],
        ambiguous_total=session_hr["ambiguous_in_use_total"],
    )

    log_event(
        "dashboard.computed",
        screen="cardio",
        user_id=owner,
        window=window.kind.value,
        configured=configured,
        cardio_sessions=totals["sessions"],
        strength_sessions=session_hr["sessions_total"],
        strength_sessions_with_hr=session_hr["sessions_with_hr"],
        ambiguous=session_hr["ambiguous_total"],
        unmatched_strength=unmatched_strength_total,
    )

    return {
        "window": window,
        "metric_versions": dict(METRIC_VERSIONS),
        "data_quality": quality,
        "limitations": list(dict.fromkeys(limitations)),
        "nature": DataNature.OBSERVED,
        "intervals_configured": configured,
        "last_synced_at": last_synced_at,
        "totals": totals,
        "by_sport": by_sport,
        "sessions": cardio,
        "strength_activities_total": len(strength_from_intervals),
        # M37: subconjunto de `strength_activities_total` - nunca maior.
        "unmatched_strength_activities_total": unmatched_strength_total,
        "other_activities_total": other_total,
        "strength_heart_rate": session_hr,
        # M98 C4
        "weeks": weeks,
        "adherence": adherence,
        "wellness": wellness,
        "pai": pai,
        "next_session": next_session,
        "zones": zones,
    }


__all__ = [
    "AMBIGUOUS_LIMITATION_TEMPLATE",
    "HR_FROM_ACTIVITY_LIMITATION_TEMPLATE",
    "HR_SOURCE_ACTIVITY",
    "HR_SOURCE_LINK",
    "CACHE_LIMITATION_TEMPLATE",
    "CARDIO_WITHOUT_HR_LIMITATION_TEMPLATE",
    "CLASSIFICATION_LIMITATION",
    "DRILLDOWN_HREF",
    "HR_MATCH_LIMITATION",
    "MAX_CARDIO_SESSIONS",
    "MAX_STRENGTH_SESSIONS",
    "METRIC_VERSIONS",
    "NEVER_SYNCED_LIMITATION",
    "NOT_CONFIGURED_LIMITATION",
    "NO_CARDIO_IN_WINDOW_LIMITATION",
    "NO_STRENGTH_SESSIONS_LIMITATION",
    "UNMATCHED_STRENGTH_LIMITATION_TEMPLATE",
    "BLOCK_LABELS",
    "BLOCK_UNAVAILABLE_TEMPLATE",
    "CARDIO_WITHOUT_ZONES_LIMITATION_TEMPLATE",
    "LATEST_RECOMMENDATION_SQL",
    "NO_PLANNED_CARDIO_LIMITATION",
    "NO_ZONES_LIMITATION",
    "REST_DAY_ADHERENCE_LIMITATION",
    "WELLNESS_SQL",
    "adherence_view",
    "build_cardio",
    "classify_quality",
    "load_adherence",
    "load_next_session",
    "load_pai",
    "load_wellness",
    "load_zones",
    "summarize_pai",
    "summarize_wellness",
    "summarize_weeks",
    "count_unmatched_strength",
    "hr_coverage_limitation",
    "load_cardio_activities",
    "load_intervals_configured",
    "load_last_sync",
    "load_linked_intervals_ids",
    "load_session_heart_rate",
    "normalize_intervals_row",
    "normalize_session_hr_row",
    "summarize_by_sport",
    "summarize_session_hr",
    "summarize_totals",
]

"""Progressao por GRUPO MUSCULAR (backlog M7, revisto no M50) - parte pura.

Metrica (decisao do usuario, 2026-09-20): tonelagem (reps x carga) das series
`working`, ponderada por `exercise_muscle_contributions.stimulus_weight` e
somada por grupo. Desde a Fase 5 as aplicacoes de tecnica (rest-pause, drop,
reps forcadas) e o backoff tambem entram - as mesmas series do volume por
grupo; so aquecimento/ajuste fica de fora (ver `_fetch_group_tonnage_rows`).
Desde o M58, series de aparelho `assisted`/`bodyweight` ficam fora da
tonelagem (mesma regra do relatorio Markdown) e sao contadas em
`*_tonnage_excluded_sets`. Janela: os `days` dias terminando hoje (no fuso do usuario)
contra os `days` dias imediatamente anteriores. A fase da trilha e informacao
secundaria e NAO define a classificacao.

## M50 - comparacao proporcional (2026-09-30)

Ate o M50 a classificacao comparava a tonelagem TOTAL das duas janelas. Total
mistura progressao com frequencia: a janela atual termina HOJE, que ainda nao
acabou (o treino do dia pode nao ter acontecido), e o corte de `days` dias
parte o ciclo do Modelo no meio - um grupo tem uma sessao a menos numa janela
so porque a borda caiu antes do proximo slot dele. Uma sessao a menos em ~8 e
-12%, o dobro da faixa estavel (5%): o painel dizia "regredindo" sem que o
desempenho de nada tivesse caido.

A variacao exibida (`change_pct`) passa a ser a da tonelagem POR SESSAO que
treinou o grupo (media das exposicoes de cada janela). E a mesma ideia do "ate
o dia N contra ate o dia N", so que na unidade que as janelas moveis tem: a
janela atual e comparada pelo que JA aconteceu nela, sem prever sessoes que
ainda nao vieram.

A media por sessao, sozinha, tem o vies oposto: ela sobe quando o grupo deixa
de receber exposicoes INDIRETAS leves (triceps no supino, biceps na remada) e
desce quando elas voltam - medido nos dados do DEV, biceps +122% por sessao
com a tonelagem total identica. Por isso a classificacao exige CONCORDANCIA:
`PROGREDINDO`/`REGREDINDO` so quando a variacao por sessao E a do total passam
da faixa no mesmo sentido; sinais discordantes (menos sessoes, cada uma igual
ou mais forte; ou mais sessoes, cada uma mais fraca) ficam `ESTAVEL`. A
regressao real - cada sessao mais fraca sem compensar em frequencia - continua
aparecendo.

Com menos de `MIN_SESSIONS_PER_WINDOW` sessoes em alguma das janelas a leitura
e `DADOS_INSUFICIENTES` - nunca "regredindo" por falta de amostra, inclusive
quando o grupo nao foi treinado na janela atual (isso e frequencia/aderencia,
que tem tela propria: `/dashboard/musculos`).

## M61 - janela personalizada (2026-10-03)

Com `date_from`/`date_to` a janela atual e exatamente a escolhida e a anterior
e o periodo de mesmo tamanho logo antes dela (`range_comparison_windows`).
Quando o fim e no passado a janela atual esta COMPLETA, mas a regra acima
(por sessao + concordancia com o total) continua a mesma de proposito: ela
nao corrige so o "hoje ainda nao acabou" - o corte do ciclo do Modelo no meio
e o vies das exposicoes indiretas existem em qualquer janela fechada, e duas
regras diferentes fariam o mesmo periodo mudar de veredito so por ter sido
escolhido pelo seletor personalizado em vez do rolling. Numa janela completa
com a mesma frequencia nas duas metades, por sessao e total coincidem.

## Volume x Desempenho (2026-10-02)

A tonelagem mede VOLUME, nao desempenho: reduzir series de proposito (Modelo
novo) derruba a tonelagem enquanto o e1RM sobe. Desde 2026-10-02 o painel
mostra duas dimensoes separadas e queda de volume nunca e chamada de
"regredindo":

- `volume_status` (este modulo): `VOLUME_UP` / `VOLUME_STABLE` /
  `VOLUME_DOWN` - mesma regra de concordancia acima, rotulos neutros - mais
  `DADOS_INSUFICIENTES` / `NOVO` / `SEM_DADOS`;
- `performance_status` (`muscle_group_performance.py`): melhor e1RM do mesmo
  exercicio nas duas janelas, ponderado pela ativacao.

`classification` continua no payload, com os valores de antes, so por
compatibilidade (frontend publicado antes desta mudanca); e DEPRECADO - nao
use em tela nova: e o volume com rotulo de desempenho.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

# Variacao percentual (em modulo) abaixo da qual o grupo e "estavel".
STABLE_BAND_PCT = 5.0

# Minimo de sessoes (com tonelagem no grupo) em CADA janela para classificar.
# Mesmo piso do `INSUFFICIENT_DATA` por exercicio ("menos de 2 exposicoes
# comparaveis"): uma sessao so e comparacao de um treino contra outro.
MIN_SESSIONS_PER_WINDOW = 2

# Base da variacao `change_pct` (ecoada no payload para o frontend explicar).
COMPARISON_BASIS = "tonnage_per_session"

PROGREDINDO = "PROGREDINDO"
ESTAVEL = "ESTAVEL"
REGREDINDO = "REGREDINDO"
NOVO = "NOVO"  # sem sessao no periodo anterior: nao ha variacao calculavel
SEM_DADOS = "SEM_DADOS"  # sem tonelagem em nenhum dos dois periodos
DADOS_INSUFICIENTES = "DADOS_INSUFICIENTES"  # poucas sessoes para comparar

# Status de VOLUME (rotulos neutros: menos volume nao e regressao).
VOLUME_UP = "VOLUME_UP"
VOLUME_STABLE = "VOLUME_STABLE"
VOLUME_DOWN = "VOLUME_DOWN"
_VOLUME_STATUS = {PROGREDINDO: VOLUME_UP, ESTAVEL: VOLUME_STABLE, REGREDINDO: VOLUME_DOWN}


def volume_status(classification: str) -> str:
    """Classificacao de tonelagem -> status de volume neutro. Os estados sem
    leitura (`DADOS_INSUFICIENTES`/`NOVO`/`SEM_DADOS`) passam iguais."""
    return _VOLUME_STATUS.get(classification, classification)


def comparison_windows(today: date, days: int) -> Dict[str, date]:
    """As duas janelas, inclusivas e do MESMO tamanho, em datas locais.

    Atual = `days` dias terminando `today`; anterior = os `days` dias logo
    antes. (A consulta antiga usava `>= CURDATE() - days`, que da `days + 1`
    dias na atual contra `days` na anterior, e `CURDATE()` do banco - UTC em
    producao - em vez do "hoje" do usuario.)"""
    size = max(int(days), 1)
    return range_comparison_windows(today - timedelta(days=size - 1), today)


def range_comparison_windows(date_from: date, date_to: date) -> Dict[str, date]:
    """Janela `custom` (M61): atual = `[date_from, date_to]` escolhida pelo
    usuario (inclusiva); anterior = o periodo de MESMO tamanho logo antes de
    `date_from`. Mesmo formato de `comparison_windows`, que e o caso
    particular `date_to = hoje`. Validacao (ordem, futuro, teto) e do
    chamador - aqui so a aritmetica."""
    if date_from > date_to:
        raise ValueError("date_from nao pode ser depois de date_to")
    size = (date_to - date_from).days + 1
    previous_to = date_from - timedelta(days=1)
    return {
        "current_from": date_from,
        "current_to": date_to,
        "previous_from": previous_to - timedelta(days=size - 1),
        "previous_to": previous_to,
    }


def per_session(tonnage: float, sessions: int) -> Optional[float]:
    """Tonelagem media por sessao; `None` (nunca 0) sem sessao."""
    if sessions <= 0:
        return None
    return round(float(tonnage) / sessions, 1)


def _pct(current: float, previous: float) -> float:
    return (current - previous) / previous * 100.0


def _direction(change: float) -> int:
    if change >= STABLE_BAND_PCT:
        return 1
    if change <= -STABLE_BAND_PCT:
        return -1
    return 0


def classify_change(
    current_tonnage: float,
    previous_tonnage: float,
    current_sessions: int,
    previous_sessions: int,
) -> Dict[str, Any]:
    """Classificacao da janela atual contra a anterior (ver docstring do
    modulo, M50). `change_pct` = variacao da tonelagem POR SESSAO;
    `total_change_pct` = variacao da tonelagem total (contexto)."""
    empty = {"change_pct": None, "total_change_pct": None}
    if current_sessions <= 0 and previous_sessions <= 0:
        return {"classification": SEM_DADOS, **empty}
    if previous_sessions <= 0 or previous_tonnage <= 0:
        return {"classification": NOVO, **empty}
    if current_sessions < MIN_SESSIONS_PER_WINDOW or previous_sessions < MIN_SESSIONS_PER_WINDOW:
        # Inclui "nenhuma sessao na janela atual": ausencia de treino e
        # frequencia/aderencia, nao queda de desempenho.
        return {"classification": DADOS_INSUFICIENTES, **empty}
    per_session_change = _pct(current_tonnage / current_sessions, previous_tonnage / previous_sessions)
    total_change = _pct(current_tonnage, previous_tonnage)
    direction = _direction(per_session_change)
    if direction != _direction(total_change):
        direction = 0
    label = {1: PROGREDINDO, -1: REGREDINDO, 0: ESTAVEL}[direction]
    return {
        "classification": label,
        "change_pct": round(per_session_change, 1),
        "total_change_pct": round(total_change, 1),
    }


def build_group_progression(
    rows: Iterable[Dict[str, Any]],
    phases: Optional[Dict[int, Dict[str, Any]]] = None,
    performance: Optional[Dict[int, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """`rows`: uma linha por (grupo, periodo) com `period` ('current'|'previous'),
    `muscle_group_id`, `muscle_group_name`, `tonnage_kg` (ja ponderada),
    `sets_count` e `sessions_count` (sessoes com tonelagem no grupo).
    `performance`: saida de `muscle_group_performance.build_group_performance`
    (grupo ausente = sem exercicio comparavel). Devolve um item por grupo,
    ordenado por tonelagem atual."""
    from .muscle_group_performance import insufficient

    groups: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        gid = int(row["muscle_group_id"])
        item = groups.setdefault(
            gid,
            {
                "muscle_group_id": gid,
                "muscle_group_name": row.get("muscle_group_name"),
                "current_tonnage_kg": 0.0,
                "previous_tonnage_kg": 0.0,
                "current_sets": 0,
                "previous_sets": 0,
                "current_sessions": 0,
                "previous_sessions": 0,
                # M58: series fora da tonelagem (`assisted`/`bodyweight`).
                # Campo novo, aditivo: cliente antigo ignora.
                "current_tonnage_excluded_sets": 0,
                "previous_tonnage_excluded_sets": 0,
            },
        )
        prefix = "current" if row["period"] == "current" else "previous"
        item[f"{prefix}_tonnage_kg"] = round(float(row.get("tonnage_kg") or 0), 1)
        item[f"{prefix}_sets"] = int(row.get("sets_count") or 0)
        item[f"{prefix}_tonnage_excluded_sets"] = int(row.get("tonnage_excluded_sets") or 0)
        item[f"{prefix}_sessions"] = int(row.get("sessions_count") or 0)

    items: List[Dict[str, Any]] = []
    for item in groups.values():
        item["current_tonnage_per_session_kg"] = per_session(item["current_tonnage_kg"], item["current_sessions"])
        item["previous_tonnage_per_session_kg"] = per_session(item["previous_tonnage_kg"], item["previous_sessions"])
        item.update(
            classify_change(
                item["current_tonnage_kg"],
                item["previous_tonnage_kg"],
                item["current_sessions"],
                item["previous_sessions"],
            )
        )
        item["volume_status"] = volume_status(item["classification"])
        item.update((performance or {}).get(item["muscle_group_id"]) or insufficient())
        phase = (phases or {}).get(item["muscle_group_id"]) or {}
        item["phase"] = phase.get("phase")
        item["phase_status"] = phase.get("phase_status")
        items.append(item)
    items.sort(key=lambda i: (-i["current_tonnage_kg"], i["muscle_group_name"] or ""))
    return items

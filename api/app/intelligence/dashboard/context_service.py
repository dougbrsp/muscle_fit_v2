"""Eventos de contexto do Novo Dashboard - `GET /analytics/dashboard/context`,
a timeline de `/dashboard/contexto`, a associacao em `recovery-signals` e as
regras de insight (plano §6, §7, §11, §12; F2.1).

Um evento de contexto responde a pergunta "o que mais estava acontecendo?".
Ele NUNCA explica um numero: a regra `PERFORMANCE_DROP_WITH_CONTEXT_EVENT` diz
"coincide com", com `data_quality=LOW` e a limitacao "associacao temporal, nao
causa" - e essa fronteira e a razao de o evento existir separado das metricas.

**Duas origens, uma timeline**

1. **Manual** (`source='manual'`): o que so o usuario sabe - alcool, viagem,
   doenca, sono atipico, fase de dieta, suplemento, lesao/dor. Linha em
   `context_events` (migracao 048), gravada pelo CRUD da api-core.

2. **Derivado** (`source='derived'`): o que o proprio banco ja sabe -
   **troca de ficha/modelo** (`workout_plan_template_assignments.assigned_at`)
   e **troca de academia** (mudanca de `activities.gym_id` entre dois dias de
   treino consecutivos).

**Decisao: derivado NAO e materializado.** Ele e calculado a cada leitura, a
partir das tabelas que ja sao a verdade. As razoes, em ordem de peso:

- *Idempotencia por construcao.* Materializar exigiria um job que, a cada
  execucao, "descobrisse" os mesmos eventos sem duplica-los - ou seja, uma
  chave de deduplicacao artificial e um upsert. Derivando na leitura, rodar
  duas vezes devolve exatamente a mesma lista, porque a lista E uma funcao
  dos dados de origem.
- *Sem dupla verdade.* Se o usuario apagar a atribuicao de Modelo, o evento
  "troca de ficha" derivado desaparece junto; um evento materializado
  sobreviveria, afirmando um fato que nao aconteceu mais.
- *Custo.* Sao 2 consultas indexadas por janela (`user_id, assigned_at` e
  `user_id, local_date`), na mesma ordem de grandeza do resto da tela - nao
  compensa uma tabela e um job para economiza-las.

O preco: um derivado nao e editavel nem apagavel pelo usuario. E o preco
certo - ele nao e uma opiniao, e um registro. O usuario que quiser anotar algo
sobre a troca registra um evento manual (o vocabulario permite `plan_change` e
`gym_change` manuais, e a timeline marca a origem de cada um).

**Janela**: um evento com duracao (`end_local_date`) entra na janela se o
INTERVALO dele toca a janela - uma viagem de 10 dias que comecou antes do
inicio da janela continua sendo contexto dos dias de dentro dela.

**Pausa de treino**: este modulo tambem devolve `days_since_last_session`, a
partir da ultima `activities.local_date` <= `date_to`. E o insumo da regra
`TRAINING_PAUSE_DETECTED` (decisao do usuario em 2026-09-24: numa pausa total,
o insight correto e "voce parou de treinar", e ele pertence ao contexto, nao a
regra de volume). Aqui so se MEDE o intervalo; quem decide se ele e uma pausa
e a regra, com o limiar do `metrics_registry`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from ...context_events_schema import (
    TABLE_MISSING_LIMITATION,
    event_type_label,
    has_context_events_table,
)
from ...observability import log_event
from .coercion import to_date
from .contracts import DataNature, DataQuality, WindowInfo
from .identity import require_user_id
from .metrics_registry import metric_version

# Teto de eventos manuais lidos por janela (365 dias de eventos diarios e
# muito mais do que alguem registra; o teto existe pelo limite de 1 MB da
# Data API, nao por expectativa de volume).
MAX_MANUAL_EVENTS = 500

# Teto por origem derivada.
MAX_DERIVED_EVENTS = 100

DRILLDOWN_HREF = "/dashboard/contexto"

METRIC_VERSIONS: Mapping[str, str] = {"context_events": metric_version("context_events")}

NO_EVENTS_LIMITATION = (
    "Nenhum evento de contexto registrado nesta janela: ausência de evento não é ausência de "
    "contexto — só quer dizer que nada foi anotado."
)

DERIVED_ONLY_LIMITATION = (
    "Troca de ficha e troca de academia são DERIVADAS do histórico (atribuição de Modelo e "
    "academia das sessões); elas não podem ser editadas nem apagadas na timeline."
)

ASSOCIATION_LIMITATION = "Evento de contexto é associação temporal, nunca causa de um número."

# Eventos manuais cujo INTERVALO toca a janela.
MANUAL_EVENTS_SQL = """
SELECT id, event_type, local_date, end_local_date, intensity, notes, source, created_at
FROM context_events
WHERE user_id = %s
  AND local_date <= %s
  AND COALESCE(end_local_date, local_date) >= %s
ORDER BY local_date ASC, id ASC
LIMIT %s
"""

# Derivado 1: troca de ficha/modelo. Mesma fonte dos "marcos" de
# `/dashboard/corpo` (`body_service.TEMPLATE_ASSIGNMENTS_SQL`).
#
# Ate a migracao 049 (M23) a atribuicao era o UNICO momento observavel,
# porque desatribuir era DELETE fisico. Agora a desatribuicao deixa
# `unassigned_at` na linha, e "fiquei sem Modelo" passa a ser um evento de
# contexto tao real quanto "troquei de Modelo" - por isso a janela filtra
# pelos DOIS instantes.
TEMPLATE_ASSIGNMENTS_SQL = """
SELECT a.id AS assignment_id, a.assigned_at, a.unassigned_at,
       t.name AS template_name, t.id AS template_id
FROM workout_plan_template_assignments a
LEFT JOIN workout_plan_templates t ON t.id = a.template_id
WHERE a.user_id = %s
  AND ((a.assigned_at >= %s AND a.assigned_at < %s)
       OR (a.unassigned_at IS NOT NULL AND a.unassigned_at >= %s AND a.unassigned_at < %s))
ORDER BY a.assigned_at ASC
LIMIT %s
"""

# Derivado 2: academia de cada dia de treino. A TROCA e detectada comparando
# dias consecutivos - por isso a consulta comeca UM DIA antes da janela: sem o
# dia anterior, o primeiro dia da janela pareceria sempre uma troca.
GYM_BY_DAY_SQL = """
SELECT a.local_date, a.gym_id, MIN(g.name) AS gym_name
FROM activities a
LEFT JOIN gyms g ON g.id = a.gym_id
WHERE a.user_id = %s AND a.local_date BETWEEN %s AND %s AND a.gym_id IS NOT NULL
GROUP BY a.local_date, a.gym_id
ORDER BY a.local_date ASC, a.gym_id ASC
"""

# Dias em que a academia PODE ter mudado sem a janela ver: quanto tempo atras
# procurar o dia de treino anterior ao inicio da janela.
GYM_LOOKBACK_DAYS = 60

LAST_SESSION_SQL = """
SELECT MAX(local_date) AS last_local_date
FROM activities
WHERE user_id = %s AND local_date <= %s
"""

SESSIONS_BEFORE_SQL = """
SELECT COUNT(DISTINCT local_date) AS n
FROM activities
WHERE user_id = %s AND local_date > %s AND local_date <= %s
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() or []


def _event(
    *,
    event_type: str,
    local_date: date,
    end_local_date: Optional[date] = None,
    intensity: Optional[int] = None,
    notes: Optional[str] = None,
    source: str,
    event_id: Optional[int] = None,
) -> dict[str, Any]:
    """Uma entrada da timeline, no MESMO formato para manual e derivado - a
    tela nao precisa de dois caminhos de renderizacao."""
    return {
        "id": event_id,
        "event_type": event_type,
        "event_type_label": event_type_label(event_type),
        "local_date": local_date,
        "end_local_date": end_local_date,
        "intensity": intensity,
        "notes": notes,
        "source": source,
        "derived": source != "manual",
        "editable": source == "manual",
    }


def _sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """Ordem total e deterministica: dia, tipo, id (derivado tem id `None` e
    vem antes do manual do mesmo tipo e dia - nao ha ambiguidade porque o
    proximo criterio ja resolve)."""
    return (
        item["local_date"],
        str(item["event_type"]),
        str(item["source"]),
        -1 if item.get("id") is None else int(item["id"]),
    )


# ---------------------------------------------------------------------------
# Derivados (funcoes PURAS sobre as linhas das consultas)
# ---------------------------------------------------------------------------


def template_change_events(
    rows: Sequence[Mapping[str, Any]], *, window: WindowInfo
) -> list[dict[str, Any]]:
    """Uma atribuicao de Modelo = um evento `plan_change` derivado; o FIM de
    uma vigencia sem substituto no mesmo dia tambem (M23).

    Duas atribuicoes no MESMO dia (corrigir a escolha logo depois de erra-la)
    viram UM evento: o que o contexto registra e "a ficha mudou neste dia",
    nao quantas vezes o botao foi clicado.

    Uma TROCA escreve os dois instantes no mesmo momento (a 049 encerra a
    anterior com o `assigned_at` da nova), entao ela cairia duas vezes no
    mesmo dia. O evento de ATRIBUICAO vence: "passei a seguir o Modelo X"
    diz mais do que "parei de seguir o Modelo Y", e e o mesmo dia. So sobra
    um evento de fim quando o usuario ficou REALMENTE sem Modelo naquele
    dia - que e a informacao nova que a 049 tornou observavel."""
    by_day: dict[date, dict[str, Any]] = {}
    fins: dict[date, dict[str, Any]] = {}
    for row in rows:
        name = row.get("template_name") or row.get("template_id")
        assigned = row.get("assigned_at")
        day = assigned.date() if isinstance(assigned, datetime) else to_date(assigned)
        if day is not None and window.date_from <= day <= window.date_to:
            by_day[day] = _event(
                event_type="plan_change",
                local_date=day,
                notes=f"Modelo de Treino atribuído: {name}",
                source="derived",
            )

        unassigned = row.get("unassigned_at")
        fim = unassigned.date() if isinstance(unassigned, datetime) else to_date(unassigned)
        if fim is not None and window.date_from <= fim <= window.date_to:
            fins[fim] = _event(
                event_type="plan_change",
                local_date=fim,
                notes=f"Modelo de Treino encerrado: {name}",
                source="derived",
            )

    for day, evento in fins.items():
        by_day.setdefault(day, evento)
    return [by_day[day] for day in sorted(by_day)]


def gym_change_events(
    rows: Sequence[Mapping[str, Any]], *, window: WindowInfo
) -> list[dict[str, Any]]:
    """Mudanca de `activities.gym_id` entre dois DIAS de treino consecutivos.

    As linhas vem de uma janela maior que a pedida (ver `GYM_LOOKBACK_DAYS`):
    o dia anterior e necessario para saber se o primeiro dia DA JANELA foi uma
    troca ou a continuacao da mesma academia. Dia com duas academias
    (treinou em duas) conta a ULTIMA por id - e o mesmo criterio estavel dos
    dois lados da comparacao, e nao ha ordem melhor disponivel.

    Voltar para a academia anterior TAMBEM e uma troca: o que muda o contexto
    e o equipamento disponivel ter mudado, nao a academia ser nova."""
    por_dia: dict[date, tuple[int, Optional[str]]] = {}
    for row in rows:
        day = to_date(row.get("local_date"))
        gym_id = row.get("gym_id")
        if day is None or gym_id is None:
            continue
        por_dia[day] = (int(gym_id), row.get("gym_name"))

    out: list[dict[str, Any]] = []
    anterior: Optional[tuple[int, Optional[str]]] = None
    for day in sorted(por_dia):
        atual = por_dia[day]
        if anterior is not None and atual[0] != anterior[0] and window.date_from <= day <= window.date_to:
            de = anterior[1] or f"academia {anterior[0]}"
            para = atual[1] or f"academia {atual[0]}"
            out.append(
                _event(
                    event_type="gym_change",
                    local_date=day,
                    notes=f"Treino mudou de {de} para {para}",
                    source="derived",
                )
            )
        anterior = atual
    return out[:MAX_DERIVED_EVENTS]


def manual_events(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        day = to_date(row.get("local_date"))
        if day is None:
            continue
        intensity = row.get("intensity")
        out.append(
            _event(
                event_id=int(row["id"]) if row.get("id") is not None else None,
                event_type=str(row.get("event_type")),
                local_date=day,
                end_local_date=to_date(row.get("end_local_date")),
                intensity=None if intensity is None else int(intensity),
                notes=row.get("notes"),
                source=str(row.get("source") or "manual"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Leitura (I/O)
# ---------------------------------------------------------------------------


def load_context_events(
    conn: Any, *, user_id: Any, window: WindowInfo
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """`(eventos, tabela_disponivel, limitacoes)` - manuais + derivados,
    ordenados.

    Antes da 048 os manuais simplesmente nao existem (a tabela nao existe) e a
    limitacao e declarada; os DERIVADOS continuam funcionando, porque eles nao
    dependem da tabela nova. Essa e a razao de a limitacao ser sobre "eventos
    registrados", nao sobre a tela inteira."""
    owner = require_user_id(user_id)
    limitations: list[str] = []

    with conn.cursor() as cursor:
        table_available = has_context_events_table(cursor)
    manual_rows: list[dict[str, Any]] = []
    if table_available:
        manual_rows = _rows(
            conn,
            MANUAL_EVENTS_SQL,
            (owner, window.date_to, window.date_from, MAX_MANUAL_EVENTS),
        )
    else:
        limitations.append(TABLE_MISSING_LIMITATION)

    started = datetime.combine(window.date_from, datetime.min.time())
    ended = datetime.combine(window.date_to + timedelta(days=1), datetime.min.time())
    try:
        assignment_rows = _rows(
            conn, TEMPLATE_ASSIGNMENTS_SQL, (owner, started, ended, started, ended, MAX_DERIVED_EVENTS)
        )
    except Exception as exc:  # noqa: BLE001 - derivado e contexto, nunca derruba a tela
        log_event("dashboard.context_derived_unavailable", user_id=owner, kind="plan_change", error=str(exc))
        assignment_rows = []
        limitations.append("Trocas de ficha não puderam ser lidas nesta janela.")

    try:
        gym_rows = _rows(
            conn,
            GYM_BY_DAY_SQL,
            (owner, window.date_from - timedelta(days=GYM_LOOKBACK_DAYS), window.date_to),
        )
    except Exception as exc:  # noqa: BLE001
        log_event("dashboard.context_derived_unavailable", user_id=owner, kind="gym_change", error=str(exc))
        gym_rows = []
        limitations.append("Trocas de academia não puderam ser lidas nesta janela.")

    events = [
        *manual_events(manual_rows),
        *template_change_events(assignment_rows, window=window),
        *gym_change_events(gym_rows, window=window),
    ]
    events.sort(key=_sort_key)
    return events, table_available, limitations


def load_deload_ranges(
    conn: Any, *, user_id: Any, window: WindowInfo, since: Optional[date] = None
) -> tuple[tuple[date, date], ...]:
    """M97 D: periodos de deload ATIVOS (nao cancelados) + dias das sessoes de
    deload que tocam a janela (desde `since`, se anterior: a regua das
    semanas da regra de volume) (`deload.deload_ranges`). Alimenta a supressao
    de "volume abaixo da meta" e de regressao (`insight_rules`). Falha de
    leitura (banco anterior a 094) -> vazio: sem deload, nada muda."""
    from ...domain import deload as deload_owner

    date_to = window.date_to
    date_from = min(window.date_from, since) if since is not None else window.date_from
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dp.starts_on, dp.ends_on FROM deload_periods dp "
                f"WHERE dp.user_id = %s AND {deload_owner.sql_period_overlaps('dp')}",
                (int(user_id), date_to, date_from),
            )
            periods = [(to_date(row["starts_on"]), to_date(row["ends_on"])) for row in cur.fetchall() or []]
            cur.execute(
                "SELECT DISTINCT a.local_date FROM activities a "
                f"WHERE a.user_id = %s AND {deload_owner.sql_is_deload('a')} "
                "AND a.local_date BETWEEN %s AND %s",
                (int(user_id), date_from, date_to),
            )
            days = [to_date(row["local_date"]) for row in cur.fetchall() or [] if row.get("local_date") is not None]
    except Exception:  # noqa: BLE001 - leitura opcional (antes da 094)
        log_event("dashboard.deload_ranges_failed", user_id=int(user_id))
        return ()
    return deload_owner.deload_ranges(
        [(start, end) for start, end in periods if start and end], [day for day in days if day]
    )


def load_training_gap(conn: Any, *, user_id: Any, window: WindowInfo) -> dict[str, Any]:
    """Intervalo desde a ultima sessao ATE `window.date_to` (nao ate "hoje":
    a janela e quem define o presente da leitura, e e assim que a tela fica
    reproduzivel) e quantos DIAS DE TREINO existiram nos 28 dias anteriores a
    essa ultima sessao.

    O segundo numero e o que separa "parou de treinar" de "ainda nao
    comecou": sem historico antes do silencio, nao houve pausa."""
    owner = require_user_id(user_id)
    rows = _rows(conn, LAST_SESSION_SQL, (owner, window.date_to))
    last = to_date((rows[0] if rows else {}).get("last_local_date"))
    if last is None:
        return {"last_session_local_date": None, "days_since_last_session": None, "sessions_before": 0}
    before_rows = _rows(conn, SESSIONS_BEFORE_SQL, (owner, last - timedelta(days=28), last))
    sessions_before = int((before_rows[0] if before_rows else {}).get("n") or 0)
    return {
        "last_session_local_date": last,
        "days_since_last_session": (window.date_to - last).days,
        # `SESSIONS_BEFORE_SQL` conta `local_date > last-28 AND <= last`, ou
        # seja, inclui a propria ultima sessao.
        "sessions_before": sessions_before,
    }


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def context_counts(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Contagem por tipo, na ordem de quem tem mais (empate pelo codigo)."""
    counts: dict[str, int] = {}
    for item in events:
        counts[str(item["event_type"])] = counts.get(str(item["event_type"]), 0) + 1
    return [
        {"event_type": key, "event_type_label": event_type_label(key), "count": value}
        for key, value in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def build_context(conn: Any, *, user_id: Any, window: WindowInfo) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/context`.

    Sempre 200: sem evento nenhum a resposta existe, com `INSUFFICIENT` e a
    limitacao que diz a verdade ("nada foi anotado"), nunca um zero que
    pareca medida."""
    owner = require_user_id(user_id)
    events, table_available, limitations = load_context_events(conn, user_id=owner, window=window)
    gap = load_training_gap(conn, user_id=owner, window=window)

    if not events:
        limitations.append(NO_EVENTS_LIMITATION)
    if any(item["derived"] for item in events):
        limitations.append(DERIVED_ONLY_LIMITATION)
    limitations.append(ASSOCIATION_LIMITATION)

    manual_total = sum(1 for item in events if not item["derived"])
    quality = DataQuality.INSUFFICIENT if not events else DataQuality.MEDIUM if manual_total else DataQuality.LOW

    log_event(
        "dashboard.computed",
        screen="context",
        user_id=owner,
        window=window.kind.value,
        events=len(events),
        manual=manual_total,
        table_available=table_available,
    )

    return {
        "window": window,
        "metric_versions": dict(METRIC_VERSIONS),
        "data_quality": quality,
        "limitations": list(dict.fromkeys(limitations)),
        "items": events,
        "counts": context_counts(events),
        "manual_total": manual_total,
        "derived_total": len(events) - manual_total,
        "table_available": table_available,
        "nature": DataNature.OBSERVED,
        "last_session_local_date": gap["last_session_local_date"],
        "days_since_last_session": gap["days_since_last_session"],
    }


__all__ = [
    "ASSOCIATION_LIMITATION",
    "DERIVED_ONLY_LIMITATION",
    "DRILLDOWN_HREF",
    "GYM_LOOKBACK_DAYS",
    "METRIC_VERSIONS",
    "NO_EVENTS_LIMITATION",
    "build_context",
    "context_counts",
    "gym_change_events",
    "load_context_events",
    "load_training_gap",
    "manual_events",
    "template_change_events",
]

"""Leitura da aderência do cardio, planejado × realizado (M98 C2).

Só I/O e montagem; as regras são dos donos:

- PLANEJADO: os itens de cardio do slot do Modelo de cada sessão de treino
  da janela (`activities.workout_plan_id` -> `workout_plans.template_id` +
  `template_slot_sequence`, na versão do Modelo que governa a ficha,
  `template_versions.plan_version_expr` — o mesmo vínculo da aderência da
  força em `dashboard/adherence_service.py`);
- REALIZADO: as atividades do Intervals (`intervals_activities`) que são
  cardio para `intervals_activity_kind` (dono único; nada de `LIKE` em SQL);
- CASAMENTO: `intervals_hr_match` (o MESMO critério de horário do casamento
  treino × WeightTraining), ancorado no FIM da sessão de força
  (`activities.ended_at`, ou o início sem fim): o cardio do Modelo é feito
  depois da força;
- TEMPO EM ZONA: `hr_zones.app_zone_seconds` (mapeamento 7 -> 5);
- MÉTRICAS E STATUS: `domain/cardio_adherence`.

Sessão sem cardio no slot não entra (nada a cumprir).

CARDIO NO DIA DE DESCANSO (decisão do usuário de 27/10/2026): o slot de um
dia passado vem da FICHA SALVA PARA A DATA (`workout_plans.plan_date`; a mais
recente da data vence, como no contexto do agente). Sem ficha, o dia não é
avaliado. Ficha de slot 'descanso' com cardio prescrito, num dia SEM sessão de
força, casa pelo dia local inteiro (`intervals_hr_match.match_local_day`; mais
de uma atividade = a mais longa, `MATCHED_AMBIGUOUS`). A atividade já casada
com a força de um dia vizinho não entra. Se o dia ainda não terminou, só
conta quando já casou (`cardio_adherence.rest_day_due`).

Datas: `activities.local_date` e `intervals_activities.activity_date` já são
o dia LOCAL; a janela é resolvida no fuso do usuário
(`time_windows.user_today_for`), nunca com `CURDATE()`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from . import intervals_activity_kind, intervals_hr_match, intervals_store
from .domain import cardio_adherence, cardio_envelope, cardio_prescription, hr_zones
from .template_versions import plan_version_expr

# Margem (dias) na busca das atividades do Intervals em volta da janela, a
# mesma do sync (`intervals_sync.LINK_DATE_MARGIN_DAYS`): o casamento é por
# horário, não por data.
CANDIDATE_DATE_MARGIN_DAYS = 1

_ITEM_COLUMNS = ", ".join(f"i.{name}" for name in cardio_prescription.PRESCRIPTION_FIELDS)

SESSIONS_WITH_CARDIO_SQL = f"""
SELECT a.id AS activity_id, a.local_date, a.started_at, a.ended_at,
       wp.id AS workout_plan_id, wp.template_id, wp.template_slot_sequence,
       i.id AS item_id, i.item_kind, {_ITEM_COLUMNS}
FROM activities a
JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
JOIN workout_plan_template_slots s
  ON s.version_id = {plan_version_expr("wp", by_date=True)} AND s.sequence = wp.template_slot_sequence
JOIN workout_plan_template_slot_items i ON i.slot_id = s.id AND i.item_kind = %s
WHERE a.user_id = %s AND a.local_date BETWEEN %s AND %s
ORDER BY a.local_date ASC, a.started_at ASC, a.id ASC, i.item_order ASC, i.id ASC
"""

# Fichas de DESCANSO com cardio: a ficha mais recente da data, slot
# 'descanso' na versão que governa a ficha, e nenhuma sessão de força no dia.
REST_DAY_PLANS_WITH_CARDIO_SQL = f"""
SELECT wp.id AS workout_plan_id, wp.plan_date AS local_date,
       wp.template_id, wp.template_slot_sequence,
       i.id AS item_id, i.item_kind, {_ITEM_COLUMNS}
FROM workout_plans wp
JOIN workout_plan_template_slots s
  ON s.version_id = {plan_version_expr("wp", by_date=True)} AND s.sequence = wp.template_slot_sequence
JOIN workout_plan_template_slot_items i ON i.slot_id = s.id AND i.item_kind = %s
WHERE wp.user_id = %s AND wp.plan_date BETWEEN %s AND %s AND s.slot_type = %s
  AND wp.id = (SELECT MAX(wl.id) FROM workout_plans wl WHERE wl.user_id = wp.user_id AND wl.plan_date = wp.plan_date)
  AND NOT EXISTS (SELECT 1 FROM activities a WHERE a.user_id = wp.user_id AND a.local_date = wp.plan_date)
ORDER BY wp.plan_date ASC, wp.id ASC, i.item_order ASC, i.id ASC
"""

CANDIDATES_SQL = """
SELECT id, external_id, activity_date, start_time, name, sport_type, moving_time_seconds,
       avg_hr_bpm, max_hr_bpm, calories_kcal, training_load, hr_zone_seconds
FROM intervals_activities
WHERE user_id = %s AND activity_date BETWEEN %s AND %s AND start_time IS NOT NULL
ORDER BY start_time ASC, external_id ASC
"""


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _is_cardio_row(row: dict) -> bool:
    return intervals_activity_kind.is_cardio(row.get("sport_type"), row.get("name"))


def _first_item_per_session(rows: list[dict], key_field: str = "activity_id") -> list[dict]:
    """Uma linha por sessão (ou por ficha de descanso, `key_field`): o 1º
    item de cardio do slot (ordem do item). Slot com mais de um cardio: os
    demais não são avaliados nesta fase (produção, 2026-10-26: nenhum slot com
    mais de um)."""
    seen: set[int] = set()
    out: list[dict] = []
    for row in rows:
        key = int(row[key_field])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _realized(match: intervals_hr_match.MatchResult, rows_by_id: dict[int, dict]) -> Optional[cardio_adherence.RealizedCardio]:
    if not match.persisted or match.candidate is None:
        return None
    candidate = match.candidate
    row = rows_by_id.get(candidate.intervals_activity_id, {})
    zone_seconds = hr_zones.app_zone_seconds(intervals_store.json_list(row.get("hr_zone_seconds")))
    seconds = candidate.moving_time_seconds
    if seconds is None and zone_seconds is not None:
        seconds = sum(zone_seconds)
    return cardio_adherence.RealizedCardio(
        seconds=float(seconds or 0),
        zone_seconds=zone_seconds,
        intervals_activity_id=candidate.intervals_activity_id,
        sport_type=candidate.sport_type,
        match_status=match.status,
    )


def _item(row: dict, anchor: str, adherence: dict[str, Any]) -> dict[str, Any]:
    activity_id = row.get("activity_id")
    plan_id = row.get("workout_plan_id")
    return {
        "activity_id": int(activity_id) if activity_id is not None else None,
        "workout_plan_id": int(plan_id) if plan_id is not None else None,
        "local_date": _as_date(row["local_date"]),
        "anchor": anchor,
        "template_id": row.get("template_id"),
        "template_slot_sequence": row.get("template_slot_sequence"),
        "item_id": row.get("item_id"),
        "adherence": adherence,
    }


def build_adherence(
    cur: Any, user_id: int, date_from: date, date_to: date, *, today: Optional[date] = None
) -> dict[str, Any]:
    """Sessões com cardio prescrito em `[date_from, date_to]` (datas locais),
    cada uma com a aderência do dono, e o resumo por semana: as sessões de
    força (âncora no fim da força) e as fichas de descanso com cardio (âncora
    no dia local inteiro). `today` = dia local do usuário; ausente, vale
    `date_to` (as janelas terminam hoje)."""
    params = (int(user_id), date_from.isoformat(), date_to.isoformat())
    cur.execute(SESSIONS_WITH_CARDIO_SQL, (cardio_prescription.CARDIO_ITEM_KIND, *params))
    sessions = _first_item_per_session([dict(row) for row in (cur.fetchall() or [])])
    cur.execute(
        REST_DAY_PLANS_WITH_CARDIO_SQL,
        (cardio_prescription.CARDIO_ITEM_KIND, *params, cardio_envelope.REST_SLOT_TYPE),
    )
    rest_days = _first_item_per_session([dict(row) for row in (cur.fetchall() or [])], "workout_plan_id")

    candidates: list[intervals_hr_match.IntervalsCandidate] = []
    rows_by_id: dict[int, dict] = {}
    if sessions or rest_days:
        margin = timedelta(days=CANDIDATE_DATE_MARGIN_DAYS)
        cur.execute(
            CANDIDATES_SQL,
            (int(user_id), (date_from - margin).isoformat(), (date_to + margin).isoformat()),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        rows_by_id = {int(row["id"]): row for row in rows}
        candidates = intervals_hr_match.eligible_candidates(rows, accept=_is_cardio_row)

    items: list[dict[str, Any]] = []
    taken: set[int] = set()
    for session in sessions:
        anchor = session.get("ended_at") or session.get("started_at")
        match = intervals_hr_match.match_session(anchor, candidates)
        if match.persisted and match.candidate is not None:
            taken.add(match.candidate.intervals_activity_id)
        adherence = cardio_adherence.evaluate(session, _realized(match, rows_by_id))
        items.append(_item(session, cardio_adherence.ANCHOR_STRENGTH_END, adherence))

    # Dia de descanso: a atividade casada com a força de um dia vizinho não
    # pode ser "roubada" pelo casamento por dia.
    free = [c for c in candidates if c.intervals_activity_id not in taken]
    local_today = today or date_to
    for plan in rest_days:
        day = _as_date(plan["local_date"])
        match = intervals_hr_match.match_local_day(day, free)
        if not cardio_adherence.rest_day_due(day, local_today, matched=match.persisted):
            continue
        adherence = cardio_adherence.evaluate(plan, _realized(match, rows_by_id))
        items.append(_item(plan, cardio_adherence.ANCHOR_LOCAL_DAY, adherence))
    items.sort(key=lambda item: item["local_date"])

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "sessions": [{**item, "local_date": item["local_date"].isoformat()} for item in items],
        "weeks": cardio_adherence.summarize_weeks(items),
        "thresholds": {
            "adherent_time_min_pct": cardio_adherence.ADHERENT_TIME_MIN_PCT,
            "adherent_zone_min_pct": cardio_adherence.ADHERENT_ZONE_MIN_PCT,
            "partial_time_min_pct": cardio_adherence.PARTIAL_TIME_MIN_PCT,
        },
    }

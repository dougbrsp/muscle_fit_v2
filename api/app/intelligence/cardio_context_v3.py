"""Contexto do agente de cardio v3 (M98 C3a; plano
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §C3).

COMPACTO e no fuso do usuário: o v2 mandava ~95 mil tokens por chamada (o
`context_snapshot` médio de produção tinha 211 KB: recuperação muscular
inteira, bioimpedância de 30 dias, markdown das 10 últimas recomendações). O v3
manda só o que muda a decisão do cardio:

- treinos de força da janela (data, grupos, séries, volume, RPE);
- prontidão por grupo (`readiness_status`, `recovery_pct`, horas restantes);
- cardio REALIZADO do Intervals (`intervals_activities`, filtrado por
  `intervals_activity_kind`, com o tempo em zona Z1–Z5 de
  `hr_zones.app_zone_seconds` e a marca de sessão intensa do envelope);
- aderência planejado × realizado (`cardio_adherence_service`);
- wellness do Intervals (CTL/ATL/TSB, Ramp Rate, HRV, FC de repouso, com a
  precedência do sync/edição manual que já está gravada), sono e PAI manual;
- zonas do atleta (`athlete_hr_zones.load_athlete_zones`, precedência do dono);
- os próximos dias do plano (`cardio_envelope.project_plan_slots` sobre o
  Modelo ativo: slot, grupos e o cardio já prescrito em cada dia);
- as últimas recomendações (estruturadas; as antigas, em markdown, cortadas).

Só leitura e montagem: nenhuma regra numérica mora aqui (os donos são
`cardio_envelope`, `cardio_prescription`, `hr_zones` e `cardio_adherence`).
Datas: "hoje" e as janelas vêm de `IntelligenceServiceBase._user_today`
(`time_windows`), nunca de `CURDATE()`.

Cada bloco que lê o banco degrada sozinho (lista vazia + aviso no log): um
Modelo sem slot ou um cache do Intervals ausente não derruba a recomendação.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

from ..domain import cardio_envelope, cardio_prescription, hr_zones
from .cardio_context_service import CardioAgentContextService

logger = logging.getLogger(__name__)

# Últimas recomendações no contexto (memória adaptativa) e o corte do markdown
# das recomendações antigas (v2, sem JSON).
PAST_RECOMMENDATIONS_LIMIT = 5
LEGACY_MARKDOWN_MAX_CHARS = 400

WELLNESS_KEYS = ("wellness_date", "fitness_ctl", "fatigue_atl", "form_tsb", "ramp_rate", "hrv_ms", "resting_hr_bpm")
SLEEP_KEYS = ("local_date", "duration_hours", "deep_sleep_hours", "rem_sleep_hours", "awake_count", "notes")
# M99: EPOC/PAI de 7 dias ESTIMADOS (motor `domain/cardio_load.py`), sem a versão por linha.
EFFORT_KEYS = ("local_date", "epoc_7d_estimated", "pai_7d_estimated")
RECOVERY_KEYS = ("readiness_status", "recovery_pct", "remaining_hours")

REALIZED_CARDIO_SQL = """
SELECT id, activity_date, start_time, name, sport_type, moving_time_seconds, avg_hr_bpm, max_hr_bpm,
       training_load, hr_zone_seconds
FROM intervals_activities
WHERE user_id = %s AND activity_date BETWEEN %s AND %s
ORDER BY activity_date ASC, start_time ASC, id ASC
"""

PLANS_IN_HORIZON_SQL = """
SELECT id, plan_date, template_slot_sequence
FROM workout_plans
WHERE user_id = %s AND template_id = %s AND template_slot_sequence IS NOT NULL
  AND plan_date BETWEEN %s AND %s
ORDER BY id ASC
"""

PLAN_BY_ID_SQL = """
SELECT id, plan_date, template_id, template_slot_sequence
FROM workout_plans
WHERE id = %s AND user_id = %s
"""

PAST_RECOMMENDATIONS_SQL = """
SELECT id, reference_date, generated_at, prompt_version, recommendation_json, recommendation_markdown
FROM cardio_recommendations
WHERE user_id = %s
ORDER BY generated_at DESC, id DESC
LIMIT %s
"""

# Antes da migração 098 (sem `prompt_version`/`recommendation_json`).
PAST_RECOMMENDATIONS_LEGACY_SQL = """
SELECT id, reference_date, generated_at, recommendation_markdown
FROM cardio_recommendations
WHERE user_id = %s
ORDER BY generated_at DESC, id DESC
LIMIT %s
"""

WELLNESS_SQL = """
SELECT wellness_date, fitness_ctl, fatigue_atl, form_tsb, ramp_rate, hrv_ms, resting_hr_bpm
FROM intervals_wellness
WHERE user_id = %s AND wellness_date >= %s
ORDER BY wellness_date ASC
"""

SLEEP_SQL = """
SELECT local_date, duration_hours, deep_sleep_hours, rem_sleep_hours, awake_count, notes
FROM sleep_metrics
WHERE user_id = %s AND local_date >= %s
ORDER BY local_date ASC, id ASC
"""



def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _minutes(seconds: Any) -> Optional[float]:
    try:
        return round(float(seconds) / cardio_prescription.SECONDS_PER_MINUTE, 1)
    except (TypeError, ValueError):
        return None


class CardioAgentContextV3Service(CardioAgentContextService):
    """Contexto compacto do `cardio-agent-v3` (ver o docstring do módulo)."""

    # -- leituras simples (IntelligenceServiceBase.fetch_all) ------------------

    def _strength(self, since: date, user_id: int) -> list[dict[str, Any]]:
        rows = self._get_recent_workouts(days=0, user_id=user_id, since=since)
        out = []
        for row in rows:
            out.append(
                {
                    "date": _iso(row.get("local_date")),
                    "activity_name": row.get("activity_name"),
                    "muscle_groups": row.get("muscle_groups") or [],
                    "sets": row.get("sets_count"),
                    "volume_kg": row.get("volume_kg"),
                    "avg_rpe": row.get("avg_rpe"),
                    "minutes": _minutes(row.get("duration_seconds")),
                }
            )
        return out

    def _recovery(self, user_id: int) -> list[dict[str, Any]]:
        items = self.muscle_recovery_service.get_recovery_overview(user_id=user_id)
        return [
            {"muscle_group": item.get("muscle_group_name") or item.get("muscle_group"), **_pick(item, RECOVERY_KEYS)}
            for item in items or []
        ]

    def _rows(self, sql: str, user_id: int, since: date, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        return [_pick(row, keys) for row in self.fetch_all(sql, (int(user_id), since.isoformat()))]

    def _effort_estimates(self, user_id: int, since: date, today: date) -> list[dict[str, Any]]:
        """EPOC/PAI de 7 dias estimados de `since` a `today`, só os dias com
        valor, em ordem crescente (M99). O número vem pronto do motor."""
        from ..effort_estimates import load_estimates

        estimates = load_estimates(self.fetch_all, int(user_id), since, today)
        return [_pick(e.as_dict(), EFFORT_KEYS) for e in estimates if e.epoc_7d_estimated is not None]

    def _past_recommendations(self, user_id: int) -> list[dict[str, Any]]:
        try:
            rows = self.fetch_all(PAST_RECOMMENDATIONS_SQL, (int(user_id), PAST_RECOMMENDATIONS_LIMIT))
        except Exception as exc:  # noqa: BLE001 - só "coluna inexistente" (antes da 098) cai na leitura legada
            if "unknown column" not in str(exc).lower():
                raise
            rows = self.fetch_all(PAST_RECOMMENDATIONS_LEGACY_SQL, (int(user_id), PAST_RECOMMENDATIONS_LIMIT))
        out = []
        for row in rows:
            entry: dict[str, Any] = {
                "id": row.get("id"),
                "reference_date": _iso(row.get("reference_date")),
                "generated_at": row.get("generated_at"),
            }
            payload = _json_value(row.get("recommendation_json"))
            if isinstance(payload, dict) and payload.get("sessions") is not None:
                entry["prompt_version"] = row.get("prompt_version")
                entry["sessions"] = [
                    {"date": s.get("date"), "text": s.get("prescription_text")} for s in payload.get("sessions") or []
                ]
            else:
                markdown = str(row.get("recommendation_markdown") or "")
                entry["markdown_excerpt"] = markdown[:LEGACY_MARKDOWN_MAX_CHARS]
            out.append(entry)
        return out

    # -- leituras com cursor (zonas, Intervals, aderência, plano) -------------

    def _with_cursor(self, fn, default):
        from ..db import transaction

        try:
            with transaction() as conn:
                with conn.cursor() as cur:
                    return fn(cur)
        except Exception:  # noqa: BLE001 - bloco acessório: degrada sem derrubar a recomendação
            logger.warning("cardio_context_v3: bloco %s indisponível", getattr(fn, "__name__", "?"), exc_info=True)
            return default

    def _zones(self, user_id: int) -> dict[str, Any]:
        from .. import athlete_hr_zones

        def zones(cur):
            return athlete_hr_zones.zones_payload(athlete_hr_zones.load_athlete_zones(cur, user_id))

        return self._with_cursor(zones, athlete_hr_zones.zones_payload(None))

    def _realized_cardio(self, user_id: int, date_from: date, date_to: date) -> list[dict[str, Any]]:
        from .. import intervals_activity_kind, intervals_store

        def realized(cur):
            cur.execute(REALIZED_CARDIO_SQL, (int(user_id), date_from.isoformat(), date_to.isoformat()))
            out = []
            for row in cur.fetchall() or []:
                row = self._jsonable_row(dict(row))
                if not intervals_activity_kind.is_cardio(row.get("sport_type"), row.get("name")):
                    continue
                zone_seconds = hr_zones.app_zone_seconds(intervals_store.json_list(row.get("hr_zone_seconds")))
                out.append(
                    {
                        "date": _iso(row.get("activity_date")),
                        "sport_type": row.get("sport_type"),
                        "activity_name": row.get("name"),
                        "minutes": _minutes(row.get("moving_time_seconds")),
                        "avg_hr_bpm": row.get("avg_hr_bpm"),
                        "max_hr_bpm": row.get("max_hr_bpm"),
                        "training_load": row.get("training_load"),
                        "zone_minutes": (
                            {hr_zones.zone_label(z): _minutes(s) for z, s in zip(hr_zones.HR_ZONE_NUMBERS, zone_seconds)}
                            if zone_seconds is not None
                            else None
                        ),
                        "intense": cardio_envelope.is_high_intensity(zone_seconds),
                    }
                )
            return out

        return self._with_cursor(realized, [])

    def _adherence(self, user_id: int, date_from: date, date_to: date) -> dict[str, Any]:
        from .. import cardio_adherence_service

        def adherence(cur):
            data = cardio_adherence_service.build_adherence(cur, user_id, date_from, date_to)
            sessions = []
            for session in data.get("sessions") or []:
                evaluation = session.get("adherence") or {}
                zones = evaluation.get("zones") or {}
                sessions.append(
                    {
                        "date": session.get("local_date"),
                        "planned": evaluation.get("prescription_text"),
                        "mode": evaluation.get("mode"),
                        "status": evaluation.get("status"),
                        "time_completion_pct": evaluation.get("time_completion_pct"),
                        "zone_completion_pct": zones.get("target_zone_completion_pct", zones.get("high_zone_completion_pct")),
                    }
                )
            return {"sessions": sessions, "weeks": data.get("weeks") or []}

        return self._with_cursor(adherence, {"sessions": [], "weeks": []})

    def _plan(
        self, user_id: int, days: list[date], workout_plan_id: Optional[int]
    ) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
        """(Modelo ativo, um dia do plano por data de `days`)."""
        from ..template_assignments import resolve_active_template_id
        from ..workout_plan_templates import _get_template
        from ..workout_plan_templates_engine import resolve_next_slot

        empty = [{"date": d.isoformat(), "weekday": d.isoweekday(), "source": cardio_envelope.SOURCE_NONE,
                  "muscle_groups": [], "planned_cardio": []} for d in days]
        try:
            template_id = resolve_active_template_id(user_id)
            template = _get_template(template_id) if template_id else None
        except Exception:  # noqa: BLE001 - sem Modelo legível o agente segue sem plano
            logger.warning("cardio_context_v3: Modelo ativo indisponível", exc_info=True)
            template = None
        if not template or not template.get("slots"):
            return None, empty

        def plans(cur):
            cur.execute(PLANS_IN_HORIZON_SQL, (int(user_id), int(template["id"]), days[0].isoformat(), days[-1].isoformat()))
            by_date: dict[date, int] = {}
            for row in cur.fetchall() or []:
                day = date.fromisoformat(_iso(row["plan_date"]))
                by_date[day] = int(row["template_slot_sequence"])  # a ficha mais recente da data vence
            if workout_plan_id:
                cur.execute(PLAN_BY_ID_SQL, (int(workout_plan_id), int(user_id)))
                row = cur.fetchone()
                if row and row.get("plan_date") and row.get("template_slot_sequence") is not None and (
                    row.get("template_id") == template["id"]
                ):
                    by_date[date.fromisoformat(_iso(row["plan_date"]))] = int(row["template_slot_sequence"])
            group_ids = {
                int(item["muscle_group_id"])
                for slot in template["slots"]
                for item in slot.get("items") or []
                if item.get("muscle_group_id")
            }
            names: dict[int, str] = {}
            if group_ids:
                placeholders = ",".join(["%s"] * len(group_ids))
                cur.execute(f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})", tuple(sorted(group_ids)))
                names = {int(row["id"]): row["name"] for row in cur.fetchall() or []}
            return by_date, names

        by_date, names = self._with_cursor(plans, ({}, {}))
        try:
            next_slot = resolve_next_slot(template, user_id)
        except Exception:  # noqa: BLE001 - sem a continuação, a sequência começa no 1º slot
            next_slot = None
        projected = cardio_envelope.project_plan_slots(
            template["slots"], days, next_sequence=(next_slot or {}).get("sequence"), plans_by_date=by_date
        )
        out = []
        for day, slot, source in projected:
            entry: dict[str, Any] = {"date": day.isoformat(), "weekday": day.isoweekday(), "source": source}
            if slot is None:
                out.append({**entry, "muscle_groups": [], "planned_cardio": []})
                continue
            groups: list[str] = []
            for item in slot.get("items") or []:
                name = names.get(int(item["muscle_group_id"])) if item.get("muscle_group_id") else None
                if name and name not in groups:
                    groups.append(name)
            planned = [
                {
                    "item_id": item.get("id"),
                    "item_order": item.get("item_order"),
                    "text": cardio_prescription.describe(item),
                    **{k: v for k, v in cardio_prescription.normalize_fields(item).items() if v is not None},
                }
                for item in slot.get("items") or []
                if cardio_prescription.is_cardio_item(item)
            ]
            out.append(
                {
                    **entry,
                    "slot_id": slot.get("id"),
                    "slot_sequence": slot.get("sequence"),
                    "slot_name": slot.get("name"),
                    "slot_type": slot.get("slot_type"),
                    "is_rest_day": slot.get("slot_type") == cardio_envelope.REST_SLOT_TYPE,
                    "muscle_groups": groups,
                    "planned_cardio": planned,
                }
            )
        version = template.get("current_version") or {}
        plan = {"template_id": template.get("id"), "template_name": template.get("name"), "template_version_id": version.get("id")}
        return plan, out

    # -- montagem --------------------------------------------------------------

    def build_context_v3(
        self,
        days: int = 14,
        user_id: Optional[int] = None,
        *,
        today: Optional[date] = None,
        reference_date: Optional[date] = None,
        workout_plan_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Contexto do `cardio-agent-v3`. `today` (opcional) congela o "hoje"
        do usuário (coleta de casos do eval); `reference_date` (>= hoje) é o 1º
        dia do horizonte (padrão: hoje); `workout_plan_id` ancora a ficha do
        pedido no seu dia."""
        if user_id is None:
            raise ValueError("cardio-agent-v3 exige user_id")
        now_time = None if today else self._user_now(user_id).strftime("%H:%M")
        today = today or self._user_today(user_id)
        start = reference_date if reference_date and reference_date >= today else today
        since = today - timedelta(days=int(days))
        horizon = list(cardio_envelope.horizon_dates(start))
        plan, plan_days = self._plan(user_id, [*horizon, horizon[-1] + timedelta(days=1)], workout_plan_id)
        strength = self._strength(since, user_id)
        done_today = [w for w in strength if w["date"] == today.isoformat()]
        if done_today and plan_days and plan_days[0]["date"] == today.isoformat():
            plan_days[0]["strength_done_today"] = [
                {"activity_name": w["activity_name"], "muscle_groups": w["muscle_groups"]} for w in done_today
            ]
        context = {
            "today": today.isoformat(),
            "days": int(days),
            "horizon": {"date_from": horizon[0].isoformat(), "date_to": horizon[-1].isoformat()},
            "athlete_zones": self._zones(user_id),
            "plan": plan,
            "plan_days": plan_days[:-1],
            "plan_day_after_horizon": plan_days[-1],
            "recent_workouts": strength,
            "muscle_recovery": self._recovery(user_id),
            "realized_cardio": self._realized_cardio(user_id, since, today),
            "cardio_adherence": self._adherence(user_id, since, today),
            "intervals_wellness": self._rows(WELLNESS_SQL, user_id, since, WELLNESS_KEYS),
            "sleep_metrics": self._rows(SLEEP_SQL, user_id, since, SLEEP_KEYS),
            "effort_metrics_estimated": self._effort_estimates(user_id, since, today),
            "past_recommendations": self._past_recommendations(user_id),
        }
        if now_time:
            # Hora na parede do usuario (a Lambda roda em UTC).
            context["now_time"] = now_time
        return context


__all__ = ["CardioAgentContextV3Service", "LEGACY_MARKDOWN_MAX_CHARS", "PAST_RECOMMENDATIONS_LIMIT"]

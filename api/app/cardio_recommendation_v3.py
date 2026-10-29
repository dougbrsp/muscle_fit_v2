"""Contrato da recomendação de cardio estruturada (`cardio-agent-v3`, M98 C3a).

Três responsabilidades, nenhuma numérica de domínio (os números são dos donos
`cardio_prescription`, `cardio_envelope` e `hr_zones`):

1. `llm_schema()`: o JSON Schema (strict) que a OpenAI é obrigada a seguir.
   Enum de modo vem do dono; limites de campo NÃO vão no schema (o dono valida).
2. `process_llm_output()`: valida cada sessão pelo dono (`clamp_fields` +
   `validate`), reaplica o envelope (`cardio_envelope.apply_envelope`), anexa o
   vínculo com o Modelo (`plan_link`) e monta o JSON persistido em
   `cardio_recommendations.recommendation_json` (migração 098). Nada some sem
   registro: corte e correção vão para `rejected`/`adjustments` com o código.
3. `render_markdown()`: o markdown legado (`recommendation_markdown`, NOT NULL)
   gerado do JSON, para a tela atual e o histórico continuarem legíveis.

Contrato do vínculo com o Modelo (`plan_link`, consumido pela C3b "aplicar no
Modelo"; onde a C3b grava está no mapa, §69.4, e no PR da C3a):

    {
      "template_id": 36,             # Modelo ativo na geração
      "template_version_id": 120,    # versão lida (a C3b confere com a vigente)
      "slot_id": 801,                # id do slot NAQUELA versão (muda a cada versão)
      "slot_sequence": 3,            # chave estável do slot dentro do Modelo
      "slot_name": "Legs - SAB",
      "action": "replace" | "add",   # substitui o cardio do slot ou acrescenta um item
      "item_id": 5012 | null,        # item de cardio substituído (id naquela versão)
      "item_order": 4 | null,        # posição do item substituído no slot
      "source": "ficha" | "weekday" | "sequence"
    }

A C3b localiza o slot por `slot_sequence` (estável entre versões) e, em
`replace`, o item de cardio por `item_order` (com `item_id` como conferência na
mesma versão); `add` acrescenta um item `item_kind='cardio'` no fim do slot.
Dia sem slot do Modelo -> `plan_link = null` (nada a aplicar).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from .domain import cardio_envelope, cardio_prescription
from .domain.cardio_prescription import CardioPrescriptionError

SCHEMA_VERSION = 1
SCHEMA_NAME = "cardio_recommendation_v3"
CONFIDENCE_VALUES = ("low", "medium", "high")
CONFIDENCE_LABELS = {"low": "Baixo", "medium": "Médio", "high": "Alto"}

ACTION_REPLACE = "replace"
ACTION_ADD = "add"

# Tetos de TEXTO da saída (apresentação, não regra de domínio).
RATIONALE_MAX_CHARS = 600
SIGNALS_MAX_ITEMS = 8
SIGNAL_MAX_CHARS = 120
SUMMARY_MAX_CHARS = 1500

REJECT_INVALID_DATE = "invalid_date"
REJECT_INVALID_PRESCRIPTION = "invalid_prescription"
ADJUST_CLAMPED = "clamped"

# Campos numéricos da prescrição no schema (o modo vai em `cardio_mode`).
_NUMERIC_FIELDS = tuple(f for f in cardio_prescription.PRESCRIPTION_FIELDS if f != cardio_prescription.MODE_FIELD)

WEEKDAY_LABELS = {1: "seg", 2: "ter", 3: "qua", 4: "qui", 5: "sex", 6: "sáb", 7: "dom"}

CODE_LABELS = {
    REJECT_INVALID_DATE: "data inválida",
    REJECT_INVALID_PRESCRIPTION: "prescrição inválida",
    ADJUST_CLAMPED: "campo ajustado ao limite",
    cardio_envelope.ADJ_OUTSIDE_HORIZON: "fora do período",
    cardio_envelope.ADJ_DAY_LIMIT: "mais de uma sessão no dia",
    cardio_envelope.ADJ_HIIT_VETOED: "HIIT vetado, trocado por Normal",
    cardio_envelope.ADJ_WEEKLY_HIIT_CAP: "teto semanal de HIIT, trocado por Normal",
    cardio_envelope.ADJ_ZONE_CAPPED: "zona rebaixada para a máxima do dia",
    cardio_envelope.ADJ_DURATION_CAPPED: "tempo limitado ao teto do contínuo",
    cardio_envelope.ADJ_WEEKLY_SESSIONS_CAP: "teto semanal de sessões",
    cardio_envelope.ADJ_WEEKLY_MINUTES_CAP: "teto semanal de minutos",
}
REASON_LABELS = {
    cardio_envelope.REASON_LEG_DAY: "dia de perna",
    cardio_envelope.REASON_EVE_OF_LEG_DAY: "véspera de perna",
    cardio_envelope.REASON_TSB_LOW: "TSB baixo",
    cardio_envelope.REASON_TSB_HIGH_RISK: "TSB em alto risco",
    cardio_envelope.REASON_HRV_DROP: "queda de HRV",
    cardio_envelope.REASON_RESTING_HR_RISE: "FC de repouso alta",
    cardio_envelope.REASON_LEGS_NOT_RECOVERED: "perna sem recuperação",
}


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


def llm_schema() -> dict[str, Any]:
    """JSON Schema strict da saída do agente."""
    session_properties: dict[str, Any] = {
        "date": {"type": "string"},
        cardio_prescription.MODE_FIELD: {"type": "string", "enum": list(cardio_prescription.CARDIO_MODE_VALUES)},
        **{name: _nullable("integer") for name in _NUMERIC_FIELDS},
        "rationale": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
    }
    session = {
        "type": "object",
        "additionalProperties": False,
        "required": list(session_properties),
        "properties": session_properties,
    }
    properties = {
        "summary": {"type": "string"},
        "objective": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE_VALUES)},
        "confidence_reason": {"type": "string"},
        "sessions": {"type": "array", "items": session},
    }
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _as_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def validate_sessions(raw_sessions: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    """Sessões do LLM -> (válidas, rejeitadas, corrigidas). Válida: data ISO e
    prescrição aceita por `cardio_prescription.validate` depois de
    `clamp_fields` (número fora do limite vai ao limite e é registrado)."""
    valid: list[dict] = []
    rejected: list[dict] = []
    corrected: list[dict] = []
    for raw in raw_sessions or ():
        day = _as_date(raw.get("date"))
        if day is None:
            rejected.append({"date": _text(raw.get("date"), 32), "code": REJECT_INVALID_DATE, "detail": None})
            continue
        prescription = {name: raw.get(name) for name in cardio_prescription.PRESCRIPTION_FIELDS}
        try:
            fixed, clamped = cardio_prescription.clamp_fields(prescription)
        except CardioPrescriptionError as exc:
            rejected.append({"date": day.isoformat(), "code": REJECT_INVALID_PRESCRIPTION, "detail": str(exc)})
            continue
        error = cardio_prescription.validation_error(fixed)
        if error:
            rejected.append({"date": day.isoformat(), "code": REJECT_INVALID_PRESCRIPTION, "detail": error})
            continue
        if clamped:
            corrected.append({"date": day.isoformat(), "action": "adjusted", "code": ADJUST_CLAMPED, "detail": ",".join(clamped)})
        signals = [_text(s, SIGNAL_MAX_CHARS) for s in (raw.get("signals") or [])[:SIGNALS_MAX_ITEMS]]
        valid.append(
            {
                "date": day.isoformat(),
                "prescription": fixed,
                "rationale": _text(raw.get("rationale"), RATIONALE_MAX_CHARS),
                "signals": [s for s in signals if s],
            }
        )
    return valid, rejected, corrected


def plan_link(day: Optional[Mapping[str, Any]], plan: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Vínculo da sessão com o Modelo (contrato no docstring do módulo)."""
    if not plan or not day or day.get("slot_sequence") is None:
        return None
    planned = list(day.get("planned_cardio") or [])
    first = planned[0] if planned else None
    return {
        "template_id": plan.get("template_id"),
        "template_version_id": plan.get("template_version_id"),
        "slot_id": day.get("slot_id"),
        "slot_sequence": day.get("slot_sequence"),
        "slot_name": day.get("slot_name"),
        "action": ACTION_REPLACE if first else ACTION_ADD,
        "item_id": first.get("item_id") if first else None,
        "item_order": first.get("item_order") if first else None,
        "source": day.get("source"),
    }


def _final_prescription(prescription: Mapping[str, Any]) -> dict[str, Any]:
    """Só o modo e os campos do modo (forma da coluna, sem os do outro modo)."""
    fields = cardio_prescription.normalize_fields({**prescription, "item_kind": cardio_prescription.CARDIO_ITEM_KIND})
    return {key: value for key, value in fields.items() if value is not None}


def process_llm_output(
    data: Mapping[str, Any],
    envelope: cardio_envelope.Envelope,
    context: Mapping[str, Any],
    *,
    prompt_version: str,
) -> dict[str, Any]:
    """Saída crua do LLM -> `recommendation_json` (validada e com o envelope)."""
    valid, rejected, corrected = validate_sessions(data.get("sessions") or [])
    accepted, envelope_log = cardio_envelope.apply_envelope(valid, envelope)
    plan = context.get("plan")
    days = {day.get("date"): day for day in context.get("plan_days") or []}
    adjustments = corrected + [a for a in envelope_log if a["action"] == cardio_envelope.ACTION_ADJUSTED]
    rejected = rejected + [a for a in envelope_log if a["action"] == cardio_envelope.ACTION_REJECTED]
    sessions = []
    for session in accepted:
        prescription = _final_prescription(session["prescription"])
        day = days.get(session["date"])
        sessions.append(
            {
                "date": session["date"],
                "weekday": date.fromisoformat(session["date"]).isoweekday(),
                "prescription": prescription,
                "prescription_text": cardio_prescription.describe(prescription),
                "prescription_short": cardio_prescription.describe_short(prescription),
                "total_seconds": int(math.floor(cardio_prescription.total_seconds(prescription) + 0.5)),
                "rationale": session["rationale"],
                "signals": session["signals"],
                "plan_link": plan_link(day, plan),
                "adjustments": [a["code"] for a in adjustments if a.get("date") == session["date"]],
            }
        )
    confidence = data.get("confidence") if data.get("confidence") in CONFIDENCE_VALUES else None
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": prompt_version,
        "reference_date": (context.get("horizon") or {}).get("date_from"),
        "horizon": context.get("horizon"),
        "summary": _text(data.get("summary"), SUMMARY_MAX_CHARS),
        "objective": _text(data.get("objective"), SUMMARY_MAX_CHARS),
        "confidence": confidence,
        "confidence_reason": _text(data.get("confidence_reason"), RATIONALE_MAX_CHARS),
        "sessions": sessions,
        "rejected": rejected,
        "adjustments": adjustments,
        "envelope": envelope.to_dict(),
        "plan": plan,
    }


def _day_label(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day.strftime('%d/%m')} ({WEEKDAY_LABELS[day.isoweekday()]})"


def _minutes_label(seconds: int) -> str:
    return str(cardio_prescription.minutes_from_seconds(seconds))


def _note_detail_label(code: Any, detail: Any) -> Optional[str]:
    if not detail:
        return None
    if code == cardio_envelope.ADJ_HIIT_VETOED:
        return ", ".join(REASON_LABELS.get(part, part) for part in str(detail).split(","))
    return str(detail)


def describe_notes(recommendation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ajustes e cortes do motor (`adjustments` + `rejected`) com rótulos
    legíveis: o que o markdown e a tela (M98 C3b) mostram."""
    out = []
    for note in [*(recommendation.get("adjustments") or []), *(recommendation.get("rejected") or [])]:
        code = note.get("code")
        out.append(
            {
                "date": note.get("date"),
                "date_label": _day_label(note["date"]) if _as_date(note.get("date")) else (note.get("date") or "-"),
                "action": note.get("action"),
                "code": code,
                "label": CODE_LABELS.get(code, code),
                "detail_label": _note_detail_label(code, note.get("detail")),
            }
        )
    return out


def _session_view(session: Mapping[str, Any], *, can_apply: bool) -> dict[str, Any]:
    link = session.get("plan_link")
    return {
        "date": session.get("date"),
        "date_label": _day_label(session["date"]) if _as_date(session.get("date")) else session.get("date"),
        "prescription_text": session.get("prescription_text"),
        "prescription_short": session.get("prescription_short"),
        "total_minutes_label": cardio_prescription.minutes_label(session.get("total_seconds")),
        "rationale": session.get("rationale"),
        "signals": list(session.get("signals") or []),
        "adjustment_labels": [CODE_LABELS.get(code, code) for code in session.get("adjustments") or []],
        "slot_name": (link or {}).get("slot_name"),
        "plan_action": (link or {}).get("action"),
        "applicable": bool(link) and can_apply,
    }


def next_session(recommendation: Optional[Mapping[str, Any]], today: date) -> Optional[dict[str, Any]]:
    """Próxima sessão ainda VÁLIDA de uma recomendação estruturada (M98 C4,
    dashboard de cardio): a de menor data `>= today` (dia local do usuário).
    Sessão passada não é "próxima"; recomendação antiga (sem JSON) ou sem
    sessão futura = None. Mesma forma de exibição de `build_view`, sem
    `applicable` (o dashboard só mostra; aplicar é na tela do agente)."""
    if not recommendation:
        return None
    upcoming = [
        (day, session)
        for session in recommendation.get("sessions") or []
        if (day := _as_date(session.get("date"))) is not None and day >= today
    ]
    if not upcoming:
        return None
    upcoming.sort(key=lambda pair: pair[0])
    view = _session_view(upcoming[0][1], can_apply=False)
    view.pop("applicable", None)
    return view


def build_view(recommendation: Optional[Mapping[str, Any]], *, can_apply: bool) -> Optional[dict[str, Any]]:
    """Recomendação estruturada pronta para EXIBIR (M98 C3b, `GET
    /cardio-recommendations/{id}`): rótulos de data, tempo total em minutos,
    códigos do motor com rótulo e se a sessão pode ir para o Modelo. A tela
    não calcula nem traduz código. `can_apply` é a regra de papel de quem
    edita o Modelo, decidida pela rota. Recomendação antiga (sem JSON) = None."""
    if not recommendation:
        return None
    sessions = [_session_view(session, can_apply=can_apply) for session in recommendation.get("sessions") or []]
    confidence = recommendation.get("confidence")
    return {
        "summary": recommendation.get("summary"),
        "objective": recommendation.get("objective"),
        "confidence_label": CONFIDENCE_LABELS.get(confidence or "", None),
        "confidence_reason": recommendation.get("confidence_reason"),
        "sessions": sessions,
        "notes": describe_notes(recommendation),
        "plan": recommendation.get("plan"),
        "can_apply": can_apply,
    }


def render_markdown(recommendation: Mapping[str, Any]) -> str:
    """Markdown legado gerado do JSON (as seções da v2 que a tela lê)."""
    lines = ["## Diagnóstico", recommendation.get("summary") or "-", "", "## Objetivo do cardio",
             recommendation.get("objective") or "-", "", "## Sessões recomendadas"]
    sessions = recommendation.get("sessions") or []
    if not sessions:
        lines.append("Nenhum cardio adicional recomendado para o período.")
    for session in sessions:
        line = (
            f"- **{_day_label(session['date'])}**: {session['prescription_text']} "
            f"({_minutes_label(session['total_seconds'])} min no total)."
        )
        if session.get("rationale"):
            line += f" {session['rationale']}"
        lines.append(line)
    notes = describe_notes(recommendation)
    if notes:
        lines += ["", "## Ajustes do motor"]
        for note in notes:
            lines.append(f"- {note['date_label']}: {note['label']}" + (f" ({note['detail_label']})" if note["detail_label"] else ""))
    confidence = CONFIDENCE_LABELS.get(recommendation.get("confidence") or "", "-")
    lines += ["", "## Grau de confiança", f"{confidence}. {recommendation.get('confidence_reason') or ''}".strip()]
    return "\n".join(lines).strip() + "\n"


__all__ = [
    "ACTION_ADD",
    "ACTION_REPLACE",
    "CODE_LABELS",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "build_view",
    "describe_notes",
    "llm_schema",
    "next_session",
    "plan_link",
    "process_llm_output",
    "render_markdown",
    "validate_sessions",
]

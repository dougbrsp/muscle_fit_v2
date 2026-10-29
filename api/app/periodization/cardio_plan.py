"""Cardio na geracao de Modelo por IA (M98 C5; plano
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §C5).

Este modulo NAO tem regra numerica de cardio: limites, padroes e validacao sao
de `domain/cardio_prescription.py`; interferencia com a perna, teto de HIIT,
faixa do TSB e tetos da semana sao de `domain/cardio_envelope.py`; textos de
preferencia e do HIIT sao de `domain/cardio_policy.py`. Aqui so mora a
COLAGEM com a periodizacao:

1. **Quando** o Modelo leva cardio (`cardio_requested`): o pedido explicito
   (`include_cardio` do pedido) vence; sem ele, as preferencias do usuario
   (`instructions`) precisam mencionar cardio (`CARDIO_REQUEST_RE`). O objetivo
   do Modelo (hipertrofia/forca) nao pede cardio sozinho. Sem pedido, o prompt,
   o schema e o custo sao os da v12, letra por letra, e qualquer cardio que
   aparecer e removido com registro.
2. **Contexto** (`build_cardio_context`): a faixa do TSB do Intervals.icu (a
   linha mais recente, `wellness_freshness`: velha demais = sem dado) e os
   nomes dos grupos (para o dono decidir o que e dia de perna).
3. **Prompt** (`prompt_section`): regras do cardio e do envelope com todo
   numero interpolado dos donos + o recorte compacto do RAG de cardio
   (`rag_chunking.select_periodization_cardio_rag_text`). A prosa mora no
   repositorio privado (M103 P3, `api/privado/prompts/periodization_cardio/`).
4. **Depois do LLM** (`apply_cardio`): cada prescricao passa por
   `cardio_prescription.clamp_fields` + `validate`; o envelope do Modelo e
   reaplicado (`cardio_envelope.apply_template_envelope`); o que sobra vira
   item `item_kind='cardio'` no FIM do slot (depois da forca). Corrigido ou
   removido sempre entra no relatorio (`CARDIO_ADJUSTED`/`CARDIO_REMOVED`).

O cardio entra DEPOIS de toda a cascata de forca (referencias, regras do save,
objetivo, seguranca, complemento de volume, ativacao e metodologia): volume
muscular, complemento e metodologia seguem sem enxergar cardio, como antes.

Mapa: `docs/motores/FONTES_DA_VERDADE.md` §69.7.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Callable, Mapping, Optional

from .. import prompt_store
from ..domain import cardio_envelope, cardio_policy, cardio_prescription
from ..domain.cardio_prescription import CardioPrescriptionError
from ..domain.hr_zones import HR_ZONE_MAX, HR_ZONE_MIN, zone_label

logger = logging.getLogger("musclefit.periodization")

# Campo do slot na saida da LLM (lista de prescricoes; vazia = sem cardio).
from .output_schema import CARDIO_ITEMS_FIELD  # noqa: E402 - nome unico do campo

# DONO do gatilho "quando o Modelo leva cardio" (regra nao numerica; mapa
# §69.7): o agente de cardio nao tem vocabulario paralelo (ele sempre roda).

# Mencao a cardio nas preferencias (texto ja sem acento e minusculo).
CARDIO_REQUEST_RE = re.compile(
    r"\b(cardio\w*|aerobic\w*|hiit|corrid\w*|correr|esteira|bike|bicicleta|ergometr\w*|caminhad\w*"
    r"|eliptic\w*|condicionamento|intervalad\w*|zona\s*2|z2)\b"
)

# Origem do pedido (vai para o prompt e para o contexto).
SOURCE_FLAG = "include_cardio"
SOURCE_INSTRUCTIONS = "instructions"

# Codigos do relatorio da validacao.
CARDIO_ADJUSTED = "CARDIO_ADJUSTED"
CARDIO_REMOVED = "CARDIO_REMOVED"
REASON_NOT_REQUESTED = "not_requested"
REASON_SLOT_REMOVED = "slot_removed"
REASON_INVALID = "invalid_prescription"
REASON_CLAMPED = "clamped"

# Rotulos das mensagens do relatorio (texto de tela, com acento).
_LOCAL_LABELS = {
    REASON_NOT_REQUESTED: "removido: o pedido não tinha cardio",
    REASON_SLOT_REMOVED: "removido junto com o dia",
    REASON_INVALID: "prescrição inválida, removida",
    REASON_CLAMPED: "campo ajustado ao limite",
}


def _normalize(text: Optional[str]) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.lower().split())


def cardio_requested(request: Any) -> Optional[str]:
    """Origem do pedido de cardio (`SOURCE_FLAG`/`SOURCE_INSTRUCTIONS`) ou
    None (Modelo sem cardio)."""
    flag = getattr(request, "include_cardio", None)
    if flag is not None:
        return SOURCE_FLAG if flag else None
    return SOURCE_INSTRUCTIONS if CARDIO_REQUEST_RE.search(_normalize(getattr(request, "instructions", None))) else None


def load_tsb(user_id: Optional[int]) -> Optional[float]:
    """TSB (Forma) mais recente do usuario; velho demais (`wellness_freshness`)
    ou sem dado = None (faixa desconhecida: so as regras estruturais valem).
    Reusa a leitura compartilhada de `IntelligenceServiceBase`; falha de
    integracao nunca derruba a geracao."""
    if user_id is None:
        return None
    from ..intelligence import wellness_freshness
    from ..intelligence.base import IntelligenceServiceBase

    try:
        row = IntelligenceServiceBase().get_latest_intervals_wellness(user_id=user_id)
        if not row or wellness_freshness.is_row_stale(row):
            return None
        value = row.get("form_tsb")
        return float(value) if value is not None else None
    except Exception as exc:  # noqa: BLE001 - sem TSB a geracao segue com a faixa desconhecida
        # So o tipo do erro (sem dado do usuario): sem isso, o Modelo sairia sem
        # a trava do TSB e ninguem veria.
        logger.warning("periodizacao: TSB indisponivel para o cardio (%s); faixa desconhecida", type(exc).__name__)
        return None


def build_cardio_context(
    request: Any, groups: list[Mapping[str, Any]], user_id: Optional[int], *, rag_text: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Bloco `cardio` do contexto (None = Modelo sem cardio). `signals` e a
    faixa do TSB do dono (`cardio_envelope.template_signals`)."""
    source = cardio_requested(request)
    if source is None:
        return None
    if rag_text is None:
        from ..rag_chunking import select_periodization_cardio_rag_text

        rag_text = select_periodization_cardio_rag_text()
    signals = cardio_envelope.template_signals(load_tsb(user_id))
    return {
        "source": source,
        "signals": {"tsb": signals.tsb, "band": signals.band.value},
        "group_names": {int(group["id"]): group.get("name") for group in groups if group.get("id") is not None},
        "rag_text": rag_text,
    }


def _signals(cardio: Mapping[str, Any]) -> cardio_envelope.RecoverySignals:
    return cardio_envelope.template_signals((cardio.get("signals") or {}).get("tsb"))


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

# M103 P3: a PROSA da secao mora no repositorio privado,
# `api/privado/prompts/periodization_cardio/<versao>[.parte].md`
# (`prompt_store`), com `<versao>` = `core.PROMPT_VERSION`. Aqui ficam os ramos
# (origem do pedido, faixa do TSB, RAG) e os valores, todos lidos dos donos.
PROMPT_FEATURE_DIR = "periodization_cardio"

# Origem do pedido de cardio -> fragmento do texto.
_SOURCE_PART = {
    SOURCE_FLAG: ".origem_flag",
    SOURCE_INSTRUCTIONS: ".origem_preferencias",
}


def _text(part: str, values: Optional[Mapping[str, str]] = None) -> str:
    from .context import prompt_template_version

    return prompt_store.render(PROMPT_FEATURE_DIR, f"{prompt_template_version()}{part}", values or {})


def _band_line(signals: cardio_envelope.RecoverySignals) -> str:
    band = signals.band
    tsb = _text(".tsb_sem_dado") if signals.tsb is None else cardio_policy.format_decimal_br(signals.tsb)
    if not cardio_envelope.BAND_ALLOWS_HIIT[band]:
        limit = (
            cardio_envelope.TSB_HIGH_RISK_BELOW
            if band is cardio_envelope.RecoveryBand.HIGH_RISK
            else cardio_envelope.HIIT_MIN_TSB
        )
        return _text(".banda_sem_hiit", {"tsb": tsb, "limite": f"{limit}"})
    return _text(".banda_com_hiit", {"tsb": tsb})


def prompt_section(cardio: Mapping[str, Any]) -> str:
    """Secao CARDIO do prompt v13/v14 (so com cardio pedido). Todo numero vem dos
    donos; o texto do RAG vem por ultimo."""
    signals = _signals(cardio)
    band = signals.band
    default = cardio_prescription.default_prescription
    continuous = cardio_prescription.CardioMode.CONTINUOUS
    hiit = cardio_prescription.CardioMode.HIIT
    lines = [
        _text(
            "",
            {
                "origem": _text(_SOURCE_PART.get(cardio.get("source"), _SOURCE_PART[SOURCE_INSTRUCTIONS])),
                "campo": CARDIO_ITEMS_FIELD,
                "sessoes_por_dia_max": f"{cardio_envelope.SESSIONS_PER_DAY_MAX}",
                "zona_min": f"{zone_label(HR_ZONE_MIN)}",
                "zona_max": f"{zone_label(HR_ZONE_MAX)}",
                "modo_continuo": f"{continuous.value}",
                "modo_hiit": f"{hiit.value}",
                "limites": f"{cardio_prescription.limits_text()}",
                "sessao_max_min": f"{cardio_prescription.CARDIO_SESSION_MAX_MINUTES}",
                "padrao_continuo": f"{cardio_prescription.describe(default(continuous))}",
                "padrao_hiit": f"{cardio_prescription.describe(default(hiit))}",
                "sessoes_semana_min": f"{cardio_envelope.WEEKLY_CARDIO_SESSIONS_MIN}",
                "sessoes_semana_max": f"{cardio_envelope.WEEKLY_CARDIO_SESSIONS_MAX}",
                "escada_preferencia": f"{cardio_policy.preference_ladder_text()}",
                "protocolo_hiit": f"{cardio_policy.hiit_protocol_text()}",
                "grupos_perna": ", ".join(cardio_envelope.LEG_MUSCLE_GROUPS),
                "hiit_semana_max": f"{cardio_envelope.WEEKLY_HIIT_MAX}",
                "minutos_semana_max": f"{cardio_envelope.BAND_WEEKLY_MINUTES_MAX[band]}",
                "continuo_max_min": f"{cardio_envelope.CONTINUOUS_SESSION_MAX_MINUTES}",
                "zona_maxima": f"{zone_label(cardio_envelope.BAND_MAX_ZONE[band])}",
                "banda": _band_line(signals),
            },
        )
    ]
    rag = (cardio.get("rag_text") or "").strip()
    if rag:
        lines.append(_text(".rag", {"rag": rag}))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Depois do LLM
# ---------------------------------------------------------------------------


def _labels() -> dict[str, str]:
    from ..cardio_recommendation_v3 import CODE_LABELS, REASON_LABELS

    return {**CODE_LABELS, **REASON_LABELS, **_LOCAL_LABELS}


def _message(sequence: Any, name: Any, reason: str, detail: Optional[str]) -> str:
    labels = _labels()
    where = f"Dia {sequence} ({name})" if name else f"Dia {sequence}"
    text = f"{where}: cardio — {labels.get(reason, reason)}"
    if detail:
        parts = [labels.get(part, part) for part in str(detail).split(",") if part]
        text += f" ({', '.join(parts)})"
    return text


def _strength_group_names(slot: Any, group_names: Mapping[Any, Any]) -> list[str]:
    names = {int(key): value for key, value in (group_names or {}).items()}
    return [
        names[int(item.muscle_group_id)]
        for item in slot.items
        if item.item_kind != cardio_prescription.CARDIO_ITEM_KIND
        and item.muscle_group_id is not None
        and int(item.muscle_group_id) in names
    ]


def apply_cardio(
    template: Any,
    raw_by_slot: Mapping[int, tuple[int, str, list[dict[str, Any]]]],
    cardio: Optional[Mapping[str, Any]],
    record: Callable[..., None],
) -> None:
    """Cardio da LLM -> itens `item_kind='cardio'` no fim de cada slot.

    `raw_by_slot`: `id(slot)` do `WorkoutPlanTemplateIn` -> (sequence e nome
    originais, prescricoes cruas). `record(code, **campos)` e o `auto_fix` do
    relatorio. Slot que o auto-fix estrutural removeu leva o cardio junto
    (`REASON_SLOT_REMOVED`)."""
    from ..models import WorkoutPlanTemplateSlotItemIn
    from ..template_drafts import cardio_item_payload

    def removed(sequence: Any, name: Any, reason: str, detail: Optional[str] = None) -> None:
        record(CARDIO_REMOVED, slot=sequence, slot_name=name, reason=reason, detail=detail,
               message=_message(sequence, name, reason, detail))

    def adjusted(sequence: Any, name: Any, reason: str, detail: Optional[str] = None) -> None:
        record(CARDIO_ADJUSTED, slot=sequence, slot_name=name, reason=reason, detail=detail,
               message=_message(sequence, name, reason, detail))

    current = {id(slot): slot for slot in template.slots}
    for key, (sequence, name, raws) in raw_by_slot.items():
        if raws and key not in current:
            for _raw in raws:
                removed(sequence, name, REASON_SLOT_REMOVED)
    pending = [(current[key], raws) for key, (_s, _n, raws) in raw_by_slot.items() if raws and key in current]
    if not pending:
        return
    if cardio is None:
        for slot, raws in pending:
            for _raw in raws:
                removed(slot.sequence, slot.name, REASON_NOT_REQUESTED)
        return

    sessions: list[dict[str, Any]] = []
    for slot, raws in pending:
        for raw in raws:
            prescription = {name: raw.get(name) for name in cardio_prescription.PRESCRIPTION_FIELDS}
            try:
                fixed, clamped = cardio_prescription.clamp_fields(prescription)
            except CardioPrescriptionError as exc:
                removed(slot.sequence, slot.name, REASON_INVALID, str(exc))
                continue
            error = cardio_prescription.validation_error(fixed)
            if error:
                removed(slot.sequence, slot.name, REASON_INVALID, error)
                continue
            if clamped:
                adjusted(slot.sequence, slot.name, REASON_CLAMPED, ",".join(clamped))
            sessions.append({"sequence": slot.sequence, "prescription": fixed})

    slots_info = [
        {
            "sequence": slot.sequence,
            "weekday": slot.weekday,
            "muscle_groups": _strength_group_names(slot, cardio.get("group_names") or {}),
        }
        for slot in template.slots
    ]
    accepted, log = cardio_envelope.apply_template_envelope(sessions, slots_info, _signals(cardio))
    by_sequence = {slot.sequence: slot for slot in template.slots}
    for entry in log:
        slot = by_sequence.get(entry.get("sequence"))
        name = slot.name if slot is not None else None
        if entry["action"] == cardio_envelope.ACTION_REJECTED:
            removed(entry.get("sequence"), name, entry["code"], entry.get("detail"))
        else:
            adjusted(entry.get("sequence"), name, entry["code"], entry.get("detail"))
    for session in accepted:
        slot = by_sequence[session["sequence"]]
        slot.items.append(
            WorkoutPlanTemplateSlotItemIn(**cardio_item_payload(session["prescription"], len(slot.items) + 1))
        )


__all__ = [
    "CARDIO_ADJUSTED",
    "CARDIO_ITEMS_FIELD",
    "CARDIO_REMOVED",
    "CARDIO_REQUEST_RE",
    "apply_cardio",
    "build_cardio_context",
    "cardio_requested",
    "load_tsb",
    "prompt_section",
]

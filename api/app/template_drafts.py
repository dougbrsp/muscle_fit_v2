"""Rascunho do Modelo de Treino (migracao 067).

"Salvar rascunho" grava o estado do editor em `workout_plan_template_drafts`
SEM criar versao e sem tocar slots, versao vigente ou colunas do Modelo.
"Publicar modelo" e o PUT de sempre (validacao completa + versao nova quando
ha mudanca estrutural), que apaga o rascunho NA MESMA TRANSACAO
(`discard_draft`). Reverter uma versao tambem apaga.

Regra de leitura: este modulo e as rotas do Modelo
(`workout_plan_templates.py`) sao os UNICOS lugares que citam a tabela. Motor
de geracao, dashboards, ficha e relatorios leem so a versao vigente -
`tests/unit/test_template_drafts.py` falha se alguem passar a ler o rascunho
em outro lugar.

Janela de deploy (codigo novo antes da 067 aplicada): a ausencia da tabela e
tratada como "sem rascunho" nas leituras e no descarte, para o GET e o PUT do
Modelo nunca quebrarem por causa dela. Gravar um rascunho nessa janela
devolve 503 (nao ha onde gravar).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("fitness.template_drafts")

TABLE = "workout_plan_template_drafts"

# Teto do JSON serializado: um Modelo de 8 slots com ~10 itens cada fica na
# casa de 30 KB; 512 KB so barra abuso, nunca um rascunho real.
MAX_PAYLOAD_BYTES = 512 * 1024

UPSERT_SQL = f"""
INSERT INTO {TABLE} (template_id, payload, updated_by_user_id, updated_at)
VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
ON DUPLICATE KEY UPDATE
    payload = VALUES(payload),
    updated_by_user_id = VALUES(updated_by_user_id),
    updated_at = CURRENT_TIMESTAMP
"""

SELECT_SQL = f"""
SELECT d.template_id, d.payload, d.updated_at, d.updated_by_user_id,
       u.username AS updated_by_username, u.full_name AS updated_by_full_name
FROM {TABLE} d
LEFT JOIN users u ON u.id = d.updated_by_user_id
WHERE d.template_id = %s
"""

DELETE_SQL = f"DELETE FROM {TABLE} WHERE template_id = %s"


class DraftTableMissing(Exception):
    """A 067 ainda nao foi aplicada neste banco."""


class DraftTooLarge(Exception):
    """Payload acima de `MAX_PAYLOAD_BYTES`."""


def is_missing_table_error(exc: Exception) -> bool:
    """MySQL 1146 (`Table '...workout_plan_template_drafts' doesn't exist`),
    nos dois backends (PyMySQL e Data API devolvem a mensagem do MySQL)."""
    text = str(exc)
    return TABLE in text and "doesn't exist" in text


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def serialize_payload(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise DraftTooLarge()
    return text


def save_draft(cur, template_id: int, payload: dict[str, Any], *, actor: Optional[int]) -> dict[str, Any]:
    """Grava/sobrescreve o rascunho e devolve como `get_draft` o leria."""
    text = serialize_payload(payload)
    try:
        cur.execute(UPSERT_SQL, (template_id, text, actor))
    except Exception as exc:  # noqa: BLE001 - so a tabela ausente vira erro proprio
        if is_missing_table_error(exc):
            raise DraftTableMissing() from exc
        raise
    draft = get_draft(cur, template_id)
    assert draft is not None  # acabou de ser gravado na mesma transacao
    return draft


def get_draft(cur, template_id: int) -> Optional[dict[str, Any]]:
    """`{payload, updated_at, updated_by}` ou `None` (sem rascunho, ou 067
    ainda nao aplicada)."""
    try:
        cur.execute(SELECT_SQL, (template_id,))
    except Exception as exc:  # noqa: BLE001
        if is_missing_table_error(exc):
            return None
        raise
    row = cur.fetchone()
    if not row:
        return None
    return {
        "template_id": int(row["template_id"]),
        "payload": _decode_payload(row.get("payload")),
        "updated_at": row.get("updated_at"),
        "updated_by": {
            "id": row.get("updated_by_user_id"),
            "name": row.get("updated_by_full_name") or row.get("updated_by_username") or None,
        },
    }


def discard_draft(cur, template_id: int) -> bool:
    """Apaga o rascunho (publicar, reverter, descartar, excluir). `True` se
    havia um. Tabela ausente = nao havia rascunho."""
    try:
        cur.execute(DELETE_SQL, (template_id,))
    except Exception as exc:  # noqa: BLE001
        if is_missing_table_error(exc):
            return False
        raise
    return (cur.rowcount or 0) > 0


def draft_stamps(cur, template_ids: Optional[list[int]] = None) -> dict[int, Any]:
    """`template_id -> updated_at` dos Modelos com rascunho (lista e detalhe
    mostram `has_draft`/`draft_updated_at`). Informativo: qualquer falha vira
    "sem rascunho" e um warning, nunca derruba a leitura do Modelo."""
    sql = f"SELECT template_id, updated_at FROM {TABLE}"
    params: tuple = ()
    if template_ids is not None:
        if not template_ids:
            return {}
        sql += f" WHERE template_id IN ({','.join(['%s'] * len(template_ids))})"
        params = tuple(int(t) for t in template_ids)
    try:
        cur.execute(sql, params)
        return {int(row["template_id"]): row.get("updated_at") for row in (cur.fetchall() or [])}
    except Exception as exc:  # noqa: BLE001 - leitura informativa
        if not is_missing_table_error(exc):
            logger.warning("rascunhos de Modelo indisponiveis", exc_info=True)
        return {}


def attach_draft_flags(template: dict[str, Any], stamps: dict[int, Any]) -> dict[str, Any]:
    stamp = stamps.get(int(template["id"]))
    template["has_draft"] = stamp is not None
    template["draft_updated_at"] = stamp
    return template


# ---------------------------------------------------------------------------
# "Aplicar no Modelo" da recomendação de cardio (M98 C3b)
# ---------------------------------------------------------------------------
# Decisão do usuário (2026-10-26): a recomendação NUNCA mexe na versão
# publicada. Ela grava no RASCUNHO do Modelo, e o usuário revisa e publica no
# editor. Esta parte é PURA (sem banco): recebe o payload do rascunho (ou o da
# versão vigente, quando ainda não há rascunho) e devolve o payload novo e o
# resultado de cada sessão. Quem lê e grava é `workout_plan_templates.
# apply_cardio_sessions_to_draft`, pelo `get_draft`/`save_draft` daqui.
#
# Contrato do `plan_link`: `cardio_recommendation_v3` (docstring do módulo).
# Slot por `slot_sequence` (estável entre versões). `replace` troca o 1º item
# de cardio do slot; `add` acrescenta um item de cardio no fim do slot. A
# prescrição é validada pelo dono (`cardio_prescription.validate`).

APPLY_APPLIED = "applied"
APPLY_ALREADY = "already_applied"
APPLY_REJECTED = "rejected"

CODE_REPLACED = "replaced"
CODE_ADDED = "added"
CODE_ALREADY_APPLIED = "already_applied"
CODE_NO_PLAN_LINK = "no_plan_link"
CODE_OTHER_TEMPLATE = "other_template"
CODE_SLOT_MISSING = "slot_missing"
CODE_SLOT_TAKEN = "slot_taken"
CODE_CARDIO_MISSING = "cardio_missing"
CODE_CARDIO_EDITED = "cardio_edited_in_draft"
CODE_SLOT_HAS_CARDIO = "slot_has_cardio"
CODE_INVALID_PRESCRIPTION = "invalid_prescription"
CODE_NOT_IN_RECOMMENDATION = "not_in_recommendation"

_REVIEW_HINT = "Revise no editor do Modelo (publique ou descarte o rascunho) e aplique de novo."
APPLY_MESSAGES = {
    CODE_REPLACED: "Cardio do slot {slot} substituído no rascunho.",
    CODE_ADDED: "Cardio acrescentado ao slot {slot} no rascunho.",
    CODE_ALREADY_APPLIED: "O rascunho já tem este cardio no slot {slot}.",
    CODE_NO_PLAN_LINK: "Este dia não tem slot no Modelo: não há onde aplicar.",
    CODE_OTHER_TEMPLATE: "A sessão foi gerada para outro Modelo.",
    CODE_SLOT_MISSING: "O slot {slot} não existe mais no Modelo.",
    CODE_SLOT_TAKEN: "Outra sessão desta aplicação já usa o slot {slot}.",
    CODE_CARDIO_MISSING: "O slot {slot} não tem mais item de cardio para substituir. " + _REVIEW_HINT,
    CODE_CARDIO_EDITED: "O cardio do slot {slot} já foi alterado no rascunho. " + _REVIEW_HINT,
    CODE_SLOT_HAS_CARDIO: "O slot {slot} já tem outro cardio. " + _REVIEW_HINT,
    CODE_NOT_IN_RECOMMENDATION: "Esta data não tem sessão na recomendação.",
}


def apply_result(
    date: Any,
    status: str,
    code: str,
    *,
    slot: Optional[dict[str, Any]] = None,
    link: Optional[dict[str, Any]] = None,
    message: Optional[str] = None,
) -> dict[str, Any]:
    name = (slot or {}).get("name") or (link or {}).get("slot_name")
    text = message or APPLY_MESSAGES[code].format(slot=f"“{name}”" if name else "do dia")
    return {
        "date": date,
        "status": status,
        "code": code,
        "message": text,
        "slot_sequence": (link or {}).get("slot_sequence"),
        "slot_name": name,
    }


def _cardio_items(slot: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    from .domain import cardio_prescription

    return [item for item in (slot or {}).get("items") or [] if cardio_prescription.is_cardio_item(item)]


def _slot_by_sequence(slots: Any, sequence: Any) -> Optional[dict[str, Any]]:
    if sequence is None:
        return None
    for slot in slots or []:
        if slot.get("sequence") is not None and int(slot["sequence"]) == int(sequence):
            return slot
    return None


def cardio_item_payload(prescription: dict[str, Any], item_order: int) -> dict[str, Any]:
    """Item de cardio na forma do PUT do Modelo (`WorkoutPlanTemplateSlotItemIn`):
    só o modo e os campos do modo (os do outro modo vão None, como no editor)."""
    from .domain import cardio_prescription

    fields = cardio_prescription.normalize_fields({**prescription, "item_kind": cardio_prescription.CARDIO_ITEM_KIND})
    return {
        "item_order": item_order,
        "item_kind": cardio_prescription.CARDIO_ITEM_KIND,
        "sets_count": None,
        "reps_target": None,
        "rpe_target": None,
        "prioritize": False,
        **fields,
    }


def apply_cardio_sessions(
    payload: dict[str, Any],
    sessions: list[dict[str, Any]],
    *,
    template_id: int,
    published: Optional[dict[str, Any]],
    has_draft: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Aplica as sessões (`{date, prescription, plan_link}`) ao `payload` do
    rascunho. Devolve (payload novo, resultado por sessão). Não grava nada.

    - `plan_link` nulo, de outro Modelo, slot inexistente ou prescrição
      inválida pelo dono: sessão recusada com a mensagem;
    - `replace`: o 1º cardio do slot vira a prescrição. Se o rascunho já
      alterou esse cardio (difere do publicado), é conflito: o rascunho do
      usuário nunca é sobrescrito em silêncio. Sem rascunho e na mesma versão
      da recomendação, `item_id` confere o item;
    - `add`: acrescenta no fim do slot. Se o slot já tem outro cardio, é
      conflito;
    - a mesma prescrição já no slot: `already_applied` (idempotente, nada muda);
    - duas sessões no mesmo slot na mesma aplicação: vale a 1ª (por data).

    `published` é o Modelo vigente (`_get_template`), base da conferência de
    conflito; `has_draft` diz se `payload` veio de um rascunho."""
    from .cardio_recommendation_v3 import ACTION_REPLACE
    from .domain import cardio_prescription
    from .domain.cardio_prescription import CardioPrescriptionError

    new_payload = json.loads(json.dumps(payload, default=str))
    slots = new_payload.get("slots") or []
    published_slots = (published or {}).get("slots") or []
    published_version = ((published or {}).get("current_version") or {}).get("id")
    results: list[dict[str, Any]] = []
    touched: set[int] = set()
    for session in sorted(sessions, key=lambda s: str(s.get("date") or "")):
        day = session.get("date")
        link = session.get("plan_link")
        if not link:
            results.append(apply_result(day, APPLY_REJECTED, CODE_NO_PLAN_LINK))
            continue
        if link.get("template_id") is None or int(link["template_id"]) != int(template_id):
            results.append(apply_result(day, APPLY_REJECTED, CODE_OTHER_TEMPLATE, link=link))
            continue
        prescription = session.get("prescription") or {}
        try:
            cardio_prescription.validate(prescription)
        except CardioPrescriptionError as exc:
            results.append(
                apply_result(
                    day, APPLY_REJECTED, CODE_INVALID_PRESCRIPTION, link=link, message=f"Prescrição inválida: {exc}"
                )
            )
            continue
        slot = _slot_by_sequence(slots, link.get("slot_sequence"))
        if slot is None:
            results.append(apply_result(day, APPLY_REJECTED, CODE_SLOT_MISSING, link=link))
            continue
        sequence = int(slot["sequence"])
        if sequence in touched:
            results.append(apply_result(day, APPLY_REJECTED, CODE_SLOT_TAKEN, slot=slot, link=link))
            continue
        cardio = _cardio_items(slot)
        replace = link.get("action") == ACTION_REPLACE
        candidates = cardio[:1] if replace else cardio
        if any(cardio_prescription.same_prescription(item, prescription) for item in candidates):
            touched.add(sequence)
            results.append(apply_result(day, APPLY_ALREADY, CODE_ALREADY_APPLIED, slot=slot, link=link))
            continue
        if replace:
            if not cardio:
                results.append(apply_result(day, APPLY_REJECTED, CODE_CARDIO_MISSING, slot=slot, link=link))
                continue
            published_cardio = _cardio_items(_slot_by_sequence(published_slots, sequence))
            target = cardio[0]
            if has_draft:
                conflict = not published_cardio or not cardio_prescription.same_prescription(
                    target, published_cardio[0]
                )
            else:
                conflict = (
                    published_version is not None
                    and link.get("template_version_id") == published_version
                    and link.get("item_id") is not None
                    and bool(published_cardio)
                    and published_cardio[0].get("id") is not None
                    and published_cardio[0]["id"] != link["item_id"]
                )
            if conflict:
                results.append(apply_result(day, APPLY_REJECTED, CODE_CARDIO_EDITED, slot=slot, link=link))
                continue
            replacement = cardio_item_payload(prescription, int(target.get("item_order") or 0))
            slot["items"] = [replacement if item is target else item for item in slot.get("items") or []]
            code = CODE_REPLACED
        else:
            if cardio:
                results.append(apply_result(day, APPLY_REJECTED, CODE_SLOT_HAS_CARDIO, slot=slot, link=link))
                continue
            items = list(slot.get("items") or [])
            next_order = max((int(item.get("item_order") or 0) for item in items), default=0) + 1
            slot["items"] = items + [cardio_item_payload(prescription, next_order)]
            code = CODE_ADDED
        touched.add(sequence)
        results.append(apply_result(day, APPLY_APPLIED, code, slot=slot, link=link))
    return new_payload, results

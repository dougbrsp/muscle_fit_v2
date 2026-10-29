"""Validacao de SEGURANCA dos itens de um modelo `equipment_series`
(PR-12; PLANO_20260912.md secao 6.2 passo 5 e decisao D14).

Duas perguntas, uma resposta por item:

1. a **tecnica fixa** do item e valida para o par exercicio x equipamento?
   (terminal: `training_safety.validate_technique`, que e defensiva - tecnica
   que exige falha num par sem falha permitida e invalida mesmo marcada
   compativel na matriz);
2. o **`rpe_target`** do item cabe no teto seguro do par?

**D14 - por que nao e simplesmente 422 para tudo.** Os modelos em uso hoje
foram montados antes de existir catalogo de tecnicas e `max_safe_rpe`. Se o
save passasse a rejeitar qualquer incompatibilidade, um PT que so quisesse
renomear um slot ficaria travado por um item que ele nem tocou. Entao:

- item **novo ou alterado** (mudou exercicio, equipamento, tecnica ou RPE):
  incompatibilidade e **erro 422** - a pessoa esta decidindo isso agora;
- item **legado nao tocado**: **warning**, o save passa e o modelo continua
  funcionando. A geracao trata dele (clamp de RPE, remocao da tecnica) e a
  ficha avisa.

O mesmo relatorio alimenta o campo `safety_warnings` devolvido no GET do
modelo - e a versao por-modelo do "relatorio de modelos a revisar" da secao
12.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from .domain.effective_intensity import clamp_rpe_text
from .domain.prescription import parse_rpe
from .domain.training_safety import READY, compose, readiness_for_status, resolve_training_safety_bulk, validate_technique

CODE_TECHNIQUE_INVALID = "MODEL_TECHNIQUE_INVALID"
CODE_RPE_ABOVE_CEILING = "RPE_ABOVE_SAFE_CEILING"

# Campos que definem se um item "mudou" para efeito da D14. Sao exatamente os
# que a validacao le: mexer neles e decidir de novo sobre seguranca.
SAFETY_RELEVANT_FIELDS = ("mapping_id", "equipment_id", "advanced_technique", "rpe_target")


def _as_int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _item_fingerprint(item: Mapping[str, Any]) -> tuple:
    return (
        _as_int(item.get("mapping_id")),
        _as_int(item.get("equipment_id")),
        (item.get("advanced_technique") or None),
        (str(item.get("rpe_target")).strip() if item.get("rpe_target") not in (None, "") else None),
    )


def _iter_equipment_items(slots: Iterable[Any]) -> list[tuple[Any, Mapping[str, Any]]]:
    """(slot, item) de todos os itens `item_kind='equipment'`. Aceita tanto os
    modelos Pydantic do payload quanto os dicts crus de `_get_template`."""
    result: list[tuple[Any, Mapping[str, Any]]] = []
    for slot in slots or ():
        slot_dict = slot if isinstance(slot, Mapping) else slot.model_dump()
        for item in slot_dict.get("items") or ():
            item_dict = item if isinstance(item, Mapping) else item.model_dump()
            if item_dict.get("item_kind") == "equipment":
                result.append((slot_dict, item_dict))
    return result


def has_safety_relevant_input(slots: Iterable[Any]) -> bool:
    """Existe ao menos um item com tecnica OU `rpe_target`? Sem isso nao ha o
    que validar - e o save nao toca o banco (o catalogo de tecnicas e a
    matriz de seguranca nem sao lidos)."""
    return any(
        (item.get("advanced_technique") or item.get("rpe_target") not in (None, ""))
        for _, item in _iter_equipment_items(slots)
    )


def build_safety_report(conn: Any, slots: Sequence[Any]) -> list[dict[str, Any]]:
    """Um registro por incompatibilidade encontrada, em ordem estavel
    (slot, item). Lista vazia = modelo limpo."""
    if not has_safety_relevant_input(slots):
        return []
    entries = _iter_equipment_items(slots)
    pairs = sorted(
        {
            (_as_int(item.get("mapping_id")), _as_int(item.get("equipment_id")))
            for _, item in entries
            if _as_int(item.get("mapping_id")) and _as_int(item.get("equipment_id"))
        }
    )
    if not pairs:
        return []
    safety_by_pair = resolve_training_safety_bulk(conn, pairs)
    readiness = readiness_for_status(READY)

    report: list[dict[str, Any]] = []
    for slot, item in entries:
        exercise_id = _as_int(item.get("mapping_id"))
        equipment_id = _as_int(item.get("equipment_id"))
        safety = safety_by_pair.get((exercise_id, equipment_id)) if exercise_id and equipment_id else None
        if safety is None:
            continue
        base = {
            "slot_sequence": slot.get("sequence"),
            "slot_name": slot.get("name"),
            "item_order": item.get("item_order"),
            "muscle_group_id": _as_int(item.get("muscle_group_id")),
            "mapping_id": exercise_id,
            "equipment_id": equipment_id,
            "technique": item.get("advanced_technique") or None,
            "max_safe_rpe": safety.max_safe_rpe,
            "rpe_target": item.get("rpe_target"),
        }

        technique = item.get("advanced_technique") or None
        if technique:
            validation = validate_technique(technique, safety)
            if not validation.valid:
                report.append(
                    {
                        **base,
                        "code": CODE_TECHNIQUE_INVALID,
                        "message": (
                            f"Slot '{slot.get('name')}', item {item.get('item_order')}: a técnica "
                            f"'{technique}' não é válida para este equipamento "
                            f"({_technique_reason(validation.reason, safety.max_safe_rpe)}). "
                            "Na geração da ficha ela é removida da prescrição."
                        ),
                    }
                )

        # M76 (C4): o alvo e o ULTIMO degrau da rampa (`parse_rpe`, parser
        # unico) e o texto que a geracao vai usar sai do corte UNICO
        # (`clamp_rpe_text`) - "8-9-10" num par com teto 9 vira "8-9".
        prescribed = parse_rpe(item.get("rpe_target"))
        if prescribed is not None:
            composition = compose(safety, readiness, prescribed)
            if composition.target < prescribed:
                clamped, _ = clamp_rpe_text(item.get("rpe_target"), composition.ceiling)
                report.append(
                    {
                        **base,
                        "code": CODE_RPE_ABOVE_CEILING,
                        "message": (
                            f"Slot '{slot.get('name')}', item {item.get('item_order')}: RPE alvo "
                            f"{prescribed} acima do teto seguro deste equipamento "
                            f"({composition.ceiling}). Na geração da ficha o alvo é reduzido para "
                            f"{clamped}."
                        ),
                    }
                )
    return report


def _technique_reason(reason: Optional[str], max_safe_rpe: int) -> str:
    if reason == "FAILURE_REQUIRED_NOT_ALLOWED":
        return f"exige chegar à falha, e o RPE máximo seguro aqui é {max_safe_rpe}"
    if reason == "TECHNIQUE_UNKNOWN":
        return "técnica desconhecida ou inativa no catálogo"
    return "não marcada como compatível para este par exercício × equipamento"


def split_report_by_authorship(
    report: Sequence[Mapping[str, Any]], slots: Sequence[Any], existing_slots: Optional[Sequence[Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(erros, warnings) segundo a D14.

    `existing_slots` = o modelo como esta no banco (`None` na criacao, onde
    tudo e novo). Um item conta como ALTERADO quando qualquer campo de
    `SAFETY_RELEVANT_FIELDS` difere do que estava salvo naquele
    (`slot.sequence`, `item.item_order`).
    """
    previous: dict[tuple, tuple] = {}
    for slot, item in _iter_equipment_items(existing_slots or ()):
        previous[(slot.get("sequence"), item.get("item_order"))] = _item_fingerprint(item)
    current: dict[tuple, tuple] = {}
    for slot, item in _iter_equipment_items(slots):
        current[(slot.get("sequence"), item.get("item_order"))] = _item_fingerprint(item)

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for entry in report:
        key = (entry.get("slot_sequence"), entry.get("item_order"))
        untouched = key in previous and previous[key] == current.get(key)
        (warnings if untouched else errors).append(dict(entry))
    return errors, warnings

"""M98 C5: a geracao de Modelo por IA (periodizacao) prescreve cardio.

Protege:
- QUANDO: flag do pedido > preferencias que falam de cardio; sem pedido, o
  prompt e o schema sao os da v12 e cardio que aparecer e removido com registro;
- schema: `cardio_items` por slot so com cardio pedido, com o enum do dono;
- prompt: secao de cardio com os numeros dos donos (nada escrito a mao);
- depois do LLM: dono da prescricao (`clamp_fields` + `validate`) e envelope do
  Modelo (`cardio_envelope.apply_template_envelope`): HIIT vetado no dia de
  perna e na vespera, descanso com cardio continua descanso, item de cardio no
  FIM do slot, tempo do slot com o cardio (`time_estimate`).
"""

from __future__ import annotations

import pytest

from app.domain import cardio_envelope, cardio_prescription
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import cardio_plan, validation
from app.periodization.context import build_prompt
from app.periodization.output_schema import (
    OUTPUT_SCHEMA,
    OUTPUT_SCHEMA_WITH_CARDIO,
    PeriodizationItemOut,
    output_schema,
)
from app.periodization.time_estimate import estimate_slot_seconds
from tests.support.privado import exigir

LEG, CHEST, BACK = 1, 2, 3
GROUP_NAMES = {LEG: "Quadríceps", CHEST: "Peitoral", BACK: "Costas"}
HIIT = {
    "cardio_mode": "hiit",
    "cardio_warmup_minutes": 5,
    "hiit_low_seconds": 120,
    "hiit_high_seconds": 120,
    "hiit_low_zone": 2,
    "hiit_high_zone": 4,
    "hiit_rounds": 5,
    "cardio_cooldown_minutes": 5,
    "cardio_duration_minutes": None,
    "cardio_target_zone": None,
}
NORMAL = {
    "cardio_mode": "continuous",
    "cardio_warmup_minutes": 5,
    "cardio_duration_minutes": 30,
    "cardio_target_zone": 2,
    "cardio_cooldown_minutes": 5,
    "hiit_low_seconds": None,
    "hiit_high_seconds": None,
    "hiit_low_zone": None,
    "hiit_high_zone": None,
    "hiit_rounds": None,
}


def _ctx(tsb=None):
    return {
        "source": cardio_plan.SOURCE_INSTRUCTIONS,
        "signals": {"tsb": tsb, "band": cardio_envelope.recovery_band(tsb).value},
        "group_names": dict(GROUP_NAMES),
        "rag_text": "",
    }


def _mg(group):
    return {"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": group, "priority_min_pct": 50, "priority_max_pct": 60}


def _template(*slots):
    return WorkoutPlanTemplateIn(name="x", volume_mode="percent_budget", slots=list(slots))


def _apply(template, raw_by_index, cardio):
    log = []
    raw = {
        id(template.slots[i]): (template.slots[i].sequence, template.slots[i].name, items)
        for i, items in raw_by_index.items()
    }
    cardio_plan.apply_cardio(template, raw, cardio, lambda code, **f: log.append({"code": code, **f}))
    return log


# ---------------------------------------------------------------------------
# Quando
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "instructions", "esperado"),
    [
        (True, None, cardio_plan.SOURCE_FLAG),
        (False, "quero cardio todo dia", None),
        (None, "Quero 30 min de esteira depois do treino", cardio_plan.SOURCE_INSTRUCTIONS),
        (None, "HIIT 2x por semana", cardio_plan.SOURCE_INSTRUCTIONS),
        (None, "Caminhada leve no descanso", cardio_plan.SOURCE_INSTRUCTIONS),
        (None, "Prefiro máquinas; dor no ombro", None),
        (None, None, None),
    ],
)
def test_quando_o_modelo_leva_cardio(flag, instructions, esperado):
    pedido = PeriodizationDraftIn(include_cardio=flag, instructions=instructions)
    assert cardio_plan.cardio_requested(pedido) == esperado


def test_pedido_sem_flag_continua_valido():
    assert PeriodizationDraftIn().include_cardio is None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _slot_props(schema):
    return schema["properties"]["template"]["properties"]["slots"]["items"]["properties"]


def test_schema_sem_cardio_e_o_da_v12_e_com_cardio_ganha_cardio_items():
    assert "cardio_items" not in _slot_props(OUTPUT_SCHEMA)
    assert output_schema(False) is OUTPUT_SCHEMA
    assert output_schema(True) is OUTPUT_SCHEMA_WITH_CARDIO
    slot = OUTPUT_SCHEMA_WITH_CARDIO["properties"]["template"]["properties"]["slots"]["items"]
    assert "cardio_items" in slot["required"]
    item = slot["properties"]["cardio_items"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == set(cardio_prescription.PRESCRIPTION_FIELDS)
    assert set(item["required"]) == set(item["properties"])
    assert item["properties"]["cardio_mode"]["enum"] == list(cardio_prescription.CARDIO_MODE_VALUES)
    # Item de forca nao ganhou campo de cardio (custo de saida).
    assert "cardio_mode" not in _slot_props(OUTPUT_SCHEMA_WITH_CARDIO)["items"]["items"]["properties"]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _contexto(cardio=None):
    ctx = {
        "request": {"volume_mode": "percent_budget", "days_per_cycle": 4, "volume_counting_mode": "primary_only"},
        "muscle_groups": [],
        "targets": [],
        "methodology_ranges": [],
        "methodology_targets": [],
        "techniques": [],
        "gym_catalog": {},
        "rag_text": "RAG",
        "instructions": "",
    }
    if cardio is not None:
        ctx["cardio"] = cardio
    return ctx


def test_prompt_sem_cardio_nao_tem_secao_de_cardio(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cardio_plan, "prompt_section", lambda cardio: chamadas.append(cardio) or "")
    prompt = build_prompt(_contexto())
    assert chamadas == []
    exigir("cardio_items" not in prompt, "sem cardio_items no prompt sem cardio")


# A prosa da secao (rotulos dos numeros, a linha do TSB) e travada no privado
# (`api/privado/tests/unit/test_periodization_prompt.py`). Aqui: os VALORES dos
# donos e a posicao da secao, sem por o texto do prompt no log.
def test_prompt_com_cardio_interpola_os_donos():
    cardio = {**_ctx(), "rag_text": "TRECHO DO RAG"}
    contexto = _contexto(cardio)
    prompt = build_prompt(contexto)
    secao = cardio_plan.prompt_section(cardio)
    exigir(secao in prompt, "secao de cardio no prompt")
    exigir(f"`{cardio_plan.CARDIO_ITEMS_FIELD}`" in secao, "campo cardio_items na secao")
    for rotulo, valor in (
        ("HIIT por semana", cardio_envelope.WEEKLY_HIIT_MAX),
        ("minutos do Normal", cardio_envelope.CONTINUOUS_SESSION_MAX_MINUTES),
        ("limites", cardio_prescription.limits_text()),
        ("padrao HIIT", cardio_prescription.describe(cardio_prescription.default_prescription(cardio_prescription.CardioMode.HIIT))),
        ("RAG", "TRECHO DO RAG"),
    ):
        exigir(f"{valor}" in secao, f"{rotulo} na secao de cardio")
    # A secao vem depois do RAG de metodologia (prefixo cacheavel intocado).
    exigir(prompt.index(secao) > prompt.index(contexto["rag_text"]), "cardio depois do RAG de metodologia")


def test_prompt_com_tsb_baixo_proibe_hiit_e_baixa_a_zona():
    tsb = cardio_envelope.HIIT_MIN_TSB - 1
    baixo = cardio_plan.prompt_section(_ctx(tsb))
    alto = cardio_plan.prompt_section(_ctx(cardio_envelope.HIIT_MIN_TSB + 10))
    zona = cardio_envelope.BAND_MAX_ZONE[cardio_envelope.RecoveryBand.LOADED]
    exigir(f"Z{zona};" in baixo, "zona maxima da faixa carregada")
    exigir(f"{cardio_envelope.HIIT_MIN_TSB}:" in baixo, "limite do TSB na linha do HIIT")
    exigir(cardio_plan._band_line(cardio_envelope.template_signals(tsb)) in baixo, "linha da faixa no TSB baixo")
    exigir(cardio_plan._band_line(cardio_envelope.template_signals(tsb)) not in alto, "linha do HIIT muda com a faixa")


# ---------------------------------------------------------------------------
# Depois do LLM
# ---------------------------------------------------------------------------


def _ciclo():
    return _template(
        {"sequence": 1, "name": "Peito", "items": [_mg(CHEST)]},
        {"sequence": 2, "name": "Perna", "items": [_mg(LEG)]},
        {"sequence": 3, "name": "Costas", "items": [_mg(BACK)]},
        {"sequence": 4, "name": "Descanso", "slot_type": "descanso", "items": []},
    )


def test_sem_pedido_todo_cardio_e_removido_com_registro():
    template = _ciclo()
    log = _apply(template, {0: [NORMAL]}, None)
    assert [e["code"] for e in log] == [cardio_plan.CARDIO_REMOVED]
    assert log[0]["reason"] == cardio_plan.REASON_NOT_REQUESTED
    assert all(item.item_kind != "cardio" for slot in template.slots for item in slot.items)


def test_hiit_na_vespera_de_perna_vira_normal_e_descanso_com_cardio_continua_descanso():
    template = _ciclo()
    log = _apply(template, {0: [HIIT], 2: [HIIT], 3: [NORMAL]}, _ctx())
    peito, perna, costas, descanso = template.slots
    # Vespera de perna: HIIT vetado pelo dono, trocado pelo Normal padrao.
    assert peito.items[-1].item_kind == "cardio"
    assert peito.items[-1].cardio_mode == "continuous"
    vetado = [e for e in log if e["reason"] == cardio_envelope.ADJ_HIIT_VETOED]
    assert vetado and vetado[0]["slot"] == 1 and cardio_envelope.REASON_EVE_OF_LEG_DAY in vetado[0]["detail"]
    assert "véspera de perna" in vetado[0]["message"]
    # Dia seguinte a perna: HIIT permitido, depois da forca.
    assert [i.item_kind for i in costas.items] == ["muscle_group", "cardio"]
    assert costas.items[-1].cardio_mode == "hiit" and costas.items[-1].item_order == 2
    # Descanso com cardio: continua descanso, so o cardio.
    assert descanso.slot_type == "descanso"
    assert [i.item_kind for i in descanso.items] == ["cardio"]
    assert [i.item_kind for i in perna.items] == ["muscle_group"]
    # O save do Modelo aceita o rascunho.
    from app.workout_plan_templates import _validate_template_payload

    _validate_template_payload(template)


def test_ultimo_slot_e_vespera_do_primeiro_no_ciclo_por_sequencia():
    template = _template(
        {"sequence": 1, "name": "Perna", "items": [_mg(LEG)]},
        {"sequence": 2, "name": "Peito", "items": [_mg(CHEST)]},
        {"sequence": 3, "name": "Costas", "items": [_mg(BACK)]},
    )
    log = _apply(template, {2: [HIIT]}, _ctx())
    assert template.slots[2].items[-1].cardio_mode == "continuous"
    assert log[0]["reason"] == cardio_envelope.ADJ_HIIT_VETOED


def test_por_dia_da_semana_a_vespera_de_segunda_e_o_domingo():
    slots = [
        {"sequence": 1, "weekday": 1, "muscle_groups": ["Quadríceps"]},
        {"sequence": 2, "weekday": 3, "muscle_groups": ["Peitoral"]},
        {"sequence": 3, "weekday": 7, "muscle_groups": []},
    ]
    signals = cardio_envelope.template_signals(None)
    envelope, dia = cardio_envelope.build_template_envelope(slots, signals)
    domingo = envelope.day(dia[3])
    assert cardio_envelope.REASON_EVE_OF_LEG_DAY in domingo.reasons
    quarta = envelope.day(dia[2])
    assert quarta.allows(cardio_prescription.CardioMode.HIIT.value)


def test_tsb_em_alto_risco_corta_hiit_e_zona_em_todos_os_dias():
    template = _template(
        {"sequence": 1, "name": "Peito", "items": [_mg(CHEST)]},
        {"sequence": 2, "name": "Costas", "items": [_mg(BACK)]},
    )
    # Curto o bastante para os dois caberem no teto semanal de minutos da faixa.
    alta = {**NORMAL, "cardio_target_zone": 4, "cardio_duration_minutes": 10}
    _apply(template, {0: [HIIT], 1: [alta]}, _ctx(cardio_envelope.TSB_HIGH_RISK_BELOW - 1))
    teto = cardio_envelope.BAND_MAX_ZONE[cardio_envelope.RecoveryBand.HIGH_RISK]
    for slot in template.slots:
        item = slot.items[-1]
        assert item.cardio_mode == "continuous" and item.cardio_target_zone <= teto


def test_prescricao_invalida_e_removida_e_numero_fora_do_limite_e_ajustado():
    template = _ciclo()
    invertido = {**HIIT, "hiit_low_zone": 4, "hiit_high_zone": 2}
    longo = {**NORMAL, "cardio_warmup_minutes": cardio_prescription.CARDIO_WARMUP_MINUTES_MAX + 30}
    log = _apply(template, {2: [invertido], 3: [longo]}, _ctx())
    removido = [e for e in log if e["code"] == cardio_plan.CARDIO_REMOVED]
    assert removido and removido[0]["reason"] == cardio_plan.REASON_INVALID and removido[0]["slot"] == 3
    assert all(i.item_kind != "cardio" for i in template.slots[2].items)
    ajustado = [e for e in log if e["reason"] == cardio_plan.REASON_CLAMPED]
    assert ajustado and ajustado[0]["detail"] == cardio_prescription.WARMUP_FIELD
    assert template.slots[3].items[-1].cardio_warmup_minutes == cardio_prescription.CARDIO_WARMUP_MINUTES_MAX


def test_mais_de_um_cardio_no_slot_corta_o_excedente():
    template = _ciclo()
    log = _apply(template, {3: [NORMAL, NORMAL]}, _ctx())
    assert sum(1 for i in template.slots[3].items if i.item_kind == "cardio") == cardio_envelope.SESSIONS_PER_DAY_MAX
    assert any(e["reason"] == cardio_envelope.ADJ_DAY_LIMIT for e in log)


def test_cardio_de_slot_removido_pelo_auto_fix_sai_com_registro():
    template = _ciclo()
    perdido = template.slots.pop(3)
    log = []
    raw = {id(perdido): (4, "Descanso", [NORMAL])}
    cardio_plan.apply_cardio(template, raw, _ctx(), lambda code, **f: log.append({"code": code, **f}))
    assert log[0]["reason"] == cardio_plan.REASON_SLOT_REMOVED and log[0]["slot"] == 4


def test_tempo_do_slot_inclui_o_cardio_gerado():
    template = _ciclo()
    _apply(template, {3: [NORMAL]}, _ctx())
    descanso = template.slots[3]
    assert estimate_slot_seconds(descanso.items, 60) == cardio_prescription.total_seconds(NORMAL)


# ---------------------------------------------------------------------------
# validate_draft (cascata inteira, sem banco)
# ---------------------------------------------------------------------------


def _item_llm(group):
    base = {name: None for name in PeriodizationItemOut.model_fields}
    base.update(item_kind="muscle_group", muscle_group_id=group, priority_min_pct=40, priority_max_pct=60, prioritize=False)
    return base


def _saida(slots):
    return {
        "template": {"name": "Gerado", "description": None, "volume_mode": "percent_budget", "slots": slots},
        "rationale": "ok",
        "source_citations": [],
    }


@pytest.fixture
def sem_banco(monkeypatch):
    monkeypatch.setattr(validation, "_check_references", lambda *a, **k: [])
    monkeypatch.setattr(validation, "_check_template_rules", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_check_activation", lambda *a, **k: None)


def test_validate_draft_poe_o_cardio_no_fim_e_registra_o_envelope(sem_banco):
    pedido = PeriodizationDraftIn(days_per_cycle=3, instructions="quero cardio")
    saida = _saida(
        [
            {"sequence": 1, "name": "Peito", "slot_type": "normal", "weekday": None,
             "items": [_item_llm(CHEST)], "cardio_items": [HIIT]},
            {"sequence": 2, "name": "Perna", "slot_type": "normal", "weekday": None,
             "items": [_item_llm(LEG)], "cardio_items": []},
            {"sequence": 3, "name": "Descanso", "slot_type": "descanso", "weekday": None,
             "items": [], "cardio_items": [NORMAL]},
        ]
    )
    draft, report, _extras = validation.validate_draft(None, pedido, saida, cardio=_ctx())
    assert report["errors"] == []
    peito, perna, descanso = draft["slots"]
    assert peito["items"][-1]["item_kind"] == "cardio" and peito["items"][-1]["cardio_mode"] == "continuous"
    assert descanso["slot_type"] == "descanso" and [i["item_kind"] for i in descanso["items"]] == ["cardio"]
    assert [i["item_kind"] for i in perna["items"]] == ["muscle_group"]
    assert any(e["code"] == cardio_plan.CARDIO_ADJUSTED for e in report["auto_fixes"])


def test_validate_draft_sem_contexto_de_cardio_remove_o_cardio(sem_banco):
    pedido = PeriodizationDraftIn(days_per_cycle=1)
    saida = _saida(
        [{"sequence": 1, "name": "Peito", "slot_type": "normal", "weekday": None,
          "items": [_item_llm(CHEST)], "cardio_items": [NORMAL]}]
    )
    draft, report, _extras = validation.validate_draft(None, pedido, saida)
    assert [i["item_kind"] for i in draft["slots"][0]["items"]] == ["muscle_group"]
    assert [e["reason"] for e in report["auto_fixes"] if e["code"] == cardio_plan.CARDIO_REMOVED] == [
        cardio_plan.REASON_NOT_REQUESTED
    ]


def test_saida_da_v12_sem_cardio_items_continua_valida(sem_banco):
    pedido = PeriodizationDraftIn(days_per_cycle=1)
    saida = _saida([{"sequence": 1, "name": "Peito", "slot_type": "normal", "weekday": None, "items": [_item_llm(CHEST)]}])
    draft, report, _extras = validation.validate_draft(None, pedido, saida)
    assert report["errors"] == [] and len(draft["slots"][0]["items"]) == 1

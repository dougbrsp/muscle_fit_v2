"""Rótulos pt-BR e vocabulário RDC 429 (dono `app/domain/nutrition/labels.py`)
e o bloco `nutrition` de `export_for_frontend()` (o FE só lê o JSON gerado)."""

from __future__ import annotations

import json
from enum import Enum

import pytest

from app.domain import nutrition
from app.domain.nutrition import labels as lb
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex
from app.domain.nutrition.bmr import BmrFormula
from app.domain.nutrition.body_composition import BodyFatMethod
from app.domain.nutrition.carb_cycling import DayKind
from app.domain.nutrition.catalog import FoodSource
from app.domain.nutrition.energy_expenditure import SessionKind, TdeeMode, TrainingKcalNature
from app.domain.nutrition.external_sources import UsdaDataType
from app.domain.nutrition.label_photo import LabelLegibility
from app.domain.nutrition.meal_math import MealNature
from app.domain.nutrition import ai_usage as _ai
from app.domain.nutrition import anthropometrics as _an
from app.domain.nutrition import bmr as _bmr
from app.domain.nutrition import body_composition as _bc
from app.domain.nutrition import carb_cycling as _cc
from app.domain.nutrition import catalog as _cat
from app.domain.nutrition import meal_math as _mm
from app.domain.nutrition import daily_target as _dt
from app.domain.nutrition import energy_expenditure as _ee
from app.domain.nutrition import external_sources as _ext
from app.domain.nutrition import label_photo as _lp
from app.domain.nutrition import macros as _macros
from app.domain.nutrition import diary as _diary
from app.domain.nutrition import plan as _plan
from app.domain.nutrition import strategy as _st
from app.domain.nutrition import weekly_budget as _wb
from app.domain.nutrition.phases import BreakProtocol, PhaseKind
from app.domain.nutrition.plan import PlanState
from app.domain.nutrition.strategy import ProteinBasis, Strategy

ROTULOS = [
    (Sex, lb.SEX_LABELS, "sexes", "sex_labels"),
    (ActivityLevel, lb.ACTIVITY_LEVEL_LABELS, "activity_levels", "activity_level_labels"),
    (Strategy, lb.STRATEGY_LABELS, "strategies", "strategy_labels"),
    (ProteinBasis, lb.PROTEIN_BASIS_LABELS, "protein_bases", "protein_basis_labels"),
    (DayKind, lb.DAY_KIND_LABELS, "day_kinds", "day_kind_labels"),
    (lb.MealSlot, lb.MEAL_SLOT_LABELS, "meal_slots", "meal_slot_labels"),
    (BmrFormula, lb.BMR_FORMULA_LABELS, "bmr_formulas", "bmr_formula_labels"),
    (TdeeMode, lb.TDEE_MODE_LABELS, "tdee_modes", "tdee_mode_labels"),
    (SessionKind, lb.SESSION_KIND_LABELS, "session_kinds", "session_kind_labels"),
    (TrainingKcalNature, lb.TRAINING_KCAL_NATURE_LABELS, "training_kcal_natures", "training_kcal_nature_labels"),
    (MealNature, lb.MEAL_NATURE_LABELS, "meal_natures", "meal_nature_labels"),
    (BodyFatMethod, lb.BODY_FAT_METHOD_LABELS, "body_fat_methods", "body_fat_method_labels"),
    (PhaseKind, lb.PHASE_KIND_LABELS, "phase_kinds", "phase_kind_labels"),
    (BreakProtocol, lb.BREAK_PROTOCOL_LABELS, "break_protocols", "break_protocol_labels"),
    (PlanState, lb.PLAN_STATE_LABELS, "plan_states", "plan_state_labels"),
    (FoodSource, lb.FOOD_SOURCE_LABELS, "food_sources", "food_source_labels"),
    (UsdaDataType, lb.USDA_DATA_TYPE_LABELS, "usda_data_types", "usda_data_type_labels"),
    (LabelLegibility, lb.LABEL_LEGIBILITY_LABELS, "label_legibilities", "label_legibility_labels"),
]
IDS = [chave for _, _, chave, _ in ROTULOS]


@pytest.mark.parametrize(("enum_cls", "rotulos", "_codigos", "_rotulos"), ROTULOS, ids=IDS)
def test_todo_codigo_tem_rotulo_unico_e_nao_vazio(enum_cls: type[Enum], rotulos, _codigos, _rotulos):
    assert set(rotulos) == set(enum_cls)
    assert all(isinstance(texto, str) and texto.strip() == texto and texto for texto in rotulos.values())
    assert len(set(rotulos.values())) == len(rotulos)


def test_mesmo_conceito_mesmo_rotulo():
    for estrategia in Strategy:
        assert lb.PHASE_KIND_LABELS[PhaseKind(estrategia.value)] == lb.STRATEGY_LABELS[estrategia]


def test_rotulo_da_kcal_do_treino_e_natureza_nao_proveniencia():
    textos = " ".join(lb.TRAINING_KCAL_NATURE_LABELS.values()).lower()
    assert "intervals" not in textos and "manual" not in textos


def test_labels_by_code():
    assert lb.labels_by_code(lb.STRATEGY_LABELS) == {"cutting": "Cutting", "bulking": "Bulking", "maintenance": "Manutenção"}


def test_rdc429():
    codigos = [campo.code for campo in lb.RDC429_FIELDS]
    assert len(codigos) == len(set(codigos))
    obrigatorios = {campo.code for campo in lb.RDC429_FIELDS if campo.required}
    assert {"energy_kcal", "carbohydrates_g", "total_sugars_g", "added_sugars_g", "protein_g", "total_fat_g",
            "saturated_fat_g", "trans_fat_g", "fiber_g", "sodium_mg", "serving_size"} <= obrigatorios
    assert all(campo.label and campo.unit for campo in lb.RDC429_FIELDS)
    json.dumps([campo.to_dict() for campo in lb.RDC429_FIELDS])


def test_export_for_frontend():
    bloco = nutrition.export_for_frontend()
    # L1: 34; L2b: + plan_states, plan_state_labels, default_bmr_formula, strategy_only_fields, notices;
    # L3a: + food_sources, food_source_labels, food_bases, food_basis_labels, default_food_basis,
    # food_per_100_fields, food_serving_fields, food_required_fields, food_citations;
    # L3b: + food_licenses, usda_data_types, usda_data_type_labels;
    # L4: + unit_labels, portion_units, nutrient_fields, energy_field, item_nutrient_fields, hunger_levels,
    # hunger_labels, water_ml_range, meal_max_items
    # L5b: + meal_parse (teto do texto e a decisão de cada item; a cota vem da API)
    # L6: + label_photo, label_legibilities, label_legibility_labels
    assert len(bloco) == 64
    from app.domain.nutrition import meal_parse as _mp

    assert bloco["meal_parse"] == {
        "text_max_chars": _mp.TEXT_MAX_CHARS,
        "match_statuses": [s.value for s in _mp.MatchStatus],
        "match_status_labels": {s.value: lb.MEAL_PARSE_MATCH_STATUS_LABELS[s] for s in _mp.MatchStatus},
        "estimate_source": _cat.FoodSource.AI_ESTIMATE.value,
        "estimate_basis": _mp.ESTIMATE_BASIS.value,
        "quota_exceeded_label": lb.ai_quota_exceeded_label("meal_parse"),
    }
    assert bloco["energy_field"] == _mm.ENERGY_FIELD and bloco["item_nutrient_fields"][0] == bloco["energy_field"]
    assert bloco["item_nutrient_fields"][1:] == list(_mm.ATWATER_MACRO_FIELDS)
    assert set(bloco["item_nutrient_fields"]) <= set(bloco["nutrient_fields"])
    # Selos e dicas do diário com o MESMO texto das limitações do motor.
    for codigo in (_dt.LIMIT_TARGET_BACKFILLED, _wb.LIMIT_INCOMPLETE_INTAKE_DAYS, _dt.LIMIT_TRAINING_UNKNOWN_FUTURE_DAY):
        assert bloco["notices"][codigo] == lb.limitation_label(codigo)
    assert str(_dt.TARGET_FREEZE_LAG_DAYS) in bloco["notices"][lb.NOTICE_TARGET_FROZEN]
    assert set(bloco["unit_labels"]) == set(bloco["units"])
    assert bloco["portion_units"] == ["unit", "portion"] and set(bloco["portion_units"]) < set(bloco["units"])
    assert set(bloco["nutrient_fields"]) <= set(bloco["food_per_100_fields"])
    assert bloco["hunger_levels"] == list(_diary.hunger_levels())
    assert set(bloco["hunger_labels"]) == {str(n) for n in bloco["hunger_levels"]}
    assert bloco["water_ml_range"] == list(_diary.WATER_ML_RANGE) and bloco["meal_max_items"] == _diary.MEAL_MAX_ITEMS
    for porcao in bloco["generic_portions"]:
        assert bloco["unit_labels"][porcao["code"]] == porcao["label"]  # mesmo rótulo da medida caseira
    rdc = {campo.code for campo in lb.RDC429_FIELDS}
    assert set(bloco["food_per_100_fields"]) <= rdc and set(bloco["food_serving_fields"]) <= rdc
    assert set(bloco["food_required_fields"]) <= set(bloco["food_per_100_fields"])
    assert bloco["food_bases"] == ["g", "ml"] and bloco["default_food_basis"] in bloco["food_bases"]
    assert set(bloco["food_basis_labels"]) == set(bloco["food_bases"])
    assert bloco["food_citations"] == {
        "taco": _cat.TACO_CITATION, "usda": _cat.USDA_CITATION, "off": _cat.OFF_CITATION}
    assert bloco["food_licenses"] == {"taco": _cat.TACO_LICENSE, "usda": _cat.USDA_LICENSE, "off": _cat.OFF_LICENSE}
    assert bloco["usda_data_types"] == [t.value for t in _ext.USDA_DATA_TYPES]
    assert bloco["default_bmr_formula"] == _bmr.DEFAULT_BMR_FORMULA.value
    assert bloco["default_bmr_formula"] == _plan.build_plan_settings(strategy="maintenance").settings.bmr_formula.value
    assert bloco["strategy_only_fields"] == {campo: e.value for campo, e in _plan.STRATEGY_ONLY_FIELDS.items()}
    for campo, estrategia in bloco["strategy_only_fields"].items():
        assert campo in bloco["form_ranges"][estrategia]  # o campo existe no formulário da estratégia dona
        outras = [e for e in bloco["strategies"] if e != estrategia]
        assert all(campo not in bloco["form_ranges"][e] for e in outras)
    assert bloco["notices"] == dict(lb.NOTICE_LABELS)
    assert f"{_bmr.SCALE_BMR_DEVIATION_WARN_SHARE:.0%}" in bloco["notices"][lb.NOTICE_SCALE_BMR_DEVIATION]
    assert bloco["form_ranges"]["weight_kg"] == [_an.WEIGHT_KG_RANGE[0], _an.WEIGHT_KG_RANGE[1]]
    for estrategia in Strategy:
        base = _st.StrategyParams.defaults_for(estrategia).protein_basis.value
        assert bloco["form_ranges"][estrategia.value]["protein_basis"] == base
    assert bloco["engine_version"] == nutrition.ENGINE_VERSION
    for enum_cls, rotulos, chave_codigos, chave_rotulos in ROTULOS:
        assert bloco[chave_codigos] == [m.value for m in enum_cls]
        assert bloco[chave_rotulos] == lb.labels_by_code(rotulos)
    assert bloco["default_tdee_mode"] in bloco["tdee_modes"]
    assert set(bloco["form_ranges"]["stop_body_fat_percent"]) == {"bulking", "cutting"}
    assert [p["code"] for p in bloco["generic_portions"]] == [c for c in bloco["units"] if c.startswith(("colher", "xicara"))]
    assert [c["code"] for c in bloco["rdc429_fields"]] == [c.code for c in lb.RDC429_FIELDS]
    texto = json.dumps(bloco, ensure_ascii=False)
    assert json.loads(texto) == bloco
    assert "keytel" not in texto.lower()
    # Proveniência da kcal (manual × Intervals) é de `domain/calories_source.py` (L0), não deste bloco.
    assert not [chave for chave in bloco if "kcal_source" in chave or "calories_source" in chave]


# ---------------------------------------------------------------------------
# L2b: texto das limitações (a API devolve `{code, label}`; o FE só exibe)
# ---------------------------------------------------------------------------

_PREFIXADOS = {
    _st.LIMIT_PARAM_OUT_OF_RANGE,
    _plan.ERR_INVALID_CODE,
    _plan.ERR_PARAM_NOT_APPLICABLE,
    _bc.LIMIT_BODY_FAT_OUT_OF_RANGE,
    # L3a: catálogo (`<código>:<campo da RDC 429>`).
    _cat.ERR_MISSING_FIELD,
    _cat.ERR_INVALID_VALUE,
    _cat.ERR_ABOVE_BASIS,
    _cat.WARN_SUBTOTAL_ABOVE_TOTAL,
    # L4: diário (`<código>:<campo da RDC 429>`, regra do macro ausente e totais).
    _mm.LIMIT_MACRO_INFERRED_ZERO,
    _mm.LIMIT_NUTRIENT_UNKNOWN,
    _mm.LIMIT_TOTAL_INCOMPLETE,
    # L6: foto do rótulo (`<código>:<campo da RDC 429>`).
    _lp.WARN_LABEL_DERIVED_FROM_SERVING,
    _lp.WARN_LABEL_SERVING_INCONSISTENT,
    _lp.WARN_LABEL_NOT_SIGNIFICANT,
}


def _codigos_do_motor() -> set[str]:
    codigos: set[str] = set()
    for modulo in (_bmr, _bc, _cc, _dt, _ee, _macros, _plan, _st, _cat, _ext, _mm, _wb, _diary, _ai, _lp):
        for nome, valor in vars(modulo).items():
            if nome.startswith(("LIMIT_", "CLAMP_", "ERR_", "WARN_")) and isinstance(valor, str):
                codigos.add(valor)
    return codigos


def test_todo_codigo_de_limitacao_da_meta_e_do_plano_tem_texto():
    sem_texto = sorted(c for c in _codigos_do_motor() if c not in lb.LIMITATION_LABELS and c not in _PREFIXADOS)
    assert sem_texto == []
    assert set(lb.PREFIXED_LIMITATION_LABELS) == _PREFIXADOS


def test_codigo_com_campo_recebe_o_rotulo_do_campo():
    assert lb.limitation_label("param_out_of_range:protein_g_per_kg") == "Proteína (g/kg): fora da faixa da fonte."
    assert lb.limitation_label("invalid_code:bmr_formula") == "Fórmula da TMB: código inválido."
    assert "Circunferências" in lb.limitation_label("body_fat_out_of_range:navy")
    assert lb.limitation_label("codigo_que_nao_existe") == "codigo_que_nao_existe"
    assert lb.limitation_label("param_out_of_range:campo_novo") == "campo_novo: fora da faixa da fonte."


def test_diario_rotula_os_codigos_com_campo_e_numeros_do_dono():
    assert lb.limitation_label("macro_inferred_zero:protein_g").startswith("Proteínas (g): ausente na tabela")
    assert lb.limitation_label("nutrient_unknown:energy_kcal").startswith("Valor energético (kcal)")
    assert lb.limitation_label("total_incomplete:sodium_mg").startswith("Sódio (mg): total incompleto")
    assert str(_diary.MEAL_MAX_ITEMS) in lb.limitation_label(_diary.ERR_TOO_MANY_ITEMS)
    assert f"{_diary.ITEM_AMOUNT_MAX:g}" in lb.limitation_label(_diary.ERR_ITEM_AMOUNT_OUT_OF_RANGE)
    assert f"{_wb.MAX_DAILY_CARRYOVER_SHARE:.0%}" in lb.limitation_label(_wb.CLAMP_DAILY_CARRYOVER)
    assert f"{_wb.MAX_WEEKLY_BANK_SHARE:.0%}" in lb.limitation_label(_wb.CLAMP_WEEKLY_BANK)
    assert lb.labels_by_code(lb.HUNGER_LABELS)["1"] == lb.HUNGER_LABELS[1]


def test_numero_no_texto_vem_do_dono():
    assert str(_bmr.FFM_FRESHNESS_MAX_DAYS) in lb.limitation_label(_bmr.LIMIT_FFM_STALE)
    assert f"{_st.CUT_MAX_DEFICIT_SHARE:.0%}" in lb.limitation_label(_st.CLAMP_MAX_DEFICIT_SHARE)
    assert f"{_mm.ATWATER_TOLERANCE_SHARE:.0%}" in lb.limitation_label(_cat.WARN_ATWATER)
    assert f"{_cat.PORTION_AMOUNT_MAX:g}" in lb.limitation_label(_cat.ERR_INVALID_PORTION_AMOUNT)


def test_catalogo_rotulos_de_base_e_campo_da_rdc429():
    assert set(lb.FOOD_BASIS_LABELS) == set(_cat.FOOD_BASES)
    assert len(set(lb.FOOD_BASIS_LABELS.values())) == len(lb.FOOD_BASIS_LABELS)
    assert lb.limitation_label("missing_field:energy_kcal") == "Valor energético (kcal): obrigatório."
    assert lb.limitation_label("subtotal_above_total:total_sugars_g").startswith("Açúcares totais (g)")
    assert set(lb.RDC429_FIELD_LABELS) == {campo.code for campo in lb.RDC429_FIELDS}


def test_limitation_items_preserva_ordem_sem_repetir():
    itens = lb.limitation_items(["missing_sex", "missing_weight", "missing_sex"])
    assert [i["code"] for i in itens] == ["missing_sex", "missing_weight"]
    assert all(i["label"] for i in itens)
    assert lb.limitation_items(None) == []


def test_campos_do_plano_tem_rotulo():
    campos = set(_plan.PlanSettings.__dataclass_fields__) - {"advanced", "carb_cycling_enabled"}
    assert campos <= set(lb.PLAN_FIELD_LABELS)
    assert _dt.LIMIT_TRAINING_UNKNOWN_FUTURE_DAY in lb.LIMITATION_LABELS
    assert _ee.LIMIT_MISSING_ACTIVITY_LEVEL in lb.LIMITATION_LABELS
    assert _macros.LIMIT_MISSING_PROTEIN_BASIS in lb.LIMITATION_LABELS
    assert _cc.LIMIT_CARB_FLOOR_PREVENTS_CYCLE in lb.LIMITATION_LABELS

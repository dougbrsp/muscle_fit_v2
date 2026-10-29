"""Motor de nutrição (Lote L1 do plano `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`).

Fonte única da verdade de toda regra, constante e rótulo de nutrição (mapa
`docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Python puro, sem I/O:
constantes `Final`, dataclasses `frozen=True` com `to_dict()`, `None` = não
calculável (+ código em `limitations`).

Módulos (um dono por regra):
- `anthropometrics`: sexo, idade, IMC, nível/fatores de atividade, `Subject`.
- `bmr`: Mifflin-St Jeor, Katch-McArdle, Cunningham, Harris-Benedict; seleção.
- `energy_expenditure`: kcal do treino (informada > MET; sem Keytel, D-K), TDEE.
- `body_composition`: %G Marinha dos EUA, MLG, FFMI, cintura/altura, peso-alvo.
- `energy_density`: Hall 2008 / Forbes (kcal por kg de variação de peso).
- `strategy`: cutting / bulking / manutenção, faixas, defaults, alvo calórico.
- `macros`: Atwater, fibra, divisão em gramas, proteína por refeição.
- `carb_cycling`: distribuição semanal do carbo com média preservada.
- `phases`: calendário (MATADOR, ICECAP, refeeds, teto de semanas, %G de parada).
- `adaptive_tdee`: gasto observado; só SUGERE recálculo (D-J).
- `weekly_budget`: orçamento, banco e aderência da semana.
- `meal_math`: gramas por unidade/porção, macros de item/refeição, consistência.
- `food_text`: normalização de nome, sinônimos pt-BR, matching e busca (L3a).
- `catalog`: catálogo de alimentos — fontes, citação/licença, base, campos por
  100 g/ml da RDC 429 e regras de entrada de alimento e porção (L3a).
- `external_sources`: USDA FoodData Central e Open Food Facts — ids/chaves de
  nutriente, conversões, escolha da energia, código de barras e limite diário
  de consultas ao OFF (L3b).
- `diary`: diário de refeições — refeição (`MealSlot`), origem, entrada da
  refeição e do item, água e fome do dia (L4).
- `labels`: rótulos pt-BR, vocabulário RDC 429 e texto das limitações.
- `daily_target`: orquestrador `compute_daily_target`, `ENGINE_VERSION`,
  congelamento da meta materializada e contexto do dia (L2b).
- `plan`: parâmetros do plano do aluno (defaults + validação), estado e vigência (L2b).
- `subject`: `Subject` do dia a partir das leituras de perfil/bioimpedância/fita (L2b).
- `ai_usage`: uso e cota de IA das features de nutrição — features, status,
  cota diária, job type de cada feature e custo por modelo (L5a).
- `meal_parse`: leitura da refeição por IA — vocabulário da IA, estimativa por
  100 g, casamento com o catálogo e o item estimado no diário (L5b).
- `label_photo`: foto do rótulo — upload (tamanho, tipo), chave e retenção no
  S3, redução no navegador e a revisão da leitura da IA (L6).

`export_for_frontend()` gera o bloco `nutrition` de
`frontend/src/lib/motor-constants.generated.json` (via
`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`): rótulos, códigos,
faixas de formulário, campos da RDC 429, porções genéricas e slots de refeição.
Nenhum coeficiente de cálculo vai para o FE — o frontend não recalcula.

Consumidores em runtime (L2b, mapa §47): `api/app/nutrition_targets_service.py`
(materialização via `compute_daily_target`) e `api/app/nutrition_plans.py`
(rotas do plano, que validam com `plan.build_plan_settings`). L3a (mapa §48):
`api/app/nutrition_foods.py` (catálogo: busca por `food_text`, entrada por
`catalog`) e `api/app/nutrition_access.py` (quem edita/promove um alimento).
L3b (mapa §49): `api/app/nutrition_external_foods.py` (código de barras e USDA,
pelo `external_sources`) e o cliente HTTP `api/app/external_food_sources.py`.
L4 (mapa §52): `api/app/nutrition_meals.py` (refeições: `meal_math` + `diary`),
`api/app/nutrition_days.py` (dia e semana: `weekly_budget`, `daily_target`,
`energy_expenditure`) e `nutrition_targets_service.ensure_diary_targets`.
L5a (mapa §54): `api/app/nutrition_ai_quota.py` (`ai_usage_log`: cota, reserva,
fechamento e sweep, pelo `ai_usage`).
L5b (mapa §55): `api/app/nutrition_meal_parse.py` (rota 202 + cota),
`api/app/nutrition_meal_parse_ai.py` (prompt e schema), o worker
`api/app/nutrition_meal_parse_worker.py` e o item estimado em `nutrition_meals.py`.
L6 (mapa §56): `api/app/nutrition_label_photo.py` (upload e 202),
`api/app/nutrition_label_storage.py` (S3) e o worker `api/app/nutrition_label_ai.py`
(leitura pela IA + `label_photo.review_reading`).
"""

from __future__ import annotations

from typing import Any

from . import (  # noqa: F401  (reexport dos módulos do pacote)
    adaptive_tdee,
    ai_usage,
    anthropometrics,
    bmr,
    body_composition,
    carb_cycling,
    catalog,
    daily_target,
    diary,
    energy_density,
    energy_expenditure,
    external_sources,
    food_text,
    label_photo,
    labels,
    macros,
    meal_math,
    meal_parse,
    phases,
    plan,
    strategy,
    subject,
    weekly_budget,
)
from .daily_target import ENGINE_VERSION, DailyTarget, DayContext, PlanParams, compute_daily_target  # noqa: F401

__all__ = [
    "ENGINE_VERSION",
    "DailyTarget",
    "DayContext",
    "PlanParams",
    "compute_daily_target",
    "export_for_frontend",
]


def _codes(enum_cls: Any) -> list[str]:
    return [member.value for member in enum_cls]


def _range(bounds: tuple[Any, Any]) -> list[Any]:
    return [bounds[0], bounds[1]]


def export_for_frontend() -> dict[str, Any]:
    """Bloco `nutrition` de `motor-constants.generated.json` (só exibição e
    validação de formulário; nenhum coeficiente de cálculo)."""
    lb = labels
    st = strategy
    return {
        "engine_version": ENGINE_VERSION,
        "sexes": _codes(anthropometrics.Sex),
        "sex_labels": lb.labels_by_code(lb.SEX_LABELS),
        "activity_levels": _codes(anthropometrics.ActivityLevel),
        "activity_level_labels": lb.labels_by_code(lb.ACTIVITY_LEVEL_LABELS),
        "strategies": _codes(st.Strategy),
        "strategy_labels": lb.labels_by_code(lb.STRATEGY_LABELS),
        "protein_bases": _codes(st.ProteinBasis),
        "protein_basis_labels": lb.labels_by_code(lb.PROTEIN_BASIS_LABELS),
        "day_kinds": _codes(carb_cycling.DayKind),
        "day_kind_labels": lb.labels_by_code(lb.DAY_KIND_LABELS),
        "meal_slots": _codes(lb.MealSlot),
        "meal_slot_labels": lb.labels_by_code(lb.MEAL_SLOT_LABELS),
        "bmr_formulas": _codes(bmr.BmrFormula),
        "bmr_formula_labels": lb.labels_by_code(lb.BMR_FORMULA_LABELS),
        "default_bmr_formula": bmr.DEFAULT_BMR_FORMULA.value,
        "tdee_modes": _codes(energy_expenditure.TdeeMode),
        "tdee_mode_labels": lb.labels_by_code(lb.TDEE_MODE_LABELS),
        "default_tdee_mode": energy_expenditure.DEFAULT_TDEE_MODE.value,
        "session_kinds": _codes(energy_expenditure.SessionKind),
        "session_kind_labels": lb.labels_by_code(lb.SESSION_KIND_LABELS),
        "training_kcal_natures": _codes(energy_expenditure.TrainingKcalNature),
        "training_kcal_nature_labels": lb.labels_by_code(lb.TRAINING_KCAL_NATURE_LABELS),
        "meal_natures": _codes(meal_math.MealNature),
        "meal_nature_labels": lb.labels_by_code(lb.MEAL_NATURE_LABELS),
        "body_fat_methods": _codes(body_composition.BodyFatMethod),
        "body_fat_method_labels": lb.labels_by_code(lb.BODY_FAT_METHOD_LABELS),
        "phase_kinds": _codes(phases.PhaseKind),
        "phase_kind_labels": lb.labels_by_code(lb.PHASE_KIND_LABELS),
        "break_protocols": _codes(phases.BreakProtocol),
        "break_protocol_labels": lb.labels_by_code(lb.BREAK_PROTOCOL_LABELS),
        "plan_states": _codes(plan.PlanState),
        "plan_state_labels": lb.labels_by_code(lb.PLAN_STATE_LABELS),
        # Campo do formulário -> a única estratégia em que ele vale (L2b).
        "strategy_only_fields": {campo: estrategia.value for campo, estrategia in plan.STRATEGY_ONLY_FIELDS.items()},
        "notices": dict(lb.NOTICE_LABELS),
        "units": _codes(meal_math.Unit),
        "form_ranges": {
            "height_cm": _range(anthropometrics.HEIGHT_CM_RANGE),
            "weight_kg": _range(anthropometrics.WEIGHT_KG_RANGE),
            "body_fat_percent": _range(body_composition.BODY_FAT_RANGE_VALID),
            "meals_per_day": _range(macros.MEALS_PER_DAY_RANGE),
            "cutting": {
                "protein_basis": st.StrategyParams.defaults_for(st.Strategy.CUTTING).protein_basis.value,
                "weekly_rate_share": _range(st.CUT_WEEKLY_RATE_SHARE_RANGE),
                "weekly_rate_share_default": st.CUT_WEEKLY_RATE_SHARE_DEFAULT,
                "protein_g_per_kg": _range(st.CUT_PROTEIN_G_PER_KG_FFM_RANGE),
                "protein_g_per_kg_default": st.CUT_PROTEIN_G_PER_KG_FFM_DEFAULT,
                "fat_kcal_share": _range(st.CUT_FAT_KCAL_SHARE_RANGE),
                "fat_kcal_share_default": st.CUT_FAT_KCAL_SHARE_DEFAULT,
                "max_deficit_share": st.CUT_MAX_DEFICIT_SHARE,
                "min_intake_bmr_multiple": st.CUT_MIN_INTAKE_BMR_MULTIPLE,
            },
            "bulking": {
                "protein_basis": st.StrategyParams.defaults_for(st.Strategy.BULKING).protein_basis.value,
                "surplus_share": _range(st.BULK_SURPLUS_SHARE_RANGE),
                "surplus_share_default": st.BULK_SURPLUS_SHARE_DEFAULT,
                "surplus_share_advanced": _range(st.BULK_SURPLUS_SHARE_RANGE_ADVANCED),
                "surplus_share_default_advanced": st.BULK_SURPLUS_SHARE_DEFAULT_ADVANCED,
                "protein_g_per_kg": _range(st.BULK_PROTEIN_G_PER_KG_RANGE),
                "protein_g_per_kg_default": st.BULK_PROTEIN_G_PER_KG_DEFAULT,
                "fat_kcal_share": _range(st.BULK_FAT_KCAL_SHARE_RANGE),
                "fat_kcal_share_default": st.BULK_FAT_KCAL_SHARE_DEFAULT,
                "carb_min_g_per_kg": _range(st.BULK_CARB_MIN_G_PER_KG_RANGE),
                "carb_min_g_per_kg_default": st.BULK_CARB_MIN_G_PER_KG,
            },
            "maintenance": {
                "protein_basis": st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE).protein_basis.value,
                "protein_g_per_kg": _range(st.MAINT_PROTEIN_G_PER_KG_RANGE),
                "protein_g_per_kg_default": st.MAINT_PROTEIN_G_PER_KG_DEFAULT,
                "fat_kcal_share": _range(st.MAINT_FAT_KCAL_SHARE_RANGE),
                "fat_kcal_share_default": st.MAINT_FAT_KCAL_SHARE_DEFAULT,
                "tolerance_share": st.MAINT_TOLERANCE_SHARE,
            },
            "refeed_days_per_week_default": phases.REFEED_DAYS_PER_WEEK_DEFAULT,
            "max_cut_weeks_without_break": phases.MAX_CUT_WEEKS_WITHOUT_BREAK,
            "stop_body_fat_percent": {
                st.Strategy.BULKING.value: {sex.value: valor for sex, valor in phases.BULK_STOP_BODY_FAT_PERCENT.items()},
                st.Strategy.CUTTING.value: {sex.value: valor for sex, valor in phases.CUT_STOP_BODY_FAT_PERCENT.items()},
            },
        },
        "rdc429_fields": [campo.to_dict() for campo in lb.RDC429_FIELDS],
        "generic_portions": [porcao.to_dict() for porcao in meal_math.GENERIC_PORTIONS.values()],
        # Catálogo de alimentos (L3a): vocabulário, campos e citações para a tela
        # `/nutricao/alimentos`; regras de entrada e permissões ficam na API.
        "food_sources": _codes(catalog.FoodSource),
        "food_source_labels": lb.labels_by_code(lb.FOOD_SOURCE_LABELS),
        "food_bases": [base.value for base in catalog.FOOD_BASES],
        "food_basis_labels": lb.labels_by_code(lb.FOOD_BASIS_LABELS),
        "default_food_basis": catalog.DEFAULT_FOOD_BASIS.value,
        "food_per_100_fields": list(catalog.PER_100_FIELDS),
        "food_serving_fields": list(catalog.SERVING_FIELDS),
        "food_required_fields": list(catalog.REQUIRED_PER_100_FIELDS),
        "food_citations": {fonte.value: texto for fonte, texto in catalog.SOURCE_CITATIONS.items()},
        # Fontes externas (L3b): licença de cada fonte para o rodapé (ODbL exige a
        # atribuição, R4) e os tipos de dado do USDA que a importação aceita.
        "food_licenses": {fonte.value: texto for fonte, texto in catalog.SOURCE_LICENSES.items()},
        "usda_data_types": _codes(external_sources.UsdaDataType),
        "usda_data_type_labels": lb.labels_by_code(lb.USDA_DATA_TYPE_LABELS),
        # Diário (L4): rótulos das unidades, campos de nutriente exibidos, escala
        # de fome e faixas do formulário; macros, totais e balanço vêm da API.
        "unit_labels": lb.labels_by_code(lb.UNIT_LABELS),
        "portion_units": [unidade.value for unidade in meal_math.PORTION_UNITS],
        "nutrient_fields": list(meal_math.NUTRIENT_FIELDS),
        "energy_field": meal_math.ENERGY_FIELD,
        "item_nutrient_fields": [meal_math.ENERGY_FIELD, *meal_math.ATWATER_MACRO_FIELDS],
        "hunger_levels": list(diary.hunger_levels()),
        "hunger_labels": lb.labels_by_code(lb.HUNGER_LABELS),
        "water_ml_range": _range(diary.WATER_ML_RANGE),
        "meal_max_items": diary.MEAL_MAX_ITEMS,
        # Leitura da refeição por IA (L5b): teto do texto e a decisão de cada item.
        # A cota do dia vem da API (`GET /nutrition/meals/parse/quota`), nunca daqui.
        "meal_parse": {
            "text_max_chars": meal_parse.TEXT_MAX_CHARS,
            "match_statuses": _codes(meal_parse.MatchStatus),
            "match_status_labels": lb.labels_by_code(lb.MEAL_PARSE_MATCH_STATUS_LABELS),
            "estimate_source": meal_parse.ESTIMATE_SOURCE.value,
            "estimate_basis": meal_parse.ESTIMATE_BASIS.value,
            "quota_exceeded_label": lb.ai_quota_exceeded_label(ai_usage.AiFeature.MEAL_PARSE),
        },
        # Foto do rótulo (L6): parâmetros da redução por canvas e do upload (a API
        # confere de novo: 413/415) e a legibilidade que a leitura devolve.
        "label_photo": {
            "max_bytes": label_photo.LABEL_PHOTO_MAX_BYTES,
            "content_type": label_photo.LABEL_PHOTO_CONTENT_TYPE,
            "resize_max_edge_px": label_photo.RESIZE_MAX_EDGE_PX,
            "resize_quality": label_photo.RESIZE_JPEG_QUALITY,
            "retention_days": label_photo.LABEL_PHOTO_RETENTION_DAYS,
            # Fonte com que o formulário da leitura é salvo (`POST /nutrition/foods`).
            "food_source": catalog.FoodSource.LABEL_PHOTO.value,
        },
        "label_legibilities": _codes(label_photo.LabelLegibility),
        "label_legibility_labels": lb.labels_by_code(lb.LABEL_LEGIBILITY_LABELS),
    }

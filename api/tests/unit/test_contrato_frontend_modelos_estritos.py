"""Contrato frontend x modelos `extra="forbid"` (SEG-11).

Cada payload abaixo foi copiado literalmente da chamada real do frontend
(valores de exemplo; chaves com `null` incluidas). Se alguem adicionar um
campo no frontend sem acrescentar ao modelo, este teste quebra no CI.

Rodar: cd api && python -m pytest tests/unit/test_contrato_frontend_modelos_estritos.py
"""

from __future__ import annotations

from app.models import InsightsGenerateIn
from app.request_models import (
    BodyCompositionUpdateIn,
    FoodItemCreateIn,
    FoodItemIn,
    FoodPortionIn,
    GymCreateIn,
    LabelPhotoUploadIn,
    MealBodyIn,
    MealIn,
    MealParseIn,
    MealPreviewIn,
    NutritionDayIn,
    NutritionPlanIn,
    NutritionPlanPublishIn,
    NutritionPlanSettingsIn,
    NutritionTargetsRecomputeIn,
    SleepMetricsUpdateIn,
    UserCreateIn,
    UserProfileUpdateIn,
    UserUpdateIn,
)


def test_put_users_me_perfil():
    # frontend/src/app/(app)/perfil/PerfilClient.tsx (saveProfileMutation). Nutrição
    # L2a: `sex`/`activity_level` com o código do motor e `height_cm` número; os dois
    # últimos só vão quando preenchidos (segundo payload: sem eles).
    UserProfileUpdateIn.model_validate(
        {
            "full_name": "Doug",
            "email": None,
            "birth_date": "1990-05-01",
            "sex": "male",
            "height_cm": 178.5,
            "activity_level": "moderate",
            "intervals_athlete_id": "i12345",
            "preferred_weight_unit": "kg",
            "timezone": "America/Sao_Paulo",
            "intervals_api_key": None,
        }
    )
    UserProfileUpdateIn.model_validate(
        {
            "full_name": "Doug",
            "email": None,
            "birth_date": None,
            "sex": None,
            "intervals_athlete_id": None,
            "preferred_weight_unit": "kg",
            "timezone": "America/Sao_Paulo",
            "intervals_api_key": None,
        }
    )


def test_put_users_me_so_unidade():
    # frontend/src/lib/use-weight-unit.ts:88 (corpo parcial)
    UserProfileUpdateIn.model_validate({"preferred_weight_unit": "lb"})


def test_post_users_admin():
    # frontend/src/app/(app)/usuarios/UsuariosClient.tsx:152-158 (submitCreate)
    UserCreateIn.model_validate(
        {"username": "maria", "password": "Senha12345", "role": "regular", "full_name": None, "email": "m@x.com"}
    )
    UserCreateIn.model_validate(
        {"username": "maria", "password": "Senha12345", "role": "admin", "full_name": "Maria", "email": None}
    )
    # Nutrição L0 (2026-10-15): `personal_trainer` atribuível (16 caracteres, cabe no max_length).
    UserCreateIn.model_validate(
        {"username": "pt", "password": "Senha12345", "role": "personal_trainer", "full_name": None, "email": None}
    )


def test_put_users_id_admin():
    # frontend/src/app/(app)/usuarios/UsuariosClient.tsx (saveEdit); `sex` com o
    # código do motor desde o L2a (opções de `nutrition-constants.ts`).
    UserUpdateIn.model_validate(
        {
            "full_name": None,
            "email": "a@b.com",
            "birth_date": None,
            "sex": "female",
            "role": "regular",
            "is_active": True,
        }
    )


def test_post_gyms():
    # frontend/src/app/(app)/academias/AcademiasClient.tsx:119-125
    GymCreateIn.model_validate({"name": "Smart Fit", "usual_weekdays": [1, 3, 5], "is_recurring": True})
    GymCreateIn.model_validate({"name": "Avulsa", "usual_weekdays": [], "is_recurring": False})


def test_put_body_composition_form_completo_com_nulls_e_strings_numericas():
    # frontend/src/app/(app)/indicadores-fisicos/BioimpedanciaSection.tsx:165 +
    # linhas 395-413 (initial ao editar; a API devolve Decimal como string)
    body = BodyCompositionUpdateIn.model_validate(
        {
            "local_date": "2026-10-08",
            "measured_at": None,
            "weight_kg": "80.20",
            "bmi": None,
            "body_fat_percent": 18.5,
            "fat_mass_kg": None,
            "fat_free_mass_kg": None,
            "muscle_mass_kg": "38.10",
            "skeletal_muscle_percent": None,
            "body_water_percent": None,
            "visceral_fat_rating": 7,
            "bone_mass_kg": None,
            "bmr_kcal": 1800,
            "subcutaneous_fat_percent": None,
            "protein_percent": None,
            "metabolic_age": 30,
            "notes": None,
        }
    )
    assert body.model_dump(exclude_unset=True)["weight_kg"] == 80.2


def test_put_sleep_metrics():
    # frontend/src/lib/physical-indicators-types.ts:192-203 (sleepFormToApiPayload),
    # usado em frontend/src/app/(app)/indicadores-fisicos/SleepMetricsSection.tsx:133
    SleepMetricsUpdateIn.model_validate(
        {
            "local_date": "2026-10-08",
            "duration_hours": 7.5,
            "regularity_percent": 82,
            "deep_sleep_hours": 1.5,
            "rem_sleep_hours": None,
            "awake_count": 2,
            "awake_hours": 0.25,
            "notes": None,
        }
    )


def test_post_insights_generate():
    # frontend/src/app/(app)/insights/page.tsx:104 ({ question, window })
    InsightsGenerateIn.model_validate({"question": "Como esta meu volume?", "window": "28d"})


# Nutrição L2b: frontend/src/components/nutrition/PlanForm.tsx:158-175 (payload do
# rascunho; frações já convertidas de % pela tela, campos de outra estratégia em
# null, ciclo de carbo sempre false até o L8).
_PLANO_CUTTING = {
    "strategy": "cutting",
    "advanced": False,
    "protein_g_per_kg": 2.6,
    "fat_kcal_share": 0.25,
    "weekly_rate_share": 0.007,
    "surplus_share": None,
    "carb_min_g_per_kg": None,
    "bmr_formula": "auto",
    "tdee_mode": "explicit_training",
    "activity_level": None,
    "meals_per_day": 4,
    "goal_body_fat_percent": 12,
    "goal_weight_kg": None,
    "carb_cycling_enabled": False,
    "notes": None,
}


def test_post_nutrition_plans_rascunho():
    # PlanForm.tsx:175 (createNutritionPlan({ ...payload, user_id }))
    NutritionPlanIn.model_validate({**_PLANO_CUTTING, "user_id": 2})
    NutritionPlanIn.model_validate(
        {
            **_PLANO_CUTTING,
            "user_id": 2,
            "strategy": "bulking",
            "advanced": True,
            "weekly_rate_share": None,
            "surplus_share": 0.05,
            "carb_min_g_per_kg": 3,
            "activity_level": "moderate",
            "notes": "Off-season",
        }
    )


def test_put_nutrition_plans_rascunho():
    # PlanForm.tsx:175 (updateNutritionPlan(plan.id, payload)): sem user_id
    NutritionPlanSettingsIn.model_validate(_PLANO_CUTTING)


def test_post_nutrition_plans_publish():
    # frontend/src/components/nutrition/PlanPreview.tsx (publishNutritionPlan(plan.id, currentPlanId)):
    # o id da vigente que a tela viu, ou null quando o aluno não tinha.
    NutritionPlanPublishIn.model_validate({"expected_current_id": 7})
    NutritionPlanPublishIn.model_validate({"expected_current_id": None})


def test_post_nutrition_targets_recompute():
    # frontend/src/components/nutrition/PlanPreview.tsx:144
    NutritionTargetsRecomputeIn.model_validate({"user_id": 2, "date_from": "2026-10-17", "date_to": None})


# Nutrição L3a: frontend/src/lib/nutrition-food-form.ts:63-77 (`foodPayloadFromForm`,
# usado por FoodForm.tsx em createFood/updateFood): nome, marca, categoria, base,
# os 10 campos por 100 da RDC 429 (vazio = null) e os 3 da porção do rótulo.
_ALIMENTO = {
    "name": "Arroz caseiro",
    "brand": None,
    "category": "Cereais",
    "basis": "g",
    "energy_kcal": 128,
    "carbohydrates_g": 28.1,
    "total_sugars_g": None,
    "added_sugars_g": None,
    "protein_g": 2.5,
    "total_fat_g": 0.2,
    "saturated_fat_g": None,
    "trans_fat_g": None,
    "fiber_g": 1.6,
    "sodium_mg": None,
    "serving_size": 50,
    "household_measure": "1 escumadeira",
    "servings_per_package": None,
}


def test_post_e_put_nutrition_foods():
    # FoodForm.tsx (createFood(payload) / updateFood(food.id, payload)): mesmo corpo.
    FoodItemIn.model_validate(_ALIMENTO)
    FoodItemIn.model_validate({**_ALIMENTO, "basis": "ml", "brand": "Marca", "serving_size": None, "household_measure": None})
    # O POST usa FoodItemCreateIn (o mesmo corpo + `source`, nutrição L6).
    FoodItemCreateIn.model_validate(_ALIMENTO)


def test_post_nutrition_foods_da_foto_do_rotulo():
    # FoodForm.tsx com `createSource` (AlimentosClient.tsx, leitura da foto do rótulo, L6):
    # createFood({ ...payload, source: LABEL_PHOTO.food_source }). O PUT não aceita `source`.
    FoodItemCreateIn.model_validate({**_ALIMENTO, "source": "label_photo"})
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FoodItemIn.model_validate({**_ALIMENTO, "source": "label_photo"})


def test_post_nutrition_foods_label_photo():
    # frontend/src/lib/nutrition-api.ts (uploadLabelPhoto): { image_base64 } sem padding (L6).
    LabelPhotoUploadIn.model_validate({"image_base64": "/9j/4A"})


def test_post_nutrition_food_portions():
    # frontend/src/components/nutrition/PortionsEditor.tsx:28 ({ label, amount })
    FoodPortionIn.model_validate({"label": "1 unidade média", "amount": 86})
    FoodPortionIn.model_validate({"label": "1 colher", "amount": 12.5})


# Diário (L4): corpos montados por `frontend/src/lib/nutrition-meal-form.ts`
# (`mealPayload`, `mealCreatePayload`, `itemsPayload`) e enviados pelo
# `MealEditor.tsx` (createMeal / updateMeal / previewMeal). O FE nunca manda macro.
_REFEICAO = {
    "local_date": "2026-10-17",
    "meal_slot": "lunch",
    "eaten_at": None,
    "notes": None,
    "items": [
        {"food_id": 3, "quantity": 150, "unit": "g", "portion_id": None},
        {"id": 9, "food_id": 4, "quantity": 2, "unit": "portion", "portion_id": 7},
    ],
}


def test_post_e_put_nutrition_meals():
    # MealEditor.tsx: createMeal(mealCreatePayload(form, userId)) e updateMeal(meal.id, mealPayload(form)).
    MealIn.model_validate(_REFEICAO)
    MealIn.model_validate({**_REFEICAO, "user_id": 2, "eaten_at": "12:30", "notes": "ok"})
    MealBodyIn.model_validate(_REFEICAO)


def test_post_nutrition_meals_preview():
    # MealEditor.tsx: previewMeal({ ...mealCreatePayload(...), meal_id }) — meal_id só na edição.
    MealPreviewIn.model_validate({**_REFEICAO, "meal_id": 4})
    MealPreviewIn.model_validate({**_REFEICAO, "user_id": 2})


# Leitura por IA (L5b): `MealParseModal.tsx` (parseMealText) e o editor com os itens
# lidos (`nutrition-meal-form.ts`: `itemsPayload` com `estimate`/`match_confidence`,
# `mealCreatePayload` com `ai_job_id`/`description_raw`).
_ESTIMATIVA = {"name": "whey protein", "energy_kcal": 400, "protein_g": 80, "carbohydrates_g": 10, "total_fat_g": 5,
               "fiber_g": None}


def test_post_nutrition_meals_parse():
    MealParseIn.model_validate({"text": "arroz e feijão", "local_date": "2026-10-18", "meal_slot": None})
    MealParseIn.model_validate({"text": "arroz", "local_date": "2026-10-18", "meal_slot": "lunch", "user_id": 2})


def test_post_nutrition_meals_lida_pela_ia():
    corpo = {
        **_REFEICAO,
        "user_id": 2,
        "ai_job_id": "job-1",
        "description_raw": "arroz, frango e whey",
        "items": [
            {"food_id": 3, "quantity": 120, "unit": "g", "portion_id": None, "match_confidence": 1},
            {"food_id": None, "estimate": _ESTIMATIVA, "quantity": 30, "unit": "g", "portion_id": None},
        ],
    }
    MealIn.model_validate(corpo)
    MealPreviewIn.model_validate(corpo)
    # Edição do item estimado gravado: só o id (a API reusa o snapshot).
    MealBodyIn.model_validate({**_REFEICAO, "items": [{"id": 8, "food_id": None, "quantity": 30, "unit": "g",
                                                       "portion_id": None}]})


def test_put_nutrition_days():
    # WaterHungerInputs.tsx: saveNutritionDayLog(date, { water_ml, hunger, notes }, userId).
    NutritionDayIn.model_validate({"water_ml": 2000, "hunger": 3, "notes": None})
    NutritionDayIn.model_validate({"water_ml": None, "hunger": None, "notes": "ok"})


def test_post_cardio_recommendations_apply():
    # frontend/src/components/cardio/CardioRecommendationPanel.tsx (applyMutation),
    # por sessão e "Aplicar todas" (M98 C3b).
    from app.cardio_recommendations import CardioApplyIn

    CardioApplyIn.model_validate({"dates": ["2026-10-28"]})
    CardioApplyIn.model_validate({"dates": ["2026-10-28", "2026-10-30"]})

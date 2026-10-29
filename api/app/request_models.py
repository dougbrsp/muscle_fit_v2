"""Modelos de entrada com `extra="forbid"` e tamanhos maximos (SEG-11).

Substituem os `Dict[str, Any] = Body(...)` das rotas de usuarios, academias,
bioimpedancia, sono e mapa corporal. Campo desconhecido ou texto acima do
teto responde 422 (antes era ignorado ou estourava no banco).

Limites: colunas do banco quando existem (users.email 160, users.full_name 160,
gyms.name 120...) e, para texto livre, 2000 caracteres. O maior valor lido em
producao em 2026-10-08 (users.full_name 16, email 22; sleep_metrics.notes 51;
manual_effort_metrics.notes 27) esta muito abaixo de todos os tetos.

Os modelos nao validam REGRA (e-mail, fuso, unidade): isso continua nos
handlers, com o mesmo 400 de antes.
"""
from __future__ import annotations

from datetime import date, time
from typing import Any, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

NOTES_MAX = 2000
SHORT_TEXT_MAX = 160

Number = Union[int, float]


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCreateIn(_StrictIn):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)
    role: Optional[str] = Field(default=None, max_length=16)
    full_name: Optional[str] = Field(default=None, max_length=160)
    email: Optional[str] = Field(default=None, max_length=160)
    birth_date: Optional[str] = Field(default=None, max_length=32)
    sex: Optional[str] = Field(default=None, max_length=20)
    intervals_athlete_id: Optional[str] = Field(default=None, max_length=80)
    intervals_api_key: Optional[str] = Field(default=None, max_length=100)
    is_advanced_user: Optional[bool] = None


class UserProfileUpdateIn(_StrictIn):
    """PUT /users/me (nao altera role/username/is_active).

    Perfil antropometrico (nutricao L2a): `sex`, `height_cm` e `activity_level`
    sao validados no handler pelo motor (`domain/nutrition/anthropometrics`),
    com 400 `{"error": <codigo do motor>, "message": ...}`; o banco grava o
    codigo canonico."""

    full_name: Optional[str] = Field(default=None, max_length=160)
    email: Optional[str] = Field(default=None, max_length=160)
    birth_date: Optional[str] = Field(default=None, max_length=32)
    sex: Optional[str] = Field(default=None, max_length=20)
    height_cm: Optional[Number] = None
    activity_level: Optional[str] = Field(default=None, max_length=32)
    intervals_athlete_id: Optional[str] = Field(default=None, max_length=80)
    intervals_api_key: Optional[str] = Field(default=None, max_length=100)
    is_advanced_user: Optional[bool] = None
    # Any de proposito: valor invalido (inclusive nao-string) deve cair no 400 do handler
    # (`_validate_weight_unit`), contrato de produto anterior ao modelo.
    preferred_weight_unit: Optional[Any] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    # M98 C1 (migração 096): FCmáx manual em bpm. Any de proposito: a faixa e o
    # "inteiro" sao do dono (`hr_zones.validate_manual_max_hr`, 400 no handler);
    # `null` explicito limpa o campo (ver `users._max_hr_set`).
    max_hr_bpm_manual: Optional[Any] = None


class UserUpdateIn(UserProfileUpdateIn):
    """PUT /users/{id} (admin): perfil + role e is_active."""

    role: Optional[str] = Field(default=None, max_length=16)
    is_active: Optional[bool] = None


class GymCreateIn(_StrictIn):
    name: str = Field(max_length=120)
    usual_weekdays: Optional[List[int]] = Field(default=None, max_length=7)
    is_recurring: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=NOTES_MAX)


class BodyCompositionUpdateIn(_StrictIn):
    local_date: Optional[str] = Field(default=None, max_length=32)
    measured_at: Optional[str] = Field(default=None, max_length=40)
    weight_kg: Optional[Number] = None
    bmi: Optional[Number] = None
    body_fat_percent: Optional[Number] = None
    fat_mass_kg: Optional[Number] = None
    fat_free_mass_kg: Optional[Number] = None
    muscle_mass_kg: Optional[Number] = None
    skeletal_muscle_percent: Optional[Number] = None
    body_water_percent: Optional[Number] = None
    visceral_fat_rating: Optional[Number] = None
    bone_mass_kg: Optional[Number] = None
    bmr_kcal: Optional[Number] = None
    subcutaneous_fat_percent: Optional[Number] = None
    protein_percent: Optional[Number] = None
    metabolic_age: Optional[int] = None
    source: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=NOTES_MAX)


class SleepMetricsUpdateIn(_StrictIn):
    local_date: Optional[str] = Field(default=None, max_length=32)
    duration_hours: Optional[Number] = None
    regularity_percent: Optional[Number] = None
    deep_sleep_hours: Optional[Number] = None
    rem_sleep_hours: Optional[Number] = None
    awake_count: Optional[int] = None
    awake_hours: Optional[Number] = None
    notes: Optional[str] = Field(default=None, max_length=NOTES_MAX)


class MuscleGroupBodyMapIn(_StrictIn):
    muscle_group: str = Field(max_length=120)
    slugs: List[str] = Field(max_length=64)


class NutritionPlanSettingsIn(_StrictIn):
    """PUT /nutrition/plans/{id} (só rascunho). Códigos e faixas NÃO são
    validados aqui: `domain/nutrition/plan.build_plan_settings` responde 400 com
    o código do motor (campo ausente = default da estratégia)."""

    strategy: str = Field(max_length=16)
    advanced: bool = False
    protein_g_per_kg: Optional[Number] = None
    fat_kcal_share: Optional[Number] = None
    weekly_rate_share: Optional[Number] = None
    surplus_share: Optional[Number] = None
    carb_min_g_per_kg: Optional[Number] = None
    bmr_formula: Optional[str] = Field(default=None, max_length=24)
    tdee_mode: Optional[str] = Field(default=None, max_length=24)
    activity_level: Optional[str] = Field(default=None, max_length=16)
    meals_per_day: Optional[Number] = None
    goal_body_fat_percent: Optional[Number] = None
    goal_weight_kg: Optional[Number] = None
    # Recusado pelo motor até o L8 (`carb_cycling_unavailable`).
    carb_cycling_enabled: bool = False
    notes: Optional[str] = Field(default=None, max_length=1000)


class NutritionPlanIn(NutritionPlanSettingsIn):
    """POST /nutrition/plans (admin/PT): rascunho para o aluno `user_id`."""

    user_id: int


class NutritionPlanPublishIn(_StrictIn):
    """POST /nutrition/plans/{id}/publish (admin/PT). Concorrência otimista: o
    id da vigente que a tela viu (ou null, se o aluno não tinha). Obrigatório
    (pode ser null): divergir da vigente travada responde 409 `publish_conflict`."""

    expected_current_id: Optional[int]


class NutritionTargetsRecomputeIn(_StrictIn):
    """POST /nutrition/targets/recompute (admin/PT)."""

    user_id: int
    date_from: date
    date_to: Optional[date] = None


class FoodItemIn(_StrictIn):
    """POST /nutrition/foods e PUT /nutrition/foods/{id} (nutrição L3a). Valores
    POR 100 g/ml, com os códigos da RDC 429 do motor (`catalog.PER_100_FIELDS`).
    Obrigatórios, faixas, base e avisos (Atwater, subtotal) NÃO são validados
    aqui: `domain/nutrition/catalog.validate_food_values` responde 400 com o
    código do motor; aqui só tipo e tamanho."""

    name: str = Field(min_length=1, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=120)
    category: Optional[str] = Field(default=None, max_length=120)
    basis: Optional[str] = Field(default=None, max_length=8)
    energy_kcal: Optional[Number] = None
    carbohydrates_g: Optional[Number] = None
    total_sugars_g: Optional[Number] = None
    added_sugars_g: Optional[Number] = None
    protein_g: Optional[Number] = None
    total_fat_g: Optional[Number] = None
    saturated_fat_g: Optional[Number] = None
    trans_fat_g: Optional[Number] = None
    fiber_g: Optional[Number] = None
    sodium_mg: Optional[Number] = None
    serving_size: Optional[Number] = None
    household_measure: Optional[str] = Field(default=None, max_length=120)
    servings_per_package: Optional[Number] = None


class FoodItemCreateIn(FoodItemIn):
    """POST /nutrition/foods (nutrição L6): o cadastro de `FoodItemIn` + a fonte.
    `source` ausente = cadastro manual; `label_photo` = formulário pré-preenchido
    pela leitura da foto do rótulo, conferido pelo usuário. As fontes aceitas são
    do motor (`catalog.USER_ENTRY_SOURCES`; outra -> 400). O PUT segue com
    `FoodItemIn`: a fonte não muda depois de criada."""

    source: Optional[str] = Field(default=None, max_length=16)


class LabelPhotoUploadIn(_StrictIn):
    """POST /nutrition/foods/label-photo (nutrição L6): a foto JÁ REDUZIDA pela
    tela (JPEG do canvas) em base64, sem padding (o alfabeto do base64 não forma
    as assinaturas que o CRS do WAF bloqueia no corpo). Tamanho, alfabeto e tipo
    NÃO são validados aqui: `domain/nutrition/label_photo.decode_upload` e
    `upload_error` respondem 400/413/415 com o código do motor."""

    image_base64: str


class FoodPortionIn(_StrictIn):
    """POST /nutrition/foods/{id}/portions (nutrição L3a): quantidade NA BASE do
    alimento (g ou ml); a faixa é do motor (`catalog.validate_portion_amount`)."""

    label: str = Field(min_length=1, max_length=120)
    amount: Number


class MealItemEstimateIn(_StrictIn):
    """Estimativa da IA por 100 g de um alimento fora do catálogo (nutrição L5b),
    devolvida pela tela como a leitura entregou (códigos de
    `meal_parse.ESTIMATE_FIELDS`). Obrigatórios e faixas: `meal_parse.validate_estimate`."""

    name: str = Field(min_length=1, max_length=255)
    energy_kcal: Optional[Number] = None
    protein_g: Optional[Number] = None
    carbohydrates_g: Optional[Number] = None
    total_fat_g: Optional[Number] = None
    fiber_g: Optional[Number] = None


class MealItemIn(_StrictIn):
    """Item de uma refeição do diário (nutrição L4): alimento do catálogo,
    quantidade e unidade (códigos de `meal_math.Unit`); `portion_id` escolhe
    uma porção cadastrada do alimento. `id` (só na edição) é o item já gravado
    da refeição: com o mesmo alimento, reusa os valores CONGELADOS (E4).
    Conversão, faixa e unidade NÃO são validadas aqui: o motor responde 400.

    L5b: o item estimado pela IA vem sem `food_id` e com `estimate` (ou, na
    edição, só com o `id` do item estimado gravado); exatamente um dos dois é
    regra do motor (`diary.item_source_errors`). `match_confidence` = nota do
    casamento que a leitura entregou (auditoria)."""

    id: Optional[int] = None
    food_id: Optional[int] = None
    estimate: Optional[MealItemEstimateIn] = None
    quantity: Number
    unit: str = Field(min_length=1, max_length=24)
    portion_id: Optional[int] = None
    match_confidence: Optional[Number] = None


class MealBodyIn(_StrictIn):
    """PUT /nutrition/meals/{id}: a refeição inteira (os itens substituem os
    gravados). Slot e quantidade de itens: regras de `domain/nutrition/diary`."""

    local_date: date
    meal_slot: str = Field(min_length=1, max_length=24)
    eaten_at: Optional[time] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    items: List[MealItemIn]


class MealIn(MealBodyIn):
    """POST /nutrition/meals: `user_id` = aluno (admin/PT); ausente = o próprio.
    L5b: `ai_job_id` (o job da leitura por IA, a origem vira `ai_text`) e
    `description_raw` (o texto descrito, só no nosso banco)."""

    user_id: Optional[int] = None
    ai_job_id: Optional[str] = Field(default=None, max_length=64)
    description_raw: Optional[str] = Field(default=None, max_length=NOTES_MAX)


class MealParseIn(_StrictIn):
    """POST /nutrition/meals/parse (nutrição L5b): o texto da refeição, o dia e
    a refeição (opcional; o prompt usa o rótulo). Texto vazio ou acima do teto
    do motor (`meal_parse.TEXT_MAX_CHARS`) e slot fora do vocabulário: 400 do
    motor; aqui só tipo e tamanho."""

    user_id: Optional[int] = None
    text: str = Field(max_length=NOTES_MAX)
    local_date: date
    meal_slot: Optional[str] = Field(default=None, max_length=24)


class MealPreviewIn(MealIn):
    """POST /nutrition/meals/preview (sem gravar). `meal_id` = refeição em
    edição (os itens com `id` usam o snapshot congelado dela)."""

    meal_id: Optional[int] = None


class NutritionDayIn(_StrictIn):
    """PUT /nutrition/days/{date}: água (ml), fome e anotação do dia; substitui
    a linha inteira (ausente = não informado). Faixas: `diary.validate_day_log`."""

    water_ml: Optional[Number] = None
    hunger: Optional[Number] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


__all__ = [
    "BodyCompositionUpdateIn",
    "FoodItemIn",
    "FoodPortionIn",
    "GymCreateIn",
    "MealBodyIn",
    "MealIn",
    "MealItemEstimateIn",
    "MealItemIn",
    "MealParseIn",
    "MealPreviewIn",
    "MuscleGroupBodyMapIn",
    "NutritionDayIn",
    "NutritionPlanIn",
    "NutritionPlanPublishIn",
    "NutritionPlanSettingsIn",
    "NutritionTargetsRecomputeIn",
    "SleepMetricsUpdateIn",
    "UserCreateIn",
    "UserProfileUpdateIn",
    "UserUpdateIn",
]

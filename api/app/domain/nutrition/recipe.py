"""Receita (issue #286): dono do cálculo dos nutrientes de uma receita.

O usuário informa ingredientes (alimentos comuns do catálogo, em peso cru) e o peso final pronto; o motor devolve o
total, o valor por 100 g da receita pronta, o rendimento e as limitações. Toda soma e escala por 100 g vêm de
`meal_math` (`sum_scaled_fields`, `per_reference_from_serving`, `atwater_check`); a plausibilidade dos valores
substituídos vem de `catalog.validate_food_values`. Nada aqui grava: a rota grava os valores calculados e os efetivos.

Decisões (defaults de desenho, SEM fonte bibliográfica):
- `RECIPE_MAX_INGREDIENTS` = 30, o mesmo teto de itens de uma refeição (`diary.MEAL_MAX_ITEMS`).
- Rendimento (peso pronto ÷ peso cru) entre 10% e 500%: trava de erro de digitação (ex.: 45 g em vez de 450 g), não
  modelo de cozimento; arroz e massa passam de 200%, carne perde água e fica abaixo de 100%.
- Ingrediente em ml: seus nutrientes entram normalmente (valor por 100 ml x ml), mas ml não vira massa: o peso cru
  soma só os gramas. Com QUALQUER ingrediente em ml o rendimento NÃO é calculado (`yield_percent` = `None`), a trava
  de faixa acima não se aplica (vale só o teto absoluto `RECIPE_FINAL_WEIGHT_MAX_G`) e a limitação
  `recipe_raw_weight_excludes_ml` registra isso.
- Substituição manual (`apply_overrides`): valor `None` = sem substituição (vale o calculado); campo fora de
  `catalog.PER_100_FIELDS` é erro; `bool` não é número. O calculado é preservado ao lado do efetivo.
- A energia entra por nome (`energy_kcal` em `catalog.PER_100_FIELDS`), como qualquer outro campo.
- Nutriente que algum ingrediente não informa (`None`) nunca vira zero: o campo fica em `incomplete_fields`, o valor
  por 100 g dele é `None` e o total é só um mínimo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final, Mapping, Optional, Sequence

from . import catalog, diary
from .macros import Macros
from .meal_math import (
    AmountResolution,
    AtwaterCheck,
    Unit,
    atwater_check,
    non_negative_number,
    normalize_unit,
    per_reference_from_serving,
    positive_number,
    sum_scaled_fields,
)

RECIPE_ENGINE_VERSION: Final[str] = "recipe-v1"
RECIPE_MAX_INGREDIENTS: Final[int] = diary.MEAL_MAX_ITEMS
RECIPE_YIELD_MIN_SHARE: Final[float] = 0.10
RECIPE_YIELD_MAX_SHARE: Final[float] = 5.0
RECIPE_FINAL_WEIGHT_MAX_G: Final[float] = 20000.0
RECIPE_INGREDIENT_AMOUNT_MAX: Final[float] = RECIPE_FINAL_WEIGHT_MAX_G  # teto próprio (não o do item do diário)
RECIPE_NAME_MAX_CHARS: Final[int] = 255
RECIPE_COPY_SUFFIX: Final[str] = " (cópia)"
YIELD_PERCENT_SCALE: Final[float] = 100.0  # converte razão em percentual; não é fator de nutriente
RECIPE_ID_MAX: Final[int] = 2_147_483_647  # teto de id de ingrediente/porção aceito (INT com sinal): acima, "não encontrado"
RECIPE_LIST_DEFAULT_LIMIT: Final[int] = 500  # sem `limit`, a lista traz até aqui (o comportamento anterior da tela)
RECIPE_LIST_MAX_LIMIT: Final[int] = 500
RECIPE_LIST_MAX_OFFSET: Final[int] = 100_000

CATALOG_CURRENT: Final[str] = "current"
CATALOG_OUTDATED: Final[str] = "outdated"
CATALOG_UNAVAILABLE: Final[str] = "unavailable"
_PER_100_TOLERANCE: Final[float] = 1e-9

ERR_INGREDIENT_AMOUNT_OUT_OF_RANGE = "recipe_ingredient_amount_out_of_range"  # `:<índice>`

ERR_NO_INGREDIENTS = "recipe_no_ingredients"
ERR_TOO_MANY_INGREDIENTS = "recipe_too_many_ingredients"
ERR_INVALID_FINAL_WEIGHT = "recipe_invalid_final_weight"
ERR_INVALID_INGREDIENT_AMOUNT = "recipe_invalid_ingredient_amount"  # `:<índice>`
ERR_YIELD_IMPLAUSIBLE = "recipe_yield_implausible"
ERR_OVERRIDE_FIELD = "recipe_override_field_invalid"  # sem sufixo: a chave recebida não é ecoada
ERR_OVERRIDE_VALUE = "recipe_override_value_invalid"  # `:<campo>` (campo já validado contra PER_100_FIELDS)

# Códigos da rota de receitas (a rota só os devolve; texto em `labels`).
ERR_INGREDIENT_NOT_FOUND = "recipe_ingredient_not_found"  # `:<índice>`
ERR_INGREDIENT_IS_RECIPE = "recipe_ingredient_is_recipe"  # `:<índice>`
ERR_INGREDIENT_PORTION_NOT_FOUND = "recipe_ingredient_portion_not_found"  # `:<índice>`
ERR_VERSION_CONFLICT = "recipe_version_conflict"

ERR_PUBLISH_NAME_REQUIRED = "recipe_publish_name_required"
ERR_PUBLISH_FINAL_WEIGHT_REQUIRED = "recipe_publish_final_weight_required"
ERR_PUBLISH_NO_INGREDIENTS = "recipe_publish_no_ingredients"
ERR_PUBLISH_INVALID_VALUES = "recipe_publish_invalid_values"
ERR_PUBLISH_ARCHIVED = "recipe_publish_archived"
ERR_PUBLIC_EDIT_FORBIDDEN = "recipe_public_edit_forbidden"
ERR_PUBLIC_EDIT_FORBIDDEN_STATUS = 403  # pública existe para todos: não há o que esconder (privada alheia é 404)

LIMIT_RAW_WEIGHT_EXCLUDES_ML = "recipe_raw_weight_excludes_ml"
LIMIT_INCOMPLETE = "recipe_incomplete"  # `:<campo>`
WARN_ATWATER = catalog.WARN_ATWATER


class RecipeError(ValueError):
    """Entrada de receita recusada; `code` é estável (a rota o devolve e a tela traduz)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RecipeIngredient:
    """Ingrediente JÁ resolvido: `amount` na base do alimento (g ou ml) e `per_100` com os campos de
    `catalog.PER_100_FIELDS` (`None` = a fonte não informou)."""

    label: str
    amount: float
    basis: Unit
    per_100: Mapping[str, Any]
    limitations: tuple[str, ...] = ()  # códigos da resolução da quantidade (ex.: densidade assumida)


@dataclass(frozen=True)
class RecipeResult:
    totals: Mapping[str, Optional[float]]
    per_100_g: Mapping[str, Optional[float]]
    raw_weight_g: float
    final_weight_g: float
    yield_percent: Optional[float]
    incomplete_fields: tuple[str, ...]
    missing_by_field: Mapping[str, tuple[str, ...]]  # campo -> rótulos dos ingredientes que não informam
    atwater: Optional[AtwaterCheck]
    limitations: tuple[str, ...]
    warnings: tuple[str, ...]
    engine_version: str = RECIPE_ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "totals": dict(self.totals),
            "per_100_g": dict(self.per_100_g),
            "raw_weight_g": self.raw_weight_g,
            "final_weight_g": self.final_weight_g,
            "yield_percent": self.yield_percent,
            "incomplete_fields": list(self.incomplete_fields),
            "missing_by_field": {campo: list(rotulos) for campo, rotulos in self.missing_by_field.items()},
            "atwater": self.atwater.to_dict() if self.atwater else None,
            "limitations": list(self.limitations),
            "warnings": list(self.warnings),
            "engine_version": self.engine_version,
        }


def _atwater_of(per_100: Mapping[str, Optional[float]]) -> Optional[AtwaterCheck]:
    energia, carbo = per_100.get("energy_kcal"), per_100.get("carbohydrates_g")
    proteina, gordura = per_100.get("protein_g"), per_100.get("total_fat_g")
    if energia is None or carbo is None or proteina is None or gordura is None:
        return None
    return atwater_check(Macros(kcal=energia, protein_g=proteina, carb_g=carbo, fat_g=gordura))


def calculate_recipe(ingredients: Sequence[RecipeIngredient], final_weight_g: Any) -> RecipeResult:
    if not ingredients:
        raise RecipeError(ERR_NO_INGREDIENTS)
    if len(ingredients) > RECIPE_MAX_INGREDIENTS:
        raise RecipeError(ERR_TOO_MANY_INGREDIENTS)
    peso_final = positive_number(final_weight_g)
    if peso_final is None or peso_final > RECIPE_FINAL_WEIGHT_MAX_G:
        raise RecipeError(ERR_INVALID_FINAL_WEIGHT)
    for indice, ingrediente in enumerate(ingredients):
        if not isinstance(ingrediente.amount, (int, float)) or not math.isfinite(ingrediente.amount) or ingrediente.amount <= 0:
            raise RecipeError(f"{ERR_INVALID_INGREDIENT_AMOUNT}:{indice}")

    peso_cru = float(sum(i.amount for i in ingredients if i.basis == Unit.GRAM))
    limitacoes: list[str] = []
    tem_ml = any(i.basis == Unit.MILLILITER for i in ingredients)
    if tem_ml:
        limitacoes.append(LIMIT_RAW_WEIGHT_EXCLUDES_ML)
    # `<código>:<índice>` diz QUAL ingrediente (o texto sai "Ingrediente N: ...").
    limitacoes.extend(f"{codigo}:{indice}" for indice, i in enumerate(ingredients) for codigo in i.limitations)
    rendimento: Optional[float] = None
    # Com ml o peso cru é parcial: sem rendimento e sem a trava da faixa.
    if peso_cru > 0 and not tem_ml:
        razao = peso_final / peso_cru
        if not RECIPE_YIELD_MIN_SHARE <= razao <= RECIPE_YIELD_MAX_SHARE:
            raise RecipeError(ERR_YIELD_IMPLAUSIBLE)
        rendimento = razao * YIELD_PERCENT_SCALE

    totais, faltam = sum_scaled_fields([(i.amount, i.per_100) for i in ingredients], catalog.PER_100_FIELDS)
    incompletos = tuple(campo for campo in catalog.PER_100_FIELDS if faltam[campo])
    por_100: dict[str, Optional[float]] = {}
    for campo in catalog.PER_100_FIELDS:
        total = totais[campo]
        por_100[campo] = None if campo in incompletos or total is None else per_reference_from_serving(total, peso_final)
    limitacoes.extend(f"{LIMIT_INCOMPLETE}:{campo}" for campo in incompletos)
    checagem = _atwater_of(por_100)
    avisos = (WARN_ATWATER,) if checagem is not None and not checagem.consistent else ()
    return RecipeResult(
        totals=totais,
        per_100_g=por_100,
        raw_weight_g=peso_cru,
        final_weight_g=peso_final,
        yield_percent=rendimento,
        incomplete_fields=incompletos,
        missing_by_field={campo: tuple(ingredients[i].label for i in faltam[campo]) for campo in incompletos},
        atwater=checagem,
        limitations=tuple(limitacoes),
        warnings=avisos,
    )


@dataclass(frozen=True)
class RecipeEffective:
    """Valores por 100 g que valem (`effective`) × os calculados (`calculated`, preservados para a rota gravar os dois)."""

    effective: Mapping[str, Optional[float]]
    calculated: Mapping[str, Optional[float]]
    overridden_fields: tuple[str, ...]
    atwater: Optional[AtwaterCheck]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "effective": dict(self.effective),
            "calculated": dict(self.calculated),
            "overridden_fields": list(self.overridden_fields),
            "atwater": self.atwater.to_dict() if self.atwater else None,
            "warnings": list(self.warnings),
        }


def merged_warnings(result: RecipeResult, effective: RecipeEffective) -> tuple[str, ...]:
    """Avisos da prévia sem repetir: o do calculado e o do efetivo são o mesmo código (Atwater). Com substituição, vale
    só o do efetivo (os valores que serão gravados); o do calculado já não descreve a receita."""
    if effective.overridden_fields:
        return tuple(dict.fromkeys(effective.warnings))
    return tuple(dict.fromkeys((*result.warnings, *effective.warnings)))


def is_valid_id(value: Any) -> bool:
    """Id de alimento/porção de ingrediente dentro de 1..`RECIPE_ID_MAX` (fora disso a rota trata como inexistente)."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= RECIPE_ID_MAX


def apply_overrides(calculated_per_100: Mapping[str, Any], overrides: Optional[Mapping[str, Any]]) -> RecipeEffective:
    """Aplica substituições manuais por campo sobre o por 100 g calculado. Só campos de `PER_100_FIELDS`, valor
    não negativo e plausível (mesma checagem de `catalog.validate_food_values`, base g); o resto -> `RecipeError`."""
    calculados = {campo: non_negative_number(calculated_per_100.get(campo)) for campo in catalog.PER_100_FIELDS}
    # `None` = sem substituição para o campo (usa o calculado); campo desconhecido continua erro.
    substituicoes = {campo: valor for campo, valor in (overrides or {}).items() if valor is not None}
    for campo in overrides or {}:
        if campo not in catalog.PER_100_FIELDS:
            raise RecipeError(ERR_OVERRIDE_FIELD)
    for campo, valor in substituicoes.items():
        if non_negative_number(valor) is None:
            raise RecipeError(f"{ERR_OVERRIDE_VALUE}:{campo}")
    efetivos = {**calculados, **{campo: non_negative_number(valor) for campo, valor in substituicoes.items()}}
    validacao = catalog.validate_food_values(efetivos, Unit.GRAM, required=())
    for erro in validacao.errors:
        campo = erro.partition(":")[2]
        if campo in substituicoes:
            raise RecipeError(f"{ERR_OVERRIDE_VALUE}:{campo}")
    return RecipeEffective(
        effective={campo: validacao.values.get(campo) for campo in catalog.PER_100_FIELDS},
        calculated=calculados,
        overridden_fields=tuple(campo for campo in catalog.PER_100_FIELDS if campo in substituicoes),
        atwater=validacao.atwater,
        warnings=tuple(validacao.warnings),
    )


def ingredient_amount_errors(resolution: AmountResolution) -> tuple[str, ...]:
    """Quantidade de UM ingrediente: sem conversão, o erro do diário; na base, até `RECIPE_INGREDIENT_AMOUNT_MAX`
    (teto próprio da receita, maior que o do item do diário)."""
    if resolution.amount is None:
        return diary.item_amount_errors(resolution)
    return (ERR_INGREDIENT_AMOUNT_OUT_OF_RANGE,) if resolution.amount > RECIPE_INGREDIENT_AMOUNT_MAX else ()


def ingredient_key(food_id: Any, quantity: Any, unit: Any, portion_id: Any) -> tuple[int, float, str, Optional[int]]:
    """Identidade de um ingrediente na edição: igual à da versão atual = reaproveita o snapshot gravado."""
    normalizada = normalize_unit(unit)
    return (
        int(food_id), float(quantity), normalizada.value if normalizada else str(unit),
        None if portion_id is None else int(portion_id),
    )


def ingredient_catalog_status(snapshot_per_100: Optional[Mapping[str, Any]], current_per_100: Optional[Mapping[str, Any]]) -> str:
    """`unavailable` (alimento inativo/inacessível: `current_per_100` None), `outdated` (valores por 100 diferentes
    do snapshot) ou `current`."""
    if current_per_100 is None:
        return CATALOG_UNAVAILABLE
    antigo, novo = snapshot_per_100 or {}, current_per_100
    for campo in set(antigo) | set(novo):
        a, b = antigo.get(campo), novo.get(campo)
        if a is None or b is None:
            if a is not b:
                return CATALOG_OUTDATED
            continue
        try:
            igual = math.isclose(float(a), float(b), rel_tol=_PER_100_TOLERANCE, abs_tol=_PER_100_TOLERANCE)
        except (TypeError, ValueError):  # snapshot gravado com valor estranho: não é igual ao atual
            return CATALOG_OUTDATED
        if not igual:
            return CATALOG_OUTDATED
    return CATALOG_CURRENT


def overrides_from_effective(effective_json: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Substituições manuais de uma versão gravada (`RecipeEffective.to_dict()`): campo -> valor efetivo."""
    efetivo = effective_json or {}
    valores = efetivo.get("effective") or {}
    return {campo: valores.get(campo) for campo in efetivo.get("overridden_fields") or ()}


def publish_blockers(
    name: Any, final_weight_g: Any, ingredient_count: int, per_100: Mapping[str, Any], is_active: Any = True
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Requisitos para tornar a receita pública (como alimento): ativa (arquivada não publica), nome, peso final, ao
    menos um ingrediente e valores por 100 g válidos pela regra do catálogo. Devolve (códigos `recipe_publish_*`,
    erros do catálogo dos valores)."""
    codigos: list[str] = []
    if not is_active:
        codigos.append(ERR_PUBLISH_ARCHIVED)
    if not str(name or "").strip():
        codigos.append(ERR_PUBLISH_NAME_REQUIRED)
    if positive_number(final_weight_g) is None:
        codigos.append(ERR_PUBLISH_FINAL_WEIGHT_REQUIRED)
    if ingredient_count < 1:
        codigos.append(ERR_PUBLISH_NO_INGREDIENTS)
    erros = tuple(catalog.validate_food_values(per_100, "g", required=catalog.RECIPE_REQUIRED_PER_100_FIELDS).errors)
    if erros:
        codigos.append(ERR_PUBLISH_INVALID_VALUES)
    return tuple(codigos), erros


def can_view_recipe(owner_user_id: Optional[int], user_id: int) -> bool:
    """Pública (dono NULL) ou do próprio usuário; o resto é inexistente para ele. Vale também para o modo de preparo."""
    return owner_user_id is None or int(owner_user_id) == int(user_id)


def can_edit_recipe(owner_user_id: Optional[int], user_id: int, administers_catalog: bool) -> bool:
    """Privada: só o dono. Pública: só quem administra o catálogo (o autor original sem a concessão não edita).
    Regra única de posse de edição: `nutrition_access.can_edit_food` delega aqui."""
    if owner_user_id is None:
        return bool(administers_catalog)
    return int(owner_user_id) == int(user_id)


def copy_name(name: str) -> str:
    """Nome da cópia: sufixo `RECIPE_COPY_SUFFIX`, cortando o nome para caber em `RECIPE_NAME_MAX_CHARS`."""
    limite = RECIPE_NAME_MAX_CHARS - len(RECIPE_COPY_SUFFIX)
    return f"{(name or '').strip()[:limite]}{RECIPE_COPY_SUFFIX}"

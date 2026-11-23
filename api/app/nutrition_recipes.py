"""Receitas do usuário (issue #286, primeira fatia). Vive na api-core (`{proxy+}`).

A receita É uma linha de `food_items` (`source = recipe`, base g, privada do dono, valores por 100 g = os
EFETIVOS), então busca, diário, favoritos e porções funcionam como com qualquer alimento. Cada gravação cria uma
VERSÃO (`food_recipe_versions`, migração 125) com os ingredientes e o snapshot de cada um.

Rotas (prefixo `/nutrition/recipes`; `nutricao.alimentos`: `ver` nas leituras, `editar` nas demais):
- `POST /preview`      `RecipePreviewIn` -> cálculo SEM gravar
- `POST /`             `RecipeSaveIn` -> 201: alimento + versão 1 + ingredientes, numa transação
- `GET  /`             as receitas ativas do próprio usuário (sem ingredientes)
- `GET  /{food_id}`    receita + versão atual + ingredientes (`instructions` só para o dono)
- `PUT  /{food_id}`    `RecipeSaveIn` -> versão nova (version + 1) e atualiza a linha do alimento

Erros, três formatos para a tela: (1) 422 do corpo, no formato do pydantic (`detail` é lista); (2) 422 tipado
`{error, code, message}` (`recipe_*` do motor e dos ingredientes); (3) 400 `invalid_food` com `errors` (valores do
catálogo). Detalhe do 422 tipado: `recipe_*` e códigos do motor com `:<índice>` ou `:<campo>`; ingrediente
inexistente OU invisível ao usuário -> o MESMO `recipe_ingredient_not_found:<i>` (sem oráculo); receita de outro
-> 404 `food_not_found`, igual à inexistente. Valores efetivos fora das regras do catálogo -> 400 `invalid_food`
(a mesma resposta do cadastro de alimento). Tabelas ausentes: 503 citando a 125 (`nutrition_schema`).

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md`): somas, escala por 100 g, rendimento, substituições e
limitações são de `domain/nutrition/recipe`; quantidade na base é de `meal_math`; snapshot do alimento é o do
diário (`nutrition_meals.food_snapshot`). Esta camada resolve ids, chama o motor e grava o que ele devolveu.
Posse e visibilidade: `nutrition_access`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from . import nutrition_foods as foods_mod
from . import nutrition_meals as meals_mod
from .access import EffectivePermissions, require_permission
from .access.deps import permissions_for_request
from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503
from .domain.nutrition import catalog, diary, meal_math
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition import recipe as recipe_engine
from .nutrition_access import can_log_food, can_log_portion
from .nutrition_schema import require_food_tables, require_recipe_tables
from .request_models import FoodItemIn, RecipePreviewIn, RecipeSaveIn
from .security import Identity

router = APIRouter(prefix="/nutrition/recipes", tags=["nutrition"])

VERSIONS = "food_recipe_versions"
INGREDIENTS = "food_recipe_ingredients"
LIST_LIMIT = 500

ERR_NOT_FOUND = "food_not_found"
ERR_INGREDIENT_NOT_FOUND = "recipe_ingredient_not_found"  # `:<índice>`
ERR_INGREDIENT_IS_RECIPE = "recipe_ingredient_is_recipe"  # `:<índice>`
ERR_INGREDIENT_PORTION = "recipe_ingredient_portion_not_found"  # `:<índice>`

_VERSION_COLUMNS = (
    "id, food_id, version, final_weight_g, raw_weight_g, yield_percent, instructions, calculated_json, "
    "effective_json, engine_version, created_by, created_at"
)
CURRENT_VERSION_SQL = f"SELECT {_VERSION_COLUMNS} FROM {VERSIONS} WHERE food_id = %s ORDER BY version DESC LIMIT 1"
MAX_VERSION_SQL = f"SELECT COALESCE(MAX(version), 0) AS v FROM {VERSIONS} WHERE food_id = %s"
MAX_VERSION_FOR_UPDATE_SQL = f"{MAX_VERSION_SQL} FOR UPDATE"
INSERT_VERSION_SQL = (
    f"INSERT INTO {VERSIONS} (food_id, version, final_weight_g, raw_weight_g, yield_percent, instructions, "
    "calculated_json, effective_json, engine_version, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
INGREDIENTS_OF_VERSION_SQL = (
    "SELECT id, version_id, position, food_id, item_label, quantity, unit, portion_id, amount, basis, "
    f"catalog_snapshot_json FROM {INGREDIENTS} WHERE version_id = %s ORDER BY position, id"
)
INSERT_INGREDIENT_SQL = (
    f"INSERT INTO {INGREDIENTS} (version_id, position, food_id, item_label, quantity, unit, portion_id, amount, "
    "basis, catalog_snapshot_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
LIST_SQL = (
    f"SELECT id, name, serving_size, {', '.join(catalog.PER_100_FIELDS)}, updated_at FROM {foods_mod.FOODS} "
    f"WHERE source = %s AND owner_user_id = %s AND is_active = 1 ORDER BY name_normalized, id LIMIT {LIST_LIMIT}"
)


def latest_versions_sql(count: int) -> str:
    """Versão atual (maior `version`) de cada receita da lista: um `%s` por `food_id`."""
    marcas = ", ".join(["%s"] * count)
    return (
        f"SELECT food_id, version, created_at FROM {VERSIONS} v WHERE food_id IN ({marcas}) "
        f"AND version = (SELECT MAX(version) FROM {VERSIONS} WHERE food_id = v.food_id)"
    )


def _connect():
    return connect_or_503(get_conn)


def _error(status: int, code: str, message: Optional[str] = None, **extra: Any) -> HTTPException:
    texto = message or nutrition_labels.limitation_label(code)
    return HTTPException(status_code=status, detail={"error": code, "code": code, "message": texto, **extra})


def _not_found() -> HTTPException:
    return _error(404, ERR_NOT_FOUND, "Alimento não encontrado.")


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value) if isinstance(value, (Decimal, int, float)) else float(str(value))


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class _Resolved:
    position: int
    food_id: int
    portion_id: Optional[int]
    quantity: float
    unit: str
    snapshot: dict[str, Any]
    ingredient: recipe_engine.RecipeIngredient
    limitations: tuple[str, ...] = ()  # do `meal_math.resolve_amount` (ex.: densidade assumida g<->ml)


def _by_ids(cur: Any, sql_for: Any, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    unicos = sorted(set(int(i) for i in ids))
    if not unicos:
        return {}
    cur.execute(sql_for(len(unicos)), tuple(unicos))
    return {int(row["id"]): row for row in cur.fetchall() or []}


def _resolve_ingredients(cur: Any, identity: Identity, payload: RecipePreviewIn) -> list[_Resolved]:
    """Alimento que o usuário pode usar (regra do diário), não receita; porção do alimento; quantidade na base
    pelo `meal_math`. 422 com o índice antes de qualquer escrita."""
    uid = int(identity.user_id)
    itens = payload.ingredients
    alimentos = _by_ids(cur, meals_mod.foods_by_ids_sql, [i.food_id for i in itens])
    porcoes = _by_ids(cur, meals_mod.portions_by_ids_sql, [i.portion_id for i in itens if i.portion_id is not None])
    resolvidos: list[_Resolved] = []
    for indice, item in enumerate(itens):
        alimento = alimentos.get(int(item.food_id))
        if alimento is None or not can_log_food(identity, uid, alimento):
            raise _error(422, f"{ERR_INGREDIENT_NOT_FOUND}:{indice}", index=indice)
        if catalog.is_recipe_source(alimento.get("source")):
            raise _error(422, f"{ERR_INGREDIENT_IS_RECIPE}:{indice}", index=indice)
        porcao = None
        if item.portion_id is not None:
            porcao = porcoes.get(int(item.portion_id))
            if porcao is None or int(porcao["food_id"]) != int(item.food_id) or not can_log_portion(identity, uid, porcao):
                raise _error(422, f"{ERR_INGREDIENT_PORTION}:{indice}", index=indice)
        quantidade_porcao = meal_math.portion_amount_for(
            item.unit, porcao.get("amount") if porcao else None, alimento.get("serving_size")
        )
        resolucao = meal_math.resolve_amount(item.quantity, item.unit, alimento.get("basis"), quantidade_porcao)
        erros = diary.item_amount_errors(resolucao)
        if erros:
            raise _error(422, f"{erros[0]}:{indice}", index=indice)
        snapshot = {
            "food": meals_mod.food_snapshot(alimento),
            "portion": meals_mod.portion_snapshot(porcao) if porcao else None,
        }
        resolvidos.append(
            _Resolved(
                position=indice,
                food_id=int(item.food_id),
                portion_id=None if item.portion_id is None else int(item.portion_id),
                quantity=float(item.quantity),
                unit=resolucao.unit.value if resolucao.unit else str(item.unit),
                snapshot=snapshot,
                limitations=tuple(resolucao.limitations),
                ingredient=recipe_engine.RecipeIngredient(
                    label=str(alimento.get("name") or ""),
                    amount=float(resolucao.amount),  # type: ignore[arg-type]
                    basis=resolucao.basis,  # type: ignore[arg-type]
                    per_100=snapshot["food"]["per_100"],
                ),
            )
        )
    return resolvidos


def _compute(
    cur: Any, identity: Identity, payload: RecipePreviewIn
) -> tuple[list[_Resolved], recipe_engine.RecipeResult, recipe_engine.RecipeEffective]:
    resolvidos = _resolve_ingredients(cur, identity, payload)
    try:
        resultado = recipe_engine.calculate_recipe([r.ingredient for r in resolvidos], payload.final_weight_g)
        efetivo = recipe_engine.apply_overrides(resultado.per_100_g, payload.overrides)
    except recipe_engine.RecipeError as exc:
        raise _error(422, exc.code) from exc
    return resolvidos, resultado, efetivo


def _limitation_codes(resultado: recipe_engine.RecipeResult, resolvidos: Sequence[_Resolved]) -> list[str]:
    """Limitações do resultado + dos ingredientes: a mesma lista na prévia e na versão gravada."""
    return [*resultado.limitations, *(c for r in resolvidos for c in r.limitations)]


def _preview_public(
    resolvidos: Sequence[_Resolved], resultado: recipe_engine.RecipeResult, efetivo: recipe_engine.RecipeEffective
) -> dict[str, Any]:
    return {
        "ingredients": [
            {
                "position": r.position,
                "food_id": r.food_id,
                "portion_id": r.portion_id,
                "label": r.ingredient.label,
                "quantity": r.quantity,
                "unit": r.unit,
                "amount": r.ingredient.amount,
                "basis": r.ingredient.basis.value,
                "per_100": dict(r.ingredient.per_100),
            }
            for r in resolvidos
        ],
        "totals": dict(resultado.totals),
        "per_100_calculated": dict(resultado.per_100_g),
        "per_100_effective": dict(efetivo.effective),
        "overridden_fields": list(efetivo.overridden_fields),
        "raw_weight_g": resultado.raw_weight_g,
        "final_weight_g": resultado.final_weight_g,
        "yield_percent": resultado.yield_percent,
        "incomplete_fields": list(resultado.incomplete_fields),
        "missing_by_field": {c: list(r) for c, r in resultado.missing_by_field.items()},
        "atwater": efetivo.atwater.to_dict() if efetivo.atwater else None,
        "limitations": nutrition_labels.limitation_items(_limitation_codes(resultado, resolvidos)),
        "warnings": nutrition_labels.limitation_items([*resultado.warnings, *efetivo.warnings]),
        "engine_version": resultado.engine_version,
    }


def _food_values(
    payload: RecipeSaveIn, efetivo: recipe_engine.RecipeEffective, atual: Optional[dict[str, Any]] = None
) -> tuple[Any, ...]:
    """Valores de `food_items` na ordem de `_WRITE_COLUMNS`, validados pela regra do catálogo (400 `invalid_food`).
    No PUT (`atual` = linha travada), o que a receita não envia preserva o gravado; `serving_size: null` apaga."""
    extras: dict[str, Any] = {"serving_size": payload.serving_size}
    if atual is not None:
        extras = {
            "brand": atual.get("brand"),
            "category": atual.get("category"),
            "household_measure": atual.get("household_measure"),
            "servings_per_package": _num(atual.get("servings_per_package")),
            "serving_size": payload.serving_size
            if "serving_size" in payload.model_fields_set
            else _num(atual.get("serving_size")),
        }
    try:
        entrada = FoodItemIn(name=payload.name, basis="g", **extras, **dict(efetivo.effective))
    except ValidationError as exc:
        campo = str(exc.errors()[0]["loc"][0]) if exc.errors() and exc.errors()[0]["loc"] else "value"
        codigo = catalog.ERR_INVALID_NAME if campo == "name" else f"{catalog.ERR_INVALID_VALUE}:{campo}"
        raise foods_mod._invalid_food([codigo]) from exc
    valores, _validacao = foods_mod._validated_write_values(entrada)
    return valores


def _insert_version(
    cur: Any, food_id: int, version: int, payload: RecipeSaveIn, resolvidos: Sequence[_Resolved],
    resultado: recipe_engine.RecipeResult, efetivo: recipe_engine.RecipeEffective, user_id: int,
) -> None:
    instrucoes = (payload.instructions or "").strip() or None
    calculado = {**resultado.to_dict(), "limitation_codes": _limitation_codes(resultado, resolvidos)}
    cur.execute(
        INSERT_VERSION_SQL,
        (
            food_id, version, resultado.final_weight_g, resultado.raw_weight_g, resultado.yield_percent, instrucoes,
            json.dumps(calculado, ensure_ascii=False), json.dumps(efetivo.to_dict(), ensure_ascii=False),
            resultado.engine_version, user_id,
        ),
    )
    version_id = cur.lastrowid
    for r in resolvidos:
        cur.execute(
            INSERT_INGREDIENT_SQL,
            (
                int(version_id), r.position, r.food_id, r.ingredient.label, r.quantity, r.unit, r.portion_id,
                r.ingredient.amount, r.ingredient.basis.value, json.dumps(r.snapshot, ensure_ascii=False),
            ),
        )


def _recipe_row(cur: Any, food_id: int, identity: Identity, lock: bool = False) -> dict[str, Any]:
    """Receita visível ao usuário ou 404 igual ao de inexistente (sem oráculo). `lock`: leitura com FOR UPDATE."""
    cur.execute(f"{foods_mod.FOOD_BY_ID_SQL} FOR UPDATE" if lock else foods_mod.FOOD_BY_ID_SQL, (int(food_id),))
    linha = cur.fetchone()
    if (
        not linha
        or not linha.get("is_active", 1)
        or not catalog.is_recipe_source(linha.get("source"))
        # Estritamente do dono, inclusive para quem administra o catálogo; quando a receita puder ser pública
        # (segunda fatia), a regra de leitura será revista.
        or linha.get("owner_user_id") is None
        or int(linha["owner_user_id"]) != int(identity.user_id)
    ):
        raise _not_found()
    return linha


def _recipe_public(cur: Any, linha: dict[str, Any], identity: Identity, permissoes: EffectivePermissions) -> dict[str, Any]:
    cur.execute(CURRENT_VERSION_SQL, (int(linha["id"]),))
    versao = cur.fetchone()
    if versao is None:
        raise _not_found()
    cur.execute(INGREDIENTS_OF_VERSION_SQL, (int(versao["id"]),))
    ingredientes = cur.fetchall() or []
    dono = linha.get("owner_user_id") is not None and int(linha["owner_user_id"]) == int(identity.user_id)
    calculo = _json(versao.get("calculated_json")) or {}
    return {
        **foods_mod.food_public(linha, identity, permissoes),
        "recipe": {
            "version": int(versao["version"]),
            "created_at": _iso(versao.get("created_at")),
            "final_weight_g": _num(versao.get("final_weight_g")),
            "raw_weight_g": _num(versao.get("raw_weight_g")),
            "yield_percent": _num(versao.get("yield_percent")),
            "instructions": versao.get("instructions") if dono else None,
            "engine_version": versao.get("engine_version"),
            "calculation": calculo or None,
            # Versão anterior a este campo não tem `limitation_codes`: cai nas do resultado.
            "limitations": nutrition_labels.limitation_items(calculo.get("limitation_codes", calculo.get("limitations"))),
            "effective": _json(versao.get("effective_json")),
            "ingredients": [
                {
                    "position": int(i["position"]),
                    "food_id": int(i["food_id"]),
                    "portion_id": None if i.get("portion_id") is None else int(i["portion_id"]),
                    "label": i.get("item_label"),
                    "quantity": _num(i.get("quantity")),
                    "unit": i.get("unit"),
                    "amount": _num(i.get("amount")),
                    "basis": i.get("basis"),
                    "per_100": (_json(i.get("catalog_snapshot_json")) or {}).get("food", {}).get("per_100"),
                }
                for i in ingredientes
            ],
        },
    }


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.post("/preview")
def preview_recipe(
    payload: RecipePreviewIn, identity: Identity = Depends(require_permission("nutricao.alimentos", "editar"))
):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            return _preview_public(*_compute(cur, identity, payload))


@router.post("", status_code=201)
def create_recipe(
    request: Request, payload: RecipeSaveIn, identity: Identity = Depends(require_permission("nutricao.alimentos", "editar"))
):
    permissoes = permissions_for_request(request, identity)
    uid = int(identity.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            resolvidos, resultado, efetivo = _compute(cur, identity, payload)
            valores = _food_values(payload, efetivo)
            cur.execute(foods_mod.INSERT_FOOD_SQL, (catalog.FoodSource.RECIPE.value, None, *valores, uid, uid))
            food_id = cur.lastrowid
            if not food_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
                raise _error(500, "food_insert_failed", "Receita não encontrada após gravar.")
            _insert_version(cur, int(food_id), 1, payload, resolvidos, resultado, efetivo, uid)
            linha = _recipe_row(cur, int(food_id), identity)
            return _recipe_public(cur, linha, identity, permissoes)


@router.get("")
def list_recipes(identity: Identity = Depends(require_permission("nutricao.alimentos", "ver"))):
    uid = int(identity.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            cur.execute(LIST_SQL, (catalog.FoodSource.RECIPE.value, uid))
            linhas = cur.fetchall() or []
            versoes: dict[int, dict[str, Any]] = {}
            if linhas:
                ids = [int(r["id"]) for r in linhas]
                cur.execute(latest_versions_sql(len(ids)), tuple(ids))
                versoes = {int(v["food_id"]): v for v in cur.fetchall() or []}
    itens = []
    for r in linhas:
        versao = versoes.get(int(r["id"]))
        itens.append(
            {
                "id": int(r["id"]),
                "name": r.get("name"),
                "per_100": {c: _num(r.get(c)) for c in catalog.PER_100_FIELDS},
                "serving_size": _num(r.get("serving_size")),
                "version": int(versao["version"]) if versao else None,
                "version_created_at": _iso(versao.get("created_at")) if versao else None,
            }
        )
    return {"items": itens}


@router.get("/{food_id}")
def get_recipe(request: Request, food_id: int, identity: Identity = Depends(require_permission("nutricao.alimentos", "ver"))):
    permissoes = permissions_for_request(request, identity)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            linha = _recipe_row(cur, food_id, identity)
            return _recipe_public(cur, linha, identity, permissoes)


@router.put("/{food_id}")
def update_recipe(
    request: Request, food_id: int, payload: RecipeSaveIn,
    identity: Identity = Depends(require_permission("nutricao.alimentos", "editar")),
):
    permissoes = permissions_for_request(request, identity)
    uid = int(identity.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            # Leitura sem trava confere a posse (404 igual ao de inexistente): quem não é o dono não segura trava alheia.
            # Depois trava a linha: duas edições simultâneas não calculam o mesmo `version + 1` (UNIQUE) e as
            # mesmas condições valem de novo sobre a linha travada (arquivar concorrente -> 404).
            for lock in (False, True):
                linha = _recipe_row(cur, food_id, identity, lock=lock)
            resolvidos, resultado, efetivo = _compute(cur, identity, payload)
            valores = _food_values(payload, efetivo, linha)
            # Leitura com trava: sem ela o MAX viria do retrato antigo (REPEATABLE READ) e repetiria a versão.
            cur.execute(MAX_VERSION_FOR_UPDATE_SQL, (int(food_id),))
            proxima = int((cur.fetchone() or {}).get("v") or 0) + 1
            cur.execute(foods_mod.UPDATE_FOOD_SQL, (*valores, int(food_id)))
            try:
                _insert_version(cur, int(food_id), proxima, payload, resolvidos, resultado, efetivo, uid)
            except Exception as exc:
                if is_duplicate_key_error(exc):
                    raise _error(409, "recipe_version_conflict")
                raise
            linha = _recipe_row(cur, food_id, identity)
            return _recipe_public(cur, linha, identity, permissoes)

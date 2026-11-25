"""Receitas do usuário (issue #286, primeira fatia). Vive na api-core (`{proxy+}`).

A receita É uma linha de `food_items` (`source = recipe`, base g, privada do dono, valores por 100 g = os
EFETIVOS), então busca, diário, favoritos e porções funcionam como com qualquer alimento. Cada gravação cria uma
VERSÃO (`food_recipe_versions`, migração 125) com os ingredientes e o snapshot de cada um.

Rotas (prefixo `/nutrition/recipes`; `nutricao.alimentos`: `ver` nas leituras, `editar` nas demais):
- `POST /preview`      `RecipePreviewIn` -> cálculo SEM gravar
- `POST /`             `RecipeSaveIn` -> 201: alimento + versão 1 + ingredientes, numa transação
- `GET  /?scope=`      `mine` (padrão, ativas do usuário) | `public` | `favorites` (favoritas visíveis); sem ingredientes
- `GET  /{food_id}`    receita + versão atual + ingredientes e `instructions` (própria ou PÚBLICA; privada alheia = 404)
- `PUT  /{food_id}`    privada: só o dono; pública: só `admin.catalogos:editar` (outro -> 403 `recipe_public_edit_forbidden`,
                       inclusive o autor sem a concessão). `RecipeSaveIn` -> versão nova (version + 1) e atualiza a linha; ingrediente igual
                       ao da versão atual (food_id, quantity, unit, portion_id) reaproveita o snapshot gravado, salvo
                       `refresh_ingredients: true` (todos resolvem do catálogo atual)
- `GET  /{food_id}/versions`    versões (número, criado em, peso final, energia por 100 g efetiva), receita visível
- `POST /{food_id}/duplicate`   201: receita nova privada do chamador a partir da versão atual de receita pública ou própria
Publicar: `POST /nutrition/foods/{id}/promote` (`admin.catalogos:editar`, em `nutrition_foods`) aplica
`recipe.publish_blockers` (422 `recipe_publish_*`) e torna a linha e as porções públicas no lugar.
Ingrediente de pública vindo de alimento privado do autor: só o snapshot gravado; `catalog_status` de quem não é o
autor sai `unavailable`.
Leitura: cada ingrediente traz `catalog_status` (`current`|`outdated`|`unavailable`) contra o catálogo de hoje.

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
from typing import Any, Literal, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from . import nutrition_foods as foods_mod
from . import nutrition_meals as meals_mod
from .access import EffectivePermissions, require_permission
from .access.deps import permissions_for_request
from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503
from .domain.nutrition import catalog, meal_math
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition import recipe as recipe_engine
from .nutrition_access import administers_public_catalog, can_log_food, can_log_portion, ownership
from .nutrition_schema import require_food_tables, require_recipe_tables
from .request_models import FoodItemIn, RecipePreviewIn, RecipeSaveIn
from .security import Identity

router = APIRouter(prefix="/nutrition/recipes", tags=["nutrition"])

VERSIONS = "food_recipe_versions"
INGREDIENTS = "food_recipe_ingredients"

ERR_NOT_FOUND = "food_not_found"
ERR_INGREDIENT_NOT_FOUND = recipe_engine.ERR_INGREDIENT_NOT_FOUND
ERR_INGREDIENT_IS_RECIPE = recipe_engine.ERR_INGREDIENT_IS_RECIPE
ERR_INGREDIENT_PORTION = recipe_engine.ERR_INGREDIENT_PORTION_NOT_FOUND

_VERSION_COLUMNS = (
    "id, food_id, version, final_weight_g, raw_weight_g, yield_percent, instructions, calculated_json, "
    "effective_json, engine_version, created_by, created_at"
)
CURRENT_VERSION_SQL = f"SELECT {_VERSION_COLUMNS} FROM {VERSIONS} WHERE food_id = %s ORDER BY version DESC LIMIT 1"
VERSIONS_LIST_SQL = (
    f"SELECT version, created_at, final_weight_g, effective_json FROM {VERSIONS} WHERE food_id = %s ORDER BY version DESC"
)
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
_LIST_HEAD = (
    f"SELECT id, name, serving_size, owner_user_id, {', '.join(catalog.PER_100_FIELDS)}, updated_at "
    f"FROM {foods_mod.FOODS} WHERE source = %s AND is_active = 1"
)
_LIST_TAIL = "ORDER BY name_normalized, id LIMIT %s OFFSET %s"  # + (limit, offset) ao fim dos parâmetros
LIST_SQL = f"{_LIST_HEAD} AND owner_user_id = %s {_LIST_TAIL}"
LIST_PUBLIC_SQL = f"{_LIST_HEAD} AND owner_user_id IS NULL {_LIST_TAIL}"
# Favoritas (tabela dos alimentos, migração 102): só as que o usuário pode ver (públicas ou dele).
LIST_FAVORITES_SQL = (
    f"{_LIST_HEAD} AND {foods_mod.FAVORITES_ONLY_SQL} AND (owner_user_id IS NULL OR owner_user_id = %s) {_LIST_TAIL}"
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


def _by_ids(cur: Any, sql_for: Any, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    unicos = sorted(set(int(i) for i in ids))
    if not unicos:
        return {}
    cur.execute(sql_for(len(unicos)), tuple(unicos))
    return {int(row["id"]): row for row in cur.fetchall() or []}


def _snapshot_parts(bruto: Any) -> Optional[tuple[dict[str, Any], Optional[dict[str, Any]]]]:
    """(food, portion) do snapshot gravado, ou `None` se não tem a forma esperada (food dict com `per_100` dict;
    `portion` ausente/nula ou dict). Nunca levanta: snapshot estranho não pode virar 500."""
    try:
        snapshot = _json(bruto)
    except ValueError:
        return None
    food = snapshot.get("food") if isinstance(snapshot, dict) else None
    porcao = snapshot.get("portion") if isinstance(snapshot, dict) else None
    if not isinstance(food, dict) or not isinstance(food.get("per_100"), dict):
        return None
    return (food, porcao) if porcao is None or isinstance(porcao, dict) else None


def _snapshot_valido(bruto: Any) -> bool:
    return _snapshot_parts(bruto) is not None


def _snapshot_per_100(bruto: Any) -> Optional[dict[str, Any]]:
    partes = _snapshot_parts(bruto)
    return partes[0]["per_100"] if partes else None


def _item_key(row: dict[str, Any]) -> tuple[int, float, str, Optional[int]]:
    return recipe_engine.ingredient_key(row["food_id"], row["quantity"], row["unit"], row.get("portion_id"))


def _resolve_ingredients(
    cur: Any, identity: Identity, payload: RecipePreviewIn, reuse: Optional[dict[Any, dict[str, Any]]] = None
) -> list[_Resolved]:
    """Alimento que o usuário pode usar (regra do diário), não receita; porção do alimento; quantidade na base
    pelo `meal_math`. 422 com o índice antes de qualquer escrita. `reuse` (edição): ingrediente com a mesma chave da
    versão atual usa o snapshot gravado (sem catálogo, sem checar acesso: a receita salva continua válida)."""
    uid = int(identity.user_id)
    itens = payload.ingredients
    reuse = reuse or {}
    antigos = [reuse.get(recipe_engine.ingredient_key(i.food_id, i.quantity, i.unit, i.portion_id)) for i in itens]
    # Snapshot gravado nulo/malformado não serve: o ingrediente é resolvido do catálogo como novo.
    antigos = [a if a is not None and _snapshot_valido(a.get("catalog_snapshot_json")) else None for a in antigos]
    novos = [i for i, antigo in zip(itens, antigos) if antigo is None]
    # Id fora de 1..RECIPE_ID_MAX nem vai ao banco: cai nos mesmos "não encontrado" abaixo.
    alimentos = _by_ids(cur, meals_mod.foods_by_ids_sql, [i.food_id for i in novos if recipe_engine.is_valid_id(i.food_id)])
    porcoes = _by_ids(
        cur, meals_mod.portions_by_ids_sql,
        [i.portion_id for i in novos if i.portion_id is not None and recipe_engine.is_valid_id(i.portion_id)],
    )
    resolvidos: list[_Resolved] = []
    for indice, (item, antigo) in enumerate(zip(itens, antigos)):
        if antigo is not None:
            snapshot = _json(antigo.get("catalog_snapshot_json"))
            alimento, porcao = snapshot["food"], snapshot.get("portion")  # forma já garantida por `_snapshot_parts`
        else:
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
            snapshot = {
                "food": meals_mod.food_snapshot(alimento),
                "portion": meals_mod.portion_snapshot(porcao) if porcao else None,
            }
        quantidade_porcao = meal_math.portion_amount_for(
            item.unit, porcao.get("amount") if porcao else None, alimento.get("serving_size")
        )
        resolucao = meal_math.resolve_amount(item.quantity, item.unit, alimento.get("basis"), quantidade_porcao)
        erros = recipe_engine.ingredient_amount_errors(resolucao)
        if erros:
            raise _error(422, f"{erros[0]}:{indice}", index=indice)
        resolvidos.append(
            _Resolved(
                position=indice,
                food_id=int(item.food_id),
                portion_id=None if item.portion_id is None else int(item.portion_id),
                quantity=float(item.quantity),
                unit=resolucao.unit.value if resolucao.unit else str(item.unit),
                snapshot=snapshot,
                ingredient=recipe_engine.RecipeIngredient(
                    label=str(alimento.get("name") or ""),
                    amount=float(resolucao.amount),  # type: ignore[arg-type]
                    basis=resolucao.basis,  # type: ignore[arg-type]
                    per_100=snapshot["food"]["per_100"],
                    limitations=tuple(resolucao.limitations),
                ),
            )
        )
    return resolvidos


def _compute(
    cur: Any, identity: Identity, payload: RecipePreviewIn, reuse: Optional[dict[Any, dict[str, Any]]] = None
) -> tuple[list[_Resolved], recipe_engine.RecipeResult, recipe_engine.RecipeEffective]:
    resolvidos = _resolve_ingredients(cur, identity, payload, reuse)
    try:
        resultado = recipe_engine.calculate_recipe([r.ingredient for r in resolvidos], payload.final_weight_g)
        efetivo = recipe_engine.apply_overrides(resultado.per_100_g, payload.overrides)
    except recipe_engine.RecipeError as exc:
        raise _error(422, exc.code) from exc
    return resolvidos, resultado, efetivo


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
        "limitations": nutrition_labels.limitation_items(resultado.limitations),
        "warnings": nutrition_labels.limitation_items(recipe_engine.merged_warnings(resultado, efetivo)),
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
    valores, _validacao = foods_mod._validated_write_values(entrada, required=catalog.RECIPE_REQUIRED_PER_100_FIELDS)
    return valores


def _insert_version(
    cur: Any, food_id: int, version: int, payload: RecipeSaveIn, resolvidos: Sequence[_Resolved],
    resultado: recipe_engine.RecipeResult, efetivo: recipe_engine.RecipeEffective, user_id: int,
) -> None:
    instrucoes = (payload.instructions or "").strip() or None
    calculado = resultado.to_dict()  # `limitations` já traz as dos ingredientes (versões antigas: `limitation_codes`)
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
        # Pública ou do próprio usuário (inclusive para quem administra o catálogo: privada alheia segue 404).
        or not recipe_engine.can_view_recipe(linha.get("owner_user_id"), int(identity.user_id))
    ):
        raise _not_found()
    return linha


def promotion_blockers(cur: Any, linha: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Requisitos de publicação da receita `linha` (regra do motor) sobre a versão atual gravada. Sem as tabelas da
    migração 125: 503 (nunca um erro de SQL)."""
    require_recipe_tables(cur)
    versao, ingredientes = _current_ingredients(cur, int(linha["id"]))
    return recipe_engine.publish_blockers(
        linha.get("name"), versao.get("final_weight_g") if versao else None, len(ingredientes),
        {c: _num(linha.get(c)) for c in catalog.PER_100_FIELDS}, linha.get("is_active", 1),
    )


def _current_ingredients(
    cur: Any, food_id: int, lock: bool = False
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """`lock`: leituras com FOR UPDATE (veem a versão confirmada mais recente, não o retrato da transação)."""
    sufixo = " FOR UPDATE" if lock else ""
    cur.execute(CURRENT_VERSION_SQL + sufixo, (int(food_id),))
    versao = cur.fetchone()
    if versao is None:
        return None, []
    cur.execute(INGREDIENTS_OF_VERSION_SQL + sufixo, (int(versao["id"]),))
    return versao, cur.fetchall() or []


def _create(
    cur: Any, identity: Identity, permissoes: EffectivePermissions, payload: RecipeSaveIn,
    reuse: Optional[dict[Any, dict[str, Any]]] = None,
) -> dict[str, Any]:
    uid = int(identity.user_id)
    resolvidos, resultado, efetivo = _compute(cur, identity, payload, reuse)
    valores = _food_values(payload, efetivo)
    cur.execute(foods_mod.INSERT_FOOD_SQL, (catalog.FoodSource.RECIPE.value, None, *valores, uid, uid))
    food_id = cur.lastrowid
    if not food_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
        raise _error(500, "food_insert_failed", "Receita não encontrada após gravar.")
    _insert_version(cur, int(food_id), 1, payload, resolvidos, resultado, efetivo, uid)
    linha = _recipe_row(cur, int(food_id), identity)
    return _recipe_public(cur, linha, identity, permissoes)


def _recipe_public(cur: Any, linha: dict[str, Any], identity: Identity, permissoes: EffectivePermissions) -> dict[str, Any]:
    cur.execute(CURRENT_VERSION_SQL, (int(linha["id"]),))
    versao = cur.fetchone()
    if versao is None:
        raise _not_found()
    cur.execute(INGREDIENTS_OF_VERSION_SQL, (int(versao["id"]),))
    ingredientes = cur.fetchall() or []
    atuais = _by_ids(cur, meals_mod.foods_by_ids_sql, [i["food_id"] for i in ingredientes])  # uma consulta, sem N+1
    uid = int(identity.user_id)

    def _status(ingrediente: dict[str, Any]) -> str:
        # Alimento privado do autor, visto por outro usuário: `can_log_food` nega -> "unavailable" (sem o estado atual).
        alimento = atuais.get(int(ingrediente["food_id"]))
        visivel = alimento is not None and alimento.get("is_active", 1) and can_log_food(identity, uid, alimento)
        valores = meals_mod.food_snapshot(alimento)["per_100"] if visivel else None
        gravado = _snapshot_per_100(ingrediente.get("catalog_snapshot_json"))
        return recipe_engine.ingredient_catalog_status(gravado, valores)

    calculo = _json(versao.get("calculated_json")) or {}
    return {
        **foods_mod.food_public(linha, identity, permissoes),
        "recipe": {
            "version": int(versao["version"]),
            "created_at": _iso(versao.get("created_at")),
            "final_weight_g": _num(versao.get("final_weight_g")),
            "raw_weight_g": _num(versao.get("raw_weight_g")),
            "yield_percent": _num(versao.get("yield_percent")),
            "instructions": versao.get("instructions"),  # a linha já passou por `_recipe_row`: pública ou do usuário
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
                    "per_100": _snapshot_per_100(i.get("catalog_snapshot_json")),
                    "catalog_status": _status(i),
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
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            return _create(cur, identity, permissoes, payload)


@router.get("")
def list_recipes(
    scope: Literal["mine", "public", "favorites"] = "mine",
    limit: int = Query(default=recipe_engine.RECIPE_LIST_DEFAULT_LIMIT, ge=1, le=recipe_engine.RECIPE_LIST_MAX_LIMIT),
    offset: int = Query(default=0, ge=0, le=recipe_engine.RECIPE_LIST_MAX_OFFSET),
    identity: Identity = Depends(require_permission("nutricao.alimentos", "ver")),
):
    """`scope`: `mine` (padrão: as do usuário), `public` (do catálogo) ou `favorites` (favoritadas, públicas e próprias).
    `limit`/`offset` paginam (tetos do motor); sem eles vale o comportamento anterior (até o teto, do início)."""
    uid = int(identity.user_id)
    fonte = catalog.FoodSource.RECIPE.value
    sql, params = {
        "mine": (LIST_SQL, (fonte, uid)),
        "public": (LIST_PUBLIC_SQL, (fonte,)),
        "favorites": (LIST_FAVORITES_SQL, (fonte, uid, uid)),
    }[scope]
    params = (*params, int(limit), int(offset))
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            cur.execute(sql, params)
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
                **ownership(identity, r.get("owner_user_id")),
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
                # Pública: só quem administra o catálogo edita (403 tipado: a pública existe para todos).
                if not recipe_engine.can_edit_recipe(
                    linha.get("owner_user_id"), uid, administers_public_catalog(permissoes, "editar")
                ):
                    raise _error(recipe_engine.ERR_PUBLIC_EDIT_FORBIDDEN_STATUS, recipe_engine.ERR_PUBLIC_EDIT_FORBIDDEN)
            reuse = {} if payload.refresh_ingredients else {
                _item_key(i): i for i in _current_ingredients(cur, int(food_id), lock=True)[1]
            }
            resolvidos, resultado, efetivo = _compute(cur, identity, payload, reuse)
            valores = _food_values(payload, efetivo, linha)
            # Leitura com trava: sem ela o MAX viria do retrato antigo (REPEATABLE READ) e repetiria a versão.
            cur.execute(MAX_VERSION_FOR_UPDATE_SQL, (int(food_id),))
            proxima = int((cur.fetchone() or {}).get("v") or 0) + 1
            cur.execute(foods_mod.UPDATE_FOOD_SQL, (*valores, int(food_id)))
            try:
                _insert_version(cur, int(food_id), proxima, payload, resolvidos, resultado, efetivo, uid)
            except Exception as exc:
                if is_duplicate_key_error(exc):
                    raise _error(409, recipe_engine.ERR_VERSION_CONFLICT) from exc
                raise
            linha = _recipe_row(cur, food_id, identity)
            return _recipe_public(cur, linha, identity, permissoes)


@router.get("/{food_id}/versions")
def list_versions(food_id: int, identity: Identity = Depends(require_permission("nutricao.alimentos", "ver"))):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            _recipe_row(cur, food_id, identity)
            cur.execute(VERSIONS_LIST_SQL, (int(food_id),))
            versoes = cur.fetchall() or []
    return {
        "items": [
            {
                "version": int(v["version"]),
                "created_at": _iso(v.get("created_at")),
                "final_weight_g": _num(v.get("final_weight_g")),
                "energy_kcal_per_100g": ((_json(v.get("effective_json")) or {}).get("effective") or {}).get("energy_kcal"),
            }
            for v in versoes
        ]
    }


@router.post("/{food_id}/duplicate", status_code=201)
def duplicate_recipe(
    request: Request, food_id: int, identity: Identity = Depends(require_permission("nutricao.alimentos", "editar"))
):
    """Cópia privada do chamador, de receita pública ou própria (`_recipe_row`); ingredientes com o snapshot da versão atual."""
    permissoes = permissions_for_request(request, identity)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_recipe_tables(cur)
            linha = _recipe_row(cur, food_id, identity)
            versao, ingredientes = _current_ingredients(cur, int(food_id))
            if versao is None:
                raise _not_found()
            payload = RecipeSaveIn(
                name=recipe_engine.copy_name(str(linha.get("name") or "")),
                ingredients=[
                    {
                        "food_id": int(i["food_id"]), "quantity": _num(i["quantity"]), "unit": i["unit"],
                        "portion_id": None if i.get("portion_id") is None else int(i["portion_id"]),
                    }
                    for i in ingredientes
                ],
                final_weight_g=_num(versao["final_weight_g"]),
                overrides=recipe_engine.overrides_from_effective(_json(versao.get("effective_json"))) or None,
                serving_size=_num(linha.get("serving_size")),
                instructions=versao.get("instructions"),
            )
            return _create(cur, identity, permissoes, payload, {_item_key(i): i for i in ingredientes})

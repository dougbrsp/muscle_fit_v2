"""Alimentos preferidos do usuario (nutricao N3, migracao 102). Vive na api-core
(`{proxy+}`).

Rotas (prefixo `/nutrition/favorites`; a lista e PESSOAL: nenhuma rota recebe
`user_id`, ver `nutrition_access.favorites_owner`):
- `GET    /`               a lista do usuario, na ordem dele, cada item com o
                           alimento (`food_public`) e a porcao padrao
- `POST   /`               `FavoriteCreateIn` -> favorita (201); ja favorito -> 200
                           sem alterar a porcao (use o PUT)
- `PUT    /{food_id}`      `FavoriteDefaultIn` -> troca (ou limpa) a porcao padrao
- `DELETE /{food_id}`      desfavorita
- `POST   /reorder`        `FavoriteReorderIn` -> nova ordem da lista inteira

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md`, secao dos preferidos):
teto da lista, porcao padrao (a mesma conversao do diario: `meal_math` e
`diary`) e ordem sao de `domain/nutrition/favorites`; textos de `labels`;
visibilidade de alimento/porcao e dono da lista de `nutrition_access`. Esta
camada so monta o SQL e devolve o que o motor decidiu. Tabela ausente: 503
citando a 102 (`nutrition_schema`).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from .domain.nutrition import favorites as favorites_engine
from .domain.nutrition import labels as nutrition_labels
from .nutrition_access import can_favorite_food, can_favorite_portion, favorites_owner
from .nutrition_foods import FOOD_COLUMNS, FOODS, PORTION_COLUMNS, PORTIONS, _connect, _error, _iso, _num, food_public
from .nutrition_schema import require_favorites_table, require_food_tables
from .request_models import FavoriteCreateIn, FavoriteDefaultIn, FavoriteReorderIn
from .security import Identity, get_current_identity

router = APIRouter(prefix="/nutrition/favorites", tags=["nutrition"])

TABLE = "user_favorite_foods"
_COLUMNS = "id, user_id, food_id, default_quantity, default_unit, default_portion_id, position, created_at, updated_at"

FAVORITES_OF_USER_SQL = f"SELECT {_COLUMNS} FROM {TABLE} WHERE user_id = %s ORDER BY position, id"
FAVORITE_BY_FOOD_SQL = f"SELECT {_COLUMNS} FROM {TABLE} WHERE user_id = %s AND food_id = %s"
COUNT_AND_MAX_POSITION_SQL = f"SELECT COUNT(*) AS n, MAX(position) AS max_position FROM {TABLE} WHERE user_id = %s"
INSERT_FAVORITE_SQL = (
    f"INSERT INTO {TABLE} (user_id, food_id, default_quantity, default_unit, default_portion_id, position) "
    "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE id = id"
)
UPDATE_DEFAULT_SQL = (
    f"UPDATE {TABLE} SET default_quantity = %s, default_unit = %s, default_portion_id = %s "
    "WHERE user_id = %s AND food_id = %s"
)
UPDATE_POSITION_SQL = f"UPDATE {TABLE} SET position = %s WHERE user_id = %s AND food_id = %s"
DELETE_FAVORITE_SQL = f"DELETE FROM {TABLE} WHERE user_id = %s AND food_id = %s"
FOOD_SQL = f"SELECT {', '.join(FOOD_COLUMNS)} FROM {FOODS} WHERE id = %s"
PORTION_SQL = f"SELECT {PORTION_COLUMNS} FROM {PORTIONS} WHERE id = %s"


def foods_by_ids_sql(count: int) -> str:
    return f"SELECT {', '.join(FOOD_COLUMNS)} FROM {FOODS} WHERE id IN ({', '.join(['%s'] * count)})"


def portions_by_ids_sql(count: int) -> str:
    return f"SELECT {PORTION_COLUMNS} FROM {PORTIONS} WHERE id IN ({', '.join(['%s'] * count)})"


def _not_found() -> HTTPException:
    return _error(404, "favorite_not_found", "Este alimento não está nos seus preferidos.")


def _food_not_found() -> HTTPException:
    return _error(404, "food_not_found", "Alimento não encontrado.")


def _invalid(codes: list[str] | tuple[str, ...], status: int = 400) -> HTTPException:
    return _error(
        status,
        "invalid_favorite",
        "Preferido fora das regras.",
        errors=nutrition_labels.limitation_items(list(codes)),
    )


def _item_public(
    favorito: dict[str, Any], alimento: dict[str, Any], porcao: Optional[dict[str, Any]], identity: Identity
) -> dict[str, Any]:
    quantidade = favorito.get("default_quantity")
    padrao = None
    if quantidade is not None and favorito.get("default_unit"):
        padrao = {
            "quantity": _num(quantidade),
            "unit": favorito.get("default_unit"),
            "portion_id": None if favorito.get("default_portion_id") is None else int(favorito["default_portion_id"]),
            "portion_label": porcao.get("label") if porcao else None,
            "portion_amount": _num(porcao.get("amount")) if porcao else None,
        }
    return {
        "food": food_public(alimento, identity),
        "position": int(favorito.get("position") or 0),
        "default": padrao,
        "created_at": _iso(favorito.get("created_at")),
        "updated_at": _iso(favorito.get("updated_at")),
    }


def _by_id(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(r["id"]): r for r in rows}


def _listed(cur: Any, identity: Identity) -> list[dict[str, Any]]:
    """Favoritos do usuário cujo alimento ele ainda pode usar, na ordem dele."""
    cur.execute(FAVORITES_OF_USER_SQL, (favorites_owner(identity),))
    favoritos = cur.fetchall() or []
    if not favoritos:
        return []
    ids = sorted({int(f["food_id"]) for f in favoritos})
    cur.execute(foods_by_ids_sql(len(ids)), tuple(ids))
    alimentos = _by_id(cur.fetchall() or [])
    porcao_ids = sorted({int(f["default_portion_id"]) for f in favoritos if f.get("default_portion_id") is not None})
    porcoes: dict[int, dict[str, Any]] = {}
    if porcao_ids:
        cur.execute(portions_by_ids_sql(len(porcao_ids)), tuple(porcao_ids))
        porcoes = _by_id(cur.fetchall() or [])
    itens = []
    for favorito in favoritos:
        alimento = alimentos.get(int(favorito["food_id"]))
        if alimento is None or not can_favorite_food(identity, alimento):
            continue
        porcao = porcoes.get(int(favorito["default_portion_id"])) if favorito.get("default_portion_id") is not None else None
        itens.append(_item_public(favorito, alimento, porcao, identity))
    return itens


def _response(cur: Any, identity: Identity) -> dict[str, Any]:
    return {"items": _listed(cur, identity), "max_items": favorites_engine.MAX_FAVORITES}


def _usable_food(cur: Any, identity: Identity, food_id: int) -> dict[str, Any]:
    cur.execute(FOOD_SQL, (int(food_id),))
    alimento = cur.fetchone()
    if not alimento or not can_favorite_food(identity, alimento):
        raise _food_not_found()  # não revela o privado de outro
    return alimento


def _validated_default(
    cur: Any, identity: Identity, alimento: dict[str, Any], payload: FavoriteDefaultIn
) -> tuple[Optional[float], Optional[str], Optional[int]]:
    """Valida a porção padrão no motor e devolve `(quantidade, unidade, porcao_id)` para gravar."""
    unidade = None if payload.default_unit is None else (
        favorites_engine.canonical_unit(payload.default_unit) or payload.default_unit.strip().lower()
    )
    porcao: Optional[dict[str, Any]] = None
    if payload.default_portion_id is not None:
        cur.execute(PORTION_SQL, (int(payload.default_portion_id),))
        porcao = cur.fetchone()
        if (
            porcao is None
            or int(porcao["food_id"]) != int(alimento["id"])
            or not can_favorite_portion(identity, porcao)
        ):
            raise _error(404, "portion_not_found", "Porção não encontrada para este alimento.")
    erros = favorites_engine.default_errors(
        payload.default_quantity,
        unidade,
        payload.default_portion_id,
        alimento.get("basis"),
        None if porcao is None else porcao.get("amount"),
        alimento.get("serving_size"),
    )
    if erros:
        raise _invalid(erros)
    quantidade = None if payload.default_quantity is None else float(payload.default_quantity)
    return quantidade, unidade, None if payload.default_portion_id is None else int(payload.default_portion_id)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.get("")
def list_favorites(identity: Identity = Depends(get_current_identity)):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_favorites_table(cur)
            require_food_tables(cur)
            return _response(cur, identity)


@router.post("/reorder")
def reorder_favorites(payload: FavoriteReorderIn, identity: Identity = Depends(get_current_identity)):
    dono = favorites_owner(identity)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_favorites_table(cur)
            require_food_tables(cur)
            atuais = [int(item["food"]["id"]) for item in _listed(cur, identity)]
            erros = favorites_engine.order_errors(atuais, payload.food_ids)
            if erros:
                raise _invalid(erros)
            for food_id, posicao in favorites_engine.positions_for(payload.food_ids):
                cur.execute(UPDATE_POSITION_SQL, (posicao, dono, food_id))
            return _response(cur, identity)


@router.post("", status_code=201)
def add_favorite(response: Response, payload: FavoriteCreateIn, identity: Identity = Depends(get_current_identity)):
    dono = favorites_owner(identity)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_favorites_table(cur)
            require_food_tables(cur)
            alimento = _usable_food(cur, identity, payload.food_id)
            cur.execute(FAVORITE_BY_FOOD_SQL, (dono, int(payload.food_id)))
            existente = cur.fetchone()
            if existente is not None:
                response.status_code = 200  # já era favorito: nada muda (a porção padrão é do PUT)
            else:
                quantidade, unidade, porcao_id = _validated_default(cur, identity, alimento, payload)
                cur.execute(COUNT_AND_MAX_POSITION_SQL, (dono,))
                resumo = cur.fetchone() or {}
                if not favorites_engine.can_add(int(resumo.get("n") or 0)):
                    raise _invalid([favorites_engine.ERR_TOO_MANY_FAVORITES], status=409)
                cur.execute(
                    INSERT_FAVORITE_SQL,
                    (dono, int(payload.food_id), quantidade, unidade, porcao_id,
                     favorites_engine.next_position(resumo.get("max_position"))),
                )
            cur.execute(FAVORITE_BY_FOOD_SQL, (dono, int(payload.food_id)))
            favorito = cur.fetchone()
            if favorito is None:  # pragma: no cover - defesa: a linha acabou de ser gravada
                raise _error(500, "favorite_insert_failed", "Preferido não encontrado após gravar.")
            porcao = None
            if favorito.get("default_portion_id") is not None:
                cur.execute(PORTION_SQL, (int(favorito["default_portion_id"]),))
                porcao = cur.fetchone()
            item = _item_public(favorito, alimento, porcao, identity)
    return item


@router.put("/{food_id}")
def update_favorite_default(
    payload: FavoriteDefaultIn,
    food_id: int = Path(..., gt=0),
    identity: Identity = Depends(get_current_identity),
):
    dono = favorites_owner(identity)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_favorites_table(cur)
            require_food_tables(cur)
            cur.execute(FAVORITE_BY_FOOD_SQL, (dono, int(food_id)))
            if cur.fetchone() is None:
                raise _not_found()
            alimento = _usable_food(cur, identity, food_id)
            quantidade, unidade, porcao_id = _validated_default(cur, identity, alimento, payload)
            cur.execute(UPDATE_DEFAULT_SQL, (quantidade, unidade, porcao_id, dono, int(food_id)))
            cur.execute(FAVORITE_BY_FOOD_SQL, (dono, int(food_id)))
            favorito = cur.fetchone()
            porcao = None
            if porcao_id is not None:
                cur.execute(PORTION_SQL, (porcao_id,))
                porcao = cur.fetchone()
            return _item_public(favorito, alimento, porcao, identity)  # type: ignore[arg-type]


@router.delete("/{food_id}")
def remove_favorite(food_id: int = Path(..., gt=0), identity: Identity = Depends(get_current_identity)):
    dono = favorites_owner(identity)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_favorites_table(cur)
            cur.execute(DELETE_FAVORITE_SQL, (dono, int(food_id)))
            if int(cur.rowcount or 0) != 1:
                raise _not_found()
    return {"status": "deleted", "food_id": int(food_id)}


__all__ = ["router"]

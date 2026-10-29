"""Catálogo de alimentos (plano de nutrição §8.1, §10 e §13, Lote L3a). Vive na
api-core (`{proxy+}`).

Rotas (prefixo `/nutrition/foods`):
- `GET  /search?q=&source=&scope=&limit=`  busca: termos do motor no FULLTEXT de
                                           `name_normalized` (prefixo quando o termo é
                                           curto demais para o índice) -> até
                                           `SEARCH_CANDIDATE_POOL` candidatos -> ordem pela
                                           nota do motor; públicos + os do usuário, com a
                                           citação de cada item. `scope=private_all` (admin):
                                           os privados de todos, para promover
- `GET  /{id}`                             alimento + porções visíveis + permissões
- `POST /`                                 `FoodItemCreateIn` -> alimento PRIVADO do usuário
                                           (D-G), 201; regra do motor violada -> 400 com os
                                           códigos; Atwater/subtotal/soma dos macros ->
                                           `warnings`. `source` (L6): ausente = manual,
                                           `label_photo` = formulário da foto do rótulo
                                           (`catalog.USER_ENTRY_SOURCES`; outra -> 400)
- `PUT  /{id}`                             o dono (privado) ou o admin (público); fonte
                                           somente leitura (TACO) -> 409
- `POST /{id}/portions`                    `FoodPortionIn` -> 201 (pública só do admin em
                                           alimento público)
- `POST /{id}/promote`                     admin: privado -> público, com as porções do dono

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §48): normalização,
termos da busca, nota, ordem e limites são de `domain/nutrition/food_text`;
fontes, base, campos da RDC 429, citação, somente leitura e regras de entrada de
`domain/nutrition/catalog`; textos de `labels`. Esta camada só monta o SQL e
devolve o que o motor decidiu. Acesso (D-G): `nutrition_access`. Tabelas
ausentes: 503 citando a 080/081 (`nutrition_schema`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import catalog, food_text
from .domain.nutrition import labels as nutrition_labels
from .nutrition_access import (
    VISIBLE_PORTIONS_SQL,
    can_add_portion,
    can_edit_food,
    can_list_private_foods,
    can_promote_food,
    can_view_food,
    foods_visibility,
    ownership,
    portion_owner_for,
    portions_visibility,
)
from .nutrition_schema import require_food_tables
from .request_models import FoodItemCreateIn, FoodItemIn, FoodPortionIn
from .security import Identity, get_current_identity, require_admin

router = APIRouter(prefix="/nutrition/foods", tags=["nutrition"])

FOODS = "food_items"
PORTIONS = "food_portions"

SCOPE_VISIBLE = "visible"
SCOPE_PRIVATE_ALL = "private_all"
SCOPES: tuple[str, ...] = (SCOPE_VISIBLE, SCOPE_PRIVATE_ALL)

_TEXT_FIELDS: tuple[str, ...] = ("brand", "category", "household_measure")
_NUMERIC_FIELDS: tuple[str, ...] = (*catalog.PER_100_FIELDS, "serving_size", "servings_per_package")

FOOD_COLUMNS: tuple[str, ...] = (
    "id", "source", "source_id", "source_version", "barcode", "name", "brand", "category", "basis",
    *catalog.PER_100_FIELDS, *catalog.SERVING_FIELDS,
    "license", "citation", "field_sources_json", "owner_user_id", "is_active", "created_by", "created_at", "updated_at",
)
_SELECT_FOODS = f"SELECT {', '.join(FOOD_COLUMNS)} FROM {FOODS}"
FOOD_BY_ID_SQL = f"{_SELECT_FOODS} WHERE id = %s"
MATCH_EXPR = "MATCH(name_normalized) AGAINST (%s IN BOOLEAN MODE)"

# Colunas gravadas pelo cadastro do usuário (POST) e pela edição (PUT), na ordem.
_WRITE_COLUMNS: tuple[str, ...] = ("name", "name_normalized", "brand", "category", "basis", *catalog.PER_100_FIELDS,
                                   "serving_size", "household_measure", "servings_per_package")
INSERT_FOOD_SQL = (
    f"INSERT INTO {FOODS} (source, {', '.join(_WRITE_COLUMNS)}, owner_user_id, is_active, created_by) "
    f"VALUES (%s, {', '.join(['%s'] * len(_WRITE_COLUMNS))}, %s, 1, %s)"
)
UPDATE_FOOD_SQL = f"UPDATE {FOODS} SET {', '.join(f'{c} = %s' for c in _WRITE_COLUMNS)} WHERE id = %s"
PROMOTE_FOOD_SQL = f"UPDATE {FOODS} SET owner_user_id = NULL WHERE id = %s AND owner_user_id = %s"

PORTION_COLUMNS = "id, food_id, label, amount, source, owner_user_id, created_at"
# Visibilidade das porções: condição de `nutrition_access.portions_visibility`.
PORTIONS_OF_FOOD_SQL = (
    f"SELECT {PORTION_COLUMNS} FROM {PORTIONS} WHERE food_id = %s AND {VISIBLE_PORTIONS_SQL} ORDER BY amount, id"
)
PORTION_BY_ID_SQL = f"SELECT {PORTION_COLUMNS} FROM {PORTIONS} WHERE id = %s"
INSERT_PORTION_SQL = (
    f"INSERT INTO {PORTIONS} (food_id, label, amount, source, owner_user_id, created_by) VALUES (%s, %s, %s, %s, %s, %s)"
)
PROMOTE_PORTIONS_SQL = f"UPDATE {PORTIONS} SET owner_user_id = NULL WHERE food_id = %s AND owner_user_id = %s"


def search_sql(*, fulltext: bool, visibility: str, with_source: bool) -> str:
    """SELECT da busca. Parâmetros, na ordem: expressão (FULLTEXT ou prefixo
    LIKE, ambas de `food_text`), os da visibilidade (`nutrition_access`), fonte
    (se filtrada), expressão de novo (ordem do FULLTEXT) e o lote de candidatos."""
    filtros = [MATCH_EXPR if fulltext else "name_normalized LIKE %s", "is_active = 1", visibility]
    if with_source:
        filtros.append("source = %s")
    ordem = f"{MATCH_EXPR} DESC, id" if fulltext else "name_normalized, id"
    return f"{_SELECT_FOODS} WHERE {' AND '.join(filtros)} ORDER BY {ordem} LIMIT %s"


def _connect():
    return connect_or_503(get_conn)


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value) if isinstance(value, (Decimal, int, float)) else float(str(value))


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _text_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    texto = " ".join(value.split())
    return texto or None


def food_public(row: dict[str, Any], identity: Identity) -> dict[str, Any]:
    """Alimento para a API: valores por 100 g/ml (códigos da RDC 429), porção do
    rótulo, citação/licença da linha, a origem dos valores que vieram de outra
    fonte (`field_sources`, com a nota pronta do motor; M85) e o que ESTE
    usuário pode fazer com ele."""
    return {
        "id": int(row["id"]),
        "source": row.get("source"),
        "source_id": row.get("source_id"),
        "source_version": row.get("source_version"),
        "barcode": row.get("barcode"),
        "name": row.get("name"),
        "brand": row.get("brand"),
        "category": row.get("category"),
        "basis": row.get("basis"),
        "per_100": {campo: _num(row.get(campo)) for campo in catalog.PER_100_FIELDS},
        "serving": {
            "serving_size": _num(row.get("serving_size")),
            "household_measure": row.get("household_measure"),
            "servings_per_package": _num(row.get("servings_per_package")),
        },
        "license": row.get("license"),
        "citation": row.get("citation"),
        "field_sources": nutrition_labels.field_sources_public(row.get("field_sources_json")),
        **ownership(identity, row.get("owner_user_id")),
        "is_active": bool(row.get("is_active")),
        "permissions": {
            "can_edit": can_edit_food(identity, row),
            "can_add_portion": can_add_portion(identity, row),
            "can_promote": can_promote_food(identity, row),
        },
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def portion_public(row: dict[str, Any], identity: Identity) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "food_id": int(row["food_id"]),
        "label": row.get("label"),
        "amount": _num(row.get("amount")),
        "source": row.get("source"),
        **ownership(identity, row.get("owner_user_id")),
    }


def _fetch_food(cur: Any, food_id: int, identity: Identity) -> dict[str, Any]:
    """Alimento visível para o usuário ou 404 (não revela o privado de outro)."""
    cur.execute(FOOD_BY_ID_SQL, (int(food_id),))
    row = cur.fetchone()
    if not row or not can_view_food(identity, row):
        raise _error(404, "food_not_found", "Alimento não encontrado.")
    return row


def _invalid_food(codes: list[str]) -> HTTPException:
    return _error(
        400,
        "invalid_food",
        "Valores do alimento fora das regras do catálogo.",
        errors=nutrition_labels.limitation_items(codes),
    )


def _validated_write_values(payload: FoodItemIn) -> tuple[tuple[Any, ...], catalog.FoodValidation]:
    """Valida no motor e devolve os valores na ordem de `_WRITE_COLUMNS`."""
    validacao = catalog.validate_food_values(payload.model_dump(), payload.basis)
    nome = " ".join(payload.name.split())
    normalizado = food_text.normalize_name(nome)
    erros = list(validacao.errors)
    if not normalizado:
        erros.insert(0, catalog.ERR_INVALID_NAME)
    if erros:
        raise _invalid_food(erros)
    numeros = validacao.values
    valores = {
        "name": nome,
        "name_normalized": normalizado,
        "brand": _text_or_none(payload.brand),
        "category": _text_or_none(payload.category),
        "basis": validacao.basis.value,  # type: ignore[union-attr]
        "household_measure": _text_or_none(payload.household_measure),
        **{campo: numeros.get(campo) for campo in _NUMERIC_FIELDS},
    }
    return tuple(valores[coluna] for coluna in _WRITE_COLUMNS), validacao


def _with_validation(food: dict[str, Any], validacao: catalog.FoodValidation) -> dict[str, Any]:
    return {
        **food,
        "warnings": nutrition_labels.limitation_items(validacao.warnings),
        "atwater": validacao.atwater.to_dict() if validacao.atwater else None,
    }


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.get("/search")
def search_foods(
    q: str = Query(..., min_length=1, max_length=120),
    source: Optional[str] = Query(default=None, max_length=16),
    scope: str = Query(default=SCOPE_VISIBLE, max_length=16),
    limit: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    fonte = None
    if source is not None:
        fonte = catalog.normalize_source(source)
        if fonte is None:
            raise _error(400, catalog.ERR_INVALID_SOURCE, nutrition_labels.limitation_label(catalog.ERR_INVALID_SOURCE))
    if scope not in SCOPES:
        raise _error(400, "invalid_scope", "Escopo de busca inválido.", allowed=list(SCOPES))
    private_all = scope == SCOPE_PRIVATE_ALL
    if private_all and not can_list_private_foods(identity):
        raise _error(403, "food_forbidden", "Só o administrador lista os alimentos privados de outros usuários.")

    normalizado = food_text.normalize_name(q)
    resposta: dict[str, Any] = {"query": q, "normalized": normalizado, "items": []}
    # Expressão da busca: do motor (FULLTEXT booleano; prefixo LIKE para termo curto).
    expressao = food_text.fulltext_boolean_query(q)
    fulltext = expressao is not None
    if expressao is None:
        expressao = food_text.prefix_pattern(q)
    if expressao is None:
        return resposta
    visibilidade, params_visibilidade = foods_visibility(identity, private_all=private_all)
    params: list[Any] = [expressao, *params_visibilidade]
    if fonte is not None:
        params.append(fonte.value)
    if fulltext:
        params.append(expressao)
    params.append(food_text.SEARCH_CANDIDATE_POOL)
    sql = search_sql(fulltext=fulltext, visibility=visibilidade, with_source=fonte is not None)

    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            cur.execute(sql, tuple(params))
            linhas = cur.fetchall() or []
    por_id = {int(linha["id"]): linha for linha in linhas}
    ordem = food_text.order_for_search(q, [(food_id, linha["name"]) for food_id, linha in por_id.items()])
    resposta["items"] = [
        {**food_public(por_id[int(m.candidate_id)], identity), "score": m.score, "auto_accept": m.auto_accept}
        for m in ordem[: food_text.search_limit(limit)]
    ]
    return resposta


@router.get("/{food_id}")
def get_food(food_id: int, identity: Identity = Depends(get_current_identity)):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            linha = _fetch_food(cur, food_id, identity)
            _condicao, params_visibilidade = portions_visibility(identity, linha)
            cur.execute(PORTIONS_OF_FOOD_SQL, (int(food_id), *params_visibilidade))
            porcoes = cur.fetchall() or []
    return {**food_public(linha, identity), "portions": [portion_public(p, identity) for p in porcoes]}


@router.post("", status_code=201)
def create_food(payload: FoodItemCreateIn, identity: Identity = Depends(get_current_identity)):
    fonte = catalog.user_entry_source(payload.source)
    if fonte is None:
        raise _invalid_food([catalog.ERR_INVALID_SOURCE])
    valores, validacao = _validated_write_values(payload)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            cur.execute(
                INSERT_FOOD_SQL,
                (fonte.value, *valores, int(identity.user_id), int(identity.user_id)),
            )
            novo_id = cur.lastrowid
            if not novo_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
                raise _error(500, "food_insert_failed", "Alimento não encontrado após gravar.")
            linha = _fetch_food(cur, int(novo_id), identity)
    return _with_validation(food_public(linha, identity), validacao)


@router.put("/{food_id}")
def update_food(food_id: int, payload: FoodItemIn, identity: Identity = Depends(get_current_identity)):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            linha = _fetch_food(cur, food_id, identity)
            if catalog.is_read_only(linha.get("source")):
                raise _error(409, catalog.ERR_SOURCE_READ_ONLY, nutrition_labels.limitation_label(catalog.ERR_SOURCE_READ_ONLY))
            if not can_edit_food(identity, linha):
                raise _error(403, "food_forbidden", "Só o dono edita um alimento privado; o público, só o administrador.")
            valores, validacao = _validated_write_values(payload)
            cur.execute(UPDATE_FOOD_SQL, (*valores, int(food_id)))
            linha = _fetch_food(cur, food_id, identity)
    return _with_validation(food_public(linha, identity), validacao)


@router.post("/{food_id}/portions", status_code=201)
def add_portion(food_id: int, payload: FoodPortionIn, identity: Identity = Depends(get_current_identity)):
    erro = catalog.validate_portion_amount(payload.amount)
    rotulo = " ".join(payload.label.split())
    if erro or not rotulo:
        raise _error(
            400,
            "invalid_portion",
            "Porção fora das regras do catálogo.",
            errors=nutrition_labels.limitation_items([erro or catalog.ERR_INVALID_PORTION_AMOUNT]),
        )
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            linha = _fetch_food(cur, food_id, identity)
            if not can_add_portion(identity, linha):
                raise _error(403, "food_forbidden", "Só o dono cria porções num alimento privado.")
            cur.execute(
                INSERT_PORTION_SQL,
                (int(food_id), rotulo, payload.amount, catalog.USER_ENTRY_SOURCE.value,
                 portion_owner_for(identity, linha), int(identity.user_id)),
            )
            novo_id = cur.lastrowid
            cur.execute(PORTION_BY_ID_SQL, (int(novo_id),))
            porcao = cur.fetchone()
    return portion_public(porcao, identity)


@router.post("/{food_id}/promote")
def promote_food(food_id: int, identity: Identity = Depends(require_admin)):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            linha = _fetch_food(cur, food_id, identity)
            if not can_promote_food(identity, linha):
                raise _error(409, "food_already_public", "O alimento já é público.")
            dono = int(linha["owner_user_id"])
            cur.execute(PROMOTE_FOOD_SQL, (int(food_id), dono))
            if int(cur.rowcount or 0) != 1:
                raise _error(409, "food_already_public", "O alimento já é público.")
            cur.execute(PROMOTE_PORTIONS_SQL, (int(food_id), dono))
            linha = _fetch_food(cur, food_id, identity)
    return food_public(linha, identity)


__all__ = ["food_public", "portion_public", "router", "search_sql"]

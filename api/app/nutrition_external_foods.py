"""Fontes externas do catálogo de alimentos (plano de nutrição §8.1, §10 e §13,
Lote L3b): código de barras pelo Open Food Facts e busca/importação do USDA
FoodData Central. Vive na api-core (`{proxy+}`): só ela recebe a chave do USDA
(`USDA_API_KEY_SECRET_ARN` -> `secrets_bootstrap` -> `USDA_API_KEY`).

Rotas (prefixo `/nutrition/foods`):
- `GET  /barcode/{ean}`         GTIN validado pelo motor -> cache (`food_items`,
                                `source='off'`, `source_id` = GTIN canônico) ->
                                senão, limite diário do motor
                                (`OFF_LOOKUPS_PER_USER_DAY`, 429) -> OFF ->
                                grava o produto PÚBLICO (`source='off'`,
                                `barcode`). 404 se o OFF não conhece o código;
                                422 se o produto não passa nas regras do catálogo
- `GET  /usda/search?q=`        proxy da busca do FDC (só Foundation e SR Legacy):
                                nome em inglês, fdcId, tipo de dado e os campos
                                por 100 g mapeados pelo motor
- `POST /usda/{fdc_id}/import`  detalhe do FDC -> motor -> grava PÚBLICO
                                (`source='usda'`, `source_id` = fdcId, citação);
                                idempotente pelo `UNIQUE (source, source_id)`:
                                201 ao criar, 200 se já existia

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §49): GTIN, limite
diário, tipos de dado do USDA, ids/chaves de nutriente, conversões, energia,
citação e licença são de `domain/nutrition/external_sources` e `catalog`; textos
de `labels`; "hoje" do usuário é de `time_windows`. Esta camada só monta o SQL,
chama o cliente HTTP (`external_food_sources`, timeout curto, erros -> 502/503/
504/404) e devolve o que o motor decidiu. Sem chave do USDA: 503
`usda_not_configured`. Tabelas ausentes: 503 citando a 080/081.

A conexão com o banco NUNCA fica aberta durante a chamada externa (até
`EXTERNAL_HTTP_TIMEOUT_SECONDS`): lê cache e contagem, fecha, chama a fonte,
abre de novo para gravar. Duas consultas simultâneas do mesmo código gravam uma
linha só (`ON DUPLICATE KEY UPDATE` sem efeito + releitura pela chave única).
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from . import external_food_sources as ext
from .domain.nutrition import catalog
from .domain.nutrition import external_sources as fontes
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.catalog import FoodSource
from .intelligence.dashboard.time_windows import user_day_start_db_for
from .nutrition_access import can_view_food
from .nutrition_foods import FOOD_COLUMNS, FOODS, _connect, _error, food_public
from .nutrition_schema import require_food_tables
from .security import Identity, get_current_identity

router = APIRouter(prefix="/nutrition/foods", tags=["nutrition"])

T = TypeVar("T")

FOOD_BY_SOURCE_SQL = f"SELECT {', '.join(FOOD_COLUMNS)} FROM {FOODS} WHERE source = %s AND source_id = %s"
# Consultas novas ao OFF do usuário hoje = linhas `off` que ele criou desde o
# início do SEU dia (no relógio do banco, `time_windows.user_day_start_db`).
OFF_LOOKUPS_TODAY_SQL = f"SELECT COUNT(*) AS n FROM {FOODS} WHERE source = %s AND created_by = %s AND created_at >= %s"

# Colunas gravadas pela importação, na ordem. Público (`owner_user_id` NULL):
# dado da fonte, igual para todos.
_IMPORT_COLUMNS: tuple[str, ...] = (
    "source", "source_id", "source_version", "barcode", "name", "name_normalized", "brand", "category", "basis",
    *catalog.PER_100_FIELDS, *catalog.SERVING_FIELDS, "license", "citation", "created_by",
)
INSERT_EXTERNAL_FOOD_SQL = (
    f"INSERT INTO {FOODS} ({', '.join(_IMPORT_COLUMNS)}, owner_user_id, is_active) "
    f"VALUES ({', '.join(['%s'] * len(_IMPORT_COLUMNS))}, NULL, 1) "
    "ON DUPLICATE KEY UPDATE id = id"
)

# Tamanho das colunas de texto (espelho do DDL da 080): o texto da fonte é cortado.
_TEXT_COLUMN_MAX: dict[str, int] = {
    "name": 255, "name_normalized": 255, "brand": 120, "category": 120, "household_measure": 120,
    "source_version": 32, "license": 255, "citation": 500,
}


def _clip(column: str, value: Optional[str]) -> Optional[str]:
    limite = _TEXT_COLUMN_MAX.get(column)
    if value is None or limite is None:
        return value
    return value[:limite]


def _external(call: Callable[[], T]) -> T:
    """Chama a fonte; a falha vira a resposta HTTP que o cliente já decidiu."""
    try:
        return call()
    except ext.ExternalSourceError as exc:
        raise _error(exc.status_code, exc.code, exc.message, source=exc.source) from None


def _food_by_source(cur: Any, source: FoodSource, source_id: str) -> Optional[dict[str, Any]]:
    cur.execute(FOOD_BY_SOURCE_SQL, (source.value, source_id))
    return cur.fetchone()


def _off_day_start(conn: Any, user_id: int) -> Any:
    """Início do dia do usuário (no fuso dele) no relógio do banco."""
    return user_day_start_db_for(conn, user_id)


def _off_lookups_today(conn: Any, cur: Any, user_id: int) -> int:
    """Consultas novas do usuário hoje, pelo critério do motor (`OFF_LOOKUP_COUNTED_SOURCE`)."""
    cur.execute(OFF_LOOKUPS_TODAY_SQL,
                (fontes.OFF_LOOKUP_COUNTED_SOURCE.value, int(user_id), _off_day_start(conn, user_id)))
    linha = cur.fetchone() or {}
    return int(linha.get("n") or 0)


def _row_validation(row: dict[str, Any]) -> catalog.FoodValidation:
    """Avisos do catálogo (Atwater, subtotal) sobre o que ficou gravado."""
    return catalog.validate_food_values(row, row.get("basis"), required=catalog.EXTERNAL_REQUIRED_PER_100_FIELDS)


def _external_food_response(row: dict[str, Any], identity: Identity, **extra: Any) -> dict[str, Any]:
    if not row.get("is_active") or not can_view_food(identity, row):
        raise _error(404, "food_not_found", "Alimento não encontrado.")
    validacao = _row_validation(row)
    return {
        **food_public(row, identity),
        "warnings": nutrition_labels.limitation_items(validacao.warnings),
        "atwater": validacao.atwater.to_dict() if validacao.atwater else None,
        **extra,
    }


def _invalid_external_food(alimento: fontes.ExternalFood) -> HTTPException:
    return _error(
        422,
        "invalid_external_food",
        "Os dados da fonte externa não passam nas regras do catálogo; cadastre o alimento manualmente.",
        source=alimento.source.value,
        errors=nutrition_labels.limitation_items(alimento.errors),
    )


def _insert_values(alimento: fontes.ExternalFood, user_id: int) -> tuple[Any, ...]:
    valores: dict[str, Any] = {
        "source": alimento.source.value,
        "source_id": alimento.source_id,
        "source_version": alimento.source_version,
        "barcode": alimento.barcode,
        "name": alimento.name,
        "name_normalized": alimento.name_normalized,
        "brand": alimento.brand,
        "category": alimento.category,
        "basis": alimento.basis.value,
        **{campo: alimento.values.get(campo) for campo in catalog.PER_100_FIELDS},
        "serving_size": alimento.values.get("serving_size"),
        "household_measure": alimento.household_measure,
        "servings_per_package": alimento.values.get("servings_per_package"),
        "license": alimento.license,
        "citation": alimento.citation,
        "created_by": int(user_id),
    }
    return tuple(_clip(coluna, valores[coluna]) if isinstance(valores[coluna], str) else valores[coluna]
                 for coluna in _IMPORT_COLUMNS)


def _save(alimento: fontes.ExternalFood, identity: Identity) -> dict[str, Any]:
    """Grava (sem efeito se a chave já existe) e relê pela chave única."""
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            cur.execute(INSERT_EXTERNAL_FOOD_SQL, _insert_values(alimento, int(identity.user_id)))
            linha = _food_by_source(cur, alimento.source, alimento.source_id)
    if linha is None:  # pragma: no cover - defesa: a linha acabou de ser gravada
        raise _error(500, "food_insert_failed", "Alimento não encontrado após gravar.")
    return linha


# ---------------------------------------------------------------------------
# Open Food Facts: código de barras
# ---------------------------------------------------------------------------


@router.get("/barcode/{ean}")
def lookup_barcode(
    ean: str = Path(..., min_length=1, max_length=32),
    identity: Identity = Depends(get_current_identity),
):
    codigo = fontes.normalize_gtin(ean)
    if codigo is None:
        raise _error(400, fontes.ERR_INVALID_BARCODE, nutrition_labels.limitation_label(fontes.ERR_INVALID_BARCODE))
    usadas = 0
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            linha = _food_by_source(cur, FoodSource.OFF, codigo)
            if linha is None:
                usadas = _off_lookups_today(conn, cur, int(identity.user_id))
    if linha is not None:
        return _external_food_response(linha, identity, from_cache=True)
    if not fontes.off_lookup_allowed(usadas):
        raise _error(
            429,
            fontes.ERR_OFF_DAILY_LIMIT,
            nutrition_labels.limitation_label(fontes.ERR_OFF_DAILY_LIMIT),
            limit=fontes.OFF_LOOKUPS_PER_USER_DAY,
        )
    produto = _external(lambda: ext.off_product(codigo, fields=fontes.OFF_PRODUCT_FIELDS))
    alimento = fontes.map_off_product(produto, codigo)
    if not alimento.ok:
        raise _invalid_external_food(alimento)
    return _external_food_response(_save(alimento, identity), identity, from_cache=False)


# ---------------------------------------------------------------------------
# USDA FoodData Central: busca e importação
# ---------------------------------------------------------------------------


@router.get("/usda/search")
def search_usda(
    q: str = Query(..., min_length=1, max_length=120),
    identity: Identity = Depends(get_current_identity),
):
    _external(ext.ensure_usda_configured)
    termo = " ".join(q.split())
    if not termo:
        return {"query": termo, "total_hits": 0, "items": []}
    payload = _external(lambda: ext.usda_search(fontes.usda_search_request(termo)))
    return {"query": termo, **fontes.usda_search_result(payload)}


@router.post("/usda/{fdc_id}/import", status_code=201)
def import_usda_food(
    response: Response,
    fdc_id: int = Path(..., gt=0, le=2_147_483_647),
    identity: Identity = Depends(get_current_identity),
):
    _external(ext.ensure_usda_configured)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            existente = _food_by_source(cur, FoodSource.USDA, str(fdc_id))
    if existente is not None:
        response.status_code = 200
        return _external_food_response(existente, identity, created=False)
    detalhe = _external(lambda: ext.usda_food(fdc_id))
    alimento = fontes.map_usda_food(detalhe, fdc_id)
    if not alimento.ok:
        raise _invalid_external_food(alimento)
    return _external_food_response(_save(alimento, identity), identity, created=True)


__all__ = ["router"]

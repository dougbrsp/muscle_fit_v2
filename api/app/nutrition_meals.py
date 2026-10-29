"""Refeições do diário de nutrição (plano de nutrição §8.1, §10 e §13, Lote L4).
Vive na api-core (`{proxy+}`).

Rotas (prefixo `/nutrition/meals`):
- `POST   /`          `MealIn` -> refeição gravada (201) com os nutrientes de cada
                      item CONGELADOS (E4: valores do motor + `catalog_snapshot_json`)
- `PUT    /{id}`      `MealBodyIn` -> substitui a refeição; item com `id` e o mesmo
                      alimento reusa o snapshot congelado (editar o catálogo depois
                      não muda refeição antiga)
- `DELETE /{id}`      apaga a refeição (os itens vão junto, FK CASCADE)
- `POST   /preview`   `MealPreviewIn` -> itens e totais calculados pelo motor, SEM
                      gravar (o FE não calcula, E9)

400 `invalid_meal` com os códigos do motor (slot, itens, unidade sem conversão,
quantidade fora da faixa) e o índice do item; 404 `food_not_found`/
`portion_not_found` para alimento ou porção que o diário do aluno não usa.

Leitura por IA (Lote L5b, mapa §55): o item fora do catálogo chega sem
`food_id` e com a estimativa da IA por 100 g (`estimate`), que o motor valida
(`meal_parse.validate_estimate`) e transforma em nutrientes de natureza
ESTIMATED (`meal_parse.estimated_item_nutrition`), congelados no snapshot como
qualquer item (fonte `ai_estimate`); na edição, o item estimado mantido (`id`,
sem `food_id` nem `estimate`) reusa o snapshot. A refeição de uma leitura grava
`source = ai_text` (`meal_parse.meal_source`), `ai_job_id` e `description_raw`.

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §52): quantidade
na base, porção, macro ausente (inferido × desconhecido), nutrientes do item,
totais, natureza e entrada da refeição são de `domain/nutrition/meal_math` e
`domain/nutrition/diary`; textos de `labels`. Esta camada lê o catálogo, monta o
snapshot e grava o que o motor devolveu. Acesso: `nutrition_access` (o próprio
aluno; admin/PT para qualquer aluno, D-L). Tabelas ausentes: 503 citando a
082/083 (`nutrition_schema`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException

from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import ENGINE_VERSION, catalog, diary, meal_math, meal_parse
from .domain.nutrition import labels as nutrition_labels
from .nutrition_access import can_log_food, can_log_portion, fetch_target_user, resolve_target_user
from .nutrition_schema import require_diary_tables, require_food_tables
from .request_models import MealBodyIn, MealIn, MealItemIn, MealPreviewIn
from .security import Identity, get_current_identity

router = APIRouter(prefix="/nutrition/meals", tags=["nutrition"])

MEALS = "meals"
ITEMS = "meal_items"
FOODS = "food_items"
PORTIONS = "food_portions"
NUTRIENTS = meal_math.NUTRIENT_FIELDS

MEAL_COLUMNS: tuple[str, ...] = (
    "id", "user_id", "local_date", "meal_slot", "eaten_at", "source", "description_raw", "ai_job_id", "notes", *NUTRIENTS,
    "totals_incomplete_json", "totals_nature", "item_count", "engine_version", "created_by", "updated_by",
    "created_at", "updated_at",
)
ITEM_COLUMNS: tuple[str, ...] = (
    "id", "meal_id", "position", "food_id", "portion_id", "item_label", "quantity", "unit", "amount", "basis",
    *NUTRIENTS, "nature", "match_confidence", "limitations_json", "catalog_snapshot_json", "engine_version",
)
_SELECT_MEALS = f"SELECT {', '.join(MEAL_COLUMNS)} FROM {MEALS}"
MEAL_BY_ID_SQL = f"{_SELECT_MEALS} WHERE id = %s"
MEALS_OF_RANGE_SQL = f"{_SELECT_MEALS} WHERE user_id = %s AND local_date BETWEEN %s AND %s ORDER BY local_date, id"

_TOTAL_COLUMNS: tuple[str, ...] = (*NUTRIENTS, "totals_incomplete_json", "totals_nature", "item_count", "engine_version")
# Colunas do cabeçalho gravadas na criação, na ordem dos parâmetros (antes dos totais).
_MEAL_HEADER_COLUMNS: tuple[str, ...] = (
    "user_id", "local_date", "meal_slot", "eaten_at", "source", "description_raw", "ai_job_id", "notes",
)
INSERT_MEAL_SQL = (
    f"INSERT INTO {MEALS} ({', '.join(_MEAL_HEADER_COLUMNS)}, {', '.join(_TOTAL_COLUMNS)}, created_by, updated_by) "
    f"VALUES ({', '.join(['%s'] * (len(_MEAL_HEADER_COLUMNS) + len(_TOTAL_COLUMNS) + 2))})"
)
UPDATE_MEAL_SQL = (
    f"UPDATE {MEALS} SET local_date = %s, meal_slot = %s, eaten_at = %s, notes = %s, "
    f"{', '.join(f'{c} = %s' for c in _TOTAL_COLUMNS)}, updated_by = %s WHERE id = %s"
)
DELETE_MEAL_SQL = f"DELETE FROM {MEALS} WHERE id = %s"
DELETE_ITEMS_SQL = f"DELETE FROM {ITEMS} WHERE meal_id = %s"

_ITEM_WRITE_COLUMNS: tuple[str, ...] = (
    "meal_id", "position", "food_id", "portion_id", "item_label", "quantity", "unit", "amount", "basis", *NUTRIENTS,
    "nature", "match_confidence", "limitations_json", "catalog_snapshot_json", "engine_version",
)
INSERT_ITEM_SQL = (
    f"INSERT INTO {ITEMS} ({', '.join(_ITEM_WRITE_COLUMNS)}) VALUES ({', '.join(['%s'] * len(_ITEM_WRITE_COLUMNS))})"
)

FOOD_COLUMNS: tuple[str, ...] = (
    "id", "source", "source_id", "source_version", "name", "brand", "basis", *catalog.PER_100_FIELDS,
    "serving_size", "household_measure", "citation", "field_sources_json", "owner_user_id", "is_active",
)
PORTION_COLUMNS: tuple[str, ...] = ("id", "food_id", "label", "amount", "owner_user_id")


def _in_list(count: int) -> str:
    return ", ".join(["%s"] * count)


def items_of_meals_sql(count: int) -> str:
    return f"SELECT {', '.join(ITEM_COLUMNS)} FROM {ITEMS} WHERE meal_id IN ({_in_list(count)}) ORDER BY meal_id, position, id"


def foods_by_ids_sql(count: int) -> str:
    return f"SELECT {', '.join(FOOD_COLUMNS)} FROM {FOODS} WHERE id IN ({_in_list(count)})"


def portions_by_ids_sql(count: int) -> str:
    return f"SELECT {', '.join(PORTION_COLUMNS)} FROM {PORTIONS} WHERE id IN ({_in_list(count)})"


# ---------------------------------------------------------------------------
# Conversões de leitura (sem regra)
# ---------------------------------------------------------------------------


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


def _text_or_none(value: Any) -> Optional[str]:
    texto = str(value).strip() if value is not None else ""
    return texto or None


def _json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _hhmm(value: Any) -> Optional[str]:
    """TIME lido do banco (`timedelta` no PyMySQL e no shim) -> `HH:MM`."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        minutos = int(value.total_seconds()) // 60
        return f"{minutos // 60:02d}:{minutos % 60:02d}"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)[:5]


def _as_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def totals_public(totals: Optional[meal_math.NutritionTotals]) -> Optional[dict[str, Any]]:
    """Total do motor para a API (limitações já rotuladas). `None` = sem refeição."""
    if totals is None:
        return None
    corpo = totals.to_dict()
    corpo["limitations"] = nutrition_labels.limitation_items(totals.limitations)
    return corpo


def stored_meal_totals(row: dict[str, Any]) -> meal_math.NutritionTotals:
    return meal_math.totals_from_stored(
        row, _json_value(row.get("totals_incomplete_json")) or (), row.get("totals_nature"), row.get("item_count")
    )


def _item_row_public(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = _json_value(row.get("catalog_snapshot_json")) or {}
    alimento = snapshot.get("food") or {}
    porcao = snapshot.get("portion") or None
    return {
        "id": int(row["id"]),
        "position": int(row["position"]),
        "food_id": int(row["food_id"]) if row.get("food_id") is not None else None,
        "portion_id": int(row["portion_id"]) if row.get("portion_id") is not None else None,
        "portion_label": porcao.get("label") if porcao else None,
        "label": row.get("item_label"),
        "quantity": _num(row.get("quantity")),
        "unit": row.get("unit"),
        "amount": _num(row.get("amount")),
        "basis": row.get("basis"),
        "nutrients": {campo: _num(row.get(campo)) for campo in NUTRIENTS},
        "nature": row.get("nature"),
        "match_confidence": _num(row.get("match_confidence")),
        "limitations": nutrition_labels.limitation_items(_json_value(row.get("limitations_json")) or []),
        "food_source": alimento.get("source"),
        "citation": alimento.get("citation"),
        "field_sources": nutrition_labels.field_sources_public(alimento.get("field_sources")),
    }


def meal_row_public(row: dict[str, Any], items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "local_date": _iso(_as_date(row.get("local_date"))),
        "meal_slot": row.get("meal_slot"),
        "eaten_at": _hhmm(row.get("eaten_at")),
        "source": row.get("source"),
        "description_raw": row.get("description_raw"),
        "ai_job_id": row.get("ai_job_id"),
        "notes": row.get("notes"),
        "totals": totals_public(stored_meal_totals(row)),
        "items": [_item_row_public(item) for item in sorted(items, key=lambda r: (int(r["position"]), int(r["id"])))],
        "engine_version": row.get("engine_version"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def sort_meals(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordem do dia pelo motor (`diary.meal_sort_key`: slot, hora, id)."""
    return sorted(rows, key=lambda r: diary.meal_sort_key(r.get("meal_slot"), _hhmm(r.get("eaten_at")), r.get("id")))


def read_meal_rows(cur: Any, user_id: int, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Linhas cruas das refeições do aluno no intervalo, na ordem do dia."""
    cur.execute(MEALS_OF_RANGE_SQL, (int(user_id), date_from, date_to))
    return sort_meals(list(cur.fetchall() or []))


def meals_public(cur: Any, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """As refeições (linhas cruas) com os itens, na forma pública."""
    itens_por_refeicao: dict[int, list[dict[str, Any]]] = {}
    if rows:
        ids = [int(r["id"]) for r in rows]
        cur.execute(items_of_meals_sql(len(ids)), tuple(ids))
        for item in cur.fetchall() or []:
            itens_por_refeicao.setdefault(int(item["meal_id"]), []).append(item)
    return [meal_row_public(r, itens_por_refeicao.get(int(r["id"]), [])) for r in rows]


def day_totals(rows: Sequence[dict[str, Any]]) -> Optional[meal_math.NutritionTotals]:
    """Total do dia pelo motor a partir das refeições gravadas (`None` = sem refeição)."""
    return meal_math.combine_totals([stored_meal_totals(row) for row in rows])


# ---------------------------------------------------------------------------
# Itens: catálogo ou snapshot -> motor
# ---------------------------------------------------------------------------


def food_snapshot(food: dict[str, Any]) -> dict[str, Any]:
    """O alimento como estava na gravação (E4): valores por 100 com os códigos
    da RDC 429, base, porção do rótulo, citação e a origem dos valores que
    vieram de outra fonte (`field_sources`, M85; snapshot anterior não tem)."""
    return {
        "id": int(food["id"]),
        "source": food.get("source"),
        "source_id": food.get("source_id"),
        "source_version": food.get("source_version"),
        "name": food.get("name"),
        "brand": food.get("brand"),
        "basis": food.get("basis"),
        "per_100": {campo: _num(food.get(campo)) for campo in catalog.PER_100_FIELDS},
        "serving_size": _num(food.get("serving_size")),
        "household_measure": food.get("household_measure"),
        "citation": food.get("citation"),
        "field_sources": catalog.field_sources_snapshot(food.get("field_sources_json")),
    }


def portion_snapshot(portion: dict[str, Any]) -> dict[str, Any]:
    return {"id": int(portion["id"]), "label": portion.get("label"), "amount": _num(portion.get("amount"))}


@dataclass(frozen=True)
class ResolvedItem:
    """Um item pronto para gravar ou mostrar: entrada, snapshot e o motor.
    `food_id` None = item estimado pela IA (L5b)."""

    position: int
    food_id: Optional[int]
    portion_id: Optional[int]
    label: str
    quantity: float
    unit: str
    snapshot: dict[str, Any]
    nutrition: meal_math.ItemNutrition
    match_confidence: Optional[float] = None

    def public(self) -> dict[str, Any]:
        porcao = self.snapshot.get("portion")
        return {
            "id": None,
            "position": self.position,
            "food_id": self.food_id,
            "portion_id": self.portion_id,
            "portion_label": porcao.get("label") if porcao else None,
            "label": self.label,
            "quantity": self.quantity,
            "unit": self.unit,
            "amount": self.nutrition.amount,
            "basis": self.nutrition.basis.value,
            "nutrients": self.nutrition.nutrients.to_dict(),
            "nature": self.nutrition.nature.value,
            "match_confidence": self.match_confidence,
            "limitations": nutrition_labels.limitation_items(self.nutrition.limitations),
            "food_source": self.snapshot["food"].get("source"),
            "citation": self.snapshot["food"].get("citation"),
            "field_sources": nutrition_labels.field_sources_public(self.snapshot["food"].get("field_sources")),
        }


def _invalid_meal(errors: Sequence[str], items: Sequence[dict[str, Any]] = ()) -> HTTPException:
    return _error(
        400,
        "invalid_meal",
        "A refeição não segue as regras do diário.",
        errors=nutrition_labels.limitation_items(errors),
        items=list(items),
    )


def _fetch_by_ids(cur: Any, sql_for: Any, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    unicos = sorted(set(int(i) for i in ids))
    if not unicos:
        return {}
    cur.execute(sql_for(len(unicos)), tuple(unicos))
    return {int(row["id"]): row for row in cur.fetchall() or []}


def _reusable_snapshot(item: MealItemIn, stored: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Snapshot congelado (E4) que o item mantido na edição reusa: o mesmo
    alimento do catálogo, ou o item estimado pela IA mantido sem estimativa nova."""
    snapshot = _json_value(stored.get("catalog_snapshot_json")) or {}
    if not snapshot.get("food") or item.estimate is not None:
        return None
    gravado = stored.get("food_id")
    if item.food_id is not None:
        return snapshot if gravado is not None and int(gravado) == int(item.food_id) else None
    if gravado is None and meal_parse.snapshot_is_estimate(snapshot["food"]):
        return snapshot
    return None


def resolve_items(
    cur: Any,
    identity: Identity,
    diary_user_id: int,
    slot: str,
    items: Sequence[MealItemIn],
    existing: Optional[dict[int, dict[str, Any]]] = None,
) -> list[ResolvedItem]:
    """Valida a refeição no motor e resolve cada item: snapshot congelado (item
    mantido, mesmo alimento ou estimado mantido), o catálogo atual ou a
    estimativa da IA (L5b); porção cadastrada ou do rótulo; quantidade na base e
    nutrientes pelo motor. 400/404 antes de qualquer escrita."""
    erros_cabecalho = diary.validate_meal_header(slot, len(items))
    if erros_cabecalho:
        raise _invalid_meal(erros_cabecalho)
    existentes = existing or {}

    snapshots: dict[int, dict[str, Any]] = {}
    for indice, item in enumerate(items):
        if item.id is None:
            continue
        gravado = existentes.get(int(item.id))
        if gravado is None:
            raise _error(404, "meal_item_not_found", "Item não encontrado nesta refeição.", index=indice)
        snapshot = _reusable_snapshot(item, gravado)
        if snapshot is not None:
            snapshots[indice] = snapshot

    # Origem de cada item (motor): alimento do catálogo OU estimativa da IA (o
    # estimado mantido na edição conta como estimativa).
    erros_origem: list[dict[str, Any]] = []
    for indice, item in enumerate(items):
        tem_estimativa = item.estimate is not None or (indice in snapshots and item.food_id is None)
        erros = diary.item_source_errors(item.food_id is not None, tem_estimativa)
        if erros:
            erros_origem.append({"index": indice, "errors": nutrition_labels.limitation_items(erros)})
    if erros_origem:
        raise _invalid_meal([erro["code"] for bloco in erros_origem for erro in bloco["errors"]], erros_origem)

    do_catalogo = [
        item.food_id for indice, item in enumerate(items) if indice not in snapshots and item.food_id is not None
    ]
    alimentos = _fetch_by_ids(cur, foods_by_ids_sql, do_catalogo)
    for indice, item in enumerate(items):
        if indice in snapshots or item.food_id is None:
            continue
        alimento = alimentos.get(int(item.food_id))
        if alimento is None or not can_log_food(identity, diary_user_id, alimento):
            raise _error(404, "food_not_found", "Alimento não encontrado.", index=indice, food_id=int(item.food_id))

    porcoes_novas = [
        item.portion_id
        for indice, item in enumerate(items)
        if item.portion_id is not None
        and item.food_id is not None
        and not (indice in snapshots and (snapshots[indice].get("portion") or {}).get("id") == item.portion_id)
    ]
    porcoes = _fetch_by_ids(cur, portions_by_ids_sql, porcoes_novas)

    resolvidos: list[ResolvedItem] = []
    erros_itens: list[dict[str, Any]] = []
    for indice, item in enumerate(items):
        snapshot = snapshots.get(indice)
        if snapshot is None and item.food_id is not None:
            snapshot = {"food": food_snapshot(alimentos[int(item.food_id)]), "portion": None}
        elif snapshot is None:
            estimativa = meal_parse.validate_estimate(item.estimate.model_dump() if item.estimate else None)
            if estimativa.nutrients is None:
                erros_itens.append({"index": indice, "errors": nutrition_labels.limitation_items(estimativa.errors)})
                continue
            snapshot = meal_parse.estimate_snapshot(item.estimate.name if item.estimate else None, estimativa.nutrients)
        estimado = meal_parse.snapshot_is_estimate(snapshot["food"])
        snapshot = {"food": snapshot["food"], "portion": None if item.portion_id is None else snapshot.get("portion")}
        if item.portion_id is not None and (snapshot["portion"] or {}).get("id") != item.portion_id:
            porcao = porcoes.get(int(item.portion_id)) if item.food_id is not None else None
            if (
                porcao is None
                or int(porcao["food_id"]) != int(item.food_id)  # type: ignore[arg-type]
                or not can_log_portion(identity, diary_user_id, porcao)
            ):
                raise _error(404, "portion_not_found", "Porção não encontrada para este alimento.", index=indice)
            snapshot["portion"] = portion_snapshot(porcao)
        alimento = snapshot["food"]
        quantidade_porcao = meal_math.portion_amount_for(
            item.unit, (snapshot["portion"] or {}).get("amount"), alimento.get("serving_size")
        )
        resolucao = meal_math.resolve_amount(item.quantity, item.unit, alimento.get("basis"), quantidade_porcao)
        erros = diary.item_amount_errors(resolucao)
        por_100 = meal_math.nutrients_from_values(alimento.get("per_100") or {})
        if estimado:
            nutricao = meal_parse.estimated_item_nutrition(por_100, resolucao)
        else:
            nutricao = meal_math.item_nutrition(por_100, resolucao)
        if erros or nutricao is None:
            erros_itens.append({"index": indice, "errors": nutrition_labels.limitation_items(erros)})
            continue
        resolvidos.append(
            ResolvedItem(
                position=indice,
                food_id=None if estimado else int(item.food_id),  # type: ignore[arg-type]
                portion_id=None if item.portion_id is None else int(item.portion_id),
                label=str(alimento.get("name") or ""),
                quantity=float(item.quantity),
                unit=resolucao.unit.value if resolucao.unit else str(item.unit),
                snapshot=snapshot,
                nutrition=nutricao,
                match_confidence=meal_parse.normalize_confidence(item.match_confidence),
            )
        )
    if erros_itens:
        codigos = [erro["code"] for bloco in erros_itens for erro in bloco["errors"]]
        raise _invalid_meal(codigos, erros_itens)
    return resolvidos


def _totals_values(totals: meal_math.NutritionTotals) -> tuple[Any, ...]:
    """Valores das colunas de `_TOTAL_COLUMNS`, na ordem."""
    return (
        *(totals.nutrients.get(campo) for campo in NUTRIENTS),
        list(totals.incomplete_fields),
        totals.nature.value if totals.nature else None,
        totals.item_count,
        ENGINE_VERSION,
    )


def _item_params(meal_id: int, item: ResolvedItem) -> tuple[Any, ...]:
    return (
        int(meal_id),
        item.position,
        item.food_id,
        item.portion_id,
        item.label,
        item.quantity,
        item.unit,
        item.nutrition.amount,
        item.nutrition.basis.value,
        *(item.nutrition.nutrients.get(campo) for campo in NUTRIENTS),
        item.nutrition.nature.value,
        item.match_confidence,
        list(item.nutrition.limitations),
        item.snapshot,
        ENGINE_VERSION,
    )


def _fetch_meal(cur: Any, meal_id: int) -> dict[str, Any]:
    cur.execute(MEAL_BY_ID_SQL, (int(meal_id),))
    row = cur.fetchone()
    if not row:
        raise _error(404, "meal_not_found", "Refeição não encontrada.")
    return row


def _meal_items(cur: Any, meal_id: int) -> list[dict[str, Any]]:
    cur.execute(items_of_meals_sql(1), (int(meal_id),))
    return list(cur.fetchall() or [])


def _require_tables(cur: Any) -> None:
    require_diary_tables(cur)
    require_food_tables(cur)


def _write_items(cur: Any, meal_id: int, items: Sequence[ResolvedItem]) -> None:
    # Data API: BatchExecuteStatement na MESMA transação (db_dataapi.Cursor.executemany).
    cur.executemany(INSERT_ITEM_SQL, [_item_params(meal_id, item) for item in items])


def _read_back(cur: Any, meal_id: int) -> dict[str, Any]:
    return meal_row_public(_fetch_meal(cur, meal_id), _meal_items(cur, meal_id))


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.post("/preview")
def preview_meal(payload: MealPreviewIn, identity: Identity = Depends(get_current_identity)):
    """Itens e totais pelo motor, sem gravar nada."""
    aluno = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            existentes: dict[int, dict[str, Any]] = {}
            if payload.meal_id is not None:
                refeicao = _fetch_meal(cur, payload.meal_id)
                if int(refeicao["user_id"]) != aluno:
                    raise _error(404, "meal_not_found", "Refeição não encontrada.")
                existentes = {int(item["id"]): item for item in _meal_items(cur, payload.meal_id)}
            itens = resolve_items(cur, identity, aluno, payload.meal_slot, payload.items, existentes)
    totais = meal_math.sum_items([item.nutrition for item in itens])
    return {
        "user_id": aluno,
        "local_date": payload.local_date.isoformat(),
        "meal_slot": payload.meal_slot,
        "items": [item.public() for item in itens],
        "totals": totals_public(totais),
        "engine_version": ENGINE_VERSION,
    }


@router.post("", status_code=201)
def create_meal(payload: MealIn, identity: Identity = Depends(get_current_identity)):
    aluno = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            fetch_target_user(cur, aluno)
            itens = resolve_items(cur, identity, aluno, payload.meal_slot, payload.items)
            totais = meal_math.sum_items([item.nutrition for item in itens])
            slot = diary.normalize_meal_slot(payload.meal_slot)
            origem = meal_parse.meal_source(payload.ai_job_id)
            leitura = origem is diary.MealSource.AI_TEXT
            cur.execute(
                INSERT_MEAL_SQL,
                (
                    aluno, payload.local_date, slot.value if slot else payload.meal_slot, payload.eaten_at,
                    origem.value, _text_or_none(payload.description_raw) if leitura else None,
                    _text_or_none(payload.ai_job_id), payload.notes, *_totals_values(totais),
                    int(identity.user_id), int(identity.user_id),
                ),
            )
            novo_id = cur.lastrowid
            if not novo_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
                raise _error(500, "meal_insert_failed", "Refeição não encontrada após gravar.")
            _write_items(cur, int(novo_id), itens)
            return _read_back(cur, int(novo_id))


@router.put("/{meal_id}")
def update_meal(meal_id: int, payload: MealBodyIn, identity: Identity = Depends(get_current_identity)):
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            refeicao = _fetch_meal(cur, meal_id)
            aluno = resolve_target_user(identity, int(refeicao["user_id"]))
            existentes = {int(item["id"]): item for item in _meal_items(cur, meal_id)}
            itens = resolve_items(cur, identity, aluno, payload.meal_slot, payload.items, existentes)
            totais = meal_math.sum_items([item.nutrition for item in itens])
            slot = diary.normalize_meal_slot(payload.meal_slot)
            cur.execute(
                UPDATE_MEAL_SQL,
                (
                    payload.local_date, slot.value if slot else payload.meal_slot, payload.eaten_at, payload.notes,
                    *_totals_values(totais), int(identity.user_id), int(meal_id),
                ),
            )
            cur.execute(DELETE_ITEMS_SQL, (int(meal_id),))
            _write_items(cur, int(meal_id), itens)
            return _read_back(cur, int(meal_id))


@router.delete("/{meal_id}")
def delete_meal(meal_id: int, identity: Identity = Depends(get_current_identity)):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_diary_tables(cur)
            refeicao = _fetch_meal(cur, meal_id)
            resolve_target_user(identity, int(refeicao["user_id"]))
            cur.execute(DELETE_MEAL_SQL, (int(meal_id),))
    return {"status": "deleted", "id": int(meal_id)}


__all__ = [
    "MEAL_COLUMNS",
    "ITEM_COLUMNS",
    "ResolvedItem",
    "day_totals",
    "meal_row_public",
    "meals_public",
    "read_meal_rows",
    "resolve_items",
    "router",
    "stored_meal_totals",
    "totals_public",
]

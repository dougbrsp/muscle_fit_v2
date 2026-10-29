"""CRUD dos macro-grupos de volume (Fase 6, PR 1 - migração 057).

Um macro-grupo (`muscle_volume_classes`) reúne grupos musculares que dividem
as mesmas metas de volume: séries (e, opcionalmente, exercícios) por treino
para cada frequência semanal 1x / 2x / 3x+ (`muscle_volume_class_ranges`) e
uma meta semanal. Nascem Grandes, Pequenos e Outros com os números da
metodologia da Fase 4; o admin cria, renomeia, exclui e associa grupos.

Desde o PR 2 da Fase 6 o motor da metodologia lê este cadastro
(`methodology_volume_service.load_volume_classes`).

Modo de contagem do volume (migração 073): faixas e meta semanal existem POR
MODO (`methodology_volume.VolumeCountingMode`). As rotas de leitura, de faixas
e de edição aceitam `?counting_mode=` (default `primary_only`: sem o parâmetro,
a resposta e a escrita são as de antes). As tabelas da 057
(`muscle_volume_class_ranges` e `muscle_volume_classes.weekly_*`) SÃO o
cadastro de `primary_only`, intocadas; os outros modos usam as filhas
`muscle_volume_class_mode_ranges` e `muscle_volume_class_weekly_targets`
(rollback de código seguro: o código antigo nunca vê o modo ponderado). O
modo ponderado nasceu como CÓPIA das
faixas de `primary_only` (`copied_from_counting_mode` preenchido); a resposta
devolve `uncalibrated: true` nessas linhas, e a marca cai quando o admin grava
um número de séries (ou de meta semanal) diferente do copiado.

Guards reaproveitados de `exercise_mapping_catalog`: leitura para qualquer
usuário autenticado, escrita só para admin (403).

Origem dos números (`*_source`): `methodology` (número do autor, com o
EXG-0xx), `derived` (número nosso, com a regra escrita) ou `admin`. Toda
edição feita aqui grava `admin` e limpa a evidência - exceto quando o valor
de um eixo chega IGUAL ao que já está lá: a UI reenvia as três linhas de
frequência inteiras, e reescrever um número intocado como `admin` apagaria a
procedência dele sem ninguém ter decidido nada.
"""

from __future__ import annotations

from typing import Any, Dict, List, NoReturn, Optional

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query

from . import security
from .exercise_mapping_catalog import (
    _connect,
    _require_admin_identity,
    _require_identity,
    _row_jsonable,
)
from .db import is_duplicate_key_error

router = APIRouter(prefix="/muscle-volume-classes", tags=["muscle-volume-classes"])

FREQUENCY_BUCKETS = ("1x", "2x", "3x_plus")
ADMIN_SOURCE = "admin"

# Teto de sanidade (DECIMAL(5,1) aceita até 9999,9): protege contra erro de
# digitação sem limitar nenhuma prescrição real.
_MAX_SETS = 999.0
_MAX_EXERCISES = 99

_CLASS_COLUMNS = (
    "id, name, description, sort_order, weekly_sets_min, weekly_sets_max, "
    "weekly_source, weekly_evidence, created_at, updated_at"
)
_RANGE_COLUMNS = (
    "class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
    "sets_source, sets_evidence, exercises_source, exercises_evidence"
)
_MODE_RANGE_COLUMNS = _RANGE_COLUMNS + ", copied_from_counting_mode"
_WEEKLY_COLUMNS = (
    "class_id, weekly_sets_min, weekly_sets_max, weekly_source, weekly_evidence, copied_from_counting_mode"
)


def _counting_mode(value: Optional[str]) -> str:
    """Valor de `VolumeCountingMode` (dono: `intelligence.methodology_volume`);
    ausente = o default do motor. Fora do domínio = 422. Import tardio: o
    pacote `intelligence` é pesado e este router é importado no boot."""
    from .intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE, VolumeCountingMode

    if value is None or not str(value).strip():
        return DEFAULT_VOLUME_COUNTING_MODE.value
    try:
        return VolumeCountingMode(str(value).strip()).value
    except ValueError:
        aceitos = ", ".join(mode.value for mode in VolumeCountingMode)
        raise HTTPException(
            status_code=422, detail=f"counting_mode inválido: {value!r}. Valores aceitos: {aceitos}."
        )


def _is_default_mode(mode: str) -> bool:
    from .intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE

    return mode == DEFAULT_VOLUME_COUNTING_MODE.value


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def _number(value: Any, field: str, *, integer: bool = False, maximum: float) -> Optional[float]:
    """`None` / string vazia = sem valor. Número negativo, acima do teto ou
    não numérico = 422."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{field} inválido: esperado número.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{field} inválido: {str(value)!r}. Esperado número.")
    if number != number:  # NaN
        raise HTTPException(status_code=422, detail=f"{field} inválido: esperado número.")
    if integer and number != int(number):
        raise HTTPException(status_code=422, detail=f"{field} inválido: esperado número inteiro.")
    if number < 0:
        raise HTTPException(status_code=422, detail=f"{field} não pode ser negativo.")
    if number > maximum:
        raise HTTPException(status_code=422, detail=f"{field} acima do limite ({maximum:g}).")
    return int(number) if integer else round(number, 1)


def _pair(
    minimum: Optional[float],
    maximum: Optional[float],
    label: str,
    *,
    required: bool,
) -> None:
    if minimum is None and maximum is None:
        if required:
            raise HTTPException(status_code=422, detail=f"{label}: mínimo e máximo são obrigatórios.")
        return
    if minimum is None or maximum is None:
        raise HTTPException(status_code=422, detail=f"{label}: informe mínimo e máximo, ou nenhum dos dois.")
    if minimum > maximum:
        raise HTTPException(status_code=422, detail=f"{label}: mínimo ({minimum:g}) maior que máximo ({maximum:g}).")


def _clean_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > 80:
        raise HTTPException(status_code=422, detail="name aceita no máximo 80 caracteres.")
    return text or None


def _clean_description(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > 500:
        raise HTTPException(status_code=422, detail="description aceita no máximo 500 caracteres.")
    return text or None


def _clean_sort_order(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"sort_order inválido: {str(value)!r}.")


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return float(a) == float(b)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _load(cursor, class_id: Optional[int] = None, counting_mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """Macro-grupos com as faixas e a meta semanal DO MODO pedido (default
    `primary_only`). Cada item traz `counting_mode`; faixa e meta copiadas e
    ainda não calibradas vêm com `uncalibrated: true`."""
    mode = _counting_mode(counting_mode)
    where = " WHERE id = %s" if class_id is not None else ""
    params = (class_id,) if class_id is not None else ()
    cursor.execute(
        f"SELECT {_CLASS_COLUMNS} FROM muscle_volume_classes{where} ORDER BY sort_order, name",
        params,
    )
    classes = [_row_jsonable(row) for row in (cursor.fetchall() or [])]
    if not classes:
        return []

    range_where = " AND class_id = %s" if class_id is not None else ""
    if _is_default_mode(mode):
        # Tabela da 057: o cadastro de primary_only, sem coluna de modo.
        cursor.execute(
            f"SELECT {_RANGE_COLUMNS} FROM muscle_volume_class_ranges"
            + (" WHERE class_id = %s" if class_id is not None else ""),
            params,
        )
    else:
        cursor.execute(
            f"SELECT {_MODE_RANGE_COLUMNS} FROM muscle_volume_class_mode_ranges WHERE counting_mode = %s{range_where}",
            (mode, *params),
        )
    ranges: Dict[int, List[Dict[str, Any]]] = {}
    for row in cursor.fetchall() or []:
        item = _row_jsonable(row)
        item.setdefault("copied_from_counting_mode", None)
        item["uncalibrated"] = item["copied_from_counting_mode"] is not None
        ranges.setdefault(int(item.pop("class_id")), []).append(item)

    weekly: Dict[int, Dict[str, Any]] = {}
    if not _is_default_mode(mode):
        cursor.execute(
            f"SELECT {_WEEKLY_COLUMNS} FROM muscle_volume_class_weekly_targets WHERE counting_mode = %s{range_where}",
            (mode, *params),
        )
        weekly = {int(row["class_id"]): _row_jsonable(row) for row in (cursor.fetchall() or [])}

    group_where = " AND volume_class_id = %s" if class_id is not None else ""
    cursor.execute(
        f"SELECT id, volume_class_id FROM muscle_groups WHERE volume_class_id IS NOT NULL{group_where} "
        "ORDER BY name",
        params,
    )
    groups: Dict[int, List[int]] = {}
    for row in cursor.fetchall() or []:
        groups.setdefault(int(row["volume_class_id"]), []).append(int(row["id"]))

    for item in classes:
        cid = int(item["id"])
        item["counting_mode"] = mode
        if _is_default_mode(mode):
            item["weekly_copied_from_counting_mode"] = None
        else:
            # Outro modo: a meta semanal é a da filha; sem linha = sem meta.
            meta = weekly.get(cid, {})
            for key in ("weekly_sets_min", "weekly_sets_max", "weekly_source", "weekly_evidence"):
                item[key] = meta.get(key)
            item["weekly_copied_from_counting_mode"] = meta.get("copied_from_counting_mode")
        item["weekly_uncalibrated"] = item["weekly_copied_from_counting_mode"] is not None
        item["ranges"] = sorted(
            ranges.get(cid, []), key=lambda r: FREQUENCY_BUCKETS.index(str(r["frequency_bucket"]))
        )
        item["muscle_group_ids"] = groups.get(cid, [])
    return classes


def _load_one(cursor, class_id: int, counting_mode: Optional[str] = None) -> Dict[str, Any]:
    items = _load(cursor, class_id, counting_mode)
    if not items:
        raise HTTPException(status_code=404, detail="Macro-grupo não encontrado.")
    return items[0]


def _raise_duplicate_name(exc: Exception) -> NoReturn:
    """Nome repetido (UNIQUE) vira 409; qualquer outro erro sobe como veio."""
    if is_duplicate_key_error(exc):
        raise HTTPException(status_code=409, detail="Já existe um macro-grupo com esse nome.") from exc
    raise exc


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.get("")
def list_volume_classes(
    counting_mode: Optional[str] = Query(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)
    mode = _counting_mode(counting_mode)
    with _connect() as conn:
        with conn.cursor() as cursor:
            items = _load(cursor, counting_mode=mode)
    return {"items": items, "counting_mode": mode}


@router.post("")
def create_volume_class(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    name = _clean_name(item.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="name é obrigatório.")
    weekly_min = _number(item.get("weekly_sets_min"), "weekly_sets_min", maximum=_MAX_SETS)
    weekly_max = _number(item.get("weekly_sets_max"), "weekly_sets_max", maximum=_MAX_SETS)
    _pair(weekly_min, weekly_max, "Meta semanal", required=False)

    with _connect() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO muscle_volume_classes
                        (name, description, sort_order, weekly_sets_min, weekly_sets_max,
                         weekly_source, weekly_evidence)
                    VALUES (%s, %s, %s, %s, %s, %s, NULL)
                    """,
                    (
                        name,
                        _clean_description(item.get("description")),
                        _clean_sort_order(item.get("sort_order")),
                        weekly_min,
                        weekly_max,
                        ADMIN_SOURCE,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - reclassifica só a duplicidade
                _raise_duplicate_name(exc)
            class_id = int(cursor.lastrowid)
            created = _load_one(cursor, class_id)
    return {"status": "ok", "item": created}


@router.put("/{class_id}")
def update_volume_class(
    class_id: int,
    item: Dict[str, Any] = Body(...),
    counting_mode: Optional[str] = Query(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Edição parcial: campo ausente do payload preserva o valor atual.
    Mexer na meta semanal (qualquer um dos dois limites) grava
    `weekly_source='admin'`, a não ser que os dois cheguem iguais aos atuais.

    `?counting_mode=` (073): a meta semanal editada é a DESSE modo (default
    `primary_only`, a coluna de sempre); nome, descrição e ordem são do
    macro-grupo, iguais em todos os modos."""
    _require_admin_identity(x_api_token, musclefit_session)
    mode = _counting_mode(counting_mode)

    with _connect() as conn:
        with conn.cursor() as cursor:
            current = _load_one(cursor, class_id, mode)

            sets: List[str] = []
            params: List[Any] = []
            if "name" in item:
                name = _clean_name(item.get("name"))
                if not name:
                    raise HTTPException(status_code=422, detail="name não pode ficar vazio.")
                sets.append("name = %s")
                params.append(name)
            if "description" in item:
                sets.append("description = %s")
                params.append(_clean_description(item.get("description")))
            if "sort_order" in item:
                sets.append("sort_order = %s")
                params.append(_clean_sort_order(item.get("sort_order")))
            if "weekly_sets_min" in item or "weekly_sets_max" in item:
                weekly_min = (
                    _number(item.get("weekly_sets_min"), "weekly_sets_min", maximum=_MAX_SETS)
                    if "weekly_sets_min" in item
                    else current["weekly_sets_min"]
                )
                weekly_max = (
                    _number(item.get("weekly_sets_max"), "weekly_sets_max", maximum=_MAX_SETS)
                    if "weekly_sets_max" in item
                    else current["weekly_sets_max"]
                )
                _pair(weekly_min, weekly_max, "Meta semanal", required=False)
                mudou = not (
                    _same(weekly_min, current["weekly_sets_min"])
                    and _same(weekly_max, current["weekly_sets_max"])
                )
                if not _is_default_mode(mode):
                    if mudou:
                        _upsert_weekly(cursor, class_id, mode, weekly_min, weekly_max)
                elif mudou:
                    sets.extend(
                        [
                            "weekly_sets_min = %s",
                            "weekly_sets_max = %s",
                            "weekly_source = %s",
                            "weekly_evidence = NULL",
                        ]
                    )
                    params.extend([weekly_min, weekly_max, ADMIN_SOURCE])

            if sets:
                try:
                    cursor.execute(
                        f"UPDATE muscle_volume_classes SET {', '.join(sets)} WHERE id = %s",
                        (*params, class_id),
                    )
                except Exception as exc:  # noqa: BLE001 - reclassifica só a duplicidade
                    _raise_duplicate_name(exc)
            updated = _load_one(cursor, class_id, mode)
    return {"status": "ok", "item": updated}


def _upsert_weekly(cursor, class_id: int, mode: str, weekly_min: Optional[float], weekly_max: Optional[float]) -> None:
    """Meta semanal de um modo != `primary_only` (073): valor novo grava
    `admin`, limpa a evidência e a marca de cópia (calibrada)."""
    cursor.execute(
        """
        INSERT INTO muscle_volume_class_weekly_targets
            (class_id, counting_mode, weekly_sets_min, weekly_sets_max, weekly_source, weekly_evidence,
             copied_from_counting_mode)
        VALUES (%s, %s, %s, %s, %s, NULL, NULL)
        ON DUPLICATE KEY UPDATE weekly_sets_min = VALUES(weekly_sets_min),
            weekly_sets_max = VALUES(weekly_sets_max), weekly_source = VALUES(weekly_source),
            weekly_evidence = NULL, copied_from_counting_mode = NULL
        """,
        (class_id, mode, weekly_min, weekly_max, ADMIN_SOURCE),
    )


@router.delete("/{class_id}")
def delete_volume_class(
    class_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """409 se houver grupo associado: excluir tiraria esses grupos da
    avaliação em silêncio. A FK (ON DELETE RESTRICT) é a garantia no banco;
    esta checagem existe para a resposta ser 409 com mensagem, e não 500."""
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            current = _load_one(cursor, class_id)
            if current["muscle_group_ids"]:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Macro-grupo com grupos musculares associados não pode ser excluído. "
                        "Mova ou desassocie os grupos primeiro."
                    ),
                )
            # As faixas caem junto (FK ON DELETE CASCADE).
            cursor.execute("DELETE FROM muscle_volume_classes WHERE id = %s", (class_id,))
    return {"status": "ok", "deleted_id": class_id}


def _parse_ranges(payload: Any) -> List[Dict[str, Any]]:
    raw = payload.get("ranges") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="ranges precisa ser uma lista.")
    parsed: List[Dict[str, Any]] = []
    vistos = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=422, detail="Cada faixa precisa ser um objeto.")
        bucket = str(entry.get("frequency_bucket") or "").strip()
        if bucket not in FREQUENCY_BUCKETS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"frequency_bucket inválido: {bucket!r}. "
                    f"Valores aceitos: {', '.join(FREQUENCY_BUCKETS)}."
                ),
            )
        if bucket in vistos:
            raise HTTPException(status_code=422, detail=f"frequency_bucket repetido: {bucket}.")
        vistos.add(bucket)
        sets_min = _number(entry.get("sets_min"), f"{bucket}.sets_min", maximum=_MAX_SETS)
        sets_max = _number(entry.get("sets_max"), f"{bucket}.sets_max", maximum=_MAX_SETS)
        _pair(sets_min, sets_max, f"Séries por treino ({bucket})", required=True)
        ex_min = _number(entry.get("exercises_min"), f"{bucket}.exercises_min", integer=True, maximum=_MAX_EXERCISES)
        ex_max = _number(entry.get("exercises_max"), f"{bucket}.exercises_max", integer=True, maximum=_MAX_EXERCISES)
        _pair(ex_min, ex_max, f"Exercícios por treino ({bucket})", required=False)
        parsed.append(
            {
                "frequency_bucket": bucket,
                "sets_min": sets_min,
                "sets_max": sets_max,
                "exercises_min": ex_min,
                "exercises_max": ex_max,
            }
        )
    return parsed


@router.put("/{class_id}/ranges")
def replace_volume_class_ranges(
    class_id: int,
    payload: Any = Body(...),
    counting_mode: Optional[str] = Query(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Substitui as linhas de frequência do macro-grupo NO MODO `?counting_mode=`
    (073; default `primary_only`) pelo que veio no payload (`{"ranges": [...]}`).
    As linhas dos outros modos não são tocadas. Frequência ausente do payload
    fica SEM linha - o motor avalia `NO_RANGE_FOR_FREQUENCY` nela.

    Origem por eixo: valor igual ao atual preserva `source`/`evidence`; valor
    novo grava `admin` e limpa a evidência. A marca de cópia não calibrada
    (`copied_from_counting_mode`) segue o eixo de SÉRIES, o que muda entre os
    modos: séries iguais às copiadas mantêm a marca; séries novas a limpam."""
    _require_admin_identity(x_api_token, musclefit_session)
    ranges = _parse_ranges(payload)
    mode = _counting_mode(counting_mode)

    with _connect() as conn:
        with conn.cursor() as cursor:
            current = _load_one(cursor, class_id, mode)
            antigos = {r["frequency_bucket"]: r for r in current["ranges"]}

            padrao = _is_default_mode(mode)
            if padrao:
                cursor.execute("DELETE FROM muscle_volume_class_ranges WHERE class_id = %s", (class_id,))
            else:
                cursor.execute(
                    "DELETE FROM muscle_volume_class_mode_ranges WHERE class_id = %s AND counting_mode = %s",
                    (class_id, mode),
                )
            for faixa in ranges:
                antigo = antigos.get(faixa["frequency_bucket"])
                sets_iguais = antigo is not None and _same(faixa["sets_min"], antigo["sets_min"]) and _same(
                    faixa["sets_max"], antigo["sets_max"]
                )
                ex_iguais = antigo is not None and _same(faixa["exercises_min"], antigo["exercises_min"]) and _same(
                    faixa["exercises_max"], antigo["exercises_max"]
                )
                if sets_iguais:
                    sets_source, sets_evidence = antigo["sets_source"], antigo["sets_evidence"]
                    copied_from = antigo.get("copied_from_counting_mode")
                else:
                    sets_source, sets_evidence = ADMIN_SOURCE, None
                    copied_from = None
                if faixa["exercises_min"] is None:
                    ex_source, ex_evidence = None, None
                elif ex_iguais:
                    ex_source, ex_evidence = antigo["exercises_source"], antigo["exercises_evidence"]
                else:
                    ex_source, ex_evidence = ADMIN_SOURCE, None
                valores = (
                    faixa["frequency_bucket"],
                    faixa["sets_min"],
                    faixa["sets_max"],
                    faixa["exercises_min"],
                    faixa["exercises_max"],
                    sets_source,
                    sets_evidence,
                    ex_source,
                    ex_evidence,
                )
                if padrao:
                    cursor.execute(
                        """
                        INSERT INTO muscle_volume_class_ranges
                            (class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max,
                             sets_source, sets_evidence, exercises_source, exercises_evidence)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (class_id, *valores),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO muscle_volume_class_mode_ranges
                            (class_id, counting_mode, frequency_bucket, sets_min, sets_max, exercises_min,
                             exercises_max, sets_source, sets_evidence, exercises_source, exercises_evidence,
                             copied_from_counting_mode)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (class_id, mode, *valores, copied_from),
                    )
            updated = _load_one(cursor, class_id, mode)
    return {"status": "ok", "item": updated}


# Copia EXPLICITA (botao "copiar de Só primários" da aba admin, Lote 3): só
# preenche o que o modo ainda NÃO tem - nunca sobrescreve uma faixa ou meta já
# gravada (calibrada ou não). A mesma forma do seed da 073, por macro-grupo.
_COPY_RANGES_SQL = (
    "INSERT INTO muscle_volume_class_mode_ranges "
    "(class_id, counting_mode, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
    "sets_source, sets_evidence, exercises_source, exercises_evidence, copied_from_counting_mode) "
    "SELECT r.class_id, %s, r.frequency_bucket, r.sets_min, r.sets_max, r.exercises_min, r.exercises_max, "
    "r.sets_source, r.sets_evidence, r.exercises_source, r.exercises_evidence, %s "
    "FROM muscle_volume_class_ranges r WHERE r.class_id = %s AND NOT EXISTS ("
    "SELECT 1 FROM muscle_volume_class_mode_ranges x WHERE x.class_id = r.class_id AND x.counting_mode = %s "
    "AND x.frequency_bucket = r.frequency_bucket)"
)
_COPY_WEEKLY_SQL = (
    "INSERT INTO muscle_volume_class_weekly_targets "
    "(class_id, counting_mode, weekly_sets_min, weekly_sets_max, weekly_source, weekly_evidence, "
    "copied_from_counting_mode) "
    "SELECT c.id, %s, c.weekly_sets_min, c.weekly_sets_max, COALESCE(c.weekly_source, %s), c.weekly_evidence, %s "
    "FROM muscle_volume_classes c WHERE c.id = %s AND NOT EXISTS ("
    "SELECT 1 FROM muscle_volume_class_weekly_targets w WHERE w.class_id = c.id AND w.counting_mode = %s)"
)


@router.post("/{class_id}/copy-from-default")
def copy_volume_class_from_default_mode(
    class_id: int,
    counting_mode: Optional[str] = Query(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Ação EXPLÍCITA do admin (nunca automática): copia para o modo
    `?counting_mode=` as faixas por frequência e a meta semanal do modo default
    (`primary_only`, as tabelas da 057) que o modo ainda não tem - o caso do
    macro-grupo criado depois da 073. Bucket ou meta que o modo já tem não é
    tocado. O que entra fica marcado `copied_from_counting_mode` = default
    ("cópia — não calibrada") até o admin gravar um número diferente.
    Modo default (ou ausente) = 422: não há de onde copiar."""
    _require_admin_identity(x_api_token, musclefit_session)
    from .intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE

    mode = _counting_mode(counting_mode)
    if _is_default_mode(mode):
        raise HTTPException(
            status_code=422,
            detail="counting_mode precisa ser um modo diferente do default: é dele que as faixas são copiadas.",
        )
    default = DEFAULT_VOLUME_COUNTING_MODE.value
    with _connect() as conn:
        with conn.cursor() as cursor:
            _load_one(cursor, class_id, mode)
            cursor.execute(_COPY_RANGES_SQL, (mode, default, class_id, mode))
            cursor.execute(_COPY_WEEKLY_SQL, (mode, ADMIN_SOURCE, default, class_id, mode))
            updated = _load_one(cursor, class_id, mode)
    return {"status": "ok", "item": updated}


def _parse_group_ids(payload: Any) -> List[int]:
    raw = payload.get("muscle_group_ids") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="muscle_group_ids precisa ser uma lista.")
    ids: List[int] = []
    for value in raw:
        if isinstance(value, bool):
            raise HTTPException(status_code=422, detail="muscle_group_ids aceita só ids numéricos.")
        try:
            group_id = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"muscle_group_ids inválido: {str(value)!r}.")
        if group_id <= 0:
            raise HTTPException(status_code=422, detail="muscle_group_ids inválido: id precisa ser positivo.")
        if group_id not in ids:
            ids.append(group_id)
    return ids


@router.put("/{class_id}/groups")
def replace_volume_class_groups(
    class_id: int,
    payload: Any = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Substitui o conjunto de grupos do macro-grupo (`{"muscle_group_ids":
    [...]}`). Um grupo pertence a no máximo um macro-grupo, então incluir aqui
    um grupo que está em outro o MOVE. Grupo que sai da lista fica sem
    macro-grupo.

    Fatia (`methodology_parent_group_id` preenchido) é recusada: ela soma no
    grupo pai e usa o macro-grupo dele - associar a fatia a outro macro-grupo
    faria o mesmo bloco ter duas metas."""
    _require_admin_identity(x_api_token, musclefit_session)
    ids = _parse_group_ids(payload)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _load_one(cursor, class_id)
            if ids:
                placeholders = ", ".join(["%s"] * len(ids))
                cursor.execute(
                    f"SELECT id, name, methodology_parent_group_id FROM muscle_groups WHERE id IN ({placeholders})",
                    tuple(ids),
                )
                encontrados = {int(row["id"]): row for row in (cursor.fetchall() or [])}
                faltando = [gid for gid in ids if gid not in encontrados]
                if faltando:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Grupo muscular inexistente: {', '.join(str(g) for g in faltando)}.",
                    )
                fatias = [
                    str(encontrados[gid]["name"])
                    for gid in ids
                    if encontrados[gid]["methodology_parent_group_id"] is not None
                ]
                if fatias:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"{', '.join(fatias)}: é fatia de outro grupo e usa o macro-grupo dele; "
                            "associe o grupo pai."
                        ),
                    )
                cursor.execute(
                    f"UPDATE muscle_groups SET volume_class_id = NULL "
                    f"WHERE volume_class_id = %s AND id NOT IN ({placeholders})",
                    (class_id, *ids),
                )
                cursor.execute(
                    f"UPDATE muscle_groups SET volume_class_id = %s WHERE id IN ({placeholders})",
                    (class_id, *ids),
                )
            else:
                cursor.execute(
                    "UPDATE muscle_groups SET volume_class_id = NULL WHERE volume_class_id = %s",
                    (class_id,),
                )
            updated = _load_one(cursor, class_id)
    return {"status": "ok", "item": updated}

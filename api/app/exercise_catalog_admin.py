"""CRUD admin-only do dominio exercicios/equipamentos reestruturado
(exercise_catalog, exercise_aliases, exercise_external_mappings,
exercise_muscle_contributions via reuso de muscle_contributions_router,
equipment_types), usado pela nova secao `/admin/dominio-exercicios` do
frontend.

Mesmo padrao de conexao/identidade/JSON de exercise_mapping_catalog.py e
gyms.py (pymysql direto, sem app.db, _require_identity/_require_admin_identity
resolvidos a partir do header X-API-Token). Leitura livre a qualquer usuario
autenticado, escrita restrita a admin.

Este router NAO substitui exercise_mapping_catalog.py (que continua servindo
a tela legada /mapeamentos sobre a mesma exercise_catalog/exercise_aliases,
ja que os ids coincidem) — e aditivo, focado no catalogo canonico novo com
todos os campos (is_unilateral/is_compound, external mappings, ids legados
fundidos).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query

from . import security
from . import exercise_description_service
from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503, DB_UNAVAILABLE_MYSQL, jsonable as _jsonable
from .domain import laboral as laboral_domain
from .domain.set_measures import MEASURE_UNKNOWN, item_validation_message, parse_measure
from .security import resolve_identity_from_credentials as _require_identity
from .exercise_mapping_canonical import reclassify_after_catalog_change

router = APIRouter(prefix="/exercises", tags=["exercise-catalog-admin"])
equipment_types_router = APIRouter(prefix="/equipment-types", tags=["equipment-types"])
external_mappings_router = APIRouter(prefix="/exercise-external-mappings", tags=["exercise-external-mappings"])


def _connect():
    return connect_or_503(get_conn, DB_UNAVAILABLE_MYSQL)


def _require_admin_identity(
    x_api_token: Optional[str],
    musclefit_session: Optional[str] = None,
) -> security.Identity:
    identity = _require_identity(x_api_token, musclefit_session)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return identity


def _row_jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _jsonable(value) for key, value in row.items()}


def _rows_jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_row_jsonable(row) for row in rows]


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _clean_optional(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text or text in {"—", "-", "null", "None"}:
        return None
    return text


DEFAULT_MEASURE_FIELD = "default_measure"


def clean_default_measure(value: Any) -> Optional[str]:
    """`exercise_catalog.default_measure` (089; Lote T2): sugestão de medida
    do exercício para o editor do Modelo. Vazio/None = sem sugestão (NULL);
    valor fora do vocabulário do dono (`set_measures.parse_measure`) = 400,
    nunca gravado."""
    if _clean_optional(value) is None:
        return None
    measure = parse_measure(value)
    if measure is None:
        raise HTTPException(status_code=400, detail=f"default_measure: {item_validation_message(MEASURE_UNKNOWN)}.")
    return measure.value


def _store_default_measure(cursor, exercise_id: int, item: Dict[str, Any], value: Optional[str]) -> None:
    """Grava `default_measure` só quando o campo veio no payload (PUT parcial,
    como o resto; payload sem o campo não toca a coluna)."""
    if DEFAULT_MEASURE_FIELD not in item:
        return
    cursor.execute(
        "UPDATE exercise_catalog SET default_measure = %s WHERE id = %s",
        (value, exercise_id),
    )


def clean_laboral_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Flag `is_laboral` e padrões do laboral (099; M100) que vieram no
    payload, já validados pelo dono (`domain/laboral.py`); fora dos limites =
    400, nunca gravado. Campo ausente não entra (PUT parcial)."""
    cleaned: Dict[str, Any] = {}
    try:
        if laboral_domain.FLAG_FIELD in item:
            cleaned[laboral_domain.FLAG_FIELD] = int(_clean_bool(item.get(laboral_domain.FLAG_FIELD), False))
        if laboral_domain.DEFAULT_SETS_FIELD in item:
            cleaned[laboral_domain.DEFAULT_SETS_FIELD] = laboral_domain.clean_default_sets(
                item.get(laboral_domain.DEFAULT_SETS_FIELD)
            )
        if laboral_domain.DEFAULT_REPS_FIELD in item:
            cleaned[laboral_domain.DEFAULT_REPS_FIELD] = laboral_domain.clean_default_reps(
                item.get(laboral_domain.DEFAULT_REPS_FIELD)
            )
        if laboral_domain.DEFAULT_LOAD_FIELD in item:
            cleaned[laboral_domain.DEFAULT_LOAD_FIELD] = laboral_domain.clean_default_load(
                item.get(laboral_domain.DEFAULT_LOAD_FIELD)
            )
    except laboral_domain.LaboralDefaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return cleaned


def _store_laboral(cursor, exercise_id: int, cleaned: Dict[str, Any]) -> None:
    """Grava só os campos do laboral que vieram no payload (os nomes vêm da
    tupla fixa do dono, nunca do cliente)."""
    fields = [field for field in laboral_domain.CATALOG_FIELDS if field in cleaned]
    if not fields:
        return
    assignments = ", ".join(f"{field} = %s" for field in fields)
    cursor.execute(
        f"UPDATE exercise_catalog SET {assignments} WHERE id = %s",
        (*(cleaned[field] for field in fields), exercise_id),
    )


def _clean_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "sim", "ativo", "active"}:
        return True
    if text in {"0", "false", "no", "não", "nao", "inativo", "inactive"}:
        return False
    return default


def _require_exercise_exists(cursor, exercise_id: int) -> None:
    cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (exercise_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")


# ---------------------------------------------------------------------------
# exercise_catalog: CRUD principal
# ---------------------------------------------------------------------------


@router.get("")
def list_exercises(
    query: str = Query(default=""),
    active_only: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    where: List[str] = []
    params: List[Any] = []

    if active_only:
        where.append("ec.is_active = 1")

    if query:
        like = f"%{query}%"
        where.append(
            """
            (
                ec.name LIKE %s OR
                ec.keywords LIKE %s OR
                ec.movement LIKE %s OR
                ec.biomechanical_pattern LIKE %s OR
                ec.description LIKE %s
            )
            """
        )
        params.extend([like] * 5)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_sql = f"SELECT COUNT(*) AS total FROM exercise_catalog ec {where_sql}"

    sql = f"""
        SELECT ec.*
        FROM exercise_catalog ec
        {where_sql}
        ORDER BY ec.name
        LIMIT %s OFFSET %s
    """

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(count_sql, tuple(params))
            total = cursor.fetchone()["total"]
            cursor.execute(sql, tuple(params) + (limit, offset))
            rows = cursor.fetchall()

    return {
        "query": query,
        "active_only": active_only,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": _rows_jsonable(rows),
    }


@router.post("")
def create_exercise(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    name = _clean_text(item.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="name é obrigatório.")
    default_measure = clean_default_measure(item.get(DEFAULT_MEASURE_FIELD))
    laboral_fields = clean_laboral_fields(item)

    with _connect() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO exercise_catalog (
                        name, body_position, movement, trajectory, biomechanical_pattern,
                        primary_joint, resistance_vector, keywords, description,
                        is_active, is_unilateral, is_compound
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        name,
                        _clean_optional(item.get("body_position")),
                        _clean_optional(item.get("movement")),
                        _clean_optional(item.get("trajectory")),
                        _clean_optional(item.get("biomechanical_pattern")),
                        _clean_optional(item.get("primary_joint")),
                        _clean_optional(item.get("resistance_vector")),
                        _clean_optional(item.get("keywords")),
                        _clean_optional(item.get("description")),
                        _clean_bool(item.get("is_active"), True),
                        _clean_bool(item.get("is_unilateral"), False),
                        item.get("is_compound") if item.get("is_compound") is None else _clean_bool(item.get("is_compound"), False),
                    ),
                )
            except Exception as exc:
                if not is_duplicate_key_error(exc):
                    raise
                raise HTTPException(status_code=409, detail="Já existe um exercício com esse nome.") from exc

            exercise_id = cursor.lastrowid
            _store_default_measure(cursor, exercise_id, item, default_measure)
            _store_laboral(cursor, exercise_id, laboral_fields)
            cursor.execute("SELECT * FROM exercise_catalog WHERE id = %s", (exercise_id,))
            row = cursor.fetchone()

    # Depois do commit: nome/keywords novos podem casar exercicios sem vinculo.
    reclassify_after_catalog_change("exercises.create")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.put("/{exercise_id}")
def update_exercise(
    exercise_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_admin_identity(x_api_token, musclefit_session)
    default_measure = clean_default_measure(item.get(DEFAULT_MEASURE_FIELD))
    laboral_fields = clean_laboral_fields(item)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_exercise_exists(cursor, exercise_id)

            cursor.execute("SELECT * FROM exercise_catalog WHERE id = %s", (exercise_id,))
            current = cursor.fetchone()

            def _pick(field: str, cleaner=_clean_optional):
                if field in item:
                    return cleaner(item.get(field))
                return current.get(field)

            name = _clean_text(item.get("name")) if "name" in item else current["name"]
            if not name:
                raise HTTPException(status_code=400, detail="name não pode ficar vazio.")

            is_active = _clean_bool(item.get("is_active"), bool(current["is_active"])) if "is_active" in item else bool(current["is_active"])
            is_unilateral = _clean_bool(item.get("is_unilateral"), bool(current.get("is_unilateral") or False)) if "is_unilateral" in item else bool(current.get("is_unilateral") or False)
            if "is_compound" in item:
                is_compound = None if item.get("is_compound") is None else _clean_bool(item.get("is_compound"), False)
            else:
                is_compound = current.get("is_compound")

            try:
                cursor.execute(
                    """
                    UPDATE exercise_catalog
                    SET name = %s,
                        body_position = %s,
                        movement = %s,
                        trajectory = %s,
                        biomechanical_pattern = %s,
                        primary_joint = %s,
                        resistance_vector = %s,
                        keywords = %s,
                        description = %s,
                        is_active = %s,
                        is_unilateral = %s,
                        is_compound = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (
                        name,
                        _pick("body_position"),
                        _pick("movement"),
                        _pick("trajectory"),
                        _pick("biomechanical_pattern"),
                        _pick("primary_joint"),
                        _pick("resistance_vector"),
                        _pick("keywords"),
                        _pick("description"),
                        is_active,
                        is_unilateral,
                        is_compound,
                        exercise_id,
                    ),
                )
            except Exception as exc:
                if not is_duplicate_key_error(exc):
                    raise
                raise HTTPException(status_code=409, detail="Já existe um exercício com esse nome.") from exc

            # `execution_description` (migracao 039) fica FORA do UPDATE acima
            # de proposito: gravar o texto e uma REVISAO HUMANA, que tambem
            # carimba `reviewed_by`/`reviewed_at` e limpa a etiqueta do modelo
            # (`exercise_description_service.store_manual_text`). Campo ausente
            # no payload = nada muda, como todo o resto deste PUT parcial.
            _store_default_measure(cursor, exercise_id, item, default_measure)
            _store_laboral(cursor, exercise_id, laboral_fields)

            if "execution_description" in item:
                exercise_description_service.store_manual_text(
                    cursor,
                    exercise_id,
                    item.get("execution_description"),
                    reviewer_user_id=identity.user_id,
                )

            cursor.execute("SELECT * FROM exercise_catalog WHERE id = %s", (exercise_id,))
            row = cursor.fetchone()

    reclassify_after_catalog_change("exercises.update")
    return {"status": "ok", "item": _row_jsonable(row)}


# ---------------------------------------------------------------------------
# exercise_catalog.execution_description: geracao pela OpenAI (migracao 039)
# ---------------------------------------------------------------------------


@router.get("/execution-descriptions/pending")
def count_pending_execution_descriptions(
    prompt_version: Optional[str] = Query(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Quantos exercicios ATIVOS ainda estao sem descricao de execucao.

    Existe para a tela de `/dominio-exercicios` poder dizer "86 exercícios sem
    descrição" ANTES de o admin disparar o backfill - cada item do lote e uma
    chamada de LLM paga, e ninguem deve apertar esse botao sem saber o
    tamanho da conta. Somente leitura: nao gera nada.

    Declarado ANTES de `GET /{exercise_id}` de proposito - o FastAPI casa
    rotas na ordem de declaracao, e `/{exercise_id}` engoliria
    `/execution-descriptions/pending` (e responderia 422, id nao inteiro).
    """
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            pending = exercise_description_service.pending_count(
                cursor, prompt_version=_clean_optional(prompt_version)
            )
            cursor.execute("SELECT COUNT(*) AS total FROM exercise_catalog WHERE is_active = 1")
            total = int((cursor.fetchone() or {}).get("total") or 0)

    return {
        "pending": pending,
        "total_active": total,
        "max_batch": exercise_description_service.MAX_BATCH,
        "time_budget_seconds": exercise_description_service.BATCH_TIME_BUDGET_SECONDS,
        "prompt_version": exercise_description_service.EXEC_DESC_PROMPT_VERSION,
    }


@router.post("/execution-descriptions/generate-batch")
def generate_execution_descriptions_batch(
    payload: Dict[str, Any] = Body(default_factory=dict),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Gera a descricao de execucao de um LOTE de exercicios do catalogo.

    Este e o caminho de backfill do catalogo inteiro: chamar repetidamente ate
    `remaining` chegar a zero. O lote e limitado em itens (`MAX_BATCH`) e em
    TEMPO (`BATCH_TIME_BUDGET_SECONDS`), porque cada item e uma chamada de LLM
    e a rota roda sob o timeout de 29 s do API Gateway: quando o orcamento de
    tempo acaba, a resposta volta com `time_budget_exhausted: true` e o que
    nao foi tentado em `deferred` - e o chamador chama de novo.

    Cada item gerado e COMMITADO na hora (`commit=conn.commit`): se o gateway
    cortar o request no item seguinte, o que ja foi gerado - e pago - fica no
    catalogo, em vez de a transacao inteira ser descartada com a Lambda.

    Corpo (tudo opcional): `limit`, `exercise_ids` (ids explicitos),
    `force` (regera por cima do que ja existe, inclusive texto revisado por
    humano - use com cuidado), `prompt_version` (tambem regera o que saiu de
    uma versao de prompt DIFERENTE desta, desde que nao revisado a mao) e
    `model`.

    Rota ANTES de `/{exercise_id}` neste arquivo? Nao precisa: este endpoint e
    POST e nao existe `POST /exercises/{exercise_id}` para colidir.
    """
    _require_admin_identity(x_api_token, musclefit_session)

    prompt_version = _clean_optional(payload.get("prompt_version"))
    exercise_ids = payload.get("exercise_ids") or None
    if exercise_ids is not None and not isinstance(exercise_ids, list):
        raise HTTPException(status_code=400, detail="exercise_ids deve ser uma lista de ids.")
    try:
        limit = int(payload.get("limit") or exercise_description_service.MAX_BATCH)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit inválido.") from None

    with _connect() as conn:
        with conn.cursor() as cursor:
            result = exercise_description_service.generate_batch(
                cursor,
                limit=limit,
                exercise_ids=[int(value) for value in exercise_ids] if exercise_ids else None,
                force=_clean_bool(payload.get("force"), False),
                prompt_version=prompt_version,
                model=_clean_optional(payload.get("model")),
                # Persistencia item a item: nos dois backends (`db_dataapi`,
                # `db_pymysql`) `commit()` fecha a transacao aberta; o que
                # vier depois roda autocommitado e o commit implicito do
                # `with conn:` no fim vira no-op. Nada fica preso numa
                # transacao que a morte da Lambda descartaria.
                commit=conn.commit,
            )
    return {"status": "ok", **result}


@router.post("/{exercise_id}/execution-description/generate")
def generate_execution_description_for_exercise(
    exercise_id: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Gera (ou regera, com `force`) a descricao de execucao de UM exercicio.

    `409` quando ja existe descricao e `force` nao foi pedido - o texto pode
    ter sido revisado a mao, e sobrescrever calado seria apagar trabalho
    humano. `502` quando a OpenAI falha: o catalogo continua exatamente como
    estava.
    """
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_exercise_exists(cursor, exercise_id)
            result = exercise_description_service.generate_and_store(
                cursor,
                exercise_id,
                force=_clean_bool(payload.get("force"), False),
                model=_clean_optional(payload.get("model")),
            )
            if result["status"] == exercise_description_service.STATUS_FAILED:
                raise HTTPException(
                    status_code=502,
                    detail={"error": "execution_description_generation_failed", "message": result.get("error")},
                )
            if result["status"] == exercise_description_service.STATUS_SKIPPED:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "execution_description_already_generated",
                        "message": "Este exercício já tem descrição. Use force=true para regerar.",
                    },
                )
            cursor.execute("SELECT * FROM exercise_catalog WHERE id = %s", (exercise_id,))
            row = cursor.fetchone()

    return {"status": "ok", "generation": result, "item": _row_jsonable(row)}


@router.get("/{exercise_id}")
def get_exercise_detail(
    exercise_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Endpoint agregado: dados do exercício + aliases + mapeamentos externos +
    contribuições musculares + equipamentos vinculados + ids legados fundidos
    (exercise_legacy_id_map), para montar a tela de detalhe/edição em uma
    única chamada."""
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM exercise_catalog WHERE id = %s", (exercise_id,))
            exercise = cursor.fetchone()
            if not exercise:
                raise HTTPException(status_code=404, detail="Exercício não encontrado.")

            cursor.execute(
                "SELECT id, exercise_id, alias, source, created_at FROM exercise_aliases WHERE exercise_id = %s ORDER BY alias",
                (exercise_id,),
            )
            aliases = cursor.fetchall()

            cursor.execute(
                """
                SELECT id, exercise_id, provider, external_id, external_name, mapping_status, source, notes, is_active, created_at, updated_at
                FROM exercise_external_mappings
                WHERE exercise_id = %s
                ORDER BY provider, external_name
                """,
                (exercise_id,),
            )
            external_mappings = cursor.fetchall()

            cursor.execute(
                """
                SELECT emc.id, emc.exercise_id, emc.muscle_group_id, mg.name AS muscle_group_name,
                       emc.role, emc.stimulus_weight, emc.fatigue_weight, emc.is_active
                FROM exercise_muscle_contributions emc
                JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
                WHERE emc.exercise_id = %s AND emc.is_active = 1
                ORDER BY FIELD(emc.role, 'PRIMARY', 'SECONDARY', 'STABILIZER'), mg.name
                """,
                (exercise_id,),
            )
            muscle_contributions = cursor.fetchall()

            cursor.execute(
                """
                SELECT eqex.id, eqex.equipment_id, eq.name AS equipment_name, eqex.is_preferred, eqex.is_active, eqex.notes,
                       (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://app.musclefit.com.br/', eq.photo_path) ELSE NULL END) AS photo_url
                FROM equipment_exercises eqex
                JOIN equipment eq ON eq.id = eqex.equipment_id
                WHERE eqex.exercise_id = %s AND eqex.is_active = 1
                ORDER BY eq.name
                """,
                (exercise_id,),
            )
            equipment_links = cursor.fetchall()

            cursor.execute(
                "SELECT old_exercise_id FROM exercise_legacy_id_map WHERE canonical_exercise_id = %s AND old_exercise_id != %s ORDER BY old_exercise_id",
                (exercise_id, exercise_id),
            )
            legacy_ids = [row["old_exercise_id"] for row in cursor.fetchall()]

    return {
        "exercise": _row_jsonable(exercise),
        "aliases": _rows_jsonable(aliases),
        "external_mappings": _rows_jsonable(external_mappings),
        "muscle_contributions": _rows_jsonable(muscle_contributions),
        "equipment_links": _rows_jsonable(equipment_links),
        "legacy_ids_merged": legacy_ids,
    }


# ---------------------------------------------------------------------------
# exercise_aliases (exercise_catalog)
# ---------------------------------------------------------------------------


@router.get("/{exercise_id}/aliases")
def list_exercise_aliases(
    exercise_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_exercise_exists(cursor, exercise_id)
            cursor.execute(
                "SELECT id, exercise_id, alias, source, created_at FROM exercise_aliases WHERE exercise_id = %s ORDER BY alias",
                (exercise_id,),
            )
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


@router.post("/{exercise_id}/aliases")
def create_exercise_alias(
    exercise_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    alias = _clean_text(item.get("alias"))
    if not alias:
        raise HTTPException(status_code=400, detail="alias é obrigatório.")
    source = _clean_optional(item.get("source"))

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_exercise_exists(cursor, exercise_id)
            try:
                cursor.execute(
                    "INSERT INTO exercise_aliases (exercise_id, alias, source) VALUES (%s, %s, %s)",
                    (exercise_id, alias, source),
                )
            except Exception as exc:
                # So UNIQUE (exercise_id, alias), como na Aurora (029): o mesmo
                # apelido em OUTRO exercicio e aceito (decisao 2026-09-14).
                if not is_duplicate_key_error(exc):
                    raise
                raise HTTPException(status_code=409, detail="Este apelido já está cadastrado para este exercício.") from exc

            alias_id = cursor.lastrowid
            cursor.execute(
                "SELECT id, exercise_id, alias, source, created_at FROM exercise_aliases WHERE id = %s",
                (alias_id,),
            )
            row = cursor.fetchone()

    reclassify_after_catalog_change("exercises.aliases.create")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/aliases/{alias_id}")
def delete_exercise_alias(
    alias_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM exercise_aliases WHERE id = %s", (alias_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Apelido não encontrado.")

    return {"status": "ok", "id": alias_id}


# ---------------------------------------------------------------------------
# exercise_external_mappings
# ---------------------------------------------------------------------------


@router.get("/{exercise_id}/external-mappings")
def list_exercise_external_mappings(
    exercise_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_exercise_exists(cursor, exercise_id)
            cursor.execute(
                """
                SELECT id, exercise_id, provider, external_id, external_name, mapping_status, source, notes, is_active, created_at, updated_at
                FROM exercise_external_mappings
                WHERE exercise_id = %s
                ORDER BY provider, external_name
                """,
                (exercise_id,),
            )
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


@router.post("/{exercise_id}/external-mappings")
def create_exercise_external_mapping(
    exercise_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    provider = _clean_text(item.get("provider"))
    external_name = _clean_text(item.get("external_name"))
    if not provider:
        raise HTTPException(status_code=400, detail="provider é obrigatório.")
    if not external_name:
        raise HTTPException(status_code=400, detail="external_name é obrigatório.")

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_exercise_exists(cursor, exercise_id)
            cursor.execute(
                """
                INSERT INTO exercise_external_mappings (
                    exercise_id, provider, external_id, external_name, mapping_status, source, notes, is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    exercise_id,
                    provider,
                    _clean_optional(item.get("external_id")),
                    external_name,
                    _clean_text(item.get("mapping_status"), "CONFIRMED"),
                    _clean_optional(item.get("source")),
                    _clean_optional(item.get("notes")),
                    _clean_bool(item.get("is_active"), True),
                ),
            )
            mapping_row_id = cursor.lastrowid
            cursor.execute(
                "SELECT * FROM exercise_external_mappings WHERE id = %s",
                (mapping_row_id,),
            )
            row = cursor.fetchone()

    reclassify_after_catalog_change("exercises.external_mappings.create")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.put("/external-mappings/{mapping_row_id}")
def update_exercise_external_mapping(
    mapping_row_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM exercise_external_mappings WHERE id = %s", (mapping_row_id,))
            current = cursor.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Mapeamento externo não encontrado.")

            cursor.execute(
                """
                UPDATE exercise_external_mappings
                SET provider = %s,
                    external_id = %s,
                    external_name = %s,
                    mapping_status = %s,
                    source = %s,
                    notes = %s,
                    is_active = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    _clean_text(item.get("provider"), current["provider"]),
                    item.get("external_id") if "external_id" in item else current["external_id"],
                    _clean_text(item.get("external_name"), current["external_name"]),
                    _clean_text(item.get("mapping_status"), current["mapping_status"]),
                    item.get("source") if "source" in item else current["source"],
                    item.get("notes") if "notes" in item else current["notes"],
                    _clean_bool(item.get("is_active"), bool(current["is_active"])) if "is_active" in item else bool(current["is_active"]),
                    mapping_row_id,
                ),
            )
            cursor.execute("SELECT * FROM exercise_external_mappings WHERE id = %s", (mapping_row_id,))
            row = cursor.fetchone()

    reclassify_after_catalog_change("exercises.external_mappings.update")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/external-mappings/{mapping_row_id}")
def delete_exercise_external_mapping(
    mapping_row_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM exercise_external_mappings WHERE id = %s", (mapping_row_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Mapeamento externo não encontrado.")

    return {"status": "ok", "id": mapping_row_id}


# ---------------------------------------------------------------------------
# exercise_external_mappings: listagem FLAT (uma linha por mapeamento, nao
# agrupada por exercicio), usada pela tela /mapeamentos ("Mapeamentos
# Externos"). Complementa o GET por-exercicio acima, que continua servindo a
# tela de detalhe de /admin/dominio-exercicios.
#
# Escrita (POST/PUT/DELETE) continua nas rotas por-exercicio ja existentes:
#   POST   /exercises/{exercise_id}/external-mappings
#   PUT    /exercises/external-mappings/{id}
#   DELETE /exercises/external-mappings/{id}
# ---------------------------------------------------------------------------


@external_mappings_router.get("")
def list_external_mappings_flat(
    query: str = Query(default=""),
    provider: str = Query(default=""),
    active_only: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    where: List[str] = []
    params: List[Any] = []

    if active_only:
        where.append("exm.is_active = 1")

    if provider:
        where.append("exm.provider = %s")
        params.append(provider)

    if query:
        like = f"%{query}%"
        where.append("(ec.name LIKE %s OR exm.external_name LIKE %s OR exm.external_id LIKE %s)")
        params.extend([like] * 3)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM exercise_external_mappings exm
        JOIN exercise_catalog ec ON ec.id = exm.exercise_id
        {where_sql}
    """

    sql = f"""
        SELECT
            exm.id,
            exm.exercise_id,
            ec.name AS exercise_name,
            ec.is_active AS exercise_is_active,
            exm.provider,
            exm.external_id,
            exm.external_name,
            exm.mapping_status,
            exm.source,
            exm.notes,
            exm.is_active,
            exm.created_at,
            exm.updated_at
        FROM exercise_external_mappings exm
        JOIN exercise_catalog ec ON ec.id = exm.exercise_id
        {where_sql}
        ORDER BY ec.name, exm.provider, exm.external_name
        LIMIT %s OFFSET %s
    """

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(count_sql, tuple(params))
            total = cursor.fetchone()["total"]
            cursor.execute(sql, tuple(params) + (limit, offset))
            rows = cursor.fetchall()

    return {
        "query": query,
        "provider": provider,
        "active_only": active_only,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": _rows_jsonable(rows),
    }


@external_mappings_router.get("/providers")
def list_external_mapping_providers(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Providers distintos existentes, para popular o filtro da tela."""
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT provider, COUNT(*) AS count
                FROM exercise_external_mappings
                GROUP BY provider
                ORDER BY provider
                """
            )
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


# ---------------------------------------------------------------------------
# equipment_types: CRUD simples (8 linhas hoje)
# ---------------------------------------------------------------------------


@equipment_types_router.get("")
def list_equipment_types(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM equipment_types ORDER BY name")
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


@equipment_types_router.post("")
def create_equipment_type(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    code = _clean_text(item.get("code")).upper()
    name = _clean_text(item.get("name"))
    if not code:
        raise HTTPException(status_code=400, detail="code é obrigatório.")
    if not name:
        raise HTTPException(status_code=400, detail="name é obrigatório.")

    with _connect() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    "INSERT INTO equipment_types (code, name, description) VALUES (%s, %s, %s)",
                    (code, name, _clean_optional(item.get("description"))),
                )
            except Exception as exc:
                if not is_duplicate_key_error(exc):
                    raise
                raise HTTPException(status_code=409, detail="Já existe um tipo de equipamento com esse código/nome.") from exc

            type_id = cursor.lastrowid
            cursor.execute("SELECT * FROM equipment_types WHERE id = %s", (type_id,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@equipment_types_router.put("/{type_id}")
def update_equipment_type(
    type_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM equipment_types WHERE id = %s", (type_id,))
            current = cursor.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Tipo de equipamento não encontrado.")

            try:
                cursor.execute(
                    """
                    UPDATE equipment_types
                    SET code = %s, name = %s, description = %s
                    WHERE id = %s
                    """,
                    (
                        _clean_text(item.get("code"), current["code"]).upper(),
                        _clean_text(item.get("name"), current["name"]),
                        item.get("description") if "description" in item else current["description"],
                        type_id,
                    ),
                )
            except Exception as exc:
                if not is_duplicate_key_error(exc):
                    raise
                raise HTTPException(status_code=409, detail="Já existe um tipo de equipamento com esse código/nome.") from exc

            cursor.execute("SELECT * FROM equipment_types WHERE id = %s", (type_id,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@equipment_types_router.delete("/{type_id}")
def delete_equipment_type(
    type_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM equipment WHERE equipment_type_id = %s", (type_id,))
            in_use = cursor.fetchone()["total"]
            if in_use:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tipo em uso por {in_use} equipamento(s); remova a associação antes de excluir.",
                )

            cursor.execute("DELETE FROM equipment_types WHERE id = %s", (type_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Tipo de equipamento não encontrado.")

    return {"status": "ok", "id": type_id}

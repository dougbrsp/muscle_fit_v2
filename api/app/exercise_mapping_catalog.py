from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query

from . import security
from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503, DB_UNAVAILABLE_MYSQL, jsonable as _jsonable
from .security import resolve_identity_from_credentials as _require_identity
from .exercise_mapping_canonical import reclassify_after_catalog_change
from .intelligence.muscle_contribution_resolver import DEFAULT_WRITE_WEIGHTS

router = APIRouter(prefix="/exercise-mapping-catalog", tags=["exercise-mapping-catalog"])


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


def _find_or_create_muscle_group(cursor, name: Optional[str]) -> Optional[int]:
    """Resolve nome -> muscle_groups.id, criando a linha se não existir.

    Usado apenas nos pontos onde um humano (curador via /mapeamentos) está
    escolhendo/digitando deliberadamente um grupo muscular — nunca para
    inferência automática de IA. Isso preserva o comportamento anterior de
    "digite um grupo novo e ele passa a existir", só que agora canonicalizado
    numa tabela em vez de texto livre repetido por linha.
    """
    clean = _clean_optional(name)
    if not clean:
        return None

    cursor.execute("SELECT id FROM muscle_groups WHERE name = %s", (clean,))
    row = cursor.fetchone()
    if row:
        return row["id"]

    cursor.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (clean,))
    return cursor.lastrowid


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


def _clean_bool(value: Any, default: bool = True) -> bool:
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


def _clean_item(item: Dict[str, Any]) -> Dict[str, Any]:
    exercise_name = _clean_text(item.get("exercise_name") or item.get("Exercício") or item.get("exercicio"))
    amazfit_name = _clean_text(item.get("amazfit_name") or item.get("Amazfit") or item.get("zepp_name"))

    if not exercise_name:
        raise HTTPException(status_code=400, detail="exercise_name é obrigatório.")

    if not amazfit_name:
        raise HTTPException(status_code=400, detail="amazfit_name é obrigatório.")

    return {
        "exercise_name": exercise_name,
        "amazfit_name": amazfit_name,
        "equipment": _clean_optional(item.get("equipment") or item.get("Equipamento")),
        "body_position": _clean_optional(item.get("body_position") or item.get("Posição")),
        "movement": _clean_optional(item.get("movement") or item.get("Movimento")),
        "trajectory": _clean_optional(item.get("trajectory") or item.get("Trajetória")),
        "biomechanical_pattern": _clean_optional(item.get("biomechanical_pattern") or item.get("Padrão biomecânico")),
        "primary_joint": _clean_optional(item.get("primary_joint") or item.get("Articulação principal")),
        "resistance_vector": _clean_optional(item.get("resistance_vector") or item.get("Vetor de resistência")),
        # primary_group/secondary_group chegam como NOME (texto) do curador
        # via /mapeamentos; são resolvidos para muscle_groups.id em
        # _write_catalog_item (find-or-create), nunca gravados como texto
        # livre na tabela. primary_muscles/secondary_muscles não existem mais
        # no catálogo (migraram para muscle_groups) — se vierem num payload de
        # import legado, são ignorados silenciosamente em vez de quebrar o
        # import.
        "primary_group": _clean_optional(item.get("primary_group") or item.get("Grupo principal")),
        "secondary_group": _clean_optional(item.get("secondary_group") or item.get("Grupo secundário")),
        "keywords": _clean_optional(item.get("keywords") or item.get("Palavras-chave")),
        "description": _clean_optional(item.get("description") or item.get("Descrição")),
        "mapping_status": _clean_text(item.get("mapping_status"), "confirmed"),
        "source": _clean_text(item.get("source"), "manual"),
        "is_active": _clean_bool(item.get("is_active"), True),
    }


# Placeholders SEMPRE posicionais (`%s` + tupla): a camada de dados (app/db.py)
# nao aceita placeholder nomeado + dict em nenhum dos dois backends - PyMySQL
# levanta TypeError e o shim Data API conta 0 placeholders. Coluna e valor saem
# da mesma tupla, entao a ordem nao tem como divergir.
#
# (coluna de exercise_catalog, chave de _clean_item)
_CATALOG_WRITE_COLUMNS = (
    ("name", "exercise_name"),
    ("body_position", "body_position"),
    ("movement", "movement"),
    ("trajectory", "trajectory"),
    ("biomechanical_pattern", "biomechanical_pattern"),
    ("primary_joint", "primary_joint"),
    ("resistance_vector", "resistance_vector"),
    ("keywords", "keywords"),
    ("description", "description"),
    ("is_active", "is_active"),
)


def _placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


def _select_catalog_item(cursor, exercise_id: int) -> Optional[Dict[str, Any]]:
    """Devolve um item de exercise_catalog no MESMO shape JSON de
    list_mappings (byte a byte nos nomes de campo), para uma unica linha
    identificada por id. Usado pelo retorno de POST/PUT
    /exercise-mapping-catalog agora que essas rotas escrevem em
    exercise_catalog (ver _write_catalog_item) em vez do legado
    exercise_zepp_mappings."""
    cursor.execute(
        """
        SELECT
            ec.id,
            ec.name AS exercise_name,
            (
                SELECT exm.external_name
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS amazfit_name,
            NULL AS equipment,
            ec.body_position,
            ec.movement,
            ec.trajectory,
            ec.biomechanical_pattern,
            ec.primary_joint,
            ec.resistance_vector,
            pg.id AS primary_group_id,
            pg.name AS primary_group,
            sg.id AS secondary_group_id,
            sg.name AS secondary_group,
            ec.keywords,
            ec.description,
            (
                SELECT exm.mapping_status
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS mapping_status,
            (
                SELECT exm.source
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS source,
            ec.is_active,
            ec.created_at,
            ec.updated_at
        FROM exercise_catalog ec
        LEFT JOIN muscle_groups pg ON pg.id = (
            SELECT emc_pri.muscle_group_id
            FROM exercise_muscle_contributions emc_pri
            WHERE emc_pri.exercise_id = ec.id AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
            ORDER BY emc_pri.id ASC LIMIT 1
        )
        LEFT JOIN muscle_groups sg ON sg.id = (
            SELECT emc_sec.muscle_group_id
            FROM exercise_muscle_contributions emc_sec
            WHERE emc_sec.exercise_id = ec.id AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
            ORDER BY emc_sec.id ASC LIMIT 1
        )
        WHERE ec.id = %s
        """,
        (exercise_id,),
    )
    return cursor.fetchone()


# Pesos default para exercise_muscle_contributions ao gravar via o formulario
# legado de /exercise-mapping-catalog, que so informa "primary_group"/
# "secondary_group" (sem peso explicito): os defaults de escrita por papel do
# dono unico (`muscle_contribution_resolver.DEFAULT_WRITE_WEIGHTS`, M76 Lote 3c).
_PRIMARY_CONTRIBUTION_WEIGHT = DEFAULT_WRITE_WEIGHTS["PRIMARY"]
_SECONDARY_CONTRIBUTION_WEIGHT = DEFAULT_WRITE_WEIGHTS["SECONDARY"]


def _upsert_role_contribution(cursor, exercise_id: int, role: str, muscle_group_id: Optional[int], weight: float) -> None:
    """Garante que a UNICA contribuicao ATIVA do papel `role` (PRIMARY ou
    SECONDARY) para `exercise_id` aponte para `muscle_group_id` — desativa a
    contribuicao antiga desse papel se o grupo mudou/foi removido, e
    ativa/insere a nova quando `muscle_group_id` for informado."""
    cursor.execute(
        "SELECT id, muscle_group_id FROM exercise_muscle_contributions "
        "WHERE exercise_id = %s AND role = %s AND is_active = 1",
        (exercise_id, role),
    )
    current = cursor.fetchone()

    if muscle_group_id is None:
        if current:
            cursor.execute(
                "UPDATE exercise_muscle_contributions SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (current["id"],),
            )
        return

    if current and current["muscle_group_id"] == muscle_group_id:
        return

    if current:
        cursor.execute(
            "UPDATE exercise_muscle_contributions SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (current["id"],),
        )

    cursor.execute(
        """
        INSERT INTO exercise_muscle_contributions (
            exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active
        )
        VALUES (%s, %s, %s, %s, %s, 1)
        ON DUPLICATE KEY UPDATE
            role = VALUES(role),
            stimulus_weight = VALUES(stimulus_weight),
            fatigue_weight = VALUES(fatigue_weight),
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (exercise_id, muscle_group_id, role, weight, weight),
    )


def _upsert_amazfit_external_mapping(
    cursor,
    exercise_id: int,
    amazfit_name: str,
    mapping_status: str,
    source: str,
) -> None:
    """Upsert da UNICA linha AMAZFIT ativa de exercise_external_mappings para
    o exercicio (unico provider usado pelo formulario legado de
    /exercise-mapping-catalog)."""
    cursor.execute(
        "SELECT id FROM exercise_external_mappings "
        "WHERE exercise_id = %s AND provider = 'AMAZFIT' AND is_active = 1 "
        "ORDER BY id ASC LIMIT 1",
        (exercise_id,),
    )
    current = cursor.fetchone()

    if current:
        cursor.execute(
            """
            UPDATE exercise_external_mappings
            SET external_name = %s, mapping_status = %s, source = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (amazfit_name, mapping_status, source, current["id"]),
        )
    else:
        cursor.execute(
            """
            INSERT INTO exercise_external_mappings (
                exercise_id, provider, external_name, mapping_status, source, is_active
            )
            VALUES (%s, 'AMAZFIT', %s, %s, %s, 1)
            """,
            (exercise_id, amazfit_name, mapping_status, source),
        )


def _write_catalog_item(cursor, cleaned: Dict[str, Any], exercise_id: Optional[int] = None) -> int:
    """Grava um item do formulario legado de /exercise-mapping-catalog na
    fonte de verdade ATUAL do catalogo canonico (exercise_catalog +
    exercise_external_mappings + exercise_muscle_contributions), em vez do
    legado exercise_zepp_mappings.

    Corrige a divergencia leitura/escrita relatada na tela /grupos-musculares
    (alerta amarelo em GruposMuscularesClient.tsx): GET /exercise-mapping-
    catalog ja lia de exercise_catalog (ver list_mappings acima, migrado
    ha mais tempo) mas POST/PUT continuavam gravando em
    exercise_zepp_mappings — uma tabela que os motores de calculo
    (VolumeService, muscle_contribution_resolver etc.) nao leem mais desde a
    reestruturacao de 2026-08. O item ficava invisivel na propria listagem
    logo apos ser salvo.

    `equipment` continua sem persistir: nao existe coluna equivalente em
    exercise_catalog (mesma ressalva ja documentada em list_mappings) — a
    fonte de verdade de equipamento e equipment_exercise_mappings/
    gym_equipment, fora do escopo deste formulario legado.

    Se `exercise_id` for None, resolve por nome exato (mesma semantica de
    upsert-por-exercise_name que POST tinha antes) ou cria um exercicio novo.
    """
    primary_group_id = _find_or_create_muscle_group(cursor, cleaned.get("primary_group"))
    secondary_group_id = _find_or_create_muscle_group(cursor, cleaned.get("secondary_group"))

    if exercise_id is None:
        cursor.execute("SELECT id FROM exercise_catalog WHERE name = %s", (cleaned["exercise_name"],))
        existing = cursor.fetchone()
        exercise_id = existing["id"] if existing else None

    columns = [column for column, _ in _CATALOG_WRITE_COLUMNS]
    values = tuple(cleaned[key] for _, key in _CATALOG_WRITE_COLUMNS)

    if exercise_id is None:
        cursor.execute(
            f"INSERT INTO exercise_catalog ({', '.join(columns)}) VALUES ({_placeholders(len(columns))})",
            values,
        )
        exercise_id = cursor.lastrowid
    else:
        assignments = ", ".join(f"{column} = %s" for column in columns)
        cursor.execute(
            f"UPDATE exercise_catalog SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            values + (exercise_id,),
        )

    _upsert_amazfit_external_mapping(
        cursor,
        exercise_id,
        cleaned["amazfit_name"],
        cleaned["mapping_status"],
        cleaned["source"],
    )

    _upsert_role_contribution(cursor, exercise_id, "PRIMARY", primary_group_id, _PRIMARY_CONTRIBUTION_WEIGHT)

    secondary_target = secondary_group_id if secondary_group_id != primary_group_id else None
    _upsert_role_contribution(cursor, exercise_id, "SECONDARY", secondary_target, _SECONDARY_CONTRIBUTION_WEIGHT)

    return exercise_id


@router.get("/health")
def health(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)
    return {
        "status": "ok",
        "read_table": "exercise_catalog",
        "write_table": "exercise_catalog",
        "purpose": (
            "leitura e escrita (POST/PUT/DELETE = inativar) no catálogo canônico (exercise_catalog + "
            "exercise_external_mappings + exercise_muscle_contributions). "
            "Nenhuma rota escreve mais em exercise_zepp_mappings."
        ),
    }


@router.get("")
def list_mappings(
    query: str = Query(default=""),
    primary_group: str = Query(default=""),
    amazfit_name: str = Query(default=""),
    active_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Busca de LEITURA migrada para o catálogo canônico novo.

    Fonte: `exercise_catalog` (+ `exercise_muscle_contributions` para
    primary/secondary group, `exercise_external_mappings` provider='AMAZFIT'
    para `amazfit_name`, `exercise_aliases` para a busca textual). NÃO lê mais
    `exercise_zepp_mappings` (legado, divergente: 93 linhas originais contra os
    79 canônicos consolidados).

    O shape do JSON de resposta é preservado byte a byte em nomes de campo,
    porque este endpoint alimenta as ferramentas MCP
    `search_exercise_mapping_catalog` / `get_exercise_mapping_for_training`
    usadas pelo fluxo conversacional do Telegram/OpenClaw. Duas ressalvas:

    - `equipment` (texto livre do legado) não existe em `exercise_catalog` e
      volta sempre `None` — a informação de equipamento vive hoje em
      `equipment_exercise_mappings`/`/gym-equipment/recommend`.
    - `mapping_status`/`source` passam a vir da linha AMAZFIT de
      `exercise_external_mappings` (que herdou esses valores na migração);
      `None` quando o exercício não tiver mapeamento externo AMAZFIT.

    Critério de desempate quando há mais de um PRIMARY/SECONDARY para o mesmo
    exercício: `ORDER BY emc.id ASC LIMIT 1`, idêntico ao já usado por
    `intelligence/volume_service.py`, para as duas leituras nunca divergirem.
    """
    _require_identity(x_api_token, musclefit_session)
    with _connect() as conn:
        return query_mapping_catalog(
            conn,
            query=query,
            primary_group=primary_group,
            amazfit_name=amazfit_name,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )


def query_mapping_catalog(
    conn,
    *,
    query: str = "",
    primary_group: str = "",
    amazfit_name: str = "",
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """Leitura do catalogo canonico no formato de `GET /exercise-mapping-catalog`.

    Fonte unica da rota e da geracao de ficha
    (`workout_plan_ai.gather_ficha_context`), que antes fazia self-call HTTP
    com o token mestre (SEG-04). Criterios de busca e desempate: ver a
    docstring de `list_mappings`."""
    where = []
    params: List[Any] = []

    if active_only:
        where.append("ec.is_active = 1")

    if primary_group:
        where.append("pg.name = %s")
        params.append(primary_group)

    if amazfit_name:
        where.append(
            """
            EXISTS (
                SELECT 1 FROM exercise_external_mappings exm_f
                WHERE exm_f.exercise_id = ec.id
                  AND exm_f.provider = 'AMAZFIT'
                  AND exm_f.is_active = 1
                  AND exm_f.external_name LIKE %s
            )
            """
        )
        params.append(f"%{amazfit_name}%")

    if query:
        # Busca simples (LIKE, não FULLTEXT) sobre os campos textuais do
        # exercício + nome/músculos do grupo muscular canônico + nome externo
        # (Amazfit e demais providers) + APELIDOS. Os 419 aliases de
        # exercise_aliases são o ganho real desta migração: o legado só tinha o
        # campo `keywords`, bem mais esparso.
        like = f"%{query}%"
        where.append(
            """
            (
                ec.name LIKE %s OR
                ec.movement LIKE %s OR
                ec.biomechanical_pattern LIKE %s OR
                pg.name LIKE %s OR
                sg.name LIKE %s OR
                pg.primary_muscles LIKE %s OR
                pg.secondary_muscles LIKE %s OR
                sg.primary_muscles LIKE %s OR
                sg.secondary_muscles LIKE %s OR
                ec.keywords LIKE %s OR
                ec.description LIKE %s OR
                EXISTS (
                    SELECT 1 FROM exercise_aliases ea
                    WHERE ea.exercise_id = ec.id AND ea.alias LIKE %s
                ) OR
                EXISTS (
                    SELECT 1 FROM exercise_external_mappings exm_q
                    WHERE exm_q.exercise_id = ec.id
                      AND exm_q.is_active = 1
                      AND exm_q.external_name LIKE %s
                )
            )
            """
        )
        params.extend([like] * 13)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    from_sql = """
        FROM exercise_catalog ec
        LEFT JOIN muscle_groups pg ON pg.id = (
            SELECT emc_pri.muscle_group_id
            FROM exercise_muscle_contributions emc_pri
            WHERE emc_pri.exercise_id = ec.id AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
            ORDER BY emc_pri.id ASC LIMIT 1
        )
        LEFT JOIN muscle_groups sg ON sg.id = (
            SELECT emc_sec.muscle_group_id
            FROM exercise_muscle_contributions emc_sec
            WHERE emc_sec.exercise_id = ec.id AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
            ORDER BY emc_sec.id ASC LIMIT 1
        )
    """

    sql = f"""
        SELECT
            ec.id,
            ec.name AS exercise_name,
            (
                SELECT exm.external_name
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS amazfit_name,
            NULL AS equipment,
            ec.body_position,
            ec.movement,
            ec.trajectory,
            ec.biomechanical_pattern,
            ec.primary_joint,
            ec.resistance_vector,
            pg.id AS primary_group_id,
            pg.name AS primary_group,
            sg.id AS secondary_group_id,
            sg.name AS secondary_group,
            ec.keywords,
            ec.description,
            (
                SELECT exm.mapping_status
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS mapping_status,
            (
                SELECT exm.source
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS source,
            ec.is_active,
            ec.created_at,
            ec.updated_at
        {from_sql}
        {where_sql}
        ORDER BY
            CASE
                WHEN ec.name = %s THEN 0
                WHEN EXISTS (
                    SELECT 1 FROM exercise_aliases ea_exact
                    WHERE ea_exact.exercise_id = ec.id AND ea_exact.alias = %s
                ) THEN 1
                ELSE 2
            END,
            pg.name,
            ec.name
        LIMIT %s OFFSET %s
    """

    count_sql = f"""
        SELECT COUNT(*) AS total
        {from_sql}
        {where_sql}
    """
    count_params = tuple(params)
    list_params = count_params + (query, query, limit, offset)

    with conn.cursor() as cursor:
        cursor.execute(count_sql, count_params)
        total = cursor.fetchone()["total"]
        cursor.execute(sql, list_params)
        rows = [_row_jsonable(row) for row in cursor.fetchall()]

    return {
        "query": query,
        "primary_group": primary_group,
        "active_only": active_only,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": rows,
    }


@router.get("/groups")
def list_groups(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                -- Migrado de exercise_zepp_mappings.primary_group_id para
                -- exercise_catalog + exercise_muscle_contributions (role
                -- PRIMARY), mesmo criterio de desempate de list_mappings.
                SELECT pg.name AS primary_group, COUNT(*) AS count
                FROM exercise_catalog ec
                JOIN muscle_groups pg ON pg.id = (
                    SELECT emc_pri.muscle_group_id
                    FROM exercise_muscle_contributions emc_pri
                    WHERE emc_pri.exercise_id = ec.id AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
                    ORDER BY emc_pri.id ASC LIMIT 1
                )
                WHERE ec.is_active = 1
                GROUP BY pg.name
                ORDER BY pg.name
                """
            )
            rows = [_row_jsonable(row) for row in cursor.fetchall()]

    return {"items": rows}


@router.get("/{mapping_id}")
def get_mapping(
    mapping_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)
    # Migrado para exercise_catalog (mesma fonte de list_mappings/POST/PUT):
    # antes lia de exercise_zepp_mappings, que ficou desatualizado assim que
    # POST/PUT passaram a gravar em exercise_catalog (ver _write_catalog_item).
    with _connect() as conn:
        with conn.cursor() as cursor:
            row = _select_catalog_item(cursor, mapping_id)

    if not row:
        raise HTTPException(status_code=404, detail="Mapeamento não encontrado.")

    return _row_jsonable(row)


@router.post("")
def create_or_upsert_mapping(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)
    cleaned = _clean_item(item)

    with _connect() as conn:
        with conn.cursor() as cursor:
            exercise_id = _write_catalog_item(cursor, cleaned)
            row = _select_catalog_item(cursor, exercise_id)

    # Depois do commit: nome/AMAZFIT/grupo primario novos podem casar
    # exercicios sem vinculo.
    reclassify_after_catalog_change("exercise-mapping-catalog.create")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.put("/{mapping_id}")
def update_mapping(
    mapping_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)
    cleaned = _clean_item(item)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (mapping_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Mapeamento não encontrado.")

            exercise_id = _write_catalog_item(cursor, cleaned, exercise_id=mapping_id)
            row = _select_catalog_item(cursor, exercise_id)

    reclassify_after_catalog_change("exercise-mapping-catalog.update")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/{mapping_id}")
def deactivate_mapping(
    mapping_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Inativa (SOFT delete) um item do catalogo canonico - o "Inativar" de
    /grupos-musculares.

    Antes gravava em `exercise_zepp_mappings` (legado) enquanto GET/POST/PUT
    ja usam `exercise_catalog`: item criado pelo POST nao existia no legado,
    o UPDATE nao encontrava linha e a rota respondia 404 com o item ativo.

    Soft delete (`is_active = 0`), nunca DELETE: as FKs para
    exercise_catalog(id) sao CASCADE (aliases, nomes externos, contribuicoes,
    equipment_exercises, gym_equipment, equipment_exercise_mappings) e SET NULL
    (exercises, workout_plan_exercises, workout_plan_template_slot_items) -
    apagar a linha desfaria o vinculo do historico de treino. Dependentes ficam
    intactos, entao o PUT com `is_active` verdadeiro reativa o item.

    Existencia checada por SELECT (como no PUT), nao por `rowcount`:
    inativar um item ja inativo e idempotente (200)."""
    _require_admin_identity(x_api_token, musclefit_session)
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (mapping_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Mapeamento não encontrado.")

            cursor.execute(
                "UPDATE exercise_catalog SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (mapping_id,),
            )

    # Depois do commit, mesmo contrato de POST/PUT: o matching passa a ignorar
    # o item inativado. Vinculos ja gravados (historico) nao sao alterados -
    # a reclassificacao so processa `mapping_id IS NULL`.
    reclassify_after_catalog_change("exercise-mapping-catalog.deactivate")
    return {"status": "ok", "id": mapping_id, "is_active": False}


@router.get("/export/markdown")
def export_markdown(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)
    # Chamada direta (Python), nao via FastAPI: todo parametro precisa ser
    # passado explicitamente, senao o default e o objeto Query(...) e nao o
    # valor. Bug pre-existente, corrigido aqui.
    rows = list_mappings(
        query="",
        primary_group="",
        amazfit_name="",
        active_only=True,
        limit=1000,
        offset=0,
        x_api_token=x_api_token,
        musclefit_session=musclefit_session,
    )["items"]

    headers = [
        "Exercício",
        "Amazfit",
        "Equipamento",
        "Posição",
        "Movimento",
        "Trajetória",
        "Padrão biomecânico",
        "Articulação principal",
        "Vetor de resistência",
        "Grupo principal",
        "Grupo secundário",
        "Palavras-chave",
        "Descrição",
    ]

    key_map = [
        "exercise_name",
        "amazfit_name",
        "equipment",
        "body_position",
        "movement",
        "trajectory",
        "biomechanical_pattern",
        "primary_joint",
        "resistance_vector",
        "primary_group",
        "secondary_group",
        "keywords",
        "description",
    ]

    output = io.StringIO()
    output.write("# Mapeamentos Amazfit / Zepp\n\n")
    output.write("> Fonte exportada do banco MySQL/Fitness API. Edite pelo front-end quando possível.\n\n")
    output.write("| " + " | ".join(headers) + " |\n")
    output.write("|" + "|".join(["---"] * len(headers)) + "|\n")

    for row in rows:
        values = []
        for key in key_map:
            value = str(row.get(key) or "").replace("|", "\\|").replace("\n", " ")
            values.append(value)
        output.write("| " + " | ".join(values) + " |\n")

    return {"markdown": output.getvalue(), "count": len(rows)}


# ---------------------------------------------------------------------------
# exercise_aliases: apelidos/textos alternativos que resolvem para um
# exercício canônico (N:1). Gestão mínima para permitir que futuras entradas
# do RAG sejam cadastradas sem precisar de uma migração de dados a cada vez
# (ver fitness-platform/mysql/init/001_schema.sql, migração 16).
# ---------------------------------------------------------------------------


@router.get("/{mapping_id}/aliases")
def list_mapping_aliases(
    mapping_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            # exercise_aliases.mapping_id foi renomeado/migrado para
            # exercise_id, com FK real para exercise_catalog (migração
            # 003_exercise_domain_admin.sql/Fase 5). Os ids coincidem com
            # exercise_zepp_mappings para todo mapeamento existente, então
            # esta tela legada (/mapeamentos) continua funcionando sem
            # mudança de comportamento visível.
            cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (mapping_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Mapeamento não encontrado.")

            cursor.execute(
                "SELECT id, exercise_id AS mapping_id, alias, source, created_at FROM exercise_aliases WHERE exercise_id = %s ORDER BY alias",
                (mapping_id,),
            )
            rows = cursor.fetchall()

    return {"items": [_row_jsonable(row) for row in rows]}


@router.post("/{mapping_id}/aliases")
def create_mapping_alias(
    mapping_id: int,
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
            cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (mapping_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Mapeamento não encontrado.")

            try:
                cursor.execute(
                    "INSERT INTO exercise_aliases (exercise_id, alias, source) VALUES (%s, %s, %s)",
                    (mapping_id, alias, source),
                )
            except Exception as exc:
                # So UNIQUE (exercise_id, alias), como na Aurora (029): o mesmo
                # apelido em OUTRO exercicio e aceito (decisao 2026-09-14), entao
                # a colisao so pode ser no MESMO exercicio.
                if not is_duplicate_key_error(exc):
                    raise
                raise HTTPException(status_code=409, detail="Este apelido já está cadastrado para este exercício.") from exc

            alias_id = cursor.lastrowid
            cursor.execute(
                "SELECT id, exercise_id AS mapping_id, alias, source, created_at FROM exercise_aliases WHERE id = %s",
                (alias_id,),
            )
            row = cursor.fetchone()

    reclassify_after_catalog_change("exercise-mapping-catalog.aliases.create")
    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/aliases/{alias_id}")
def delete_mapping_alias(
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
# muscle_groups: tabela canonica de grupo muscular. CRUD simples (mesmo
# padrão de /equipment em gyms.py), registrado neste módulo por conveniência
# — não altera nenhuma rota existente de exercise-mapping-catalog.
# ---------------------------------------------------------------------------

muscle_groups_router = APIRouter(prefix="/muscle-groups", tags=["muscle-groups"])

# --- Agregacao (migracao 055, Fase 4) e porte (deprecated) -----------------
#
# `methodology_parent_group_id` aponta uma FATIA para o grupo que a representa
# (ex.: "Peitoral superior" soma em "Peitoral"); o macro-grupo e o
# `volume_class_id` (057).
#
# `size_class` (porte da 055) saiu do payload e do CRUD no M76 Lote 4a (E12):
# o motor le so `volume_class_id` desde a Fase 6, e aceitar/gravar o porte
# deixava uma segunda classificacao editavel que nada consumia. A COLUNA
# continua no banco (NOT NULL DEFAULT 'unclassified') ate a migracao de
# contract; o INSERT deixa o DEFAULT agir e o PUT nao a toca. Um `size_class`
# vindo de cliente antigo e ignorado (nunca 422), e a resposta nao o traz.

# Sentinela para distinguir "campo ausente do payload" (preserva o valor
# atual) de "campo presente com null" (limpa o valor). `None` sozinho não
# consegue dizer as duas coisas.
_AUSENTE = object()


def _without_size_class(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Linha de `muscle_groups` sem o porte deprecated (E12)."""
    if row is None:
        return None
    return {key: value for key, value in row.items() if key != "size_class"}


def _parse_parent_group_id(item: Dict[str, Any]) -> Any:
    """`_AUSENTE` = não mexer; `None` = desagregar; int = id do grupo pai."""
    if "methodology_parent_group_id" not in item:
        return _AUSENTE
    raw = item.get("methodology_parent_group_id")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"methodology_parent_group_id inválido: {str(raw)!r}. Esperado id numérico ou null.",
        )
    if value <= 0:
        raise HTTPException(
            status_code=422,
            detail="methodology_parent_group_id inválido: id precisa ser positivo.",
        )
    return value


def _parse_volume_class_id(item: Dict[str, Any]) -> Any:
    """Macro-grupo de volume (migração 057, Fase 6). Mesma semântica do pai:
    `_AUSENTE` = não mexer; `None` = sem macro-grupo; int = id do macro-grupo."""
    if "volume_class_id" not in item:
        return _AUSENTE
    raw = item.get("volume_class_id")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"volume_class_id inválido: {str(raw)!r}. Esperado id numérico ou null.",
        )
    if value <= 0:
        raise HTTPException(status_code=422, detail="volume_class_id inválido: id precisa ser positivo.")
    return value


def _validate_volume_class(cursor, volume_class_id: Any) -> None:
    """A FK recusaria o id inexistente, mas como erro de driver (500); aqui
    vira 422 com mensagem."""
    if volume_class_id is _AUSENTE or volume_class_id is None:
        return
    cursor.execute("SELECT id FROM muscle_volume_classes WHERE id = %s", (volume_class_id,))
    if not cursor.fetchone():
        raise HTTPException(
            status_code=422,
            detail=f"volume_class_id inválido: macro-grupo {volume_class_id} não existe.",
        )


def _validate_parent_group(cursor, parent_id: Optional[int], *, child_id: Optional[int]) -> None:
    """Recusa pai inexistente, auto-referência e ciclo.

    A FK garante que o pai existe, mas não que a agregação seja acíclica: o
    banco aceitaria A->B->A de bom grado, e a soma das fatias entraria em
    laço infinito na avaliação. A verificação sobe a cadeia a partir do pai
    proposto; se chegar no próprio grupo, é ciclo. `muscle_groups` tem
    dezenas de linhas — ler a tabela inteira aqui é mais barato que uma CTE
    recursiva e funciona igual no Data API."""
    if parent_id is None:
        return
    if child_id is not None and parent_id == child_id:
        raise HTTPException(
            status_code=422,
            detail="methodology_parent_group_id não pode apontar para o próprio grupo.",
        )

    cursor.execute("SELECT id, methodology_parent_group_id FROM muscle_groups")
    pais = {int(row["id"]): row["methodology_parent_group_id"] for row in (cursor.fetchall() or [])}
    if parent_id not in pais:
        raise HTTPException(
            status_code=422,
            detail=f"methodology_parent_group_id inválido: grupo {parent_id} não existe.",
        )
    if child_id is None:
        return

    visitados = {child_id}
    atual: Optional[int] = parent_id
    while atual is not None:
        if atual in visitados:
            raise HTTPException(
                status_code=422,
                detail="methodology_parent_group_id criaria um ciclo de agregação.",
            )
        visitados.add(atual)
        proximo = pais.get(atual)
        atual = int(proximo) if proximo is not None else None


@muscle_groups_router.get("")
def list_muscle_groups(
    query: str = Query(default=""),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    where = []
    params: List[Any] = []
    if query:
        where.append("name LIKE %s")
        params.append(f"%{query}%")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT id, name, primary_muscles, secondary_muscles, "
                f"methodology_parent_group_id, volume_class_id, created_at, updated_at "
                f"FROM muscle_groups {where_sql} ORDER BY name",
                tuple(params),
            )
            rows = cursor.fetchall()

    return {"items": [_row_jsonable(row) for row in rows]}


@muscle_groups_router.post("")
def create_muscle_group(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    name = _clean_text(item.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="name é obrigatório.")

    parent_id = _parse_parent_group_id(item)
    if parent_id is _AUSENTE:
        parent_id = None
    volume_class_id = _parse_volume_class_id(item)
    if volume_class_id is _AUSENTE:
        volume_class_id = None

    with _connect() as conn:
        with conn.cursor() as cursor:
            # Grupo novo: sem id ainda, então não há auto-referência possível;
            # só se confere que o pai existe.
            _validate_parent_group(cursor, parent_id, child_id=None)
            _validate_volume_class(cursor, volume_class_id)
            # `methodology_parent_group_id` fica FORA do
            # `ON DUPLICATE KEY UPDATE` de propósito. Este POST é um upsert por
            # NOME, e a UI reenvia o formulário inteiro; se as duas colunas
            # entrassem aqui, re-cadastrar um grupo já existente (ou qualquer
            # cliente que não conheça as colunas — a janela inversa, com código
            # velho falando com schema novo) apagaria a classificação feita
            # pelo seed da 055 ou pelo admin. Mudar porte/agregação de um grupo
            # que já existe é o PUT, que identifica a linha por id e enxerga o
            # que está lá. `volume_class_id` (057) fica fora pelo mesmo motivo:
            # re-cadastrar um grupo não pode desassociá-lo do macro-grupo.
            cursor.execute(
                """
                INSERT INTO muscle_groups
                    (name, primary_muscles, secondary_muscles, methodology_parent_group_id, volume_class_id)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    primary_muscles = VALUES(primary_muscles),
                    secondary_muscles = VALUES(secondary_muscles),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    name,
                    _clean_optional(item.get("primary_muscles")),
                    _clean_optional(item.get("secondary_muscles")),
                    parent_id,
                    volume_class_id,
                ),
            )
            cursor.execute("SELECT * FROM muscle_groups WHERE name = %s", (name,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(_without_size_class(row))}


@muscle_groups_router.put("/{muscle_group_id}")
def update_muscle_group(
    muscle_group_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    parent_id = _parse_parent_group_id(item)
    volume_class_id = _parse_volume_class_id(item)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, methodology_parent_group_id FROM muscle_groups WHERE id = %s", (muscle_group_id,)
            )
            current = cursor.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Grupo muscular não encontrado.")

            if parent_id is not _AUSENTE:
                _validate_parent_group(cursor, parent_id, child_id=muscle_group_id)
            _validate_volume_class(cursor, volume_class_id)
            # Mesma regra do `PUT /muscle-volume-classes/{id}/groups`: a fatia
            # é avaliada no bloco do pai e usa o macro-grupo DELE; associar a
            # fatia não teria efeito nenhum no motor. O pai que vale é o do
            # payload, quando vem, senão o que está gravado.
            effective_parent = current["methodology_parent_group_id"] if parent_id is _AUSENTE else parent_id
            if volume_class_id is not _AUSENTE and volume_class_id is not None and effective_parent is not None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{current['name']}: é fatia de outro grupo e usa o macro-grupo dele; "
                        "associe o grupo pai."
                    ),
                )

            # O pai precisa da sentinela porque `null` ali é um pedido
            # legítimo — "desagregar esta fatia" —, que um COALESCE não
            # saberia distinguir de "não mexer". `size_class` não é mais
            # gravado (E12).
            cursor.execute(
                f"""
                UPDATE muscle_groups
                SET name = COALESCE(%s, name),
                    primary_muscles = %s,
                    secondary_muscles = %s,
                    {"methodology_parent_group_id = %s," if parent_id is not _AUSENTE else ""}
                    {"volume_class_id = %s," if volume_class_id is not _AUSENTE else ""}
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    _clean_optional(item.get("name")),
                    _clean_optional(item.get("primary_muscles")),
                    _clean_optional(item.get("secondary_muscles")),
                    *(() if parent_id is _AUSENTE else (parent_id,)),
                    *(() if volume_class_id is _AUSENTE else (volume_class_id,)),
                    muscle_group_id,
                ),
            )
            cursor.execute("SELECT * FROM muscle_groups WHERE id = %s", (muscle_group_id,))
            row = cursor.fetchone()

    # Renomear um grupo (ex.: "Outros" -> nome real) muda quais exercicios do
    # catalogo entram no matching.
    reclassify_after_catalog_change("muscle-groups.update")
    return {"status": "ok", "item": _row_jsonable(_without_size_class(row))}


# ---------------------------------------------------------------------------
# exercise_muscle_contributions: pesos de estímulo/fadiga por par
# exercício×grupamento (Fase 4 da reforma de cálculo de volume). Permite ao
# admin calibrar manualmente os pesos usados por VolumeService em vez de
# depender só do backfill/fallback automático (Fases 1-3, já em main).
# Mesmo padrão de _connect()/_require_identity()/_require_admin_identity()
# e mesmo estilo de resposta JSON do restante deste arquivo.
# ---------------------------------------------------------------------------

muscle_contributions_router = APIRouter(prefix="/muscle-contributions", tags=["muscle-contributions"])

_MUSCLE_CONTRIBUTION_ROLES = {"PRIMARY", "SECONDARY", "STABILIZER"}

# Teto de peso: a spec original limitava stimulus_weight/fatigue_weight a
# [0, 1] (o teto real de PRIMARY já é 1.0), mas usamos 2.0 como teto
# generoso de segurança em vez de 1.0 para não travar calibração fina futura
# (ex.: um exercício com estímulo excepcionalmente alto para um grupamento
# secundário). Ainda assim protege contra valores absurdos/typos.
_MUSCLE_CONTRIBUTION_WEIGHT_MIN = 0.0
_MUSCLE_CONTRIBUTION_WEIGHT_MAX = 2.0


def _require_mapping_exists(cursor, mapping_id: int) -> None:
    # exercise_muscle_contributions.exercise_id agora tem FK real para
    # exercise_catalog (migração 003_exercise_domain_admin.sql/Fase 5), não
    # mais para exercise_zepp_mappings. Os ids coincidem para os mapeamentos
    # existentes (exercise_catalog preserva o id de exercise_zepp_mappings),
    # então esta troca não quebra a tela legada /mapeamentos — só corrige a
    # validação para bater com a FK real.
    cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (mapping_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Mapeamento não encontrado.")


def _require_muscle_group_exists(cursor, muscle_group_id: int) -> None:
    cursor.execute("SELECT id FROM muscle_groups WHERE id = %s", (muscle_group_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Grupo muscular não encontrado.")


def _clean_contribution_weight(value: Any, field: str) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{field} deve ser numérico.")

    if weight < _MUSCLE_CONTRIBUTION_WEIGHT_MIN or weight > _MUSCLE_CONTRIBUTION_WEIGHT_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"{field} deve estar entre {_MUSCLE_CONTRIBUTION_WEIGHT_MIN} e {_MUSCLE_CONTRIBUTION_WEIGHT_MAX}.",
        )

    return weight


@muscle_contributions_router.get("/{mapping_id}")
def list_muscle_contributions(
    mapping_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_mapping_exists(cursor, mapping_id)

            cursor.execute(
                """
                SELECT
                    emc.id,
                    emc.exercise_id,
                    emc.muscle_group_id,
                    mg.name AS muscle_group_name,
                    emc.role,
                    emc.stimulus_weight,
                    emc.fatigue_weight,
                    emc.is_active
                FROM exercise_muscle_contributions emc
                JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
                WHERE emc.exercise_id = %s AND emc.is_active = 1
                ORDER BY FIELD(emc.role, 'PRIMARY', 'SECONDARY', 'STABILIZER'), mg.name
                """,
                (mapping_id,),
            )
            rows = cursor.fetchall()

    return {"items": [_row_jsonable(row) for row in rows]}


@muscle_contributions_router.put("/{mapping_id}/{muscle_group_id}")
def upsert_muscle_contribution(
    mapping_id: int,
    muscle_group_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    role = _clean_text(item.get("role")).upper()
    if role not in _MUSCLE_CONTRIBUTION_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role deve ser um de {sorted(_MUSCLE_CONTRIBUTION_ROLES)}.",
        )

    stimulus_weight = _clean_contribution_weight(item.get("stimulus_weight"), "stimulus_weight")
    fatigue_weight = _clean_contribution_weight(item.get("fatigue_weight"), "fatigue_weight")

    with _connect() as conn:
        with conn.cursor() as cursor:
            _require_mapping_exists(cursor, mapping_id)
            _require_muscle_group_exists(cursor, muscle_group_id)

            # Substituição direta (sem GREATEST/MAX automático): é edição
            # manual explícita do admin, que tem controle direto do valor.
            cursor.execute(
                """
                INSERT INTO exercise_muscle_contributions (
                    exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active
                )
                VALUES (%s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    role = VALUES(role),
                    stimulus_weight = VALUES(stimulus_weight),
                    fatigue_weight = VALUES(fatigue_weight),
                    is_active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (mapping_id, muscle_group_id, role, stimulus_weight, fatigue_weight),
            )

            cursor.execute(
                """
                SELECT
                    emc.id,
                    emc.exercise_id,
                    emc.muscle_group_id,
                    mg.name AS muscle_group_name,
                    emc.role,
                    emc.stimulus_weight,
                    emc.fatigue_weight,
                    emc.is_active
                FROM exercise_muscle_contributions emc
                JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
                WHERE emc.exercise_id = %s AND emc.muscle_group_id = %s
                """,
                (mapping_id, muscle_group_id),
            )
            row = cursor.fetchone()

    # Exercicio do catalogo sem contribuicao PRIMARY fica fora do matching.
    reclassify_after_catalog_change("muscle-contributions.upsert")
    return {"status": "ok", "item": _row_jsonable(row)}


@muscle_contributions_router.delete("/{mapping_id}/{muscle_group_id}")
def deactivate_muscle_contribution(
    mapping_id: int,
    muscle_group_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            # Soft delete: nunca hard delete, mesma política já usada em
            # gym_equipment_sync.py, para não quebrar histórico/relacionamentos.
            cursor.execute(
                """
                UPDATE exercise_muscle_contributions
                SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                WHERE exercise_id = %s AND muscle_group_id = %s
                """,
                (mapping_id, muscle_group_id),
            )

            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Contribuição não encontrada.")

    # Desativar a 1a PRIMARY pode expor outra PRIMARY ativa (ex.: "Outros" ->
    # grupo real) e colocar o exercicio no matching.
    reclassify_after_catalog_change("muscle-contributions.deactivate")
    return {"status": "ok", "mapping_id": mapping_id, "muscle_group_id": muscle_group_id, "is_active": False}
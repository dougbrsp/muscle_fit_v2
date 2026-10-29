"""Relacao GLOBAL equipamento <-> exercicio: `equipment_exercises` e a fonte
CANONICA (PR-05 da refatoracao, gap G18).

Contexto:
- `equipment_exercises` (002_reestruturacao.sql) ja e a tabela lida pelo
  runtime: `gym_equipment_sync.py`, `GET /exercises/{id}` (`equipment_links`)
  e a view `vw_muscle_group_equipment_exercises`.
- `equipment_exercise_mappings` (001_schema.sql) e a tabela LEGADA. Ate este
  PR ela era a escrita primaria, com espelho em `equipment_exercises`. Agora o
  sentido e o inverso: escrita primaria aqui, espelho na legada durante a
  janela de compatibilidade. As rotas `/equipment-exercise-mappings` sairam
  no COD-05 (2026-10-09); o espelho e o DROP da tabela ficam para a fase 2
  (docs/refatoracao-motores/PLANO_20260912.md).
- Proximos PRs vao guardar overrides por par exercicio/equipamento (RPE maximo
  seguro, tecnicas compatíveis) nesta tabela - por isso nao pode haver duas
  fontes concorrentes de escrita.

Espelho legado e a FK de `equipment_exercise_mappings.mapping_id`:
O caminho esperado e essa FK referenciar `exercise_catalog(id)` (migracao
`023_mapping_fks_exercise_catalog.py`, que reaponta a `fk_eem_mapping` do
001_schema.sql; a Aurora documentada ja aponta para o catalogo) - entao todo
exercicio aceito aqui (que precisa existir em `exercise_catalog`) e espelhado.

Protecao mantida para bancos sem a 023: com a FK ainda em
`exercise_zepp_mappings(id)`, um exercicio criado so no catalogo canonico
violaria a FK. Decisao deterministica: antes de espelhar, descobre (via
`information_schema`) para qual tabela a FK aponta e
checa se o id existe nela. Se nao existir (ou a tabela legada nao existir),
o espelho e PULADO com log de warning estruturado - a escrita canonica nunca
falha por causa da tabela legada. Desativacao sempre espelha (UPDATE nao
viola FK). So o estado CANONICO dessa deteccao e cacheado, e por schema (ver
`_canonical_legacy_fk_schemas`): "a 023 ainda nao rodou" e transitorio e nunca
e memorizado.

`exercise_catalog.id` preserva o id legado de `exercise_zepp_mappings.id`, entao
`mapping_id == exercise_id` para o mesmo exercicio.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query

from . import security
from .db import get_conn
from .db_helpers import connect_or_503, DB_UNAVAILABLE_RDS
from .security import resolve_identity_from_credentials as _require_identity
from .gym_equipment_sync import resolve_fk_referenced_table, sync_gym_equipment_for_equipment
from .load_conventions import (
    current_schema,
    has_load_convention_column,
    load_convention_select_expr,
    normalize_load_convention,
)

router = APIRouter(tags=["equipment-exercises"])

logger = logging.getLogger(__name__)

PUBLIC_ASSETS_DOMAIN = "app.musclefit.com.br"
NOTES_MAX_LENGTH = 500  # equipment_exercises.notes VARCHAR(500)

MIRROR_OK = "mirrored"
MIRROR_SKIPPED_NO_LEGACY_TABLE = "skipped_no_legacy_table"
MIRROR_SKIPPED_FK_TARGET_MISSING = "skipped_fk_target_missing"
MIRROR_SKIPPED_ERROR = "skipped_error"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")

# Tabela canonica que a FK legada deve referenciar depois da migracao 023.
LEGACY_TABLE = "equipment_exercise_mappings"
CANONICAL_FK_TARGET = "exercise_catalog"

# Cache SO DO ESTADO CANONICO (tabela legada presente E FK ja reapontada para
# `exercise_catalog` pela migracao 023), chaveado por SCHEMA - mesma regra que
# o PR #182 fixou em `load_conventions.py` (M18).
#
# O que havia antes: uma tupla unica por PROCESSO que guardava tambem
# `legacy_exists=False` e o alvo LEGADO da FK. Os dois sao estados
# TRANSITORIOS - significam "a 023 ainda nao rodou neste banco" -, e memoriza-
# los envenenava o container que atendesse uma requisicao na janela entre o
# deploy e a migracao: ele passava a PULAR o espelho legado ate reciclar,
# mesmo depois de a migracao ter corrigido a FK. Aqui o efeito e atenuado (o
# espelho e best-effort e so vira warning, nunca derruba a escrita canonica),
# mas o defeito e o mesmo e o custo de corrigir e uma consulta a
# `information_schema` enquanto o estado nao for o canonico.
#
# A chave por schema tambem elimina o vazamento entre arquivos da suite de
# integracao, que criam um schema por arquivo no MESMO processo (a armadilha
# que fazia `test_migration_023_mapping_fks.py` falhar so dentro da suite).
_canonical_legacy_fk_schemas: set[str] = set()


# ---------------------------------------------------------------------------
# Logica pura (sem banco) - coberta por tests/unit/test_equipment_exercises.py
# ---------------------------------------------------------------------------


def parse_positive_id(value: Any, field: str) -> int:
    """Converte `value` em id inteiro positivo ou levanta ValueError com
    mensagem em PT-BR. Aceita int ou string numerica; rejeita bool, float
    fracionario, vazio e <= 0."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field} é obrigatório.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{field} deve ser um inteiro positivo.")
    if parsed <= 0:
        raise ValueError(f"{field} deve ser um inteiro positivo.")
    return parsed


def normalize_notes(value: Any) -> Optional[str]:
    """`notes` opcional: trim, string vazia vira NULL, limite do VARCHAR(500)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("notes deve ser texto.")
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > NOTES_MAX_LENGTH:
        raise ValueError(f"notes deve ter no máximo {NOTES_MAX_LENGTH} caracteres.")
    return stripped


def normalize_load_convention_field(item: Mapping[str, Any]) -> Tuple[Optional[str], bool]:
    """`load_convention` opcional do corpo (migracao 045, plano §8.1).

    Retorna `(valor, enviado)`:
    - campo AUSENTE -> `(None, False)`: preserva o que ja esta gravado;
    - `null`/`""` -> `(None, True)`: limpa a coluna, e o par volta a valer
      como `total` (NULL = total, D12);
    - valor do vocabulario -> `(valor canonico, True)`;
    - qualquer outra coisa -> `ValueError` (o endpoint converte em 400).

    A validacao e a MESMA funcao que o dashboard usa para comparar exposicoes
    (`load_conventions.normalize_load_convention`, reexportada por
    `intelligence/dashboard/comparability.py`): um unico vocabulario, sem
    espelho para dessincronizar.
    """
    if "load_convention" not in item:
        return None, False
    raw = item.get("load_convention")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, True
    if not isinstance(raw, str):
        raise ValueError("load_convention deve ser texto.")
    try:
        return normalize_load_convention(raw).value, True
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def normalize_link_payload(
    item: Mapping[str, Any], exercise_field: str = "exercise_id"
) -> Tuple[int, int, Optional[str], bool]:
    """Valida o corpo de criacao. `exercise_field` permite ao endpoint legado
    reusar a mesma validacao com `mapping_id`. Retorna
    (equipment_id, exercise_id, notes, notes_provided) - `notes_provided`
    distingue "nao enviado" (preserva notes existente na reativacao) de
    "enviado vazio" (limpa)."""
    if not isinstance(item, Mapping):
        raise ValueError("Corpo da requisição deve ser um objeto JSON.")
    equipment_id = parse_positive_id(item.get("equipment_id"), "equipment_id")
    exercise_id = parse_positive_id(item.get(exercise_field), exercise_field)
    notes_provided = "notes" in item
    notes = normalize_notes(item.get("notes")) if notes_provided else None
    return equipment_id, exercise_id, notes, notes_provided


def decide_mirror(legacy_table_exists: bool, referenced_table: Optional[str], fk_target_exists: bool) -> str:
    """Decide se o espelho de CRIACAO em `equipment_exercise_mappings` pode ser
    feito sem violar a FK legada. Deterministico, sem I/O."""
    if not legacy_table_exists:
        return MIRROR_SKIPPED_NO_LEGACY_TABLE
    if referenced_table is None:
        # Sem FK em mapping_id (ex.: ambiente onde a constraint foi removida).
        return MIRROR_OK
    return MIRROR_OK if fk_target_exists else MIRROR_SKIPPED_FK_TARGET_MISSING


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


def serialize_link_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Normaliza uma linha (enriquecida ou crua) de `equipment_exercises` para
    JSON: flags TINYINT -> bool, Decimal/datetime -> tipos JSON."""
    result = {key: _jsonable(value) for key, value in row.items()}
    for flag in ("is_active", "is_preferred", "has_photo"):
        if flag in result and result[flag] is not None:
            result[flag] = bool(result[flag])
    return result


# ---------------------------------------------------------------------------
# Infra comum
# ---------------------------------------------------------------------------


def _connect():
    return connect_or_503(get_conn, DB_UNAVAILABLE_RDS)


def _require_admin_identity(x_api_token: Optional[str], musclefit_session: Optional[str]) -> security.Identity:
    identity = _require_identity(x_api_token, musclefit_session)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return identity


def _resolve_legacy_fk(cursor: Any) -> Tuple[bool, Optional[str]]:
    """`(tabela legada existe?, tabela referenciada pela FK dela)` NESTE schema.

    Cacheia SO o estado canonico (existe + FK em `exercise_catalog`) e so para
    o schema onde ele foi observado - ver `_canonical_legacy_fk_schemas`.
    Qualquer outra resposta e reconsultada na chamada seguinte."""
    schema = current_schema(cursor)
    if schema in _canonical_legacy_fk_schemas:
        return (True, CANONICAL_FK_TARGET)

    cursor.execute(
        """
        SELECT COUNT(*) AS n FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (LEGACY_TABLE,),
    )
    legacy_exists = int(cursor.fetchone()["n"]) > 0

    referenced: Optional[str] = None
    if legacy_exists:
        referenced = resolve_fk_referenced_table(cursor, LEGACY_TABLE, "mapping_id")

    if legacy_exists and referenced == CANONICAL_FK_TARGET:
        _canonical_legacy_fk_schemas.add(schema)
    return (legacy_exists, referenced)


def reset_legacy_fk_cache() -> None:
    """Esquece a deteccao de TODOS os schemas. Como so o estado canonico e
    cacheado, isto so importa no caminho inverso - um teste que DESFAZ a 023."""
    _canonical_legacy_fk_schemas.clear()


def _mirror_create_legacy(cursor: Any, equipment_id: int, exercise_id: int) -> str:
    """Espelho TEMPORARIO (janela de compatibilidade) da criacao/reativacao na
    tabela legada. Nunca levanta: qualquer impedimento vira warning."""
    try:
        legacy_exists, referenced = _resolve_legacy_fk(cursor)
        fk_target_exists = False
        if legacy_exists and referenced is not None:
            if not _SAFE_IDENTIFIER.match(referenced):
                raise ValueError(f"nome de tabela referenciada inesperado: {referenced!r}")
            cursor.execute(f"SELECT 1 AS ok FROM `{referenced}` WHERE id = %s", (exercise_id,))
            fk_target_exists = cursor.fetchone() is not None

        decision = decide_mirror(legacy_exists, referenced, fk_target_exists)
        if decision == MIRROR_OK:
            cursor.execute(
                """
                INSERT INTO equipment_exercise_mappings (equipment_id, mapping_id)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE is_active = 1
                """,
                (equipment_id, exercise_id),
            )
        else:
            logger.warning(
                "equipment_exercises.legacy_mirror_skipped",
                extra={
                    "event": "equipment_exercises.legacy_mirror_skipped",
                    "reason": decision,
                    "equipment_id": equipment_id,
                    "exercise_id": exercise_id,
                    "legacy_fk_referenced_table": referenced,
                    # caminho esperado: FK em exercise_catalog (migracao 023); pular indica banco sem a 023
                    "expected_fk_referenced_table": "exercise_catalog",
                },
            )
        return decision
    except Exception as exc:  # noqa: BLE001 - espelho legado nunca derruba a escrita canonica
        logger.warning(
            "equipment_exercises.legacy_mirror_failed",
            extra={
                "event": "equipment_exercises.legacy_mirror_failed",
                "reason": MIRROR_SKIPPED_ERROR,
                "equipment_id": equipment_id,
                "exercise_id": exercise_id,
                "error": str(exc),
            },
        )
        return MIRROR_SKIPPED_ERROR


def _mirror_deactivate_legacy(cursor: Any, equipment_id: int, exercise_id: int) -> None:
    try:
        legacy_exists, _ = _resolve_legacy_fk(cursor)
        if legacy_exists:
            cursor.execute(
                "UPDATE equipment_exercise_mappings SET is_active = 0 WHERE equipment_id = %s AND mapping_id = %s",
                (equipment_id, exercise_id),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "equipment_exercises.legacy_mirror_failed",
            extra={
                "event": "equipment_exercises.legacy_mirror_failed",
                "reason": MIRROR_SKIPPED_ERROR,
                "operation": "deactivate",
                "equipment_id": equipment_id,
                "exercise_id": exercise_id,
                "error": str(exc),
            },
        )


# ---------------------------------------------------------------------------
# Servico (unica implementacao de escrita; reusada pelos endpoints legados)
# ---------------------------------------------------------------------------


# Colunas devolvidas pelos endpoints de escrita. `load_convention` (migracao
# 045) entra como coluna quando ela existe e como `NULL` quando ainda nao -
# o campo esta SEMPRE na resposta, e `NULL` ja significa `total` (D12).
#
# Por que tolerar a ausencia: o codigo pode ser publicado antes de a 045 rodar
# (ordem de deploy) e bancos montados num estado de schema antigo continuam
# tendo que gravar vinculos - e o que
# `tests/integration/test_migration_023_mapping_fks.py` exercita.
def _link_columns(cursor: Any) -> str:
    convention = load_convention_select_expr(cursor, alias="equipment_exercises")
    return (
        "id, equipment_id, exercise_id, is_preferred, is_active, notes, "
        f"{convention} AS load_convention, created_at, updated_at"
    )


def upsert_equipment_exercise(
    conn: Any,
    equipment_id: int,
    exercise_id: int,
    notes: Optional[str] = None,
    notes_provided: bool = False,
    load_convention: Optional[str] = None,
    load_convention_provided: bool = False,
) -> Dict[str, Any]:
    """Cria ou reativa o vinculo (idempotente pela UK
    `(equipment_id, exercise_id)`), espelha na tabela legada e recalcula
    `gym_equipment` das academias que possuem o equipamento. Retorna a linha
    crua de `equipment_exercises` + `legacy_mirror` (resultado do espelho).
    Levanta HTTPException 404 se equipamento/exercicio nao existirem.

    `notes` e `load_convention` seguem a MESMA regra: campo nao enviado
    (`*_provided = False`) preserva o valor gravado; enviado vazio limpa."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM equipment WHERE id = %s", (equipment_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="equipment_id não encontrado no catálogo.")

        cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s AND is_active = 1", (exercise_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="exercise_id não encontrado no catálogo canônico.")

        # INSERT montado a partir dos campos REALMENTE enviados: o que nao foi
        # enviado nao aparece no ON DUPLICATE KEY UPDATE e por isso nao pode
        # ser apagado por engano numa reativacao.
        colunas = ["equipment_id", "exercise_id", "is_active"]
        valores: List[Any] = [equipment_id, exercise_id, 1]
        updates = ["is_active = 1"]
        if notes_provided:
            colunas.append("notes")
            valores.append(notes)
            updates.append("notes = VALUES(notes)")
        if load_convention_provided:
            if not has_load_convention_column(cursor):
                raise HTTPException(
                    status_code=503,
                    detail="Convenção de carga indisponível: migração 045 ainda não aplicada neste banco.",
                )
            colunas.append("load_convention")
            valores.append(load_convention)
            updates.append("load_convention = VALUES(load_convention)")

        placeholders = ", ".join(["%s"] * len(colunas))
        cursor.execute(
            f"INSERT INTO equipment_exercises ({', '.join(colunas)}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {', '.join(updates)}",
            tuple(valores),
        )

        cursor.execute(
            f"""
            SELECT {_link_columns(cursor)}
            FROM equipment_exercises WHERE equipment_id = %s AND exercise_id = %s
            """,
            (equipment_id, exercise_id),
        )
        row = cursor.fetchone()

        mirror = _mirror_create_legacy(cursor, equipment_id, exercise_id)

    gym_sync_warnings = sync_gym_equipment_for_equipment(conn, equipment_id)

    result = serialize_link_row(row)
    result["legacy_mirror"] = mirror
    result["gym_sync_warnings"] = gym_sync_warnings
    return result


def deactivate_equipment_exercise_pair(
    conn: Any, equipment_id: int, exercise_id: int
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Soft-delete (`is_active = 0`) pelo par. Idempotente. Retorna
    (linha de `equipment_exercises` apos a desativacao ou None se o par nao
    existir na tabela canonica, avisos do sync de `gym_equipment`). O espelho
    legado e desativado e o sync roda mesmo sem linha canonica, para nao
    deixar resquicio de dados legados fora do backfill."""
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE equipment_exercises SET is_active = 0 WHERE equipment_id = %s AND exercise_id = %s",
            (equipment_id, exercise_id),
        )
        cursor.execute(
            f"""
            SELECT {_link_columns(cursor)}
            FROM equipment_exercises WHERE equipment_id = %s AND exercise_id = %s
            """,
            (equipment_id, exercise_id),
        )
        row = cursor.fetchone()
        _mirror_deactivate_legacy(cursor, equipment_id, exercise_id)

    gym_sync_warnings = sync_gym_equipment_for_equipment(conn, equipment_id)
    return (serialize_link_row(row) if row else None), gym_sync_warnings


def deactivate_equipment_exercise(conn: Any, link_id: int) -> Dict[str, Any]:
    """Soft-delete pelo id de `equipment_exercises`. 404 se nao existir.
    Retorna a linha desativada + `gym_sync_warnings`."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT equipment_id, exercise_id FROM equipment_exercises WHERE id = %s", (link_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Associação não encontrada.")
    deactivated, gym_sync_warnings = deactivate_equipment_exercise_pair(
        conn, int(row["equipment_id"]), int(row["exercise_id"])
    )
    result = deactivated or {"id": link_id, "is_active": False}
    result["gym_sync_warnings"] = gym_sync_warnings
    return result


# ---------------------------------------------------------------------------
# Endpoints canonicos (api-core; top-level, NAO sob /exercises/* - que no API
# Gateway e roteado parcialmente para a Lambda engines)
# ---------------------------------------------------------------------------


@router.get("/equipment-exercises")
def list_equipment_exercises(
    equipment_id: Optional[int] = Query(default=None),
    exercise_id: Optional[int] = Query(default=None),
    primary_group: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    where: List[str] = []
    params: List[Any] = []
    if active_only:
        where.append("eqex.is_active = 1")
    if equipment_id is not None:
        where.append("eqex.equipment_id = %s")
        params.append(equipment_id)
    if exercise_id is not None:
        where.append("eqex.exercise_id = %s")
        params.append(exercise_id)
    if primary_group:
        where.append("pg.name = %s")
        params.append(primary_group)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    # Mesmo enriquecimento do antigo GET /equipment-exercise-mappings (desempate
    # PRIMARY/SECONDARY por emc.id ASC), mas lendo a tabela canonica.
    # `{load_convention}` e resolvido com o cursor aberto (a coluna pode nao
    # existir num banco anterior a 045; ver `_link_columns`).
    sql_template = f"""
        SELECT
            eqex.id, eqex.equipment_id, eq.name AS equipment_name, (eq.photo_path IS NOT NULL) AS has_photo,
            (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path) ELSE NULL END) AS photo_url,
            eqex.exercise_id,
            ec.name AS exercise_name,
            ec.default_measure AS default_measure,
            (
                SELECT exm.external_name
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ) AS exercise_amazfit_name,
            pg.name AS primary_group, sg.name AS secondary_group,
            eqex.is_preferred, eqex.is_active, eqex.notes, {{load_convention}} AS load_convention, eqex.created_at, eqex.updated_at
        FROM equipment_exercises eqex
        JOIN equipment eq ON eq.id = eqex.equipment_id
        JOIN exercise_catalog ec ON ec.id = eqex.exercise_id
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
        {where_sql}
        ORDER BY eq.name, pg.name, ec.name
    """

    with _connect() as conn:
        with conn.cursor() as cursor:
            sql = sql_template.format(load_convention=load_convention_select_expr(cursor))
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()

    return {"items": [serialize_link_row(row) for row in rows]}


@router.post("/equipment-exercises")
def create_equipment_exercise(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)
    try:
        equipment_id, exercise_id, notes, notes_provided = normalize_link_payload(item)
        convention, convention_provided = normalize_load_convention_field(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with _connect() as conn:
        row = upsert_equipment_exercise(
            conn,
            equipment_id,
            exercise_id,
            notes,
            notes_provided,
            convention,
            convention_provided,
        )

    gym_sync_warnings = row.pop("gym_sync_warnings", [])
    return {"status": "ok", "item": row, "gym_sync_warnings": gym_sync_warnings}


@router.put("/equipment-exercises/{link_id}")
def update_equipment_exercise(
    link_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Edicao dos atributos do par JA existente (`notes`, `load_convention`).

    Pelo id de `equipment_exercises` porque e o que a tela tem em maos na
    linha da tabela. O par (`equipment_id`/`exercise_id`) NAO muda aqui: trocar
    o par seria outra associacao, criada pelo POST. Campo ausente preserva o
    valor gravado; enviado vazio limpa. Mesma permissao do POST/DELETE (admin).
    """
    _require_admin_identity(x_api_token, musclefit_session)
    if not isinstance(item, Mapping):
        raise HTTPException(status_code=400, detail="Corpo da requisição deve ser um objeto JSON.")

    try:
        notes_provided = "notes" in item
        notes = normalize_notes(item.get("notes")) if notes_provided else None
        convention, convention_provided = normalize_load_convention_field(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not notes_provided and not convention_provided:
        raise HTTPException(status_code=400, detail="Informe ao menos um campo editável (notes, load_convention).")

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT equipment_id, exercise_id FROM equipment_exercises WHERE id = %s",
                (link_id,),
            )
            existente = cursor.fetchone()
            if not existente:
                raise HTTPException(status_code=404, detail="Associação não encontrada.")

            if convention_provided and not has_load_convention_column(cursor):
                raise HTTPException(
                    status_code=503,
                    detail="Convenção de carga indisponível: migração 045 ainda não aplicada neste banco.",
                )

            sets: List[str] = []
            params: List[Any] = []
            if notes_provided:
                sets.append("notes = %s")
                params.append(notes)
            if convention_provided:
                sets.append("load_convention = %s")
                params.append(convention)
            params.append(link_id)
            cursor.execute(f"UPDATE equipment_exercises SET {', '.join(sets)} WHERE id = %s", tuple(params))

            cursor.execute(f"SELECT {_link_columns(cursor)} FROM equipment_exercises WHERE id = %s", (link_id,))
            row = cursor.fetchone()

    return {"status": "ok", "item": serialize_link_row(row)}


@router.delete("/equipment-exercises/{link_id}")
def delete_equipment_exercise(
    link_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        result = deactivate_equipment_exercise(conn, link_id)

    return {
        "status": "ok",
        "id": link_id,
        "is_active": False,
        "gym_sync_warnings": result["gym_sync_warnings"],
    }

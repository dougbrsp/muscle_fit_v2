"""Perfis de resistencia por par equipamento x exercicio (issue #288, fatia 1 parte A).

Vocabulario, validacao, transicao de status, confiabilidade e assinatura sao do dono
`domain/resistance_profile.py`; aqui so se le, grava e serializa (docs/motores/FONTES_DA_VERDADE.md).

    GET  /resistance-profiles/vocabulary         export do dono
    GET  /resistance-profiles/coverage           pares ativos x perfis
    GET  /resistance-profiles                    lista paginada (filtros status, confidence, equipment_id,
                                                 exercise_id, pending); par sem perfil = desconhecido
    GET  /resistance-profiles/{equipment_exercise_id}
    GET  /resistance-profiles/{equipment_exercise_id}/history
    PUT  /resistance-profiles/{equipment_exercise_id}   admin; substituicao completa (status obrigatorio)
    POST /resistance-profiles/suggest                    admin; job assincrono de sugestao por IA (202 `{job_id}`)

A sugestao por IA (`resistance_profile_ai.py`, Lambda `musclefit-workout-ai`) e de ate `SUGGEST_PAIRS_PER_JOB` pares
por job; a rota aceita ate `SUGGEST_MAX_PAIRS_PER_REQUEST` pares e os divide em varios jobs (`job_ids`; `job_id` e o
primeiro; `failed_batches` conta blocos nao despachados). Sem cota de IA por usuario (admin do catalogo); a IA so cria `suggested`. Sem ids, escolhe pares pendentes
(sem perfil primeiro, depois os sugeridos mais antigos); sem pares, nao cria job (`job_id` nulo).

Permissoes como `equipment_exercises.py`: leitura `exercicios.catalogo`/`ver`, escrita `admin.catalogos`/`editar`.
Sem as tabelas (migracao 126): 503 citando a 126. Erro tipado do dono: 422 com `code` e `field`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from . import async_jobs
from .access import require_permission
from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503, jsonable
from .domain import resistance_profile as rp
from .request_models import ResistanceProfileIn, ResistanceSuggestIn
from .domain.resistance_profile import SUGGEST_JOB_TYPE
from .security import Identity

logger = logging.getLogger("musclefit.resistance_profiles")
router = APIRouter(prefix="/resistance-profiles", tags=["resistance-profiles"])

PROFILES = "equipment_exercise_resistance_profiles"
HISTORY = "equipment_exercise_resistance_profile_history"
MIGRATION = "126"
LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 200
HISTORY_MAX_LIMIT = 100
_PENDING = tuple(sorted(rp.PENDING_STATUSES))


def _is_deadlock_error(exc: Exception) -> bool:
    """MySQL 1213 (pymysql: '(1213, ...)'; Data API: texto da mensagem), no mesmo estilo de is_duplicate_key_error."""
    text = str(exc)
    return "Deadlock found" in text or "(1213," in text

_COMPONENTS = ("source", "mechanism", "trajectory", "profile", "line", "direction_changes")
_PROFILE_COLUMNS = (
    "p.id AS profile_id, p.source, p.mechanism, p.trajectory, p.profile, p.line, p.direction_changes, p.flags_json, "
    "p.confidence, p.status, p.origin, p.rationale, p.evidence, p.vocabulary_version, p.model_version, "
    "p.reviewed_by, p.reviewed_at, p.updated_at"
)
_PAIR_FROM = (
    "FROM equipment_exercises eqex JOIN equipment eq ON eq.id = eqex.equipment_id "
    f"JOIN exercise_catalog ec ON ec.id = eqex.exercise_id LEFT JOIN {PROFILES} p ON p.equipment_exercise_id = eqex.id "
    "WHERE eqex.is_active = 1"
)
_PAIR_SELECT = (
    "SELECT eqex.id AS equipment_exercise_id, eqex.equipment_id, eq.name AS equipment_name, eqex.exercise_id, "
    f"ec.name AS exercise_name, {_PROFILE_COLUMNS} "
)
PAIR_SQL = f"{_PAIR_SELECT}{_PAIR_FROM} AND eqex.id = %s"
LIST_SQL = f"{_PAIR_SELECT}{_PAIR_FROM}{{filters}} ORDER BY eq.name, ec.name, eqex.id LIMIT %s OFFSET %s"
COUNT_SQL = f"SELECT COUNT(*) AS total {_PAIR_FROM}{{filters}}"
COVERAGE_SQL = (
    "SELECT p.status, p.source, p.mechanism, p.trajectory, p.profile, p.line, p.direction_changes, COUNT(*) AS n "
    f"{_PAIR_FROM} GROUP BY p.status, p.source, p.mechanism, p.trajectory, p.profile, p.line, p.direction_changes"
)
PROFILE_FOR_UPDATE_SQL = (
    f"SELECT id, {', '.join(_COMPONENTS)}, flags_json, confidence, status, origin, rationale, evidence, "
    f"vocabulary_version FROM {PROFILES} WHERE equipment_exercise_id = %s FOR UPDATE"
)
INSERT_SQL = (
    f"INSERT INTO {PROFILES} (equipment_exercise_id, {', '.join(_COMPONENTS)}, flags_json, confidence, status, "
    "origin, rationale, evidence, vocabulary_version, created_by, reviewed_by, reviewed_at) VALUES "
    f"(%s, {', '.join(['%s'] * len(_COMPONENTS))}, %s, %s, %s, %s, %s, %s, %s, %s, %s, {{reviewed_at}})"
)
UPDATE_SQL = (
    f"UPDATE {PROFILES} SET {', '.join(c + ' = %s' for c in _COMPONENTS)}, flags_json = %s, confidence = %s, "
    "status = %s, origin = %s, rationale = %s, evidence = %s, vocabulary_version = %s, reviewed_by = %s, "
    "reviewed_at = {reviewed_at}, model_version = NULL WHERE id = %s"  # admin gravou: deixa de ser da IA
)
HISTORY_INSERT_SQL = (
    f"INSERT INTO {HISTORY} (profile_id, before_json, after_json, actor_user_id, origin, reason) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
HISTORY_LIST_SQL = (
    "SELECT id, before_json, after_json, actor_user_id, origin, reason, created_at "
    f"FROM {HISTORY} WHERE profile_id = %s ORDER BY id DESC LIMIT %s OFFSET %s"
)
SUGGEST_PENDING_SQL = (
    f"SELECT eqex.id AS equipment_exercise_id {_PAIR_FROM}{{filters}} "
    "ORDER BY (p.id IS NOT NULL), p.updated_at, eqex.id LIMIT %s"
)
PROFILE_ID_SQL = f"SELECT id FROM {PROFILES} WHERE equipment_exercise_id = %s"
TABLES_SQL = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
    "AND TABLE_NAME IN (%s, %s)"
)
_REVIEWED_AT_SET = "CURRENT_TIMESTAMP"
_REVIEWED_AT_NULL = "NULL"

_tables_present = False  # cache so do positivo (tabela criada pela migracao nao some)


def reset_tables_cache() -> None:
    """So para testes."""
    global _tables_present
    _tables_present = False


def _require_tables(cur: Any) -> None:
    global _tables_present
    if _tables_present:
        return
    cur.execute(TABLES_SQL, (PROFILES, HISTORY))
    if int((cur.fetchone() or {}).get("n") or 0) < 2:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "resistance_profile_tables_missing",
                "migration": MIGRATION,
                "message": f"Perfis de resistência indisponíveis: as tabelas `{PROFILES}` e `{HISTORY}` "
                f"(migração {MIGRATION}) ainda não existem neste banco.",
            },
        )
    _tables_present = True


def _connect():
    return connect_or_503(get_conn)


def _typed_error(exc: rp.ResistanceProfileError) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": exc.code, "code": exc.code, "field": exc.field})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "pair_not_found", "message": "Par equipamento x exercício não encontrado."})


def _json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value) if isinstance(value, str) else value


def _flags(row: dict[str, Any]) -> list[str]:
    bruto = _json(row.get("flags_json"))
    return sorted(bruto) if isinstance(bruto, list) else []


def _signature(components: dict[str, Any]) -> dict[str, Optional[str]]:
    try:
        return {"signature": rp.signature(components), "signature_label": rp.signature_label(components)}
    except rp.ResistanceProfileError:  # codigo gravado fora do vocabulario atual: sem assinatura, nunca 500
        return {"signature": None, "signature_label": None}


def _item(row: dict[str, Any]) -> dict[str, Any]:
    com_perfil = row.get("profile_id") is not None
    componentes = {c: (row.get(c) if com_perfil else None) or rp.UNKNOWN for c in _COMPONENTS}
    status = row.get("status") if com_perfil else None
    return {
        "equipment_exercise_id": int(row["equipment_exercise_id"]),
        "equipment_id": int(row["equipment_id"]),
        "equipment_name": row["equipment_name"],
        "exercise_id": int(row["exercise_id"]),
        "exercise_name": row["exercise_name"],
        "has_profile": com_perfil,
        **componentes,
        "flags": _flags(row) if com_perfil else [],
        "confidence": row.get("confidence") if com_perfil else None,
        "status": status,
        "origin": row.get("origin") if com_perfil else None,
        "rationale": row.get("rationale") if com_perfil else None,
        "evidence": row.get("evidence") if com_perfil else None,
        "vocabulary_version": row.get("vocabulary_version") if com_perfil else None,
        "model_version": row.get("model_version") if com_perfil else None,
        "reviewed_by": row.get("reviewed_by") if com_perfil else None,
        "reviewed_at": jsonable(row.get("reviewed_at")) if com_perfil else None,
        "updated_at": jsonable(row.get("updated_at")) if com_perfil else None,
        "reliable": bool(status and rp.is_reliable(status)),
        **_signature(componentes),
    }


def _snapshot(v: rp.ValidatedProfile, rationale: Optional[str], evidence: Optional[str]) -> dict[str, Any]:
    return {
        **{c: getattr(v, c) for c in _COMPONENTS},
        "flags": sorted(v.flags),
        "confidence": v.confidence,
        "status": v.status,
        "origin": v.origin,
        "rationale": rationale,
        "evidence": evidence,
        "vocabulary_version": rp.RESISTANCE_VOCABULARY_VERSION,
    }


def _existing_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    keys = (*_COMPONENTS, "confidence", "status", "origin", "rationale", "evidence", "vocabulary_version")
    return {**{k: row.get(k) for k in keys}, "flags": _flags(row)}


def _where(status: Optional[str], confidence: Optional[str], equipment_id: Optional[int], exercise_id: Optional[int],
           pending: bool) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        if status not in rp.REVIEW_STATUSES:
            raise _typed_error(rp.ResistanceProfileError("invalid_code", "status"))
        clauses.append("p.status = %s")
        params.append(status)
    if confidence is not None:
        if confidence not in rp.CONFIDENCES:
            raise _typed_error(rp.ResistanceProfileError("invalid_code", "confidence"))
        clauses.append("p.confidence = %s")
        params.append(confidence)
    if equipment_id is not None:
        clauses.append("eqex.equipment_id = %s")
        params.append(equipment_id)
    if exercise_id is not None:
        clauses.append("eqex.exercise_id = %s")
        params.append(exercise_id)
    if pending:
        clauses.append(f"(p.id IS NULL OR p.status IN ({', '.join(['%s'] * len(_PENDING))}))")
        params.extend(_PENDING)
    return "".join(f" AND {c}" for c in clauses), params


# ---------------------------------------------------------------------------
# Rotas (as estaticas antes de `/{equipment_exercise_id}`)
# ---------------------------------------------------------------------------


@router.post("/suggest", status_code=202)
def suggest_profiles(
    payload: ResistanceSuggestIn,
    identity: Identity = Depends(require_permission("admin.catalogos", "editar")),
):
    ids = list(dict.fromkeys(payload.equipment_exercise_ids or []))
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            if not ids:
                filtros, params = _where(None, None, None, None, True)
                cur.execute(SUGGEST_PENDING_SQL.format(filters=filtros), (*params, payload.limit))
                ids = [int(r["equipment_exercise_id"]) for r in cur.fetchall() or []]
    if not ids:
        return {"job_ids": [], "job_id": None, "pairs": 0, "failed_batches": 0}
    solicitante = int(identity.user_id)
    por_job = rp.SUGGEST_PAIRS_PER_JOB
    job_ids: list = []
    falhas = 0
    for i in range(0, len(ids), por_job):
        bloco = ids[i : i + por_job]
        job_id = None
        try:
            job_id = async_jobs.create_job(
                SUGGEST_JOB_TYPE,
                request={"payload": {"equipment_exercise_ids": bloco}, "user_id": solicitante},
                owner_user_id=solicitante,
            )
            async_jobs.invoke_async(
                "WORKOUT_AI_FUNCTION_NAME",
                {
                    "job_id": job_id,
                    "job_type": SUGGEST_JOB_TYPE,
                    "payload": {"equipment_exercise_ids": bloco},
                    "user_id": solicitante,
                },
            )
            job_ids.append(job_id)
        except Exception:
            falhas += 1
            logger.exception("Falha ao despachar um bloco da sugestao de perfis de resistencia por IA")
            if job_id is not None:  # job criado e nao despachado: nao deixa orfao em pending
                try:
                    async_jobs.mark_failed(job_id, "dispatch_failed")
                except Exception:
                    logger.exception("Falha ao marcar como falho o job orfao %s", job_id)
    if not job_ids:
        raise HTTPException(
            status_code=503,
            detail={"error": "suggest_dispatch_failed", "message": "Não foi possível iniciar a sugestão por IA."},
        )
    return {"job_ids": job_ids, "job_id": job_ids[0], "pairs": len(ids), "failed_batches": falhas}


@router.get("/vocabulary", dependencies=[Depends(require_permission("exercicios.catalogo", "ver"))])
def get_vocabulary():
    return rp.export_vocabulary()


@router.get("/coverage", dependencies=[Depends(require_permission("exercicios.catalogo", "ver"))])
def get_coverage():
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            cur.execute(COVERAGE_SQL)
            linhas = cur.fetchall() or []
    por_status = {s: 0 for s in rp.REVIEW_STATUSES}
    pares = com_perfil = desconhecidos = 0
    for r in linhas:
        n = int(r["n"])
        pares += n
        if r["status"] is not None:
            com_perfil += n
            por_status[r["status"]] = por_status.get(r["status"], 0) + n
        if rp.is_unknown(r if r["status"] is not None else None):
            desconhecidos += n
    return {
        "vocabulary_version": rp.RESISTANCE_VOCABULARY_VERSION,
        "active_pairs": pares,
        "with_profile": com_perfil,
        "without_profile": pares - com_perfil,
        "by_status": por_status,
        "unknown": desconhecidos,
    }


@router.get("", dependencies=[Depends(require_permission("exercicios.catalogo", "ver"))])
def list_profiles(
    status: Optional[str] = Query(default=None),
    confidence: Optional[str] = Query(default=None),
    equipment_id: Optional[int] = Query(default=None, ge=1),
    exercise_id: Optional[int] = Query(default=None, ge=1),
    pending: bool = Query(default=False),
    limit: int = Query(default=LIST_DEFAULT_LIMIT, ge=1, le=LIST_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    filtros, params = _where(status, confidence, equipment_id, exercise_id, pending)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            cur.execute(COUNT_SQL.format(filters=filtros), tuple(params))
            total = int((cur.fetchone() or {}).get("total") or 0)
            cur.execute(LIST_SQL.format(filters=filtros), (*params, limit, offset))
            linhas = cur.fetchall() or []
    return {"items": [_item(r) for r in linhas], "total": total, "limit": limit, "offset": offset}


@router.get("/{equipment_exercise_id}", dependencies=[Depends(require_permission("exercicios.catalogo", "ver"))])
def get_profile(equipment_exercise_id: int):
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            cur.execute(PAIR_SQL, (equipment_exercise_id,))
            row = cur.fetchone()
    if not row:
        raise _not_found()
    return _item(row)


@router.get("/{equipment_exercise_id}/history", dependencies=[Depends(require_permission("exercicios.catalogo", "ver"))])
def get_history(
    equipment_exercise_id: int,
    limit: int = Query(default=LIST_DEFAULT_LIMIT, ge=1, le=HISTORY_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            cur.execute(PAIR_SQL, (equipment_exercise_id,))
            if not cur.fetchone():
                raise _not_found()
            cur.execute(PROFILE_ID_SQL, (equipment_exercise_id,))
            perfil = cur.fetchone()
            linhas: list[dict[str, Any]] = []
            if perfil:
                cur.execute(HISTORY_LIST_SQL, (int(perfil["id"]), limit, offset))
                linhas = cur.fetchall() or []
    return {
        "items": [
            {
                "id": int(r["id"]),
                "before": _json(r["before_json"]),
                "after": _json(r["after_json"]),
                "actor_user_id": r["actor_user_id"],
                "origin": r["origin"],
                "reason": r["reason"],
                "created_at": jsonable(r["created_at"]),
            }
            for r in linhas
        ]
    }


@router.put("/{equipment_exercise_id}")
def put_profile(
    equipment_exercise_id: int,
    payload: ResistanceProfileIn,
    identity: Identity = Depends(require_permission("admin.catalogos", "editar")),
):
    dados = payload.model_dump(exclude={"reason"})
    dados["origin"] = rp.ORIGIN_ADMIN  # a rota e do admin; a IA nunca grava por aqui
    try:
        valido = rp.validate_profile(dados)
    except rp.ResistanceProfileError as exc:
        raise _typed_error(exc)
    uid = int(identity.user_id)
    revisado = rp.stamps_review(valido.status)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            cur.execute(PAIR_SQL, (equipment_exercise_id,))
            if not cur.fetchone():
                raise _not_found()
            cur.execute(PROFILE_FOR_UPDATE_SQL, (equipment_exercise_id,))
            atual = cur.fetchone()
            if not rp.can_transition(atual["status"] if atual else None, valido.status, valido.origin):
                raise _typed_error(rp.ResistanceProfileError("invalid_transition", "status"))
            valores = (
                *(getattr(valido, c) for c in _COMPONENTS), json.dumps(sorted(valido.flags)), valido.confidence,
                valido.status, valido.origin, payload.rationale, payload.evidence, rp.RESISTANCE_VOCABULARY_VERSION,
            )
            reviewed_at = _REVIEWED_AT_SET if revisado else _REVIEWED_AT_NULL
            reviewer = uid if revisado else None
            if atual:
                profile_id = int(atual["id"])
                cur.execute(UPDATE_SQL.format(reviewed_at=reviewed_at), (*valores, reviewer, profile_id))
            else:
                try:
                    cur.execute(INSERT_SQL.format(reviewed_at=reviewed_at),
                                (equipment_exercise_id, *valores, uid, reviewer))
                except Exception as exc:
                    if is_duplicate_key_error(exc) or _is_deadlock_error(exc):
                        raise HTTPException(status_code=409, detail={"error": "profile_conflict",
                                                                     "message": "Perfil gravado por outra requisição."})
                    raise
                profile_id = int(cur.lastrowid)
            antes = _existing_snapshot(atual) if atual else None
            depois = _snapshot(valido, payload.rationale, payload.evidence)
            cur.execute(HISTORY_INSERT_SQL, (profile_id, json.dumps(antes) if antes else None, json.dumps(depois),
                                             uid, valido.origin, payload.reason))
            cur.execute(PAIR_SQL, (equipment_exercise_id,))
            return _item(cur.fetchone())

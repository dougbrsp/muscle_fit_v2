from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import async_jobs, cardio_recommendation_v3, template_drafts
from .db import DataApiError, get_conn
from .domain.cardio_envelope import RECOMMENDATION_HORIZON_DAYS
from .intelligence.report_data_service import CARDIO_CONTEXT_MAX_DAYS
from .request_models import NOTES_MAX
from .security import Identity, get_current_identity, is_admin_or_pt, require_admin_or_pt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cardio-recommendations", tags=["cardio-recommendations"])

# Schema da tabela `cardio_recommendations`: migracao versionada
# api/db/migrations/025_cardio_recommendations_schema.py (aplicada pelo
# deploy antes do codigo). Nenhuma rota/handler deste modulo emite DDL - ver
# tests/integration/dataapi/test_cardio_sem_ddl_paridade.py.


def _connect():
    try:
        return get_conn()
    except DataApiError:
        logger.exception("Banco indisponivel em %s", __name__)
        raise HTTPException(
            status_code=503,
            detail={"error": "database_unavailable", "message": "Banco de dados indisponível."},
        )


_ALLOWED_FIELDS = {
    "reference_date",
    "workout_plan_id",
    "model",
    "context_snapshot",
    "recommendation_markdown",
    # Migracao 098 (M98 C3a, `cardio-agent-v3`).
    "recommendation_json",
    "prompt_version",
}


def _insert_recommendation(fields: Dict[str, Any], user_id: int) -> int:
    """Insere uma linha em cardio_recommendations e retorna o id gerado.
    Reaproveitado tanto por POST /cardio-recommendations (registro manual)
    quanto por POST /cardio-recommendations/generate (geracao + persistencia
    via IA), para nao duplicar o SQL de insert. `user_id` sempre resolvido a
    partir de `identity.user_id` no router - nunca aceito do payload."""
    fields = {k: v for k, v in fields.items() if k in _ALLOWED_FIELDS}
    fields["user_id"] = user_id

    for json_field in ("context_snapshot", "recommendation_json"):
        value = fields.get(json_field)
        if value is not None and not isinstance(value, str):
            fields[json_field] = json.dumps(value, ensure_ascii=False, default=str)

    columns = list(fields.keys())
    values = list(fields.values())
    placeholders = ", ".join(["%s"] * len(columns))

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO cardio_recommendations ({', '.join(columns)})
                VALUES ({placeholders})
                """,
                values,
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("")
def create_cardio_recommendation(
    payload: Dict[str, Any],
    identity: Identity = Depends(get_current_identity),
):
    if not payload.get("reference_date"):
        raise HTTPException(status_code=400, detail="reference_date é obrigatório.")
    if not payload.get("recommendation_markdown"):
        raise HTTPException(status_code=400, detail="recommendation_markdown é obrigatório.")

    try:
        new_id = _insert_recommendation(payload, identity.user_id)
        return {"status": "ok", "id": new_id}
    except Exception:
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")


# Teto do `proposed_workout` (IA-10): texto livre (o frontend manda
# "nome: exercicio, exercicio...") ou objeto, que vai inteiro para o prompt.
# Sem leitura de producao para este campo: teto generoso, 4x o das anotacoes.
PROPOSED_WORKOUT_MAX_CHARS = 4 * NOTES_MAX


class CardioGenerateIn(BaseModel):
    """Entrada de `POST /cardio-recommendations/generate` (IA-10).

    Antes era `Dict[str, Any]` sem schema: `days` sem teto alimentava a
    leitura de treinos e o prompt crescia ate o limite do evento assincrono.
    `days` vai de 1 a `CARDIO_CONTEXT_MAX_DAYS` (o mesmo teto da janela de
    cardio da Analise; a tela oferece 7/14/30). Ausente = default do
    `cardio_generation_core`. Campo desconhecido = 422 (`extra="forbid"`,
    padrao do SEG-11): os dois chamadores do frontend (`agente-cardio`,
    `PlanDetailClient`) mandam so estes campos."""

    model_config = ConfigDict(extra="forbid")

    days: Optional[int] = Field(default=None, ge=1, le=CARDIO_CONTEXT_MAX_DAYS)
    proposed_workout: Optional[Union[str, Dict[str, Any]]] = None
    reference_date: Optional[str] = Field(default=None, max_length=32)
    reference_time: Optional[str] = Field(default=None, max_length=16)
    workout_plan_id: Optional[int] = Field(default=None, ge=1)

    @field_validator("proposed_workout")
    @classmethod
    def _proposed_workout_size(cls, value: Any) -> Any:
        if value is None:
            return value
        size = len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False, default=str))
        if size > PROPOSED_WORKOUT_MAX_CHARS:
            raise ValueError(f"proposed_workout acima de {PROPOSED_WORKOUT_MAX_CHARS} caracteres.")
        return value


@router.post("/generate", status_code=202)
def generate_and_save_cardio_recommendation(
    payload: Optional[CardioGenerateIn] = None,
    identity: Identity = Depends(get_current_identity),
):
    """Assincrono desde a Fase 3 da migracao AWS (202 + job_id + polling via
    GET /jobs/{job_id}) - a geracao + persistencia em si
    (`cardio_generation_core.dispatch_cardio_generation`, mesma logica exata
    do endpoint sincrono anterior) roda na Lambda `musclefit-cardio-agent`,
    invocada aqui de forma assincrona (`InvocationType=Event`). Mesmo motivo
    do endpoint equivalente de plano de treino - ver
    docs/aws-migration-plan.md, secao 1.2.

    Aceita os mesmos campos de sempre no payload: "days" (janela de
    contexto, default 14), "proposed_workout", "reference_date",
    "reference_time" e "workout_plan_id" (ver docstring anterior no
    historico deste arquivo para o detalhe de cada um - preservados sem
    mudanca, so repassados ao job)."""
    if isinstance(payload, dict):  # chamada direta (testes), fora do FastAPI
        payload = CardioGenerateIn.model_validate(payload)
    payload = payload.model_dump(exclude_none=True) if payload is not None else {}
    job_id = async_jobs.create_job(
        "cardio-agent", request={"payload": payload, "user_id": identity.user_id}, owner_user_id=identity.user_id
    )
    async_jobs.invoke_async(
        "CARDIO_AGENT_FUNCTION_NAME", {"job_id": job_id, "payload": payload, "user_id": identity.user_id}
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


# Teto da listagem. Em producao cada SELECT passa pela RDS Data API, que
# encerra a chamada quando o resultado passa de 1 MiB
# (https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.limitations.html).
# Sem `context_snapshot`, cada item custa ~recommendation_markdown (medido:
# media 3,6 KB, max 4,3 KB) + ~0,5 KB de metadados no formato da Data API,
# ou seja ~5 KB: 100 itens ~= 500 KB, ~2x de folga. O teste
# tests/integration/dataapi/test_cardio_recommendations_1mb.py prova que 100
# itens com markdown de 8 KB (quase 2x o maximo medido) cabem no limite.
# REGRA: listagens nunca devem trazer colunas grandes (JSON/MEDIUMTEXT, ex.
# `context_snapshot`, `raw_payload`) e sempre precisam de teto de linhas.
LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 100


@router.get("")
def list_cardio_recommendations(
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    # `le=1000` mantido so para nao devolver 422 a clientes antigos
    # (`?limit=200`): o valor efetivo e truncado em LIST_MAX_LIMIT.
    limit: int = Query(default=LIST_DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    workout_plan_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    where = ["user_id = %s"]
    params: list[Any] = [identity.user_id]
    if days:
        where.append("reference_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)")
        params.append(days)
    if workout_plan_id:
        where.append("workout_plan_id = %s")
        params.append(workout_plan_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(min(limit, LIST_MAX_LIMIT))
    params.append(offset)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            # `context_snapshot` (JSON de ~110-170 KB por linha) fica FORA do
            # SELECT: 25 linhas ja passavam de 1 MiB e a listagem falhava em
            # producao com "The result exceeds the size limit 1 MB". Nenhuma
            # tela usa o snapshot vindo da listagem; o detalhe completo segue
            # em GET /cardio-recommendations/{id}.
            cur.execute(
                f"""
                SELECT id, reference_date, workout_plan_id, generated_at, model,
                       prompt_version, recommendation_markdown, created_at
                FROM cardio_recommendations
                {where_sql}
                ORDER BY generated_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        # Chave mantida como `null` por compatibilidade de contrato (deprecada
        # na listagem - use o detalhe para obter o snapshot).
        for row in rows:
            row["context_snapshot"] = None
        return {"items": rows}
    finally:
        conn.close()


def _load_recommendation(entry_id: int, user_id: int) -> dict[str, Any]:
    """A recomendação do usuário (404 se não é dele), com `recommendation_json`
    como objeto."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM cardio_recommendations WHERE id=%s AND user_id=%s", (entry_id, user_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    # 098 (M98 C3a): a recomendação estruturada sai como objeto (a Data API
    # devolve a coluna JSON como texto); recomendação antiga = null.
    structured = row.get("recommendation_json")
    if isinstance(structured, (bytes, bytearray)):
        structured = structured.decode("utf-8")
    if isinstance(structured, str):
        try:
            row["recommendation_json"] = json.loads(structured)
        except ValueError:
            row["recommendation_json"] = None
    return row


@router.get("/{entry_id}")
def get_cardio_recommendation(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    row = _load_recommendation(entry_id, identity.user_id)
    # M98 C3b: a recomendação pronta para exibir (rótulos, tempo total e se
    # cada sessão pode ir para o Modelo). Antiga (sem JSON) = null.
    row["recommendation_view"] = cardio_recommendation_v3.build_view(
        row.get("recommendation_json"), can_apply=is_admin_or_pt(identity)
    )
    return row


class CardioApplyIn(BaseModel):
    """Sessões escolhidas para `POST /cardio-recommendations/{id}/apply`, pela
    data (a recomendação tem no máximo uma sessão por dia, dentro do
    horizonte do envelope)."""

    model_config = ConfigDict(extra="forbid")

    dates: list[str] = Field(min_length=1, max_length=RECOMMENDATION_HORIZON_DAYS)


LEGACY_RECOMMENDATION = "Esta recomendação é anterior às sessões estruturadas e não pode ser aplicada no Modelo."
NO_TEMPLATE = "Esta recomendação não tem Modelo vinculado: não há onde aplicar."


@router.post("/{entry_id}/apply")
def apply_cardio_recommendation(
    entry_id: int,
    payload: CardioApplyIn,
    identity: Identity = Depends(require_admin_or_pt),
):
    """Aplica sessões da recomendação no RASCUNHO do Modelo ativo (M98 C3b).

    Nunca publica: o usuário revisa e publica no editor do Modelo. Só quem
    edita o Modelo (admin ou personal trainer, a mesma regra do `PUT
    /workout-plan-templates/{id}/draft`) e só a própria recomendação.
    Idempotente: aplicar de novo a mesma sessão não duplica nada. Recusa o
    pedido inteiro (409) se a recomendação não tem JSON ou Modelo, ou se o
    Modelo não é mais o ativo; o resto é recusado por sessão, com a
    mensagem, em `results`."""
    from .workout_plan_templates import apply_cardio_sessions_to_draft

    row = _load_recommendation(entry_id, identity.user_id)
    recommendation = row.get("recommendation_json")
    if not recommendation:
        raise HTTPException(status_code=409, detail={"reason": "legacy_recommendation", "message": LEGACY_RECOMMENDATION})
    template_id = (recommendation.get("plan") or {}).get("template_id")
    if template_id is None:
        raise HTTPException(status_code=409, detail={"reason": "no_template", "message": NO_TEMPLATE})
    by_date = {session.get("date"): session for session in recommendation.get("sessions") or []}
    chosen: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for day in dict.fromkeys(payload.dates):
        if day in by_date:
            chosen.append(by_date[day])
        else:
            missing.append(
                template_drafts.apply_result(day, template_drafts.APPLY_REJECTED, template_drafts.CODE_NOT_IN_RECOMMENDATION)
            )
    outcome = apply_cardio_sessions_to_draft(
        int(template_id), chosen, owner_user_id=identity.user_id, actor=identity.user_id
    )
    outcome["results"] = sorted(outcome["results"] + missing, key=lambda r: str(r.get("date") or ""))
    outcome["recommendation_id"] = entry_id
    return outcome


@router.delete("/{entry_id}")
def delete_cardio_recommendation(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cardio_recommendations WHERE id=%s AND user_id=%s", (entry_id, identity.user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
        conn.commit()
        return {"status": "deleted", "id": entry_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()

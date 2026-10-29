"""Rotas e handler de cardio SEM DDL, nos dois backends (PyMySQL e shim RDS
Data API + dublê).

Antes, a primeira chamada de cada processo a POST/GET/DELETE
/cardio-recommendations, ao handler da Lambda `musclefit-cardio-agent` e ao
contexto do agente rodava `cardio_recommendations._ensure_schema()`:
`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD` e `MODIFY COLUMN` - commit
implicito no MySQL, dentro da transacao Data API aberta pelo shim. Agora o
schema vem da migracao 025 e:

(a) nenhuma rota/handler emite CREATE/ALTER/DROP/TRUNCATE/RENAME nem consulta
    `information_schema` - capturado no cursor PyMySQL e nas chamadas
    `ExecuteStatement` do dublê (mesma captura dos testes "GET sem escrita" do #89);
(b) o handler da Lambda roda o fluxo inteiro (contexto real + insert) e
    termina `done`, sem transacao Data API pendurada.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app import async_jobs, cardio_agent_ai, lambda_cardio_agent
from app import cardio_recommendations as cr
from app.load_conventions import has_load_convention_column
from app.security import Identity
from tests.support.cardio_schema import ensure_cardio_table
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.sql_capture import ddl, recorded_sql

MARKDOWN = "# Cardio teste\nZona 2 por 30 minutos."
# Resposta do LLM no contrato do `cardio-agent-v3` (M98 C3a): JSON do schema
# strict (`cardio_recommendation_v3.llm_schema`), que `call_openai` devolve ja
# parseado. Sem sessoes: o teste mede SQL/DDL, nao a decisao do agente.
LLM_V3 = {"summary": "Teste.", "objective": "Teste.", "confidence": "medium", "confidence_reason": "Teste.", "sessions": []}


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="regular", source="session")


@pytest.fixture
def cardio_env(raw):
    """Estado de producao: tabela pela 025 e as lidas pelo contexto do agente
    (`sleep_metrics`, `manual_effort_metrics`, `intervals_wellness`) pela 027,
    na conexao neutra e FORA da captura - este fixture precisa vir antes de
    `db_backend` na assinatura do teste."""
    import app.db as db
    from app import db_pymysql

    assert db.get_conn is db_pymysql.get_conn, "cardio_env deve ser instanciado antes da troca de backend"
    ensure_cardio_table(raw)
    ensure_grupo_b_tables(raw)
    # Estado de producao do processo: a deteccao da coluna da 045
    # (`load_conventions.has_load_convention_column`, consulta a
    # `information_schema` cacheada por schema depois do primeiro positivo) ja
    # rodou. O handler chega nela pelo contexto do agente
    # (`CardioAgentContextService` -> `TrainingLoadService` ->
    # `load_convention_select_expr`). Sem aquecer aqui, qual teste "paga" a
    # consulta dependia da ordem da suite: rodando este arquivo sozinho, o
    # handler era o primeiro e a captura abaixo pegava o SELECT de
    # `information_schema` (falha que ja existia na develop isolada). Nao e DDL
    # nem acontece por requisicao em producao: no maximo uma vez por processo.
    with raw.cursor() as cur:
        assert has_load_convention_column(cur), "schema de teste sem a migracao 045"

    with raw.cursor() as cur:
        cur.execute("DELETE FROM cardio_recommendations")
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('cardio-ddl', 'x', 'regular')")
        user_id = cur.lastrowid
        cur.execute("INSERT INTO workout_plans (name, user_id) VALUES ('Plano cardio DDL', %s)", (user_id,))
        plan_id = cur.lastrowid
        cur.execute(
            "INSERT INTO cardio_recommendations (user_id, reference_date, workout_plan_id, model, recommendation_markdown) "
            "VALUES (%s, '2026-09-01', %s, 'gpt-test', '# existente')",
            (user_id, plan_id),
        )
        existing_id = cur.lastrowid
    yield {"user": user_id, "plan": plan_id, "existing": existing_id, "raw": raw}
    with raw.cursor() as cur:
        cur.execute("DELETE FROM cardio_recommendations WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM workout_plans WHERE id = %s", (plan_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def _count(env, where: str, params) -> int:
    with env["raw"].cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM cardio_recommendations WHERE {where}", params)
        return cur.fetchone()["n"]


def _create(env, monkeypatch):
    out = cr.create_cardio_recommendation(
        {"reference_date": "2026-09-02", "recommendation_markdown": MARKDOWN, "workout_plan_id": env["plan"]},
        identity=_identity(env["user"]),
    )
    assert out["status"] == "ok"
    assert _count(env, "id = %s AND user_id = %s", (out["id"], env["user"])) == 1


def _list(env, monkeypatch):
    items = cr.list_cardio_recommendations(
        days=None, limit=10, offset=0, workout_plan_id=env["plan"], identity=_identity(env["user"])
    )["items"]
    assert [item["id"] for item in items] == [env["existing"]]


def _detail(env, monkeypatch):
    row = cr.get_cardio_recommendation(env["existing"], identity=_identity(env["user"]))
    assert row["recommendation_markdown"] == "# existente"


def _apply_legacy(env, monkeypatch):
    # M98 C3b: recomendação antiga (sem JSON) não vai para o Modelo; o 409
    # sai depois de ler a linha, sem DDL e sem tocar o rascunho.
    with pytest.raises(HTTPException) as exc:
        cr.apply_cardio_recommendation(
            env["existing"], cr.CardioApplyIn(dates=["2026-09-01"]), identity=_identity(env["user"])
        )
    assert exc.value.status_code == 409 and exc.value.detail["reason"] == "legacy_recommendation"


def _delete(env, monkeypatch):
    assert cr.delete_cardio_recommendation(env["existing"], identity=_identity(env["user"]))["status"] == "deleted"
    assert _count(env, "id = %s", (env["existing"],)) == 0


def _generate_202(env, monkeypatch):
    calls = []
    monkeypatch.setattr(async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: "job-ddl")
    monkeypatch.setattr(async_jobs, "invoke_async", lambda fn_env, payload: calls.append((fn_env, payload)))
    response = cr.generate_and_save_cardio_recommendation({"days": 7}, identity=_identity(env["user"]))
    assert response.status_code == 202
    assert json.loads(response.body) == {"job_id": "job-ddl", "status": "pending"}
    assert calls and calls[0][0] == "CARDIO_AGENT_FUNCTION_NAME"


def _handler(env, monkeypatch):
    done, failed = [], []
    monkeypatch.setattr(cardio_agent_ai, "call_openai", lambda prompt, *_a, **_k: dict(LLM_V3))
    monkeypatch.setattr(async_jobs, "mark_done", lambda job_id, result: done.append((job_id, result)))
    monkeypatch.setattr(async_jobs, "mark_failed", lambda job_id, error: failed.append((job_id, error)))
    monkeypatch.setattr(async_jobs, "claim_job", lambda job_id: True)  # M81
    lambda_cardio_agent.handler(
        {
            "job_id": "job-handler",
            "payload": {"days": 14, "reference_date": "2026-09-03", "workout_plan_id": env["plan"]},
            "user_id": env["user"],
        },
        None,
    )
    assert failed == []
    assert [job for job, _ in done] == ["job-handler"]
    new_id = done[0][1]["id"]
    assert _count(env, "id = %s AND user_id = %s AND workout_plan_id = %s", (new_id, env["user"], env["plan"])) == 1
    # Persistencia v3 (migracao 098): JSON estruturado + versao do prompt; o markdown legado continua gravado.
    assert _count(
        env,
        "id = %s AND prompt_version = %s AND recommendation_json IS NOT NULL AND recommendation_markdown <> ''",
        (new_id, cardio_agent_ai.PROMPT_VERSION),
    ) == 1


ROUTES = {
    "POST /cardio-recommendations": _create,
    "GET /cardio-recommendations": _list,
    "GET /cardio-recommendations/{id}": _detail,
    "POST /cardio-recommendations/{id}/apply (antiga)": _apply_legacy,
    "DELETE /cardio-recommendations/{id}": _delete,
    "POST /cardio-recommendations/generate": _generate_202,
    "lambda musclefit-cardio-agent handler": _handler,
}


def test_instrumentacao_captura_ddl(cardio_env, db_backend, monkeypatch, fake_rds):
    """Garante que a captura abaixo nao e vazia por construcao."""
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        conn = cr._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS cardio_recommendations (id BIGINT PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
    assert len(ddl(statements)) == 1, statements


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_rota_e_handler_de_cardio_nao_emitem_ddl(cardio_env, db_backend, monkeypatch, fake_rds, route):
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        ROUTES[route](cardio_env, monkeypatch)

    if route == "POST /cardio-recommendations/generate":
        assert statements == []  # so cria o job (DynamoDB) e invoca a Lambda
    else:
        assert statements, "nenhum statement capturado - instrumentacao quebrada"
    assert ddl(statements) == []
    assert [s for s in statements if "information_schema" in s.lower()] == []
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"

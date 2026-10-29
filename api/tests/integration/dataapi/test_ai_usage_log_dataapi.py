"""Uso e cota de IA da nutrição (L5a, migração 085) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de produção).

O que este arquivo prova (o SQL de `app.nutrition_ai_quota` só tinha rodado
contra o fake dos testes unitários):

- a 085 aplicada pelo schema da suíte converge: `plan()` devolve 0 statements
  lendo o `information_schema` REAL pelos dois backends (a Lambda de migrações
  lê pela Data API), com a FK só para `users` (CASCADE);
- `open_usage` abre a linha `pending` com o `started_at` do relógio do banco
  (trava `FOR UPDATE` do usuário e a contagem na mesma transação), `link_job`
  grava o job uma vez e `finish_usage` fecha com o custo do motor
  (DECIMAL(10,6)) sem reabrir um fechamento de worker;
- a cota conta toda tentativa do DIA DO USUÁRIO: a linha de antes do início do
  dia (no fuso dele) não conta, a última do dia passa e a seguinte é 429 sem
  gravar; a outra feature e o outro usuário seguem livres;
- o sweep fecha só a `pending` vencida (`DATE_SUB(NOW(), INTERVAL %s SECOND)`)
  e o relato do worker vence a presunção `worker_lost`;
- o resultado é idêntico nos dois backends.

Os job types de nutrição entram em `async_jobs.JOB_TYPE_LAMBDA` no L5b/L6; aqui
o teste os registra (monkeypatch) para o sweep ter prazo.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import async_jobs
from app import migrate as m
from app import nutrition_ai_quota  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.domain.nutrition import ai_usage
from app.domain.nutrition.ai_usage import AiFeature, AiUsageStatus
from app.intelligence.dashboard.time_windows import user_day_start_db, user_today
from app.nutrition_schema import reset_nutrition_tables_cache
from tests.support.dataapi_backend import BACKENDS, use_backend

FUSO = "America/Sao_Paulo"
USUARIOS = ("ia_aluno", "ia_outro")
# Lambdas da decisão E8 do plano (texto na workout-ai, imagem na plan-generator).
E8 = {AiFeature.MEAL_PARSE: "musclefit-workout-ai", AiFeature.LABEL_PHOTO: "musclefit-plan-generator"}
COLUNAS = ("feature", "status", "job_id", "prompt_version", "model", "error_code", "tokens_in", "tokens_out",
           "image_count", "cost_usd")


def _modulo() -> dict:
    return m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "085_ai_usage_log.py").__globals__


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("DELETE FROM ai_usage_log")


@pytest.fixture
def usuarios(raw):
    reset_nutrition_tables_cache()
    placeholders = ", ".join(["%s"] * len(USUARIOS))
    ids = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({placeholders})", USUARIOS)
        for nome in USUARIOS:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, timezone) VALUES (%s, 'x', 'regular', %s)",
                (nome, FUSO),
            )
            ids[nome] = cur.lastrowid
    yield ids
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({placeholders})", USUARIOS)  # CASCADE leva as linhas


@pytest.fixture
def jobs_registrados(monkeypatch):
    monkeypatch.setattr(
        async_jobs, "JOB_TYPE_LAMBDA",
        {**async_jobs.JOB_TYPE_LAMBDA, **{ai_usage.FEATURE_JOB_TYPE[f]: fn for f, fn in E8.items()}},
    )


def _linha(raw, usage_id: int) -> dict:
    with raw.cursor() as cur:
        cur.execute(f"SELECT {', '.join(COLUNAS)}, started_at, finished_at FROM ai_usage_log WHERE id = %s",
                    (usage_id,))
        return cur.fetchone()


def _total(raw) -> int:
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM ai_usage_log")
        return int(cur.fetchone()["n"])


def test_085_converge_no_schema_real_com_fk_so_para_users(raw):
    assert _modulo()["plan"](m.MigrationDB(raw)) == []
    with raw.cursor() as cur:
        cur.execute(
            "SELECT REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_usage_log'"
        )
        fks = [(linha["REFERENCED_TABLE_NAME"], linha["DELETE_RULE"]) for linha in cur.fetchall()]
    assert fks == [("users", "CASCADE")]


def test_ciclo_da_cota_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema,
                                                  jobs_registrados):
    aluno, outro = usuarios["ia_aluno"], usuarios["ia_outro"]
    limite = ai_usage.MEAL_PARSE_DAILY_QUOTA
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        reset_nutrition_tables_cache()
        _limpar(raw)
        # 0. A 085 converge lida pelo backend (a Lambda de migrações usa a Data API).
        with nutrition_ai_quota.get_conn() as conn:
            assert _modulo()["plan"](m.MigrationDB(conn)) == [], backend

        # 1. Reserva -> job -> fechamento pelo worker.
        usage_id = nutrition_ai_quota.open_usage(
            user_id=aluno, feature=AiFeature.MEAL_PARSE, prompt_version="nutrition-parse-v1", model="gpt-5.4-mini"
        )
        aberta = _linha(raw, usage_id)
        assert aberta["status"] == "pending" and aberta["started_at"] is not None, backend
        assert aberta["finished_at"] is None and aberta["image_count"] == 0
        assert nutrition_ai_quota.link_job(usage_id, "job-l5a") is True
        assert nutrition_ai_quota.link_job(usage_id, "job-outro") is False
        assert nutrition_ai_quota.finish_usage(
            usage_id, status=AiUsageStatus.SUCCEEDED, model="gpt-5.4-mini-2026-03-17", tokens_in=2000, tokens_out=500
        ) is True
        assert nutrition_ai_quota.finish_usage(usage_id, status="failed", error_code="OPENAI_TIMEOUT") is False
        fechada = _linha(raw, usage_id)
        assert fechada["status"] == "succeeded" and fechada["job_id"] == "job-l5a", backend
        assert fechada["cost_usd"] == Decimal("0.003750") == ai_usage.estimate_cost_usd("gpt-5.4-mini", 2000, 500)
        assert fechada["finished_at"] is not None

        # 2. Cota no dia do usuário (fuso do perfil): a linha de antes do início do dia não conta.
        inicio = user_day_start_db(user_today(FUSO), FUSO)
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_usage_log (user_id, feature, status, started_at) VALUES (%s, 'meal_parse', 'failed', %s)",
                (aluno, inicio - _dt.timedelta(seconds=1)),
            )
            faltam = limite - 2  # já há a fechada acima; a reserva seguinte é a última do dia
            cur.execute(
                "INSERT INTO ai_usage_log (user_id, feature, status) VALUES "
                + ", ".join(["(%s, 'meal_parse', 'failed')"] * faltam),
                (aluno,) * faltam,
            )
        ultima = nutrition_ai_quota.open_usage(user_id=aluno, feature=AiFeature.MEAL_PARSE)
        antes = _total(raw)
        with pytest.raises(HTTPException) as erro:
            nutrition_ai_quota.open_usage(user_id=aluno, feature=AiFeature.MEAL_PARSE)
        assert erro.value.status_code == 429, backend
        assert erro.value.detail["used"] == limite and erro.value.detail["remaining"] == 0
        assert _total(raw) == antes  # 429 não grava
        foto = nutrition_ai_quota.open_usage(user_id=aluno, feature=AiFeature.LABEL_PHOTO, image_count=1)
        do_outro = nutrition_ai_quota.open_usage(user_id=outro, feature=AiFeature.MEAL_PARSE)

        # 3. Sweep: só a pending vencida fecha; o relato do worker vence a presunção.
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_usage_log (user_id, feature, status, model, started_at) "
                "VALUES (%s, 'meal_parse', 'pending', 'gpt-5.4-mini', DATE_SUB(NOW(), INTERVAL 10 MINUTE))",
                (outro,),
            )
            vencida = cur.lastrowid
        with nutrition_ai_quota.transaction() as conn:
            assert nutrition_ai_quota.sweep_stale_pending(conn) == 1, backend
        perdida = _linha(raw, vencida)
        assert perdida["status"] == "failed" and perdida["error_code"] == async_jobs.WORKER_LOST_ERROR_CODE
        assert [_linha(raw, i)["status"] for i in (ultima, foto, do_outro)] == ["pending"] * 3
        assert nutrition_ai_quota.finish_usage(vencida, status="succeeded", tokens_in=10, tokens_out=10) is True
        assert _linha(raw, vencida)["status"] == "succeeded" and _linha(raw, vencida)["error_code"] is None

        resultados[backend] = {
            "linhas": [
                {coluna: _linha(raw, i)[coluna] for coluna in COLUNAS}
                for i in (usage_id, ultima, foto, do_outro, vencida)
            ],
            "erro_429": erro.value.detail,
        }
    assert resultados["pymysql"] == resultados["data-api"]

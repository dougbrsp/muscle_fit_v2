"""Serviço do `ai_usage_log` (`app/nutrition_ai_quota.py`, nutrição L5a): cota
no dia do usuário (fuso do perfil), reserva com trava, 429 do dono, vínculo do
job, fechamento pelo worker (custo do motor; o relato vence a presunção do
sweep) e o sweep pelo prazo do M73, que roda no worker depois de fechar.

O banco é um fake em memória que responde EXATAMENTE os statements do serviço
(SQL novo ou alterado sem o fake acompanhar falha alto). A integração nos dois
backends está em `tests/integration/dataapi/test_ai_usage_log_dataapi.py` (CI).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from app import async_jobs
from app import nutrition_ai_quota as q
from app.domain.nutrition import ai_usage as ai
from app.domain.nutrition import labels as lb
from app.domain.nutrition.ai_usage import AiFeature, AiUsageStatus
from app.load_conventions import _CURRENT_SCHEMA_SQL
from app.nutrition_schema import reset_nutrition_tables_cache, tables_sql

USUARIO, OUTRO = 7, 8
SP = "America/Sao_Paulo"  # UTC-3, sem horário de verão
# 23:30 de 17/10 em São Paulo = 02:30 UTC de 18/10: o dia do usuário começou às 03:00 UTC de 17/10.
AGORA_UTC = datetime(2026, 10, 18, 2, 30)
INICIO_DIA_SP = datetime(2026, 10, 17, 3, 0)
# Lambdas da decisão E8 do plano (texto na workout-ai, imagem na plan-generator).
E8 = {AiFeature.MEAL_PARSE: "musclefit-workout-ai", AiFeature.LABEL_PHOTO: "musclefit-plan-generator"}


class FakeDB:
    def __init__(self, *, tabela: bool = True) -> None:
        self.tabela = tabela
        self.users: dict[int, str] = {USUARIO: SP, OUTRO: "UTC"}
        self.rows: list[dict[str, Any]] = []
        self.now = AGORA_UTC  # relógio do banco (UTC)
        self.executed: list[str] = []
        self.fail_on: Optional[str] = None

    def add(self, *, user_id: int = USUARIO, feature: AiFeature = AiFeature.MEAL_PARSE,
            status: AiUsageStatus = AiUsageStatus.PENDING, started_at: Optional[datetime] = None,
            error_code: Optional[str] = None, model: Optional[str] = None) -> int:
        linha = {
            "id": len(self.rows) + 1, "user_id": user_id, "feature": feature.value, "status": status.value,
            "job_id": None, "prompt_version": None, "model": model, "error_code": error_code, "tokens_in": None,
            "tokens_out": None, "image_count": 0, "cost_usd": None, "started_at": started_at or self.now,
            "finished_at": None,
        }
        self.rows.append(linha)
        return linha["id"]

    def row(self, usage_id: int) -> dict[str, Any]:
        return next(r for r in self.rows if r["id"] == usage_id)

    def conn(self) -> "FakeConn":
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.db)

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeCursor:
    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0
        self.lastrowid: Optional[int] = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def fetchone(self) -> Optional[dict[str, Any]]:
        return dict(self._rows[0]) if self._rows else None

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db = self.db
        p = tuple(params or ())
        db.executed.append(sql)
        if db.fail_on and db.fail_on in sql:
            raise RuntimeError("banco caiu")
        self.rowcount, self._rows = 0, []
        if sql == _CURRENT_SCHEMA_SQL:
            self._rows = [{"db": "fake_l5a"}]
        elif sql == tables_sql(1):
            self._rows = [{"n": 1 if db.tabela and p == ("ai_usage_log",) else 0}]
        elif sql == "SELECT timezone FROM users WHERE id = %s":
            self._rows = [{"timezone": db.users[p[0]]}] if p[0] in db.users else []
        elif sql == q.LOCK_USER_SQL:
            self._rows = [{"id": p[0]}] if p[0] in db.users else []
        elif sql == q.COUNT_SINCE_SQL:
            uid, feature, desde = p
            n = sum(1 for r in db.rows if r["user_id"] == uid and r["feature"] == feature and r["started_at"] >= desde)
            self._rows = [{"n": n}]
        elif sql == q.INSERT_PENDING_SQL:
            uid, feature, status, prompt_version, model, imagens = p
            self.lastrowid = db.add(user_id=uid, feature=AiFeature(feature), status=AiUsageStatus(status), model=model)
            db.row(self.lastrowid).update(prompt_version=prompt_version, image_count=imagens)
        elif sql == q.ATTACH_JOB_SQL:
            job_id, usage_id = p
            alvo = [r for r in db.rows if r["id"] == usage_id and r["job_id"] is None]
            for r in alvo:
                r["job_id"] = job_id
            self.rowcount = len(alvo)
        elif sql == q.SELECT_FOR_FINISH_SQL:
            self._rows = [{k: r[k] for k in ("model", "status", "error_code")} for r in db.rows if r["id"] == p[0]]
        elif sql == q.FINISH_SQL:
            status, model, tin, tout, cost, err, usage_id, pend, fail, lost = p
            alvo = [r for r in db.rows if r["id"] == usage_id and (
                r["status"] == pend or (r["status"] == fail and r["error_code"] == lost))]
            for r in alvo:
                r.update(status=status, model=model, tokens_in=tin, tokens_out=tout, cost_usd=cost, error_code=err,
                         finished_at=db.now)
            self.rowcount = len(alvo)
        elif sql == q.SWEEP_SQL:
            status, err, pend, feature, segundos = p
            limite = db.now - timedelta(seconds=segundos)
            alvo = [r for r in db.rows if r["status"] == pend and r["feature"] == feature and r["started_at"] < limite]
            for r in alvo:
                r.update(status=status, error_code=err, finished_at=db.now)
            self.rowcount = len(alvo)
        else:
            raise AssertionError(f"statement inesperado no fake do ai_usage_log: {sql[:120]}")


@pytest.fixture(autouse=True)
def _cache_de_schema():
    reset_nutrition_tables_cache()
    yield
    reset_nutrition_tables_cache()


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def banco(db, monkeypatch):
    """As entradas de alto nível (e o sweep do `finish_usage`) abrem a própria
    transação: aqui ela é sempre o fake, nunca um banco de verdade."""

    @contextmanager
    def transacao():
        yield db.conn()

    monkeypatch.setattr(q, "transaction", transacao)
    monkeypatch.setattr(q, "get_conn", db.conn)
    return db


@pytest.fixture
def jobs_registrados(monkeypatch):
    """O L5b/L6 registra os job types em `JOB_TYPE_LAMBDA` (exigido pelo teste do M73)."""
    monkeypatch.setattr(
        async_jobs, "JOB_TYPE_LAMBDA",
        {**async_jobs.JOB_TYPE_LAMBDA, **{ai.FEATURE_JOB_TYPE[f]: fn for f, fn in E8.items()}},
    )


def _reservar(db: FakeDB, feature: Any = AiFeature.MEAL_PARSE, **extra: Any) -> int:
    return q.reserve_usage(db.conn(), user_id=extra.pop("user_id", USUARIO), feature=feature, now_utc=AGORA_UTC,
                           **extra)


# ---------------------------------------------------------------------------
# Cota no dia do usuário
# ---------------------------------------------------------------------------


def test_dia_da_cota_e_o_do_fuso_do_usuario(db):
    db.add(started_at=INICIO_DIA_SP - timedelta(seconds=1))  # 23:59:59 de 16/10 em SP: outro dia
    db.add(started_at=INICIO_DIA_SP)  # 00:00 de 17/10 em SP
    db.add(started_at=AGORA_UTC)  # 23:30 de 17/10 em SP (02:30 UTC de 18/10)
    assert q.count_usage_today(db.conn(), user_id=USUARIO, feature="meal_parse", now_utc=AGORA_UTC) == 2
    # À meia-noite de SP o dia vira: nenhuma das três conta.
    meia_noite = datetime(2026, 10, 18, 3, 0)
    assert q.count_usage_today(db.conn(), user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=meia_noite) == 0


def test_inicio_do_dia_vem_do_dono_do_fuso(db):
    from app.intelligence.dashboard.time_windows import user_day_start_db_for

    assert user_day_start_db_for(db.conn(), USUARIO, AGORA_UTC) == INICIO_DIA_SP
    assert user_day_start_db_for(db.conn(), OUTRO, AGORA_UTC) == datetime(2026, 10, 18, 0, 0)


def test_usuario_em_utc_conta_pelo_dia_utc(db):
    db.add(user_id=OUTRO, started_at=datetime(2026, 10, 18, 0, 0))
    db.add(user_id=OUTRO, started_at=datetime(2026, 10, 17, 23, 59, 59))
    assert q.count_usage_today(db.conn(), user_id=OUTRO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC) == 1


def test_cota_e_por_feature_e_por_usuario_e_toda_tentativa_conta(db):
    for status in AiUsageStatus:
        db.add(status=status)
    db.add(status=AiUsageStatus.FAILED, error_code=async_jobs.WORKER_LOST_ERROR_CODE)
    db.add(feature=AiFeature.LABEL_PHOTO)
    db.add(user_id=OUTRO)
    uso = q.usage_today(db.conn(), user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC)
    assert uso.to_dict() == {"feature": "meal_parse", "limit": ai.MEAL_PARSE_DAILY_QUOTA, "used": 4,
                             "remaining": ai.MEAL_PARSE_DAILY_QUOTA - 4, "allowed": True}
    assert q.usage_today(db.conn(), user_id=USUARIO, feature="label_photo", now_utc=AGORA_UTC).used == 1


# ---------------------------------------------------------------------------
# Reserva
# ---------------------------------------------------------------------------


def test_reserva_comeca_pela_trava_do_usuario_e_so_depois_conta(db):
    usage_id = _reservar(db, prompt_version="nutrition-parse-v1", model="gpt-5.4-mini")
    linha = db.row(usage_id)
    assert linha["status"] == "pending" and linha["feature"] == "meal_parse" and linha["user_id"] == USUARIO
    assert linha["prompt_version"] == "nutrition-parse-v1" and linha["model"] == "gpt-5.4-mini"
    assert linha["image_count"] == 0 and linha["job_id"] is None
    # A trava é o 1º statement da transação: a contagem (1ª leitura consistente) vem depois.
    assert db.executed[0] == q.LOCK_USER_SQL
    ordem = [sql for sql in db.executed if sql in (q.LOCK_USER_SQL, q.COUNT_SINCE_SQL, q.INSERT_PENDING_SQL)]
    assert ordem == [q.LOCK_USER_SQL, q.COUNT_SINCE_SQL, q.INSERT_PENDING_SQL]
    assert "FOR SHARE" not in q.COUNT_SINCE_SQL  # leitura travada da faixa entraria em deadlock com o sweep


@pytest.mark.parametrize("feature", list(AiFeature))
def test_ultima_tentativa_do_dia_passa_e_a_seguinte_e_429(db, feature):
    limite = ai.DAILY_QUOTA[feature]
    for _ in range(limite - 1):
        db.add(feature=feature)
    db.add(feature=feature, started_at=INICIO_DIA_SP - timedelta(minutes=1))  # ontem no fuso: não conta
    _reservar(db, feature)  # a última do dia
    total = len(db.rows)
    with pytest.raises(HTTPException) as erro:
        _reservar(db, feature, image_count=1)
    assert erro.value.status_code == 429
    assert erro.value.detail == {
        "error": ai.ERR_AI_QUOTA_EXCEEDED, "message": lb.ai_quota_exceeded_label(feature), "feature": feature.value,
        "limit": limite, "used": limite, "remaining": 0, "allowed": False,
    }
    assert str(limite) in erro.value.detail["message"]
    assert len(db.rows) == total  # 429 não grava


def test_cota_de_uma_feature_nao_bloqueia_a_outra(db):
    for _ in range(ai.LABEL_PHOTO_DAILY_QUOTA):
        db.add(feature=AiFeature.LABEL_PHOTO)
    assert _reservar(db, AiFeature.MEAL_PARSE)
    with pytest.raises(HTTPException):
        _reservar(db, AiFeature.LABEL_PHOTO)


def test_sem_a_085_responde_503_sem_gravar():
    db = FakeDB(tabela=False)
    with pytest.raises(HTTPException) as erro:
        _reservar(db)
    assert erro.value.status_code == 503
    assert erro.value.detail["error"] == "ai_usage_table_missing" and erro.value.detail["migration"] == "085"
    assert q.INSERT_PENDING_SQL not in db.executed and db.rows == []
    with pytest.raises(HTTPException) as leitura:
        q.usage_today(db.conn(), user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC)
    assert leitura.value.status_code == 503  # a tela do L5b também recebe o 503, nunca um 500


def test_usuario_inexistente_e_404(db):
    with pytest.raises(HTTPException) as erro:
        _reservar(db, user_id=999)
    assert erro.value.status_code == 404 and db.rows == []


def test_textos_sao_cortados_no_tamanho_da_coluna_e_contagem_invalida_recusa(db):
    usage_id = _reservar(db, prompt_version="v" * 90, model="  m" + "x" * 200)
    assert len(db.row(usage_id)["prompt_version"]) == q._TEXT_COLUMN_MAX["prompt_version"]
    assert db.row(usage_id)["model"].startswith("mx") and len(db.row(usage_id)["model"]) == q._TEXT_COLUMN_MAX["model"]
    with pytest.raises(ValueError):
        _reservar(db, image_count=-1)
    with pytest.raises(ValueError):
        _reservar(db, feature="meal_photo")


def test_job_id_e_gravado_uma_vez(db):
    usage_id = _reservar(db)
    assert q.attach_job(db.conn(), usage_id, "job-1") is True
    assert q.attach_job(db.conn(), usage_id, "job-2") is False
    assert db.row(usage_id)["job_id"] == "job-1"


# ---------------------------------------------------------------------------
# Fechamento pelo worker
# ---------------------------------------------------------------------------


def test_worker_fecha_com_modelo_efetivo_tokens_e_custo_do_motor(db):
    usage_id = _reservar(db, model="gpt-5.4-mini")
    assert q.finish_usage(usage_id, status=AiUsageStatus.SUCCEEDED, model="gpt-5.4-mini-2026-03-17",
                          tokens_in=2000, tokens_out=500, conn=db.conn()) is True
    linha = db.row(usage_id)
    assert linha["status"] == "succeeded" and linha["model"] == "gpt-5.4-mini-2026-03-17"
    assert (linha["tokens_in"], linha["tokens_out"]) == (2000, 500)
    assert linha["cost_usd"] == ai.estimate_cost_usd("gpt-5.4-mini", 2000, 500) == Decimal("0.003750")
    assert linha["finished_at"] == db.now and linha["error_code"] is None


def test_sem_modelo_no_relato_usa_o_da_reserva_e_modelo_sem_preco_fica_null(db):
    reservado = _reservar(db, model="gpt-5.4-nano")
    assert q.finish_usage(reservado, status="succeeded", tokens_in=1000, tokens_out=1000, cached_tokens_in=500,
                          conn=db.conn())
    assert db.row(reservado)["model"] == "gpt-5.4-nano"
    assert db.row(reservado)["cost_usd"] == ai.estimate_cost_usd("gpt-5.4-nano", 1000, 1000, 500)
    sem_preco = _reservar(db, model="modelo-novo")
    assert q.finish_usage(sem_preco, status="succeeded", tokens_in=10, tokens_out=10, conn=db.conn())
    assert db.row(sem_preco)["cost_usd"] is None and db.row(sem_preco)["tokens_in"] == 10


def test_falha_do_worker_grava_o_codigo_e_nao_reabre(db):
    usage_id = _reservar(db, model="gpt-5.4-mini")
    assert q.finish_usage(usage_id, status=AiUsageStatus.FAILED, error_code="OPENAI_TIMEOUT", conn=db.conn())
    linha = db.row(usage_id)
    assert linha["status"] == "failed" and linha["error_code"] == "OPENAI_TIMEOUT" and linha["cost_usd"] is None
    # Outro relato (entrega duplicada) não sobrescreve um fechamento de worker.
    assert q.finish_usage(usage_id, status="succeeded", tokens_in=1, tokens_out=1, conn=db.conn()) is False
    assert db.row(usage_id)["status"] == "failed"


def test_relato_do_worker_vence_a_presuncao_do_sweep(db):
    usage_id = db.add(status=AiUsageStatus.FAILED, error_code=async_jobs.WORKER_LOST_ERROR_CODE, model="gpt-5.4-mini")
    assert q.finish_usage(usage_id, status="succeeded", tokens_in=100, tokens_out=50, conn=db.conn()) is True
    assert db.row(usage_id)["status"] == "succeeded" and db.row(usage_id)["error_code"] is None
    concluida = db.add(status=AiUsageStatus.SUCCEEDED)
    assert q.finish_usage(concluida, status="failed", error_code="x", conn=db.conn()) is False


@pytest.mark.parametrize("status", ["pending", "done"])
def test_status_invalido_nao_levanta_nem_grava(db, status):
    usage_id = _reservar(db)
    assert q.finish_usage(usage_id, status=status, conn=db.conn()) is False
    assert db.row(usage_id)["status"] == "pending"


def test_fechamento_nunca_levanta(db, monkeypatch):
    assert q.finish_usage(None, status="succeeded") is False
    assert q.finish_usage(999, status="succeeded", conn=db.conn()) is False  # linha inexistente
    db.fail_on = "UPDATE"
    usage_id = db.add()
    assert q.finish_usage(usage_id, status="succeeded", conn=db.conn()) is False

    @contextmanager
    def banco_fora():
        raise RuntimeError("Data API indisponível")
        yield  # pragma: no cover

    monkeypatch.setattr(q, "transaction", banco_fora)
    assert q.finish_usage(usage_id, status="succeeded") is False  # sem conn: abre a própria transação


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def test_sweep_so_vale_para_feature_com_job_type_registrado(db, monkeypatch):
    """Feature cujo job type ainda não está em `JOB_TYPE_LAMBDA` (antes do lote
    da rota dela) não tem prazo: o sweep não a toca. Desde o L5b o texto da
    refeição está registrado; o teste tira os da nutrição para provar a regra."""
    da_nutricao = set(ai.FEATURE_JOB_TYPE.values())
    monkeypatch.setattr(
        async_jobs, "JOB_TYPE_LAMBDA", {k: v for k, v in async_jobs.JOB_TYPE_LAMBDA.items() if k not in da_nutricao}
    )
    db.add(started_at=AGORA_UTC - timedelta(days=1))
    assert q.stale_after_seconds() == {}
    assert q.sweep_stale_pending(db.conn()) == 0 and db.rows[0]["status"] == "pending"


def test_prazo_do_sweep_e_o_do_worker_perdido_do_m73(jobs_registrados):
    prazos = q.stale_after_seconds()
    assert prazos == {
        f: async_jobs.LAMBDA_TIMEOUT_SECONDS[E8[f]] + async_jobs.WORKER_LOST_GRACE_SECONDS for f in AiFeature
    }
    for feature, segundos in prazos.items():
        assert segundos == async_jobs.worker_lost_deadline_seconds(ai.FEATURE_JOB_TYPE[feature])


def test_job_type_registrado_roda_na_lambda_da_decisao_e8():
    """Paridade para o L5b/L6: quando o job type entrar em `JOB_TYPE_LAMBDA`, é na
    Lambda que o plano decidiu (E8) — é dela o timeout do sweep."""
    for feature, job_type in ai.FEATURE_JOB_TYPE.items():
        if job_type in async_jobs.JOB_TYPE_LAMBDA:
            assert async_jobs.JOB_TYPE_LAMBDA[job_type] == E8[feature], feature
        assert E8[feature] in async_jobs.LAMBDA_TIMEOUT_SECONDS


def test_sweep_fecha_so_pending_vencida_de_cada_feature(db, jobs_registrados):
    prazos = q.stale_after_seconds()
    texto_vencida = db.add(started_at=db.now - timedelta(seconds=prazos[AiFeature.MEAL_PARSE] + 1))
    texto_no_prazo = db.add(started_at=db.now - timedelta(seconds=prazos[AiFeature.MEAL_PARSE] - 1))
    # O mesmo tempo que venceu o texto ainda está no prazo da imagem (Lambda de 180 s).
    foto_no_prazo = db.add(feature=AiFeature.LABEL_PHOTO,
                           started_at=db.now - timedelta(seconds=prazos[AiFeature.MEAL_PARSE] + 1))
    foto_vencida = db.add(feature=AiFeature.LABEL_PHOTO,
                          started_at=db.now - timedelta(seconds=prazos[AiFeature.LABEL_PHOTO] + 1))
    concluida = db.add(status=AiUsageStatus.SUCCEEDED, started_at=db.now - timedelta(days=1))
    assert q.sweep_stale_pending(db.conn()) == 2
    for usage_id in (texto_vencida, foto_vencida):
        linha = db.row(usage_id)
        assert linha["status"] == "failed" and linha["error_code"] == async_jobs.WORKER_LOST_ERROR_CODE
        assert linha["finished_at"] == db.now
    assert [db.row(i)["status"] for i in (texto_no_prazo, foto_no_prazo, concluida)] == ["pending", "pending",
                                                                                         "succeeded"]
    assert q.sweep_stale_pending(db.conn()) == 0  # idempotente


# ---------------------------------------------------------------------------
# Entradas de alto nível (transação própria)
# ---------------------------------------------------------------------------


def test_open_usage_e_uma_transacao_curta_sem_sweep(banco, jobs_registrados):
    vencida = banco.add(started_at=banco.now - timedelta(days=1))
    usage_id = q.open_usage(user_id=USUARIO, feature=AiFeature.MEAL_PARSE, prompt_version="p1", model="gpt-5.4-mini",
                            now_utc=AGORA_UTC)
    assert banco.row(usage_id)["status"] == "pending"
    assert q.SWEEP_SQL not in banco.executed  # o sweep fica fora do caminho do usuário
    assert banco.row(vencida)["status"] == "pending"


def test_finish_usage_varre_depois_de_fechar(banco, jobs_registrados):
    vencida = banco.add(started_at=banco.now - timedelta(days=1))
    usage_id = q.open_usage(user_id=USUARIO, feature="label_photo", image_count=1, now_utc=AGORA_UTC)
    assert banco.row(usage_id)["image_count"] == 1
    assert q.finish_usage(usage_id, status="succeeded", tokens_in=10, tokens_out=10) is True
    assert banco.row(usage_id)["status"] == "succeeded"
    assert banco.row(vencida)["status"] == "failed"
    assert banco.executed.index(q.FINISH_SQL) < banco.executed.index(q.SWEEP_SQL)


def test_com_conn_do_chamador_o_fechamento_nao_varre(banco, jobs_registrados):
    """O sweep numa segunda conexão esperaria a trava da linha que a transação do
    chamador ainda não commitou: com `conn`, `finish_usage` só fecha."""
    vencida = banco.add(started_at=banco.now - timedelta(days=1))
    usage_id = q.open_usage(user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC)
    assert q.finish_usage(usage_id, status="succeeded", tokens_in=1, tokens_out=1, conn=banco.conn()) is True
    assert q.SWEEP_SQL not in banco.executed and banco.row(vencida)["status"] == "pending"


def test_fechamento_que_falha_nao_varre(banco, jobs_registrados):
    vencida = banco.add(started_at=banco.now - timedelta(days=1))
    assert q.finish_usage(999, status="succeeded") is False  # linha inexistente: fecha nada, mas commita
    assert banco.row(vencida)["status"] == "failed"  # o sweep roda depois do próprio commit
    banco.fail_on = "SELECT model"
    outra = banco.add(started_at=banco.now - timedelta(days=1))
    assert q.finish_usage(1, status="succeeded") is False  # erro no fechamento: sem sweep
    assert banco.row(outra)["status"] == "pending"


def test_sweep_quebrado_nao_impede_o_fechamento(banco, jobs_registrados, monkeypatch):
    def quebrado(_conn):
        raise RuntimeError("sweep falhou")

    monkeypatch.setattr(q, "sweep_stale_pending", quebrado)
    usage_id = q.open_usage(user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC)
    assert q.finish_usage(usage_id, status="failed", error_code="OPENAI_TIMEOUT") is True
    assert banco.row(usage_id)["error_code"] == "OPENAI_TIMEOUT"


def test_open_usage_propaga_o_429(banco):
    for _ in range(ai.MEAL_PARSE_DAILY_QUOTA):
        banco.add()
    with pytest.raises(HTTPException) as erro:
        q.open_usage(user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC)
    assert erro.value.status_code == 429


def test_link_job_nunca_levanta(banco):
    usage_id = q.open_usage(user_id=USUARIO, feature=AiFeature.MEAL_PARSE, now_utc=AGORA_UTC)
    assert q.link_job(usage_id, "job-9") is True and banco.row(usage_id)["job_id"] == "job-9"
    assert q.link_job(None, "job-9") is False and q.link_job(usage_id, None) is False
    banco.fail_on = "SET job_id"
    assert q.link_job(usage_id, "job-10") is False

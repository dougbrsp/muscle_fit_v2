"""Rota da leitura da refeição por IA (`app/nutrition_meal_parse.py`, L5b) sem
banco nem AWS: contrato do L5a (cota -> job -> vínculo -> invoke com o
`ai_usage_id` -> 202), 400 do motor antes de gastar a cota, 429 da cota, 503
com a linha fechada quando o despacho falha, acesso (próprio; admin/PT para o
aluno; terceiro 403) e o texto fora do pedido gravado no DynamoDB."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import nutrition_meal_parse as rota
from app.domain.nutrition import diary
from app.domain.nutrition import meal_parse as mp
from app.domain.nutrition.ai_usage import MEAL_PARSE_JOB_TYPE, AiFeature, AiUsageStatus, QuotaStatus
from app.nutrition_ai_quota import quota_exceeded_error
from app.nutrition_meal_parse_ai import PROMPT_VERSION
from app.security import Identity, get_current_identity

ALUNO, OUTRO = 2, 4


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):  # pragma: no cover - só o fetch_target_user usa
        self.sql = sql

    def fetchone(self):
        return {"id": OUTRO, "username": "outro", "full_name": "Outro"}


class _Conexao:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor()


@pytest.fixture
def chamadas(monkeypatch):
    registro: dict = {"open": [], "jobs": [], "link": [], "invoke": [], "finish": []}
    monkeypatch.setattr(rota, "get_conn", lambda: _Conexao())
    monkeypatch.setattr(rota, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(rota, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(rota.quota, "open_usage", lambda **k: registro["open"].append(k) or 55)
    monkeypatch.setattr(rota.quota, "link_job", lambda usage_id, job_id: registro["link"].append((usage_id, job_id)))
    monkeypatch.setattr(rota.quota, "finish_usage", lambda usage_id, **k: registro["finish"].append((usage_id, k)))
    monkeypatch.setattr(
        rota.quota, "usage_today", lambda conn, *, user_id, feature: QuotaStatus(AiFeature.MEAL_PARSE, 30, 3)
    )

    def _create_job(job_type, request=None, *, owner_user_id):
        registro["jobs"].append({"job_type": job_type, "request": request, "owner": owner_user_id})
        return "job-1"

    monkeypatch.setattr(rota.async_jobs, "create_job", _create_job)
    monkeypatch.setattr(rota.async_jobs, "invoke_async", lambda env, payload: registro["invoke"].append((env, payload)))
    return registro


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _corpo(**extra) -> dict:
    return {"text": "arroz, feijão e um bife", "local_date": "2026-10-18", "meal_slot": "lunch", **extra}


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert "/nutrition/meals/parse" in caminhos and "/nutrition/meals/parse/quota" in caminhos
    assert TestClient(app, raise_server_exceptions=False).post("/nutrition/meals/parse", json={}).status_code in (401, 403)


def test_202_segue_o_contrato_do_l5a(chamadas):
    resposta = _client().post("/nutrition/meals/parse", json=_corpo(text="  arroz,  feijão e um bife "))
    assert resposta.status_code == 202, resposta.text
    assert resposta.json() == {"job_id": "job-1", "status": "pending",
                               "quota": {"feature": "meal_parse", "limit": 30, "used": 3, "remaining": 27,
                                         "allowed": True}}
    [aberta] = chamadas["open"]
    assert aberta == {"user_id": ALUNO, "feature": AiFeature.MEAL_PARSE, "prompt_version": PROMPT_VERSION}
    [job] = chamadas["jobs"]
    assert job["job_type"] == MEAL_PARSE_JOB_TYPE and job["owner"] == ALUNO
    # O pedido de auditoria no DynamoDB não leva o texto, só o tamanho.
    assert "text" not in job["request"]["payload"] and job["request"]["payload"]["text_chars"] == len("arroz, feijão e um bife")
    assert chamadas["link"] == [(55, "job-1")]
    [(env, evento)] = chamadas["invoke"]
    assert env == "WORKOUT_AI_FUNCTION_NAME"
    assert evento == {"job_id": "job-1", "job_type": MEAL_PARSE_JOB_TYPE, "user_id": ALUNO, "ai_usage_id": 55,
                      "payload": {"local_date": "2026-10-18", "meal_slot": "lunch", "diary_user_id": ALUNO,
                                  "text": "arroz, feijão e um bife", "job_id": "job-1"}}
    assert chamadas["finish"] == []


@pytest.mark.parametrize(
    ("extra", "codigo"),
    [({"text": "   "}, mp.ERR_TEXT_EMPTY), ({"text": "a" * (mp.TEXT_MAX_CHARS + 1)}, mp.ERR_TEXT_TOO_LONG),
     ({"meal_slot": "brunch"}, diary.ERR_INVALID_MEAL_SLOT)],
)
def test_400_do_motor_antes_de_gastar_a_cota(chamadas, extra, codigo):
    resposta = _client().post("/nutrition/meals/parse", json=_corpo(**extra))
    assert resposta.status_code == 400
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "invalid_meal_parse" and [e["code"] for e in detalhe["errors"]] == [codigo]
    assert chamadas["open"] == [] and chamadas["jobs"] == []


def test_slot_e_opcional(chamadas):
    assert _client().post("/nutrition/meals/parse", json=_corpo(meal_slot=None)).status_code == 202
    assert chamadas["invoke"][0][1]["payload"]["meal_slot"] is None


def test_429_da_cota_sem_job(chamadas, monkeypatch):
    def _estourou(**k):
        raise quota_exceeded_error(QuotaStatus(AiFeature.MEAL_PARSE, 30, 30))

    monkeypatch.setattr(rota.quota, "open_usage", _estourou)
    resposta = _client().post("/nutrition/meals/parse", json=_corpo())
    assert resposta.status_code == 429
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "ai_quota_exceeded" and detalhe["remaining"] == 0 and detalhe["allowed"] is False
    assert chamadas["jobs"] == [] and chamadas["invoke"] == []


@pytest.mark.parametrize("falha", ["create_job", "invoke_async"])
def test_despacho_que_falha_fecha_a_linha_e_responde_503(chamadas, monkeypatch, falha):
    def _quebra(*a, **k):
        raise RuntimeError("DynamoDB/Lambda fora")

    monkeypatch.setattr(rota.async_jobs, falha, _quebra)
    resposta = _client().post("/nutrition/meals/parse", json=_corpo())
    assert resposta.status_code == 503 and resposta.json()["detail"]["error"] == mp.ERR_DISPATCH_FAILED
    assert chamadas["finish"] == [(55, {"status": AiUsageStatus.FAILED, "error_code": mp.ERR_DISPATCH_FAILED})]


def test_admin_ou_pt_le_para_o_aluno_e_a_cota_e_de_quem_pede(chamadas):
    for papel in ("admin", "personal_trainer"):
        resposta = _client(role=papel, user_id=1).post("/nutrition/meals/parse", json=_corpo(user_id=OUTRO))
        assert resposta.status_code == 202, resposta.text
    assert [a["user_id"] for a in chamadas["open"]] == [1, 1]
    assert {e["payload"]["diary_user_id"] for _, e in chamadas["invoke"]} == {OUTRO}
    assert {e["user_id"] for _, e in chamadas["invoke"]} == {1}


def test_terceiro_sem_papel_e_403_sem_gastar_a_cota(chamadas):
    resposta = _client().post("/nutrition/meals/parse", json=_corpo(user_id=OUTRO))
    assert resposta.status_code == 403 and chamadas["open"] == []


def test_a_cota_do_dia_para_a_tela(chamadas):
    resposta = _client().get("/nutrition/meals/parse/quota")
    assert resposta.status_code == 200
    assert resposta.json() == {"feature": "meal_parse", "limit": 30, "used": 3, "remaining": 27, "allowed": True}


def test_cota_indisponivel_nao_derruba_o_202(chamadas, monkeypatch):
    def _sem_tabela(conn, **k):
        raise HTTPException(status_code=503, detail="sem 085")

    monkeypatch.setattr(rota.quota, "usage_today", _sem_tabela)
    resposta = _client().post("/nutrition/meals/parse", json=_corpo())
    assert resposta.status_code == 202 and resposta.json()["quota"] is None


def test_contrato_estrito():
    resposta = TestClient(_app_sem_banco()).post("/nutrition/meals/parse", json=_corpo(extra="x"))
    assert resposta.status_code == 422


def _app_sem_banco() -> FastAPI:
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    return app

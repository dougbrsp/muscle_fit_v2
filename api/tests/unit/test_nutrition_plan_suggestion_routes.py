"""Rotas da sugestão de metas (`app/nutrition_plan_suggestion.py`, N6) e da prévia do formulário
(`POST /nutrition/plan-preview`), sem banco, DynamoDB nem Lambda reais: dono, terceiro (403), admin/PT, cota (429), perfil
incompleto antes da cota, ordem do despacho e o fechamento como `failed` quando o despacho falha."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import async_jobs, nutrition_plan_suggestion as rota, nutrition_plans
from app.domain.nutrition import plan_suggestion as ps
from app.domain.nutrition.ai_usage import PLAN_SUGGESTION_JOB_TYPE, AiFeature, AiUsageStatus, QuotaStatus
from app.nutrition_ai_quota import quota_exceeded_error
from app.nutrition_plan_suggestion_ai import PROMPT_VERSION
from app.security import Identity, get_current_identity

USUARIO = 7
ALUNO = 12
URL = "/nutrition/plan-suggestion"
CORPO = {"current": {"strategy": "cutting", "protein_g_per_kg": 2.6, "manual_targets": False}}


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor()


class _Despacho:
    def __init__(self, monkeypatch, *, cota=True, falha_job=False, falha_invoke=False, faltas=()):
        self.eventos: list[tuple] = []
        self.cota = cota
        self.falha_job = falha_job
        self.falha_invoke = falha_invoke
        monkeypatch.setattr(rota, "get_conn", lambda: _Conn())
        monkeypatch.setattr(rota, "require_nutrition_tables", lambda cur: None)
        monkeypatch.setattr(rota, "fetch_target_user", self.fetch_user)
        monkeypatch.setattr(rota, "_missing_subject_codes", lambda conn, cur, aluno: faltas)
        monkeypatch.setattr(rota.nutrition_ai_quota, "open_usage", self.open_usage)
        monkeypatch.setattr(rota.nutrition_ai_quota, "finish_usage", self.finish_usage)
        monkeypatch.setattr(rota.nutrition_ai_quota, "link_job", self.link_job)
        monkeypatch.setattr(async_jobs, "create_job", self.create_job)
        monkeypatch.setattr(async_jobs, "invoke_async", self.invoke)

    def fetch_user(self, cur, user_id):
        self.eventos.append(("user", user_id))
        return {"id": user_id}

    def open_usage(self, **kw):
        self.eventos.append(("reserva", kw))
        if not self.cota:
            raise quota_exceeded_error(QuotaStatus(AiFeature.PLAN_SUGGESTION, 5, 5))
        return 55

    def finish_usage(self, usage_id, **kw):
        self.eventos.append(("fecha", usage_id, kw))
        return True

    def link_job(self, usage_id, job_id):
        self.eventos.append(("vinculo", usage_id, job_id))
        return True

    def create_job(self, job_type, *, request, owner_user_id):
        self.eventos.append(("job", job_type, request, owner_user_id))
        if self.falha_job:
            raise RuntimeError("dynamo fora")
        return "job-1"

    def invoke(self, nome, evento):
        self.eventos.append(("invoke", nome, evento))
        if self.falha_invoke:
            raise RuntimeError("invoke fora")

    def nomes(self):
        return [e[0] for e in self.eventos]


def _cliente(user_id=USUARIO, role="regular"):
    app = FastAPI()
    app.include_router(rota.router)
    app.include_router(nutrition_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app)


def test_dono_pede_a_propria_sugestao(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _cliente().post(URL, json=CORPO)
    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["job_id"] == "job-1" and corpo["status"] == "pending"
    assert corpo["poll_timeout_seconds"] == async_jobs.worker_lost_deadline_seconds(PLAN_SUGGESTION_JOB_TYPE)
    assert despacho.nomes() == ["reserva", "job", "vinculo", "invoke"]

    reserva = despacho.eventos[0][1]
    assert reserva["user_id"] == USUARIO and reserva["feature"] == AiFeature.PLAN_SUGGESTION
    assert reserva["prompt_version"] == PROMPT_VERSION

    _, tipo, request, dono = despacho.eventos[1]
    assert tipo == PLAN_SUGGESTION_JOB_TYPE and dono == USUARIO
    # Auditoria no DynamoDB sem o estado do formulário.
    assert request == {"target_user_id": USUARIO, "ai_usage_id": 55}

    _, nome, evento = despacho.eventos[3]
    assert nome == "WORKOUT_AI_FUNCTION_NAME"
    assert evento["job_type"] == PLAN_SUGGESTION_JOB_TYPE and evento["ai_usage_id"] == 55
    assert evento["payload"]["target_user_id"] == USUARIO
    assert evento["payload"]["current"]["strategy"] == "cutting"


def test_terceiro_recebe_403_sem_gastar_cota(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _cliente().post(URL, json={**CORPO, "user_id": ALUNO})
    assert resposta.status_code == 403
    assert despacho.eventos == []


def test_admin_pede_para_o_aluno_com_a_propria_cota(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _cliente(role="admin").post(URL, json={**CORPO, "user_id": ALUNO})
    assert resposta.status_code == 202
    assert despacho.eventos[0] == ("user", ALUNO)
    assert despacho.eventos[1][1]["user_id"] == USUARIO
    assert [e for e in despacho.eventos if e[0] == "invoke"][0][2]["payload"]["target_user_id"] == ALUNO


def test_cota_esgotada_responde_429_sem_job(monkeypatch):
    despacho = _Despacho(monkeypatch, cota=False)
    resposta = _cliente().post(URL, json=CORPO)
    assert resposta.status_code == 429
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "ai_quota_exceeded" and detalhe["limit"] == 5 and detalhe["remaining"] == 0
    assert despacho.nomes() == ["reserva"]


def test_perfil_incompleto_responde_422_sem_gastar_cota(monkeypatch):
    despacho = _Despacho(monkeypatch, faltas=("missing_weight",))
    resposta = _cliente().post(URL, json=CORPO)
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "nutrition_subject_incomplete" and detalhe["missing"][0]["code"] == "missing_weight"
    assert despacho.eventos == []


@pytest.mark.parametrize("falha", ["falha_job", "falha_invoke"])
def test_despacho_que_falha_fecha_a_linha_e_responde_503(monkeypatch, falha):
    despacho = _Despacho(monkeypatch, **{falha: True})
    resposta = _cliente().post(URL, json=CORPO)
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["error"] == ps.ERR_SUGGESTION_DISPATCH_FAILED
    [fecha] = [e for e in despacho.eventos if e[0] == "fecha"]
    assert fecha[1] == 55 and fecha[2]["status"] == AiUsageStatus.FAILED


def test_corpo_com_campo_a_mais_e_recusado(monkeypatch):
    despacho = _Despacho(monkeypatch)
    resposta = _cliente().post(URL, json={"current": {"protein_g": 200}})
    assert resposta.status_code == 422
    assert despacho.eventos == []


def test_uso_da_cota_e_de_quem_pede(monkeypatch):
    monkeypatch.setattr(rota, "get_conn", lambda: _Conn())
    vistos = []

    def _usage(conn, *, user_id, feature):
        vistos.append((user_id, feature))
        return QuotaStatus(feature, 5, 2)

    monkeypatch.setattr(rota.nutrition_ai_quota, "usage_today", _usage)
    resposta = _cliente().get(f"{URL}/usage")
    assert resposta.status_code == 200
    assert resposta.json()["remaining"] == 3
    assert vistos == [(USUARIO, AiFeature.PLAN_SUGGESTION)]


def test_router_montado_no_app_real():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert URL in caminhos and f"{URL}/usage" in caminhos and "/nutrition/plan-preview" in caminhos
    cliente = TestClient(app, raise_server_exceptions=False)
    assert cliente.get(f"{URL}/usage").status_code in (401, 403)
    assert cliente.post(URL, json=CORPO).status_code in (401, 403)
    assert cliente.post("/nutrition/plan-preview", json={"strategy": "maintenance"}).status_code in (401, 403)


# ---------------------------------------------------------------------------
# Prévia do formulário ainda não salvo
# ---------------------------------------------------------------------------


class _Previa:
    def __init__(self, monkeypatch, computable=True):
        self.calculados = []
        comp = type("C", (), {})()
        comp.day = "hoje"
        comp.target = type("T", (), {"computable": computable, "limitations": ("missing_weight",)})()
        monkeypatch.setattr(nutrition_plans, "get_conn", lambda: _Conn())
        monkeypatch.setattr(nutrition_plans, "require_nutrition_tables", lambda cur: None)
        monkeypatch.setattr(nutrition_plans, "fetch_target_user", lambda cur, uid: {"id": uid})
        monkeypatch.setattr(nutrition_plans, "_today_for", lambda conn, uid: "hoje")
        monkeypatch.setattr(nutrition_plans, "_past_training", lambda cur, uid, settings, hoje: [])

        def _semana(cur, aluno, settings, hoje):
            self.calculados.append((aluno, settings))
            return [comp]

        monkeypatch.setattr(nutrition_plans, "_compute_week", _semana)
        monkeypatch.setattr(
            nutrition_plans, "_preview_payload", lambda row, user, semana, hoje, passado: {"plan": row, "user": user}
        )


def test_previa_calcula_sem_gravar_e_sem_gastar_cota(monkeypatch):
    previa = _Previa(monkeypatch)
    resposta = _cliente().post("/nutrition/plan-preview", json={"strategy": "maintenance"})
    assert resposta.status_code == 200
    assert resposta.json() == {"plan": None, "user": {"id": USUARIO}}
    [(aluno, settings)] = previa.calculados
    assert aluno == USUARIO and settings.strategy.value == "maintenance"


def test_previa_fora_da_faixa_responde_400_com_o_codigo_do_motor(monkeypatch):
    previa = _Previa(monkeypatch)
    resposta = _cliente().post("/nutrition/plan-preview", json={"strategy": "maintenance", "protein_g_per_kg": 9})
    assert resposta.status_code == 400
    assert resposta.json()["detail"]["errors"][0]["code"] == "param_out_of_range:protein_g_per_kg"
    assert previa.calculados == []


def test_previa_de_terceiro_e_403(monkeypatch):
    previa = _Previa(monkeypatch)
    resposta = _cliente().post("/nutrition/plan-preview", json={"strategy": "maintenance", "user_id": ALUNO})
    assert resposta.status_code == 403
    assert previa.calculados == []


def test_previa_com_perfil_incompleto_responde_422(monkeypatch):
    _Previa(monkeypatch, computable=False)

    def _incompleto(comp):
        return HTTPException(status_code=422, detail={"error": "nutrition_subject_incomplete"})

    monkeypatch.setattr(nutrition_plans, "_subject_incomplete", _incompleto)
    resposta = _cliente().post("/nutrition/plan-preview", json={"strategy": "maintenance"})
    assert resposta.status_code == 422
    assert json.dumps(resposta.json())

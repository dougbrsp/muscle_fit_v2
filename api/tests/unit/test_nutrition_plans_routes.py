"""Rotas do plano nutricional (`app/nutrition_plans.py`, L2b) sem banco real:
acesso (admin/PT × aluno), validação com o código do motor (400), rascunho
editável só enquanto rascunho (409), preview sem gravar (422 lista os dados
faltantes), publicação com concorrência otimista (`expected_current_id` +
`FOR UPDATE`, 409 `publish_conflict`) que materializa a janela do motor,
recálculo na janela do motor e o plano em vigor hoje do próprio usuário. 503
com as tabelas ausentes. O router é conferido também no `app.main.app` real.

Banco em memória: `tests/support/nutrition_fake.py`."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import Sex
from app.domain.nutrition.phases import CUT_STOP_BODY_FAT_PERCENT
from app.nutrition_schema import tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeCursor, FakeNutritionDB

HOJE = date(2026, 10, 17)  # sábado: a semana atual vai de 12/10 a 18/10
ALUNO = 2


@pytest.fixture
def db(monkeypatch):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, HOJE - timedelta(days=3), 80)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _client(role: str = "admin", user_id: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin(db):
    return _client("admin")


def _novo_rascunho(cliente, **corpo) -> dict:
    resposta = cliente.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance", **corpo})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _publicar(cliente, plano_id: int, esperado):
    return cliente.post(f"/nutrition/plans/{plano_id}/publish", json={"expected_current_id": esperado})


# ---------------------------------------------------------------------------
# Router no app real (o try/except do main.py engoliria erro de import)
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/nutrition/me/plan", "/nutrition/plans", "/nutrition/plans/{plan_id}/publish",
                    "/nutrition/targets/recompute"):
        assert caminho in caminhos, caminho
    resposta = TestClient(app, raise_server_exceptions=False).get("/nutrition/me/plan")
    assert resposta.status_code in (401, 403)  # existe e exige identidade (não 404)


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "caminho", "corpo"),
    [
        ("get", f"/nutrition/plans?user_id={ALUNO}", None),
        ("post", "/nutrition/plans", {"user_id": ALUNO, "strategy": "maintenance"}),
        ("put", "/nutrition/plans/1", {"strategy": "maintenance"}),
        ("delete", "/nutrition/plans/1", None),
        ("post", "/nutrition/plans/1/preview", None),
        ("post", "/nutrition/plans/1/publish", {"expected_current_id": None}),
        ("post", "/nutrition/targets/recompute", {"user_id": ALUNO, "date_from": HOJE.isoformat()}),
    ],
)
def test_aluno_comum_nao_gerencia_planos(db, metodo, caminho, corpo):
    cliente = _client("regular", user_id=ALUNO)
    kwargs = {"json": corpo} if corpo is not None else {}
    assert getattr(cliente, metodo)(caminho, **kwargs).status_code == 403


def test_personal_trainer_gerencia_qualquer_aluno_sem_vinculo(db):
    cliente = _client("personal_trainer", user_id=1)
    assert _novo_rascunho(cliente)["user_id"] == ALUNO


# ---------------------------------------------------------------------------
# Rascunho
# ---------------------------------------------------------------------------


def test_cria_rascunho_com_os_defaults_do_motor(admin, db):
    plano = _novo_rascunho(admin, strategy="cutting")
    base = st.StrategyParams.defaults_for(st.Strategy.CUTTING)
    assert plano["state"] == "draft" and plano["status"] == "draft"
    assert plano["protein_g_per_kg"] == base.protein_g_per_kg
    assert plano["weekly_rate_share"] == base.weekly_rate_share
    assert plano["protein_basis"] == "fat_free_mass"
    assert plano["effective_from"] is None
    assert db.targets == {}


@pytest.mark.parametrize(
    ("corpo", "codigo"),
    [
        ({"protein_g_per_kg": st.MAINT_PROTEIN_G_PER_KG_RANGE[1] + 1}, "param_out_of_range:protein_g_per_kg"),
        ({"weekly_rate_share": st.CUT_WEEKLY_RATE_SHARE_DEFAULT}, "param_not_applicable:weekly_rate_share"),
        ({"carb_cycling_enabled": True}, "carb_cycling_unavailable"),
        ({"strategy": "keto"}, "invalid_code:strategy"),
    ],
)
def test_validacao_responde_400_com_o_codigo_do_motor(admin, db, corpo, codigo):
    resposta = admin.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance", **corpo})
    assert resposta.status_code == 400
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "invalid_plan"
    assert codigo in [item["code"] for item in detalhe["errors"]]
    assert all(item["label"] for item in detalhe["errors"])
    assert db.plans == {}


def test_campo_desconhecido_e_aluno_inexistente(admin, db):
    assert admin.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance", "x": 1}).status_code == 422
    assert admin.post("/nutrition/plans", json={"user_id": 999, "strategy": "maintenance"}).status_code == 404


def test_lista_os_planos_do_aluno(admin, db):
    _novo_rascunho(admin)
    resposta = admin.get(f"/nutrition/plans?user_id={ALUNO}")
    assert resposta.status_code == 200
    assert resposta.json()["user"]["username"] == "aluno"
    assert [p["state"] for p in resposta.json()["items"]] == ["draft"]


def test_edita_e_apaga_so_rascunho(admin, db):
    rascunho = _novo_rascunho(admin)
    editado = admin.put(f"/nutrition/plans/{rascunho['id']}", json={"strategy": "bulking", "advanced": True})
    assert editado.status_code == 200 and editado.json()["surplus_share"] == st.BULK_SURPLUS_SHARE_DEFAULT_ADVANCED
    publicado = db.add_plan(ALUNO, status="published", effective_from=HOJE)
    assert admin.put(f"/nutrition/plans/{publicado}", json={"strategy": "bulking"}).json()["detail"]["error"] == "plan_not_draft"
    assert admin.delete(f"/nutrition/plans/{publicado}").status_code == 409
    assert admin.delete(f"/nutrition/plans/{rascunho['id']}").status_code == 200
    assert rascunho["id"] not in db.plans
    assert admin.delete("/nutrition/plans/12345").status_code == 404


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_calcula_a_semana_sem_gravar(admin, db):
    rascunho = _novo_rascunho(admin)
    db.writes.clear()
    resposta = admin.post(f"/nutrition/plans/{rascunho['id']}/preview")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert db.writes == [] and db.targets == {}
    assert corpo["today"] == HOJE.isoformat()
    assert [d["local_date"] for d in corpo["week"]] == [(date(2026, 10, 12) + timedelta(days=i)).isoformat() for i in range(7)]
    assert corpo["subject"]["sex"] == "male" and corpo["subject"]["weight_kg"] == 80.0
    assert corpo["target"]["kcal"] > 0 and corpo["target"]["bmr"]["formula"] == "mifflin_st_jeor"
    assert corpo["target"]["local_date"] == HOJE.isoformat()
    domingo = corpo["week"][-1]
    assert domingo["training_known"] is False
    assert "training_unknown_future_day" in [i["code"] for i in domingo["limitations"]]
    # Antes da primeira pesagem (12/10 e 13/10) a meta não é calculável: None, nunca 0.
    assert corpo["week"][0]["kcal"] is None


def test_preview_sem_dados_responde_422_com_o_que_falta(admin, db):
    rascunho = admin.post("/nutrition/plans", json={"user_id": 3, "strategy": "maintenance"}).json()
    resposta = admin.post(f"/nutrition/plans/{rascunho['id']}/preview")
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "nutrition_subject_incomplete"
    codigos = [item["code"] for item in detalhe["missing"]]
    assert codigos[:4] == ["missing_sex", "missing_or_invalid_height", "missing_weight", "missing_age"]
    assert "missing_activity_level" in codigos
    assert "missing_bmr" not in codigos  # derivada, não é dado a preencher


# ---------------------------------------------------------------------------
# Publicação
# ---------------------------------------------------------------------------


def test_publica_substitui_a_vigente_e_materializa_de_hoje_em_diante(admin, db):
    antigo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=20),
                         published_at=datetime(2026, 9, 27, 9))
    ontem = HOJE - timedelta(days=1)
    db.targets[(ALUNO, ontem)] = {"user_id": ALUNO, "local_date": ontem, "plan_id": antigo, "marcador": "intacto"}
    rascunho = _novo_rascunho(admin, strategy="bulking")
    resposta = _publicar(admin, rascunho["id"], antigo)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["plan"]["state"] == "current" and corpo["plan"]["effective_from"] == HOJE.isoformat()
    assert db.plans[antigo]["superseded_at"] == db.now
    assert ("current", ALUNO) in db.locks  # a vigente foi lida com FOR UPDATE
    assert [m["local_date"] for m in corpo["targets"]] == [HOJE.isoformat(), (HOJE + timedelta(days=1)).isoformat()]
    assert all(m["plan_id"] == rascunho["id"] for m in corpo["targets"])
    domingo = (HOJE + timedelta(days=1)).isoformat()
    assert corpo["window"] == {"date_from": HOJE.isoformat(), "date_to": domingo, "create_until": domingo}
    assert db.targets[(ALUNO, ontem)]["marcador"] == "intacto"  # passado intacto


def test_publicacao_atualiza_todas_as_metas_futuras_ja_materializadas(admin, db):
    antigo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=3))
    longe = HOJE + timedelta(days=25)
    recalculo = admin.post("/nutrition/targets/recompute", json={
        "user_id": ALUNO, "date_from": HOJE.isoformat(), "date_to": longe.isoformat()})
    assert recalculo.status_code == 200, recalculo.text
    rascunho = _novo_rascunho(admin, strategy="bulking")
    corpo = _publicar(admin, rascunho["id"], antigo).json()
    assert corpo["window"]["date_to"] == longe.isoformat()
    # Nenhuma meta futura não congelada fica com o plano antigo.
    futuras = [linha for (uid, dia), linha in db.targets.items() if uid == ALUNO and dia >= HOJE]
    assert len(futuras) == 26 and {linha["plan_id"] for linha in futuras} == {rascunho["id"]}


def test_publicar_de_novo_e_409(admin, db):
    rascunho = _novo_rascunho(admin)
    assert _publicar(admin, rascunho["id"], None).status_code == 200
    resposta = _publicar(admin, rascunho["id"], rascunho["id"])
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "plan_not_draft"


def test_concorrencia_otimista_vigente_diferente_da_vista_e_409(admin, db):
    antigo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=5))
    rascunho = _novo_rascunho(admin)
    # A tela viu "sem vigente" (ou outra), mas há a vigente `antigo`.
    for visto in (None, antigo + 100):
        resposta = _publicar(admin, rascunho["id"], visto)
        assert resposta.status_code == 409
        assert resposta.json()["detail"]["error"] == "publish_conflict"
        assert resposta.json()["detail"]["current_id"] == antigo
    assert db.plans[antigo]["superseded_at"] is None and db.plans[rascunho["id"]]["status"] == "draft"
    assert db.targets == {}
    # Com a vigente certa, publica.
    assert _publicar(admin, rascunho["id"], antigo).status_code == 200
    # O campo é obrigatório (pode ser null): sem ele, 422.
    assert admin.post(f"/nutrition/plans/{rascunho['id']}/publish", json={}).status_code == 422


def test_corrida_no_unique_tambem_e_409_e_nada_fica_gravado(admin, db, monkeypatch):
    antigo = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=5))
    rascunho = _novo_rascunho(admin)
    original = FakeCursor.execute

    def execute_com_concorrente(self, sql, params=None):
        original(self, sql, params)
        if sql == plans_mod._SUPERSEDE_SQL:
            # Defesa em profundidade: se algo furar a trava, o UNIQUE da 078 ainda barra.
            self.db.add_plan(ALUNO, status="published", effective_from=HOJE)

    monkeypatch.setattr(FakeCursor, "execute", execute_com_concorrente)
    resposta = _publicar(admin, rascunho["id"], antigo)
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "publish_conflict"
    assert db.plans[antigo]["superseded_at"] is None  # rollback
    assert db.plans[rascunho["id"]]["status"] == "draft"
    assert db.targets == {}


def test_publicar_sem_dados_do_aluno_e_422_sem_gravar(admin, db):
    rascunho = admin.post("/nutrition/plans", json={"user_id": 3, "strategy": "maintenance"}).json()
    resposta = admin.post(f"/nutrition/plans/{rascunho['id']}/publish", json={"expected_current_id": None})
    assert resposta.status_code == 422
    assert db.plans[rascunho["id"]]["status"] == "draft" and db.targets == {}


# ---------------------------------------------------------------------------
# Recálculo e plano do próprio usuário
# ---------------------------------------------------------------------------


def test_recalculo_na_janela_do_motor(admin, db):
    anteontem = {"user_id": ALUNO, "date_from": (HOJE - timedelta(days=2)).isoformat()}
    resposta = admin.post("/nutrition/targets/recompute", json=anteontem)
    assert resposta.status_code == 400 and resposta.json()["detail"]["error"] == "date_from_frozen"
    assert resposta.json()["detail"]["message"]
    sem_plano = admin.post("/nutrition/targets/recompute", json={"user_id": ALUNO, "date_from": HOJE.isoformat()})
    assert sem_plano.status_code == 404 and sem_plano.json()["detail"]["error"] == "no_published_plan"
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=3))
    longo = admin.post("/nutrition/targets/recompute", json={
        "user_id": ALUNO, "date_from": HOJE.isoformat(), "date_to": (HOJE + timedelta(days=90)).isoformat()})
    assert longo.status_code == 400 and longo.json()["detail"]["error"] == "range_too_long"
    invertido = admin.post("/nutrition/targets/recompute", json={
        "user_id": ALUNO, "date_from": HOJE.isoformat(), "date_to": (HOJE - timedelta(days=1)).isoformat()})
    assert invertido.json()["detail"]["error"] == "date_to_before_date_from"
    # Ontem ainda não congelou: recalculável (o mesmo critério do GET /me/plan).
    ok = admin.post("/nutrition/targets/recompute", json={
        "user_id": ALUNO, "date_from": (HOJE - timedelta(days=1)).isoformat()})
    assert ok.status_code == 200, ok.text
    assert [m["local_date"] for m in ok.json()["targets"]] == [
        (HOJE + timedelta(days=d)).isoformat() for d in (-1, 0, 1)
    ]


def test_meu_plano_so_leitura_com_a_semana(db):
    cliente = _client("regular", user_id=ALUNO)
    nao_tem = cliente.get("/nutrition/me/plan")
    assert nao_tem.status_code == 404 and nao_tem.json()["detail"]["error"] == "no_published_plan"
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=3))
    resposta = cliente.get("/nutrition/me/plan")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["plan"]["state"] == "current" and corpo["subject"]["weight_kg"] == 80.0
    assert len(corpo["week"]) == 7 and corpo["today_target"]["local_date"] == HOJE.isoformat()
    assert corpo["missing"] == []
    assert corpo["goal"]["goal_body_fat_is_default"] is False  # manutenção não tem %G de parada
    # Segunda a quinta já congeladas e sem meta: continuam sem (nunca se escreve o passado).
    assert [dia["target"] is None for dia in corpo["week"]] == [True] * 4 + [False] * 3
    db.writes.clear()
    assert cliente.get("/nutrition/me/plan").json() == corpo
    assert db.writes == []  # o segundo GET não regrava nada


def test_meu_plano_e_o_em_vigor_hoje_pelo_motor(db):
    """Um plano publicado com início no futuro não é o de hoje; o substituído
    que vale hoje é (vigência por data do motor, não `superseded_at` no SQL)."""
    cliente = _client("regular", user_id=ALUNO)
    vale_hoje = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=3),
                            superseded_at=datetime(2026, 10, 17, 9))
    db.add_plan(ALUNO, status="published", effective_from=HOJE + timedelta(days=2))
    corpo = cliente.get("/nutrition/me/plan").json()
    assert corpo["plan"]["id"] == vale_hoje


def test_cutting_mostra_o_percentual_de_parada_padrao_do_motor(db):
    db.add_weight(ALUNO, HOJE - timedelta(days=1), 80, body_fat_percent=Decimal("18.0"),
                  fat_free_mass_kg=Decimal("65.6"))
    db.add_plan(ALUNO, status="published", strategy="cutting", effective_from=HOJE - timedelta(days=3),
                weekly_rate_share=Decimal("0.0070"), protein_g_per_kg=Decimal("2.60"))
    corpo = _client("regular", user_id=ALUNO).get("/nutrition/me/plan").json()
    assert corpo["goal"]["goal_body_fat_percent"] == CUT_STOP_BODY_FAT_PERCENT[Sex.MALE]
    assert corpo["goal"]["goal_body_fat_is_default"] is True


def test_tabelas_ausentes_respondem_503(admin, db, monkeypatch):
    def sem_tabelas(cur):
        raise tables_missing_error()

    monkeypatch.setattr(plans_mod, "require_nutrition_tables", sem_tabelas)
    resposta = admin.get(f"/nutrition/plans?user_id={ALUNO}")
    assert resposta.status_code == 503 and resposta.json()["detail"]["migration"] == "078/079"
    assert _client("regular", user_id=ALUNO).get("/nutrition/me/plan").status_code == 503

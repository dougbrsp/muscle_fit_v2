"""Modo `adaptive_balance` ponta a ponta na camada de serviço (Lote C): rascunho, prévia, publicação, metas, dashboard.

Cobre o que a spec §21 pede de plano: legado abre/edita/publica igual (hash), rascunho adaptativo não altera a meta
publicada, publicar grava versão + prévia e as metas passam a usar a estimativa, voltar ao legado restaura o cálculo,
meta congelada não é regravada quando o histórico muda, falha do histórico cai na estimativa inicial, e o gate de
leitura (plano legado não paga nenhuma consulta nova). Banco em memória: `tests/support/nutrition_fake.py`."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_dashboard as dash_mod
from app import nutrition_days as days_mod
from app import nutrition_energy_service as energy_svc
from app import nutrition_meals as meals_mod
from app import nutrition_plans as plans_mod
from app import nutrition_schema as schema
from app import nutrition_targets_service as svc
from app.domain.nutrition import adaptive_energy as adaptive
from app.domain.nutrition.daily_target import TargetWindow
from app.security import Identity, get_current_identity
from tests.support.nutrition_dashboard_fake import FakeDashboardDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

HOJE = date(2026, 11, 5)  # quinta-feira
ALUNO, OUTRO, ADMIN = 2, 4, 1
SYNC_UTC = datetime(2026, 11, 5, 9, 0)  # 06:00 em São Paulo: confirma até o dia 04
MACROS = {"energy_kcal": 2400.0, "protein_g": 150.0, "carbohydrates_g": 280.0, "total_fat_g": 80.0, "fiber_g": 30.0}
MODOS_ADAPTATIVOS = {"adaptive", "adaptive_bootstrap"}


def _refeicao(db, user_id: int, dia: date, **valores) -> None:
    linha = {c: Decimal(str(MACROS.get(c, 0))) for c in meals_mod.NUTRIENTS}
    linha.update({c: Decimal(str(v)) for c, v in valores.items()})
    ident = db._novo_id()
    linha.update(id=ident, user_id=user_id, local_date=dia, totals_incomplete_json="[]", totals_nature="derived", item_count=1)
    db.meals[ident] = linha


def _historico(db, user_id: int = ALUNO, dias: int = 30) -> None:
    for i in range(1, dias + 1):
        dia = HOJE - timedelta(days=i)
        _refeicao(db, user_id, dia)
        db.add_body(user_id, dia, weight=80.0 - (dias - i) * 0.02, body_fat=20.0)


@pytest.fixture
def db(monkeypatch):
    banco = FakeDashboardDB()
    for modulo in (dash_mod, meals_mod, days_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
    for modulo in (meals_mod, dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    for modulo in (dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(dash_mod, "_timezone_for", lambda conn, user_id: "America/Sao_Paulo")
    monkeypatch.setattr(days_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    schema.reset_nutrition_tables_cache()
    yield banco
    schema.reset_nutrition_tables_cache()


def _client(role: str = "admin", user_id: int = ADMIN) -> TestClient:
    app = FastAPI()
    for modulo in (dash_mod, meals_mod, days_mod, plans_mod):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _rascunho(cliente, **corpo) -> dict:
    resposta = cliente.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance", **corpo})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _publicar(cliente, plano_id: int, esperado=None):
    return cliente.post(f"/nutrition/plans/{plano_id}/publish", json={"expected_current_id": esperado})


def _adaptativo(cliente, modo: str = "gross", **corpo) -> dict:
    return _rascunho(cliente, tdee_mode="adaptive_balance", energy_params={"watch_calorie_mode": modo}, **corpo)


def _metas_do_aluno(db) -> dict[date, dict]:
    return {dia: linha for (uid, dia), linha in db.targets.items() if uid == ALUNO}


# --- contrato do plano -------------------------------------------------------------------------------------------


def test_rascunho_adaptativo_persiste_energy_params_e_a_saida_publica_os_traz(db):
    admin = _client()
    plano = _adaptativo(admin, "gross")
    assert plano["tdee_mode"] == "adaptive_balance"
    assert plano["energy_params"] == {"watch_calorie_mode": "gross"} and plano["energy_model_version"] is None
    assert json.loads(db.plans[plano["id"]]["energy_params_json"]) == {"watch_calorie_mode": "gross"}  # JSON como o MySQL
    editado = admin.put(f"/nutrition/plans/{plano['id']}", json={
        "strategy": "maintenance", "tdee_mode": "adaptive_balance", "energy_params": {"watch_calorie_mode": "active"}})
    assert editado.status_code == 200, editado.text
    assert editado.json()["energy_params"] == {"watch_calorie_mode": "active"}


def test_plano_legado_abre_e_edita_igual_com_energy_params_padrao_e_coluna_nula(db):
    admin = _client()
    plano = _rascunho(admin)
    assert plano["energy_params"] is None and plano["energy_model_version"] is None
    assert db.plans[plano["id"]]["energy_params_json"] is None
    assert admin.put(f"/nutrition/plans/{plano['id']}", json={"strategy": "bulking"}).status_code == 200
    assert db.plans[plano["id"]]["energy_params_json"] is None


@pytest.mark.parametrize("params", [{"watch_calorie_mode": "lixo"}, {"outra": "x"}])
def test_energy_params_invalido_e_400_com_o_codigo_do_motor(db, params):
    resposta = _client().post("/nutrition/plans", json={
        "user_id": ALUNO, "strategy": "maintenance", "tdee_mode": "adaptive_balance", "energy_params": params})
    assert resposta.status_code == 400
    assert resposta.json()["detail"]["errors"][0]["code"] == "invalid_code:energy_params"


def test_energy_params_fora_do_adaptativo_e_recusado(db):
    resposta = _client().post("/nutrition/plans", json={
        "user_id": ALUNO, "strategy": "maintenance", "tdee_mode": "explicit_training",
        "energy_params": {"watch_calorie_mode": "gross"}})
    assert resposta.status_code == 400
    assert resposta.json()["detail"]["errors"][0]["code"] == "param_not_applicable:energy_params"


# --- legado: mesmas metas e hash --------------------------------------------------------------------------------


def test_publicar_plano_legado_mantem_metas_e_hash_iguais_ao_calculo_direto(db):
    _historico(db)
    admin = _client()
    plano = _rascunho(admin)
    assert _publicar(admin, plano["id"]).status_code == 200
    hoje = _metas_do_aluno(db)[HOJE]
    assert hoje["inputs_json"].get("energy") is None and "energy" not in hoje["inputs_json"]
    settings = svc.settings_from_row(db.plans[plano["id"]])
    entradas = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE, [settings])
    comp = svc.compute_day(entradas, settings, HOJE, HOJE)
    assert hoje["inputs_hash"] == svc.inputs_fingerprint(plano["id"], comp)
    assert float(hoje["tdee_kcal"]) == pytest.approx(comp.target.tdee.kcal, abs=0.01)
    assert db.plans[plano["id"]]["energy_model_version"] is None


def test_gate_plano_legado_nao_dispara_nenhuma_consulta_nova_e_o_adaptativo_dispara(db):
    _historico(db)
    admin = _client()
    legado = svc.settings_from_row(db.plans[_rascunho(admin)["id"]])
    adaptativo = svc.settings_from_row(db.plans[_adaptativo(admin)["id"]])
    novas = {energy_svc.USER_TIMEZONE_SQL, energy_svc.MEAL_TOTALS_SQL, energy_svc.TARGET_BMR_SQL,
             energy_svc.INTERVALS_SYNC_SQL, energy_svc.SHADOW_ACTIVITIES_SQL}

    def consultas(configuracao):
        cursor = db.conn().cursor()
        executadas: list[str] = []
        original = cursor.execute
        cursor.execute = lambda sql, params=None: (executadas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
        entradas = svc.load_user_inputs(cursor, ALUNO, HOJE, HOJE, HOJE, [configuracao])
        return [sql for sql in executadas if sql in novas], entradas

    do_legado, entradas_legado = consultas(legado)
    assert do_legado == [] and entradas_legado.energy == {}
    do_adaptativo, entradas = consultas(adaptativo)
    assert len(do_adaptativo) == 4  # fuso, refeições, TMB gravada e prova de sincronização (sem atividade importada)
    assert set(entradas.energy) == {("gross", "auto")}
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=1), "Run", 200.0, start=datetime(2026, 11, 4, 7, 0), synced_at=SYNC_UTC)
    assert len(consultas(adaptativo)[0]) == 5  # + atividades


# --- rascunho, prévia e publicação adaptativos ---------------------------------------------------------------------


def test_rascunho_e_previa_adaptativos_nao_alteram_a_meta_publicada(db):
    _historico(db)
    admin = _client()
    vigente = _rascunho(admin)
    assert _publicar(admin, vigente["id"]).status_code == 200
    antes = {dia: dict(linha) for dia, linha in _metas_do_aluno(db).items()}
    rascunho = _adaptativo(admin)
    previa = admin.post(f"/nutrition/plans/{rascunho['id']}/preview")
    assert previa.status_code == 200, previa.text
    alvo = previa.json()["target"]
    assert alvo["tdee_basis"] in MODOS_ADAPTATIVOS and alvo["energy"]["algorithm_version"] == adaptive.ALGORITHM_VERSION
    partes = [p["code"] for p in alvo["tdee_breakdown"]["parts"]]
    assert partes[0] == "base" and sum(p["kcal"] for p in alvo["tdee_breakdown"]["parts"]) == alvo["tdee_breakdown"]["total_kcal"]
    sem_gravar = admin.post("/nutrition/plan-preview", json={
        "user_id": ALUNO, "strategy": "maintenance", "tdee_mode": "adaptive_balance"})
    assert sem_gravar.status_code == 200 and sem_gravar.json()["target"]["energy"] is not None
    assert {dia: dict(linha) for dia, linha in _metas_do_aluno(db).items()} == antes  # nada regravado
    assert db.plans[vigente["id"]]["status"] == "published" and db.plans[rascunho["id"]]["status"] == "draft"


def test_publicar_adaptativo_grava_versao_e_previa_e_as_metas_usam_a_estimativa(db):
    _historico(db)
    admin = _client()
    rascunho = _adaptativo(admin, "gross")
    resposta = _publicar(admin, rascunho["id"])
    assert resposta.status_code == 200, resposta.text
    linha = db.plans[rascunho["id"]]
    assert linha["energy_model_version"] == adaptive.ALGORITHM_VERSION
    previa = json.loads(linha["published_preview_json"])
    assert previa["target"]["energy"] is not None and "plan" not in previa and "user" not in previa
    metas = _metas_do_aluno(db)
    hoje = metas[HOJE]
    assert hoje["inputs_json"]["energy"]["algorithm_version"] == adaptive.ALGORITHM_VERSION
    assert hoje["inputs_json"]["tdee_basis"] in MODOS_ADAPTATIVOS
    assert float(hoje["tdee_kcal"]) == pytest.approx(hoje["inputs_json"]["tdee"]["kcal"], abs=0.01)
    assert hoje["inputs_json"]["tdee"]["mode"] == "adaptive_balance"
    # o gasto vem do histórico (peso em queda leve, 2.400 kcal/dia): não é o fator × TMB do legado
    assert hoje["inputs_json"]["energy"]["valid_days"] >= 14 or hoje["inputs_json"]["tdee_basis"] == "adaptive_bootstrap"


def test_voltar_ao_legado_republicando_restaura_o_calculo_legado(db):
    _historico(db)
    admin = _client()
    adaptativo = _adaptativo(admin)
    assert _publicar(admin, adaptativo["id"]).status_code == 200
    assert _metas_do_aluno(db)[HOJE]["inputs_json"]["tdee_basis"] in MODOS_ADAPTATIVOS
    legado = _rascunho(admin, strategy="maintenance")
    assert _publicar(admin, legado["id"], esperado=adaptativo["id"]).status_code == 200
    hoje = _metas_do_aluno(db)[HOJE]
    assert hoje["plan_id"] == legado["id"] and hoje["inputs_json"]["tdee_basis"] == "classic"
    assert "energy" not in hoje["inputs_json"]
    settings = svc.settings_from_row(db.plans[legado["id"]])
    comp = svc.compute_day(svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE, [settings]), settings, HOJE, HOJE)
    assert hoje["inputs_hash"] == svc.inputs_fingerprint(legado["id"], comp)
    assert db.plans[legado["id"]]["energy_model_version"] is None


def test_ciclo_de_carbo_e_refeicoes_do_plano_sao_preservados_no_adaptativo(db):
    _historico(db)
    admin = _client()
    plano = _adaptativo(admin, meals_per_day=5, cycle_days={"0": "high", "2": "low", "4": "high", "5": "low"})
    assert plano["meals_per_day"] == 5 and plano["tdee_mode"] == "adaptive_balance"
    assert plano["cycle_days"] is not None and plano["cycle_days"]["0"] == "high"
    assert _publicar(admin, plano["id"]).status_code == 200
    linha = db.plans[plano["id"]]
    assert linha["meals_per_day"] == 5 and linha["carb_cycling_json"]
    sexta = next(m for dia, m in sorted(_metas_do_aluno(db).items()) if dia.weekday() == 4)
    assert sexta["day_kind"] == "high"  # o ciclo de carbo segue valendo sobre a meta adaptativa
    assert sexta["inputs_json"]["tdee_basis"] in MODOS_ADAPTATIVOS


# --- reprodutibilidade, falha e usuários ------------------------------------------------------------------------


def _db_com_plano_adaptativo(modo: str = "gross") -> FakeDashboardDB:
    banco = FakeDashboardDB()
    _historico(banco)
    banco.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=20),
                   published_at=datetime(2026, 10, 16, 12), training_kcal_mode="adaptive_balance", activity_level="light",
                   energy_params_json=json.dumps({"watch_calorie_mode": modo}))
    return banco


def _ensure(banco, de, ate, *, hoje, criar_ate=None):
    janela = TargetWindow(date_from=de, date_to=ate, create_until=criar_ate or ate)
    return svc.ensure_daily_targets(banco.conn(), ALUNO, janela, today=hoje)


@pytest.fixture
def banco_adaptativo(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    schema.reset_nutrition_tables_cache()
    return _db_com_plano_adaptativo()


def test_meta_congelada_do_plano_adaptativo_nao_e_regravada_quando_o_historico_muda(banco_adaptativo):
    banco = banco_adaptativo
    dia_antigo = HOJE - timedelta(days=4)
    # Hoje = dia_antigo + 4: o dia ainda não congelou na primeira leitura (D+2 depois dele).
    primeira = _ensure(banco, dia_antigo, dia_antigo, hoje=dia_antigo + timedelta(days=1))
    linha = dict(banco.targets[(ALUNO, dia_antigo)])
    assert linha["inputs_json"]["energy"] and primeira
    # O histórico muda depois (refeição nova e outro peso); o dia agora está congelado (4 dias atrás).
    _refeicao(banco, ALUNO, dia_antigo - timedelta(days=1), energy_kcal=500.0)
    banco.add_body(ALUNO, dia_antigo - timedelta(days=1), weight=90.0, body_fat=20.0)
    banco.writes.clear()
    _ensure(banco, dia_antigo, dia_antigo, hoje=HOJE)
    assert banco.writes == []  # congelado: nem tenta regravar
    assert banco.targets[(ALUNO, dia_antigo)] == linha


def test_dia_nao_congelado_acompanha_o_historico_e_o_congelado_fica(banco_adaptativo):
    banco = banco_adaptativo
    _ensure(banco, HOJE - timedelta(days=1), HOJE, hoje=HOJE)
    ontem = dict(banco.targets[(ALUNO, HOJE - timedelta(days=1))])
    _refeicao(banco, ALUNO, HOJE - timedelta(days=2), energy_kcal=500.0)  # muda o histórico
    _ensure(banco, HOJE - timedelta(days=1), HOJE, hoje=HOJE)
    depois = banco.targets[(ALUNO, HOJE - timedelta(days=1))]
    assert depois["inputs_hash"] != ontem["inputs_hash"] or depois["inputs_json"]["energy"] != ontem["inputs_json"]["energy"]


def test_falha_do_historico_cai_na_estimativa_inicial_sem_500(banco_adaptativo, monkeypatch):
    def quebra(*args, **kwargs):
        raise RuntimeError("segredo=1")

    monkeypatch.setattr(svc, "load_history", quebra)
    linhas = _ensure(banco_adaptativo, HOJE, HOJE, hoje=HOJE)
    assert linhas and linhas[0]["tdee_basis"] == "adaptive_bootstrap"
    meta = banco_adaptativo.targets[(ALUNO, HOJE)]
    assert meta["inputs_json"]["energy"]["algorithm_version"] == adaptive.ALGORITHM_VERSION
    assert float(meta["tdee_kcal"]) > 0


def test_modo_da_kcal_do_plano_chega_ao_calculo(banco_adaptativo):
    ativo = _db_com_plano_adaptativo("active")
    db_gross = banco_adaptativo
    for banco in (ativo, db_gross):
        banco.add_intervals_row(ALUNO, HOJE - timedelta(days=1), "WeightTraining", 428.0,
                                start=datetime(2026, 11, 4, 18, 0), elapsed=3600, synced_at=SYNC_UTC)
    entradas_ativo = svc.load_user_inputs(ativo.conn().cursor(), ALUNO, HOJE, HOJE, HOJE, [svc.settings_from_row(next(iter(ativo.plans.values())))])
    entradas_gross = svc.load_user_inputs(db_gross.conn().cursor(), ALUNO, HOJE, HOJE, HOJE, [svc.settings_from_row(next(iter(db_gross.plans.values())))])
    ontem = HOJE - timedelta(days=1)
    kcal_ativo = next(d for d in entradas_ativo.energy[("active", "auto")].days if d.day == ontem).active_kcal
    kcal_gross = next(d for d in entradas_gross.energy[("gross", "auto")].days if d.day == ontem).active_kcal
    assert kcal_ativo == 428.0 and 0 < kcal_gross < 428.0  # ativa entra inteira; bruta desconta o repouso


# --- dashboard -----------------------------------------------------------------------------------------------------


def test_dashboard_com_plano_vigente_adaptativo_tem_applied_true_e_o_mesmo_estado_da_meta(db):
    _historico(db)
    admin = _client()
    plano = _adaptativo(admin, "gross")
    assert _publicar(admin, plano["id"]).status_code == 200
    resposta = _client("regular", ALUNO).get("/nutrition/dashboard")
    assert resposta.status_code == 200, resposta.text
    bloco = resposta.json()["adaptive_energy"]
    assert bloco["available"] is True and bloco["applied"] is True
    meta = _metas_do_aluno(db)[HOJE]
    estimativa = meta["inputs_json"]["energy"]
    # cartão e meta de hoje mostram o MESMO estado do histórico
    assert bloco["state"]["adaptive_tdee"] == estimativa["adaptive_tdee"]
    assert bloco["state"]["valid_days"] == estimativa["valid_days"]


def test_dashboard_com_plano_legado_segue_em_sombra_applied_false(db):
    _historico(db)
    admin = _client()
    assert _publicar(admin, _rascunho(admin)["id"]).status_code == 200
    bloco = _client("regular", ALUNO).get("/nutrition/dashboard").json()["adaptive_energy"]
    assert bloco["available"] is True and bloco["applied"] is False


def test_admin_vendo_aluno_e_terceiro_negado_no_dashboard_adaptativo(db):
    _historico(db)
    admin = _client()
    assert _publicar(admin, _adaptativo(admin)["id"]).status_code == 200
    visto = _client("admin", ADMIN).get(f"/nutrition/dashboard?user_id={ALUNO}")
    assert visto.status_code == 200 and visto.json()["adaptive_energy"]["applied"] is True
    assert _client("regular", OUTRO).get(f"/nutrition/dashboard?user_id={ALUNO}").status_code == 403


def test_dia_do_diario_expoe_o_retrato_da_estimativa_sem_somar_treino_de_novo(db):
    _historico(db)
    admin = _client()
    assert _publicar(admin, _adaptativo(admin)["id"]).status_code == 200
    db.add_activity(ALUNO, HOJE, kcal=428.0, seconds=3600)  # treino do app no dia
    corpo = _client("regular", ALUNO).get(f"/nutrition/days/{HOJE.isoformat()}").json()
    meta = corpo["target"]
    assert meta["tdee_basis"] in MODOS_ADAPTATIVOS and meta["energy"]["algorithm_version"] == adaptive.ALGORITHM_VERSION
    gravada = _metas_do_aluno(db)[HOJE]
    assert meta["tdee_kcal"] == pytest.approx(float(gravada["tdee_kcal"]), abs=0.01)  # a mesma gravada: nada somado de novo
    assert meta["extra_activity"]["kcal"] == 0.0

"""Colunas da migração 106 em `nutrition_plans` (Lote C da nutrição adaptativa): guarda de schema, prévia gravada na
publicação (`published_preview_json`), `energy_model_version` e `energy_params_json`.

Só leitura/escrita de colunas aditivas: planos legados seguem idênticos (mesmas metas, mesmo hash). Banco em memória:
`tests/support/nutrition_fake.py`, que projeta só as colunas dos SELECT e devolve JSON como TEXTO, como o MySQL."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_schema as schema
from app import nutrition_targets_service as svc
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeNutritionDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

HOJE = date(2026, 10, 17)  # sábado
ALUNO = 2


@pytest.fixture
def db(monkeypatch):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, HOJE - timedelta(days=3), 80)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    schema.reset_nutrition_tables_cache()
    yield banco
    schema.reset_nutrition_tables_cache()


@pytest.fixture
def admin(db):
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="admin", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _rascunho(admin, **corpo) -> dict:
    resposta = admin.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance", **corpo})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _publicar(admin, plano_id: int, esperado=None):
    return admin.post(f"/nutrition/plans/{plano_id}/publish", json={"expected_current_id": esperado})


# --- guarda de schema (código novo sobre o banco sem a 106) ----------------------------------------------------------


def test_sem_a_106_as_rotas_do_plano_respondem_503_citando_a_migracao(db, admin):
    db.plan_energy_columns = False
    for chamada in (lambda: admin.get(f"/nutrition/plans?user_id={ALUNO}"),
                    lambda: admin.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance"})):
        resposta = chamada()
        assert resposta.status_code == 503, resposta.text
        detalhe = resposta.json()["detail"]
        assert detalhe["error"] == "nutrition_plan_migration_pending" and detalhe["migration"] == "106"
    assert db.writes == []  # nada gravado


def test_com_a_106_o_guarda_e_positivo_e_cacheado(db, admin):
    assert admin.get(f"/nutrition/plans?user_id={ALUNO}").status_code == 200
    db.plan_energy_columns = False  # o cache é só do positivo: a janela de deploy não volta atrás
    assert admin.get(f"/nutrition/plans?user_id={ALUNO}").status_code == 200


def test_a_lista_de_planos_nao_le_a_previa_gravada(db):
    assert "published_preview_json" not in plans_mod._PLAN_COLUMNS
    assert "energy_model_version" in plans_mod._PLAN_COLUMNS and "energy_params_json" in plans_mod._PLAN_COLUMNS
    assert "published_preview_json" not in plans_mod._PUBLISH_SQL  # a prévia vai num UPDATE à parte, protegido
    assert "published_preview_json" in plans_mod._PUBLISHED_PREVIEW_SQL


# --- publicação: prévia aprovada e versão do algoritmo ---------------------------------------------------------------


def test_publicar_grava_a_previa_do_servidor_e_nao_muda_as_metas_do_plano_legado(db, admin):
    rascunho = _rascunho(admin)  # manutenção: não exige massa gorda no perfil
    previa_da_rota = admin.post(f"/nutrition/plans/{rascunho['id']}/preview").json()
    publicado = _publicar(admin, rascunho["id"])
    assert publicado.status_code == 200, publicado.text
    linha = db.plans[rascunho["id"]]
    assert linha["energy_model_version"] is None  # legado: sem versão do algoritmo
    gravada = json.loads(linha["published_preview_json"])
    assert "plan" not in gravada and "user" not in gravada
    for chave in ("target", "week", "goal", "projection", "can_publish", "today", "engine_version"):
        assert gravada[chave] == {k: v for k, v in previa_da_rota.items() if k not in ("plan", "user")}[chave], chave
    # as metas materializadas seguem o hash de sempre: nada do snapshot entra nas entradas
    plano_settings = svc.settings_from_row(linha)
    assert plano_settings.tdee_mode.value == "explicit_training"
    metas = [r for (uid, _dia), r in db.targets.items() if uid == ALUNO]
    assert metas and all(len(m["inputs_hash"]) == 64 for m in metas)
    assert all("published_preview" not in json.dumps(m["inputs_json"]) for m in metas)


def test_falha_ao_montar_a_previa_nao_bloqueia_a_publicacao(db, admin, monkeypatch, caplog):
    def quebra(*args, **kwargs):
        raise RuntimeError("segredo=123")

    monkeypatch.setattr(plans_mod, "_preview_payload", quebra)
    rascunho = _rascunho(admin)
    with caplog.at_level("WARNING"):
        resposta = _publicar(admin, rascunho["id"])
    assert resposta.status_code == 200, resposta.text
    assert db.plans[rascunho["id"]]["published_preview_json"] is None
    assert "RuntimeError" in caplog.text and "segredo" not in caplog.text


def test_rascunho_nao_grava_previa_nem_versao(db, admin):
    rascunho = _rascunho(admin)
    linha = db.plans[rascunho["id"]]
    assert linha["published_preview_json"] is None and linha["energy_model_version"] is None
    admin.post(f"/nutrition/plans/{rascunho['id']}/preview")
    assert db.plans[rascunho["id"]]["published_preview_json"] is None  # a prévia da rota nunca grava


def test_energy_model_version_so_no_plano_adaptativo():
    from app.domain.nutrition import adaptive_energy
    from app.domain.nutrition.plan import build_plan_settings

    legado = build_plan_settings(strategy="maintenance").settings
    assert legado.energy_model_version is None
    adaptativo = build_plan_settings(strategy="maintenance", tdee_mode="adaptive_balance").settings
    assert adaptativo.energy_model_version == adaptive_energy.ALGORITHM_VERSION

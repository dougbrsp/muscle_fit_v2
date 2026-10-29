"""Rotas das regras de carga da próxima série (`app/next_set_load_rules_admin.py`,
R2) sem banco real: acesso (admin × autenticado), 422 com os códigos do dono,
avisos sem bloquear, 409 de concorrência na publicação, `GET /published` sem
linha = padrão v0, `describe` e `simulate`. O router é conferido também no
`app.main.app` real (o try/except do main.py engoliria erro de import).

Banco em memória: `tests/support/next_set_load_rules_fake.py`.

Rodar: cd api && python -m pytest tests/unit/test_next_set_load_rules_admin.py
"""

from __future__ import annotations

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import next_set_load_rules as rules
from app import next_set_load_rules_admin as admin_mod
from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.security import Identity, get_current_identity
from tests.support.next_set_load_rules_fake import FakeRuleSetsDB

BASE = "/next-set-load-rules"
EXEMPLO = {
    "reps_floor": 6,
    "reps_ceiling": 12,
    "rules": [
        {"set_position": "first", "fatigue": "rested", "reps": {"op": ">", "value": "ceiling"},
         "rpe": {"op": "<", "value": "max"}, "respect_planned_ramp": True, "action": "INCREASE_LOAD",
         "magnitude": {"kind": "percent", "value": 25}},
        {"reps": {"op": "<", "value": "floor"}, "rpe": {"op": ">=", "value": 10}, "action": "DECREASE_LOAD",
         "magnitude": {"kind": "steps", "value": 1}},
    ],
}
# 1ª série, músculo descansado, 13 reps em RPE 8 com teto 9, 20 kg no passo 2,5.
FATOS = {"working_set_index": 1, "fatigue_band": "rested", "reps": 13, "rpe": 8, "rpe_ceiling": 9,
         "weight_kg": 20, "step_kg": 2.5}


@pytest.fixture
def db(monkeypatch):
    banco = FakeRuleSetsDB()
    monkeypatch.setattr(admin_mod, "transaction", banco.transaction())
    monkeypatch.setattr(rules, "fetch_rows", banco.fetch_rows)
    return banco


def _client(role: str = "admin", user_id: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(admin_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin(db):
    return _client("admin")


def _salvar(cliente, corpo=None):
    return cliente.put(f"{BASE}/draft", json=corpo or EXEMPLO)


def _publicar(cliente, draft_id, esperado):
    return cliente.post(f"{BASE}/publish", json={"draft_id": draft_id, "expected_published_version": esperado})


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for sufixo in ("/published", "/draft", "/publish", "/versions", "/describe", "/simulate"):
        assert BASE + sufixo in caminhos, sufixo
    assert TestClient(app, raise_server_exceptions=False).get(f"{BASE}/published").status_code in (401, 403)


@pytest.mark.parametrize(
    ("metodo", "caminho", "corpo"),
    [
        ("get", "/draft", None),
        ("put", "/draft", EXEMPLO),
        ("post", "/publish", {"draft_id": 1, "expected_published_version": 0}),
        ("get", "/versions", None),
        ("post", "/describe", {"ruleset": EXEMPLO}),
        ("post", "/simulate", {"facts": FATOS}),
    ],
)
def test_usuario_comum_nao_cadastra(db, metodo, caminho, corpo):
    kwargs = {"json": corpo} if corpo is not None else {}
    assert getattr(_client("regular", 2), metodo)(BASE + caminho, **kwargs).status_code == 403


def test_published_sem_linha_e_o_padrao_v0_para_qualquer_autenticado(db):
    resposta = _client("regular", 2).get(f"{BASE}/published")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert (corpo["version"], corpo["source"], corpo["id"], corpo["published_at"]) == (0, "default", None, None)
    padrao = nsl.DEFAULT_RULESET
    assert [r["text"] for r in corpo["ruleset"]["rules"]] == [nsl.describe_rule(r, padrao) for r in padrao.rules]
    assert corpo["summary"] == nsl.describe_ruleset(padrao)


def test_salvar_rascunho_devolve_texto_e_avisos_sem_bloquear(admin, db):
    so_aumento = copy.deepcopy(EXEMPLO)
    so_aumento["rules"] = so_aumento["rules"][:1]
    resposta = _salvar(admin, {**so_aumento, "notes": "  teste  "})
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["warnings"] == [nsl.WARN_NO_DECREASE_RULE]
    rascunho = corpo["draft"]
    assert rascunho["status"] == "draft" and rascunho["version"] is None and rascunho["notes"] == "teste"
    assert rascunho["ruleset"]["rules"][0]["text"].startswith("1ª série · músculo descansado")
    assert admin.get(f"{BASE}/draft").json()["draft"]["id"] == rascunho["id"]


def test_rascunho_vazio_e_null(admin, db):
    assert admin.get(f"{BASE}/draft").json() == {"draft": None, "warnings": []}


@pytest.mark.parametrize(
    ("ajuste", "codigo"),
    [
        ({"rules": [{**EXEMPLO["rules"][0], "magnitude": {"kind": "percent", "value": 999}}]},
         nsl.RULE_MAGNITUDE_OUT_OF_RANGE + ":1"),
        ({"reps_floor": 12, "reps_ceiling": 6}, nsl.REPS_FLOOR_NOT_BELOW_CEILING),
        ({"rules": []}, nsl.RULESET_EMPTY),
        ({"rules": [{"action": "JUMP", "reps": {"op": ">", "value": 3}}]}, nsl.RULE_ACTION_UNKNOWN + ":1"),
    ],
)
def test_put_draft_422_com_o_codigo_do_dono(admin, db, ajuste, codigo):
    resposta = _salvar(admin, {**EXEMPLO, **ajuste})
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "invalid_ruleset" and codigo in detalhe["codes"]
    assert db.rows == []  # nada gravado


def test_put_draft_422_de_forma(admin, db):
    assert _salvar(admin, {**EXEMPLO, "campo": 1}).status_code == 422
    assert _salvar(admin, {**EXEMPLO, "rules": [{"action": "HOLD", "reason_code": "X"}]}).status_code == 422


def test_publicar_v1_e_409_com_versao_velha(admin, db):
    draft_id = _salvar(admin).json()["draft"]["id"]
    resposta = _publicar(admin, draft_id, 0)
    assert resposta.status_code == 200, resposta.text
    publicado = resposta.json()["published"]
    assert (publicado["version"], publicado["status"], publicado["current"]) == (1, "published", True)
    vigente = admin.get(f"{BASE}/published").json()
    assert (vigente["version"], vigente["source"], vigente["id"]) == (1, "published", draft_id)
    assert vigente["published_at"] is not None

    outro = _salvar(admin).json()["draft"]["id"]
    conflito = _publicar(admin, outro, 0)
    assert conflito.status_code == 409
    assert conflito.json()["detail"] == {
        "error": "publish_conflict",
        "message": "A versão vigente mudou desde que a tela carregou; recarregue antes de publicar.",
        "current_version": 1,
    }
    assert _publicar(admin, outro, 1).json()["published"]["version"] == 2
    versoes = admin.get(f"{BASE}/versions").json()["versions"]
    assert [(v["version"], v["current"]) for v in versoes] == [(2, True), (1, False)]


def test_publicar_rascunho_inexistente_e_404(admin, db):
    resposta = _publicar(admin, 42, 0)
    assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "draft_not_found"


def test_publicar_rascunho_recusado_pelo_dono_e_422_sem_gravar(admin, db):
    fora = {**EXEMPLO, "rules": [{**EXEMPLO["rules"][0], "magnitude": {"kind": "percent", "value": 999}}]}
    draft_id = db.add(status=rules.STATUS_DRAFT, version_number=None, ruleset_dict=fora)
    resposta = _publicar(admin, draft_id, 0)
    assert resposta.status_code == 422 and resposta.json()["detail"]["codes"] == [nsl.RULE_MAGNITUDE_OUT_OF_RANGE + ":1"]
    assert db.by_id(draft_id)["status"] == "draft" and db.rollbacks == 1


def test_describe_devolve_textos_e_codigos_sem_gravar(admin, db):
    corpo = admin.post(f"{BASE}/describe", json={"ruleset": {**EXEMPLO, "reps_floor": 12, "reps_ceiling": 6}}).json()
    conjunto = rules.ruleset_from_input(rules.RuleSetIn(**{**EXEMPLO, "reps_floor": 12, "reps_ceiling": 6}))
    assert corpo["texts"] == [nsl.describe_rule(r, conjunto) for r in conjunto.rules]
    assert corpo["summary"] == nsl.describe_ruleset(conjunto)
    assert nsl.REPS_FLOOR_NOT_BELOW_CEILING in corpo["errors"]
    assert db.rows == [] and db.statements == []


def test_simulate_com_conjunto_enviado_aplica_o_percentual_do_dono(admin, db):
    resposta = admin.post(f"{BASE}/simulate", json={"ruleset": EXEMPLO, "facts": FATOS})
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    conjunto = rules.ruleset_from_input(rules.RuleSetIn(**EXEMPLO))
    esperado = nsl.evaluate_ruleset(rules.SetFactsIn(**FATOS).to_facts(), conjunto).to_dict()
    assert corpo["result"] == esperado
    assert (corpo["result"]["direction"], corpo["result"]["load_kg"], corpo["result"]["rule_position"]) == (
        pe.INCREASE_LOAD, 25.0, 1
    )
    # 2ª série igual não casa a regra `first`: nenhuma regra -> manter
    segunda = admin.post(f"{BASE}/simulate", json={"ruleset": EXEMPLO, "facts": {**FATOS, "working_set_index": 2}})
    assert segunda.json()["result"]["direction"] == pe.HOLD


def test_simulate_sem_conjunto_usa_o_vigente_e_fora_da_regra_e_null(admin, db):
    corpo = admin.post(f"{BASE}/simulate", json={"facts": {**FATOS, "reps": 16}}).json()
    assert (corpo["ruleset_version"], corpo["source"]) == (0, "default")
    assert corpo["result"]["reason"] == nsl.REASON_REPS_ABOVE_LIMIT
    aquecimento = admin.post(f"{BASE}/simulate", json={"facts": {**FATOS, "set_type": "warmup"}}).json()
    assert aquecimento["result"] is None


def test_simulate_422_com_conjunto_invalido(admin, db):
    resposta = admin.post(f"{BASE}/simulate", json={"ruleset": {**EXEMPLO, "rules": []}, "facts": FATOS})
    assert resposta.status_code == 422 and nsl.RULESET_EMPTY in resposta.json()["detail"]["codes"]

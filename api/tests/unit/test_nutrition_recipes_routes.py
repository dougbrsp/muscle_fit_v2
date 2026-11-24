"""Rotas de receitas (`app/nutrition_recipes.py`, issue #286) sem banco real: prévia, criação (alimento + versão 1 +
ingredientes com snapshot), leitura, edição (versão 2), posse sem oráculo, erros tipados e 503 sem a migração 125.

Banco em memória: `tests/support/nutrition_foods_fake.py`. Números: 100 g de arroz + 200 g de frango, pronto 250 g."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_recipes as recipes_mod
from app.domain.nutrition import recipe as engine
from app.nutrition_schema import recipe_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_foods_fake import FakeFoodsDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

DONO, OUTRO = 2, 3


@pytest.fixture
def db(monkeypatch):
    banco = FakeFoodsDB()
    banco.arroz = banco.add_food("Arroz cozido", energy_kcal=130, carbohydrates_g=28, protein_g=2.5, total_fat_g=0.3)
    banco.frango = banco.add_food("Frango grelhado", energy_kcal=160, carbohydrates_g=0, protein_g=30, total_fat_g=3)
    banco.privado = banco.add_food("Molho do outro", source="user", owner=OUTRO, energy_kcal=50, carbohydrates_g=5,
                                   protein_g=1, total_fat_g=2)
    banco.inativo = banco.add_food("Inativo", source="user", owner=None, active=False, energy_kcal=1,
                                   carbohydrates_g=0, protein_g=0, total_fat_g=0)
    monkeypatch.setattr(recipes_mod, "get_conn", banco.conn)
    monkeypatch.setattr(recipes_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(recipes_mod, "require_recipe_tables", lambda cur: None)
    return banco


def _client(user_id: int = DONO, role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(recipes_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _corpo(db, **extra):
    base = {
        "ingredients": [
            {"food_id": db.arroz, "quantity": 100, "unit": "g"},
            {"food_id": db.frango, "quantity": 200, "unit": "g"},
        ],
        "final_weight_g": 250,
    }
    return {**base, **extra}


def _codigo(resposta) -> str:
    return resposta.json()["detail"]["code"]


def test_previa_com_numeros_conferidos_e_sem_gravar(db):
    r = _client().post("/nutrition/recipes/preview", json=_corpo(db))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["totals"]["energy_kcal"] == pytest.approx(450)
    por_100 = corpo["per_100_effective"]
    assert por_100["energy_kcal"] == pytest.approx(180)
    assert por_100["protein_g"] == pytest.approx(25)
    assert por_100["carbohydrates_g"] == pytest.approx(11.2)
    assert corpo["per_100_calculated"] == por_100 and corpo["overridden_fields"] == []
    assert corpo["raw_weight_g"] == 300 and corpo["final_weight_g"] == 250
    assert corpo["yield_percent"] == pytest.approx(250 / 300 * 100)
    assert "fiber_g" in corpo["incomplete_fields"] and corpo["engine_version"] == engine.RECIPE_ENGINE_VERSION
    assert [i["label"] for i in corpo["ingredients"]] == ["Arroz cozido", "Frango grelhado"]
    assert corpo["ingredients"][1]["amount"] == 200 and corpo["ingredients"][1]["per_100"]["protein_g"] == 30
    assert not db.recipe_versions and len(db.foods) == 4


def test_criar_grava_alimento_versao_1_e_ingredientes_com_snapshot(db):
    r = _client().post("/nutrition/recipes", json=_corpo(db, name="Arroz com frango", instructions="Misturar.",
                                                         serving_size=300))
    assert r.status_code == 201, r.text
    corpo = r.json()
    linha = db.foods[corpo["id"]]
    assert linha["source"] == "recipe" and linha["owner_user_id"] == DONO and linha["basis"] == "g"
    assert float(linha["energy_kcal"]) == pytest.approx(180) and float(linha["serving_size"]) == 300
    assert corpo["is_own"] and not corpo["is_public"] and corpo["name"] == "Arroz com frango"
    versao = next(iter(db.recipe_versions.values()))
    assert versao["version"] == 1 and versao["food_id"] == corpo["id"] and versao["created_by"] == DONO
    assert json.loads(versao["calculated_json"])["per_100_g"]["energy_kcal"] == pytest.approx(180)
    assert json.loads(versao["effective_json"])["overridden_fields"] == []
    ingredientes = sorted(db.recipe_ingredients.values(), key=lambda i: i["position"])
    assert [i["food_id"] for i in ingredientes] == [db.arroz, db.frango]
    snapshot = json.loads(ingredientes[0]["catalog_snapshot_json"])
    assert snapshot["food"]["name"] == "Arroz cozido" and snapshot["food"]["per_100"]["energy_kcal"] == 130
    assert corpo["recipe"]["version"] == 1 and corpo["recipe"]["instructions"] == "Misturar."
    assert len(corpo["recipe"]["ingredients"]) == 2


def test_ler_traz_versao_atual_e_instructions_so_para_o_dono(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R", instructions="Segredo")).json()
    dono = _client().get(f"/nutrition/recipes/{novo['id']}")
    assert dono.status_code == 200 and dono.json()["recipe"]["instructions"] == "Segredo"
    assert _client(OUTRO).get(f"/nutrition/recipes/{novo['id']}").status_code == 404  # privada alheia
    db.foods[novo["id"]]["owner_user_id"] = None  # pública: o modo de preparo vai junto
    outro = _client(OUTRO).get(f"/nutrition/recipes/{novo['id']}")
    assert outro.status_code == 200 and outro.json()["recipe"]["instructions"] == "Segredo"


def test_editar_cria_versao_2_e_atualiza_o_por_100_do_alimento(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    r = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R2", final_weight_g=300))
    assert r.status_code == 200, r.text
    assert r.json()["recipe"]["version"] == 2 and r.json()["name"] == "R2"
    assert sorted(v["version"] for v in db.recipe_versions.values()) == [1, 2]
    assert float(db.foods[novo["id"]]["energy_kcal"]) == pytest.approx(150)  # 450 / 300 g
    lido = _client().get(f"/nutrition/recipes/{novo['id']}").json()
    assert lido["recipe"]["version"] == 2 and lido["recipe"]["final_weight_g"] == 300


def test_outro_usuario_nao_ve_nem_edita_e_o_erro_e_o_de_inexistente(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    inexistente = _client(OUTRO).get("/nutrition/recipes/9999")
    for resposta in (
        _client(OUTRO).get(f"/nutrition/recipes/{novo['id']}"),
        _client(OUTRO).put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="X")),
    ):
        assert resposta.status_code == inexistente.status_code == 404
        assert resposta.json() == inexistente.json()
    assert len(db.recipe_versions) == 1
    assert _client(OUTRO).get("/nutrition/recipes").json() == {"items": []}


def test_alimento_comum_nao_e_receita_para_as_rotas(db):
    assert _client().get(f"/nutrition/recipes/{db.arroz}").status_code == 404


def test_ingrediente_invisivel_inexistente_e_inativo_dao_o_mesmo_erro_com_indice(db):
    for food_id in (db.privado, 9999, db.inativo):
        corpo = _corpo(db)
        corpo["ingredients"][1]["food_id"] = food_id
        r = _client().post("/nutrition/recipes/preview", json=corpo)
        assert r.status_code == 422 and r.json()["detail"] == {
            "error": "recipe_ingredient_not_found:1", "code": "recipe_ingredient_not_found:1",
            "message": r.json()["detail"]["message"], "index": 1,
        }
    assert not db.recipe_versions


def test_ingrediente_que_e_receita_e_recusado(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    corpo = _corpo(db)
    corpo["ingredients"][0]["food_id"] = novo["id"]
    r = _client().post("/nutrition/recipes/preview", json=corpo)
    assert r.status_code == 422 and _codigo(r) == "recipe_ingredient_is_recipe:0"


def test_porcao_de_outro_alimento_e_quantidade_sem_conversao_dao_422_com_indice(db):
    porcao = db.add_portion(db.frango, "Filé", 120, owner=None)
    corpo = _corpo(db)
    corpo["ingredients"][0].update(unit="portion", portion_id=porcao)  # porção do frango no arroz
    assert _codigo(_client().post("/nutrition/recipes/preview", json=corpo)) == "recipe_ingredient_portion_not_found:0"
    corpo = _corpo(db)
    corpo["ingredients"][1].update(unit="unit")  # sem porção: unidade sem conversão
    r = _client().post("/nutrition/recipes/preview", json=corpo)
    assert r.status_code == 422 and _codigo(r) == "unit_without_conversion:1"
    msg = r.json()["detail"]["message"]
    assert msg != "unit_without_conversion:1" and msg.startswith("Ingrediente 2: ") and "_" not in msg


def test_limitacoes_dos_ingredientes_chegam_em_criar_ler_e_editar_como_na_previa(db):
    corpo = _corpo(db, name="R")
    corpo["ingredients"][1].update(quantity=200, unit="ml")  # ml sem densidade: assume densidade
    previa = _client().post("/nutrition/recipes/preview", json=corpo).json()["limitations"]
    assert any(i["code"] == "assumed_density" for i in previa)
    criada = _client().post("/nutrition/recipes", json=corpo).json()
    lida = _client().get(f"/nutrition/recipes/{criada['id']}").json()
    editada = _client().put(f"/nutrition/recipes/{criada['id']}", json=corpo).json()
    for resposta in (criada, lida, editada):
        assert resposta["recipe"]["limitations"] == previa


def test_ingrediente_por_porcao_cadastrada_usa_o_peso_da_porcao(db):
    porcao = db.add_portion(db.frango, "Filé", 120, owner=None)
    corpo = _corpo(db)
    corpo["ingredients"][1].update(quantity=2, unit="unit", portion_id=porcao)
    r = _client().post("/nutrition/recipes/preview", json=corpo)
    assert r.status_code == 200, r.text
    assert r.json()["ingredients"][1]["amount"] == 240 and r.json()["raw_weight_g"] == 340


def test_substituicao_manual_preserva_o_calculado(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R", overrides={"energy_kcal": 170})).json()
    assert float(db.foods[novo["id"]]["energy_kcal"]) == pytest.approx(170)
    versao = next(iter(db.recipe_versions.values()))
    efetivo = json.loads(versao["effective_json"])
    assert efetivo["overridden_fields"] == ["energy_kcal"]
    assert efetivo["calculated"]["energy_kcal"] == pytest.approx(180) and efetivo["effective"]["energy_kcal"] == 170
    assert json.loads(versao["calculated_json"])["per_100_g"]["energy_kcal"] == pytest.approx(180)
    previa = _client().post("/nutrition/recipes/preview", json=_corpo(db, overrides={"energy_kcal": 170})).json()
    assert previa["per_100_effective"]["energy_kcal"] == 170 and previa["per_100_calculated"]["energy_kcal"] == pytest.approx(180)
    assert previa["overridden_fields"] == ["energy_kcal"]


@pytest.mark.parametrize(
    "extra, codigo",
    [
        ({"final_weight_g": 5000}, "recipe_yield_implausible"),
        ({"overrides": {"campo_falso": 1}}, "recipe_override_field_invalid:campo_falso"),
        ({"overrides": {"protein_g": -1}}, "recipe_override_value_invalid:protein_g"),
        ({"ingredients": []}, "recipe_no_ingredients"),
    ],
)
def test_erros_do_motor_viram_422_com_codigo(db, extra, codigo):
    r = _client().post("/nutrition/recipes/preview", json=_corpo(db, **extra))
    assert r.status_code == 422 and _codigo(r) == codigo and r.json()["detail"]["message"]


def test_corpo_invalido_e_422_do_pydantic(db):
    cliente = _client()
    assert cliente.post("/nutrition/recipes/preview", json=_corpo(db, final_weight_g=0)).status_code == 422
    assert cliente.post("/nutrition/recipes/preview", json=_corpo(db, extra_field=1)).status_code == 422
    muitos = [{"food_id": db.arroz, "quantity": 1, "unit": "g"}] * (engine.RECIPE_MAX_INGREDIENTS + 1)
    assert cliente.post("/nutrition/recipes/preview", json=_corpo(db, ingredients=muitos)).status_code == 422
    assert cliente.post("/nutrition/recipes", json=_corpo(db)).status_code == 422  # sem nome


def test_valores_efetivos_sem_energia_dao_400_do_catalogo_e_nada_grava(db):
    sem_energia = db.add_food("Sem energia", carbohydrates_g=10, protein_g=1, total_fat_g=1)
    corpo = {"name": "R", "ingredients": [{"food_id": sem_energia, "quantity": 100, "unit": "g"}], "final_weight_g": 100}
    r = _client().post("/nutrition/recipes", json=corpo)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_food"
    assert not db.recipe_versions and not [f for f in db.foods.values() if f["source"] == "recipe"]


def test_ingrediente_sem_carboidrato_e_proteina_grava_nulo_e_lista_como_incompleto(db):
    # TACO: óleo de soja sem carboidrato/proteína (NULL, nunca 0); só a energia é obrigatória na receita.
    oleo = db.add_food("Óleo", energy_kcal=884, total_fat_g=100)
    corpo = {"name": "R", "final_weight_g": 110, "ingredients": [
        {"food_id": db.arroz, "quantity": 100, "unit": "g"}, {"food_id": oleo, "quantity": 10, "unit": "g"}]}
    r = _client().post("/nutrition/recipes", json=corpo)
    assert r.status_code == 201, r.text
    linha = db.foods[r.json()["id"]]
    assert linha["carbohydrates_g"] is None and linha["protein_g"] is None
    assert float(linha["energy_kcal"]) > 0 and linha["source"] == "recipe"
    calculado = json.loads(next(iter(db.recipe_versions.values()))["calculated_json"])
    assert {"carbohydrates_g", "protein_g"} <= set(calculado["incomplete_fields"])


def test_sem_a_migracao_125_responde_503(db, monkeypatch):
    def _falta(cur):
        raise recipe_tables_missing_error()

    monkeypatch.setattr(recipes_mod, "require_recipe_tables", _falta)
    r = _client().post("/nutrition/recipes", json=_corpo(db, name="R"))
    assert r.status_code == 503 and r.json()["detail"]["migration"] == "125"
    assert _client().get("/nutrition/recipes").status_code == 503
    assert _client().get("/nutrition/recipes/1").status_code == 503


def test_listar_so_as_proprias_ativas_sem_ingredientes(db):
    minha = _client().post("/nutrition/recipes", json=_corpo(db, name="Minha", serving_size=200)).json()
    _client().put(f"/nutrition/recipes/{minha['id']}", json=_corpo(db, name="Minha", final_weight_g=300, serving_size=200))
    _client(OUTRO).post("/nutrition/recipes", json=_corpo(db, name="Do outro"))
    inativa = _client().post("/nutrition/recipes", json=_corpo(db, name="Inativa")).json()
    db.foods[inativa["id"]]["is_active"] = 0
    itens = _client().get("/nutrition/recipes").json()["items"]
    assert [i["name"] for i in itens] == ["Minha"]
    assert itens[0]["version"] == 2 and itens[0]["per_100"]["energy_kcal"] == pytest.approx(150)
    assert itens[0]["serving_size"] == 200 and "ingredients" not in itens[0]
    assert itens[0]["version_created_at"]


def test_rotas_estao_no_app_real_e_exigem_identidade():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert {"/nutrition/recipes", "/nutrition/recipes/preview", "/nutrition/recipes/{food_id}"} <= set(caminhos)
    assert TestClient(app, raise_server_exceptions=False).get("/nutrition/recipes").status_code in (401, 403)


def test_editar_trava_a_linha_do_alimento_antes_de_ler_o_maximo_da_versao(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.queries.clear()
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R2")).status_code == 200
    sqls = [s for s, _ in db.queries]
    assert sqls.index(f"{recipes_mod.foods_mod.FOOD_BY_ID_SQL} FOR UPDATE") < sqls.index(recipes_mod.MAX_VERSION_FOR_UPDATE_SQL)
    assert recipes_mod.MAX_VERSION_FOR_UPDATE_SQL.endswith(" FOR UPDATE") and recipes_mod.MAX_VERSION_SQL not in sqls
    assert sqls.index(recipes_mod.foods_mod.FOOD_BY_ID_SQL) < sqls.index(f"{recipes_mod.foods_mod.FOOD_BY_ID_SQL} FOR UPDATE")


def test_put_com_versao_duplicada_na_insercao_da_409(db, monkeypatch):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()

    def _duplica(*_a, **_k):
        raise RuntimeError("(1062, \"Duplicate entry '1-2' for key 'uq_food_version'\")")

    monkeypatch.setattr(recipes_mod, "_insert_version", _duplica)
    r = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R2"))
    assert r.status_code == 409 and "recipe_version_conflict" in r.text


def test_modelo_limita_overrides_peso_final_e_quantidade():
    from pydantic import ValidationError

    from app.domain.nutrition import catalog
    from app.request_models import RecipePreviewIn

    base = {"ingredients": [{"food_id": 1, "quantity": 10, "unit": "g"}], "final_weight_g": 100}
    RecipePreviewIn(**base)
    with pytest.raises(ValidationError):
        RecipePreviewIn(**base, overrides={f"c{i}": 1 for i in range(len(catalog.PER_100_FIELDS) + 1)})
    with pytest.raises(ValidationError):
        RecipePreviewIn(**{**base, "final_weight_g": engine.RECIPE_FINAL_WEIGHT_MAX_G + 1})
    with pytest.raises(ValidationError):
        RecipePreviewIn(**{**base, "ingredients": [{"food_id": 1, "quantity": engine.RECIPE_FINAL_WEIGHT_MAX_G + 1, "unit": "g"}]})


def test_put_em_receita_de_outro_usuario_nao_emite_for_update(db):
    alheia = _client(OUTRO).post("/nutrition/recipes", json=_corpo(db, name="Alheia")).json()
    db.queries.clear()
    assert _client().put(f"/nutrition/recipes/{alheia['id']}", json=_corpo(db, name="X")).status_code == 404
    assert not [s for s, _ in db.queries if "FOR UPDATE" in s]


def test_admin_do_catalogo_nao_ve_nem_edita_receita_privada_alheia_e_o_dono_sim(db):
    alheia = _client(OUTRO).post("/nutrition/recipes", json=_corpo(db, name="Alheia")).json()
    inexistente = _client().get("/nutrition/recipes/9999")
    admin = TestClient(_client().app, raise_server_exceptions=False)
    admin.app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=DONO, role="admin", source="session")
    db.queries.clear()
    for r in (admin.get(f"/nutrition/recipes/{alheia['id']}"), admin.put(f"/nutrition/recipes/{alheia['id']}", json=_corpo(db, name="X"))):
        assert r.status_code == 404 and r.json() == inexistente.json()
    assert not [s for s, _ in db.queries if "FOR UPDATE" in s]
    assert _client(OUTRO).get(f"/nutrition/recipes/{alheia['id']}").status_code == 200
    assert _client(OUTRO).put(f"/nutrition/recipes/{alheia['id']}", json=_corpo(db, name="Y")).status_code == 200


def test_corpo_estrito_recusa_bool_e_string_numerica_e_aceita_inteiro_e_decimal(db):
    c = _client()
    assert c.post("/nutrition/recipes/preview", json=_corpo(db, overrides={"energy_kcal": True})).status_code == 422
    assert c.post("/nutrition/recipes/preview", json=_corpo(db, final_weight_g="500")).status_code == 422
    ruim = _corpo(db)
    ruim["ingredients"][0]["quantity"] = True
    assert c.post("/nutrition/recipes/preview", json=ruim).status_code == 422
    ruim["ingredients"][0].update(quantity=100, food_id=True)
    assert c.post("/nutrition/recipes/preview", json=ruim).status_code == 422
    assert c.post("/nutrition/recipes/preview", json=_corpo(db, serving_size=engine.RECIPE_FINAL_WEIGHT_MAX_G + 1)).status_code == 422
    ok = _corpo(db, final_weight_g=250.5, overrides={"energy_kcal": 120, "protein_g": 3.5})
    ok["ingredients"][0]["quantity"] = 100.5
    assert c.post("/nutrition/recipes/preview", json=ok).status_code == 200


def test_overrides_recusam_nao_finito_e_chave_longa_mas_aceitam_null():
    from pydantic import ValidationError

    from app.request_models import RecipePreviewIn

    base = {"ingredients": [{"food_id": 1, "quantity": 1, "unit": "g"}], "final_weight_g": 100}
    assert RecipePreviewIn(**base, overrides={"protein_g": None}).overrides == {"protein_g": None}
    for ruim in ({"protein_g": float("inf")}, {"protein_g": float("nan")}, {"x" * 65: 1}):
        with pytest.raises(ValidationError):
            RecipePreviewIn(**base, overrides=ruim)


def test_put_sem_serving_size_preserva_a_porcao_e_null_explicito_apaga(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R", serving_size=300)).json()
    linha = db.foods[novo["id"]]
    linha["brand"], linha["category"] = "Casa", "Almoço"
    sem_campo = _corpo(db, name="R2")
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=sem_campo).status_code == 200
    assert float(linha["serving_size"]) == 300 and linha["brand"] == "Casa" and linha["category"] == "Almoço"
    apagada = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R3", serving_size=None))
    assert apagada.status_code == 200 and linha["serving_size"] is None and linha["brand"] == "Casa"


def test_put_com_linha_inativa_da_404_e_arquivada_entre_leitura_e_trava_tambem(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.queries.clear()
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R2")).status_code == 200
    assert db.queries[0][0] == recipes_mod.foods_mod.FOOD_BY_ID_SQL  # primeira leitura sem trava
    db.foods[novo["id"]]["is_active"] = 0  # arquivada antes da leitura
    versoes = len(db.recipe_versions)
    db.queries.clear()
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R3")).status_code == 404
    assert len(db.recipe_versions) == versoes and not [s for s, _ in db.queries if "FOR UPDATE" in s]
    # arquivada entre a leitura sem trava e a trava: a revalidação pós-trava devolve 404
    db.foods[novo["id"]]["is_active"] = 1
    original = recipes_mod._recipe_row

    def _arquiva_na_trava(cur, food_id, identity, lock=False):
        if lock:
            db.foods[food_id]["is_active"] = 0
        return original(cur, food_id, identity, lock=lock)

    recipes_mod._recipe_row = _arquiva_na_trava
    try:
        assert _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R4")).status_code == 404
    finally:
        recipes_mod._recipe_row = original
    assert len(db.recipe_versions) == versoes


def test_valores_nao_finitos_sao_recusados_no_modelo_e_nome_invalido_da_400(db):
    # Pela rota, o `Infinity` do corpo vira 500 ao serializar o erro 422 do pydantic (limite global do app, sem
    # handler de RequestValidationError); aqui basta provar que o modelo o recusa antes do motor.
    from pydantic import ValidationError

    from app.request_models import RecipePreviewIn

    for campo in ("final_weight_g", "serving_size"):
        with pytest.raises(ValidationError):
            RecipePreviewIn(**{**_corpo(db), campo: float("inf")})
    cliente = _client()
    r = cliente.post("/nutrition/recipes", json=_corpo(db, name="   "))
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_food"


def test_substituicao_nula_usa_o_calculado_e_nao_conta_como_substituida(db):
    previa = _client().post("/nutrition/recipes/preview", json=_corpo(db, overrides={"protein_g": None}))
    assert previa.status_code == 200, previa.text
    corpo = previa.json()
    assert corpo["per_100_effective"]["protein_g"] == corpo["per_100_calculated"]["protein_g"]
    assert "protein_g" not in corpo["overridden_fields"]


def _ingredientes_gravados(db, food_id, version):
    vid = next(v["id"] for v in db.recipe_versions.values() if v["food_id"] == food_id and v["version"] == version)
    return sorted((i for i in db.recipe_ingredients.values() if i["version_id"] == vid), key=lambda i: i["position"])


def _kcal(ingrediente):
    return json.loads(ingrediente["catalog_snapshot_json"])["food"]["per_100"]["energy_kcal"]


def test_ingrediente_acima_de_2000_g_ate_20000_g_e_aceito_e_acima_do_teto_da_422(db):
    corpo = _corpo(db, final_weight_g=12000)
    corpo["ingredients"][0]["quantity"] = 10000
    r = _client().post("/nutrition/recipes/preview", json=corpo)
    assert r.status_code == 200, r.text
    assert r.json()["ingredients"][0]["amount"] == 10000
    corpo["ingredients"][0].update(quantity=1000, unit="portion", portion_id=db.add_portion(db.arroz, "Prato", 30))
    r = _client().post("/nutrition/recipes/preview", json=corpo)  # 1000 x 30 g = 30.000 g na base
    assert r.status_code == 422 and _codigo(r) == "recipe_ingredient_amount_out_of_range:0"
    assert r.json()["detail"]["message"].startswith("Ingrediente 1: ") and "20000" in r.json()["detail"]["message"]


def test_editar_sem_refresh_preserva_o_snapshot_mesmo_com_catalogo_alterado(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.foods[db.arroz]["energy_kcal"] += 100  # catálogo mudou
    r = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R"))
    assert r.status_code == 200, r.text
    assert float(db.foods[novo["id"]]["energy_kcal"]) == pytest.approx(180)  # valores gravados, não os do catálogo
    assert _kcal(_ingredientes_gravados(db, novo["id"], 2)[0]) == 130
    assert [i["catalog_status"] for i in r.json()["recipe"]["ingredients"]] == ["outdated", "current"]


def test_chave_com_unidade_e_quantidade_em_formato_diferente_reaproveita_e_porcao_nula_tambem(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    for ing in _ingredientes_gravados(db, novo["id"], 1):
        ing["unit"], ing["quantity"] = " G ", Decimal("100.0") if ing["position"] == 0 else Decimal("200.000")
        assert ing["portion_id"] is None
    db.foods[db.arroz]["energy_kcal"] += 100
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R")).status_code == 200
    assert _kcal(_ingredientes_gravados(db, novo["id"], 2)[0]) == 130  # snapshot reaproveitado


@pytest.mark.parametrize("bruto", [None, "null", "{bad", '{"portion": null}', '{"food": {"name": "x"}}', "[]"])
def test_snapshot_gravado_invalido_nao_e_reaproveitado_e_resolve_do_catalogo(db, bruto):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    _ingredientes_gravados(db, novo["id"], 1)[0]["catalog_snapshot_json"] = bruto
    db.foods[db.arroz]["energy_kcal"] += 100
    r = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R"))
    assert r.status_code == 200, r.text
    assert [_kcal(i) for i in _ingredientes_gravados(db, novo["id"], 2)] == [230, 160]


def test_put_le_a_versao_atual_com_for_update_depois_da_trava_da_linha(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.queries.clear()
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R")).status_code == 200
    sqls = [s for s, _ in db.queries]
    trava = sqls.index(f"{recipes_mod.foods_mod.FOOD_BY_ID_SQL} FOR UPDATE")
    atual = f"{recipes_mod.CURRENT_VERSION_SQL} FOR UPDATE"
    ingredientes = f"{recipes_mod.INGREDIENTS_OF_VERSION_SQL} FOR UPDATE"
    assert trava < sqls.index(atual) < sqls.index(ingredientes) < sqls.index(recipes_mod.MAX_VERSION_FOR_UPDATE_SQL)
    assert recipes_mod.CURRENT_VERSION_SQL not in sqls[: sqls.index(ingredientes)]


def test_ingrediente_alterado_resolve_do_catalogo_e_refresh_resolve_todos(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.foods[db.arroz]["energy_kcal"] += 100
    db.foods[db.frango]["energy_kcal"] += 100
    corpo = _corpo(db, name="R")
    corpo["ingredients"][1]["quantity"] = 150  # só o frango mudou
    assert _client().put(f"/nutrition/recipes/{novo['id']}", json=corpo).status_code == 200
    assert [_kcal(i) for i in _ingredientes_gravados(db, novo["id"], 2)] == [130, 260]
    r = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R", refresh_ingredients=True))
    assert r.status_code == 200
    assert [_kcal(i) for i in _ingredientes_gravados(db, novo["id"], 3)] == [230, 260]
    assert [i["catalog_status"] for i in r.json()["recipe"]["ingredients"]] == ["current", "current"]


def test_ingrediente_reaproveitado_inacessivel_continua_valido_e_aparece_como_indisponivel(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.foods[db.frango]["is_active"] = 0
    lida = _client().get(f"/nutrition/recipes/{novo['id']}").json()
    assert [i["catalog_status"] for i in lida["recipe"]["ingredients"]] == ["current", "unavailable"]
    r = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R"))
    assert r.status_code == 200, r.text
    assert r.json()["recipe"]["version"] == 2
    refresh = _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R", refresh_ingredients=True))
    assert refresh.status_code == 422 and _codigo(refresh) == "recipe_ingredient_not_found:1"


def test_leitura_consulta_os_alimentos_da_versao_uma_vez(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    db.queries.clear()
    _client().get(f"/nutrition/recipes/{novo['id']}")
    assert len([s for s, _ in db.queries if s == recipes_mod.meals_mod.foods_by_ids_sql(2)]) == 1


def test_versoes_so_para_o_dono_com_energia_efetiva(db):
    novo = _client().post("/nutrition/recipes", json=_corpo(db, name="R")).json()
    _client().put(f"/nutrition/recipes/{novo['id']}", json=_corpo(db, name="R", final_weight_g=300))
    r = _client().get(f"/nutrition/recipes/{novo['id']}/versions")
    assert r.status_code == 200, r.text
    itens = r.json()["items"]
    assert [i["version"] for i in itens] == [2, 1]
    assert itens[0]["final_weight_g"] == 300 and itens[0]["energy_kcal_per_100g"] == pytest.approx(150)
    assert itens[1]["energy_kcal_per_100g"] == pytest.approx(180) and itens[1]["created_at"]
    inexistente = _client(OUTRO).get("/nutrition/recipes/9999/versions")
    alheia = _client(OUTRO).get(f"/nutrition/recipes/{novo['id']}/versions")
    assert alheia.status_code == inexistente.status_code == 404 and alheia.json() == inexistente.json()


def test_duplicar_cria_receita_privada_nova_com_snapshot_ajustes_e_modo_de_preparo(db):
    corpo = _corpo(db, name="Original", instructions="Misturar.", serving_size=200, overrides={"energy_kcal": 170})
    novo = _client().post("/nutrition/recipes", json=corpo).json()
    db.foods[db.arroz]["energy_kcal"] += 100  # a cópia leva o snapshot, não o catálogo
    r = _client().post(f"/nutrition/recipes/{novo['id']}/duplicate")
    assert r.status_code == 201, r.text
    copia = r.json()
    assert copia["id"] != novo["id"] and copia["name"] == "Original (cópia)" and copia["is_own"] and not copia["is_public"]
    assert copia["recipe"]["version"] == 1 and copia["recipe"]["instructions"] == "Misturar."
    assert copia["recipe"]["final_weight_g"] == 250 and copia["recipe"]["effective"]["overridden_fields"] == ["energy_kcal"]
    linha = db.foods[copia["id"]]
    assert float(linha["energy_kcal"]) == pytest.approx(170) and float(linha["serving_size"]) == 200
    assert linha["owner_user_id"] == DONO and db.foods[novo["id"]]["name"] == "Original"
    assert _kcal(_ingredientes_gravados(db, copia["id"], 1)[0]) == 130
    assert _client(OUTRO).post(f"/nutrition/recipes/{novo['id']}/duplicate").status_code == 404


# ---------------------------------------------------------------------------
# Publicação (#286, parte 2): leitura, edição, duplicação e listagem da receita pública
# ---------------------------------------------------------------------------


def _publica(db, user: int = DONO, **extra) -> int:
    """Receita criada pela rota e tornada pública (o que a promoção do catálogo faz: dono NULL)."""
    rid = _client(user).post("/nutrition/recipes", json=_corpo(db, name="Pública", instructions="Cozinhar.", **extra)).json()["id"]
    db.foods[rid]["owner_user_id"] = None
    return rid


def test_publica_e_lida_por_qualquer_usuario_com_ingredientes_e_versoes_privada_alheia_segue_404(db):
    rid = _publica(db)
    r = _client(OUTRO).get(f"/nutrition/recipes/{rid}")
    assert r.status_code == 200 and r.json()["is_public"] and not r.json()["is_own"]
    assert len(r.json()["recipe"]["ingredients"]) == 2 and r.json()["recipe"]["instructions"] == "Cozinhar."
    assert [v["version"] for v in _client(OUTRO).get(f"/nutrition/recipes/{rid}/versions").json()["items"]] == [1]
    privada = _client().post("/nutrition/recipes", json=_corpo(db, name="P")).json()["id"]
    assert _client(OUTRO).get(f"/nutrition/recipes/{privada}").json() == _client(OUTRO).get("/nutrition/recipes/9999").json()
    db.foods[rid]["is_active"] = 0
    assert _client(OUTRO).get(f"/nutrition/recipes/{rid}").status_code == 404


def test_editar_publica_so_o_admin_do_catalogo_cria_versao_e_o_autor_comum_leva_403_tipado(db):
    rid = _publica(db)
    for quem in (_client(OUTRO), _client(DONO)):  # inclusive o autor original, sem `admin.catalogos`
        r = quem.put(f"/nutrition/recipes/{rid}", json=_corpo(db, name="X"))
        assert r.status_code == 403 and _codigo(r) == "recipe_public_edit_forbidden"
        assert "_" not in r.json()["detail"]["message"]
    assert len(db.recipe_versions) == 1 and db.foods[rid]["name"] == "Pública"
    ok = _client(99, "admin").put(f"/nutrition/recipes/{rid}", json=_corpo(db, name="Revisada", final_weight_g=300))
    assert ok.status_code == 200, ok.text
    assert ok.json()["recipe"]["version"] == 2 and ok.json()["is_public"] and db.foods[rid]["owner_user_id"] is None
    assert sorted(v["version"] for v in db.recipe_versions.values()) == [1, 2]


def test_duplicar_publica_de_outro_gera_copia_privada_do_chamador(db):
    rid = _publica(db)
    r = _client(OUTRO).post(f"/nutrition/recipes/{rid}/duplicate")
    assert r.status_code == 201, r.text
    copia = r.json()
    assert copia["is_own"] and not copia["is_public"] and db.foods[copia["id"]]["owner_user_id"] == OUTRO
    assert copia["recipe"]["instructions"] == "Cozinhar." and db.foods[rid]["owner_user_id"] is None


def test_listar_por_escopo_mine_public_favorites(db):
    minha = _client().post("/nutrition/recipes", json=_corpo(db, name="Minha")).json()["id"]
    publica = _publica(db, OUTRO)
    alheia = _client(OUTRO).post("/nutrition/recipes", json=_corpo(db, name="Alheia privada")).json()["id"]

    def ids(escopo=None, user=DONO):
        params = {} if escopo is None else {"scope": escopo}
        return [i["id"] for i in _client(user).get("/nutrition/recipes", params=params).json()["items"]]

    assert ids() == ids("mine") == [minha]
    assert ids("public") == [publica] and ids("favorites") == []
    db.favorites += [{"user_id": DONO, "food_id": f} for f in (minha, publica, alheia, db.arroz)]
    assert sorted(ids("favorites")) == sorted([minha, publica])  # nem a privada alheia nem alimento comum
    item = _client().get("/nutrition/recipes", params={"scope": "public"}).json()["items"][0]
    assert item["is_public"] and not item["is_own"]
    assert _client().get("/nutrition/recipes", params={"scope": "todas"}).status_code == 422


def test_listar_publicas_nao_tem_n_mais_1(db):
    for _ in range(3):
        _publica(db, OUTRO)
    db.queries.clear()
    assert len(_client().get("/nutrition/recipes", params={"scope": "public"}).json()["items"]) == 3
    assert len([s for s, _ in db.queries if s.startswith("SELECT food_id, version")]) == 1


def test_ingrediente_privado_do_autor_so_aparece_pelo_snapshot_e_sem_status_para_os_outros(db):
    rid = _client(OUTRO).post(
        "/nutrition/recipes",
        json={"name": "Com molho", "ingredients": [{"food_id": db.privado, "quantity": 100, "unit": "g"}], "final_weight_g": 100},
    ).json()["id"]
    db.foods[rid]["owner_user_id"] = None
    db.foods[db.privado]["energy_kcal"] = Decimal("999")  # o alimento privado mudou depois
    visto = _client(DONO).get(f"/nutrition/recipes/{rid}").json()["recipe"]["ingredients"][0]
    assert visto["per_100"]["energy_kcal"] == 50 and visto["catalog_status"] == "unavailable"
    assert "999" not in json.dumps(visto)
    autor = _client(OUTRO).get(f"/nutrition/recipes/{rid}").json()["recipe"]["ingredients"][0]
    assert autor["per_100"]["energy_kcal"] == 50 and autor["catalog_status"] == "outdated"

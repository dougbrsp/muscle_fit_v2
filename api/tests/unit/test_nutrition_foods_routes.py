"""Rotas do catálogo de alimentos (`app/nutrition_foods.py`, L3a) sem banco real:
busca (FULLTEXT do motor + ordem pela nota de `food_text`), visibilidade D-G
(privado do dono, público = TACO ou promovido), cadastro com as regras do motor
(400 com códigos; Atwater vira `warnings`), edição (dono/admin; TACO 409),
porções, promoção pelo admin e 503 com as tabelas ausentes. O router é
conferido também no `app.main.app` real.

Banco em memória: `tests/support/nutrition_foods_fake.py`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_foods as foods_mod
from app.domain.nutrition import catalog, food_text
from app.nutrition_schema import food_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_foods_fake import FakeFoodsDB

ADMIN, ALUNO, OUTRO = 1, 2, 3

ARROZ = {"name": "Arroz caseiro", "energy_kcal": 128, "carbohydrates_g": 28.1, "protein_g": 2.5, "total_fat_g": 0.2}


@pytest.fixture
def db(monkeypatch):
    banco = FakeFoodsDB()
    banco.add_food("Arroz, tipo 1, cozido", energy_kcal=128.26, carbohydrates_g=28.06, protein_g=2.52, total_fat_g=0.23,
                   fiber_g=1.56, sodium_mg=1.2)
    banco.add_food("Arroz, integral, cozido", energy_kcal=123.53, carbohydrates_g=25.81, protein_g=2.59, total_fat_g=1.0)
    banco.add_food("Frango, peito, sem pele, grelhado", energy_kcal=159.19, protein_g=32.03, total_fat_g=2.48,
                   carbohydrates_g=0)
    banco.add_food("Frango, coração, grelhado", energy_kcal=207.31, protein_g=22.44, total_fat_g=12.12, carbohydrates_g=0.6)
    banco.add_food("Ovo, de galinha, inteiro, cozido/10minutos", energy_kcal=145.7, protein_g=13.29, total_fat_g=9.48,
                   carbohydrates_g=0.61)
    banco.add_food("Arroz da vovó", source="user", owner=ALUNO, energy_kcal=140, carbohydrates_g=30, protein_g=3,
                   total_fat_g=1)
    banco.add_food("Arroz inativo", source="user", owner=None, active=False, energy_kcal=1, carbohydrates_g=0,
                   protein_g=0, total_fat_g=0)
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    monkeypatch.setattr(foods_mod, "require_food_tables", lambda cur: None)
    return banco


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _busca(cliente, q, **extra):
    resposta = cliente.get("/nutrition/foods/search", params={"q": q, **extra})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


# ---------------------------------------------------------------------------
# Router no app real (o try/except do main.py engoliria erro de import)
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/nutrition/foods/search", "/nutrition/foods", "/nutrition/foods/{food_id}",
                    "/nutrition/foods/{food_id}/portions", "/nutrition/foods/{food_id}/promote"):
        assert caminho in caminhos, caminho
    resposta = TestClient(app, raise_server_exceptions=False).get("/nutrition/foods/search?q=arroz")
    assert resposta.status_code in (401, 403)  # existe e exige identidade (não 404)


def test_tabelas_ausentes_respondem_503_citando_a_080(db, monkeypatch):
    def _sem_tabelas(cur):
        raise food_tables_missing_error()

    monkeypatch.setattr(foods_mod, "require_food_tables", _sem_tabelas)
    cliente = _client()
    for resposta in (cliente.get("/nutrition/foods/search?q=arroz"), cliente.get("/nutrition/foods/1"),
                     cliente.post("/nutrition/foods", json=ARROZ)):
        assert resposta.status_code == 503
        assert resposta.json()["detail"]["error"] == "food_catalog_tables_missing"
        assert resposta.json()["detail"]["migration"] == "080/081"


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------


def test_busca_ordena_pela_nota_do_motor_e_traz_a_citacao(db):
    corpo = _busca(_client(), "arroz branco cozido")
    assert corpo["normalized"] == "arroz tipo 1 cozido"
    primeiro = corpo["items"][0]
    assert primeiro["name"] == "Arroz, tipo 1, cozido"
    assert primeiro["score"] == 1.0 and primeiro["auto_accept"] is True
    assert primeiro["citation"] == catalog.TACO_CITATION and primeiro["license"] == catalog.TACO_LICENSE
    assert primeiro["per_100"]["energy_kcal"] == 128.26 and primeiro["per_100"]["total_sugars_g"] is None
    assert primeiro["is_public"] is True and primeiro["permissions"] == {
        "can_edit": False, "can_add_portion": True, "can_promote": False}
    notas = [item["score"] for item in corpo["items"]]
    assert notas == sorted(notas, reverse=True)
    sql, params = next(q for q in db.queries if q[0].startswith("SELECT") and "MATCH" in q[0])
    assert params[0] == "arroz* tipo* cozido*" and params[-1] == food_text.SEARCH_CANDIDATE_POOL


def test_busca_ambigua_nao_pre_seleciona(db):
    corpo = _busca(_client(), "frango grelhado")
    nomes = [item["name"] for item in corpo["items"]]
    assert "Frango, peito, sem pele, grelhado" in nomes[:3]
    assert not any(item["auto_accept"] for item in corpo["items"])


def test_publicos_e_os_do_proprio_usuario_nunca_o_privado_de_outro(db):
    assert "Arroz da vovó" in [i["name"] for i in _busca(_client(user_id=ALUNO), "arroz")["items"]]
    nomes_outro = [i["name"] for i in _busca(_client(user_id=OUTRO), "arroz")["items"]]
    assert "Arroz da vovó" not in nomes_outro and "Arroz inativo" not in nomes_outro
    proprio = next(i for i in _busca(_client(user_id=ALUNO), "arroz")["items"] if i["name"] == "Arroz da vovó")
    assert proprio["is_own"] and not proprio["is_public"] and proprio["permissions"]["can_edit"]


def test_filtro_de_fonte_e_limite(db):
    so_usuario = _busca(_client(), "arroz", source="user")["items"]
    assert [i["name"] for i in so_usuario] == ["Arroz da vovó"]
    assert len(_busca(_client(), "arroz", limit=1)["items"]) == 1
    assert _client().get("/nutrition/foods/search", params={"q": "arroz", "source": "tbca"}).status_code == 400


def test_termo_curto_usa_o_prefixo_e_texto_vazio_nao_consulta(db):
    corpo = _busca(_client(), "ov")
    assert [i["name"] for i in corpo["items"]] == ["Ovo, de galinha, inteiro, cozido/10minutos"]
    assert any("LIKE" in sql and params[0] == "ov%" for sql, params in db.queries)
    antes = len(db.queries)
    assert _busca(_client(), "!!!")["items"] == [] and len(db.queries) == antes


def test_escopo_dos_privados_e_so_do_admin(db):
    admin = _busca(_client("admin", ADMIN), "arroz", scope="private_all")["items"]
    assert [i["name"] for i in admin] == ["Arroz da vovó"] and admin[0]["permissions"]["can_promote"] is True
    assert _client().get("/nutrition/foods/search", params={"q": "arroz", "scope": "private_all"}).status_code == 403
    assert _client().get("/nutrition/foods/search", params={"q": "arroz", "scope": "tudo"}).status_code == 400


# ---------------------------------------------------------------------------
# Detalhe, cadastro e edição
# ---------------------------------------------------------------------------


def test_detalhe_com_porcoes_visiveis(db):
    db.add_portion(1, "1 escumadeira", 45)
    db.add_portion(1, "minha colher", 25, owner=ALUNO)
    db.add_portion(1, "colher do outro", 20, owner=OUTRO)
    corpo = _client().get("/nutrition/foods/1").json()
    assert [p["label"] for p in corpo["portions"]] == ["minha colher", "1 escumadeira"]
    assert corpo["portions"][0]["is_own"] and corpo["portions"][1]["is_public"]


def test_admin_ve_as_porcoes_do_dono_do_privado_antes_de_promover(db):
    """A promoção leva as porções do dono: o admin precisa vê-las no detalhe
    (`nutrition_access.portions_visibility`), e outro usuário não."""
    db.add_portion(6, "1 concha", 120, owner=ALUNO)
    db.add_portion(6, "colher do admin", 10, owner=ADMIN)
    db.add_portion(1, "porção do aluno na TACO", 50, owner=ALUNO)
    admin = _client("admin", ADMIN).get("/nutrition/foods/6").json()
    assert sorted(p["label"] for p in admin["portions"]) == ["1 concha", "colher do admin"]
    taco = _client("admin", ADMIN).get("/nutrition/foods/1").json()
    assert taco["portions"] == []  # num público, a porção privada de outro continua invisível


def test_food_item_in_tem_exatamente_os_campos_do_catalogo():
    """`FoodItemIn` (extra="forbid") escreve os campos à mão: se o motor ganhar
    um campo e o modelo não, a tela (que monta o corpo pelo JSON gerado) leva 422."""
    from app.request_models import FoodItemIn

    assert set(FoodItemIn.model_fields) == {
        "name", "brand", "category", "basis", *catalog.PER_100_FIELDS, *catalog.SERVING_FIELDS}


def test_privado_de_outro_e_404_e_o_admin_ve(db):
    assert _client(user_id=OUTRO).get("/nutrition/foods/6").status_code == 404
    assert _client("admin", ADMIN).get("/nutrition/foods/6").status_code == 200
    assert _client().get("/nutrition/foods/999").status_code == 404


def test_cadastro_nasce_privado_com_avisos_do_motor(db):
    resposta = _client().post("/nutrition/foods", json={**ARROZ, "energy_kcal": 300, "brand": "  Marca  X "})
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["source"] == catalog.USER_ENTRY_SOURCE.value and corpo["is_own"] and not corpo["is_public"]
    assert corpo["brand"] == "Marca X" and corpo["basis"] == "g" and corpo["citation"] is None
    assert [w["code"] for w in corpo["warnings"]] == [catalog.WARN_ATWATER]
    assert corpo["atwater"]["consistent"] is False
    novo = db.foods[corpo["id"]]
    assert novo["owner_user_id"] == ALUNO and novo["created_by"] == ALUNO and novo["name_normalized"] == "arroz caseiro"
    # Entra na busca do dono (FULLTEXT sobre o nome normalizado).
    assert "Arroz caseiro" in [i["name"] for i in _busca(_client(), "arroz caseiro")["items"]]


def test_cadastro_fora_das_regras_do_motor_e_400_com_codigos(db):
    resposta = _client().post("/nutrition/foods", json={"name": "...", "energy_kcal": -1, "basis": "kg"})
    assert resposta.status_code == 400
    detalhe = resposta.json()["detail"]
    codigos = [e["code"] for e in detalhe["errors"]]
    assert detalhe["error"] == "invalid_food"
    assert codigos[0] == catalog.ERR_INVALID_NAME and catalog.ERR_INVALID_BASIS in codigos
    assert all(e["label"] for e in detalhe["errors"])


def test_contrato_estrito_recusa_campo_desconhecido(db):
    assert _client().post("/nutrition/foods", json={**ARROZ, "kcal": 1}).status_code == 422
    # L6: o POST aceita `source`, mas só as fontes do cadastro do usuário (motor); o PUT não aceita.
    taco = _client().post("/nutrition/foods", json={**ARROZ, "source": "taco"})
    assert taco.status_code == 400
    assert [e["code"] for e in taco.json()["detail"]["errors"]] == [catalog.ERR_INVALID_SOURCE]
    assert _client().put("/nutrition/foods/6", json={**ARROZ, "source": "label_photo"}).status_code == 422


def test_cadastro_pela_foto_do_rotulo_grava_a_fonte_do_motor(db):
    """L6: o formulário pré-preenchido pela leitura da foto é salvo com
    `source=label_photo` (privado do usuário, como o cadastro manual)."""
    resposta = _client().post("/nutrition/foods", json={**ARROZ, "source": catalog.FoodSource.LABEL_PHOTO.value})
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["source"] == "label_photo" and corpo["is_own"] and not corpo["is_public"]
    assert db.foods[corpo["id"]]["source"] == "label_photo"
    assert corpo["permissions"]["can_edit"] is True  # não é fonte somente leitura


def test_edicao_dono_admin_e_taco(db):
    aluno, outro, admin = _client(), _client(user_id=OUTRO), _client("admin", ADMIN)
    assert aluno.put("/nutrition/foods/6", json={**ARROZ, "name": "Arroz da vó"}).json()["name"] == "Arroz da vó"
    assert outro.put("/nutrition/foods/6", json=ARROZ).status_code == 404  # privado de outro: nem existe
    taco = aluno.put("/nutrition/foods/1", json=ARROZ)
    assert taco.status_code == 409 and taco.json()["detail"]["error"] == catalog.ERR_SOURCE_READ_ONLY
    assert admin.put("/nutrition/foods/1", json=ARROZ).status_code == 409  # TACO nem para o admin
    publico = db.add_food("Pão caseiro", source="user", owner=None, energy_kcal=250, carbohydrates_g=50, protein_g=8,
                          total_fat_g=2)
    assert aluno.put(f"/nutrition/foods/{publico}", json=ARROZ).status_code == 403
    assert admin.put(f"/nutrition/foods/{publico}", json=ARROZ).status_code == 200


def test_id_inexistente_e_404_antes_da_regra_da_taco(db):
    """404 (`food_not_found`) para id que não existe, mesmo para o admin; o 409
    `food_source_read_only` só vale para a linha da TACO que existe. O id da TACO
    NÃO é estável (AUTO_INCREMENT; recarregar o seed troca os ids): quem precisa
    de uma linha da TACO a acha pela busca ou pelo `source_id`, nunca por id fixo
    (causa da falha do CI no 2749bad: a integração usava `/nutrition/foods/1`
    depois de um teste recarregar a TACO)."""
    admin = _client("admin", ADMIN)
    assert admin.put("/nutrition/foods/999", json=ARROZ).json()["detail"]["error"] == "food_not_found"
    del db.foods[1]  # "recarga" da TACO: a linha 3 da TACO volta com outro id
    nova = db.add_food("Arroz, tipo 1, cozido", energy_kcal=128.26, carbohydrates_g=28.06, protein_g=2.52,
                       total_fat_g=0.23)
    assert admin.put("/nutrition/foods/1", json=ARROZ).status_code == 404
    taco = next(i for i in _busca(admin, "arroz branco cozido")["items"] if i["source"] == "taco")
    assert taco["id"] == nova
    resposta = admin.put(f"/nutrition/foods/{taco['id']}", json=ARROZ)
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == catalog.ERR_SOURCE_READ_ONLY


def test_edicao_invalida_nao_grava(db):
    resposta = _client().put("/nutrition/foods/6", json={"name": "Arroz da vovó", "energy_kcal": 140})
    assert resposta.status_code == 400
    assert db.foods[6]["carbohydrates_g"] is not None


# ---------------------------------------------------------------------------
# Porções e promoção
# ---------------------------------------------------------------------------


def test_porcao_do_usuario_e_privada_e_a_do_admin_no_publico_e_publica(db):
    privada = _client().post("/nutrition/foods/1/portions", json={"label": " 1  escumadeira ", "amount": 45})
    assert privada.status_code == 201 and privada.json()["label"] == "1 escumadeira" and privada.json()["is_own"]
    publica = _client("admin", ADMIN).post("/nutrition/foods/1/portions", json={"label": "1 colher", "amount": 25})
    assert publica.json()["is_public"] is True
    assert _client(user_id=OUTRO).post("/nutrition/foods/6/portions", json={"label": "x", "amount": 1}).status_code == 404


@pytest.mark.parametrize("quantidade", [0, -5, catalog.PORTION_AMOUNT_MAX + 1])
def test_porcao_fora_da_faixa_do_motor_e_400(db, quantidade):
    resposta = _client().post("/nutrition/foods/1/portions", json={"label": "x", "amount": quantidade})
    assert resposta.status_code == 400
    assert resposta.json()["detail"]["errors"][0]["code"] == catalog.ERR_INVALID_PORTION_AMOUNT


def test_promocao_pelo_admin_leva_as_porcoes_do_dono(db):
    db.add_portion(6, "1 concha", 120, owner=ALUNO)
    assert _client().post("/nutrition/foods/6/promote").status_code == 403
    resposta = _client("admin", ADMIN).post("/nutrition/foods/6/promote")
    assert resposta.status_code == 200 and resposta.json()["is_public"] is True
    assert db.foods[6]["owner_user_id"] is None and all(p["owner_user_id"] is None for p in db.portions.values())
    assert "Arroz da vovó" in [i["name"] for i in _busca(_client(user_id=OUTRO), "arroz")["items"]]
    assert _client("admin", ADMIN).post("/nutrition/foods/6/promote").status_code == 409
    assert _client("admin", ADMIN).post("/nutrition/foods/1/promote").status_code == 409  # TACO já é pública

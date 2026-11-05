"""N3: cadastro manual com o EAN lido pela câmera (ou digitado) quando o Open Food
Facts não acha o produto (`POST /nutrition/foods` com `barcode`) e a consulta que
reencontra o EAN cadastrado à mão. Banco em memória e clientes HTTP trocados por
dublês, como em `test_nutrition_external_foods_routes.py`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import external_food_sources as ext
from app import nutrition_external_foods as rotas
from app import nutrition_foods as foods_mod
from app.domain.nutrition import external_sources as fontes
from app.security import Identity, get_current_identity
from tests.support import nutrition_external_payloads as payloads
from tests.support.nutrition_foods_fake import FakeFoodsDB

ALUNO, OUTRO = 2, 3
MOCA = payloads.OFF_MOCA_CODE
MANUAL = {"name": "Barra da vovó", "energy_kcal": 400, "carbohydrates_g": 60, "protein_g": 8, "total_fat_g": 14}


@pytest.fixture
def db(monkeypatch):
    banco = FakeFoodsDB()
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    for modulo in (foods_mod, rotas):
        monkeypatch.setattr(modulo, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(rotas, "_off_day_start", lambda conn, user_id: banco.now)
    return banco


@pytest.fixture
def chamadas_ao_off(monkeypatch):
    chamadas: list[str] = []

    def _off(code, *, fields):
        chamadas.append(code)
        return payloads.load(payloads.OFF_MOCA)

    monkeypatch.setattr(ext, "off_product", _off)
    return chamadas


def _client(user_id: int = ALUNO, role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(rotas.router)
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_cadastro_manual_grava_o_ean_canonico_validado_pelo_dono_do_gtin(db):
    # O GTIN-14 com zero à esquerda vira o EAN-13 canônico (a mesma chave da consulta).
    resposta = _client().post("/nutrition/foods", json={**MANUAL, "barcode": f" 0{MOCA} "})
    assert resposta.status_code == 201, resposta.text
    assert resposta.json()["barcode"] == MOCA and resposta.json()["is_own"] is True
    [linha] = [r for r in db.foods.values() if r["name"] == "Barra da vovó"]
    assert linha["barcode"] == MOCA and linha["owner_user_id"] == ALUNO and linha["source"] == "user"


def test_cadastro_manual_sem_ean_continua_valendo(db):
    resposta = _client().post("/nutrition/foods", json={**MANUAL, "barcode": "  "})
    assert resposta.status_code == 201 and resposta.json()["barcode"] is None
    assert _client().post("/nutrition/foods", json={**MANUAL, "name": "Outra"}).json()["barcode"] is None


def test_cadastro_manual_com_ean_invalido_e_400_e_nao_grava(db):
    resposta = _client().post("/nutrition/foods", json={**MANUAL, "barcode": "7891000100104"})
    assert resposta.status_code == 400
    assert resposta.json()["detail"]["error"] == fontes.ERR_INVALID_BARCODE
    assert not [r for r in db.foods.values() if r["name"] == "Barra da vovó"]


def test_ean_ja_cadastrado_responde_409_oferecendo_o_item_existente(db):
    cliente = _client()
    primeiro = cliente.post("/nutrition/foods", json={**MANUAL, "barcode": MOCA}).json()
    repetido = cliente.post("/nutrition/foods", json={**MANUAL, "name": "Outra barra", "barcode": MOCA})
    assert repetido.status_code == 409
    detalhe = repetido.json()["detail"]
    assert detalhe["error"] == "barcode_already_registered"
    assert detalhe["existing_food_id"] == primeiro["id"] and detalhe["existing_food_name"] == "Barra da vovó"
    assert len([r for r in db.foods.values() if r["barcode"] == MOCA]) == 1


def test_produto_publico_conflita_para_todos_e_o_privado_de_outro_nao(db):
    # Privado de OUTRO usuário não é visível: não conflita nem vaza o item.
    assert _client(OUTRO).post("/nutrition/foods", json={**MANUAL, "barcode": MOCA}).status_code == 201
    meu = _client().post("/nutrition/foods", json={**MANUAL, "name": "Minha barra", "barcode": MOCA})
    assert meu.status_code == 201
    # Produto público (ex.: trazido do OFF) conflita para todos.
    outro_ean = "7891000315507"
    assert fontes.normalize_gtin(outro_ean) == outro_ean
    db.add_food("Produto OFF", source="off", source_id=outro_ean, barcode=outro_ean, energy_kcal=300,
                carbohydrates_g=50, protein_g=5, total_fat_g=8)
    conflito = _client().post("/nutrition/foods", json={**MANUAL, "name": "Cópia", "barcode": outro_ean})
    assert conflito.status_code == 409 and conflito.json()["detail"]["existing_food_name"] == "Produto OFF"


def test_ean_cadastrado_a_mao_e_achado_na_consulta_sem_gastar_o_off(db, chamadas_ao_off):
    cliente = _client()
    criado = cliente.post("/nutrition/foods", json={**MANUAL, "barcode": MOCA}).json()
    achado = cliente.get(f"/nutrition/foods/barcode/{MOCA}")
    assert achado.status_code == 200, achado.text
    assert achado.json()["id"] == criado["id"] and achado.json()["from_cache"] is True
    assert chamadas_ao_off == []
    assert not [q for q in db.queries if q[0] == rotas.OFF_LOOKUPS_TODAY_SQL]
    # outro usuário não enxerga o privado: cai no OFF
    outro = _client(OUTRO).get(f"/nutrition/foods/barcode/{MOCA}")
    assert outro.status_code == 200 and outro.json()["from_cache"] is False and chamadas_ao_off == [MOCA]


def test_linha_do_off_inativa_nao_impede_o_cadastro_local_nem_a_oferta_de_cadastro(db, chamadas_ao_off):
    db.add_food("Produto OFF desativado", source="off", source_id=MOCA, barcode=MOCA, active=False,
                energy_kcal=300, carbohydrates_g=50, protein_g=5, total_fat_g=8)
    cliente = _client()
    # Sem cadastro local: 404 (a tela oferece o cadastro) e nenhuma chamada nem consulta contada ao OFF.
    vazio = cliente.get(f"/nutrition/foods/barcode/{MOCA}")
    assert vazio.status_code == 404 and vazio.json()["detail"]["error"] == "food_not_found"
    assert chamadas_ao_off == [] and not [q for q in db.queries if q[0] == rotas.OFF_LOOKUPS_TODAY_SQL]
    # O cadastro manual do mesmo EAN passa (a linha inativa não conflita) e a consulta o encontra.
    criado = cliente.post("/nutrition/foods", json={**MANUAL, "barcode": MOCA})
    assert criado.status_code == 201
    achado = cliente.get(f"/nutrition/foods/barcode/{MOCA}")
    assert achado.status_code == 200 and achado.json()["id"] == criado.json()["id"]


ADMIN = 1


def _dono(db, nome):
    [linha] = [r for r in db.foods.values() if r["name"] == nome]
    return linha["owner_user_id"]


def test_admin_com_ean_ou_rotulo_nasce_publico_e_digitado_sem_ean_fica_privado(db):
    admin = _client(ADMIN, "admin")
    com_ean = admin.post("/nutrition/foods", json={**MANUAL, "name": "Com EAN", "barcode": MOCA})
    assert com_ean.status_code == 201, com_ean.text
    assert com_ean.json()["is_public"] is True and com_ean.json()["permissions"]["can_promote"] is False
    assert _dono(db, "Com EAN") is None
    rotulo = admin.post("/nutrition/foods", json={**MANUAL, "name": "Do rotulo", "source": "label_photo"})
    assert rotulo.json()["is_public"] is True and _dono(db, "Do rotulo") is None
    assert rotulo.json()["origin"] == {"code": "label_photo", "label": "Rótulo (IA)"}
    digitado = admin.post("/nutrition/foods", json={**MANUAL, "name": "Digitado"})
    assert digitado.json()["is_public"] is False and _dono(db, "Digitado") == ADMIN
    assert digitado.json()["permissions"]["can_promote"] is True
    assert digitado.json()["origin"] == {"code": "typed", "label": "Digitado"}


def test_usuario_comum_continua_privado_nos_tres_casos_e_nao_promove(db):
    comum = _client()
    for nome, extra in (("A", {"barcode": MOCA}), ("B", {"source": "label_photo"}), ("C", {})):
        resposta = comum.post("/nutrition/foods", json={**MANUAL, "name": nome, **extra})
        assert resposta.status_code == 201, resposta.text
        assert resposta.json()["is_public"] is False and _dono(db, nome) == ALUNO
        assert comum.post(f"/nutrition/foods/{resposta.json()['id']}/promote").status_code == 403


def test_ean_repetido_segue_409_tambem_para_o_admin(db):
    assert _client(ADMIN, "admin").post("/nutrition/foods", json={**MANUAL, "barcode": MOCA}).status_code == 201
    repetido = _client(ALUNO).post("/nutrition/foods", json={**MANUAL, "name": "Outro", "barcode": MOCA})
    assert repetido.status_code == 409
    assert _client(ADMIN, "admin").post("/nutrition/foods", json={**MANUAL, "name": "Outro", "barcode": MOCA}).status_code == 409

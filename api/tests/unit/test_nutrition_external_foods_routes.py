"""Rotas das fontes externas do catálogo (`app/nutrition_external_foods.py`, L3b)
sem banco real e sem rede: banco em memória (`tests/support/nutrition_foods_fake.py`)
e os clientes HTTP trocados por dublês que devolvem os payloads REAIS gravados
(`tests/support/nutrition_external_payloads.py`).

Cobre: chave do USDA ausente -> 503; timeout -> 504; produto inexistente no OFF
-> 404; limite diário do OFF -> 429 (contado pelas linhas `off` do usuário no
dia dele, cache e 404 não contam); idempotência da importação do USDA; cache do
código de barras; 422 quando a fonte não passa nas regras do catálogo; linha
pública, somente leitura e com citação; 503 com as tabelas ausentes."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import external_food_sources as ext
from app import nutrition_external_foods as rotas
from app import nutrition_foods as foods_mod
from app.domain.nutrition import catalog
from app.domain.nutrition import external_sources as fontes
from app.domain.nutrition import labels
from app.nutrition_schema import food_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support import nutrition_external_payloads as payloads
from tests.support.nutrition_foods_fake import AGORA, FakeFoodsDB

ADMIN, ALUNO, OUTRO = 1, 2, 3
INICIO_DO_DIA = AGORA - timedelta(hours=9)  # início do dia do usuário no relógio do banco (fixado)
MOCA = payloads.OFF_MOCA_CODE


@pytest.fixture
def db(monkeypatch):
    banco = FakeFoodsDB()
    banco.add_food("Arroz, tipo 1, cozido", energy_kcal=128.26, carbohydrates_g=28.06, protein_g=2.52,
                   total_fat_g=0.23)
    # `rotas._connect` é o de `nutrition_foods` (lê `foods_mod.get_conn` na chamada).
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    for modulo in (foods_mod, rotas):
        monkeypatch.setattr(modulo, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(rotas, "_off_day_start", lambda conn, user_id: INICIO_DO_DIA)
    return banco


@pytest.fixture
def fontes_externas(monkeypatch):
    """Dublês dos clientes: devolvem os payloads reais e contam as chamadas."""
    estado = {"off": [], "usda_search": [], "usda_food": [], "erro": None,
              "produto": payloads.load(payloads.OFF_MOCA), "detalhe": payloads.load(payloads.USDA_SR_LEGACY)}

    def _off(code, *, fields):
        estado["off"].append((code, tuple(fields)))
        if estado["erro"] is not None:
            raise estado["erro"]
        return estado["produto"]

    def _search(body):
        estado["usda_search"].append(body)
        if estado["erro"] is not None:
            raise estado["erro"]
        return payloads.load(payloads.USDA_SEARCH)

    def _food(fdc_id):
        estado["usda_food"].append(fdc_id)
        if estado["erro"] is not None:
            raise estado["erro"]
        return estado["detalhe"]

    monkeypatch.setattr(ext, "off_product", _off)
    monkeypatch.setattr(ext, "usda_search", _search)
    monkeypatch.setattr(ext, "usda_food", _food)
    monkeypatch.setenv(ext.USDA_API_KEY_ENV, "chave-de-teste")
    return estado


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(rotas.router)
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _linhas(db: FakeFoodsDB, fonte: str) -> list[dict]:
    return [linha for linha in db.foods.values() if linha["source"] == fonte]


# ---------------------------------------------------------------------------
# Router no app real (o try/except do main.py engoliria erro de import)
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/nutrition/foods/barcode/{ean}", "/nutrition/foods/usda/search",
                    "/nutrition/foods/usda/{fdc_id}/import"):
        assert caminho in caminhos, caminho
    cliente = TestClient(app, raise_server_exceptions=False)
    assert cliente.get(f"/nutrition/foods/barcode/{MOCA}").status_code in (401, 403)
    assert cliente.get("/nutrition/foods/usda/search?q=rice").status_code in (401, 403)


def test_rotas_nao_estao_na_lambda_engines():
    from app.main_engines import app as engines

    assert not [p for p in engines.openapi()["paths"] if p.startswith("/nutrition")]


def test_tabelas_ausentes_respondem_503_citando_a_080(db, fontes_externas, monkeypatch):
    def _sem_tabelas(cur):
        raise food_tables_missing_error()

    monkeypatch.setattr(rotas, "require_food_tables", _sem_tabelas)
    cliente = _client()
    for resposta in (cliente.get(f"/nutrition/foods/barcode/{MOCA}"), cliente.post("/nutrition/foods/usda/173161/import")):
        assert resposta.status_code == 503
        assert resposta.json()["detail"]["migration"] == "080/081"
    assert fontes_externas["off"] == [] and fontes_externas["usda_food"] == []


# ---------------------------------------------------------------------------
# Código de barras (Open Food Facts)
# ---------------------------------------------------------------------------


def test_codigo_invalido_e_400_com_o_texto_do_motor(db, fontes_externas):
    resposta = _client().get("/nutrition/foods/barcode/7891000100104")
    assert resposta.status_code == 400
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == fontes.ERR_INVALID_BARCODE
    assert detalhe["message"] == labels.limitation_label(fontes.ERR_INVALID_BARCODE)
    assert fontes_externas["off"] == [] and db.queries == []


def test_consulta_nova_grava_publico_e_a_segunda_vem_do_cache(db, fontes_externas):
    cliente = _client()
    primeira = cliente.get(f"/nutrition/foods/barcode/{MOCA}")
    assert primeira.status_code == 200, primeira.text
    corpo = primeira.json()
    assert corpo["from_cache"] is False and corpo["source"] == "off" and corpo["barcode"] == MOCA
    assert corpo["source_id"] == MOCA and corpo["source_version"] == "2026-03-03"
    assert corpo["name"] == "Leite Condensado Integral moça" and corpo["brand"] == "Nestlé, Moça"
    assert corpo["per_100"]["energy_kcal"] == 325.0 and corpo["per_100"]["added_sugars_g"] is None
    assert corpo["serving"]["serving_size"] == 20.0 and corpo["serving"]["household_measure"] == "20 g"
    assert corpo["citation"].startswith(catalog.OFF_CITATION) and MOCA in corpo["citation"]
    assert corpo["license"] == catalog.OFF_LICENSE
    assert corpo["is_public"] is True and corpo["permissions"]["can_edit"] is False
    assert corpo["warnings"] == [] and corpo["atwater"]["consistent"] is True
    assert fontes_externas["off"] == [(MOCA, fontes.OFF_PRODUCT_FIELDS)]
    [linha] = _linhas(db, "off")
    assert linha["owner_user_id"] is None and linha["created_by"] == ALUNO

    # Outro usuário (e o mesmo código digitado como UPC/GTIN-14 com zeros) acha no cache, sem chamar o OFF.
    segunda = _client(user_id=OUTRO).get(f"/nutrition/foods/barcode/0{MOCA}")
    assert segunda.status_code == 200 and segunda.json()["from_cache"] is True
    assert segunda.json()["id"] == corpo["id"] and len(fontes_externas["off"]) == 1
    contagens = [q for q in db.queries if q[0] == rotas.OFF_LOOKUPS_TODAY_SQL]
    assert len(contagens) == 1  # o cache não consulta o limite


def test_produto_do_off_e_somente_leitura(db, fontes_externas):
    cliente = _client(role="admin", user_id=ADMIN)
    food_id = cliente.get(f"/nutrition/foods/barcode/{MOCA}").json()["id"]
    resposta = cliente.put(f"/nutrition/foods/{food_id}", json={
        "name": "x", "energy_kcal": 1, "carbohydrates_g": 0, "protein_g": 0, "total_fat_g": 0})
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == catalog.ERR_SOURCE_READ_ONLY
    porcao = _client().post(f"/nutrition/foods/{food_id}/portions", json={"label": "1 colher", "amount": 20})
    assert porcao.status_code == 201  # porção do usuário num item público continua permitida


def test_produto_inexistente_no_off_e_404_e_nao_conta(db, fontes_externas):
    fontes_externas["erro"] = ext.ExternalSourceError(ext.SOURCE_OFF, ext.KIND_NOT_FOUND)
    resposta = _client().get(f"/nutrition/foods/barcode/{payloads.OFF_NOT_FOUND_CODE}")
    assert resposta.status_code == 404
    assert resposta.json()["detail"]["error"] == "off_not_found" and resposta.json()["detail"]["source"] == "off"
    assert _linhas(db, "off") == []


@pytest.mark.parametrize(
    ("kind", "status", "codigo"),
    [(ext.KIND_TIMEOUT, 504, "off_timeout"), (ext.KIND_UNAVAILABLE, 503, "off_unavailable"),
     (ext.KIND_BAD_GATEWAY, 502, "off_bad_gateway")],
)
def test_falha_do_off_vira_502_503_504(db, fontes_externas, kind, status, codigo):
    fontes_externas["erro"] = ext.ExternalSourceError(ext.SOURCE_OFF, kind)
    resposta = _client().get(f"/nutrition/foods/barcode/{MOCA}")
    assert resposta.status_code == status and resposta.json()["detail"]["error"] == codigo
    assert _linhas(db, "off") == []


def test_limite_diario_429_pelas_linhas_do_usuario_no_dia_dele(db, fontes_externas):
    limite = fontes.OFF_LOOKUPS_PER_USER_DAY
    for i in range(limite - 1):
        db.add_food(f"Produto {i}", source="off", source_id=f"hoje-{i}", created_by=ALUNO, created_at=AGORA)
    for i in range(5):  # ontem (antes do início do dia dele) e de outro usuário: não contam
        db.add_food(f"Velho {i}", source="off", source_id=f"ontem-{i}", created_by=ALUNO,
                    created_at=INICIO_DO_DIA - timedelta(seconds=1))
        db.add_food(f"Outro {i}", source="off", source_id=f"outro-{i}", created_by=OUTRO, created_at=AGORA)
    cliente = _client()
    assert cliente.get(f"/nutrition/foods/barcode/{MOCA}").status_code == 200  # a 30ª consulta nova passa

    bloqueada = cliente.get("/nutrition/foods/barcode/96385074")
    assert bloqueada.status_code == 429
    detalhe = bloqueada.json()["detail"]
    assert detalhe["error"] == fontes.ERR_OFF_DAILY_LIMIT and detalhe["limit"] == limite
    assert detalhe["message"] == labels.limitation_label(fontes.ERR_OFF_DAILY_LIMIT)
    assert len(fontes_externas["off"]) == 1  # a bloqueada nem chama o OFF
    # O que já está no cache continua disponível depois do limite.
    assert cliente.get(f"/nutrition/foods/barcode/{MOCA}").status_code == 200
    _sql, params = next(q for q in db.queries if q[0] == rotas.OFF_LOOKUPS_TODAY_SQL)
    assert params == ("off", ALUNO, INICIO_DO_DIA)


def test_produto_sem_energia_e_422_e_nao_grava(db, fontes_externas):
    del fontes_externas["produto"]["nutrition"]
    resposta = _client().get(f"/nutrition/foods/barcode/{MOCA}")
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe["error"] == "invalid_external_food" and detalhe["source"] == "off"
    assert detalhe["errors"] == labels.limitation_items([f"{catalog.ERR_MISSING_FIELD}:energy_kcal"])
    assert _linhas(db, "off") == []


def test_inicio_do_dia_vem_do_fuso_do_usuario(monkeypatch):
    from app.intelligence.dashboard import time_windows
    from app.intelligence.dashboard.time_windows import user_day_start_db, user_today

    # O início do dia vem do dono do fuso (`time_windows.user_day_start_db_for`, L5a).
    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    esperado = user_day_start_db(user_today("America/Sao_Paulo"), "America/Sao_Paulo")
    assert rotas._off_day_start(object(), ALUNO) == esperado
    assert isinstance(esperado, datetime) and esperado.tzinfo is None


# ---------------------------------------------------------------------------
# USDA FoodData Central
# ---------------------------------------------------------------------------


def test_sem_chave_as_rotas_do_usda_respondem_503(db, fontes_externas, monkeypatch):
    monkeypatch.delenv(ext.USDA_API_KEY_ENV)
    cliente = _client()
    for resposta in (cliente.get("/nutrition/foods/usda/search", params={"q": "rice"}),
                     cliente.post("/nutrition/foods/usda/173161/import")):
        assert resposta.status_code == 503
        assert resposta.json()["detail"]["error"] == "usda_not_configured"
    assert fontes_externas["usda_search"] == [] and fontes_externas["usda_food"] == [] and db.queries == []


def test_busca_no_usda_so_foundation_e_sr_legacy_com_resumo_do_motor(db, fontes_externas):
    resposta = _client().get("/nutrition/foods/usda/search", params={"q": "  rice  "})
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["query"] == "rice" and corpo["total_hits"] == 141
    assert fontes_externas["usda_search"] == [fontes.usda_search_request("rice")]
    assert fontes_externas["usda_search"][0]["dataType"] == ["Foundation", "SR Legacy"]
    assert [i["fdc_id"] for i in corpo["items"]] == [173161, 167967, 1104812, 2710825, 171942, 171013]
    preto = next(i for i in corpo["items"] if i["fdc_id"] == 2710825)
    assert preto["per_100"]["energy_kcal"] == 361 and preto["data_type"] == "Foundation"
    assert db.queries == []  # a busca não toca no banco


def test_timeout_do_usda_e_504(db, fontes_externas):
    fontes_externas["erro"] = ext.ExternalSourceError(ext.SOURCE_USDA, ext.KIND_TIMEOUT)
    resposta = _client().get("/nutrition/foods/usda/search", params={"q": "rice"})
    assert resposta.status_code == 504 and resposta.json()["detail"]["error"] == "usda_timeout"
    importacao = _client().post("/nutrition/foods/usda/173161/import")
    assert importacao.status_code == 504 and _linhas(db, "usda") == []


def test_importacao_do_usda_e_idempotente(db, fontes_externas):
    cliente = _client()
    criada = cliente.post("/nutrition/foods/usda/173161/import")
    assert criada.status_code == 201, criada.text
    corpo = criada.json()
    assert corpo["created"] is True and corpo["source"] == "usda" and corpo["source_id"] == "173161"
    assert corpo["source_version"] == "sr_legacy-2019-04-01" and corpo["category"] == "Snacks"
    assert corpo["citation"] == f"{catalog.USDA_CITATION} SR Legacy, FDC ID 173161."
    assert corpo["license"] == catalog.USDA_LICENSE and corpo["is_public"] is True
    assert corpo["per_100"]["sodium_mg"] == 233.0 and corpo["per_100"]["added_sugars_g"] is None
    [linha] = _linhas(db, "usda")
    assert linha["created_by"] == ALUNO and linha["owner_user_id"] is None

    de_novo = _client(user_id=OUTRO).post("/nutrition/foods/usda/173161/import")
    assert de_novo.status_code == 200 and de_novo.json()["created"] is False
    assert de_novo.json()["id"] == corpo["id"]
    assert fontes_externas["usda_food"] == [173161] and len(_linhas(db, "usda")) == 1


def test_importacao_concorrente_grava_uma_linha_so(db, fontes_externas, monkeypatch):
    """Outra requisição grava o mesmo fdcId entre a leitura e a gravação desta:
    o `ON DUPLICATE KEY UPDATE` não faz nada e a releitura devolve a linha dela."""
    original = ext.usda_food

    def _concorrente(fdc_id):
        db.add_food("Rice crackers", source="usda", source_id=str(fdc_id), created_by=OUTRO, energy_kcal=416,
                    carbohydrates_g=82.64, protein_g=10, total_fat_g=5)
        return original(fdc_id)

    monkeypatch.setattr(ext, "usda_food", _concorrente)
    resposta = _client().post("/nutrition/foods/usda/173161/import")
    assert resposta.status_code == 201
    [linha] = _linhas(db, "usda")
    assert linha["created_by"] == OUTRO and resposta.json()["id"] == linha["id"]


def test_importacao_404_e_tipo_nao_aceito(db, fontes_externas):
    fontes_externas["erro"] = ext.ExternalSourceError(ext.SOURCE_USDA, ext.KIND_NOT_FOUND)
    resposta = _client().post("/nutrition/foods/usda/999999999/import")
    assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "usda_not_found"

    fontes_externas["erro"] = None
    fontes_externas["detalhe"]["dataType"] = "Branded"
    recusada = _client().post("/nutrition/foods/usda/173161/import")
    assert recusada.status_code == 422
    assert recusada.json()["detail"]["errors"] == labels.limitation_items([fontes.ERR_USDA_DATA_TYPE])
    assert _linhas(db, "usda") == []


def test_fdc_id_invalido_e_422_do_fastapi(db, fontes_externas):
    assert _client().post("/nutrition/foods/usda/0/import").status_code == 422
    assert _client().post("/nutrition/foods/usda/abc/import").status_code == 422
    assert fontes_externas["usda_food"] == []

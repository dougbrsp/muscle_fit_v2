"""Pendências da listagem por letra e da busca por marca (#208). Dono: `domain/nutrition/food_text.py`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_foods as foods_mod
from app.domain.nutrition import food_text
from app.security import Identity, get_current_identity
from tests.support.nutrition_foods_fake import FakeFoodsDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

ALUNO = 2


@pytest.fixture
def db(monkeypatch):
    banco = FakeFoodsDB()
    banco.add_food("Concentrado proteico", source="user", owner=None, brand="Growth Whey")
    banco.add_food("Barra de cereal", source="user", owner=None, brand="Growth")
    banco.add_food("Whey isolado", source="user", owner=None, brand="Outra")
    banco.add_food("Abacate")
    banco.add_food("7 grãos", source="user", owner=None)
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    monkeypatch.setattr(foods_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(foods_mod, "has_favorites_table", lambda cur: False)
    return banco


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _nomes(q: str) -> list[str]:
    r = _client().get("/nutrition/foods/search", params={"q": q})
    assert r.status_code == 200, r.text
    return [i["name"] for i in r.json()["items"]]


# --- listagem: offset com teto, letra opcional, rótulo do balde ---------------------------------------


def test_offset_acima_do_teto_e_422_e_no_teto_e_200(db):
    cliente = _client()
    acima = cliente.get("/nutrition/foods", params={"letter": "A", "offset": food_text.LIST_OFFSET_MAX + 1})
    assert acima.status_code == 422
    assert cliente.get("/nutrition/foods", params={"letter": "A", "offset": 10**30}).status_code == 422
    assert cliente.get("/nutrition/foods", params={"letter": "A", "offset": food_text.LIST_OFFSET_MAX}).status_code == 200


def test_letra_ausente_devolve_o_primeiro_balde(db):
    r = _client().get("/nutrition/foods")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["letter"] == food_text.DEFAULT_LIST_BUCKET == "A"
    assert [i["name"] for i in corpo["items"]] == ["Abacate"]


def test_resposta_traz_o_rotulo_de_cada_balde_sem_mexer_nos_campos_antigos(db):
    corpo = _client().get("/nutrition/foods", params={"letter": "outros"}).json()
    assert corpo["letter_labels"]["outros"] == food_text.OTHER_BUCKET_LABEL != "outros"
    assert corpo["letter_labels"]["B"] == "B"
    assert list(corpo["letter_labels"]) == corpo["letters"]
    assert {"letter", "letters", "items", "limit", "offset", "has_more"} <= set(corpo)
    assert [i["name"] for i in corpo["items"]] == ["7 grãos"]


# --- busca por marca: nome E marca -------------------------------------------------------------------


def test_busca_que_mistura_nome_e_marca_recupera_o_item(db):
    # "Concentrado proteico" nada tem de "whey" no nome: antes só a marca contígua ("growth whey") o achava.
    assert "Concentrado proteico" in _nomes("proteico growth")
    assert "Concentrado proteico" in _nomes("whey growth")


def test_termo_de_nome_e_marca_exige_que_cada_termo_case_em_um_dos_dois(db):
    nomes = _nomes("cereal whey")  # "cereal" só no nome de Barra; "whey" não está na marca dela nem no nome
    assert "Barra de cereal" in nomes  # o nome sozinho já casa (FULLTEXT em OU): ordem atual preservada
    consulta = [(q, p) for q, p in db.queries if "name_normalized LIKE %s OR brand LIKE %s" in q]
    assert consulta  # a consulta com termos foi feita


def test_termo_so_no_nome_sem_marca_nao_entra_pela_consulta_de_marca(db):
    # "abacate" e "grãos" só casam em nomes: nenhum item de marca entra por essa consulta.
    assert "Concentrado proteico" not in _nomes("abacate graos")


def test_ordem_atual_dos_resultados_nao_muda_com_os_termos(db):
    assert _nomes("whey isolado")[0] == "Whey isolado"


def test_lote_de_marca_ordena_por_nome_e_nao_por_cadastro(db):
    sql = foods_mod.brand_search_sql(visibility="v", with_source=False, tokens=2)
    assert "ORDER BY name_normalized, id" in sql and "ORDER BY id" not in sql


def test_padroes_de_termos_do_motor():
    assert food_text.brand_token_patterns("whey growth") == ("%whey%", "%growth%")
    assert food_text.brand_token_patterns("growth") == ()  # um termo: busca por frase
    assert food_text.brand_token_patterns("whey ab") == ()  # "ab" não passa do mínimo do índice
    muitos = " ".join(f"termo{i}" for i in range(10))
    assert len(food_text.brand_token_patterns(muitos)) == food_text.BRAND_TOKENS_MAX


def test_busca_so_de_simbolos_retorna_vazio_sem_consultar(db):
    assert _nomes("!!!") == []
    assert not any("brand LIKE" in q for q, _p in db.queries)

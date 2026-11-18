"""Busca por marca e listagem por letra do catálogo de alimentos (#195)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_foods as foods_mod
from app.domain.nutrition import food_text
from app.security import Identity, get_current_identity
from tests.support.nutrition_foods_fake import FakeFoodsDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

ALUNO, OUTRO = 2, 3


@pytest.fixture
def db(monkeypatch):
    banco = FakeFoodsDB()
    banco.add_food("Whey Protein Concentrado", source="user", owner=None, brand="Growth Supplements")
    banco.add_food("Barra de cereal", source="user", owner=None, brand="Growth")
    banco.add_food("Growth bar", source="user", owner=None, brand="Growth")  # nome e marca casam
    banco.add_food("Pasta de amendoim", source="user", owner=ALUNO, brand="Marca 100% pura")
    banco.add_food("Água de coco")
    banco.add_food("abacate")
    banco.add_food("Banana")
    banco.add_food("7 grãos", source="user", owner=None)
    banco.add_food("Óleo de soja")
    banco.add_food("Arroz do outro", source="user", owner=OUTRO)
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    monkeypatch.setattr(foods_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(foods_mod, "has_favorites_table", lambda cur: True)
    return banco


def _client(user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _busca(q: str, user_id: int = ALUNO, **extra):
    r = _client(user_id).get("/nutrition/foods/search", params={"q": q, **extra})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _letra(letter: str, user_id: int = ALUNO, **extra):
    return _client(user_id).get("/nutrition/foods", params={"letter": letter, **extra})


# --- busca por marca ---------------------------------------------------------


def test_busca_so_pela_marca_encontra_e_nao_pre_seleciona(db):
    itens = _busca("growth supplements")
    nomes = [i["name"] for i in itens]
    assert "Whey Protein Concentrado" in nomes
    assert not any(i["auto_accept"] for i in itens if i["name"] == "Whey Protein Concentrado")
    assert next(i for i in itens if i["name"] == "Whey Protein Concentrado")["brand"] == "Growth Supplements"


def test_alimento_que_casa_nome_e_marca_aparece_uma_vez(db):
    nomes = [i["name"] for i in _busca("growth")]
    assert nomes.count("Growth bar") == 1 and "Barra de cereal" in nomes


def test_marca_respeita_visibilidade_e_fonte(db):
    assert [i["name"] for i in _busca("marca 100", user_id=ALUNO)] == ["Pasta de amendoim"]
    assert _busca("marca 100", user_id=OUTRO) == []
    assert _busca("growth", source="taco") == []


def test_marca_escapa_curingas_do_usuario(db):
    assert [i["name"] for i in _busca("100% pura")] == ["Pasta de amendoim"]
    assert _busca("100_ pura") == []  # `_` não é curinga


def test_marca_curta_nao_consulta_a_marca(db):
    _busca("gro")  # 3 caracteres: consulta
    assert any("brand LIKE" in q for q, _p in db.queries)
    db.queries.clear()
    _busca("ov")
    assert not any("brand LIKE" in q for q, _p in db.queries)


def test_marca_nos_preferidos_fora_do_lote(db):
    db.favorites.append({"user_id": ALUNO, "food_id": 2})
    itens = _busca("growth")
    assert itens[0]["name"] == "Barra de cereal" and itens[0]["is_favorite"] is True
    assert sum("brand LIKE" in q for q, _p in db.queries) == 2  # marca + marca dos preferidos


def test_motor_nota_da_marca_e_auto_accept_so_pelo_nome():
    assert food_text.brand_score("growth", "Whey", None) == food_text.score("growth", "Whey")
    assert food_text.brand_score("growth", "Whey", "Growth") == 1.0
    ordem = food_text.order_for_search("growth", [(1, "Whey", "Growth")])
    assert ordem[0].score == 1.0 and ordem[0].auto_accept is False
    assert food_text.order_for_search("whey", [(1, "Whey")])[0].auto_accept is True


def test_motor_marca_do_segundo_nao_derruba_auto_accept_do_nome():
    sem = food_text.order_for_search("arroz", [(1, "Arroz"), (2, "Arroz integral cozido")])
    com = food_text.order_for_search("arroz", [(1, "Arroz"), (2, "Arroz integral cozido", "Arroz")])
    assert sem[0].candidate_id == 1 and sem[0].auto_accept is True
    assert {m.candidate_id: m.auto_accept for m in com} == {1: True, 2: False}


def test_motor_ressalva_auto_accept_so_marca_com_nome_parecido_e_lider_fora_do_topo():
    # Fixa o comportamento atual (ver "Ressalva" em `food_text._ordered`): quem entra só pela marca
    # conta como "segundo por nome" e tira a folga do líder.
    so_nome = food_text.order_for_search("arroz", [(1, "Arroz")])
    assert so_nome[0].auto_accept is True
    com_marca = food_text.order_for_search("arroz", [(1, "Arroz"), (2, "Arroz", "Tio Joao")])
    assert {m.candidate_id: m.auto_accept for m in com_marca} == {1: False, 2: False}
    # O líder por nome pode ficar abaixo de item que subiu pela marca e ainda ter `auto_accept`.
    ordem = food_text.order_for_search("whey", [(1, "Whey"), (2, "Barra", "whey")])
    assert [(m.candidate_id, m.auto_accept) for m in ordem] == [(2, False), (1, True)]


def test_motor_brand_pattern():
    assert food_text.brand_pattern("ab") is None
    assert food_text.brand_pattern("  100%  pura_x ") == "%100\\% pura\\_x%"


# --- listagem por letra ------------------------------------------------------


def test_listagem_em_ordem_alfabetica_ignorando_acento_e_caixa(db):
    corpo = _letra("a").json()
    assert corpo["letter"] == "A"
    assert [i["name"] for i in corpo["items"]] == ["abacate", "Água de coco"]
    assert corpo["has_more"] is False and corpo["offset"] == 0 and corpo["limit"] == food_text.LIST_PAGE_DEFAULT
    assert corpo["letters"] == list(food_text.LIST_BUCKETS) and "is_favorite" in corpo["items"][0]


def test_listagem_letra_sem_alimentos_e_200_vazio(db):
    r = _letra("Z")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["has_more"] is False


def test_listagem_balde_de_nao_letras(db):
    corpo = _letra("OUTROS").json()
    assert corpo["letter"] == "outros" and [i["name"] for i in corpo["items"]] == ["7 grãos"]


def test_listagem_letra_invalida_e_400(db):
    for ruim in ("ab", "1", "", "#"):
        r = _letra(ruim)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_letter", ruim
    assert _client().get("/nutrition/foods").status_code == 422  # letra obrigatória


def test_listagem_nao_mostra_privado_de_outro_nem_inativo(db):
    db.add_food("Arroz inativo", active=False)
    assert "Arroz do outro" not in [i["name"] for i in _letra("A", user_id=ALUNO).json()["items"]]
    assert "Arroz do outro" in [i["name"] for i in _letra("A", user_id=OUTRO).json()["items"]]
    assert "Arroz inativo" not in [i["name"] for i in _letra("A", user_id=OUTRO).json()["items"]]


def test_listagem_pagina_com_has_more(db):
    primeira = _letra("A", user_id=OUTRO, limit=1).json()
    assert [i["name"] for i in primeira["items"]] == ["abacate"] and primeira["has_more"] is True
    segunda = _letra("A", user_id=OUTRO, limit=1, offset=1).json()
    assert [i["name"] for i in segunda["items"]] == ["Água de coco"] and segunda["has_more"] is True
    ultima = _letra("A", user_id=OUTRO, limit=1, offset=2).json()
    assert [i["name"] for i in ultima["items"]] == ["Arroz do outro"] and ultima["has_more"] is False
    assert _letra("A", limit=10_000).json()["limit"] == food_text.LIST_PAGE_MAX


def test_listagem_marca_preferido(db):
    db.favorites.append({"user_id": ALUNO, "food_id": 6})
    itens = _letra("A").json()["items"]
    assert {i["name"]: i["is_favorite"] for i in itens} == {"abacate": True, "Água de coco": False}


def test_motor_letras():
    assert food_text.normalize_letter(" b ") == "B" and food_text.normalize_letter("Outros") == "outros"
    assert food_text.normalize_letter("ç") is None and food_text.normalize_letter(None) is None
    assert food_text.list_page("x", -4) == (food_text.LIST_PAGE_DEFAULT, 0)
    assert len(food_text.letter_prefixes(food_text.OTHER_BUCKET)) == 26

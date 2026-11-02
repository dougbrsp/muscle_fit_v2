"""Rotas dos alimentos preferidos (`app/nutrition_favorites.py`, N3) sem banco real:
banco em memória (extensão do `FakeFoodsDB`) que responde EXATAMENTE os statements
do módulo (despacho pela constante SQL; SQL novo sem o fake falha alto).

Cobre: auth (401/403 sem sessão), isolamento entre usuários (a lista de um nunca
aparece, nem é alterada, nem é apagada ou reordenada por outro; nem por admin),
alimento privado de outro (404), porção padrão pelo motor (quantidade/unidade
juntas, porção de outro alimento, unidade sem conversão, acima do teto),
idempotência do POST, reordenar (permutação exata), teto da lista, 503 sem a 102
e o CRUD completo."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_favorites as rotas
from app import nutrition_foods as foods_mod
from app.domain.nutrition import favorites as engine
from app.nutrition_schema import favorites_table_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_foods_fake import AGORA, FakeConn, FakeCursor, FakeFoodsDB

ALUNO, OUTRO, ADMIN = 2, 3, 1


class FakeFavCursor(FakeCursor):
    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db: "FakeFavDB" = self.db  # type: ignore[assignment]
        p = tuple(params or ())
        n_alimentos, n_porcoes = len(p), len(p)
        if sql == rotas.FAVORITES_OF_USER_SQL:
            db.queries.append((sql, p))
            linhas = sorted((f for f in db.favorites if f["user_id"] == p[0]), key=lambda f: (f["position"], f["id"]))
            self._rows, self.rowcount = [dict(f) for f in linhas], len(linhas)
        elif sql == rotas.FAVORITE_BY_FOOD_SQL:
            db.queries.append((sql, p))
            self._rows = [dict(f) for f in db.favorites if f["user_id"] == p[0] and f["food_id"] == p[1]]
        elif sql == rotas.COUNT_AND_MAX_POSITION_SQL:
            db.queries.append((sql, p))
            minhas = [f for f in db.favorites if f["user_id"] == p[0]]
            self._rows = [{"n": len(minhas), "max_position": max((f["position"] for f in minhas), default=None)}]
        elif sql == rotas.INSERT_FAVORITE_SQL:
            db.queries.append((sql, p))
            usuario, food_id, qtd, unidade, porcao, posicao = p
            if any(f["user_id"] == usuario and f["food_id"] == food_id for f in db.favorites):
                self.rowcount = 0
                return
            db.next_fav_id += 1
            db.favorites.append({
                "id": db.next_fav_id, "user_id": usuario, "food_id": food_id,
                "default_quantity": None if qtd is None else Decimal(str(qtd)), "default_unit": unidade,
                "default_portion_id": porcao, "position": posicao, "created_at": AGORA, "updated_at": AGORA,
            })
            self.rowcount = 1
        elif sql == rotas.UPDATE_DEFAULT_SQL:
            db.queries.append((sql, p))
            qtd, unidade, porcao, usuario, food_id = p
            for f in db.favorites:
                if f["user_id"] == usuario and f["food_id"] == food_id:
                    f.update(default_quantity=None if qtd is None else Decimal(str(qtd)), default_unit=unidade,
                             default_portion_id=porcao)
                    self.rowcount += 1
        elif sql == rotas.UPDATE_POSITION_SQL:
            db.queries.append((sql, p))
            posicao, usuario, food_id = p
            for f in db.favorites:
                if f["user_id"] == usuario and f["food_id"] == food_id:
                    f["position"] = posicao
                    self.rowcount += 1
        elif sql == rotas.DELETE_FAVORITE_SQL:
            db.queries.append((sql, p))
            antes = len(db.favorites)
            db.favorites = [f for f in db.favorites if not (f["user_id"] == p[0] and f["food_id"] == p[1])]
            self.rowcount = antes - len(db.favorites)
        elif sql == rotas.FOOD_SQL:
            db.queries.append((sql, p))
            linha = db.foods.get(int(p[0]))
            self._rows = [{k: v for k, v in linha.items() if not k.startswith("_")}] if linha else []
        elif sql == rotas.foods_by_ids_sql(n_alimentos):
            db.queries.append((sql, p))
            self._rows = [{k: v for k, v in db.foods[i].items() if not k.startswith("_")} for i in p if i in db.foods]
        elif sql == rotas.PORTION_SQL:
            db.queries.append((sql, p))
            linha = db.portions.get(int(p[0]))
            self._rows = [dict(linha)] if linha else []
        elif sql == rotas.portions_by_ids_sql(n_porcoes):
            db.queries.append((sql, p))
            self._rows = [dict(db.portions[i]) for i in p if i in db.portions]
        else:
            super().execute(sql, params)


class FakeFavConn(FakeConn):
    def cursor(self) -> FakeFavCursor:
        return FakeFavCursor(self.db)

    def __enter__(self) -> "FakeFavConn":
        super().__enter__()
        self._favoritos = copy.deepcopy(self.db.favorites)  # type: ignore[attr-defined]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.db.favorites = self._favoritos  # type: ignore[attr-defined]
        return super().__exit__(exc_type, exc, tb)


class FakeFavDB(FakeFoodsDB):
    def __init__(self) -> None:
        super().__init__()
        self.next_fav_id = 0

    def conn(self) -> FakeFavConn:  # type: ignore[override]
        return FakeFavConn(self)


@pytest.fixture
def db(monkeypatch):
    banco = FakeFavDB()
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", energy_kcal=128, carbohydrates_g=28, protein_g=2.5,
                                 total_fat_g=0.2, serving_size=100)
    banco.feijao = banco.add_food("Feijão, carioca, cozido", energy_kcal=76, carbohydrates_g=13.6, protein_g=4.8,
                                  total_fat_g=0.5)
    banco.privado_outro = banco.add_food("Doce secreto", source="user", owner=OUTRO, energy_kcal=400,
                                         carbohydrates_g=80, protein_g=2, total_fat_g=10)
    banco.privado_meu = banco.add_food("Pasta minha", source="user", owner=ALUNO, energy_kcal=300,
                                       carbohydrates_g=20, protein_g=10, total_fat_g=20)
    banco.porcao_arroz = banco.add_portion(banco.arroz, "Colher de servir", 45)
    banco.porcao_feijao = banco.add_portion(banco.feijao, "Concha", 140)
    banco.porcao_de_outro = banco.add_portion(banco.arroz, "Porção do outro", 200, owner=OUTRO)
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    for modulo in (foods_mod, rotas):
        monkeypatch.setattr(modulo, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(rotas, "require_favorites_table", lambda cur: None)
    monkeypatch.setattr(foods_mod, "has_favorites_table", lambda cur: True)
    return banco


def _client(user_id: int = ALUNO, role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(rotas.router)
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _favoritar(cliente: TestClient, food_id: int, **extra: Any):
    return cliente.post("/nutrition/favorites", json={"food_id": food_id, **extra})


def _ids(resposta) -> list[int]:
    return [item["food"]["id"] for item in resposta.json()["items"]]


def _codigos(resposta) -> list[str]:
    return [e["code"] for e in resposta.json()["detail"]["errors"]]


# ---------------------------------------------------------------------------
# Router e autenticação
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core_e_exige_sessao():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/nutrition/favorites", "/nutrition/favorites/reorder", "/nutrition/favorites/{food_id}"):
        assert caminho in caminhos, caminho
    cliente = TestClient(app, raise_server_exceptions=False)
    assert cliente.get("/nutrition/favorites").status_code in (401, 403)
    assert cliente.post("/nutrition/favorites", json={"food_id": 1}).status_code in (401, 403)
    assert cliente.put("/nutrition/favorites/1", json={}).status_code in (401, 403)
    assert cliente.delete("/nutrition/favorites/1").status_code in (401, 403)
    assert cliente.post("/nutrition/favorites/reorder", json={"food_ids": []}).status_code in (401, 403)


def test_rotas_nao_estao_na_lambda_engines():
    from app.main_engines import app as engines

    assert not [p for p in engines.openapi()["paths"] if p.startswith("/nutrition")]


def test_tabela_ausente_responde_503_citando_a_102(db, monkeypatch):
    def _sem_tabela(cur):
        raise favorites_table_missing_error()

    monkeypatch.setattr(rotas, "require_favorites_table", _sem_tabela)
    resposta = _client().get("/nutrition/favorites")
    assert resposta.status_code == 503
    assert resposta.json()["detail"]["migration"] == "102"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_lista_vazia_traz_o_teto_do_motor(db):
    resposta = _client().get("/nutrition/favorites")
    assert resposta.status_code == 200
    assert resposta.json() == {"items": [], "max_items": engine.MAX_FAVORITES}


def test_favoritar_cria_no_fim_da_lista_e_e_idempotente(db):
    cliente = _client()
    primeira = _favoritar(cliente, db.arroz)
    assert primeira.status_code == 201
    assert primeira.json()["position"] == 1 and primeira.json()["default"] is None
    assert _favoritar(cliente, db.feijao).json()["position"] == 2
    repetida = _favoritar(cliente, db.arroz)
    assert repetida.status_code == 200
    assert _ids(cliente.get("/nutrition/favorites")) == [db.arroz, db.feijao]
    assert len(db.favorites) == 2


def test_favoritar_com_porcao_padrao_usa_a_matematica_do_diario(db):
    cliente = _client()
    resposta = _favoritar(cliente, db.arroz, default_quantity=2, default_unit="portion", default_portion_id=db.porcao_arroz)
    assert resposta.status_code == 201
    assert resposta.json()["default"] == {
        "quantity": 2.0, "unit": "portion", "portion_id": db.porcao_arroz,
        "portion_label": "Colher de servir", "portion_amount": 45.0,
    }
    # g direto, sem porção cadastrada
    gramas = _favoritar(cliente, db.feijao, default_quantity=150, default_unit="G")
    assert gramas.json()["default"]["unit"] == "g" and gramas.json()["default"]["quantity"] == 150.0


def test_editar_a_porcao_padrao_e_limpar(db):
    cliente = _client()
    _favoritar(cliente, db.arroz)
    editada = cliente.put(f"/nutrition/favorites/{db.arroz}", json={"default_quantity": 120, "default_unit": "g"})
    assert editada.status_code == 200 and editada.json()["default"]["quantity"] == 120.0
    limpa = cliente.put(f"/nutrition/favorites/{db.arroz}", json={})
    assert limpa.status_code == 200 and limpa.json()["default"] is None
    assert db.favorites[0]["default_quantity"] is None and db.favorites[0]["default_unit"] is None


def test_porcao_padrao_invalida_responde_400_com_o_codigo_do_motor(db):
    cliente = _client()
    # quantidade sem unidade
    assert _codigos(_favoritar(cliente, db.arroz, default_quantity=2)) == [engine.ERR_DEFAULT_INCOMPLETE]
    # porção cadastrada com unidade em gramas
    erro = _favoritar(cliente, db.arroz, default_quantity=1, default_unit="g", default_portion_id=db.porcao_arroz)
    assert erro.status_code == 400 and _codigos(erro) == [engine.ERR_DEFAULT_PORTION_UNIT]
    # unidade desconhecida
    assert _favoritar(cliente, db.arroz, default_quantity=1, default_unit="balde").status_code == 400
    # quantidade zero e negativa
    assert _favoritar(cliente, db.arroz, default_quantity=0, default_unit="g").status_code == 400
    assert _favoritar(cliente, db.arroz, default_quantity=-5, default_unit="g").status_code == 400
    # acima do teto do diário
    assert _favoritar(cliente, db.arroz, default_quantity=999999, default_unit="g").status_code == 400
    # `unit` sem porção do rótulo nem cadastrada: sem conversão (feijão não tem serving_size)
    assert _favoritar(cliente, db.feijao, default_quantity=1, default_unit="unit").status_code == 400
    assert db.favorites == []


def test_porcao_de_outro_alimento_ou_de_outro_usuario_e_404(db):
    cliente = _client()
    errada = _favoritar(cliente, db.arroz, default_quantity=1, default_unit="portion", default_portion_id=db.porcao_feijao)
    assert errada.status_code == 404 and errada.json()["detail"]["error"] == "portion_not_found"
    alheia = _favoritar(cliente, db.arroz, default_quantity=1, default_unit="portion", default_portion_id=db.porcao_de_outro)
    assert alheia.status_code == 404
    inexistente = _favoritar(cliente, db.arroz, default_quantity=1, default_unit="portion", default_portion_id=9999)
    assert inexistente.status_code == 404
    assert db.favorites == []


def test_desfavoritar_e_404_quando_nao_e_favorito(db):
    cliente = _client()
    _favoritar(cliente, db.arroz)
    assert cliente.delete(f"/nutrition/favorites/{db.arroz}").json() == {"status": "deleted", "food_id": db.arroz}
    assert _ids(cliente.get("/nutrition/favorites")) == []
    assert cliente.delete(f"/nutrition/favorites/{db.arroz}").status_code == 404
    assert cliente.put(f"/nutrition/favorites/{db.arroz}", json={}).status_code == 404


def test_alimento_inexistente_inativo_ou_privado_de_outro_e_404(db):
    cliente = _client()
    assert _favoritar(cliente, 9999).status_code == 404
    assert _favoritar(cliente, db.privado_outro).status_code == 404  # não revela o privado de outro
    assert _favoritar(cliente, db.privado_meu).status_code == 201  # o próprio privado vale
    db.foods[db.feijao]["is_active"] = 0
    assert _favoritar(cliente, db.feijao).status_code == 404


def test_alimento_que_deixou_de_ser_usavel_some_da_lista_sem_apagar_a_linha(db):
    cliente = _client()
    _favoritar(cliente, db.arroz)
    _favoritar(cliente, db.feijao)
    db.foods[db.arroz]["is_active"] = 0
    assert _ids(cliente.get("/nutrition/favorites")) == [db.feijao]
    assert len(db.favorites) == 2


def test_teto_da_lista_vem_do_motor(db, monkeypatch):
    monkeypatch.setattr(engine, "MAX_FAVORITES", 1)
    cliente = _client()
    assert _favoritar(cliente, db.arroz).status_code == 201
    cheio = _favoritar(cliente, db.feijao)
    assert cheio.status_code == 409 and _codigos(cheio) == [engine.ERR_TOO_MANY_FAVORITES]
    assert _favoritar(cliente, db.arroz).status_code == 200  # já favorito não conta de novo


# ---------------------------------------------------------------------------
# Reordenar
# ---------------------------------------------------------------------------


def test_reordenar_regrava_as_posicoes(db):
    cliente = _client()
    for food_id in (db.arroz, db.feijao, db.privado_meu):
        _favoritar(cliente, food_id)
    resposta = cliente.post("/nutrition/favorites/reorder", json={"food_ids": [db.privado_meu, db.arroz, db.feijao]})
    assert resposta.status_code == 200
    assert _ids(resposta) == [db.privado_meu, db.arroz, db.feijao]
    assert [i["position"] for i in resposta.json()["items"]] == [1, 2, 3]
    assert _ids(cliente.get("/nutrition/favorites")) == [db.privado_meu, db.arroz, db.feijao]
    # favoritar de novo vai para o fim
    db.foods[db.privado_outro]["owner_user_id"] = None
    assert _favoritar(cliente, db.privado_outro).json()["position"] == 4


@pytest.mark.parametrize("pedido", ["faltando", "repetido", "estranho", "vazio"])
def test_reordenar_exige_permutacao_exata(db, pedido):
    cliente = _client()
    _favoritar(cliente, db.arroz)
    _favoritar(cliente, db.feijao)
    corpos = {
        "faltando": [db.arroz],
        "repetido": [db.arroz, db.arroz],
        "estranho": [db.arroz, db.feijao, db.privado_meu],
        "vazio": [],
    }
    resposta = cliente.post("/nutrition/favorites/reorder", json={"food_ids": corpos[pedido]})
    assert resposta.status_code == 400 and _codigos(resposta) == [engine.ERR_INVALID_ORDER]
    assert _ids(cliente.get("/nutrition/favorites")) == [db.arroz, db.feijao]


# ---------------------------------------------------------------------------
# Isolamento entre usuários
# ---------------------------------------------------------------------------


def test_um_usuario_nunca_le_nem_altera_os_preferidos_de_outro(db):
    aluno, outro, admin = _client(ALUNO), _client(OUTRO), _client(ADMIN, "admin")
    _favoritar(aluno, db.arroz, default_quantity=100, default_unit="g")
    _favoritar(aluno, db.feijao)
    # leitura: a lista do outro (e a do admin) é vazia
    assert _ids(outro.get("/nutrition/favorites")) == []
    assert _ids(admin.get("/nutrition/favorites")) == []
    # escrita: não há parâmetro de usuário; `user_id` extra é recusado (extra=forbid)
    assert outro.post("/nutrition/favorites", json={"food_id": db.arroz, "user_id": ALUNO}).status_code == 422
    # PUT/DELETE/reorder do outro só alcançam a lista DELE
    assert outro.put(f"/nutrition/favorites/{db.arroz}", json={"default_quantity": 1, "default_unit": "g"}).status_code == 404
    assert outro.delete(f"/nutrition/favorites/{db.arroz}").status_code == 404
    assert admin.delete(f"/nutrition/favorites/{db.arroz}").status_code == 404
    assert outro.post("/nutrition/favorites/reorder", json={"food_ids": [db.arroz, db.feijao]}).status_code == 400
    # a lista do aluno ficou intacta
    lista = aluno.get("/nutrition/favorites").json()["items"]
    assert [i["food"]["id"] for i in lista] == [db.arroz, db.feijao]
    assert lista[0]["default"]["quantity"] == 100.0
    # e o mesmo alimento favoritado pelo outro é uma linha independente
    assert _favoritar(outro, db.arroz).status_code == 201
    assert _ids(outro.get("/nutrition/favorites")) == [db.arroz]
    assert _ids(aluno.get("/nutrition/favorites")) == [db.arroz, db.feijao]
    # nenhuma linha de um usuário foi tocada pelo outro
    assert {f["user_id"] for f in db.favorites} == {ALUNO, OUTRO}


# ---------------------------------------------------------------------------
# Busca: preferidos primeiro, no dono, antes do corte (P1 da revisão)
# ---------------------------------------------------------------------------


def _muitos_arrozes(db, total: int = 70) -> list[int]:
    return [
        db.add_food(f"Arroz teste {i:03d}", energy_kcal=130, carbohydrates_g=28, protein_g=2.5, total_fat_g=0.2)
        for i in range(total)
    ]


def test_busca_poe_o_favorito_primeiro_mesmo_fora_do_lote_de_candidatos(db):
    ids = _muitos_arrozes(db)
    favorito = ids[65]  # além do lote de 60 candidatos e do corte de 20 resultados
    cliente = _client()
    sem = cliente.get("/nutrition/foods/search", params={"q": "arroz teste"}).json()["items"]
    assert favorito not in [i["id"] for i in sem] and not any(i["is_favorite"] for i in sem)
    assert _favoritar(cliente, favorito).status_code == 201
    itens = cliente.get("/nutrition/foods/search", params={"q": "arroz teste"}).json()["items"]
    assert itens[0]["id"] == favorito and itens[0]["is_favorite"] is True
    assert len(itens) == 20 and not any(i["is_favorite"] for i in itens[1:])


def test_busca_de_outro_usuario_nao_e_afetada_pelos_favoritos(db):
    ids = _muitos_arrozes(db)
    _favoritar(_client(), ids[65])
    antes = _client(OUTRO).get("/nutrition/foods/search", params={"q": "arroz teste"}).json()["items"]
    assert ids[65] not in [i["id"] for i in antes] and not any(i["is_favorite"] for i in antes)
    # o admin no escopo private_all também não recebe a marcação
    admin = _client(ADMIN, "admin").get("/nutrition/foods/search", params={"q": "arroz", "scope": "private_all"})
    assert all(i["is_favorite"] is False for i in admin.json()["items"])


def test_favorito_que_nao_casa_com_o_termo_nao_entra_na_busca(db):
    cliente = _client()
    _favoritar(cliente, db.feijao)
    itens = cliente.get("/nutrition/foods/search", params={"q": "arroz"}).json()["items"]
    assert db.feijao not in [i["id"] for i in itens]


def test_porcao_apagada_depois_de_favoritada_deixa_o_favorito_sem_padrao(db):
    cliente = _client()
    _favoritar(cliente, db.arroz, default_quantity=2, default_unit="portion", default_portion_id=db.porcao_arroz)
    del db.portions[db.porcao_arroz]
    item = cliente.get("/nutrition/favorites").json()["items"][0]
    assert item["food"]["id"] == db.arroz and item["default"] is None


def test_reordenar_grava_so_as_posicoes_que_mudaram_em_ordem_de_food_id(db):
    cliente = _client()
    for food_id in (db.arroz, db.feijao, db.privado_meu):
        _favoritar(cliente, food_id)
    db.queries.clear()
    resposta = cliente.post("/nutrition/favorites/reorder", json={"food_ids": [db.arroz, db.privado_meu, db.feijao]})
    assert resposta.status_code == 200
    escritas = [p for sql, p in db.queries if sql == rotas.UPDATE_POSITION_SQL]
    assert [e[2] for e in escritas] == sorted([db.feijao, db.privado_meu])  # arroz não mudou de posição
    assert all(e[1] == ALUNO for e in escritas)


def test_unidade_gravada_e_a_canonica_do_motor_e_so_com_motor_sem_erros(db):
    cliente = _client()
    assert _favoritar(cliente, db.arroz, default_quantity=10, default_unit=" G ").json()["default"]["unit"] == "g"
    assert _favoritar(cliente, db.feijao, default_quantity=10, default_unit="balde").status_code == 400
    assert [f["default_unit"] for f in db.favorites] == ["g"]

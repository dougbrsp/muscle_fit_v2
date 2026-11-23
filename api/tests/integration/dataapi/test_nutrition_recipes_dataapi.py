"""Receitas (L37, migração 125) contra MySQL 8 real, nos DOIS backends (PyMySQL e shim da RDS Data API).

Prova: criar com 2 ingredientes -> ler -> editar (versão 2) -> listar -> lançar no diário (totais = receita) ->
arquivar; a busca com `exclude_recipes` tira a receita."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_foods, nutrition_meals, nutrition_recipes  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
USUARIO = "aluno_receitas"
TACO_ARROZ, TACO_OLEO_SOJA = "3", "272"


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabela in ("meal_items", "meals", "food_recipe_ingredients", "food_recipe_versions", "food_portions",
                       "user_favorite_foods"):
            cur.execute(f"TRUNCATE TABLE {tabela}")
        cur.execute("DELETE FROM food_items WHERE source <> 'taco'")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    raw.commit()


@pytest.fixture
def aluno_id(raw, conceder_grupo):
    reset_nutrition_tables_cache()
    _limpar(raw)
    with raw.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (USUARIO,))
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (USUARIO,))
        uid = cur.lastrowid
        conceder_grupo(cur, uid, "regular")
    raw.commit()
    yield uid
    _limpar(raw)
    with raw.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (USUARIO,))
    raw.commit()


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    for modulo in (nutrition_foods, nutrition_recipes, nutrition_meals):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _taco(raw) -> dict[str, int]:
    with raw.cursor() as cur:
        cur.execute("SELECT id, source_id FROM food_items WHERE source = 'taco' AND source_id IN (%s, %s)",
                    (TACO_ARROZ, TACO_OLEO_SOJA))
        return {linha["source_id"]: int(linha["id"]) for linha in cur.fetchall()}


def test_fluxo_da_receita_identico_nos_dois_backends(aluno_id, raw, monkeypatch, fake_rds, mysql_schema):
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        taco = _taco(raw)
        c = _client(aluno_id)
        corpo = {
            "name": "Arroz refogado",
            "ingredients": [{"food_id": taco[TACO_ARROZ], "quantity": 200, "unit": "g"},
                            {"food_id": taco[TACO_OLEO_SOJA], "quantity": 10, "unit": "g"}],
            "final_weight_g": 180,
        }
        criada = c.post("/nutrition/recipes", json=corpo)
        assert criada.status_code == 201, (backend, criada.text)
        rid = criada.json()["id"]
        assert c.get(f"/nutrition/recipes/{rid}").json()["recipe"]["version"] == 1, backend

        editada = c.put(f"/nutrition/recipes/{rid}", json={**corpo, "final_weight_g": 150})
        assert editada.status_code == 200, (backend, editada.text)
        assert editada.json()["recipe"]["version"] == 2, backend
        assert [r["id"] for r in c.get("/nutrition/recipes").json()["items"]] == [rid], backend

        busca = c.get("/nutrition/foods/search", params={"q": "arroz refogado"}).json()["items"]
        assert any(i["id"] == rid for i in busca), backend
        sem = c.get("/nutrition/foods/search", params={"q": "arroz refogado", "exclude_recipes": "true"}).json()["items"]
        assert not any(i["id"] == rid for i in sem), backend

        refeicao = c.post("/nutrition/meals", json={
            "local_date": HOJE.isoformat(), "meal_slot": "lunch",
            "items": [{"food_id": rid, "quantity": 150, "unit": "g"}],
        })
        assert refeicao.status_code == 201, (backend, refeicao.text)
        with raw.cursor() as cur:
            cur.execute("SELECT energy_kcal FROM food_items WHERE id = %s", (rid,))
            energia = float(cur.fetchone()["energy_kcal"])  # por 100 g; 150 g de receita = 1,5 x
            cur.execute("SELECT catalog_snapshot_json FROM meal_items WHERE meal_id = %s", (refeicao.json()["id"],))
            snapshot = json.loads(cur.fetchone()["catalog_snapshot_json"])
        assert refeicao.json()["totals"]["energy_kcal"] == pytest.approx(energia * 1.5, abs=0.1), backend
        assert snapshot["food"]["recipe_version"] == 2, backend

        assert c.delete(f"/nutrition/foods/{rid}").status_code == 200, backend
        assert c.get("/nutrition/recipes").json()["items"] == [], backend

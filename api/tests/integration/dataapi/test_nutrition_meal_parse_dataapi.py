"""Leitura da refeição por IA (L5b) contra MySQL 8 real, nos DOIS backends
(PyMySQL de dev e shim da RDS Data API de produção). Sem chamada à OpenAI: o
que este arquivo prova é o SQL novo do lote.

- refeição lida pela IA (`POST /nutrition/meals` com `ai_job_id` e
  `description_raw`): grava `source = ai_text`, o texto e o job; o item do
  catálogo com `match_confidence` (DECIMAL(4,3)) e o item estimado com
  `food_id` NULL, natureza ESTIMATED, os nutrientes do motor
  (`meal_parse.estimated_item_nutrition`) e o snapshot `ai_estimate` (JSON); a
  edição reusa o snapshot do estimado; o payload é idêntico nos dois backends;
- a busca do worker (`nutrition_meal_parse_worker.search_candidates`: o
  FULLTEXT de `food_text` com o filtro de 2 parâmetros
  `nutrition_access.DIARY_FOODS_SQL`): o admin registrando para o aluno vê o
  alimento privado do aluno e NÃO o de outro usuário; ordem e notas idênticas
  nos dois backends.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_foods, nutrition_meal_parse_worker, nutrition_meals  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition import meal_parse as mp
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
USUARIOS = ("admin_leitura", "aluno_leitura", "outro_leitura")
# Arroz, tipo 1, cozido (TACO 4ª ed., seed da 081) pelo `source_id` (o id muda ao recarregar).
TACO_ARROZ = "3"
WHEY = {"name": "whey protein", "energy_kcal": 400, "protein_g": 80, "carbohydrates_g": 10, "total_fat_g": 5,
        "fiber_g": None}


def _limpar(raw, ids: dict[str, int] | None = None) -> None:
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("TRUNCATE TABLE meal_items")
        cur.execute("TRUNCATE TABLE meals")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        if ids:
            donos = tuple(ids.values())
            cur.execute(f"DELETE FROM food_items WHERE owner_user_id IN ({', '.join(['%s'] * len(donos))})", donos)
    raw.commit()


@pytest.fixture
def usuarios(raw):
    reset_nutrition_tables_cache()
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        for nome, papel in zip(USUARIOS, ("admin", "regular", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome.split("_")[0]] = cur.lastrowid
    raw.commit()
    _limpar(raw, ids)
    yield ids
    _limpar(raw, ids)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


def _client(user_id: int, role: str) -> TestClient:
    app = FastAPI()
    for modulo in (nutrition_foods, nutrition_meals):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _arroz_taco(raw) -> int:
    with raw.cursor() as cur:
        cur.execute("SELECT id FROM food_items WHERE source = 'taco' AND source_id = %s", (TACO_ARROZ,))
        return int(cur.fetchone()["id"])


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items()
                if k not in ("id", "user_id", "food_id", "created_at", "updated_at")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_refeicao_lida_pela_ia_identica_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    esperado = mp.estimated_item_nutrition(mp.validate_estimate(WHEY).nutrients, mm.resolve_amount(30, "g", "g"))
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        aluno = _client(usuarios["aluno"], "regular")
        corpo = {
            "local_date": HOJE.isoformat(),
            "meal_slot": "post_workout",
            "ai_job_id": "job-integracao",
            "description_raw": "arroz e 30 g de whey",
            "items": [
                {"food_id": _arroz_taco(raw), "quantity": 120, "unit": "g", "portion_id": None, "match_confidence": 1},
                {"food_id": None, "estimate": WHEY, "quantity": 30, "unit": "g", "portion_id": None},
            ],
        }
        criada = aluno.post("/nutrition/meals", json=corpo)
        assert criada.status_code == 201, (backend, criada.text)
        refeicao = criada.json()
        with raw.cursor() as cur:
            cur.execute("SELECT source, description_raw, ai_job_id FROM meals WHERE id = %s", (refeicao["id"],))
            cabecalho = cur.fetchone()
            cur.execute(
                "SELECT food_id, nature, match_confidence, energy_kcal, sodium_mg, catalog_snapshot_json "
                "FROM meal_items WHERE meal_id = %s ORDER BY position",
                (refeicao["id"],),
            )
            itens = cur.fetchall()
        assert cabecalho == {"source": "ai_text", "description_raw": "arroz e 30 g de whey", "ai_job_id": "job-integracao"}
        assert itens[0]["nature"] == "DERIVED" and itens[0]["match_confidence"] == Decimal("1.000"), backend
        assert itens[1]["food_id"] is None and itens[1]["nature"] == "ESTIMATED", backend
        assert itens[1]["match_confidence"] is None and itens[1]["sodium_mg"] is None  # NULL, nunca 0
        assert itens[1]["energy_kcal"] == Decimal(str(round(esperado.nutrients.energy_kcal, 2)))  # = motor
        assert json.loads(itens[1]["catalog_snapshot_json"])["food"]["source"] == mp.ESTIMATE_SOURCE.value
        assert refeicao["totals"]["nature"] == "MIXED", backend

        # Edição: o estimado mantido vai só com o id e reusa o snapshot congelado.
        editada = aluno.put(f"/nutrition/meals/{refeicao['id']}", json={
            "local_date": HOJE.isoformat(), "meal_slot": "post_workout",
            "items": [{"id": refeicao["items"][1]["id"], "food_id": None, "quantity": 60, "unit": "g", "portion_id": None}],
        })
        assert editada.status_code == 200, (backend, editada.text)
        [item] = editada.json()["items"]
        assert item["nature"] == "ESTIMATED" and item["food_source"] == mp.ESTIMATE_SOURCE.value, backend
        assert item["nutrients"]["energy_kcal"] == pytest.approx(2 * refeicao["items"][1]["nutrients"]["energy_kcal"], abs=0.02)
        assert editada.json()["source"] == "ai_text" and editada.json()["ai_job_id"] == "job-integracao"

        resultados[backend] = _sem_volateis({"criada": refeicao, "editada": editada.json()})
    assert resultados["pymysql"] == resultados["data-api"]


def test_busca_do_worker_no_diario_do_aluno_identica_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw, usuarios)
        for dono, nome in (("aluno", "Arroz integracao aluno"), ("outro", "Arroz integracao outro")):
            criado = _client(usuarios[dono], "regular").post("/nutrition/foods", json={
                "name": nome, "basis": "g", "energy_kcal": 130, "carbohydrates_g": 28, "protein_g": 2.5,
                "total_fat_g": 0.3})
            assert criado.status_code == 201, (backend, criado.text)

        with nutrition_meal_parse_worker.transaction() as conn:
            with conn.cursor() as cur:
                ranking, linhas = nutrition_meal_parse_worker.search_candidates(
                    cur, "arroz integracao aluno", registrant_user_id=usuarios["admin"], diary_user_id=usuarios["aluno"]
                )
        nomes = [match.name for match in ranking]
        assert nomes and nomes[0] == "Arroz integracao aluno", (backend, nomes)
        assert "Arroz integracao outro" not in nomes and all(int(m.candidate_id) in linhas for m in ranking), backend
        resultados[backend] = [(m.name, round(m.score, 9), m.auto_accept) for m in ranking]
    assert resultados["pymysql"] == resultados["data-api"]

"""Fontes externas do catálogo (L3b: Open Food Facts e USDA FoodData Central)
contra MySQL 8 real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API de
produção). Sem rede: o cliente HTTP (`app/external_food_sources`) é trocado por
dublês que devolvem os payloads REAIS gravados (`tests/support/nutrition_external_payloads.py`);
o SQL é o de verdade.

O que este arquivo prova (os unitários usam banco em memória):

- código de barras: a consulta nova grava a linha `off` PÚBLICA (com `barcode`,
  citação da ODbL e `created_by`) e a segunda vem do cache sem chamar o OFF;
  payload idêntico nos dois backends;
- limite diário: `OFF_LOOKUPS_PER_USER_DAY` linhas `off` do usuário criadas hoje
  (`NOW()`, comparadas com o início do dia NO FUSO DELE, `users.timezone`) ->
  429 sem chamar o OFF; linhas de outro usuário não contam;
- USDA: a importação é idempotente pela chave única `(source, source_id)`:
  201, depois 200, uma linha só; o `INSERT ... ON DUPLICATE KEY UPDATE id = id`
  repetido pelo shim não falha nem duplica;
- energia convertida de kJ (fração longa) cabe no DECIMAL(7,2) e volta igual
  nos dois backends.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import external_food_sources as ext
from app import nutrition_external_foods  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app import nutrition_foods  # noqa: F401
from app.domain.nutrition import catalog
from app.domain.nutrition import external_sources as fontes
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support import nutrition_external_payloads as payloads
from tests.support.dataapi_backend import BACKENDS, use_backend

USUARIOS = ("aluno_externo", "outro_externo")
MOCA = payloads.OFF_MOCA_CODE


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("DELETE FROM food_portions WHERE food_id IN (SELECT id FROM food_items WHERE source IN ('off', 'usda'))")
        cur.execute("DELETE FROM food_items WHERE source IN ('off', 'usda')")
    raw.commit()


@pytest.fixture
def usuarios(raw):
    reset_nutrition_tables_cache()
    _limpar(raw)
    ids = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        for nome in USUARIOS:
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (nome,))
            ids[nome.split("_")[0]] = cur.lastrowid
    raw.commit()
    yield ids
    _limpar(raw)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


@pytest.fixture
def fontes_externas(monkeypatch):
    chamadas = {"off": [], "usda": []}

    def _off(code, *, fields):
        chamadas["off"].append(code)
        return payloads.load(payloads.OFF_MOCA)

    def _usda(fdc_id):
        chamadas["usda"].append(fdc_id)
        return payloads.load(payloads.USDA_SR_LEGACY)

    monkeypatch.setattr(ext, "off_product", _off)
    monkeypatch.setattr(ext, "usda_food", _usda)
    monkeypatch.setenv(ext.USDA_API_KEY_ENV, "chave-de-teste")
    return chamadas


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_external_foods.router)
    app.include_router(nutrition_foods.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items() if k not in ("id", "created_at", "updated_at")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_codigo_de_barras_e_importacao_identicos_nos_dois_backends(
    usuarios, raw, fontes_externas, monkeypatch, fake_rds, mysql_schema
):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        fontes_externas["off"].clear()
        fontes_externas["usda"].clear()
        aluno, outro = _client(usuarios["aluno"]), _client(usuarios["outro"])

        nova = aluno.get(f"/nutrition/foods/barcode/{MOCA}")
        assert nova.status_code == 200, (backend, nova.text)
        assert nova.json()["from_cache"] is False and nova.json()["is_public"] is True, backend
        cache = outro.get(f"/nutrition/foods/barcode/{MOCA}")
        assert cache.status_code == 200 and cache.json()["from_cache"] is True, (backend, cache.text)
        assert cache.json()["id"] == nova.json()["id"] and fontes_externas["off"] == [MOCA], backend

        criada = aluno.post("/nutrition/foods/usda/173161/import")
        assert criada.status_code == 201, (backend, criada.text)
        repetida = outro.post("/nutrition/foods/usda/173161/import")
        assert repetida.status_code == 200 and repetida.json()["id"] == criada.json()["id"], backend
        assert fontes_externas["usda"] == [173161], backend

        with raw.cursor() as cur:
            cur.execute(
                "SELECT source, source_id, barcode, owner_user_id, created_by, citation, license, basis, energy_kcal "
                "FROM food_items WHERE source IN ('off', 'usda') ORDER BY source"
            )
            linhas = cur.fetchall()
        assert [(l["source"], l["source_id"]) for l in linhas] == [("off", MOCA), ("usda", "173161")], backend
        off, usda = linhas
        assert off["barcode"] == MOCA and off["owner_user_id"] is None and off["created_by"] == usuarios["aluno"]
        assert off["citation"].startswith(catalog.OFF_CITATION) and off["license"] == catalog.OFF_LICENSE
        assert usda["barcode"] is None and usda["citation"] == f"{catalog.USDA_CITATION} SR Legacy, FDC ID 173161."

        # A linha da fonte externa é somente leitura também no banco real.
        bloqueada = aluno.put(f"/nutrition/foods/{criada.json()['id']}", json={
            "name": "x", "energy_kcal": 1, "carbohydrates_g": 0, "protein_g": 0, "total_fat_g": 0})
        assert bloqueada.status_code == 409, backend

        resultados[backend] = _sem_volateis({"nova": nova.json(), "cache": cache.json(), "usda": criada.json()})
    assert resultados["pymysql"] == resultados["data-api"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_limite_diario_conta_as_linhas_do_usuario_hoje(backend, usuarios, raw, fontes_externas, monkeypatch,
                                                       fake_rds, mysql_schema):
    use_backend(backend, monkeypatch, fake_rds, mysql_schema)
    limite = fontes.OFF_LOOKUPS_PER_USER_DAY
    with raw.cursor() as cur:
        for i in range(limite):
            cur.execute(
                "INSERT INTO food_items (source, source_id, barcode, name, name_normalized, basis, created_by) "
                "VALUES ('off', %s, %s, %s, %s, 'g', %s)",
                (f"teste-{i}", f"teste-{i}", f"Produto {i}", f"produto {i}", usuarios["aluno"]),
            )
    raw.commit()

    bloqueada = _client(usuarios["aluno"]).get(f"/nutrition/foods/barcode/{MOCA}")
    assert bloqueada.status_code == 429, bloqueada.text
    assert bloqueada.json()["detail"]["error"] == fontes.ERR_OFF_DAILY_LIMIT
    assert fontes_externas["off"] == []

    liberada = _client(usuarios["outro"]).get(f"/nutrition/foods/barcode/{MOCA}")
    assert liberada.status_code == 200 and fontes_externas["off"] == [MOCA]


@pytest.mark.parametrize("backend", BACKENDS)
def test_insert_duplicado_nao_falha_nem_duplica_e_kj_cabe_no_decimal(backend, usuarios, raw, monkeypatch,
                                                                     fake_rds, mysql_schema):
    use_backend(backend, monkeypatch, fake_rds, mysql_schema)
    from app.db import get_conn

    produto = payloads.load(payloads.OFF_MOCA)
    del produto["nutrition"]["aggregated_set"]["nutrients"]["energy-kcal"]  # força kJ -> kcal (1365 / 4,184)
    alimento = fontes.map_off_product(produto, MOCA)
    assert alimento.ok and alimento.energy_origin is fontes.EnergyOrigin.FROM_KJ
    valores = nutrition_external_foods._insert_values(alimento, usuarios["aluno"])
    for _ in range(2):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(nutrition_external_foods.INSERT_EXTERNAL_FOOD_SQL, valores)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(nutrition_external_foods.FOOD_BY_SOURCE_SQL, ("off", MOCA))
            linhas = cur.fetchall()
    assert len(linhas) == 1
    assert float(linhas[0]["energy_kcal"]) == pytest.approx(round(alimento.values["energy_kcal"], 2))

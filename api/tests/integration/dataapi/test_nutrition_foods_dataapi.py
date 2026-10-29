"""Catálogo de alimentos (L3a, migrações 080/081) contra MySQL 8 real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de produção).

O que este arquivo prova (os testes unitários usam FakeDB e banco em memória):

- 080 e 081 aplicadas pelo schema da suíte convergem: `plan()` devolve 0
  statements lendo o `information_schema` e as 597 linhas REAIS (a 081 confere
  contagem e checksum com os tipos que o MySQL devolve), também pelo shim da
  Data API;
- o FULLTEXT de `food_items.name_normalized` existe como FULLTEXT e responde em
  modo booleano com prefixo (`termo*`) no MySQL 8 (risco R1 do plano), com o
  termo como PARÂMETRO nos dois backends;
- a 081 é idempotente no banco real: linha alterada -> 1 bloco -> reaplica ->
  0 de novo;
- a ESCRITA da 081 passa pelo shim da Data API (12 blocos com 1.050
  parâmetros cada, DECIMAL com typeHint, NULL) com a tabela vazia e com um
  bloco divergente, como na promoção;
- busca -> cadastro privado -> a busca do dono acha (FULLTEXT depois do
  commit), a de outro usuário não -> admin promove -> todos acham; payload
  idêntico nos dois backends;
- 084 (M85): converge no schema real, preenche SÓ os campos `*` da TACO com o
  USDA (a coluna JSON `field_sources_json` lida nos dois backends) e a escrita
  passa pelo shim; depois dela o `plan` da 081 (congelada, compara com o próprio
  seed) enxerga exatamente o bloco dos itens preenchidos (451–500: os 2 leites) — por isso os testes
  da 081 conferem `_plano_081_depois_da_084()` e não `[]`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_foods  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app.domain.nutrition import catalog
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend

MIGRACOES = ("080_food_catalog.py", "081_food_catalog_seed_taco.py")
M084 = "084_food_items_taco_usda_reavaliacao.py"
USUARIOS = ("admin_alimentos", "aluno_alimentos", "outro_alimentos")


def _modulo(nome: str) -> dict:
    return m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / nome).__globals__


def _plano_081_depois_da_084() -> list:
    """O que o `plan` da 081 devolve com a 084 aplicada: os blocos (de 50) que
    contêm os itens preenchidos pela 084 — o resto converge."""
    seed = _modulo(MIGRACOES[1])
    preenchidos = {registro["source_id"] for registro in _modulo(M084)["fills"]()}
    return [
        seed["upsert_statement"](bloco)
        for bloco in seed["blocks"](seed["seed_records"]())
        if any(registro["source_id"] in preenchidos for registro in bloco)
    ]


def _limpar_cadastros(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("DELETE FROM food_portions")
        cur.execute("DELETE FROM food_items WHERE source <> 'taco'")
    raw.commit()


@pytest.fixture
def usuarios(raw):
    reset_nutrition_tables_cache()
    _limpar_cadastros(raw)
    ids = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        for nome, papel in zip(USUARIOS, ("admin", "regular", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome.split("_")[0]] = cur.lastrowid
    raw.commit()
    yield ids
    _limpar_cadastros(raw)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


def _client(user_id: int, role: str) -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_foods.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items() if k not in ("id", "food_id", "created_at", "updated_at")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_080_e_081_convergem_no_schema_real(raw):
    assert _modulo(MIGRACOES[0])["plan"](m.MigrationDB(raw)) == []
    plano_081 = _modulo(MIGRACOES[1])["plan"](m.MigrationDB(raw))
    assert plano_081 == _plano_081_depois_da_084() and len(plano_081) == 1
    assert _modulo(M084)["plan"](m.MigrationDB(raw)) == []
    with raw.cursor() as cur:
        cur.execute(
            "SELECT INDEX_TYPE, COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'food_items' AND INDEX_NAME = 'ft_fi_name'"
        )
        assert cur.fetchall() == [{"INDEX_TYPE": "FULLTEXT", "COLUMN_NAME": "name_normalized"}]
        cur.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT category) AS grupos FROM food_items WHERE source = 'taco'")
        assert cur.fetchone() == {"n": 597, "grupos": 15}
        cur.execute(
            "SELECT name, name_normalized, energy_kcal, protein_g, citation, owner_user_id, basis "
            "FROM food_items WHERE source = 'taco' AND source_id = '3'"
        )
        arroz = cur.fetchone()
    assert arroz["name"] == "Arroz, tipo 1, cozido" and arroz["name_normalized"] == "arroz tipo 1 cozido"
    assert arroz["energy_kcal"] == Decimal("128.26") and arroz["protein_g"] == Decimal("2.52")
    assert arroz["citation"] == catalog.TACO_CITATION and arroz["owner_user_id"] is None and arroz["basis"] == "g"


def test_081_converge_tambem_lida_pelo_shim_da_data_api(dataapi_db):
    from app.db import get_conn

    with get_conn() as conn:
        assert _modulo(MIGRACOES[1])["plan"](m.MigrationDB(conn)) == _plano_081_depois_da_084()
        assert _modulo(M084)["plan"](m.MigrationDB(conn)) == []


def test_081_grava_os_12_blocos_pelo_shim_da_data_api(raw, dataapi_db):
    """A escrita da 081 em produção passa pelo shim (1.050 parâmetros por
    statement, DECIMAL com typeHint, NULL): aqui ela roda pelo shim + dublê,
    com a tabela vazia e com um bloco divergente."""
    from app.db import get_conn

    seed = _modulo(MIGRACOES[1])
    with raw.cursor() as cur:
        cur.execute("DELETE FROM food_portions")
        cur.execute("DELETE FROM food_items WHERE source = 'taco'")
    try:
        with get_conn() as conn:
            db = m.MigrationDB(conn)
            seed["up"](db)
        assert len(db.statements) == 12
        with raw.cursor() as cur:
            cur.execute("UPDATE food_items SET name = 'x' WHERE source = 'taco' AND source_id = '42'")
        with get_conn() as conn:
            db = m.MigrationDB(conn)
            seed["up"](db)
        assert len(db.statements) == 1
    finally:
        seed["up"](m.MigrationDB(raw))  # devolve a TACO inteira para os outros testes
        _modulo(M084)["up"](m.MigrationDB(raw))  # e os campos `*` preenchidos pela 084
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM food_items WHERE source = 'taco'")
        assert cur.fetchone()["n"] == 597
        cur.execute("SELECT name, energy_kcal, fiber_g FROM food_items WHERE source = 'taco' AND source_id = '42'")
        assert cur.fetchone() == {"name": "Milho, amido, cru", "energy_kcal": Decimal("361.37"), "fiber_g": Decimal("0.74")}
    assert seed["plan"](m.MigrationDB(raw)) == _plano_081_depois_da_084()
    assert _modulo(M084)["plan"](m.MigrationDB(raw)) == []


def test_081_e_idempotente_no_banco_real(raw):
    seed = _modulo(MIGRACOES[1])
    with raw.cursor() as cur:
        cur.execute("UPDATE food_items SET energy_kcal = 999 WHERE source = 'taco' AND source_id = '561'")
    raw.commit()
    try:
        statements = seed["plan"](m.MigrationDB(raw))
        # O bloco do 561 (551–597) + o da 084 (451–500), que a 081 enxerga como divergente.
        assert statements[:-1] == _plano_081_depois_da_084() and "561" in statements[-1][1]
        seed["up"](m.MigrationDB(raw))
        raw.commit()
    finally:
        _modulo(M084)["up"](m.MigrationDB(raw))  # a 081 reexecutada devolveu os `*` a NULL
        assert seed["plan"](m.MigrationDB(raw)) == _plano_081_depois_da_084()
        assert _modulo(M084)["plan"](m.MigrationDB(raw)) == []


def test_084_preenche_so_os_campos_em_reavaliacao_no_schema_real(raw):
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'food_items' AND COLUMN_NAME = 'field_sources_json'"
        )
        assert cur.fetchone() == {"COLUMN_TYPE": "json", "IS_NULLABLE": "YES"}
        cur.execute(
            "SELECT source_id, energy_kcal, protein_g, carbohydrates_g, total_fat_g, fiber_g, sodium_mg, "
            "field_sources_json FROM food_items WHERE source = 'taco' AND source_id IN ('450', '458', '517', '591')"
        )
        linhas = {linha["source_id"]: linha for linha in cur.fetchall()}
        cur.execute("SELECT COUNT(*) AS n FROM food_items WHERE field_sources_json IS NOT NULL")
        assert cur.fetchone()["n"] == len(_modulo(M084)["FILLS"])
    leite = linhas["458"]
    assert (leite["energy_kcal"], leite["protein_g"], leite["carbohydrates_g"], leite["total_fat_g"]) == (
        Decimal("61.00"), Decimal("3.15"), Decimal("4.78"), Decimal("3.27"))
    assert leite["sodium_mg"] == Decimal("63.76") and leite["fiber_g"] is None  # TACO medido / NA
    origem = catalog.parse_field_sources(leite["field_sources_json"])
    assert [(o.source.value, o.source_id) for o in origem] == [("usda", "172217")]
    assert linhas["517"]["energy_kcal"] is None and linhas["517"]["field_sources_json"] is None  # sal: NA
    assert linhas["591"]["energy_kcal"] is None and linhas["591"]["field_sources_json"] is None  # sem equivalente
    assert linhas["450"]["energy_kcal"] is None and linhas["450"]["field_sources_json"] is None  # sem valor (usuário)


def test_084_grava_pelo_shim_da_data_api_e_reexecuta_sem_nada(raw, dataapi_db):
    """A escrita da 084 em produção passa pelo shim (DECIMAL com typeHint e o
    JSON como texto numa coluna JSON)."""
    from app.db import get_conn

    usda = _modulo(M084)
    with raw.cursor() as cur:
        cur.execute(
            "UPDATE food_items SET energy_kcal = NULL, carbohydrates_g = NULL, protein_g = NULL, total_fat_g = NULL, "
            "field_sources_json = NULL WHERE source = 'taco' AND source_id = '457'"
        )
    try:
        with get_conn() as conn:
            db = m.MigrationDB(conn)
            usda["up"](db)
        assert len(db.statements) == 1 and db.statements[0].startswith("UPDATE food_items SET energy_kcal")
        with get_conn() as conn:
            assert usda["plan"](m.MigrationDB(conn)) == []
    finally:
        usda["up"](m.MigrationDB(raw))
    with raw.cursor() as cur:
        cur.execute("SELECT energy_kcal, field_sources_json FROM food_items WHERE source = 'taco' AND source_id = '457'")
        linha = cur.fetchone()
    assert linha["energy_kcal"] == Decimal("35.00")
    assert catalog.parse_field_sources(linha["field_sources_json"])[0].source_id == "173432"


def test_leite_da_taco_vem_com_a_origem_do_usda_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        aluno = _client(usuarios["aluno"], "regular")
        busca = aluno.get("/nutrition/foods/search", params={"q": "leite integral"})
        assert busca.status_code == 200, (backend, busca.text)
        leite = next(item for item in busca.json()["items"] if item["source_id"] == "458")
        assert leite["per_100"]["energy_kcal"] == 61.0, backend
        assert [o["source_id"] for o in leite["field_sources"]] == ["172217"], backend
        assert leite["field_sources"][0]["note"].startswith("Valor energético"), backend
        detalhe = aluno.get(f"/nutrition/foods/{leite['id']}").json()
        assert detalhe["field_sources"] == leite["field_sources"], backend
        resultados[backend] = _sem_volateis(detalhe)
    assert resultados["pymysql"] == resultados["data-api"]


def test_fulltext_booleano_com_prefixo_no_mysql_8(raw):
    with raw.cursor() as cur:
        cur.execute(
            "SELECT source_id FROM food_items WHERE MATCH(name_normalized) AGAINST (%s IN BOOLEAN MODE) "
            "AND source = 'taco' ORDER BY source_id + 0",
            ("frang* grelhad*",),
        )
        ids = [linha["source_id"] for linha in cur.fetchall()]
    assert "410" in ids and "395" in ids  # peito sem pele grelhado; coração grelhado


def test_fluxo_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar_cadastros(raw)
        admin = _client(usuarios["admin"], "admin")
        aluno = _client(usuarios["aluno"], "regular")
        outro = _client(usuarios["outro"], "regular")

        busca = aluno.get("/nutrition/foods/search", params={"q": "arroz branco cozido"})
        assert busca.status_code == 200, (backend, busca.text)
        primeiro = busca.json()["items"][0]
        assert primeiro["source_id"] == "3" and primeiro["auto_accept"] is True, backend
        assert primeiro["citation"] == catalog.TACO_CITATION
        assert isinstance(primeiro["per_100"]["energy_kcal"], float)

        curto = aluno.get("/nutrition/foods/search", params={"q": "ov"}).json()["items"]
        assert curto and all(item["name"].lower().startswith("ov") for item in curto), backend

        criado = aluno.post("/nutrition/foods", json={
            "name": "Granola da casa", "basis": "g", "energy_kcal": 420, "carbohydrates_g": 60, "protein_g": 10,
            "total_fat_g": 15, "fiber_g": 7})
        assert criado.status_code == 201, (backend, criado.text)
        food_id = criado.json()["id"]
        assert [i["name"] for i in aluno.get("/nutrition/foods/search", params={"q": "granola"}).json()["items"]] == [
            "Granola da casa"], backend
        assert outro.get("/nutrition/foods/search", params={"q": "granola"}).json()["items"] == [], backend

        porcao = aluno.post(f"/nutrition/foods/{food_id}/portions", json={"label": "1 xícara", "amount": 45})
        assert porcao.status_code == 201, (backend, porcao.text)
        # Id da linha da TACO pela busca: o teste da escrita pelo shim recarrega a
        # TACO e os ids AUTO_INCREMENT mudam (só `source_id` é estável).
        taco = admin.put(f"/nutrition/foods/{primeiro['id']}", json={
            "name": "x", "energy_kcal": 1, "carbohydrates_g": 0, "protein_g": 0, "total_fat_g": 0})
        assert taco.status_code == 409, backend

        promovido = admin.post(f"/nutrition/foods/{food_id}/promote")
        assert promovido.status_code == 200 and promovido.json()["is_public"] is True, (backend, promovido.text)
        detalhe = outro.get(f"/nutrition/foods/{food_id}").json()
        assert [p["label"] for p in detalhe["portions"]] == ["1 xícara"], backend

        resultados[backend] = _sem_volateis({"busca": busca.json(), "detalhe": detalhe})
    assert resultados["pymysql"] == resultados["data-api"]

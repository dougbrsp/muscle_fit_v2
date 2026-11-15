"""Alimentos preferidos (N3, migração 102) e EAN no cadastro manual contra MySQL 8
real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API de produção).

O que este arquivo prova (os testes unitários usam um banco em memória):

- a 102 aplicada pelo schema da suíte converge (`plan()` = 0 statements lendo o
  `information_schema` real) e a única FK é `user_favorite_foods -> users` com
  CASCADE; apagar o usuário leva a lista;
- o SQL novo roda no MySQL real e pelo shim: INSERT com `ON DUPLICATE KEY UPDATE`
  (a chave única `(user_id, food_id)` torna o favoritar idempotente), DECIMAL(10,3)
  da porção padrão, reordenar, apagar e `COUNT/MAX(position)`;
- isolamento: o outro usuário não vê nem altera a lista do aluno;
- `POST /nutrition/foods` com `barcode` grava `food_items.barcode` e o EAN já
  cadastrado responde 409 com o item existente (`FOOD_BY_BARCODE_SQL`);
- a busca com preferidos: `FAVORITE_IDS_SQL` e a busca extra `favorites_only`
  (`id IN (SELECT food_id FROM user_favorite_foods WHERE user_id = %s)`) trazem o
  favorito que casa com o termo mesmo fora do lote de candidatos, primeiro e com
  `is_favorite`, sem afetar a busca de outro usuário;
- reordenar parcial: só as posições que mudaram são regravadas;
- o payload é idêntico nos dois backends.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_favorites, nutrition_foods  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend

MIGRACAO = "102_user_favorite_foods.py"
USUARIOS = ("aluno_favoritos", "outro_favoritos")
TACO_ARROZ, TACO_FEIJAO = "3", "272"  # arroz e óleo de soja (seed da 081; o 2º só precisa existir)
EAN = "7891000100103"
MANUAL = {"name": "Barra de teste N3", "energy_kcal": 400, "carbohydrates_g": 60, "protein_g": 8, "total_fat_g": 14}


def _modulo(nome: str) -> dict:
    return m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / nome).__globals__


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("DELETE FROM user_favorite_foods")
        cur.execute("DELETE FROM food_portions WHERE owner_user_id IS NOT NULL")
        cur.execute("DELETE FROM food_items WHERE source <> 'taco'")
    raw.commit()


@pytest.fixture
def usuarios(raw, conceder_grupo):
    reset_nutrition_tables_cache()
    _limpar(raw)
    ids = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        for nome in USUARIOS:
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (nome,))
            ids[nome.split("_")[0]] = cur.lastrowid
            conceder_grupo(cur, ids[nome.split("_")[0]], "regular")
    raw.commit()
    yield ids
    _limpar(raw)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_foods.router)
    app.include_router(nutrition_favorites.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _taco(raw) -> dict[str, int]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, source_id FROM food_items WHERE source = 'taco' AND source_id IN (%s, %s)",
            (TACO_ARROZ, TACO_FEIJAO),
        )
        return {linha["source_id"]: int(linha["id"]) for linha in cur.fetchall()}


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items() if k not in ("id", "food_id", "existing_food_id", "created_at", "updated_at")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_102_converge_no_schema_real_com_a_fk_so_para_users(raw):
    assert _modulo(MIGRACAO)["plan"](m.MigrationDB(raw)) == []
    with raw.cursor() as cur:
        cur.execute(
            "SELECT REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_favorite_foods'"
        )
        assert cur.fetchall() == [{"REFERENCED_TABLE_NAME": "users", "DELETE_RULE": "CASCADE"}]


def test_apagar_o_usuario_leva_a_lista(usuarios, raw):
    comida = _taco(raw)[TACO_ARROZ]
    assert _client(usuarios["aluno"]).post("/nutrition/favorites", json={"food_id": comida}).status_code == 201
    with raw.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (usuarios["aluno"],))
        cur.execute("SELECT COUNT(*) AS n FROM user_favorite_foods WHERE user_id = %s", (usuarios["aluno"],))
        assert cur.fetchone() == {"n": 0}
    raw.commit()


def test_fluxo_dos_preferidos_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    comidas = _taco(raw)
    arroz, feijao = comidas[TACO_ARROZ], comidas[TACO_FEIJAO]
    resultados = []
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("DELETE FROM user_favorite_foods")
        raw.commit()
        aluno, outro = _client(usuarios["aluno"]), _client(usuarios["outro"])
        passos = {}
        passos["favoritar_arroz"] = aluno.post(
            "/nutrition/favorites", json={"food_id": arroz, "default_quantity": 150.5, "default_unit": "g"}
        )
        passos["favoritar_feijao"] = aluno.post("/nutrition/favorites", json={"food_id": feijao})
        passos["repetido"] = aluno.post("/nutrition/favorites", json={"food_id": arroz})
        passos["editar_porcao"] = aluno.put(
            f"/nutrition/favorites/{feijao}", json={"default_quantity": 2, "default_unit": "xicara"}
        )
        passos["reordenar"] = aluno.post("/nutrition/favorites/reorder", json={"food_ids": [feijao, arroz]})
        passos["lista_do_outro"] = outro.get("/nutrition/favorites")
        passos["outro_apaga"] = outro.delete(f"/nutrition/favorites/{arroz}")
        passos["lista_do_aluno"] = aluno.get("/nutrition/favorites")
        passos["desfavoritar"] = aluno.delete(f"/nutrition/favorites/{arroz}")
        passos["lista_final"] = aluno.get("/nutrition/favorites")
        assert [passos[k].status_code for k in passos] == [201, 201, 200, 200, 200, 200, 404, 200, 200, 200], backend
        assert passos["favoritar_arroz"].json()["default"]["quantity"] == 150.5
        assert passos["repetido"].json()["default"]["quantity"] == 150.5  # não regrava a porção
        assert passos["lista_do_outro"].json()["items"] == []
        assert [i["food"]["id"] for i in passos["lista_do_aluno"].json()["items"]] == [feijao, arroz]
        assert [i["food"]["id"] for i in passos["lista_final"].json()["items"]] == [feijao]
        resultados.append({k: _sem_volateis(v.json()) for k, v in passos.items()})
    assert resultados[0] == resultados[1]


def test_cadastro_manual_com_ean_e_conflito_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = []
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("DELETE FROM food_items WHERE source <> 'taco'")
        raw.commit()
        aluno, outro = _client(usuarios["aluno"]), _client(usuarios["outro"])
        criado = aluno.post("/nutrition/foods", json={**MANUAL, "barcode": EAN})
        conflito = aluno.post("/nutrition/foods", json={**MANUAL, "name": "Cópia", "barcode": EAN})
        do_outro = outro.post("/nutrition/foods", json={**MANUAL, "barcode": EAN})
        invalido = aluno.post("/nutrition/foods", json={**MANUAL, "name": "Inválido", "barcode": "123"})
        assert [criado.status_code, conflito.status_code, do_outro.status_code, invalido.status_code] == [201, 409, 201, 400]
        assert criado.json()["barcode"] == EAN
        assert conflito.json()["detail"]["existing_food_id"] == criado.json()["id"]
        with raw.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM food_items WHERE barcode = %s", (EAN,))
            assert cur.fetchone() == {"n": 2}  # um privado de cada usuário
        resultados.append([_sem_volateis(r.json()) for r in (criado, conflito, do_outro, invalido)])
    assert resultados[0] == resultados[1]


def test_busca_poe_o_favorito_primeiro_mesmo_fora_do_lote_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    """FULLTEXT real + subselect `favorites_only`: o favorito que ficaria além do lote de 60 candidatos
    (muitos itens com o mesmo termo) volta primeiro, com `is_favorite`; o outro usuário não vê a marca."""
    with raw.cursor() as cur:
        cur.execute("DELETE FROM food_items WHERE source <> 'taco'")
        ids = []
        for i in range(70):
            cur.execute(
                "INSERT INTO food_items (source, name, name_normalized, basis, energy_kcal, carbohydrates_g, protein_g, "
                "total_fat_g, owner_user_id, is_active, created_by) "
                "VALUES ('user', %s, %s, 'g', 100, 20, 2, 1, NULL, 1, %s)",
                (f"Zzbusca teste {i:03d}", f"zzbusca teste {i:03d}", usuarios["aluno"]),
            )
            ids.append(int(cur.lastrowid))
    raw.commit()
    favorito = ids[-1]
    resultados = []
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("DELETE FROM user_favorite_foods")
        raw.commit()
        aluno, outro = _client(usuarios["aluno"]), _client(usuarios["outro"])
        assert aluno.post("/nutrition/favorites", json={"food_id": favorito}).status_code == 201
        do_aluno = aluno.get("/nutrition/foods/search", params={"q": "zzbusca teste"}).json()["items"]
        do_outro = outro.get("/nutrition/foods/search", params={"q": "zzbusca teste"}).json()["items"]
        assert do_aluno[0]["id"] == favorito and do_aluno[0]["is_favorite"] is True
        assert not any(i["is_favorite"] for i in do_aluno[1:]) and not any(i["is_favorite"] for i in do_outro)
        assert favorito not in [i["id"] for i in do_outro]  # fora do lote de 20 para quem não favoritou
        resultados.append([_sem_volateis(i) for i in do_aluno[:3]])
    assert resultados[0] == resultados[1]


def test_reordenar_parcial_regrava_so_o_que_mudou_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    comidas = _taco(raw)
    arroz, oleo = comidas[TACO_ARROZ], comidas[TACO_FEIJAO]
    with raw.cursor() as cur:
        cur.execute("SELECT id FROM food_items WHERE source = 'taco' AND source_id = '517'")
        sal = int(cur.fetchone()["id"])
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("DELETE FROM user_favorite_foods")
        raw.commit()
        aluno = _client(usuarios["aluno"])
        for food_id in (arroz, oleo, sal):
            assert aluno.post("/nutrition/favorites", json={"food_id": food_id}).status_code == 201
        with raw.cursor() as cur:
            cur.execute("UPDATE user_favorite_foods SET updated_at = '2000-01-01 00:00:00' WHERE user_id = %s",
                        (usuarios["aluno"],))
        raw.commit()
        resposta = aluno.post("/nutrition/favorites/reorder", json={"food_ids": [arroz, sal, oleo]})
        assert resposta.status_code == 200
        assert [i["food"]["id"] for i in resposta.json()["items"]] == [arroz, sal, oleo]
        with raw.cursor() as cur:
            cur.execute("SELECT food_id, position, updated_at > '2000-01-01 00:00:00' AS tocado "
                        "FROM user_favorite_foods WHERE user_id = %s ORDER BY position", (usuarios["aluno"],))
            linhas = cur.fetchall()
        # o primeiro (arroz) não mudou de posição e não foi regravado; os outros dois, sim
        assert [(int(r["food_id"]), int(r["position"]), int(r["tocado"])) for r in linhas] == [
            (arroz, 1, 0), (sal, 2, 1), (oleo, 3, 1),
        ]

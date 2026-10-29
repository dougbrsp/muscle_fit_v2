"""Plano nutricional (L2b, migrações 078/079) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de produção).

O que este arquivo prova (o SQL das rotas só tinha rodado contra o banco em
memória dos testes unitários):

- 078 e 079 aplicadas pelo schema da suíte convergem: `plan()` devolve 0
  statements lendo o `information_schema` REAL, inclusive o
  `GENERATION_EXPRESSION` de `current_user_id` como o MySQL o reescreve;
- criar rascunho -> publicar -> `GET /nutrition/me/plan` duas vezes devolve as
  MESMAS linhas, e o segundo GET não regrava nada (hash das entradas);
- uma segunda publicação (com a vigente certa) responde 200, nunca 500, e a
  primeira vira substituída; com a vigente errada, 409 `publish_conflict`;
- os tipos voltam certos do banco (Decimal -> número, DATE -> ISO, JSON);
- a leitura das medidas da fita (pescoço/cintura) entra no sujeito (%G pela
  Marinha) nos dois backends;
- o payload é idêntico nos dois backends.
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_plans  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
MIGRACOES = ("078_nutrition_plans.py", "079_nutrition_daily_targets.py")
TABELAS = ("nutrition_daily_targets", "nutrition_plans", "body_measurements", "body_composition")


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabela in TABELAS:
            cur.execute(f"TRUNCATE TABLE {tabela}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    raw.commit()


@pytest.fixture
def usuarios(raw):
    reset_nutrition_tables_cache()
    reset_body_measurements_table_cache()
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabela in (*TABELAS, "users"):
            cur.execute(f"TRUNCATE TABLE {tabela}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        ids = {}
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_nutri', 'x', 'admin')")
        ids["admin"] = cur.lastrowid
        cur.execute(
            "INSERT INTO users (username, password_hash, role, sex, birth_date, height_cm, activity_level) "
            "VALUES ('aluno_nutri', 'x', 'regular', 'male', '1990-01-01', 180.0, 'moderate')"
        )
        ids["aluno"] = cur.lastrowid
    raw.commit()
    return ids


def _semear_aluno(raw, aluno: int) -> None:
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO body_composition (user_id, local_date, weight_kg, source) VALUES (%s, %s, 80.0, 'manual')",
            (aluno, HOJE - _dt.timedelta(days=2)),
        )
        for site, valor in (("neck", 40.0), ("waist", 85.0)):
            cur.execute(
                "INSERT INTO body_measurements (user_id, local_date, site, value_cm) VALUES (%s, %s, %s, %s)",
                (aluno, HOJE - _dt.timedelta(days=1), site, valor),
            )
    raw.commit()


def _client(user_id: int, role: str) -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items()
                if k not in ("id", "plan_id", "created_at", "updated_at", "published_at", "computed_at",
                             "superseded_at", "created_by", "published_by", "user_id")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_078_e_079_convergem_no_schema_real(raw):
    for nome in MIGRACOES:
        modulo = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / nome).__globals__
        assert modulo["plan"](m.MigrationDB(raw)) == [], nome
    with raw.cursor() as cur:
        cur.execute(
            "SELECT GENERATION_EXPRESSION FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nutrition_plans' AND COLUMN_NAME = 'current_user_id'"
        )
        expressao = cur.fetchone()["GENERATION_EXPRESSION"]
    modulo = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / MIGRACOES[0]).__globals__
    assert modulo["_normalize_expression"](expressao) == modulo["EXPECTED_CURRENT_EXPRESSION"]


def test_fluxo_completo_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        _semear_aluno(raw, usuarios["aluno"])
        admin = _client(usuarios["admin"], "admin")
        aluno = _client(usuarios["aluno"], "regular")

        assert aluno.get("/nutrition/me/plan").json()["detail"]["error"] == "no_published_plan", backend

        rascunho = admin.post("/nutrition/plans", json={"user_id": usuarios["aluno"], "strategy": "cutting"})
        assert rascunho.status_code == 201, (backend, rascunho.text)
        plano = rascunho.json()
        assert isinstance(plano["protein_g_per_kg"], float) and plano["weekly_rate_share"] == pytest.approx(0.007)

        preview = admin.post(f"/nutrition/plans/{plano['id']}/preview")
        assert preview.status_code == 200, (backend, preview.text)
        assert preview.json()["subject"]["body_fat_method"] == "navy"  # medidas da fita lidas no banco real

        publicado = admin.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None})
        assert publicado.status_code == 200, (backend, publicado.text)
        assert publicado.json()["plan"]["effective_from"] == HOJE.isoformat()

        primeiro = aluno.get("/nutrition/me/plan")
        assert primeiro.status_code == 200, (backend, primeiro.text)
        with raw.cursor() as cur:
            cur.execute("SELECT local_date, computed_at FROM nutrition_daily_targets ORDER BY local_date")
            antes = cur.fetchall()
        segundo = aluno.get("/nutrition/me/plan")
        assert segundo.json() == primeiro.json()
        with raw.cursor() as cur:
            cur.execute("SELECT local_date, computed_at FROM nutrition_daily_targets ORDER BY local_date")
            assert cur.fetchall() == antes  # nada regravado no segundo GET

        corpo = primeiro.json()
        hoje = corpo["today_target"]
        assert hoje["local_date"] == HOJE.isoformat()
        assert isinstance(hoje["kcal"], float) and isinstance(hoje["limitations"], list)
        assert hoje["training_known"] is True

        # Tipos de volta do banco (conexão neutra).
        with raw.cursor() as cur:
            cur.execute(
                "SELECT local_date, kcal_target, inputs_json, limitations_json, training_known, inputs_hash "
                "FROM nutrition_daily_targets WHERE local_date = %s",
                (HOJE,),
            )
            linha = cur.fetchone()
        assert isinstance(linha["local_date"], _dt.date) and isinstance(linha["kcal_target"], Decimal)
        assert json.loads(linha["inputs_json"])["subject"]["sex"] == "male"
        assert isinstance(json.loads(linha["limitations_json"]), list)
        assert linha["training_known"] == 1 and len(linha["inputs_hash"]) == 64

        # Segunda publicação: com a vigente errada, 409; com a certa, 200 (nunca 500).
        outro = admin.post("/nutrition/plans", json={"user_id": usuarios["aluno"], "strategy": "maintenance"}).json()
        errado = admin.post(f"/nutrition/plans/{outro['id']}/publish", json={"expected_current_id": None})
        assert errado.status_code == 409 and errado.json()["detail"]["error"] == "publish_conflict"
        certo = admin.post(f"/nutrition/plans/{outro['id']}/publish", json={"expected_current_id": plano["id"]})
        assert certo.status_code == 200, (backend, certo.text)
        estados = {p["id"]: p["state"] for p in admin.get(f"/nutrition/plans?user_id={usuarios['aluno']}").json()["items"]}
        assert estados == {plano["id"]: "superseded", outro["id"]: "current"}
        assert aluno.get("/nutrition/me/plan").json()["plan"]["id"] == outro["id"]

        resultados[backend] = _sem_volateis(corpo)
    assert resultados["pymysql"] == resultados["data-api"]


def test_recalculo_e_janela_do_motor(usuarios, raw, db_backend):
    _limpar(raw)
    _semear_aluno(raw, usuarios["aluno"])
    admin = _client(usuarios["admin"], "admin")
    plano = admin.post("/nutrition/plans", json={"user_id": usuarios["aluno"], "strategy": "maintenance"}).json()
    assert admin.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None}).status_code == 200
    congelado = admin.post("/nutrition/targets/recompute", json={
        "user_id": usuarios["aluno"], "date_from": (HOJE - _dt.timedelta(days=2)).isoformat()})
    assert congelado.status_code == 400 and congelado.json()["detail"]["error"] == "date_from_frozen", db_backend
    ate = HOJE + _dt.timedelta(days=10)
    ok = admin.post("/nutrition/targets/recompute", json={
        "user_id": usuarios["aluno"], "date_from": HOJE.isoformat(), "date_to": ate.isoformat()})
    assert ok.status_code == 200, (db_backend, ok.text)
    assert [m["local_date"] for m in ok.json()["targets"]][-1] == ate.isoformat()

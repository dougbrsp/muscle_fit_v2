"""Diário de refeições (L4, migrações 082/083) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de produção).

O que este arquivo prova (o SQL das rotas só tinha rodado contra o banco em
memória dos testes unitários):

- 082 e 083 aplicadas pelo schema da suíte convergem: `plan()` devolve 0
  statements lendo o `information_schema` REAL; as FKs são só
  `meals -> users` e `meal_items -> meals` (CASCADE de verdade);
- refeição com as linhas REAIS da TACO (seed da 081): os nutrientes gravados
  por item são os do motor (DECIMAL arredondado pela coluna), com o snapshot
  JSON e a hora (TIME) voltando certos; o óleo entra com proteína e carbo 0
  inferidos e o sal deixa a energia do total incompleta (NULL ≠ 0);
- preview não grava; `GET /nutrition/days/{date}` materializa a meta de hoje e
  a segunda leitura não regrava; o dia que congelou SEM meta é gravado UMA vez
  (`INSERT ... ON DUPLICATE KEY UPDATE id = id`) com `target_backfilled`;
- água e fome (`nutrition_days`, upsert), a kcal do treino com a proveniência
  da coluna (`manual`) e a estimativa MET, a semana (7 dias) e a edição que
  reusa o snapshot; apagar a refeição leva os itens pela FK;
- IDOR: outro aluno recebe 403;
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
from app import nutrition_days, nutrition_meals, nutrition_plans  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.domain.nutrition import daily_target as dt
from app.domain.nutrition import meal_math as mm
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
MIGRACOES = ("082_meals.py", "083_nutrition_days.py")
TABELAS = ("meal_items", "meals", "nutrition_days", "nutrition_daily_targets", "nutrition_plans", "body_composition",
           "activities")
USUARIOS = ("admin_diario", "aluno_diario", "outro_diario")
# Linhas da TACO 4ª ed. (seed da 081) pelo `source_id` (o id muda ao recarregar).
TACO_ARROZ, TACO_OLEO_SOJA, TACO_SAL_GROSSO = "3", "272", "517"


def _modulo(nome: str) -> dict:
    return m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / nome).__globals__


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
    _limpar(raw)
    ids = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_diario', 'x', 'admin')")
        ids["admin"] = cur.lastrowid
        for nome in USUARIOS[1:]:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, sex, birth_date, height_cm, activity_level) "
                "VALUES (%s, 'x', 'regular', 'male', '1990-01-01', 180.0, 'moderate')",
                (nome,),
            )
            ids[nome.split("_")[0]] = cur.lastrowid
    raw.commit()
    yield ids
    _limpar(raw)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


def _taco(raw) -> dict[str, int]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, source_id FROM food_items WHERE source = 'taco' AND source_id IN (%s, %s, %s)",
            (TACO_ARROZ, TACO_OLEO_SOJA, TACO_SAL_GROSSO),
        )
        return {linha["source_id"]: int(linha["id"]) for linha in cur.fetchall()}


def _client(user_id: int, role: str) -> TestClient:
    app = FastAPI()
    for modulo in (nutrition_plans, nutrition_meals, nutrition_days):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _publicar_plano_desde(raw, admin: TestClient, aluno: int, inicio: _dt.date) -> None:
    plano = admin.post("/nutrition/plans", json={"user_id": aluno, "strategy": "maintenance"}).json()
    assert admin.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None}).status_code == 200
    with raw.cursor() as cur:
        # Plano vigente desde antes (o motor decide a vigência pelo `effective_from`).
        cur.execute("UPDATE nutrition_plans SET effective_from = %s WHERE id = %s", (inicio, plano["id"]))
        cur.execute("DELETE FROM nutrition_daily_targets WHERE user_id = %s", (aluno,))
    raw.commit()


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items()
                if k not in ("id", "user_id", "food_id", "plan_id", "created_at", "updated_at", "computed_at")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_082_e_083_convergem_no_schema_real_com_as_fks_do_desenho(raw):
    for nome in MIGRACOES:
        assert _modulo(nome)["plan"](m.MigrationDB(raw)) == [], nome
    with raw.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME IN ('meals', 'meal_items', 'nutrition_days') "
            "ORDER BY TABLE_NAME"
        )
        fks = {(linha["TABLE_NAME"], linha["REFERENCED_TABLE_NAME"], linha["DELETE_RULE"]) for linha in cur.fetchall()}
    assert fks == {("meal_items", "meals", "CASCADE"), ("meals", "users", "CASCADE"), ("nutrition_days", "users", "CASCADE")}


def test_fluxo_do_diario_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    passado = HOJE - _dt.timedelta(days=5)
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        taco = _taco(raw)
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO body_composition (user_id, local_date, weight_kg, source) VALUES (%s, %s, 80.0, 'manual')",
                (usuarios["aluno"], HOJE - _dt.timedelta(days=20)),
            )
            cur.execute(
                "INSERT INTO activities (user_id, local_date, name, session_status, duration_seconds, calories_kcal, "
                "calories_source) VALUES (%s, %s, 'Treino A', 'done', 3600, 410.00, 'manual')",
                (usuarios["aluno"], HOJE),
            )
        raw.commit()
        admin = _client(usuarios["admin"], "admin")
        aluno = _client(usuarios["aluno"], "regular")
        _publicar_plano_desde(raw, admin, usuarios["aluno"], HOJE - _dt.timedelta(days=10))

        corpo = {
            "local_date": HOJE.isoformat(), "meal_slot": "lunch", "eaten_at": "12:30",
            "items": [{"food_id": taco[TACO_ARROZ], "quantity": 150, "unit": "g"},
                      {"food_id": taco[TACO_OLEO_SOJA], "quantity": 1, "unit": "colher_sopa"}],
        }
        preview = aluno.post("/nutrition/meals/preview", json=corpo)
        assert preview.status_code == 200, (backend, preview.text)
        with raw.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM meals")
            assert cur.fetchone()["n"] == 0, backend  # preview não grava

        criada = aluno.post("/nutrition/meals", json=corpo)
        assert criada.status_code == 201, (backend, criada.text)
        refeicao = criada.json()
        assert refeicao["eaten_at"] == "12:30"
        oleo = refeicao["items"][1]
        assert oleo["nutrients"]["protein_g"] == 0.0 and oleo["nutrients"]["sodium_mg"] is None
        assert refeicao["totals"]["incomplete_fields"] == ["sodium_mg"]
        with raw.cursor() as cur:
            cur.execute(
                "SELECT energy_kcal, protein_g, sodium_mg, amount, catalog_snapshot_json, limitations_json "
                "FROM meal_items WHERE meal_id = %s ORDER BY position",
                (refeicao["id"],),
            )
            itens = cur.fetchall()
        arroz_motor = mm.item_nutrition(
            mm.nutrients_from_values(json.loads(itens[0]["catalog_snapshot_json"])["food"]["per_100"]),
            mm.resolve_amount(150, "g", "g"),
        )
        assert itens[0]["energy_kcal"] == Decimal(str(round(arroz_motor.nutrients.energy_kcal, 2)))  # = motor
        assert itens[1]["protein_g"] == Decimal("0.00") and itens[1]["sodium_mg"] is None  # NULL, nunca 0
        assert "macro_inferred_zero:protein_g" in json.loads(itens[1]["limitations_json"])

        # Sal: energia do total incompleta (soma do que se conhece + campo marcado).
        com_sal = aluno.post("/nutrition/meals/preview", json={**corpo, "items": [
            *corpo["items"], {"food_id": taco[TACO_SAL_GROSSO], "quantity": 1, "unit": "g"}]}).json()
        assert "energy_kcal" in com_sal["totals"]["incomplete_fields"]

        primeiro = aluno.get(f"/nutrition/days/{HOJE.isoformat()}")
        assert primeiro.status_code == 200, (backend, primeiro.text)
        dia = primeiro.json()
        assert dia["target"]["local_date"] == HOJE.isoformat() and isinstance(dia["target"]["kcal"], float)
        assert dia["training"]["sessions"][0]["calories_source"] == "manual"
        assert dia["training"]["comparison"]["reported"]["kcal"] == 410.0
        assert dia["training"]["comparison"]["estimated"]["kcal"] == pytest.approx(294.0)
        with raw.cursor() as cur:
            cur.execute("SELECT local_date, computed_at FROM nutrition_daily_targets ORDER BY local_date")
            antes = cur.fetchall()
        assert aluno.get(f"/nutrition/days/{HOJE.isoformat()}").json() == dia
        with raw.cursor() as cur:
            cur.execute("SELECT local_date, computed_at FROM nutrition_daily_targets ORDER BY local_date")
            assert cur.fetchall() == antes  # nada regravado na segunda leitura

        # Dia que congelou sem meta: gravado UMA vez, marcado pelo motor.
        retro = aluno.get(f"/nutrition/days/{passado.isoformat()}").json()
        assert retro["target_action"] == "backfill" and retro["target"]["backfilled"] is True
        with raw.cursor() as cur:
            cur.execute(
                "SELECT computed_at, limitations_json, inputs_json FROM nutrition_daily_targets "
                "WHERE user_id = %s AND local_date = %s",
                (usuarios["aluno"], passado),
            )
            gravada = cur.fetchone()
        assert dt.LIMIT_TARGET_BACKFILLED in json.loads(gravada["limitations_json"])
        assert json.loads(gravada["inputs_json"])["materialization"]["kind"] == dt.BACKFILL_KIND
        de_novo = aluno.get(f"/nutrition/days/{passado.isoformat()}").json()
        assert de_novo["target_action"] == "read" and de_novo["target"]["kcal"] == retro["target"]["kcal"]

        # Água e fome.
        anotado = aluno.put(f"/nutrition/days/{HOJE.isoformat()}", json={"water_ml": 2500, "hunger": 3})
        assert anotado.status_code == 200 and anotado.json()["day_log"]["water_ml"] == 2500.0
        assert aluno.put(f"/nutrition/days/{HOJE.isoformat()}", json={"water_ml": 1800, "hunger": 4}).status_code == 200

        semana = aluno.get(f"/nutrition/weeks?start={HOJE.isoformat()}")
        assert semana.status_code == 200 and len(semana.json()["days"]) == 7, backend

        # Edição: o item mantido reusa o snapshot; apagar leva os itens (FK real).
        editada = aluno.put(f"/nutrition/meals/{refeicao['id']}", json={**corpo, "meal_slot": "dinner", "items": [
            {"id": refeicao["items"][0]["id"], "food_id": taco[TACO_ARROZ], "quantity": 300, "unit": "g"}]})
        assert editada.status_code == 200, (backend, editada.text)
        assert editada.json()["items"][0]["nutrients"]["energy_kcal"] == pytest.approx(
            2 * refeicao["items"][0]["nutrients"]["energy_kcal"], abs=0.02)

        intruso = _client(usuarios["outro"], "regular")
        assert intruso.delete(f"/nutrition/meals/{refeicao['id']}").status_code == 403
        assert intruso.get(f"/nutrition/days/{HOJE.isoformat()}?user_id={usuarios['aluno']}").status_code == 403

        final = aluno.get(f"/nutrition/days/{HOJE.isoformat()}").json()
        assert aluno.delete(f"/nutrition/meals/{refeicao['id']}").status_code == 200
        with raw.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM meal_items")
            assert cur.fetchone()["n"] == 0, backend

        resultados[backend] = _sem_volateis({"dia": final, "semana": semana.json(), "retro": retro})
    assert resultados["pymysql"] == resultados["data-api"]

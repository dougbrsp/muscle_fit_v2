"""Lote G (migração 108: fechamento do dia e correção de atividade) contra MySQL REAL, nos DOIS backends (PyMySQL de dev e
shim da RDS Data API de produção).

Só o banco real prova o que o banco em memória dos unitários simula:

- a 108 aplicada pelo schema da suíte converge (`plan()` = 0 statements lendo o `information_schema` real) e tem a forma
  e a FK em cascata prometidas;
- `PUT/DELETE /nutrition/days/{date}/close` (ON DUPLICATE KEY UPDATE com `closed_at = closed_at`, `UTC_TIMESTAMP()`,
  DELETE) rodam nos dois backends: fechar duas vezes mantém UMA linha e o primeiro carimbo; reabrir apaga;
- `PUT/DELETE /nutrition/activities/{id}/correction` gravam uma linha por atividade (UPSERT), refletem no JSON do dia
  (`excluded`, `correction`) e atividade de outro usuário é 404 sem gravar nada;
- apagar o usuário leva fechamentos e correções junto (FK ON DELETE CASCADE);
- o payload é idêntico nos dois backends.

Rodar: só no CI (integração nunca roda localmente). cd api && python -m pytest tests/integration/dataapi/test_nutrition_lote_g_dataapi.py
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_days, nutrition_meals, nutrition_plans  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
ONTEM = HOJE - _dt.timedelta(days=1)
MIGRACAO = "108_nutricao_fechamento_dia_correcao_atividades.py"
TABELAS = ("nutrition_day_closures", "activity_energy_corrections", "meal_items", "meals", "nutrition_days",
           "nutrition_daily_targets", "nutrition_plans", "body_composition", "activities", "intervals_activities",
           "intervals_sync_runs")
USUARIOS = ("admin_lote_g", "aluno_lote_g", "outro_lote_g")


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabela in TABELAS:
            cur.execute(f"TRUNCATE TABLE {tabela}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    raw.commit()


@pytest.fixture
def usuarios(raw, conceder_grupo):
    reset_nutrition_tables_cache()
    reset_body_measurements_table_cache()
    _limpar(raw)
    ids = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_lote_g', 'x', 'admin')")
        ids["admin"] = cur.lastrowid
        conceder_grupo(cur, ids["admin"], "admin")
        for nome in USUARIOS[1:]:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, sex, birth_date, height_cm, activity_level) "
                "VALUES (%s, 'x', 'regular', 'male', '1990-01-01', 180.0, 'moderate')",
                (nome,),
            )
            ids[nome.split("_")[0]] = cur.lastrowid
            conceder_grupo(cur, ids[nome.split("_")[0]], "regular")
    raw.commit()
    yield ids
    _limpar(raw)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


def _client(user_id: int, role: str) -> TestClient:
    app = FastAPI()
    for modulo in (nutrition_plans, nutrition_meals, nutrition_days):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _atividade(raw, user_id: int, external_id: str) -> int:
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO intervals_activities (user_id, external_id, activity_date, start_time, name, sport_type, "
            "moving_time_seconds, elapsed_time_seconds, calories_kcal, raw_json) "
            "VALUES (%s, %s, %s, %s, 'Musculação', 'WeightTraining', 3600, 3600, 400, CAST('{}' AS JSON))",
            (user_id, external_id, ONTEM, _dt.datetime.combine(ONTEM, _dt.time(18, 0))),
        )
        atividade = cur.lastrowid
    raw.commit()
    return int(atividade)


def _linhas(raw, sql: str, params=()):
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def test_108_converge_e_tem_a_forma_e_a_fk_em_cascata(usuarios, raw):
    modulo = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / MIGRACAO).__globals__
    assert modulo["plan"](m.MigrationDB(raw)) == []
    chaves = {
        r["TABLE_NAME"]: [x["COLUMN_NAME"] for x in _linhas(
            raw,
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %s AND INDEX_NAME = 'PRIMARY' ORDER BY SEQ_IN_INDEX", (r["TABLE_NAME"],))]
        for r in _linhas(
            raw,
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME IN ('nutrition_day_closures', 'activity_energy_corrections')")
    }
    assert chaves == {"nutrition_day_closures": ["user_id", "day"], "activity_energy_corrections": ["user_id", "activity_id"]}
    fks = _linhas(
        raw,
        "SELECT TABLE_NAME, REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME IN ('nutrition_day_closures', 'activity_energy_corrections') "
        "ORDER BY TABLE_NAME",
    )
    assert [(r["TABLE_NAME"], r["REFERENCED_TABLE_NAME"], r["DELETE_RULE"]) for r in fks] == [
        ("activity_energy_corrections", "users", "CASCADE"), ("nutrition_day_closures", "users", "CASCADE")]
    with raw.cursor() as cur:  # apagar o usuário leva as duas tabelas junto
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('efemero_lote_g', 'x', 'regular')")
        efemero = cur.lastrowid
        cur.execute("INSERT INTO nutrition_day_closures (user_id, day, closed_at) VALUES (%s, %s, UTC_TIMESTAMP())", (efemero, ONTEM))
        cur.execute(
            "INSERT INTO activity_energy_corrections (user_id, activity_id, action, energy_kind, created_at, updated_at) "
            "VALUES (%s, '1', 'exclude', NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP())", (efemero,))
        cur.execute("DELETE FROM users WHERE id = %s", (efemero,))
    raw.commit()
    assert not _linhas(raw, "SELECT 1 FROM nutrition_day_closures WHERE user_id = %s", (efemero,))  # pymysql: tupla vazia
    assert not _linhas(raw, "SELECT 1 FROM activity_energy_corrections WHERE user_id = %s", (efemero,))


def test_fechar_reabrir_e_corrigir_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        aluno, outro = usuarios["aluno"], usuarios["outro"]
        alheia = _atividade(raw, outro, f"alheia-{backend}")
        propria = _atividade(raw, aluno, f"propria-{backend}")
        cliente = _client(aluno, "regular")

        fechado = cliente.put(f"/nutrition/days/{ONTEM.isoformat()}/close")
        assert fechado.status_code == 200, (backend, fechado.text)
        assert fechado.json()["closure"]["closed"] is True and fechado.json()["closure"]["closed_at"], backend
        primeira = _linhas(raw, "SELECT closed_at FROM nutrition_day_closures WHERE user_id = %s", (aluno,))
        assert cliente.put(f"/nutrition/days/{ONTEM.isoformat()}/close").status_code == 200, backend
        segunda = _linhas(raw, "SELECT closed_at FROM nutrition_day_closures WHERE user_id = %s", (aluno,))
        assert len(segunda) == 1 and segunda == primeira, backend  # refechar: uma linha, primeiro carimbo
        futuro = cliente.put(f"/nutrition/days/{(HOJE + _dt.timedelta(days=1)).isoformat()}/close")
        assert futuro.status_code == 422, backend
        reaberto = cliente.delete(f"/nutrition/days/{ONTEM.isoformat()}/close")
        assert reaberto.status_code == 200 and reaberto.json()["closure"] == {"closed": False, "closed_at": None}, backend
        assert not _linhas(raw, "SELECT 1 FROM nutrition_day_closures WHERE user_id = %s", (aluno,)), backend

        negada = cliente.put(f"/nutrition/activities/{alheia}/correction", json={"action": "exclude"})
        assert negada.status_code == 404, (backend, negada.text)
        assert not _linhas(raw, "SELECT 1 FROM activity_energy_corrections"), backend
        reclassificada = cliente.put(
            f"/nutrition/activities/{propria}/correction", json={"action": "reclassify", "energy_kind": "active"})
        assert reclassificada.status_code == 200, (backend, reclassificada.text)
        assert reclassificada.json()["local_date"] == ONTEM.isoformat(), backend
        excluida = cliente.put(f"/nutrition/activities/{propria}/correction", json={"action": "exclude"})
        assert excluida.status_code == 200, (backend, excluida.text)
        linhas = _linhas(raw, "SELECT activity_id, action, energy_kind FROM activity_energy_corrections WHERE user_id = %s", (aluno,))
        assert [(r["activity_id"], r["action"], r["energy_kind"]) for r in linhas] == [(str(propria), "exclude", None)], backend
        desfeita = cliente.delete(f"/nutrition/activities/{propria}/correction")
        assert desfeita.status_code == 200, backend
        assert not _linhas(raw, "SELECT 1 FROM activity_energy_corrections"), backend
        resultados[backend] = (fechado.json()["closure"]["closed"], futuro.status_code, negada.status_code, len(linhas))
    assert resultados["pymysql"] == resultados["data-api"]

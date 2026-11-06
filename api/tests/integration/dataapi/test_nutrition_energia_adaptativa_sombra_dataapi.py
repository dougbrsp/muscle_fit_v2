"""Sombra do gasto adaptativo (`app/nutrition_energy_service.py`) contra MySQL real, nos DOIS backends (PyMySQL de dev e
shim da RDS Data API). Só o CI roda este arquivo; não roda localmente.

Prova os SELECTs novos que o `GET /nutrition/dashboard` ganhou (só leitura): `MEAL_TOTALS_SQL` (horizonte do motor),
`TARGET_BMR_SQL`, `INTERVALS_SYNC_SQL` (`MAX(synced_at)` e `MIN(activity_date)`) e `SHADOW_ACTIVITIES_SQL` (sem
`raw_json`): a atividade vira kcal ativa normalizada, o dia coberto pela sincronização é confirmado (descanso = 0),
o dia anterior à primeira atividade importada NÃO é confirmado (nunca zero por ausência), a linha de outro usuário não
entra e o payload é idêntico nos dois backends."""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_dashboard, nutrition_meals  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
TABELAS = ("intervals_activities", "meal_items", "meals", "nutrition_days", "nutrition_daily_targets", "body_composition")
USUARIOS = ("aluno_sombra", "outro_sombra")


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
        for nome in USUARIOS:
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


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _atividade(cur, usuario: int, externo: str, dia: _dt.date, kcal: float) -> None:
    cur.execute(
        "INSERT INTO intervals_activities (user_id, external_id, activity_date, start_time, name, sport_type, "
        "elapsed_time_seconds, moving_time_seconds, calories_kcal, raw_json) "
        "VALUES (%s, %s, %s, %s, 'Musculação', 'WeightTraining', 3600, 3000, %s, JSON_OBJECT('grande', 'nao deve sair'))",
        (usuario, externo, dia, _dt.datetime.combine(dia, _dt.time(18, 0)), kcal),
    )


def test_sombra_do_dashboard_identica_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        primeira = HOJE - _dt.timedelta(days=5)
        with raw.cursor() as cur:
            for indice in range(10):
                cur.execute(
                    "INSERT INTO body_composition (user_id, local_date, weight_kg, body_fat_percent, source) "
                    "VALUES (%s, %s, %s, 20.0, 'manual')",
                    (usuarios["aluno"], HOJE - _dt.timedelta(days=20 - indice), 80.0 - 0.1 * indice),
                )
            _atividade(cur, usuarios["aluno"], "f1", primeira, 428.0)
            _atividade(cur, usuarios["outro"], "x1", primeira + _dt.timedelta(days=1), 999.0)  # outro usuário: não entra
        raw.commit()
        resposta = _client(usuarios["aluno"]).get("/nutrition/dashboard?days=7")
        assert resposta.status_code == 200, (backend, resposta.text)
        bloco = resposta.json()["adaptive_energy"]
        assert bloco["available"] is True and bloco["applied"] is False, (backend, bloco["unavailable_reason"])
        por_dia = {d["day"]: d for d in bloco["days"]}
        dia_da_forca = por_dia[primeira.isoformat()]
        assert dia_da_forca["confirmed"] is True and dia_da_forca["counted_activities"] == 1, backend
        atividade = dia_da_forca["activities"][0]
        assert atividade["reported_kcal"] == 428.0 and 0 < atividade["active_kcal"] < 428.0, backend  # desconto do repouso
        assert atividade["kind"] == "strength" and atividade["flags"] == ["reduced_precision"], backend
        seguinte = por_dia[(primeira + _dt.timedelta(days=1)).isoformat()]
        assert seguinte["confirmed"] is True and seguinte["active_kcal"] == 0.0 and seguinte["activities"] == [], backend
        assert HOJE.isoformat() in por_dia and por_dia[HOJE.isoformat()]["confirmed"] is False  # hoje: pendente
        assert "nao deve sair" not in resposta.text, backend  # `raw_json` não chega
        resultados[backend] = {d: (v["confirmed"], v["active_kcal"], v["counted_activities"]) for d, v in por_dia.items()}
    assert len({repr(v) for v in resultados.values()}) == 1, resultados  # idêntico nos dois backends

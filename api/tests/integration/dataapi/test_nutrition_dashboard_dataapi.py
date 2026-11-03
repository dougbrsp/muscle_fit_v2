"""Dashboard de nutrição (N5) contra MySQL real, nos DOIS backends (PyMySQL de
dev e shim da RDS Data API de produção). Só o CI roda este arquivo.

Prova o SQL novo de `app.nutrition_dashboard` (massa muscular da janela e os totais
das refeições sem texto livre), `GET /nutrition/today` e a leitura das metas gravadas: o payload é idêntico nos dois backends, o peso e a
massa muscular voltam como número, a projeção usa a primeira pesagem do plano
como partida e o terceiro recebe 403.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_dashboard, nutrition_meals, nutrition_plans  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
TABELAS = ("meal_items", "meals", "nutrition_days", "nutrition_daily_targets", "nutrition_plans", "body_composition")
USUARIOS = ("admin_painel", "aluno_painel", "outro_painel")


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
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_painel', 'x', 'admin')")
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


def _client(user_id: int, role: str) -> TestClient:
    app = FastAPI()
    for modulo in (nutrition_plans, nutrition_meals, nutrition_dashboard):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items()
                if k not in ("id", "user_id", "plan_id", "created_at", "updated_at", "computed_at")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_dashboard_identico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    inicio_plano = HOJE - _dt.timedelta(days=20)
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        _limpar(raw)
        with raw.cursor() as cur:
            for indice in range(10):
                cur.execute(
                    "INSERT INTO body_composition (user_id, local_date, weight_kg, body_fat_percent, muscle_mass_kg, source) "
                    "VALUES (%s, %s, %s, %s, %s, 'manual')",
                    (usuarios["aluno"], HOJE - _dt.timedelta(days=18 - 2 * indice), 82.0 - 0.3 * indice, 20.0, 36.5),
                )
        raw.commit()
        admin = _client(usuarios["admin"], "admin")
        aluno = _client(usuarios["aluno"], "regular")
        plano = admin.post("/nutrition/plans", json={"user_id": usuarios["aluno"], "strategy": "cutting",
                                                     "goal_weight_kg": 75}).json()
        assert admin.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None}).status_code == 200
        with raw.cursor() as cur:
            cur.execute("UPDATE nutrition_plans SET effective_from = %s WHERE id = %s", (inicio_plano, plano["id"]))
            cur.execute("DELETE FROM nutrition_daily_targets WHERE user_id = %s", (usuarios["aluno"],))
        raw.commit()

        resposta = aluno.get("/nutrition/dashboard?days=28")
        assert resposta.status_code == 200, (backend, resposta.text)
        corpo = resposta.json()
        pontos = corpo["body"]["points"]
        assert len(pontos) == 10 and pontos[0]["muscle_mass_kg"] == 36.5 and pontos[0]["body_fat_percent"] == 20.0
        assert corpo["plan"]["strategy"] == "cutting" and corpo["plan"]["goal_weight_kg"] == 75.0
        assert corpo["projection"]["goal_kind"] == "weight"
        assert corpo["projection"]["progress_percent"] is not None  # partida = primeira pesagem do plano
        assert corpo["days"][-1]["state"] == "open_today"  # hoje ainda não fechou
        hoje = aluno.get("/nutrition/today")
        assert hoje.status_code == 200, (backend, hoje.text)
        assert hoje.json()["target"] is not None and hoje.json()["balance"]["rows"][0]["to_consume"] is not None
        assert corpo["adaptive_tdee"]["applied"] is False
        assert aluno.get("/nutrition/dashboard").status_code == 200
        assert _client(usuarios["outro"], "regular").get(f"/nutrition/dashboard?user_id={usuarios['aluno']}").status_code == 403
        resultados[backend] = _sem_volateis({**corpo, "today_target": hoje.json()["target"]})
    primeiro, *demais = resultados.values()
    for outro in demais:
        assert outro == primeiro

"""Modo automático do gasto (`auto_intervals`) contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API).

O que este arquivo prova (o SQL novo só tinha rodado contra o banco em memória dos testes unitários):

- `JSON_EXTRACT(inputs_json, ...)` de `_TARGETS_SQL` devolve o extra e a base gravados pelo motor e `/nutrition/days`
  os expõe (a leitura anterior não selecionava `inputs_json` e a tela via sempre extra 0);
- `EXTRA_ACTIVITIES_SQL` (`intervals_activities` + `LEFT JOIN activity_intervals_link`): a força vinculada não soma, o
  cardio/caminhada e QUALQUER outra atividade sem vínculo (inclusive `WeightTraining`) somam, e atividade sem kcal não soma
  (ADR 0008, `nutrition-v3`: toda atividade do Intervals conta; a sessão do app entra líquida do repouso da janela);
- `INTERVALS_CONNECTION_SQL` e `INTERVALS_SYNC_AGE_SQL` (só `intervals_activities.synced_at`): sincronizado = rotina +
  treino + extras; sincronização parada = fallback (`auto_fallback`), sem extras;
- o payload é idêntico nos dois backends.

Roda no CI; não roda localmente."""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_days, nutrition_meals, nutrition_plans  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
TABELAS = ("activity_intervals_link", "intervals_activities", "activities", "nutrition_daily_targets", "nutrition_plans",
           "body_composition")
USUARIOS = ("admin_extras", "aluno_extras")


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
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_extras', 'x', 'admin')")
        ids["admin"] = cur.lastrowid
        cur.execute(
            "INSERT INTO users (username, password_hash, role, sex, birth_date, height_cm, activity_level, "
            "intervals_athlete_id, intervals_api_key_encrypted) "
            "VALUES ('aluno_extras', 'x', 'regular', 'male', '1990-01-01', 180.0, 'active', 'i1', 'chave')"
        )
        ids["aluno"] = cur.lastrowid
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


def _publicar_automatico(raw, admin: TestClient, aluno: int) -> None:
    plano = admin.post(
        "/nutrition/plans",
        json={"user_id": aluno, "strategy": "maintenance", "tdee_mode": "auto_intervals", "activity_level": "light"},
    )
    assert plano.status_code == 201, plano.text
    assert admin.post(f"/nutrition/plans/{plano.json()['id']}/publish", json={"expected_current_id": None}).status_code == 200
    with raw.cursor() as cur:
        cur.execute("UPDATE nutrition_plans SET effective_from = %s WHERE id = %s", (HOJE - _dt.timedelta(days=10), plano.json()["id"]))
        cur.execute("DELETE FROM nutrition_daily_targets WHERE user_id = %s", (aluno,))
    raw.commit()


def _atividade_intervals(cur, aluno: int, externo: str, esporte: str, kcal) -> int:
    cur.execute(
        "INSERT INTO intervals_activities (user_id, external_id, activity_date, name, sport_type, calories_kcal) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (aluno, externo, HOJE, esporte, esporte, kcal),
    )
    return int(cur.lastrowid)


def _cenario(raw, usuarios, *, minutos_desde_a_sincronizacao: int) -> None:
    aluno = usuarios["aluno"]
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO body_composition (user_id, local_date, weight_kg, source) VALUES (%s, %s, 80.0, 'manual')",
            (aluno, HOJE - _dt.timedelta(days=20)),
        )
        cur.execute(
            "INSERT INTO activities (user_id, local_date, name, session_status, duration_seconds, calories_kcal, calories_source) "
            "VALUES (%s, %s, 'Treino A', 'done', 3600, 428.00, 'intervals')",
            (aluno, HOJE),
        )
        treino_id = int(cur.lastrowid)
        forca = _atividade_intervals(cur, aluno, "f1", "WeightTraining", 428)
        cur.execute(
            "INSERT INTO activity_intervals_link (user_id, activity_id, intervals_activity_id, intervals_external_id, "
            "match_status, match_delta_seconds, calories_kcal) VALUES (%s, %s, %s, 'f1', 'MATCHED', 0, 428)",
            (aluno, treino_id, forca),
        )
        _atividade_intervals(cur, aluno, "f2", "WeightTraining", 300)  # sem vínculo, sem horário/duração: outra atividade, SOMA (bruta)
        _atividade_intervals(cur, aluno, "w1", "Walk", 51)
        _atividade_intervals(cur, aluno, "w2", "Walk", 79)
        _atividade_intervals(cur, aluno, "r1", "VirtualRun", 265)
        _atividade_intervals(cur, aluno, "s1", "Walk", None)  # sem kcal: não soma
        cur.execute(
            "UPDATE intervals_activities SET synced_at = DATE_SUB(CURRENT_TIMESTAMP, INTERVAL %s MINUTE) WHERE user_id = %s",
            (minutos_desde_a_sincronizacao, aluno),
        )
    raw.commit()


def test_dia_sincronizado_soma_forca_e_extras_e_fallback_nao_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        for em_dia, minutos in (("sincronizado", 120), ("parado", 60 * 24 * 5)):
            _limpar(raw)
            _cenario(raw, usuarios, minutos_desde_a_sincronizacao=minutos)
            admin = _client(usuarios["admin"], "admin")
            aluno = _client(usuarios["aluno"], "regular")
            _publicar_automatico(raw, admin, usuarios["aluno"])
            resposta = aluno.get(f"/nutrition/days/{HOJE.isoformat()}")
            assert resposta.status_code == 200, (backend, em_dia, resposta.text)
            corpo = resposta.json()
            treino = corpo["training"]
            if em_dia == "sincronizado":
                assert treino["tdee_basis"] == "auto_synced", (backend, treino)
                # 51 + 79 + 265 + 300 (WeightTraining sem vínculo); as do fixture não têm duração: entram como vieram
                assert treino["extra_activity"]["kcal"] == pytest.approx(695), (backend, treino)
                # a sessão do app (428 kcal, 3600 s) entra LÍQUIDA do repouso da janela: kcal − TMB/1440 × 60
                forca = 428.0 - corpo["target"]["bmr_kcal"] / 1440 * 60
                assert treino["activity_kcal"]["training_kcal"] == pytest.approx(forca, abs=0.1)
                assert treino["activity_kcal"]["extra_kcal"] == 695.0
                assert treino["activity_kcal"]["total_kcal"] == pytest.approx(forca + 695, abs=0.1)
                assert treino["training_block"]["gross_kcal"] == 428.0
                assert corpo["target"]["tdee_kcal"] == pytest.approx(corpo["target"]["bmr_kcal"] * 1.3 + forca + 695, abs=0.2)
            else:
                assert treino["tdee_basis"] == "auto_fallback" and treino["extra_activity"]["kcal"] == 0.0, (backend, treino)
                assert "Intervals sem sincronizar" in treino["tdee_basis_note"]
                assert corpo["target"]["tdee_kcal"] == pytest.approx(corpo["target"]["bmr_kcal"] * 1.725, abs=0.05)  # perfil `active`; tolerância do DECIMAL(.,2) de bmr_kcal e tdee_kcal
            resultados[(backend, em_dia)] = (treino["tdee_basis"], treino["extra_activity"]["kcal"], corpo["target"]["tdee_kcal"])
    por_backend = {}
    for (backend, em_dia), valor in resultados.items():
        por_backend.setdefault(em_dia, set()).add(valor[:2])
    assert all(len(valores) == 1 for valores in por_backend.values()), resultados  # idêntico nos dois backends

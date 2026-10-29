"""Sessoes de treino, lancamentos e fichas SEM DDL, nos dois backends (PyMySQL
e shim RDS Data API + dublê).

Antes, a primeira chamada de cada processo a estas rotas rodava guards de
schema: `workout_sessions._ensure_schema()` (transacao propria, 5 SELECTs em
`information_schema` + ALTER se faltasse coluna) e, dentro da transacao da
rota, `services._ensure_set_hr_columns`, `_ensure_workout_plans_time_column`,
`_ensure_workout_plans_split_day_column` e `_ensure_plan_link_columns`. Agora
o schema vem da migracao 026 (aplicada ao schema de teste pelo fixture
`mysql_schema`, mesmo caminho de producao) e:

(a) nenhuma rota emite CREATE/ALTER/DROP/TRUNCATE/RENAME nem consulta
    `information_schema` - capturado no cursor PyMySQL e nas chamadas
    `ExecuteStatement` do dublê (`tests/support/sql_capture.py`);
(b) cada rota grava/le o que deveria (conferido por conexao neutra) e nao
    deixa transacao Data API pendurada.
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

import pytest

# Importados no topo: `use_dataapi` so troca o backend de modulos `app.*` ja importados.
from app import async_jobs, lambda_plan_generator, services, workout_plan_ai, workout_plans, workout_sessions
from app.models import ActivityIn, ExerciseLogUpdateIn, WorkoutPlanGenerateIn, WorkoutPlanIn
from app.security import Identity
from app.workout_sessions import SessionExerciseIn, SessionFinishIn, SessionSetIn, SessionStartIn
from tests.integration.dataapi.test_recovery_paridade import (  # noqa: F401 - fixtures reutilizadas
    FROZEN_NOW,
    frozen_clock,
    training_history,
)
from tests.support.sql_capture import ddl, recorded_sql
from tests.support.template_versions import versao_v1

SEED_TABLES = (
    "exercise_sets",
    "exercises",
    "activities",
    "workout_plan_exercises",
    "workout_plans",
    "exercise_aliases",
    "exercise_legacy_id_map",
    "exercise_muscle_contributions",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "users",
)
STARTED_AT = _dt.datetime(2026, 9, 14, 7, 30, 0)

# Tabelas do Modelo de Treino e da academia usadas pelos testes de geracao
# (PR-18). Ficam FORA de SEED_TABLES porque as listas de TRUNCATE dos dois
# seeds (aqui e em `training_history`) nao as cobrem; limpar em ordem
# filho -> pai, antes e depois, evita deixar linha orfa que quebre o TRUNCATE
# de outro teste.
MODEL_TABLES = (
    "workout_plan_template_slot_items",
    "workout_plan_template_slots",
    "workout_plan_template_assignments",
    "workout_plan_templates",
)


def _purge_modelos(raw) -> None:
    with raw.cursor() as cur:
        for table in MODEL_TABLES:
            cur.execute(f"DELETE FROM `{table}`")


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="session")


@pytest.fixture
def env(raw):
    _purge_modelos(raw)
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in SEED_TABLES:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('ssf-ddl', 'x', 'admin')")
        user_id = cur.lastrowid
        for exercise_id, name in ((1001, "Supino reto"), (1003, "Remada baixa")):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, name, name),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))
        cur.execute(
            "INSERT INTO workout_plans (user_id, name, plan_type, split_day, plan_time) VALUES (%s, 'Ficha A', 'abc', 'A', '09:00:00')",
            (user_id,),
        )
        plan_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, mapping_id, sets_count) "
            "VALUES (%s, 1, 'Supino reto', 1001, 3)",
            (plan_id,),
        )
        wpe_id = cur.lastrowid
        # PR-18: `POST /workout-plans/generate` exige um Modelo de Treino
        # atribuido ao usuario - sem ele a rota responde 422 antes de criar o
        # job. O modelo minimo abaixo (um slot, sem itens de equipamento) e o
        # bastante para a rota resolver modelo + slot e seguir para o 202; a
        # geracao em si roda na Lambda, exercitada no teste do handler.
        cur.execute(
            "INSERT INTO workout_plan_templates (name, is_active, created_by_user_id) VALUES ('Modelo SSF', 1, %s)",
            (user_id,),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (template_id, user_id, user_id),
        )
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'A', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
    yield {"raw": raw, "user": user_id, "plan": plan_id, "wpe": wpe_id, "template": template_id}
    _purge_modelos(raw)


def _one(env, sql, params):
    with env["raw"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _live_session(env, with_exercise=False):
    with env["raw"].cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status, workout_plan_id) "
            "VALUES (%s, %s, %s, 'Sessao', 'strength_training', 'in_progress', %s)",
            (env["user"], STARTED_AT.date(), STARTED_AT, env["plan"]),
        )
        session_id = cur.lastrowid
        exercise_id = None
        if with_exercise:
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, workout_plan_exercise_id, sets_count) "
                "VALUES (%s, 1, 'Supino reto', 1001, %s, 1)",
                (session_id, env["wpe"]),
            )
            exercise_id = cur.lastrowid
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe) VALUES (%s, 1, 10, '60.00', '8.00')",
                (exercise_id,),
            )
    return session_id, exercise_id


def _start(env, monkeypatch):
    out = workout_sessions.start_session(
        SessionStartIn(workout_plan_id=env["plan"], started_at=STARTED_AT), identity=_identity(env["user"])
    )
    row = _one(env, "SELECT session_status, workout_plan_id FROM activities WHERE id = %s", (out["session_id"],))
    assert row == {"session_status": "in_progress", "workout_plan_id": env["plan"]}


def _add_exercise(env, monkeypatch):
    session_id, _ = _live_session(env)
    out = workout_sessions.add_session_exercise(
        session_id,
        SessionExerciseIn(original_name="Supino reto", mapping_id=1001, workout_plan_exercise_id=env["wpe"]),
        identity=_identity(env["user"]),
    )
    row = _one(env, "SELECT workout_plan_exercise_id FROM exercises WHERE id = %s", (out["exercise_id"],))
    assert row["workout_plan_exercise_id"] == env["wpe"]


def _add_set(env, monkeypatch):
    session_id, exercise_id = _live_session(env, with_exercise=True)
    workout_sessions.add_session_set(
        session_id,
        exercise_id,
        SessionSetIn(reps=8, weight_kg=Decimal("62.5"), rpe=Decimal("9"), hr_avg_bpm=Decimal("131.5"), hr_max_bpm=Decimal("150"), muscle_pain=3, joint_pain=True),
        identity=_identity(env["user"]),
    )
    row = _one(
        env,
        "SELECT hr_avg_bpm, hr_max_bpm, muscle_pain, joint_pain FROM exercise_sets WHERE exercise_id = %s AND set_number = 2",
        (exercise_id,),
    )
    assert row == {"hr_avg_bpm": Decimal("131.50"), "hr_max_bpm": Decimal("150.00"), "muscle_pain": 3, "joint_pain": 1}


def _add_exercise_client_key(env, monkeypatch):
    """Evolução ZEPP PR C (087): a 2ª chamada com a mesma chave devolve o
    mesmo exercício com `duplicate: true` e não grava outro."""
    session_id, _ = _live_session(env)
    payload = SessionExerciseIn(original_name="Supino reto", mapping_id=1001, client_key="zepp-ex-1")
    primeiro = workout_sessions.add_session_exercise(session_id, payload, identity=_identity(env["user"]))
    segundo = workout_sessions.add_session_exercise(session_id, payload, identity=_identity(env["user"]))
    assert (primeiro["duplicate"], segundo["duplicate"]) == (False, True)
    assert segundo["exercise_id"] == primeiro["exercise_id"]
    row = _one(env, "SELECT COUNT(*) AS n, MAX(client_key) AS k FROM exercises WHERE activity_id = %s", (session_id,))
    assert (row["n"], row["k"]) == (1, "zepp-ex-1")


def _add_set_client_key(env, monkeypatch):
    """Evolução ZEPP PR C (087): série repetida pela chave devolve a mesma
    resposta (inclusive `next_set_load`) e não grava outra série."""
    session_id, exercise_id = _live_session(env, with_exercise=True)
    payload = SessionSetIn(reps=17, weight_kg=Decimal("60"), rpe=Decimal("8"), client_key="zepp-set-2")
    primeira = workout_sessions.add_session_set(session_id, exercise_id, payload, identity=_identity(env["user"]))
    segunda = workout_sessions.add_session_set(session_id, exercise_id, payload, identity=_identity(env["user"]))
    assert (primeira["duplicate"], segunda["duplicate"]) == (False, True)
    sem_flag = lambda r: {k: v for k, v in r.items() if k != "duplicate"}  # noqa: E731
    assert sem_flag(segunda) == sem_flag(primeira)
    row = _one(env, "SELECT COUNT(*) AS n FROM exercise_sets WHERE exercise_id = %s", (exercise_id,))
    assert row["n"] == 2  # a série 1 do `_live_session` + a desta chave


def _finish(env, monkeypatch):
    session_id, _ = _live_session(env, with_exercise=True)
    out = workout_sessions.finish_session(
        session_id, SessionFinishIn(ended_at=STARTED_AT + _dt.timedelta(minutes=50)), identity=_identity(env["user"])
    )
    assert out["session_status"] == "done"
    assert _one(env, "SELECT session_status FROM activities WHERE id = %s", (session_id,))["session_status"] == "done"


def _post_activity(env, monkeypatch):
    payload = ActivityIn(
        external_id="ssf-ddl-1",
        local_date=STARTED_AT.date(),
        name="Importado",
        exercises=[
            {"sequence": 1, "original_name": "Supino reto", "mapping_id": 1001,
             "sets": [{"set_number": 1, "reps": 10, "weight_kg": "60", "hr_avg_bpm": "120.5", "hr_max_bpm": "140"}]}
        ],
    ).model_dump()
    services.upsert_activity(payload, user_id=env["user"])
    row = _one(
        env,
        "SELECT s.hr_avg_bpm FROM exercise_sets s JOIN exercises e ON e.id = s.exercise_id "
        "JOIN activities a ON a.id = e.activity_id WHERE a.external_id = 'ssf-ddl-1'",
        (),
    )
    assert row["hr_avg_bpm"] == Decimal("120.50")


def _put_exercise_log(env, monkeypatch):
    session_id, exercise_id = _live_session(env, with_exercise=True)
    payload = ExerciseLogUpdateIn(
        local_date=STARTED_AT.date(),
        original_name="Supino reto",
        mapping_id=1001,
        workout_plan_exercise_id=None,
        sets=[{"set_number": 1, "reps": 12, "weight_kg": "55", "hr_avg_bpm": "118", "hr_max_bpm": "133"}],
    )
    data = payload.model_dump()
    data["workout_plan_exercise_id_provided"] = "workout_plan_exercise_id" in payload.model_fields_set
    assert services.update_exercise_log(exercise_id, data, user_id=env["user"])
    row = _one(env, "SELECT e.workout_plan_exercise_id, s.hr_avg_bpm FROM exercises e JOIN exercise_sets s ON s.exercise_id = e.id WHERE e.id = %s", (exercise_id,))
    assert row == {"workout_plan_exercise_id": None, "hr_avg_bpm": Decimal("118.00")}


def _list_plans(env, monkeypatch):
    items = workout_plans.list_plans(status=None, limit=20, has_advanced_technique=None, identity=_identity(env["user"]))["items"]
    assert [(i["id"], i["plan_time"], i["split_day"]) for i in items] == [(env["plan"], "09:00", "A")]


def _get_plan(env, monkeypatch):
    plan = workout_plans.get_plan(env["plan"], identity=_identity(env["user"]))
    assert plan["split_day"] == "A" and plan["plan_time"] == "09:00"
    assert [(e["workout_plan_exercise_id"], e["mapping_id"]) for e in plan["exercises"]] == [(env["wpe"], 1001)]


def _create_plan(env, monkeypatch):
    payload = WorkoutPlanIn(
        name="Ficha B", plan_type="abc", split_day="B", plan_time=_dt.time(18, 30),
        exercises=[{"sequence": 1, "exercise_name": "Remada baixa", "mapping_id": 1003, "sets_count": 4}],
    )
    plan = workout_plans.create_plan(payload, identity=_identity(env["user"]))
    assert (plan["split_day"], plan["plan_time"]) == ("B", "18:30")
    assert [e["mapping_id"] for e in plan["exercises"]] == [1003]


def _update_plan(env, monkeypatch):
    payload = WorkoutPlanIn(
        name="Ficha A editada", plan_type="abc", split_day="C", plan_time=_dt.time(7, 0),
        exercises=[{"sequence": 1, "exercise_name": "Supino reto", "mapping_id": 1001, "sets_count": 5}],
    )
    plan = workout_plans.update_plan(env["plan"], payload, identity=_identity(env["user"]))
    assert (plan["name"], plan["split_day"], plan["plan_time"]) == ("Ficha A editada", "C", "07:00")
    # vinculo preservado: mesma sequence -> mesmo id de workout_plan_exercises
    assert [e["workout_plan_exercise_id"] for e in plan["exercises"]] == [env["wpe"]]


def _generate_202(env, monkeypatch):
    calls = []
    monkeypatch.setattr(async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: "job-ssf")
    monkeypatch.setattr(async_jobs, "invoke_async", lambda fn_env, payload: calls.append((fn_env, payload)))
    response = workout_plans.generate_plan(
        WorkoutPlanGenerateIn(template_id=env["template"]), identity=_identity(env["user"])
    )
    assert response.status_code == 202
    assert json.loads(response.body)["job_id"] == "job-ssf"
    assert calls and calls[0][0] == "PLAN_GENERATOR_FUNCTION_NAME"


ROUTES = {
    "POST /workout-sessions/start": _start,
    "POST /workout-sessions/{id}/exercises": _add_exercise,
    "POST /workout-sessions/{id}/exercises/{ex}/sets": _add_set,
    "POST /workout-sessions/{id}/exercises (client_key repetida)": _add_exercise_client_key,
    "POST /workout-sessions/{id}/exercises/{ex}/sets (client_key repetida)": _add_set_client_key,
    "POST /workout-sessions/{id}/finish": _finish,
    "POST /activities (com series)": _post_activity,
    "PUT /exercises/logs/{id}": _put_exercise_log,
    "GET /workout-plans": _list_plans,
    "GET /workout-plans/{id}": _get_plan,
    "POST /workout-plans": _create_plan,
    "PUT /workout-plans/{id}": _update_plan,
    "POST /workout-plans/generate": _generate_202,
}


def test_instrumentacao_captura_ddl(env, db_backend, monkeypatch, fake_rds):
    """Garante que a captura abaixo nao e vazia por construcao."""
    import app.db as appdb

    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        with appdb.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS workout_plans (id BIGINT PRIMARY KEY)")
    assert len(ddl(statements)) == 1, statements


def test_087_converge_e_unique_por_pai(env):
    """A 087 aplicada pelo schema da suíte converge (0 statements lendo o
    `information_schema` real) e a UNIQUE vale por pai: a mesma chave na mesma
    sessão/exercício é recusada pelo MySQL; em outro pai, e NULL repetido, passam."""
    import pymysql

    from app import migrate as m

    modulo = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "087_client_keys_sessao_ao_vivo.py").__globals__
    assert modulo["plan"](m.MigrationDB(env["raw"])) == []
    session_a, exercise_a = _live_session(env, with_exercise=True)
    session_b, exercise_b = _live_session(env, with_exercise=True)
    with env["raw"].cursor() as cur:
        cur.execute("UPDATE exercises SET client_key = 'k' WHERE id IN (%s, %s)", (exercise_a, exercise_b))
        with pytest.raises(pymysql.err.IntegrityError):
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, client_key) VALUES (%s, 2, 'x', 'k')",
                (session_a,),
            )
        for exercise_id in (exercise_a, exercise_b):
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, client_key) VALUES (%s, 2, 's')", (exercise_id,)
            )
        with pytest.raises(pymysql.err.IntegrityError):
            cur.execute("INSERT INTO exercise_sets (exercise_id, set_number, client_key) VALUES (%s, 3, 's')", (exercise_a,))
        cur.execute("INSERT INTO exercise_sets (exercise_id, set_number) VALUES (%s, 4), (%s, 5)", (exercise_a, exercise_a))


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_rota_de_sessao_e_ficha_nao_emite_ddl(env, db_backend, monkeypatch, fake_rds, route):
    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        ROUTES[route](env, monkeypatch)

    # PR-18: a rota de geracao deixou de ser "so DynamoDB" - ela LE o modelo
    # e o slot para poder responder 404/422 na hora, em vez de deixar o erro
    # virar job `failed`. Continua sem DDL, como todas as outras.
    assert statements, "nenhum statement capturado - instrumentacao quebrada"
    assert ddl(statements) == []
    assert [s for s in statements if "information_schema" in s.lower()] == []
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


@pytest.fixture
def modelo_do_usuario(raw, training_history):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Modelo de Treino atribuido ao usuario, com um slot de grupo muscular.

    Desde o PR-18 e o unico jeito de gerar ficha: o payload da Lambda leva
    `template_id`, e o motor (template v1, com IA dublada) monta a ficha a
    partir do slot."""
    _purge_modelos(raw)
    user_id = training_history["user"]
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, is_active, created_by_user_id) "
            "VALUES ('Modelo SSF gerar', 1, %s)",
            (user_id,),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (template_id, user_id, user_id),
        )
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'A', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
        slot_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slot_items "
            "(slot_id, item_order, item_kind, muscle_group_id, sets_count) VALUES (%s, 1, 'muscle_group', %s, 3)",
            # Quadriceps: unico grupo do seed sem dor recente - Peitoral e
            # Costas voltam NO_DIRECT_WORK e o motor recusaria a ficha.
            (slot_id, training_history["Quadríceps"]),
        )
    yield template_id
    _purge_modelos(raw)


def test_gerar_ficha_handler_da_lambda_nao_emite_ddl(
    training_history, modelo_do_usuario, db_backend, monkeypatch, fake_rds  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    """Lambda `musclefit-plan-generator` (gerar ficha): desde o PR-18 a
    geracao SEMPRE parte de um Modelo de Treino atribuido - com leituras
    reais do banco (historico de series, recuperacao muscular, fichas
    recentes; `training_history`, mesmo seed da paridade de recuperacao); so
    academias/catalogo e OpenAI sao dublados."""
    catalog = [
        {"id": 1001, "exercise_name": "Supino reto", "amazfit_name": "Supino reto", "muscle_primary": "Peitoral"},
        {"id": 1003, "exercise_name": "Remada baixa", "amazfit_name": "Remada baixa", "muscle_primary": "Costas"},
        {"id": 1004, "exercise_name": "Agachamento", "amazfit_name": "Agachamento", "muscle_primary": "Quadríceps"},
    ]
    monkeypatch.setattr(workout_plan_ai, "_load_gyms", lambda: [])
    monkeypatch.setattr(workout_plan_ai, "_load_exercise_catalog", lambda: catalog)
    generated = {
        "name": "Ficha gerada",
        "exercises": [
            {"sequence": 1, "exercise_name": "Agachamento", "muscle_primary": "Quadríceps", "sets_count": 3,
             "reps_target": "8-10", "rpe_target": "8", "rest_seconds": 60}
        ],
    }
    monkeypatch.setattr(workout_plan_ai, "call_openai", lambda prompt, *_a, **_k: json.dumps(generated))
    done, failed = [], []
    monkeypatch.setattr(async_jobs, "mark_done", lambda job_id, result: done.append((job_id, result)))
    monkeypatch.setattr(async_jobs, "mark_failed", lambda job_id, error: failed.append((job_id, error)))
    monkeypatch.setattr(async_jobs, "claim_job", lambda job_id: True)  # M81
    plan_date = (FROZEN_NOW + _dt.timedelta(days=3)).date().isoformat()
    template_id = modelo_do_usuario

    with recorded_sql(db_backend, monkeypatch, fake_rds) as statements:
        lambda_plan_generator.handler(
            {
                "job_id": "job-gen",
                "payload": {"template_id": template_id, "plan_date": plan_date},
                "user_id": training_history["user"],
            },
            None,
        )

    assert failed == [] and [job for job, _ in done] == ["job-gen"], failed
    assert done[0][1]["exercises"], done[0][1]
    assert statements, "nenhum statement capturado - instrumentacao quebrada"
    assert ddl(statements) == []
    assert [s for s in statements if "information_schema" in s.lower()] == []
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"

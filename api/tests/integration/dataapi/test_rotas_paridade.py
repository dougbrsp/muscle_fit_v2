"""Rotas que QUEBRAVAM SO EM PRODUCAO (Data API) por tipo de valor lido do
banco, executadas nos dois backends com os mesmos dados -> mesmo resultado.

Evidencia de producao (CloudWatch, `/aws/lambda/musclefit-engines` e
`musclefit-api-core`, 2026-09-14):

- `GET /analytics/training-evolution`: 500 em 100% das chamadas desde
  2026-09-11 - `TypeError: unsupported operand type(s) for +=: 'float' and
  'str'` em `volume_service.get_rolling_weekly_trend_by_muscle_group`
  (`reps * weight_kg` com DECIMAL chegando como `str` -> repeticao de string).
- `POST /workout-sessions/{id}/finish`: 12 erros / 0 sucessos em 14 dias -
  `TypeError: unsupported operand type(s) for -: 'datetime.datetime' and 'str'`
  (`started_at` DATETIME chegando como `str`).
- `POST /workout-plans`: `Incorrect time value: '"09:00:00"' for column
  'plan_time'` ate 2026-09-12 17:08 UTC; o bind de `datetime.time` foi
  corrigido no #40 (17:48 UTC) - aqui fica a regressao do ciclo completo
  (gravar, ler, regravar o `timedelta` lido).

Os demais endpoints de analytics/dashboard mais usados que fazem aritmetica
com DECIMAL/datas entram na mesma comparacao.
"""

from __future__ import annotations

import copy
import datetime as _dt
from decimal import Decimal

import pytest

# Importados no topo de proposito: `use_dataapi` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import analytics, dashboard_modules, services, workout_sessions
from app.intelligence.dashboard_aggregator_service import DashboardAggregatorService
from app.intelligence.dashboard_intelligence_layer import DashboardIntelligenceLayer
from app.security import Identity
from app.workout_sessions import SessionFinishIn
from tests.integration.dataapi.test_recovery_paridade import (  # noqa: F401 - fixtures reutilizadas
    FROZEN_NOW,
    frozen_clock,
    training_history,
)
from tests.support.dataapi_backend import BACKENDS, use_backend


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="admin", source="master_token")


@pytest.fixture
def strength_history(training_history, raw):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Historico de `training_history` marcado como musculacao (as queries de
    tendencia/evolucao filtram `activity_type LIKE '%strength%'`), com DECIMAL
    em weight_kg/rpe/stimulus_weight e DATE/DATETIME reais."""
    from tests.support.grupo_b_schema import ensure_grupo_b_tables

    # intervals_activities/intervals_wellness vem da migracao 027 (mesmo
    # caminho de producao); a correlacao do training-evolution as le.
    ensure_grupo_b_tables(raw)
    with raw.cursor() as cur:
        cur.execute("UPDATE activities SET activity_type = 'strength_training'")
    return training_history


def _run_in_both(monkeypatch, fake_rds, mysql_schema, fn):
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        results[backend] = fn()
    assert fake_rds.calls_of("ExecuteStatement"), "o caminho data-api nao passou pelo dublê"
    return results


def _normalized(value):
    """Remove campos que dependem do relogio real da chamada (nao do banco)."""
    value = copy.deepcopy(value)
    volatile = {"generated_at", "computed_at", "now"}

    def walk(node):
        if isinstance(node, dict):
            for key in list(node):
                if key in volatile:
                    node.pop(key)
                else:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return value


# ---------------------------------------------------------------------------
# GET /analytics/training-evolution (engines) - 500 em producao
# ---------------------------------------------------------------------------


def test_training_evolution_nao_quebra_e_e_identico_nos_dois_backends(
    strength_history, monkeypatch, fake_rds, mysql_schema
):
    user_id = strength_history["user"]
    results = _run_in_both(
        monkeypatch,
        fake_rds,
        mysql_schema,
        lambda: DashboardIntelligenceLayer().get_training_evolution(days=90, user_id=user_id),
    )
    assert _normalized(results["data-api"]) == _normalized(results["pymysql"])

    trend = results["data-api"]["weekly_trend_by_muscle_group"]
    assert trend, "sem series: o teste nao exercitaria a soma de volume que quebrava"
    volume = sum(item.get("volume_kg") or 0 for item in _flatten_trend(trend))
    assert volume > 0


def _flatten_trend(trend):
    if isinstance(trend, dict):
        items = []
        for value in trend.values():
            items.extend(_flatten_trend(value))
        return items
    if isinstance(trend, list):
        items = []
        for value in trend:
            items.extend(_flatten_trend(value) if not (isinstance(value, dict) and "volume_kg" in value) else [value])
        return items
    return []


def test_training_evolution_rolling_trend_recebe_decimal_do_banco_nos_dois_backends(
    strength_history, monkeypatch, fake_rds, mysql_schema
):
    """Causa raiz no nivel do valor: `IntelligenceServiceBase.fetch_all` passa
    cada linha por `_jsonable` (Decimal -> float, date -> isoformat). Antes, no
    Data API, DECIMAL chegava como str e `_jsonable` o deixava passar, entao
    `reps * weight_kg` virava repeticao de string. Agora os dois backends
    entregam float/str-ISO identicos."""
    user_id = strength_history["user"]
    captured = {}

    def run():
        service = DashboardAggregatorService().volume_service
        original = service.fetch_all
        rows = []

        def spy(sql, params=()):
            result = original(sql, params)
            if "bucket_index" in sql:
                rows.extend(result)
            return result

        service.fetch_all = spy
        service.get_rolling_weekly_trend_by_muscle_group(28, user_id=user_id)
        return rows

    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        captured[backend] = run()
    for backend, rows in captured.items():
        with_sets = [row for row in rows if row.get("set_id") is not None]
        assert with_sets, backend
        assert {type(row["weight_kg"]) for row in with_sets} == {float}, backend
        assert {type(row["rpe"]) for row in with_sets if row["rpe"] is not None} == {float}, backend
        assert all(_dt.date.fromisoformat(row["local_date"]) for row in with_sets), backend
    assert captured["data-api"] == captured["pymysql"]


# ---------------------------------------------------------------------------
# Analytics / dashboards mais usados (aritmetica com DECIMAL e datas)
# ---------------------------------------------------------------------------

ROUTES = {
    "summary": lambda uid: analytics.analytics_summary(days=90, identity=_identity(uid)),
    # M98 C4: agregado por (periodo, tipo, nome) no SQL, classe de cardio em Python.
    "cardio": lambda uid: analytics.cardio_summary(period="week", days=365, identity=_identity(uid)),
    "training-trend": lambda uid: analytics.training_trend(period="week", days=365, identity=_identity(uid)),
    "rpe-distribution": lambda uid: analytics.rpe_distribution(days=90, identity=_identity(uid)),
    "dashboard-recovery": lambda uid: analytics.dashboard_recovery(days=14, identity=_identity(uid)),
    "training-volume": lambda uid: dashboard_modules.training_volume(days=30, identity=_identity(uid)),
    "muscle-recovery": lambda uid: dashboard_modules.muscle_recovery(days=14, identity=_identity(uid)),
}


@pytest.mark.parametrize("route", list(ROUTES))
def test_rota_de_analytics_identica_nos_dois_backends(strength_history, monkeypatch, fake_rds, mysql_schema, route):
    user_id = strength_history["user"]
    results = _run_in_both(monkeypatch, fake_rds, mysql_schema, lambda: ROUTES[route](user_id))
    assert _normalized(results["data-api"]) == _normalized(results["pymysql"])


# ---------------------------------------------------------------------------
# /volume-grupamentos: grupo pai e macro-grupo por linha (2026-10-17, mapa §50)
# ---------------------------------------------------------------------------

HIERARCHY_ROUTES = {
    "muscle-groups-recent-exercise-volume": lambda uid: analytics.muscle_groups_recent_exercise_volume(
        limit=400, date_from=None, date_to=None, identity=_identity(uid)
    ),
    "muscle-groups-equivalent-sets-by-activity": lambda uid: analytics.muscle_groups_equivalent_sets_by_activity(
        limit=400, identity=_identity(uid)
    ),
    "training-volume": lambda uid: dashboard_modules.training_volume(days=30, identity=_identity(uid)),
}


@pytest.fixture
def grouped_history(strength_history, raw):
    """Cadastro com pai e macro-grupo PREENCHIDOS, para o LEFT JOIN de
    `methodology_volume_service._GROUP_HIERARCHY_SQL` devolver nome e
    `sort_order` (INT) nos dois backends. As classes sao proprias do teste (a
    tabela e compartilhada com o CRUD) e saem no fim."""
    with raw.cursor() as cur:
        cur.execute("INSERT INTO muscle_volume_classes (name, sort_order) VALUES ('Paridade grandes', 1)")
        grandes = cur.lastrowid
        cur.execute("INSERT INTO muscle_volume_classes (name, sort_order) VALUES ('Paridade pequenos', 2)")
        pequenos = cur.lastrowid
        cur.execute("UPDATE muscle_groups SET volume_class_id = %s WHERE id IN (%s, %s)",
                    (grandes, strength_history["Peitoral"], strength_history["Costas"]))
        cur.execute("UPDATE muscle_groups SET volume_class_id = %s WHERE id = %s",
                    (pequenos, strength_history["Bíceps"]))
        # Tríceps como fatia do Peitoral: herda o macro-grupo do pai.
        cur.execute("UPDATE muscle_groups SET methodology_parent_group_id = %s WHERE id = %s",
                    (strength_history["Peitoral"], strength_history["Tríceps"]))
    try:
        yield {**strength_history, "grandes": grandes, "pequenos": pequenos}
    finally:
        with raw.cursor() as cur:
            cur.execute("UPDATE muscle_groups SET volume_class_id = NULL, methodology_parent_group_id = NULL")
            cur.execute("DELETE FROM muscle_volume_classes WHERE id IN (%s, %s)", (grandes, pequenos))


def _hierarchy_rows(result):
    return result["trend"]["items"] if "trend" in result else result["items"]


@pytest.mark.parametrize("route", list(HIERARCHY_ROUTES))
def test_hierarquia_por_linha_identica_nos_dois_backends(grouped_history, monkeypatch, fake_rds, mysql_schema, route):
    user_id = grouped_history["user"]
    results = _run_in_both(monkeypatch, fake_rds, mysql_schema, lambda: HIERARCHY_ROUTES[route](user_id))
    assert _normalized(results["data-api"]) == _normalized(results["pymysql"])

    by_group = {row["muscle_group"]: row for row in _hierarchy_rows(results["data-api"])}
    assert by_group, route
    if "Tríceps" in by_group:
        tricep = by_group["Tríceps"]
        assert (tricep["parent_group_id"], tricep["parent_group_name"]) == (grouped_history["Peitoral"], "Peitoral")
        assert (tricep["volume_class_id"], tricep["volume_class_name"]) == (grouped_history["grandes"], "Paridade grandes")
        assert isinstance(tricep["volume_class_order"], int)
    if "Bíceps" in by_group:
        assert by_group["Bíceps"]["volume_class_name"] == "Paridade pequenos"
    if "Quadríceps" in by_group:
        # Sem classe: o balde do dono.
        from app.intelligence.methodology_volume import VOLUME_CLASS_UNCLASSIFIED_LABEL

        assert by_group["Quadríceps"]["volume_class_id"] is None
        assert by_group["Quadríceps"]["volume_class_name"] == VOLUME_CLASS_UNCLASSIFIED_LABEL


@pytest.fixture
def slot_history(grouped_history, raw):
    """Sessoes do historico ligadas a fichas de um Modelo com dois slots (065:
    slot por versao), alternando 1, 2, 1, 2... da mais antiga para a mais
    recente, e a ultima SEM ficha (treino livre)."""
    from tests.support.template_versions import versao_v1

    user_id = grouped_history["user"]
    with raw.cursor() as cur:
        cur.execute("INSERT INTO workout_plan_templates (name, created_by_user_id) VALUES ('Paridade slots', %s)", (user_id,))
        template_id = cur.lastrowid
        cur.execute("DELETE FROM workout_plan_template_slots WHERE template_id = %s", (template_id,))
        cur.execute("DELETE FROM workout_plan_template_versions WHERE template_id = %s", (template_id,))
        version_id = versao_v1(cur, template_id)
        for sequence, name in ((1, "Dia A"), (2, "Dia B")):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name) VALUES (%s, %s, %s, %s)",
                (version_id, template_id, sequence, name),
            )
        cur.execute("SELECT id FROM activities WHERE user_id = %s ORDER BY local_date ASC, id ASC", (user_id,))
        activity_ids = [row["id"] if isinstance(row, dict) else row[0] for row in cur.fetchall()]
        for index, activity_id in enumerate(activity_ids[:-1]):
            cur.execute(
                "INSERT INTO workout_plans (user_id, name, template_id, template_slot_sequence, template_version_id) "
                "VALUES (%s, 'Ficha slot', %s, %s, %s)",
                (user_id, template_id, 1 + index % 2, version_id),
            )
            cur.execute("UPDATE activities SET workout_plan_id = %s WHERE id = %s", (cur.lastrowid, activity_id))
        if activity_ids:
            cur.execute("UPDATE activities SET workout_plan_id = NULL WHERE id = %s", (activity_ids[-1],))
    try:
        yield {**grouped_history, "template": template_id}
    finally:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM workout_plan_template_slots WHERE template_id = %s", (template_id,))
            cur.execute("DELETE FROM workout_plan_template_versions WHERE template_id = %s", (template_id,))


def test_comparativo_por_slot_identico_nos_dois_backends(slot_history, monkeypatch, fake_rds, mysql_schema):
    """`GET /analytics/muscle-groups-slot-comparison`: o slot de cada sessao
    (JOIN da ficha com o slot da versao) chega igual pela Data API e pelo
    PyMySQL, com nome do slot e chave por `template_id` + `sequence`."""
    user_id = slot_history["user"]
    results = _run_in_both(
        monkeypatch,
        fake_rds,
        mysql_schema,
        lambda: analytics.muscle_groups_slot_comparison(limit=400, identity=_identity(user_id)),
    )
    assert _normalized(results["data-api"]) == _normalized(results["pymysql"])

    rows = results["data-api"]["levels"]["muscle_group"]["volume"]
    assert rows
    for row in rows:
        if row["slot"] is None:
            assert row["comparison_status"] == "no_slot"
            assert [row["by_period"][k] for k in ("t1", "t2", "t3")] == [None, None, None]
        else:
            assert row["slot"]["template_id"] == slot_history["template"]
            assert row["slot"]["slot_name"] in {"Dia A", "Dia B"}


# ---------------------------------------------------------------------------
# POST /workout-sessions/{id}/finish (api-core) - 500 em producao
# ---------------------------------------------------------------------------

STARTED_AT = _dt.datetime(2026, 9, 14, 7, 30, 15)


def _live_session(raw, user_id, plan_id=None):
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status, workout_plan_id) "
            "VALUES (%s, %s, %s, 'Sessao ao vivo', 'strength_training', 'in_progress', %s)",
            (user_id, STARTED_AT.date(), STARTED_AT, plan_id),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, 1, 'Supino reto', 1001, 2)",
            (activity_id,),
        )
        exercise_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe) VALUES (%s, 1, 10, '60.00', '7.50'), (%s, 2, 8, '62.50', '8.50')",
            (exercise_id, exercise_id),
        )
    return activity_id


def _activity_state(raw, activity_id):
    with raw.cursor() as cur:
        cur.execute(
            "SELECT ended_at, duration_seconds, calories_kcal, session_status, avg_rpe FROM activities WHERE id = %s",
            (activity_id,),
        )
        return cur.fetchone()


class _FrozenNow(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2026, 9, 14, 8, 45, 20, 750000)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="sessao-ao-vivo-ended_at-agora"),
        pytest.param({"ended_at": _dt.datetime(2026, 9, 14, 8, 40, 0), "calories_kcal": Decimal("321.5")}, id="ended_at-do-cliente"),
    ],
)
def test_finish_session_responde_e_calcula_igual_nos_dois_backends(
    training_history, raw, monkeypatch, fake_rds, mysql_schema, payload  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    user_id = training_history["user"]
    monkeypatch.setattr(workout_sessions, "datetime", _FrozenNow)
    # Sessao ao vivo sem `ended_at`: o "agora" vem de `user_now` (hora de
    # parede do usuario, PR #305), nao mais de `datetime.now()`. `_FrozenNow`
    # ja e a hora de parede congelada.
    monkeypatch.setattr(workout_sessions, "user_now", lambda tz, now_utc=None: _FrozenNow.now())
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        activity_id = _live_session(raw, user_id, plan_id=training_history["plan"])
        response = workout_sessions.finish_session(activity_id, SessionFinishIn(**payload), identity=_identity(user_id))
        response.pop("session_id")
        results[backend] = {"response": response, "state": _activity_state(raw, activity_id)}
    assert fake_rds.calls_of("ExecuteStatement")

    assert results["data-api"] == results["pymysql"]
    response = results["data-api"]["response"]
    ended_at = payload.get("ended_at") or _FrozenNow.now()
    assert response["duration_seconds"] == int((ended_at - STARTED_AT).total_seconds())
    assert response["workout_plan_marked_done"] is True
    assert response["exercises"] == 1
    assert results["data-api"]["state"]["session_status"] == "done"


# ---------------------------------------------------------------------------
# rowcount == 0 -> 404: semantica "linhas encontradas" nos dois backends
# ---------------------------------------------------------------------------


def test_desativar_usuario_ja_inativo_nao_vira_404_em_nenhum_backend(training_history, raw, monkeypatch, fake_rds, mysql_schema):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """`DELETE /users/{id}` faz `UPDATE users SET is_active = 0` e responde 404
    se `rowcount == 0`. Na Aurora (Data API) rowcount conta linhas ENCONTRADAS,
    entao desativar de novo responde OK; antes, em dev (PyMySQL sem FOUND_ROWS),
    a segunda chamada respondia 404 so ali."""
    from fastapi import HTTPException

    from app import users

    admin = _identity(training_history["user"])
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("UPDATE users SET is_active = 1 WHERE id = %s", (training_history["other_user"],))
        first = users.deactivate_user(training_history["other_user"], identity=admin)
        second = users.deactivate_user(training_history["other_user"], identity=admin)
        with pytest.raises(HTTPException) as missing:
            users.deactivate_user(999_999, identity=admin)
        results[backend] = (first, second, missing.value.status_code)
    assert results["data-api"] == results["pymysql"]
    assert results["data-api"][0] == results["data-api"][1]
    assert results["data-api"][2] == 404


# ---------------------------------------------------------------------------
# POST/PUT /workout-plans com plan_time (TIME) - ciclo gravar/ler/regravar
# ---------------------------------------------------------------------------


def test_workout_plan_com_plan_time_grava_le_e_regrava_igual_nos_dois_backends(
    training_history, raw, monkeypatch, fake_rds, mysql_schema  # noqa: F811 - fixture do pytest importada (falso positivo)
):
    user_id = training_history["user"]
    results = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        # `WorkoutPlanIn.model_dump()` entrega `datetime.time` (tipo real do router).
        created = services.create_workout_plan(
            {"name": f"Ficha {backend}", "plan_date": _dt.date(2026, 9, 15), "plan_time": _dt.time(9, 0), "exercises": []},
            user_id=user_id,
        )
        with raw.cursor() as cur:
            cur.execute("SELECT plan_time FROM workout_plans WHERE id = %s", (created["id"],))
            stored_after_create = cur.fetchone()["plan_time"]

        # Regravar o valor LIDO pela propria camada de dados (timedelta nos dois backends).
        from app import db as appdb

        with appdb.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT plan_time, plan_date, created_at FROM workout_plans WHERE id = %s", (created["id"],))
                row = cur.fetchone()
                read_types = {key: type(value).__name__ for key, value in row.items()}
                cur.execute(
                    "UPDATE workout_plans SET plan_time = %s, plan_date = %s WHERE id = %s",
                    (row["plan_time"] + _dt.timedelta(minutes=30), row["plan_date"], created["id"]),
                )
                rowcount = cur.rowcount
        fetched = services.get_workout_plan(created["id"], user_id=user_id)
        results[backend] = {
            "create_plan_time": created["plan_time"],
            "stored_after_create": stored_after_create,
            "read_types": read_types,
            "update_rowcount": rowcount,
            "plan_time_after_update": fetched["plan_time"],
            "plan_date": fetched["plan_date"],
        }
    assert results["data-api"] == results["pymysql"]
    assert results["data-api"]["stored_after_create"] == _dt.timedelta(hours=9)
    assert results["data-api"]["read_types"] == {"plan_time": "timedelta", "plan_date": "date", "created_at": "datetime"}
    assert results["data-api"]["plan_time_after_update"] == "09:30"
    assert results["data-api"]["update_rowcount"] == 1

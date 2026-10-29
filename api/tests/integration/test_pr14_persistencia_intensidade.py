"""PR-14 - persistencia da intensidade efetiva e do evento de seguranca
(PLANO_20260912.md secoes 5.7, 8.2 - colunas da migracao 037 - e 14).

Aceite: gravar uma serie pelo Modo Treino preenche
`exercise_sets.rpe_target_effective` com o alvo EFETIVO da prescricao (nunca o
10 cru da ficha) e `exercise_sets.safety_event` quando o RPE registrado passa
do teto do par. Os dois valores vem do SERVIDOR - o cliente nao os manda.

Tambem cobre o caminho legado (ficha sem snapshot, clampada ao vivo), o treino
livre (sem ficha) e o `intensity_status` exposto em
`GET /analytics/dashboard-progression`.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics, services, workout_sessions
from app.domain.progression_engine import (
    INTENSITY_ABOVE_SAFE_CEILING,
    INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING,
    SAFETY_EVENT_RPE_ABOVE_CEILING,
)
from app.main import update_exercise_log_by_id
from app.models import ExerciseLogUpdateIn, WorkoutPlanExerciseIn
from app.security import Identity, get_current_identity

# `Banco reto` fica com teto 9 (falha PROIBIDA) e `Polia` com 10 (permitida).
TETOS = {"Banco reto": 9, "Polia": 10}


@pytest.fixture
def academia(db_conn, catalog):
    with db_conn.cursor() as cur:
        # historico de OUTRO teste nao pode aparecer no dashboard deste
        cur.execute("DELETE es FROM exercise_sets es JOIN exercises e ON e.id = es.exercise_id")
        cur.execute("DELETE FROM exercises")
        cur.execute("DELETE FROM activities")
        cur.execute("DELETE FROM workout_plans")
        gym_equipment = {}
        for name, exercise in (("Banco reto", "Supino reto"), ("Polia", "Remada baixa")):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[name]),
            )
            gym_equipment[name] = cur.lastrowid
        for name, teto in TETOS.items():
            cur.execute("UPDATE equipment SET max_safe_rpe = %s WHERE id = %s", (teto, catalog.equipment[name]))
    db_conn.commit()
    return gym_equipment


@pytest.fixture
def client(catalog) -> TestClient:
    app = FastAPI()
    app.include_router(workout_sessions.router)
    app.include_router(analytics.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=catalog.user_id, role="admin", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _ficha(catalog, academia, *, rpe_target: str, equipamento: str = "Banco reto", exercicio: str = "Supino reto"):
    """Ficha salva pelo caminho normal - o que congela a prescricao (PR-11)."""
    payload = {
        "name": "Ficha PR-14",
        "plan_date": date.today(),
        "plan_time": None,
        "plan_type": "full_body",
        "split_day": None,
        "goal": None,
        "status": "planned",
        "gym_id": catalog.gym_id,
        "gym_name": None,
        "raw_payload": None,
        "template_id": None,
        "template_slot_sequence": None,
        "exercises": [
            WorkoutPlanExerciseIn(
                sequence=1,
                exercise_name=exercicio,
                mapping_id=catalog.exercises[exercicio],
                preferred_equipment_id=academia[equipamento],
                sets_count=3,
                reps_target="10-12",
                load_target="50",
                rpe_target=rpe_target,
                rest_seconds=90,
            ).model_dump(exclude_unset=True)
        ],
    }
    return services.create_workout_plan(payload, user_id=catalog.user_id)


def _sessao(client, catalog, academia, plan, *, equipamento="Banco reto", exercicio="Supino reto"):
    started = client.post(
        "/workout-sessions/start",
        json={"name": "Treino PR-14", "workout_plan_id": plan["id"], "gym_id": catalog.gym_id},
    ).json()
    exercise = client.post(
        f"/workout-sessions/{started['session_id']}/exercises",
        json={
            "original_name": exercicio,
            "mapping_id": catalog.exercises[exercicio],
            "equipment_id": academia[equipamento],
            "workout_plan_exercise_id": plan["exercises"][0]["workout_plan_exercise_id"],
        },
    ).json()
    return started["session_id"], exercise["exercise_id"]


def _sets(conn, exercise_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_number, set_type, rpe, rpe_target_effective, safety_event FROM exercise_sets "
            "WHERE exercise_id = %s ORDER BY set_number",
            (exercise_id,),
        )
        return cur.fetchall() or []


def _grava(client, session_id, exercise_id, **body):
    payload = {"reps": 12, "weight_kg": 50, **body}
    return client.post(f"/workout-sessions/{session_id}/exercises/{exercise_id}/sets", json=payload)


# ---------------------------------------------------------------------------
# Aceite central
# ---------------------------------------------------------------------------


def test_alvo_efetivo_gravado_e_o_da_prescricao_nunca_o_10_da_ficha(db_conn, catalog, academia, client):
    """Ficha pedindo RPE 10 num par onde a falha e proibida: a prescricao
    congelou 9 e e 9 que fica gravado na serie."""
    plan = _ficha(catalog, academia, rpe_target="10")
    assert plan["exercises"][0]["prescription"]["effective_rpe_target"] == 9

    session_id, exercise_id = _sessao(client, catalog, academia, plan)
    resposta = _grava(client, session_id, exercise_id, rpe=9)

    assert resposta.status_code == 200
    assert resposta.json()["rpe_target_effective"] == 9
    assert resposta.json()["safety_event"] is None
    assert _sets(db_conn, exercise_id) == [
        {"set_number": 1, "set_type": "working", "rpe": 9, "rpe_target_effective": 9, "safety_event": None}
    ]


def test_rpe10_em_par_proibido_grava_safety_event_sem_elevar_o_alvo(db_conn, catalog, academia, client):
    plan = _ficha(catalog, academia, rpe_target="10")
    session_id, exercise_id = _sessao(client, catalog, academia, plan)

    resposta = _grava(client, session_id, exercise_id, rpe=10)

    assert resposta.json()["safety_event"] == SAFETY_EVENT_RPE_ABOVE_CEILING
    gravada = _sets(db_conn, exercise_id)[0]
    assert gravada["safety_event"] == SAFETY_EVENT_RPE_ABOVE_CEILING
    # o alvo EFETIVO continua 9: RPE 10 registrado nunca eleva alvo (5.7)
    assert gravada["rpe_target_effective"] == 9
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT rpe_target, prescription_json FROM workout_plan_exercises WHERE id = %s",
            (plan["exercises"][0]["workout_plan_exercise_id"],),
        )
        row = cur.fetchone()
    assert row["rpe_target"] == "10"  # a ficha nao e reescrita
    assert json.loads(row["prescription_json"])["effective_rpe_target"] == 9


def test_rpe10_em_par_que_permite_falha_nao_e_evento(db_conn, catalog, academia, client):
    plan = _ficha(catalog, academia, rpe_target="10", equipamento="Polia", exercicio="Remada baixa")
    assert plan["exercises"][0]["prescription"]["effective_rpe_target"] == 10

    session_id, exercise_id = _sessao(
        client, catalog, academia, plan, equipamento="Polia", exercicio="Remada baixa"
    )
    _grava(client, session_id, exercise_id, rpe=10)

    gravada = _sets(db_conn, exercise_id)[0]
    assert (gravada["rpe_target_effective"], gravada["safety_event"]) == (10, None)


def test_serie_preparatoria_nao_recebe_alvo_nem_evento(db_conn, catalog, academia, client):
    plan = _ficha(catalog, academia, rpe_target="10")
    session_id, exercise_id = _sessao(client, catalog, academia, plan)

    _grava(client, session_id, exercise_id, rpe=10, set_type="warmup", reps=15, weight_kg=10)
    _grava(client, session_id, exercise_id, rpe=10, set_type="feeler", reps=5, weight_kg=30)

    assert [(s["rpe_target_effective"], s["safety_event"]) for s in _sets(db_conn, exercise_id)] == [
        (None, None),
        (None, None),
    ]


def test_ficha_legada_sem_snapshot_e_clampada_ao_vivo(db_conn, catalog, academia, client):
    """Ficha anterior ao PR-11 (`prescription_json` NULL) com `rpe_target=10`
    num par proibido: o alvo gravado na serie e 9, cortado ao vivo."""
    plan = _ficha(catalog, academia, rpe_target="10")
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_exercises SET prescription_json = NULL WHERE plan_id = %s", (plan["id"],)
        )
    db_conn.commit()

    session_id, exercise_id = _sessao(client, catalog, academia, plan)
    _grava(client, session_id, exercise_id, rpe=10)

    gravada = _sets(db_conn, exercise_id)[0]
    assert (gravada["rpe_target_effective"], gravada["safety_event"]) == (9, SAFETY_EVENT_RPE_ABOVE_CEILING)


def test_treino_livre_sem_ficha_nao_inventa_alvo_mas_registra_o_evento(db_conn, catalog, academia, client):
    started = client.post("/workout-sessions/start", json={"name": "Livre", "gym_id": catalog.gym_id}).json()
    exercise = client.post(
        f"/workout-sessions/{started['session_id']}/exercises",
        json={
            "original_name": "Supino reto",
            "mapping_id": catalog.exercises["Supino reto"],
            "equipment_id": academia["Banco reto"],
        },
    ).json()

    _grava(client, started["session_id"], exercise["exercise_id"], rpe=10)

    gravada = _sets(db_conn, exercise["exercise_id"])[0]
    assert gravada["rpe_target_effective"] is None
    assert gravada["safety_event"] == SAFETY_EVENT_RPE_ABOVE_CEILING


def test_editar_o_exercicio_recalcula_em_vez_de_apagar_as_colunas(db_conn, catalog, academia, client):
    """`PUT /exercises/logs/{id}` (tela /registro-exercicios) RECRIA as series.
    Sem recalcular, o alvo efetivo e o evento gravados pelo Modo Treino
    sumiriam - este teste e a garantia disso."""
    plan = _ficha(catalog, academia, rpe_target="10")
    session_id, exercise_id = _sessao(client, catalog, academia, plan)
    _grava(client, session_id, exercise_id, rpe=10)

    services.update_exercise_log(
        exercise_id,
        {
            "local_date": date.today(),
            "sets": [
                {"set_number": 1, "set_type": "working", "reps": 12, "weight_kg": 50, "rpe": 10},
                {"set_number": 2, "set_type": "working", "reps": 12, "weight_kg": 50, "rpe": 8},
                {"set_number": 3, "set_type": "warmup", "reps": 15, "weight_kg": 10, "rpe": 6},
            ],
        },
        user_id=catalog.user_id,
    )

    assert [(s["rpe_target_effective"], s["safety_event"]) for s in _sets(db_conn, exercise_id)] == [
        (9, SAFETY_EVENT_RPE_ABOVE_CEILING),
        (9, None),
        (None, None),
    ]


def test_f5_editar_pelo_put_preserva_tipo_e_campos_da_serie(db_conn, catalog, academia, client):
    """Fase 5 - PR 1: o PUT de um cliente que só manda reps/carga/RPE (a tela
    antiga) não pode transformar aquecimento/drop/rest-pause em `working` nem
    apagar pausa, notas, FC e dor gravadas pelo Modo Treino."""
    plan = _ficha(catalog, academia, rpe_target="8")
    session_id, exercise_id = _sessao(client, catalog, academia, plan)
    _grava(client, session_id, exercise_id, set_type="warmup", reps=15, weight_kg=20, rpe=5, rest_seconds=60)
    _grava(client, session_id, exercise_id, rpe=8, rest_seconds=90, hr_avg_bpm=130, hr_max_bpm=150, muscle_pain=3)
    _grava(client, session_id, exercise_id, set_type="drop_set", reps=8, weight_kg=40, rpe=9, notes="drop 1", joint_pain=True)
    _grava(client, session_id, exercise_id, set_type="rest_pause", reps=4, weight_kg=40, rpe=10, rest_seconds=15)

    # handler real da rota (monta as séries com exclude_unset)
    payload = ExerciseLogUpdateIn(
        local_date=date.today(),
        original_name="Supino reto",
        sets=[{"set_number": n, "reps": 10, "weight_kg": 45, "rpe": 8} for n in (1, 2, 3, 4)],
    )
    identity = Identity(user_id=catalog.user_id, role="admin", source="session")
    assert update_exercise_log_by_id(exercise_id, payload, identity=identity)["status"] == "ok"

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT set_type, reps, rest_seconds, hr_avg_bpm, hr_max_bpm, notes, muscle_pain, joint_pain "
            "FROM exercise_sets WHERE exercise_id = %s ORDER BY set_number",
            (exercise_id,),
        )
        rows = cur.fetchall()
        cur.execute("SELECT sets_count FROM exercises WHERE id = %s", (exercise_id,))
        sets_count = cur.fetchone()["sets_count"]

    assert [
        (r["set_type"], r["reps"], r["rest_seconds"], r["notes"], r["muscle_pain"], bool(r["joint_pain"])) for r in rows
    ] == [
        ("warmup", 10, 60, None, None, False),
        ("working", 10, 90, None, 3, False),
        ("drop_set", 10, None, "drop 1", None, True),
        ("rest_pause", 10, 15, None, None, False),
    ]
    assert (rows[1]["hr_avg_bpm"], rows[1]["hr_max_bpm"]) == (Decimal("130.00"), Decimal("150.00"))
    # o aquecimento preservado continua fora dos agregados
    assert sets_count == 3


# ---------------------------------------------------------------------------
# Analytics: intensity_status exposto (secao 5.7)
# ---------------------------------------------------------------------------


def test_dashboard_progression_expoe_intensity_status(db_conn, catalog, academia, client):
    plan = _ficha(catalog, academia, rpe_target="10")
    session_id, exercise_id = _sessao(client, catalog, academia, plan)
    _grava(client, session_id, exercise_id, rpe=8)
    _grava(client, session_id, exercise_id, rpe=9)
    # aquecimento com RPE alto NAO pode decidir o status da sessao
    _grava(client, session_id, exercise_id, rpe=10, set_type="warmup", reps=15, weight_kg=10)
    client.post(f"/workout-sessions/{session_id}/finish", json={})

    items = client.get(
        f"/analytics/dashboard-progression?mapping_id={catalog.exercises['Supino reto']}&days=30"
    ).json()["items"]

    assert len(items) == 1
    assert items[0]["intensity_status"] == INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    assert items[0]["rpe_target_effective"] == 9
    assert items[0]["failure_allowed"] is False
    assert items[0]["sets_count"] == 2  # so as `working`


def test_dashboard_progression_mostra_rpe_acima_do_teto(db_conn, catalog, academia, client):
    plan = _ficha(catalog, academia, rpe_target="10")
    session_id, exercise_id = _sessao(client, catalog, academia, plan)
    _grava(client, session_id, exercise_id, rpe=10)
    client.post(f"/workout-sessions/{session_id}/finish", json={})

    items = client.get(
        f"/analytics/dashboard-progression?mapping_id={catalog.exercises['Supino reto']}&days=30"
    ).json()["items"]

    assert items[0]["intensity_status"] == INTENSITY_ABOVE_SAFE_CEILING


def test_sessao_legada_sem_alvo_gravado_nao_inventa_status(db_conn, catalog, academia, client):
    """Serie anterior ao PR-14 (colunas NULL): a secao 12 manda exibir o
    historico como esta, sem alvo retroativo."""
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type) "
            "VALUES (%s, CURDATE(), NOW(), 'Treino legado', 'Musculacao')",
            (catalog.user_id,),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, 'Supino reto', %s, %s, 1)",
            (activity_id, catalog.exercises["Supino reto"], academia["Banco reto"]),
        )
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
            "VALUES (%s, 1, 'working', 12, 50, 9)",
            (cur.lastrowid,),
        )
    db_conn.commit()

    items = client.get(
        f"/analytics/dashboard-progression?mapping_id={catalog.exercises['Supino reto']}&days=30"
    ).json()["items"]

    assert [item["intensity_status"] for item in items] == [None]

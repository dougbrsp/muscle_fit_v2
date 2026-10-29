"""PR-11 - congelamento da prescricao da ficha (PLANO_20260912.md 6.3, 9, 12).

Aceite central: **abrir o mesmo plano duas vezes devolve exatamente a mesma
prescricao**, mesmo com treino novo registrado no meio (o que, antes deste PR,
mudava a "progressao" a cada leitura).

Tambem cobre: round-trip POST -> PUT -> GET preservando `prescription_json` e
os 10 campos avancados; leitura de ficha legada (sem snapshot) nos dois
status; overlay de seguranca ao vivo (so restringe); e
`POST /workout-plans/{id}/refresh-prescription` nos tres caminhos.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import services, workout_plans
from app.domain import prescription as pd
from app.domain.prescription_service import PAIR_SUGGESTION_ENGINE_VERSION
from app.models import WorkoutPlanExerciseIn
from app.security import Identity, get_current_identity

ADVANCED = {
    "advanced_technique": "drop_set",
    "advanced_technique_applications": 2,
    "advanced_technique_duration_seconds": 30,
    "warmup_sets_count": 1,
    "warmup_reps": 12,
    "warmup_rest_seconds": 60,
    "feeler_set_sets_count": 1,
    "feeler_set_reps": 5,
    "feeler_set_rest_seconds": 90,
    "peak_contraction_seconds": 2,
}


def _ex(**kwargs) -> dict:
    """Mesmo formato que o router entrega ao service (PUT usa exclude_unset)."""
    return WorkoutPlanExerciseIn(**kwargs).model_dump(exclude_unset=True)


@pytest.fixture
def ficha(db_conn, catalog):
    """Academia do `catalog` com dois equipamentos materializados em
    `gym_equipment` e um teto de seguranca conhecido no banco reto."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plans")
        gym_equipment = {}
        for name, exercise in (("Banco reto", "Supino reto"), ("Polia", "Remada baixa")):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[name]),
            )
            gym_equipment[name] = cur.lastrowid
        # teto 8 no banco reto: o RPE prescrito 10 sofre clamp ao congelar.
        cur.execute("UPDATE equipment SET max_safe_rpe = 8 WHERE id = %s", (catalog.equipment["Banco reto"],))
    db_conn.commit()
    return gym_equipment


def _payload(catalog, ficha, *, plan_date=None, status="planned", exercises=None) -> dict:
    return {
        "name": "Ficha PR-11",
        "plan_date": plan_date or date.today(),
        "plan_time": None,
        "plan_type": "full_body",
        "split_day": None,
        "goal": None,
        "status": status,
        "gym_id": catalog.gym_id,
        "gym_name": None,
        "raw_payload": None,
        "template_id": None,
        "template_slot_sequence": None,
        "exercises": exercises
        if exercises is not None
        else [
            _ex(
                sequence=1,
                exercise_name="Supino reto",
                mapping_id=catalog.exercises["Supino reto"],
                preferred_equipment_id=ficha["Banco reto"],
                sets_count=4,
                reps_target="8-10",
                load_target="60",
                rpe_target="10",
                rest_seconds=90,
                **ADVANCED,
            ),
            _ex(
                sequence=2,
                exercise_name="Remada baixa",
                mapping_id=catalog.exercises["Remada baixa"],
                preferred_equipment_id=ficha["Polia"],
                sets_count=3,
                reps_target="10-12",
                rpe_target="8",
            ),
        ],
    }


def _registra_treino(conn, catalog, ficha, *, when: str, load: float) -> None:
    """Treino REAL depois da ficha salva: e exatamente o tipo de dado que
    fazia a progressao mudar a cada leitura antes deste PR."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type) "
            "VALUES (%s, DATE(%s), %s, 'Treino PR-11', 'Musculacao')",
            (catalog.user_id, when, when),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, 'Supino reto', %s, %s, 3)",
            (activity_id, catalog.exercises["Supino reto"], ficha["Banco reto"]),
        )
        exercise_id = cur.lastrowid
        for set_number in (1, 2, 3):
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                "VALUES (%s, %s, 'working', 12, %s, 7)",
                (exercise_id, set_number, load),
            )
    conn.commit()


def _prescriptions(plan: dict) -> dict[int, dict]:
    return {ex["sequence"]: ex["prescription"] for ex in plan["exercises"]}


def _stored_json(conn, plan_id: int) -> dict[int, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sequence, prescription_json, engine_version, requires_user_choice "
            "FROM workout_plan_exercises WHERE plan_id = %s",
            (plan_id,),
        )
        return {row["sequence"]: row for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Aceite central
# ---------------------------------------------------------------------------


def test_abrir_o_mesmo_plano_duas_vezes_devolve_exatamente_a_mesma_prescricao(db_conn, catalog, ficha):
    plan = services.create_workout_plan(_payload(catalog, ficha), user_id=catalog.user_id)
    primeira = services.get_workout_plan(plan["id"], user_id=catalog.user_id)

    _registra_treino(db_conn, catalog, ficha, when="2026-09-17 10:00:00", load=80)

    segunda = services.get_workout_plan(plan["id"], user_id=catalog.user_id)
    assert _prescriptions(segunda) == _prescriptions(primeira)
    assert [ex["progression_note"] for ex in segunda["exercises"]] == [
        ex["progression_note"] for ex in primeira["exercises"]
    ]


def test_congelamento_grava_snapshot_com_origem_e_clamp_de_seguranca(db_conn, catalog, ficha):
    plan = services.create_workout_plan(_payload(catalog, ficha), user_id=catalog.user_id)
    supino = _prescriptions(plan)[1]

    assert supino["schema_version"] == pd.SCHEMA_VERSION
    # M76 Lote 1a: ficha sem Modelo congela pelo motor v2 por par (decisao
    # composta com a seguranca do par + contrato de recuperacao projetado).
    assert supino["engine_version"] == PAIR_SUGGESTION_ENGINE_VERSION
    assert supino["source"] == pd.SOURCE_PLAN_CREATED
    assert supino["rpe_target"] == "10"  # o que foi prescrito
    assert supino["effective_rpe_ceiling"] == 8 and supino["effective_rpe_target"] == 8
    assert supino["clamps"] == [{"field": "rpe_target", "from": 10, "to": 8, "reason": "EQUIPMENT_SAFETY"}]
    assert supino["failure_allowed"] is False
    assert plan["exercises"][0]["prescription_status"] == pd.STATUS_FROZEN
    assert plan["exercises"][0]["safety_overlay"]["effective_rpe_ceiling"] == 8

    linhas = _stored_json(db_conn, plan["id"])
    assert linhas[1]["engine_version"] == PAIR_SUGGESTION_ENGINE_VERSION
    assert linhas[1]["prescription_json"] is not None


def test_overlay_ao_vivo_so_restringe_e_nunca_reescreve_o_snapshot(db_conn, catalog, ficha):
    plan_id = services.create_workout_plan(_payload(catalog, ficha), user_id=catalog.user_id)["id"]
    gravado = _stored_json(db_conn, plan_id)[1]["prescription_json"]

    with db_conn.cursor() as cur:  # teto CAI depois de a ficha ser congelada
        cur.execute("UPDATE equipment SET max_safe_rpe = 6 WHERE id = %s", (catalog.equipment["Banco reto"],))
    db_conn.commit()
    supino = services.get_workout_plan(plan_id, user_id=catalog.user_id)["exercises"][0]
    assert supino["prescription"]["effective_rpe_target"] == 6
    assert supino["safety_overlay"]["clamps"] == [
        {"field": "rpe_target", "from": 8, "to": 6, "reason": "EQUIPMENT_SAFETY"}
    ]
    assert _stored_json(db_conn, plan_id)[1]["prescription_json"] == gravado  # snapshot intacto

    with db_conn.cursor() as cur:  # teto SOBE: o overlay nao amplia nada
        cur.execute("UPDATE equipment SET max_safe_rpe = 10 WHERE id = %s", (catalog.equipment["Banco reto"],))
    db_conn.commit()
    supino = services.get_workout_plan(plan_id, user_id=catalog.user_id)["exercises"][0]
    assert supino["prescription"]["effective_rpe_target"] == 8
    assert supino["safety_overlay"]["clamps"] == []


# ---------------------------------------------------------------------------
# Round-trip POST -> PUT -> GET
# ---------------------------------------------------------------------------


def test_put_preserva_prescricao_e_os_dez_campos_avancados(db_conn, catalog, ficha):
    plan = services.create_workout_plan(_payload(catalog, ficha), user_id=catalog.user_id)
    congelado = _prescriptions(plan)

    payload = _payload(
        catalog,
        ficha,
        exercises=[
            # cliente "burro": manda so o basico, sem campos avancados e sem
            # nenhum campo de prescricao (que e read-only para ele).
            _ex(
                sequence=1,
                exercise_name="Supino reto",
                mapping_id=catalog.exercises["Supino reto"],
                preferred_equipment_id=ficha["Banco reto"],
                sets_count=5,
            ),
            _ex(
                sequence=2,
                exercise_name="Remada baixa",
                mapping_id=catalog.exercises["Remada baixa"],
                preferred_equipment_id=ficha["Polia"],
                sets_count=3,
            ),
            # exercicio NOVO: ganha prescricao propria agora.
            _ex(
                sequence=3,
                exercise_name="Agachamento",
                mapping_id=catalog.exercises["Agachamento"],
                sets_count=4,
                rpe_target="9",
            ),
        ],
    )
    payload["name"] = "Ficha PR-11 editada"
    services.update_workout_plan(plan["id"], payload, user_id=catalog.user_id)

    depois = services.get_workout_plan(plan["id"], user_id=catalog.user_id)
    assert depois["name"] == "Ficha PR-11 editada"
    assert _prescriptions(depois)[1] == congelado[1]
    assert _prescriptions(depois)[2] == congelado[2]
    assert depois["exercises"][0]["sets_count"] == 5  # o resto da ficha foi editado
    for field, value in ADVANCED.items():
        assert depois["exercises"][0][field] == value, field

    novo = _prescriptions(depois)[3]
    assert novo["source"] == pd.SOURCE_PLAN_CREATED and novo["rpe_target"] == "9"
    # sem equipamento resolvido -> default conservador D3, com aviso.
    assert novo["warnings"] == [pd.WARNING_EQUIPMENT_UNRESOLVED]
    assert novo["effective_rpe_ceiling"] == 9


# ---------------------------------------------------------------------------
# Ficha legada (secao 12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["planned", "done"])
def test_ficha_legada_sem_snapshot_continua_legivel(db_conn, catalog, ficha, status):
    plan_id = services.create_workout_plan(
        _payload(catalog, ficha, status=status), user_id=catalog.user_id
    )["id"]
    with db_conn.cursor() as cur:  # simula ficha anterior a 037/PR-11
        cur.execute(
            "UPDATE workout_plan_exercises SET prescription_json = NULL, engine_version = NULL WHERE plan_id = %s",
            (plan_id,),
        )
    db_conn.commit()

    plan = services.get_workout_plan(plan_id, user_id=catalog.user_id)
    assert plan["status"] == status
    for ex in plan["exercises"]:
        assert ex["prescription"] is None
        assert ex["prescription_status"] == pd.STATUS_LEGACY
        assert ex["progression"] is None and ex["progression_note"] is None
    # a seguranca de hoje continua sendo informada mesmo sem snapshot
    assert plan["exercises"][0]["safety_overlay"]["effective_rpe_ceiling"] == 8


# ---------------------------------------------------------------------------
# refresh-prescription
# ---------------------------------------------------------------------------


@pytest.fixture
def client(catalog) -> TestClient:
    app = FastAPI()
    app.include_router(workout_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=catalog.user_id, role="admin", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def test_refresh_recongela_a_ficha_planejada(db_conn, catalog, ficha, client):
    plan_id = services.create_workout_plan(_payload(catalog, ficha), user_id=catalog.user_id)["id"]
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_exercises SET prescription_json = NULL, engine_version = NULL WHERE plan_id = %s",
            (plan_id,),
        )
    db_conn.commit()

    response = client.post(f"/workout-plans/{plan_id}/refresh-prescription")
    assert response.status_code == 200
    body = response.json()
    assert body["refreshed"] == 2
    supino = body["plan"]["exercises"][0]
    assert supino["prescription_status"] == pd.STATUS_FROZEN
    assert supino["prescription"]["source"] == pd.SOURCE_REFRESH
    assert supino["prescription"]["effective_rpe_target"] == 8


def test_refresh_recusa_ficha_concluida(db_conn, catalog, ficha, client):
    plan_id = services.create_workout_plan(_payload(catalog, ficha, status="done"), user_id=catalog.user_id)["id"]
    response = client.post(f"/workout-plans/{plan_id}/refresh-prescription")
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "plan_not_planned"


def test_refresh_recusa_ficha_de_data_passada(db_conn, catalog, ficha, client):
    ontem = date.today() - timedelta(days=1)
    plan_id = services.create_workout_plan(_payload(catalog, ficha, plan_date=ontem), user_id=catalog.user_id)["id"]
    response = client.post(f"/workout-plans/{plan_id}/refresh-prescription")
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "plan_date_in_past"


def test_refresh_de_ficha_inexistente_e_404(client):
    assert client.post("/workout-plans/987654/refresh-prescription").status_code == 404

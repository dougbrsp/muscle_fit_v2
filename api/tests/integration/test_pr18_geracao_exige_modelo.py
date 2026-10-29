"""PR-18 - a geracao ponta a ponta so existe a partir de um Modelo de Treino
(PLANO_20260912.md secoes 6, 9, 12 e 15).

Com banco real:

- `dispatch_plan_generation` gera nos DOIS modos deterministicos a partir do
  modelo atribuido (`equipment_series` = Modo B, `percent_budget` = Modo A);
- modelo de OUTRO usuario -> 404 (o `template_id` forjado nao vira ficha);
- **compatibilidade (secao 12):** uma ficha legada, com `plan_type='abc'` e
  `split_day='B'` gravados, continua legivel pelo `GET /workout-plans/{id}` e
  aparece na listagem com os dois campos - o PR-18 tira `plan_type`/`split_day`
  do INPUT de geracao, nao da leitura.

O contrato HTTP de `POST /workout-plans/generate` (422/404 sem banco) esta em
`tests/unit/test_pr18_geracao_exige_modelo.py`.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import services, workout_plans
from app.models import WorkoutPlanGenerateIn
from app.plan_generation_core import dispatch_plan_generation
from app.security import Identity, get_current_identity
from app.workout_plan_templates import _get_template
from tests.support.template_versions import versao_v1


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """Producao do PR-12/13: as duas flags ligadas."""
    monkeypatch.setenv("GENERATION_ENGINE", "deterministic")
    monkeypatch.setenv("PROGRESSION_ENGINE", "v2")


@pytest.fixture
def recovery(monkeypatch):
    """Recuperacao neutra nos dois caminhos que a leem (mesmo dublê do PR-13):
    sem isso o Modo A dependeria do historico real do banco de teste."""
    from app import workout_plan_ai
    from app.intelligence.muscle_recovery_service import MuscleRecoveryService

    monkeypatch.setattr(MuscleRecoveryService, "get_recovery_overview", lambda self, **kwargs: [])
    monkeypatch.setattr(
        workout_plan_ai,
        "gather_ficha_context",
        lambda gym_id, user_id: {"muscle_recovery": [], "gyms": [], "recent_exercise_logs": []},
    )


@pytest.fixture
def gym(db_conn, catalog):
    """Academia com pares ativos e seguranca permissiva."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plans")
        cur.execute("DELETE FROM workout_plan_templates")
        cur.execute("DELETE FROM training_type_muscle_group_targets")
        cur.execute("DELETE FROM training_types")
        cur.execute("DELETE FROM exercise_sets")
        cur.execute("DELETE FROM exercises")
        cur.execute("DELETE FROM activities")
        cur.execute("DELETE FROM progression_decisions")
        cur.execute("DELETE FROM progression_tracks")
        cur.execute("DELETE FROM progression_group_states")
        ids = {}
        for equipment, exercise in (("Banco reto", "Supino reto"), ("Polia", "Crucifixo")):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[equipment]),
            )
            ids[(equipment, exercise)] = cur.lastrowid
        cur.execute(
            "INSERT IGNORE INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
            (catalog.equipment["Polia"], catalog.exercises["Crucifixo"]),
        )
        cur.execute("UPDATE equipment SET max_safe_rpe = 10")
    db_conn.commit()
    return ids


def _assign(conn, template_id: int, user_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (template_id, user_id, user_id),
        )
    conn.commit()


def _template_modo_b(conn, catalog, *, assign_to: int | None = None) -> int:
    """Modelo `equipment_series` com um item de equipamento."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Modelo B do PT', 'equipment_series', 1, %s)",
            (catalog.user_id,),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'A', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
        slot_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slot_items "
            "(slot_id, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, 'equipment', %s, %s, %s, 3)",
            (slot_id, catalog.muscle_groups["Peitoral"], catalog.exercises["Crucifixo"], catalog.equipment["Polia"]),
        )
    conn.commit()
    if assign_to is not None:
        _assign(conn, template_id, assign_to)
    return template_id


def _template_modo_a(conn, catalog, *, assign_to: int | None = None) -> int:
    """Modelo `percent_budget` com um slot de Peitoral (50-75% de 8 series)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO training_types (name, is_active, created_by_user_id) VALUES ('Hipertrofia', 1, %s)",
            (catalog.user_id,),
        )
        training_type_id = cur.lastrowid
        for group, (weekly_min, weekly_target, weekly_max) in {
            "Peitoral": (4, 8, 12),
            "Tríceps": (2, 6, 10),
            "Ombros": (2, 6, 10),
        }.items():
            cur.execute(
                "INSERT INTO training_type_muscle_group_targets "
                "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets) "
                "VALUES (%s,%s,%s,%s,%s)",
                (training_type_id, catalog.muscle_groups[group], weekly_min, weekly_target, weekly_max),
            )
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, training_type_id, is_active, created_by_user_id) "
            "VALUES ('Modelo A do PT', 'percent_budget', %s, 1, %s)",
            (training_type_id, catalog.user_id),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'A', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
        slot_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slot_items "
            "(slot_id, item_order, item_kind, muscle_group_id, priority_min_pct, priority_max_pct, prioritize) "
            "VALUES (%s, 1, 'muscle_group', %s, 50, 75, 0)",
            (slot_id, catalog.muscle_groups["Peitoral"]),
        )
    conn.commit()
    if assign_to is not None:
        _assign(conn, template_id, assign_to)
    return template_id


def _generate(catalog, template_id: int, **kwargs) -> dict:
    payload = WorkoutPlanGenerateIn(
        template_id=template_id, gym_id=catalog.gym_id, min_rest_seconds=60, max_rest_seconds=90, **kwargs
    )
    return dispatch_plan_generation(payload, catalog.user_id)


# ---------------------------------------------------------------------------
# Geracao a partir do modelo, nos dois modos
# ---------------------------------------------------------------------------


def test_modo_b_gera_a_partir_do_modelo_atribuido(db_conn, catalog, gym, recovery):
    template_id = _template_modo_b(db_conn, catalog, assign_to=catalog.user_id)

    draft = _generate(catalog, template_id)

    assert draft["template_id"] == template_id
    assert draft["template_slot_sequence"] == 1
    assert draft["raw_payload"]["engine_version"] == "mode-b-deterministic-v1"
    assert draft["raw_payload"]["generation_audit"]["llm_used"] is False
    assert [ex["mapping_id"] for ex in draft["exercises"]] == [catalog.exercises["Crucifixo"]]


def test_modo_a_gera_a_partir_do_modelo_atribuido(db_conn, catalog, gym, recovery):
    template_id = _template_modo_a(db_conn, catalog, assign_to=catalog.user_id)

    draft = _generate(catalog, template_id)

    assert draft["template_id"] == template_id
    assert draft["raw_payload"]["engine_version"] == "mode-a-deterministic-v1"
    assert draft["raw_payload"]["generation_audit"]["llm_used"] is False
    assert draft["exercises"], "o Modo A precisa escolher pelo menos um exercicio do orcamento"


def test_slot_explicito_do_modelo_e_respeitado(db_conn, catalog, gym, recovery):
    template_id = _template_modo_b(db_conn, catalog, assign_to=catalog.user_id)

    draft = _generate(catalog, template_id, template_slot_sequence=1)

    assert draft["template_slot_sequence"] == 1


def test_modelo_nao_atribuido_e_404_sem_gerar_nada(db_conn, catalog, gym, recovery):
    template_id = _template_modo_b(db_conn, catalog)  # sem atribuicao

    with pytest.raises(HTTPException) as exc:
        _generate(catalog, template_id)

    assert exc.value.status_code == 404


def test_modelo_sem_slots_e_422(db_conn, catalog, gym, recovery):
    template_id = _template_modo_b(db_conn, catalog, assign_to=catalog.user_id)
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plan_template_slots WHERE template_id = %s", (template_id,))
    db_conn.commit()
    assert not _get_template(template_id)["slots"]

    with pytest.raises(HTTPException) as exc:
        _generate(catalog, template_id)

    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Compatibilidade: ficha legada continua legivel (secao 12)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(catalog) -> TestClient:
    app = FastAPI()
    app.include_router(workout_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=catalog.user_id, role="admin", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def test_ficha_legada_abc_continua_legivel_no_get_e_na_listagem(db_conn, catalog, gym, client):
    """Ficha gerada ANTES do PR-18: `plan_type='abc'` + `split_day='B'`."""
    plan_id = services.create_workout_plan(
        {
            "name": "Treino B (legado)",
            "plan_date": date.today(),
            "plan_time": None,
            "plan_type": "abc",
            "split_day": "B",
            "goal": None,
            "status": "completed",
            "gym_id": catalog.gym_id,
            "gym_name": None,
            "raw_payload": None,
            "template_id": None,
            "template_slot_sequence": None,
            "exercises": [
                {
                    "sequence": 1,
                    "exercise_name": "Supino reto",
                    "mapping_id": catalog.exercises["Supino reto"],
                    "sets_count": 3,
                    "reps_target": "8-10",
                }
            ],
        },
        user_id=catalog.user_id,
    )["id"]

    detail = client.get(f"/workout-plans/{plan_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["plan_type"] == "abc"
    assert body["split_day"] == "B"
    assert body["exercises"][0]["exercise_name"] == "Supino reto"

    listing = client.get("/workout-plans")
    assert listing.status_code == 200
    item = next(row for row in listing.json()["items"] if row["id"] == plan_id)
    assert item["plan_type"] == "abc"
    assert item["split_day"] == "B"
    # Ficha sem Modelo: sem nome de Modelo -> o frontend cai no rotulo legado.
    assert item["template_id"] is None
    assert item["template_name"] is None
    assert body["template_name"] is None


def test_ficha_do_modelo_expoe_o_nome_do_modelo_no_get_e_na_listagem(db_conn, catalog, gym, client):
    """Bug do "Full Body (IA)": ficha gerada de Modelo mostra o NOME do Modelo.

    `plan_type` continua gravado como veio (o default 'full_body' do Modelo),
    mas a listagem e o detalhe passam a devolver `template_id` + `template_name`
    para a coluna "Tipo" nao exibir o rotulo legado.
    """
    template_id = _template_modo_b(db_conn, catalog, assign_to=catalog.user_id)
    plan_id = services.create_workout_plan(
        {
            "name": "Treino A",
            "plan_date": date.today(),
            "plan_time": None,
            "plan_type": "full_body",
            "split_day": None,
            "goal": None,
            "status": "planned",
            "gym_id": catalog.gym_id,
            "gym_name": None,
            "raw_payload": None,
            "template_id": template_id,
            "template_slot_sequence": 1,
            "exercises": [
                {
                    "sequence": 1,
                    "exercise_name": "Crucifixo",
                    "mapping_id": catalog.exercises["Crucifixo"],
                    "sets_count": 3,
                    "reps_target": "8-10",
                }
            ],
        },
        user_id=catalog.user_id,
    )["id"]

    detail = client.get(f"/workout-plans/{plan_id}")
    assert detail.status_code == 200
    assert detail.json()["template_id"] == template_id
    assert detail.json()["template_name"] == "Modelo B do PT"

    listing = client.get("/workout-plans")
    assert listing.status_code == 200
    item = next(row for row in listing.json()["items"] if row["id"] == plan_id)
    assert item["template_id"] == template_id
    assert item["template_name"] == "Modelo B do PT"
    # o LEFT JOIN no Modelo nao multiplica a contagem de exercicios
    assert item["exercises_count"] == 1

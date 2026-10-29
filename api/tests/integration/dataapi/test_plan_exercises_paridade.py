"""`services._replace_plan_exercises` (+ `workout_plan_exercise_merge`) nos
dois backends: cria e atualiza a ficha com campos avancados enviados,
omitidos (preservados) e `null` explicito (limpos), remocao/insercao de
sequence e troca de nome (re-resolucao de mapping via catalogo). O estado
gravado e lido por conexao neutra e precisa ser identico nos dois backends.
"""

from __future__ import annotations

import pytest

import app.db as appdb
from app import services
from app.models import WorkoutPlanExerciseIn
from tests.support.dataapi_backend import use_dataapi

STATE_COLUMNS = (
    "sequence, exercise_name, amazfit_name, mapping_id, muscle_primary, muscle_secondary, equipment_name, "
    "preferred_equipment_id, sets_count, reps_target, load_target, rpe_target, rest_seconds, warmup, "
    "substitution_options, notes, " + ", ".join(services.ADVANCED_EXERCISE_FIELDS)
)

ADVANCED_V1 = {
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


def _ex(**kwargs):
    # Mesmo formato que o router entrega ao service no PUT (exclude_unset).
    return WorkoutPlanExerciseIn(**kwargs).model_dump(exclude_unset=True)


CREATE = [
    _ex(sequence=1, exercise_name="Supino reto", mapping_id=1001, sets_count=4, reps_target="8-10", load_target="60kg",
        rpe_target="8", rest_seconds=90, warmup=True, substitution_options=["Supino com halteres", "Crucifixo"],
        notes="pegada média", **ADVANCED_V1),
    _ex(sequence=2, exercise_name="Remada baixa", mapping_id=1003, sets_count=3, **ADVANCED_V1),
    _ex(sequence=3, exercise_name="Agachamento", sets_count=5, peak_contraction_seconds=3),
    _ex(sequence=4, exercise_name="Crucifixo", sets_count=3),
]

UPDATE = [
    # seq 1: campos avancados OMITIDOS -> preservados; outros campos mudam.
    _ex(sequence=1, exercise_name="Supino reto", mapping_id=1001, sets_count=5, reps_target="6-8", warmup=False),
    # seq 2: null EXPLICITO limpa; valor novo sobrescreve; restante omitido preserva.
    _ex(sequence=2, exercise_name="Remada baixa", mapping_id=1003, advanced_technique=None, warmup_reps=None,
        peak_contraction_seconds=5),
    # seq 3: nome trocado -> mapping explicito descartado, re-resolvido pelo catalogo.
    _ex(sequence=3, exercise_name="Remada baixa", mapping_id=1001, sets_count=4),
    # seq 4 removida; seq 5 nova.
    _ex(sequence=5, exercise_name="Supino reto", sets_count=2, feeler_set_reps=3),
]


@pytest.fixture
def plan_catalog(raw):
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in ("workout_plan_exercises", "workout_plans", "exercise_aliases", "exercise_legacy_id_map",
                      "exercise_muscle_contributions", "exercise_catalog", "exercise_zepp_mappings", "users"):
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('dono', 'x', 'admin')")
        user_id = cur.lastrowid
        for exercise_id, name in ((1001, "Supino reto"), (1003, "Remada baixa"), (1004, "Agachamento"), (1002, "Crucifixo")):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, name, name),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))
    return user_id


def _state(raw, plan_id):
    with raw.cursor() as cur:
        cur.execute(f"SELECT id, {STATE_COLUMNS} FROM workout_plan_exercises WHERE plan_id = %s ORDER BY sequence", (plan_id,))
        rows = cur.fetchall()
    ids = {row["sequence"]: row.pop("id") for row in rows}
    return ids, rows


def _run(raw, user_id):
    with raw.cursor() as cur:
        cur.execute("INSERT INTO workout_plans (user_id, name) VALUES (%s, 'Ficha paridade')", (user_id,))
        plan_id = cur.lastrowid
    with appdb.transaction() as conn:
        with conn.cursor() as cur:
            services._replace_plan_exercises(cur, plan_id, [dict(e) for e in CREATE], None)
    created_ids, created = _state(raw, plan_id)
    with appdb.transaction() as conn:
        with conn.cursor() as cur:
            services._replace_plan_exercises(cur, plan_id, [dict(e) for e in UPDATE], None)
    updated_ids, updated = _state(raw, plan_id)
    return {"created": created, "updated": updated, "created_ids": created_ids, "updated_ids": updated_ids}


@pytest.fixture
def both_backends(plan_catalog, raw, monkeypatch, fake_rds, mysql_schema):
    results = {"pymysql": _run(raw, plan_catalog)}
    use_dataapi(monkeypatch, fake_rds, mysql_schema)
    results["data-api"] = _run(raw, plan_catalog)
    assert fake_rds.calls_of("ExecuteStatement"), "o caminho data-api nao passou pelo dublê"
    return results


def test_paridade_estado_gravado(both_backends):
    assert both_backends["data-api"]["created"] == both_backends["pymysql"]["created"]
    assert both_backends["data-api"]["updated"] == both_backends["pymysql"]["updated"]


@pytest.mark.parametrize("backend", ["pymysql", "data-api"])
def test_regras_de_merge_no_backend(both_backends, backend):
    result = both_backends[backend]
    created = {row["sequence"]: row for row in result["created"]}
    updated = {row["sequence"]: row for row in result["updated"]}

    assert created[1]["warmup"] == 1
    assert created[1]["substitution_options"] == '["Supino com halteres", "Crucifixo"]'
    for field, value in ADVANCED_V1.items():
        assert created[1][field] == value

    # omitidos: preservados; campos normais atualizados
    assert (updated[1]["sets_count"], updated[1]["reps_target"], updated[1]["warmup"]) == (5, "6-8", 0)
    for field, value in ADVANCED_V1.items():
        assert updated[1][field] == value, field

    # null explicito limpa, valor novo sobrescreve, omitido preserva
    assert updated[2]["advanced_technique"] is None
    assert updated[2]["warmup_reps"] is None
    assert updated[2]["peak_contraction_seconds"] == 5
    assert updated[2]["feeler_set_rest_seconds"] == 90

    # nome trocado: mapping explicito (1001) NAO e reaproveitado (re-resolucao
    # pelo nome via match_mapping; com este catalogo minimo nao ha match nos
    # dois backends - a paridade do valor resolvido e coberta acima).
    assert updated[3]["exercise_name"] == "Remada baixa"
    assert updated[3]["mapping_id"] != 1001
    assert updated[3]["peak_contraction_seconds"] == 3  # preservado (casamento por sequence)

    # removida / nova
    assert 4 not in updated
    assert updated[5]["feeler_set_reps"] == 3 and updated[5]["advanced_technique"] is None

    # UPDATE preserva id das sequences mantidas
    for sequence in (1, 2, 3):
        assert result["updated_ids"][sequence] == result["created_ids"][sequence]


# ---------------------------------------------------------------------------
# PR-11: prescricao congelada (gravacao do JSON + releitura) nos dois backends
# ---------------------------------------------------------------------------

PLANO_PR11 = {
    "name": "Ficha PR-11 paridade",
    "plan_date": None,
    "plan_time": None,
    "plan_type": "full_body",
    "split_day": None,
    "goal": None,
    "status": "planned",
    "gym_id": None,
    "gym_name": None,
    "raw_payload": None,
    "template_id": None,
    "template_slot_sequence": None,
    "exercises": [
        _ex(sequence=1, exercise_name="Supino reto", mapping_id=1001, sets_count=4, reps_target="8-10", rpe_target="10"),
        _ex(sequence=2, exercise_name="Remada baixa", mapping_id=1003, sets_count=3, rpe_target="8"),
    ],
}


def _sem_relogio(prescription: dict) -> dict:
    """`frozen_at` e o instante da execucao: difere entre as duas rodadas por
    construcao e nao entra na comparacao de paridade."""
    return {key: value for key, value in prescription.items() if key != "frozen_at"}


def _run_prescricao(user_id):
    payload = {**PLANO_PR11, "exercises": [dict(e) for e in PLANO_PR11["exercises"]]}
    plan = services.create_workout_plan(payload, user_id=user_id)
    relido = services.get_workout_plan(plan["id"], user_id=user_id)
    return {
        "criado": [_sem_relogio(ex["prescription"]) for ex in plan["exercises"]],
        "relido": [_sem_relogio(ex["prescription"]) for ex in relido["exercises"]],
        "status": [ex["prescription_status"] for ex in relido["exercises"]],
    }


@pytest.fixture
def prescricao_nos_dois_backends(plan_catalog, raw, monkeypatch, fake_rds, mysql_schema):
    results = {"pymysql": _run_prescricao(plan_catalog)}
    use_dataapi(monkeypatch, fake_rds, mysql_schema)
    results["data-api"] = _run_prescricao(plan_catalog)
    assert fake_rds.calls_of("ExecuteStatement"), "o caminho data-api nao passou pelo dublê"
    return results


def test_paridade_da_prescricao_congelada(prescricao_nos_dois_backends):
    assert prescricao_nos_dois_backends["data-api"] == prescricao_nos_dois_backends["pymysql"]


@pytest.mark.parametrize("backend", ["pymysql", "data-api"])
def test_snapshot_gravado_e_o_mesmo_que_volta_na_releitura(prescricao_nos_dois_backends, backend):
    result = prescricao_nos_dois_backends[backend]
    assert result["criado"] == result["relido"]
    assert result["status"] == ["FROZEN", "FROZEN"]
    # sem par exercicio x equipamento resolvido: default conservador D3 (9)
    assert result["relido"][0]["effective_rpe_ceiling"] == 9
    assert result["relido"][0]["rpe_target"] == "10"
    assert result["relido"][0]["clamps"] == [
        {"field": "rpe_target", "from": 10, "to": 9, "reason": "EQUIPMENT_SAFETY"}
    ]

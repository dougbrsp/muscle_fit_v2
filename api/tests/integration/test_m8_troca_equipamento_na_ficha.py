"""M8 - troca de EQUIPAMENTO de um exercicio numa ficha ja salva
(`app/workout_plan_equipment.py`), independente de como a ficha foi gerada.

O servidor e o dono da prescricao: a troca grava o vinculo e RE-DERIVA a
prescricao da linha pelo mesmo caminho do refresh
(`services._freeze_plan_prescriptions`) - Modo B, legado ou `prescription_json`
NULL. D13a vale como na geracao; a troca fica auditada no snapshot.
"""

from __future__ import annotations

import pytest

from app import services, workout_plan_equipment as wpe
from app.generation import mode_b
from app.models import WorkoutPlanExerciseIn
from tests.support.template_versions import versao_v1


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("GENERATION_ENGINE", "deterministic")
    monkeypatch.setenv("PROGRESSION_ENGINE", "v2")


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    from app import workout_plan_ai

    monkeypatch.setattr(
        workout_plan_ai,
        "gather_ficha_context",
        lambda gym_id, user_id: {"muscle_recovery": [], "gyms": [], "recent_exercise_logs": []},
    )


@pytest.fixture
def gym(db_conn, catalog):
    """Crucifixo na Polia e no Banco reto; Supino reto no Banco reto. Tudo com
    teto 10 e `rest_pause` compativel."""
    with db_conn.cursor() as cur:
        for table in (
            "workout_plans",
            "workout_plan_templates",
            "equipment_technique_compatibility",
            "progression_decisions",
            "progression_tracks",
            "progression_group_states",
        ):
            cur.execute(f"DELETE FROM {table}")
        cur.execute(
            "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
            (catalog.equipment["Banco reto"], catalog.exercises["Crucifixo"]),
        )
        links = {}
        for equipment, exercise in (("Polia", "Crucifixo"), ("Banco reto", "Crucifixo"), ("Banco reto", "Supino reto")):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[equipment]),
            )
            links[(equipment, exercise)] = cur.lastrowid
        cur.execute("UPDATE equipment SET max_safe_rpe = 10")
        for equipment_id in catalog.equipment.values():
            cur.execute(
                "INSERT INTO equipment_technique_compatibility (equipment_id, technique_code, is_compatible) VALUES (%s, 'rest_pause', 1)",
                (equipment_id,),
            )
    db_conn.commit()
    return links


def _template(conn, catalog, items):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Modelo M8', 'equipment_series', 1, %s)",
            (catalog.user_id,),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) VALUES (%s, %s, 1, 'A', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
        slot_id = cur.lastrowid
        for order, item in enumerate(items, start=1):
            columns = ["slot_id", "item_order", "item_kind"] + list(item)
            values = [slot_id, order, "equipment"] + [item[key] for key in item]
            cur.execute(
                f"INSERT INTO workout_plan_template_slot_items ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                tuple(values),
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count, reps_target, "
            "rpe_target, advanced_technique FROM workout_plan_template_slot_items WHERE slot_id = %s ORDER BY item_order",
            (slot_id,),
        )
        slot = {"id": slot_id, "sequence": 1, "name": "A", "slot_type": "normal", "items": list(cur.fetchall())}
    return {"id": template_id, "name": "Modelo M8", "template_type": None, "description": None}, slot


def _item(catalog, exercise="Crucifixo", equipment="Polia", group="Peitoral", **overrides):
    base = {
        "muscle_group_id": catalog.muscle_groups[group],
        "mapping_id": catalog.exercises[exercise],
        "equipment_id": catalog.equipment[equipment],
        "sets_count": 3,
    }
    base.update(overrides)
    return base


def _mode_b_plan(db_conn, catalog, items):
    template, slot = _template(db_conn, catalog, items)
    draft = mode_b.generate_plan_draft_from_equipment_template(
        template=template, slot=slot, gym_id=catalog.gym_id, gym_name=None, plan_date=None,
        user_id=catalog.user_id, min_rest_seconds=60, max_rest_seconds=90,
    )
    exercises = [
        WorkoutPlanExerciseIn(**{k: v for k, v in ex.items() if k in WorkoutPlanExerciseIn.model_fields}).model_dump(exclude_unset=True)
        for ex in draft["exercises"]
    ]
    return services.create_workout_plan(
        {"name": draft["name"], "gym_id": catalog.gym_id, "template_id": template["id"], "template_slot_sequence": 1,
         "exercises": exercises, "raw_payload": draft["raw_payload"]},
        user_id=catalog.user_id,
    )


def _legacy_plan(catalog, gym):
    """Ficha SEM modelo (caminho legado), com o vinculo Crucifixo/Polia."""
    return services.create_workout_plan(
        {
            "name": "Ficha legada",
            "gym_id": catalog.gym_id,
            "exercises": [
                {
                    "sequence": 1,
                    "exercise_name": "Crucifixo",
                    "mapping_id": catalog.exercises["Crucifixo"],
                    "preferred_equipment_id": gym[("Polia", "Crucifixo")],
                    "equipment_name": "Polia",
                    "sets_count": 3,
                    "reps_target": "10-12",
                }
            ],
        },
        user_id=catalog.user_id,
    )


# ---------------------------------------------------------------------------
# Opcoes
# ---------------------------------------------------------------------------


def test_opcoes_sao_do_mesmo_exercicio_com_elegibilidade_d13a(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 8 WHERE id = %s", (catalog.equipment["Banco reto"],))
    db_conn.commit()

    options = wpe.list_equipment_options(plan["id"], row["workout_plan_exercise_id"], catalog.user_id)
    assert options["mapping_id"] == catalog.exercises["Crucifixo"]
    assert options["current_gym_equipment_id"] == gym[("Polia", "Crucifixo")]
    assert options["current_max_safe_rpe"] == 10
    by_link = {item["gym_equipment_id"]: item for item in options["items"]}
    # So associacoes do Crucifixo: o Banco reto do SUPINO nao aparece.
    assert set(by_link) == {gym[("Polia", "Crucifixo")], gym[("Banco reto", "Crucifixo")]}
    assert by_link[gym[("Polia", "Crucifixo")]]["is_current"] is True
    banco = by_link[gym[("Banco reto", "Crucifixo")]]
    assert banco["eligible"] is False and banco["reason"] == "MAX_SAFE_RPE_LOWER" and banco["max_safe_rpe"] == 8


# ---------------------------------------------------------------------------
# Troca
# ---------------------------------------------------------------------------


def test_troca_recongela_so_a_linha_pelo_motor_da_ficha(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog), _item(catalog, exercise="Supino reto", equipment="Banco reto")])
    crucifixo, supino = plan["exercises"]
    frozen_supino = supino["prescription"]

    result = wpe.substitute_equipment(
        plan["id"], crucifixo["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id
    )
    updated = result["exercise"]
    assert updated["mapping_id"] == catalog.exercises["Crucifixo"]  # exercicio NAO muda
    assert updated["preferred_equipment_id"] == gym[("Banco reto", "Crucifixo")]
    assert updated["equipment_name"] == "Banco reto"
    assert updated["requires_user_choice"] is False
    prescription = updated["prescription"]
    assert prescription["engine_version"] == "mode-b-deterministic-v1"  # mesmo motor da ficha
    assert prescription["source"] == "REFRESH"
    assert "EQUIPMENT_SUBSTITUTED" in prescription["warnings"]
    assert prescription["substitution"]["kind"] == "EQUIPMENT"
    assert prescription["substitution"]["reason"] == "USER_CHOICE"
    assert prescription["substitution"]["from_equipment_id"] == catalog.equipment["Polia"]
    assert prescription["substitution"]["to_equipment_id"] == catalog.equipment["Banco reto"]
    assert prescription["substitution"]["to_gym_equipment_id"] == gym[("Banco reto", "Crucifixo")]
    # A outra linha nao foi tocada.
    assert result["plan"]["exercises"][1]["prescription"] == frozen_supino
    # A leitura devolve o mesmo que a troca devolveu.
    again = services.get_workout_plan(plan["id"], user_id=catalog.user_id)["exercises"][0]
    assert again["prescription"] == prescription


def test_troca_em_ficha_legada_sem_prescription_json(db_conn, catalog, gym):
    plan = _legacy_plan(catalog, gym)
    row = plan["exercises"][0]
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_exercises SET prescription_json = NULL, engine_version = NULL WHERE id = %s",
            (row["workout_plan_exercise_id"],),
        )
    db_conn.commit()
    assert services.get_workout_plan(plan["id"], user_id=catalog.user_id)["exercises"][0]["prescription_status"] == "LEGACY"

    result = wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id)
    updated = result["exercise"]
    assert updated["preferred_equipment_id"] == gym[("Banco reto", "Crucifixo")]
    assert updated["prescription_status"] == "FROZEN"
    # M76 Lote 1a: o recongelamento de linha sem Modelo usa o motor v2 por par.
    assert updated["prescription"]["engine_version"] == "pair-suggestion-v2"
    assert updated["prescription"]["substitution"]["reason"] == "USER_CHOICE"
    assert "EQUIPMENT_SUBSTITUTED" in updated["prescription"]["warnings"]


def test_item_que_exigia_escolha_fica_resolvido(db_conn, catalog, gym):
    """Agachamento (Quadriceps) sem equipamento na academia -> a geracao o
    deixou `requires_user_choice`. A academia ganha o Rack; a troca na ficha
    resolve o item sem passar pelo modo treino."""
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog, exercise="Agachamento", equipment="Rack", group="Quadríceps")])
    row = plan["exercises"][0]
    assert row["requires_user_choice"] is True
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
            (catalog.gym_id, catalog.exercises["Agachamento"], catalog.equipment["Rack"]),
        )
        rack = cur.lastrowid
    db_conn.commit()

    options = wpe.list_equipment_options(plan["id"], row["workout_plan_exercise_id"], catalog.user_id)
    assert options["current_gym_equipment_id"] is None
    assert [item["gym_equipment_id"] for item in options["items"]] == [rack]

    updated = wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], rack, catalog.user_id)["exercise"]
    assert updated["requires_user_choice"] is False
    assert "EQUIPMENT_UNRESOLVED" not in updated["prescription"]["warnings"]
    assert updated["prescription"]["engine_version"] == "mode-b-deterministic-v1"
    assert updated["prescription"]["substitution"]["from_equipment_id"] is None


def test_put_posterior_preserva_a_troca(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id)
    fresh = services.get_workout_plan(plan["id"], user_id=catalog.user_id)
    exercises = [
        WorkoutPlanExerciseIn(**{k: v for k, v in ex.items() if k in WorkoutPlanExerciseIn.model_fields}).model_dump(exclude_unset=True)
        for ex in fresh["exercises"]
    ]
    saved = services.update_workout_plan(
        plan["id"],
        {"name": "editada", "gym_id": catalog.gym_id, "template_id": fresh["template_id"], "template_slot_sequence": 1, "exercises": exercises},
        user_id=catalog.user_id,
    )
    assert saved["exercises"][0]["preferred_equipment_id"] == gym[("Banco reto", "Crucifixo")]
    assert saved["exercises"][0]["prescription"]["substitution"]["reason"] == "USER_CHOICE"


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def _expect(exc_type, reason, fn):
    with pytest.raises(exc_type) as info:
        fn()
    assert info.value.reason == reason
    return info.value


def test_d13a_recusa_teto_menor(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 8 WHERE id = %s", (catalog.equipment["Banco reto"],))
    db_conn.commit()
    err = _expect(
        wpe.PlanExerciseSubstitutionError,
        "max_safe_rpe_lower",
        lambda: wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id),
    )
    assert err.status == 422
    # Nada gravado.
    assert services.get_workout_plan(plan["id"], user_id=catalog.user_id)["exercises"][0]["preferred_equipment_id"] == gym[("Polia", "Crucifixo")]


def test_tecnica_prescrita_incompativel_recusa(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    row = plan["exercises"][0]
    assert row["advanced_technique"] == "rest_pause"
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE equipment_technique_compatibility SET is_compatible = 0 WHERE equipment_id = %s AND technique_code = 'rest_pause'",
            (catalog.equipment["Banco reto"],),
        )
    db_conn.commit()
    options = wpe.list_equipment_options(plan["id"], row["workout_plan_exercise_id"], catalog.user_id)
    banco = next(i for i in options["items"] if i["gym_equipment_id"] == gym[("Banco reto", "Crucifixo")])
    assert banco["eligible"] is False and banco["reason"] == "TECHNIQUE_INCOMPATIBLE"
    err = _expect(
        wpe.PlanExerciseSubstitutionError,
        "technique_incompatible",
        lambda: wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id),
    )
    assert err.status == 422


def test_par_incoerente_recusado(db_conn, catalog, gym):
    """O Banco reto do SUPINO nao serve a linha do Crucifixo (D13)."""
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    _expect(
        wpe.PlanExerciseSubstitutionError,
        "incoherent_pair",
        lambda: wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Supino reto")], catalog.user_id),
    )


def test_equipamento_de_outra_academia_recusado(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
            (catalog.other_gym_id, catalog.exercises["Crucifixo"], catalog.equipment["Banco reto"]),
        )
        other = cur.lastrowid
    db_conn.commit()
    _expect(
        wpe.PlanExerciseSubstitutionError,
        "equipment_not_in_gym",
        lambda: wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], other, catalog.user_id),
    )


def test_mesmo_equipamento_e_409(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    err = _expect(
        wpe.PlanExerciseSubstitutionError,
        "same_equipment",
        lambda: wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Polia", "Crucifixo")], catalog.user_id),
    )
    assert err.status == 409


def test_ficha_concluida_recusa_como_o_refresh(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    with db_conn.cursor() as cur:
        cur.execute("UPDATE workout_plans SET status = 'completed' WHERE id = %s", (plan["id"],))
    db_conn.commit()
    _expect(
        services.PlanPrescriptionRefreshError,
        "plan_not_planned",
        lambda: wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id),
    )


def test_ficha_de_outro_usuario_e_404(db_conn, catalog, gym):
    plan = _mode_b_plan(db_conn, catalog, [_item(catalog)])
    row = plan["exercises"][0]
    with pytest.raises(services.PlanNotFoundError):
        wpe.list_equipment_options(plan["id"], row["workout_plan_exercise_id"], catalog.user_id + 1)
    with pytest.raises(services.PlanNotFoundError):
        wpe.substitute_equipment(plan["id"], row["workout_plan_exercise_id"], gym[("Banco reto", "Crucifixo")], catalog.user_id + 1)

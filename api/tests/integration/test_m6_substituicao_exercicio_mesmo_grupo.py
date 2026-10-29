"""M6 ponta a ponta no Modo B deterministico: academia sem NENHUM
equipamento para o exercicio do modelo -> outro exercicio do MESMO grupo
muscular, escolhido por ranking deterministico, com a ficha tecnica do
exercicio CERTO; o usuario so decide quando nem isso existe (D13 b).

Regressao do bug da ficha 110 (2026-09-17): a troca por grupo do motor
legado devolvia o nome/descricao de OUTRO exercicio. Aqui o candidato E o
exercicio - nome, Amazfit e descricao vem do `exercise_id` substituto.
"""

from __future__ import annotations

import pytest

from app import services
from app.domain.equipment_pair import is_pair_coherent
from app.generation import mode_b
from app.models import WorkoutPlanExerciseIn
from tests.support.template_versions import versao_v1

DESCRICAO_SUPINO = "Deite no banco, desca a barra ate o peito e empurre."
DESCRICAO_CRUCIFIXO = "Abra e feche os bracos a frente do peito."


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
def academia(db_conn, catalog):
    """A academia tem SO o Banco reto, e so para o Supino reto (Peitoral).
    O modelo vai pedir Crucifixo (Peitoral) no Rack - nem o Rack nem
    qualquer outro equipamento do Crucifixo existem aqui."""
    ex, eq = catalog.exercises, catalog.equipment
    with db_conn.cursor() as cur:
        for table in (
            "workout_plans",
            "workout_plan_templates",
            "equipment_technique_compatibility",
            "progression_decisions",
            "progression_tracks",
            "progression_group_states",
            "user_equipment_exercise_preferences",
        ):
            cur.execute(f"DELETE FROM {table}")
        cur.execute("UPDATE equipment SET max_safe_rpe = 10")
        cur.execute("UPDATE exercise_catalog SET description = %s WHERE id = %s", (DESCRICAO_SUPINO, ex["Supino reto"]))
        cur.execute("UPDATE exercise_catalog SET description = %s WHERE id = %s", (DESCRICAO_CRUCIFIXO, ex["Crucifixo"]))
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
            (catalog.gym_id, ex["Supino reto"], eq["Banco reto"]),
        )
        supino_banco = cur.lastrowid
    db_conn.commit()
    return {"supino_banco": supino_banco}


def _template(conn, catalog, items):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Modelo M6', 'equipment_series', 1, %s)",
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
                f"INSERT INTO workout_plan_template_slot_items ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})",
                tuple(values),
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count, "
            "reps_target, rpe_target, advanced_technique FROM workout_plan_template_slot_items "
            "WHERE slot_id = %s ORDER BY item_order",
            (slot_id,),
        )
        slot = {"id": slot_id, "sequence": 1, "name": "A", "slot_type": "normal", "items": list(cur.fetchall())}
    return {"id": template_id, "name": "Modelo M6", "template_type": None, "description": None}, slot


def _item(catalog, exercise="Crucifixo", equipment="Rack", group="Peitoral", **overrides):
    base = {
        "muscle_group_id": catalog.muscle_groups[group],
        "mapping_id": catalog.exercises[exercise],
        "equipment_id": catalog.equipment[equipment],
        "sets_count": 4,
        "rpe_target": "9",
    }
    base.update(overrides)
    return base


def _generate(template, slot, catalog):
    return mode_b.generate_plan_draft_from_equipment_template(
        template=template,
        slot=slot,
        gym_id=catalog.gym_id,
        gym_name=None,
        plan_date=None,
        user_id=catalog.user_id,
        min_rest_seconds=60,
        max_rest_seconds=90,
    )


def _save(draft, catalog, template_id):
    exercises = [
        WorkoutPlanExerciseIn(
            **{key: value for key, value in exercise.items() if key in WorkoutPlanExerciseIn.model_fields}
        ).model_dump(exclude_unset=True)
        for exercise in draft["exercises"]
    ]
    return services.create_workout_plan(
        {
            "name": draft["name"],
            "gym_id": catalog.gym_id,
            "template_id": template_id,
            "template_slot_sequence": 1,
            "exercises": exercises,
            "raw_payload": draft["raw_payload"],
        },
        user_id=catalog.user_id,
    )


# ---------------------------------------------------------------------------
# 1. A troca de exercicio acontece antes de cair no usuario
# ---------------------------------------------------------------------------


def test_troca_o_exercicio_por_outro_do_mesmo_grupo(db_conn, catalog, academia):
    template, slot = _template(db_conn, catalog, [_item(catalog)])
    draft = _generate(template, slot, catalog)
    exercise = draft["exercises"][0]

    assert exercise["mapping_id"] == catalog.exercises["Supino reto"]
    assert exercise["exercise_name"] == "Supino reto"  # nome do exercicio CERTO
    assert exercise["equipment_id"] == catalog.equipment["Banco reto"]
    assert exercise["equipment_name"] == "Banco reto"
    assert exercise["preferred_equipment_id"] == academia["supino_banco"]
    assert exercise["requires_user_choice"] is False
    # O que e do MODELO (do slot, nao do exercicio) permanece.
    assert exercise["sets_count"] == 4
    assert exercise["muscle_primary"] == "Peitoral"
    # Auditoria da secao 14: de onde para onde, e por que.
    [audit] = draft["raw_payload"]["generation_audit"]["substitutions"]
    assert audit["kind"] == "EXERCISE"
    assert audit["reason"] == "EXERCISE_UNAVAILABLE_IN_GYM"
    assert audit["from_exercise_id"] == catalog.exercises["Crucifixo"]
    assert audit["to_exercise_id"] == catalog.exercises["Supino reto"]
    assert audit["to_gym_equipment_id"] == academia["supino_banco"]
    assert any("Crucifixo" in w and "substituído automaticamente por Supino reto" in w for w in draft["raw_payload"]["volume_planning"]["warnings"])
    with db_conn.cursor() as cur:
        assert is_pair_coherent(cur, exercise["mapping_id"], exercise["preferred_equipment_id"])


def test_ficha_salva_traz_a_descricao_do_substituto(db_conn, catalog, academia):
    """Regressao da ficha 110: a descricao e do exercicio que vai ser feito,
    nunca a do exercicio do modelo."""
    template, slot = _template(db_conn, catalog, [_item(catalog)])
    plan = _save(_generate(template, slot, catalog), catalog, template["id"])
    row = plan["exercises"][0]
    assert row["exercise_name"] == "Supino reto"
    assert row["exercise_description"] == DESCRICAO_SUPINO
    assert row["exercise_description"] != DESCRICAO_CRUCIFIXO


def test_equipamento_do_mesmo_exercicio_continua_tendo_prioridade(db_conn, catalog, academia):
    """D13 (a) vem antes do M6: se a academia tiver OUTRO equipamento do
    proprio Crucifixo, e ele que entra - o exercicio nao muda."""
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
            (catalog.gym_id, catalog.exercises["Crucifixo"], catalog.equipment["Polia"]),
        )
    db_conn.commit()
    template, slot = _template(db_conn, catalog, [_item(catalog)])
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["mapping_id"] == catalog.exercises["Crucifixo"]
    assert exercise["equipment_id"] == catalog.equipment["Polia"]
    [audit] = _generate(template, slot, catalog)["raw_payload"]["generation_audit"]["substitutions"]
    assert audit["kind"] == "EQUIPMENT"


# ---------------------------------------------------------------------------
# 2. Restricoes D13a
# ---------------------------------------------------------------------------


def test_candidato_menos_seguro_e_recusado_e_cai_no_usuario(db_conn, catalog, academia):
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 8 WHERE id = %s", (catalog.equipment["Banco reto"],))
    db_conn.commit()
    template, slot = _template(db_conn, catalog, [_item(catalog)])
    draft = _generate(template, slot, catalog)
    exercise = draft["exercises"][0]
    assert exercise["mapping_id"] == catalog.exercises["Crucifixo"]
    assert exercise["requires_user_choice"] is True
    assert exercise["preferred_equipment_id"] is None
    [audit] = draft["raw_payload"]["generation_audit"]["substitutions"]
    assert any(r["reason"] == "MAX_SAFE_RPE_LOWER" and r["exercise_id"] == catalog.exercises["Supino reto"] for r in audit["rejected"])


def test_candidato_incompativel_com_a_tecnica_fixa_e_recusado(db_conn, catalog, academia):
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO equipment_technique_compatibility (equipment_id, technique_code, is_compatible) VALUES (%s, 'rest_pause', 0)",
            (catalog.equipment["Banco reto"],),
        )
    db_conn.commit()
    template, slot = _template(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["requires_user_choice"] is True


def test_nao_repete_exercicio_que_ja_esta_no_slot(db_conn, catalog, academia):
    """Supino reto ja e o item 1 do slot: o Crucifixo (item 2) nao pode virar
    um segundo Supino reto -> cai no usuario."""
    template, slot = _template(
        db_conn, catalog, [_item(catalog, exercise="Supino reto", equipment="Banco reto"), _item(catalog)]
    )
    supino, crucifixo = _generate(template, slot, catalog)["exercises"]
    assert supino["mapping_id"] == catalog.exercises["Supino reto"]
    assert crucifixo["mapping_id"] == catalog.exercises["Crucifixo"]
    assert crucifixo["requires_user_choice"] is True


def test_grupo_diferente_nunca_e_candidato(db_conn, catalog, academia):
    """Agachamento (Quadriceps) sem equipamento: o Banco reto (Peitoral) nao
    serve de substituto - grupo muscular e preservado."""
    template, slot = _template(db_conn, catalog, [_item(catalog, exercise="Agachamento", group="Quadríceps")])
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["mapping_id"] == catalog.exercises["Agachamento"]
    assert exercise["requires_user_choice"] is True


# ---------------------------------------------------------------------------
# 3. Determinismo e ranking por estrelas
# ---------------------------------------------------------------------------


def test_determinismo_mesma_entrada_mesma_escolha(db_conn, catalog, academia):
    template, slot = _template(db_conn, catalog, [_item(catalog)])
    primeiro = _generate(template, slot, catalog)
    segundo = _generate(template, slot, catalog)
    assert primeiro["exercises"] == segundo["exercises"]
    assert primeiro["raw_payload"]["generation_audit"] == segundo["raw_payload"]["generation_audit"]
    assert primeiro["raw_payload"]["generation_audit"]["llm_used"] is False


def test_estrelas_do_par_decidem_entre_candidatos(db_conn, catalog, academia):
    """Dois candidatos do grupo: Supino reto no Banco reto (sem nota = 3) e
    Supino reto na Polia... nao - o ranking e por PAR e o exercicio ja
    saiu; entao um segundo EXERCICIO de Peitoral: 'Peck deck' na Polia.
    Com 5 estrelas no par (Peck deck, Polia), ele vence o Supino."""
    ex, eq = catalog.exercises, catalog.equipment
    with db_conn.cursor() as cur:
        peck = max(ex.values()) + 1
        cur.execute("INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, 'Peck deck', 'Peck deck')", (peck,))
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Peck deck')", (peck,))
        cur.execute(
            "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
            "VALUES (%s, %s, 'PRIMARY', 1.0, 1.0)",
            (peck, catalog.muscle_groups["Peitoral"]),
        )
        cur.execute("INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)", (eq["Polia"], peck))
        pair_id = cur.lastrowid
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
            (catalog.gym_id, peck, eq["Polia"]),
        )
    db_conn.commit()
    template, slot = _template(db_conn, catalog, [_item(catalog)])

    # Sem nota: desempate por nome -> "Peck deck" < "Supino reto".
    assert _generate(template, slot, catalog)["exercises"][0]["mapping_id"] == peck

    # Supino com 5 estrelas: vence.
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM equipment_exercises WHERE equipment_id = %s AND exercise_id = %s", (eq["Banco reto"], ex["Supino reto"]))
        supino_pair = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO user_equipment_exercise_preferences (user_id, equipment_exercise_id, stars) VALUES (%s, %s, 5)",
            (catalog.user_id, supino_pair),
        )
    db_conn.commit()
    assert _generate(template, slot, catalog)["exercises"][0]["mapping_id"] == ex["Supino reto"]

    # Peck deck com 5 tambem: empate em estrelas -> nome de novo.
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_equipment_exercise_preferences (user_id, equipment_exercise_id, stars) VALUES (%s, %s, 5)",
            (catalog.user_id, pair_id),
        )
    db_conn.commit()
    assert _generate(template, slot, catalog)["exercises"][0]["mapping_id"] == peck


# ---------------------------------------------------------------------------
# 4. Congelamento: a linha substituida continua sendo item do Modo B
# ---------------------------------------------------------------------------


def test_congelamento_mantem_o_motor_deterministico_e_audita_a_troca(db_conn, catalog, academia):
    template, slot = _template(db_conn, catalog, [_item(catalog, exercise="Supino reto", equipment="Banco reto", sets_count=2), _item(catalog)])
    # Item 1 (Supino) ja ocupa o Supino; para o item 2 (Crucifixo) sobrar um
    # substituto, a academia ganha um Peck deck na Polia.
    ex, eq = catalog.exercises, catalog.equipment
    with db_conn.cursor() as cur:
        peck = max(ex.values()) + 1
        cur.execute("INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, 'Peck deck', 'Peck deck')", (peck,))
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Peck deck')", (peck,))
        cur.execute(
            "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
            "VALUES (%s, %s, 'PRIMARY', 1.0, 1.0)",
            (peck, catalog.muscle_groups["Peitoral"]),
        )
        cur.execute("INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)", (eq["Polia"], peck))
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
            (catalog.gym_id, peck, eq["Polia"]),
        )
        peck_polia = cur.lastrowid
    db_conn.commit()

    plan = _save(_generate(template, slot, catalog), catalog, template["id"])
    supino, substituido = plan["exercises"]

    assert substituido["mapping_id"] == peck
    prescription = substituido["prescription"]
    assert prescription["engine_version"] == "mode-b-deterministic-v1"
    assert prescription["sets_count"] == 4  # series do ITEM 2 do modelo, nao do item 1
    assert "EQUIPMENT_SUBSTITUTED" in prescription["warnings"]
    assert prescription["substitution"]["kind"] == "EXERCISE"
    assert prescription["substitution"]["reason"] == "EXERCISE_UNAVAILABLE_IN_GYM"
    assert prescription["substitution"]["from_exercise_id"] == ex["Crucifixo"]
    assert prescription["substitution"]["to_exercise_id"] == peck
    assert prescription["substitution"]["to_gym_equipment_id"] == peck_polia
    assert substituido["requires_user_choice"] is False
    # O item nao substituido nao ganha auditoria nenhuma.
    assert supino["prescription"]["substitution"] is None
    assert "EQUIPMENT_SUBSTITUTED" not in supino["prescription"]["warnings"]

    # O refresh re-deriva a MESMA coisa (a auditoria sobrevive).
    services.refresh_plan_prescription(plan["id"], user_id=catalog.user_id)
    fresh = services.get_workout_plan(plan["id"], user_id=catalog.user_id)["exercises"][1]
    assert fresh["prescription"]["engine_version"] == "mode-b-deterministic-v1"
    assert fresh["prescription"]["substitution"]["kind"] == "EXERCISE"
    assert fresh["prescription"]["source"] == "REFRESH"


def test_linha_acrescentada_a_mao_nao_e_confundida_com_substituicao(db_conn, catalog, academia):
    """Uma linha que o usuario acrescentou ao rascunho, de grupo DIFERENTE
    do item na mesma posicao, nao e casada com o modelo: fica no legado."""
    template, slot = _template(db_conn, catalog, [_item(catalog, exercise="Supino reto", equipment="Banco reto")])
    draft = _generate(template, slot, catalog)
    draft["exercises"] = [
        {
            "sequence": 1,
            "exercise_name": "Remada baixa",
            "mapping_id": catalog.exercises["Remada baixa"],
            "muscle_primary": "Costas",
            "sets_count": 3,
            "warmup": False,
        }
    ]
    plan = _save(draft, catalog, template["id"])
    # M76 Lote 1a: linha fora do Modo B nao e mais do motor legado - e o motor
    # v2 por par (`prescription_service`), sem `substitution`.
    prescricao = plan["exercises"][0]["prescription"]
    assert prescricao["engine_version"] == "pair-suggestion-v2"
    assert prescricao["substitution"] is None

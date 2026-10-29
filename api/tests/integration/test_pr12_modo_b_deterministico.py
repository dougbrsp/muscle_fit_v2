"""PR-12 - Modo B deterministico ponta a ponta (PLANO_20260912.md 6.2, 5.9,
13 "Geracao Modo B"; decisoes D9, D13, D14).

Cobre o bloco de testes do Modo B da secao 13:

- preservacao integral dos campos do modelo;
- equipamento ausente nos DOIS caminhos da D13 (substituicao pelo mesmo
  `exercise_id` / `requires_user_choice`), incluindo a recusa de um
  equipamento MENOS seguro;
- tecnica fixa valida; incompativel; `requires_failure` em par proibido;
- `rpe_target=10` fixo em par proibido (clamp + aviso, sem bloquear);
- conflito entre tecnica fixa e tecnica da progressao;
- round-trip POST/PUT/GET dos 10 campos avancados com `prescription_json`
  preservado;
- **determinismo**: mesma entrada -> mesma ficha, `llm_used=false`;
- validacao de save do builder (D14);
- e a prova do rollback: com `GENERATION_ENGINE=legacy` o motor antigo roda.
"""

from __future__ import annotations

import pytest

from app import services
from app.generation import mode_b
from app.models import WorkoutPlanExerciseIn, WorkoutPlanTemplateIn
from tests.support.template_versions import versao_v1

ENGINE_DETERMINISTIC = "mode-b-deterministic-v1"
ENGINE_LEGACY = "equipment_series"

# Os 10 campos avancados do item do modelo que a secao 6.2 manda copiar
# INTEGRALMENTE (o modelo manda neles; a progressao nunca os toca).
MODEL_FIELDS = {
    "advanced_technique_applications": 3,
    "advanced_technique_duration_seconds": 20,
    "warmup_sets_count": 2,
    "warmup_reps": 15,
    "warmup_rest_seconds": 45,
    "feeler_set_sets_count": 1,
    "feeler_set_reps": 5,
    "feeler_set_rest_seconds": 60,
    "peak_contraction_seconds": 3,
}


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """DEV do PR-12: as duas flags ligadas. Cada teste que quiser o legado
    apaga `GENERATION_ENGINE`."""
    monkeypatch.setenv("GENERATION_ENGINE", "deterministic")
    monkeypatch.setenv("PROGRESSION_ENGINE", "v2")


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """`gather_ficha_context` faz chamadas HTTP internas (historico, academias,
    recuperacao). Aqui so a recuperacao importa, e ela entra vazia = READY
    para todos os grupos."""
    from app import workout_plan_ai

    monkeypatch.setattr(
        workout_plan_ai,
        "gather_ficha_context",
        lambda gym_id, user_id: {"muscle_recovery": [], "gyms": [], "recent_exercise_logs": []},
    )


@pytest.fixture
def gym_equipment(db_conn, catalog):
    """Academia com Crucifixo na Polia e no Banco reto (duas alternativas para
    o MESMO exercicio) e Supino reto no Banco reto."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plans")
        cur.execute("DELETE FROM workout_plan_templates")
        # Nao entram em CATALOG_TABLES (nao sao do catalogo minimo), entao
        # sobrevivem ao TRUNCATE de `catalog` e precisam sair daqui.
        cur.execute("DELETE FROM equipment_technique_compatibility")
        cur.execute("DELETE FROM progression_decisions")
        cur.execute("DELETE FROM progression_tracks")
        cur.execute("DELETE FROM progression_group_states")
        cur.execute(
            "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
            (catalog.equipment["Banco reto"], catalog.exercises["Crucifixo"]),
        )
        ids = {}
        for equipment, exercise in (
            ("Polia", "Crucifixo"),
            ("Banco reto", "Crucifixo"),
            ("Banco reto", "Supino reto"),
        ):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[equipment]),
            )
            ids[(equipment, exercise)] = cur.lastrowid
        # Base permissiva: teto 10 e as duas tecnicas auto_applicable
        # marcadas compativeis em todo equipamento. Os testes que falam de
        # RPE/tecnica apertam isso explicitamente - assim cada restricao
        # aparece no teste que a exercita, nao no fixture.
        cur.execute("UPDATE equipment SET max_safe_rpe = 10")
        for equipment_id in catalog.equipment.values():
            for code in ("rest_pause", "drop_set"):
                cur.execute(
                    "INSERT INTO equipment_technique_compatibility (equipment_id, technique_code, is_compatible) "
                    "VALUES (%s, %s, 1)",
                    (equipment_id, code),
                )
    db_conn.commit()
    return ids


def _make_template(conn, catalog, items, *, name="Modelo B") -> tuple[dict, dict]:
    """Modelo `equipment_series` com um slot e os `items` informados."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES (%s, 'equipment_series', 1, %s)",
            (name, catalog.user_id),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 1, 'A', 'normal')",
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
    template = {"id": template_id, "name": name, "template_type": None, "description": None}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count, "
            "reps_target, rpe_target, advanced_technique, advanced_technique_applications, "
            "advanced_technique_duration_seconds, warmup_sets_count, warmup_reps, warmup_rest_seconds, "
            "feeler_set_sets_count, feeler_set_reps, feeler_set_rest_seconds, peak_contraction_seconds, "
            "rest_seconds "
            "FROM workout_plan_template_slot_items WHERE slot_id = %s ORDER BY item_order",
            (slot_id,),
        )
        slot = {"id": slot_id, "sequence": 1, "name": "A", "slot_type": "normal", "items": list(cur.fetchall())}
    return template, slot


def _generate(template, slot, catalog, **kwargs):
    return mode_b.generate_plan_draft_from_equipment_template(
        template=template,
        slot=slot,
        gym_id=kwargs.pop("gym_id", catalog.gym_id),
        gym_name=None,
        plan_date=None,
        user_id=catalog.user_id,
        min_rest_seconds=60,
        max_rest_seconds=90,
        **kwargs,
    )


def _item(catalog, exercise="Crucifixo", equipment="Polia", **overrides):
    base = {
        "muscle_group_id": catalog.muscle_groups["Peitoral"],
        "mapping_id": catalog.exercises[exercise],
        "equipment_id": catalog.equipment[equipment],
        "sets_count": 3,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. O modelo manda nos campos dele
# ---------------------------------------------------------------------------


def test_preservacao_integral_dos_campos_do_modelo(db_conn, catalog, gym_equipment):
    template, slot = _make_template(
        db_conn, catalog, [_item(catalog, sets_count=4, advanced_technique="rest_pause", **MODEL_FIELDS)]
    )
    draft = _generate(template, slot, catalog)
    exercise = draft["exercises"][0]

    assert exercise["sets_count"] == 4  # series sao do MODELO
    assert exercise["rest_seconds"] == 75  # media do intervalo pedido
    assert exercise["advanced_technique"] == "rest_pause"
    for field, value in MODEL_FIELDS.items():
        assert exercise[field] == value, field
    assert exercise["mapping_id"] == catalog.exercises["Crucifixo"]
    assert exercise["equipment_id"] == catalog.equipment["Polia"]
    assert exercise["muscle_group_id"] == catalog.muscle_groups["Peitoral"]
    assert draft["raw_payload"]["engine_version"] == ENGINE_DETERMINISTIC
    assert draft["raw_payload"]["generation_audit"]["llm_used"] is False


@pytest.mark.parametrize("rest_do_modelo, esperado", [(None, 60), (45, 45), (150, 150)])
def test_descanso_do_modelo_sobrepoe_o_intervalo_sem_limite(
    db_conn, catalog, gym_equipment, rest_do_modelo, esperado
):
    """Fase 7, Frente B: `rest_seconds` do item vence o intervalo da geracao
    (30-90), inclusive fora dele (150); sem valor, vale o ponto medio (60)."""
    extra = {} if rest_do_modelo is None else {"rest_seconds": rest_do_modelo}
    template, slot = _make_template(db_conn, catalog, [_item(catalog, **extra)])
    draft = mode_b.generate_plan_draft_from_equipment_template(
        template=template,
        slot=slot,
        gym_id=catalog.gym_id,
        gym_name=None,
        plan_date=None,
        user_id=catalog.user_id,
        min_rest_seconds=30,
        max_rest_seconds=90,
    )
    assert draft["exercises"][0]["rest_seconds"] == esperado


def test_a_progressao_manda_em_reps_e_carga(db_conn, catalog, gym_equipment):
    """Trilha nova = calibracao: faixa da fase F1 (6-8) e carga ainda `null`
    (a UI pede a carga que atinja o alvo, secao 5.10)."""
    template, slot = _make_template(db_conn, catalog, [_item(catalog, reps_target="20-30")])
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["reps_target"] == "6-8"  # NAO o "20-30" do modelo
    assert exercise["suggested_load_kg"] is None
    assert exercise["load_target"] is None


def test_carga_e_faixa_saem_da_trilha_persistida(db_conn, catalog, gym_equipment):
    """Com trilha e fase gravadas (o que o `finish` com `PROGRESSION_ENGINE=v2`
    mantem), a prescricao vem DELAS."""
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO progression_group_states (user_id, muscle_group_id, phase, reps_min, reps_max, "
            "phase_entered_at, engine_version) VALUES (%s, %s, 'F2', 10, 12, NOW(), 'progression-v2.0.0')",
            (catalog.user_id, catalog.muscle_groups["Peitoral"]),
        )
        cur.execute(
            "INSERT INTO progression_tracks (user_id, exercise_id, equipment_id, muscle_group_id, load_status, "
            "working_load_kg, sets_count, engine_version) "
            "VALUES (%s, %s, %s, %s, 'ACTIVE', 42.5, 3, 'progression-v2.0.0')",
            (
                catalog.user_id,
                catalog.exercises["Crucifixo"],
                catalog.equipment["Polia"],
                catalog.muscle_groups["Peitoral"],
            ),
        )
    db_conn.commit()
    template, slot = _make_template(db_conn, catalog, [_item(catalog)])
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["reps_target"] == "10-12"
    assert exercise["suggested_load_kg"] == 42.5
    assert exercise["load_target"] == "42,5kg"


# ---------------------------------------------------------------------------
# 2. D13 - equipamento indisponivel na academia
# ---------------------------------------------------------------------------


def test_substitui_por_outro_equipamento_do_mesmo_exercicio(db_conn, catalog, gym_equipment):
    """(a) O equipamento do modelo ("Rack") nao esta na academia; existe outro
    para o MESMO exercicio -> substituicao + aviso. O EXERCICIO nao muda."""
    template, slot = _make_template(db_conn, catalog, [_item(catalog, equipment="Rack")])
    draft = _generate(template, slot, catalog)
    exercise = draft["exercises"][0]

    assert exercise["mapping_id"] == catalog.exercises["Crucifixo"]  # exercicio preservado
    assert exercise["equipment_id"] in (catalog.equipment["Polia"], catalog.equipment["Banco reto"])
    assert exercise["preferred_equipment_id"] is not None
    assert exercise["requires_user_choice"] is False
    assert any("substituído automaticamente" in w for w in draft["raw_payload"]["volume_planning"]["warnings"])
    assert draft["raw_payload"]["generation_audit"]["substitutions"]


def test_sem_alternativa_o_item_exige_escolha_do_usuario(db_conn, catalog, gym_equipment):
    """(b) A academia nao tem NENHUM equipamento para o Agachamento:
    `requires_user_choice=True` e `preferred_equipment_id=None`. Nada de
    trocar o exercicio por outro do grupo (era o `_recommend_for_group`)."""
    template, slot = _make_template(
        db_conn,
        catalog,
        [
            _item(
                catalog,
                exercise="Agachamento",
                equipment="Rack",
                muscle_group_id=catalog.muscle_groups["Quadríceps"],
            )
        ],
    )
    draft = _generate(template, slot, catalog)
    exercise = draft["exercises"][0]

    assert exercise["mapping_id"] == catalog.exercises["Agachamento"]  # exercicio NAO trocado
    assert exercise["preferred_equipment_id"] is None
    assert exercise["equipment_id"] is None
    assert exercise["requires_user_choice"] is True
    assert any("Escolha o equipamento no modo treino" in w for w in draft["raw_payload"]["volume_planning"]["warnings"])


def test_substituicao_nunca_degrada_a_seguranca(db_conn, catalog, gym_equipment):
    """A alternativa so vale com `max_safe_rpe` >= o do original. Banco reto
    com teto 8 contra um original de teto 10 e recusado -> cai em (b)."""
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 8 WHERE id = %s", (catalog.equipment["Banco reto"],))
        cur.execute(
            "DELETE FROM gym_equipment WHERE gym_id = %s AND equipment_id = %s",
            (catalog.gym_id, catalog.equipment["Polia"]),
        )
    db_conn.commit()
    template, slot = _make_template(db_conn, catalog, [_item(catalog, equipment="Rack")])
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["requires_user_choice"] is True


def test_substituicao_respeita_a_tecnica_fixa(db_conn, catalog, gym_equipment):
    """Alternativa incompativel com a tecnica fixa do item nao serve."""
    with db_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM gym_equipment WHERE gym_id = %s AND equipment_id = %s",
            (catalog.gym_id, catalog.equipment["Polia"]),
        )
        # Banco reto passa a NEGAR rest_pause (a linha do par vence o
        # default permissivo do fixture).
        cur.execute(
            "UPDATE equipment_technique_compatibility SET is_compatible = 0 "
            "WHERE equipment_id = %s AND technique_code = 'rest_pause'",
            (catalog.equipment["Banco reto"],),
        )
    db_conn.commit()
    template, slot = _make_template(
        db_conn, catalog, [_item(catalog, equipment="Rack", advanced_technique="rest_pause")]
    )
    exercise = _generate(template, slot, catalog)["exercises"][0]
    assert exercise["requires_user_choice"] is True


# ---------------------------------------------------------------------------
# 3. Tecnicas (secao 5.9) e RPE (secao 6.2 passo 3)
# ---------------------------------------------------------------------------


def _mark_compatible(conn, equipment_id, code, compatible=1):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO equipment_technique_compatibility (equipment_id, technique_code, is_compatible) "
            "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE is_compatible = VALUES(is_compatible)",
            (equipment_id, code, compatible),
        )
    conn.commit()


def test_tecnica_fixa_valida_permanece_na_prescricao(db_conn, catalog, gym_equipment):
    _mark_compatible(db_conn, catalog.equipment["Polia"], "rest_pause", 1)
    template, slot = _make_template(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    draft = _generate(template, slot, catalog)
    assert draft["exercises"][0]["advanced_technique"] == "rest_pause"
    assert not any("técnica avançada" in w for w in draft["raw_payload"]["volume_planning"]["warnings"])


def test_tecnica_fixa_incompativel_e_removida_com_aviso(db_conn, catalog, gym_equipment):
    _mark_compatible(db_conn, catalog.equipment["Polia"], "rest_pause", 0)
    template, slot = _make_template(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    draft = _generate(template, slot, catalog)
    assert draft["exercises"][0]["advanced_technique"] is None
    assert any("não é válida para este equipamento" in w for w in draft["raw_payload"]["volume_planning"]["warnings"])


def test_tecnica_que_exige_falha_em_par_proibido_e_removida(db_conn, catalog, gym_equipment):
    """Validacao defensiva: `rest_pause` esta marcada COMPATIVEL, mas exige
    falha, e o par tem teto 9."""
    _mark_compatible(db_conn, catalog.equipment["Polia"], "rest_pause", 1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 9 WHERE id = %s", (catalog.equipment["Polia"],))
    db_conn.commit()
    template, slot = _make_template(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    draft = _generate(template, slot, catalog)
    assert draft["exercises"][0]["advanced_technique"] is None


def test_rpe_10_do_modelo_em_par_proibido_e_clampado_sem_bloquear(db_conn, catalog, gym_equipment):
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 9 WHERE id = %s", (catalog.equipment["Polia"],))
    db_conn.commit()
    template, slot = _make_template(db_conn, catalog, [_item(catalog, rpe_target="10")])
    draft = _generate(template, slot, catalog)

    assert draft["exercises"][0]["rpe_target"] == "9"  # a ficha nunca mostra 10
    clamps = draft["raw_payload"]["generation_audit"]["safety_clamps"]
    assert clamps and clamps[0]["from"] == 10 and clamps[0]["to"] == 9
    assert len(draft["exercises"]) == 1  # a geracao NAO foi bloqueada


def test_tecnica_do_modelo_vence_a_da_progressao(db_conn, catalog, gym_equipment):
    """Conflito tecnica fixa x tecnica da progressao: o modelo vence
    (hierarquia da secao 5.9)."""
    for code in ("rest_pause", "drop_set"):
        _mark_compatible(db_conn, catalog.equipment["Polia"], code, 1)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO progression_tracks (user_id, exercise_id, equipment_id, muscle_group_id, load_status, "
            "working_load_kg, sets_count, technique_code, engine_version) "
            "VALUES (%s,%s,%s,%s,'ACTIVE',40,3,'rest_pause','progression-v2.0.0')",
            (
                catalog.user_id,
                catalog.exercises["Crucifixo"],
                catalog.equipment["Polia"],
                catalog.muscle_groups["Peitoral"],
            ),
        )
    db_conn.commit()
    template, slot = _make_template(db_conn, catalog, [_item(catalog, advanced_technique="drop_set")])
    assert _generate(template, slot, catalog)["exercises"][0]["advanced_technique"] == "drop_set"


# ---------------------------------------------------------------------------
# 4. Determinismo e rollback pela flag
# ---------------------------------------------------------------------------


def test_determinismo_mesma_entrada_mesma_ficha(db_conn, catalog, gym_equipment):
    template, slot = _make_template(
        db_conn, catalog, [_item(catalog, advanced_technique="rest_pause", **MODEL_FIELDS), _item(catalog, exercise="Supino reto", equipment="Banco reto")]
    )
    primeiro = _generate(template, slot, catalog)
    segundo = _generate(template, slot, catalog)
    assert primeiro["exercises"] == segundo["exercises"]
    assert primeiro["template_slot_targets"] == segundo["template_slot_targets"]
    assert primeiro["raw_payload"]["generation_audit"] == segundo["raw_payload"]["generation_audit"]
    assert primeiro["raw_payload"]["generation_audit"]["llm_used"] is False


def test_flag_legacy_mantem_o_motor_antigo(db_conn, catalog, gym_equipment, monkeypatch):
    """Rollback da secao 15: `GENERATION_ENGINE=legacy` (o default de
    producao) devolve o motor anterior, byte a byte no que importa - inclusive
    a `engine_version` gravada no `raw_payload`."""
    monkeypatch.setenv("GENERATION_ENGINE", "legacy")
    template, slot = _make_template(db_conn, catalog, [_item(catalog)])
    draft = _generate(template, slot, catalog)
    assert draft["raw_payload"]["engine_version"] == ENGINE_LEGACY
    assert "generation_audit" not in draft["raw_payload"]
    assert "requires_user_choice" not in draft["exercises"][0]


def test_flag_deterministica_sem_progressao_v2_cai_no_legado(db_conn, catalog, gym_equipment, monkeypatch):
    """A dependencia operacional: sem `PROGRESSION_ENGINE=v2` as trilhas nao
    sao alimentadas, e o motor novo NAO assume o lugar."""
    monkeypatch.setenv("PROGRESSION_ENGINE", "shadow")
    template, slot = _make_template(db_conn, catalog, [_item(catalog)])
    assert _generate(template, slot, catalog)["raw_payload"]["engine_version"] == ENGINE_LEGACY


# ---------------------------------------------------------------------------
# 5. Congelamento da ficha e round-trip
# ---------------------------------------------------------------------------


def _save(draft, catalog, template_id):
    """Salva o rascunho como ficha, pelo mesmo caminho do router."""
    exercises = [
        WorkoutPlanExerciseIn(**{key: value for key, value in exercise.items() if key in WorkoutPlanExerciseIn.model_fields}).model_dump(
            exclude_unset=True
        )
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


def test_ficha_congelada_vem_do_motor_deterministico(db_conn, catalog, gym_equipment):
    template, slot = _make_template(db_conn, catalog, [_item(catalog, **MODEL_FIELDS)])
    plan = _save(_generate(template, slot, catalog), catalog, template["id"])
    prescription = plan["exercises"][0]["prescription"]

    assert prescription["engine_version"] == ENGINE_DETERMINISTIC
    assert prescription["source"] == "PLAN_CREATED"
    assert plan["exercises"][0]["prescription_status"] == "FROZEN"
    assert prescription["reps_target"] == "6-8"  # da progressao, nao do modelo
    # Abrir a ficha duas vezes devolve exatamente a mesma prescricao (PR-11).
    again = services.get_workout_plan(plan["id"], user_id=catalog.user_id)
    assert again["exercises"][0]["prescription"] == prescription


def test_congelamento_registra_substituicao_e_escolha_pendente(db_conn, catalog, gym_equipment):
    template, slot = _make_template(
        db_conn,
        catalog,
        [
            _item(catalog, equipment="Rack"),
            _item(
                catalog,
                exercise="Agachamento",
                equipment="Rack",
                muscle_group_id=catalog.muscle_groups["Quadríceps"],
            ),
        ],
    )
    plan = _save(_generate(template, slot, catalog), catalog, template["id"])
    substituido, sem_equipamento = plan["exercises"]

    assert "EQUIPMENT_SUBSTITUTED" in substituido["prescription"]["warnings"]
    assert substituido["requires_user_choice"] is False
    assert "EQUIPMENT_UNRESOLVED" in sem_equipamento["prescription"]["warnings"]
    assert sem_equipamento["requires_user_choice"] is True


def test_congelamento_registra_tecnica_invalida_do_modelo(db_conn, catalog, gym_equipment):
    _mark_compatible(db_conn, catalog.equipment["Polia"], "rest_pause", 0)
    template, slot = _make_template(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    plan = _save(_generate(template, slot, catalog), catalog, template["id"])
    assert "MODEL_TECHNIQUE_INVALID" in plan["exercises"][0]["prescription"]["warnings"]
    assert plan["exercises"][0]["prescription"]["advanced_technique"] is None


def test_round_trip_post_put_get_preserva_campos_e_prescricao(db_conn, catalog, gym_equipment):
    """Os 10 campos avancados sobrevivem a POST -> PUT -> GET, e o
    `prescription_json` de uma linha que ja existia NAO e reescrito no PUT."""
    template, slot = _make_template(db_conn, catalog, [_item(catalog, advanced_technique=None, **MODEL_FIELDS)])
    plan = _save(_generate(template, slot, catalog), catalog, template["id"])
    original = plan["exercises"][0]["prescription"]

    payload = {
        "name": "Editado",
        "gym_id": catalog.gym_id,
        "template_id": template["id"],
        "template_slot_sequence": 1,
        "exercises": [
            WorkoutPlanExerciseIn(
                **{
                    key: value
                    for key, value in plan["exercises"][0].items()
                    if key in WorkoutPlanExerciseIn.model_fields
                }
            ).model_dump(exclude_unset=True)
        ],
    }
    updated = services.update_workout_plan(plan["id"], payload, user_id=catalog.user_id)
    exercise = updated["exercises"][0]

    for field, value in MODEL_FIELDS.items():
        assert exercise[field] == value, field
    assert exercise["prescription"] == original


# ---------------------------------------------------------------------------
# 6. D14 - validacao de seguranca no save do modelo
# ---------------------------------------------------------------------------


def _payload(catalog, **item_overrides) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(
        name="Modelo D14",
        volume_mode="equipment_series",
        slots=[
            {
                "sequence": 1,
                "name": "A",
                "slot_type": "normal",
                "items": [{"item_order": 1, "item_kind": "equipment", **_item(catalog), **item_overrides}],
            }
        ],
    )


def test_d14_criacao_com_tecnica_invalida_e_422(db_conn, catalog, gym_equipment):
    from fastapi import HTTPException

    from app import workout_plan_templates as wpt

    _mark_compatible(db_conn, catalog.equipment["Polia"], "rest_pause", 0)
    with pytest.raises(HTTPException) as exc:
        wpt._enforce_template_safety(_payload(catalog, advanced_technique="rest_pause"), None)
    assert exc.value.status_code == 422
    assert "rest_pause" in exc.value.detail


def test_d14_rpe_acima_do_teto_em_item_novo_e_422(db_conn, catalog, gym_equipment):
    from fastapi import HTTPException

    from app import workout_plan_templates as wpt

    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 8 WHERE id = %s", (catalog.equipment["Polia"],))
    db_conn.commit()
    with pytest.raises(HTTPException) as exc:
        wpt._enforce_template_safety(_payload(catalog, rpe_target="10"), None)
    assert exc.value.status_code == 422
    assert "teto seguro" in exc.value.detail


def test_d14_item_legado_nao_tocado_passa_e_vira_warning(db_conn, catalog, gym_equipment):
    """O ponto da D14: modelos em uso continuam salvaveis."""
    from app import workout_plan_templates as wpt

    _mark_compatible(db_conn, catalog.equipment["Polia"], "rest_pause", 0)
    payload = _payload(catalog, advanced_technique="rest_pause")
    salvo = {
        "slots": [
            {
                "sequence": 1,
                "name": "A",
                "items": [
                    {
                        "item_order": 1,
                        "item_kind": "equipment",
                        **_item(catalog),
                        "advanced_technique": "rest_pause",
                        "rpe_target": None,
                    }
                ],
            }
        ]
    }
    wpt._enforce_template_safety(payload, salvo)  # nao levanta

    template, _ = _make_template(db_conn, catalog, [_item(catalog, advanced_technique="rest_pause")])
    detalhe = wpt._attach_safety_warnings(wpt._get_template(template["id"]))
    assert [entry["code"] for entry in detalhe["safety_warnings"]] == ["MODEL_TECHNIQUE_INVALID"]
    assert detalhe["safety_warnings"][0]["slot_sequence"] == 1
    assert detalhe["safety_warnings"][0]["item_order"] == 1

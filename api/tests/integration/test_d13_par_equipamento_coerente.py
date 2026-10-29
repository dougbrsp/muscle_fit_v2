"""D13 na ESCRITA da ficha: o par exercicio x equipamento nunca sai torto.

Bug de producao (cenario sintetico baseado no caso real: ficha, modelo e
academia ilustrativos, `engine_version = mode-b-deterministic-v1`): o Modo B
deterministico decidiu corretamente a D13 caminho (b) - equipamento do modelo
ausente na academia, nenhuma alternativa para o MESMO exercicio, logo
`preferred_equipment_id = None` + `requires_user_choice = True` -, mas a
ficha salva saiu com um equipamento de OUTRO exercicio:

| item | modelo pedia          | ficha entregou                  |
|------|-----------------------|---------------------------------|
| 1    | Maquina Orbital Row   | Estacao de polia / Pulley  (!)  |
| 3    | MTS Iso-Lateral High Row | Estacao de polia / Pulley (!) |
| 6    | Maquina Biceps 45 graus | Barra W/EZ               (!)  |

Marca diagnostica: nesses tres itens `workout_plan_exercises.equipment_name`
ficou NULL. Era a assinatura do caminho culpado: a PERSISTENCIA
(`services._replace_plan_exercises`) via `preferred_equipment_id` vazio e
chamava `services.resolve_equipment_id`, que escolhia - SEM hint de nome - o
melhor equipamento do GRUPO MUSCULAR primario do exercicio, isto é, a mesma
substituicao por grupo que a D13 aboliu e que o PR-12 removeu do motor
(`_recommend_for_group`), viva na camada de gravacao.

Este arquivo fixa as duas metades da D13 no caminho ponta a ponta
(gerar -> salvar -> ler):

1. resolucao de equipamento e SEMPRE por `exercise_id`, nunca por grupo;
2. qualquer par incoerente que chegue a escrita e erro do motor: falha alto
   pelo helper (`assert_pair_coherent`) e, no runtime, vira
   `requires_user_choice` auditado em vez de ficha torta;
3. a substituicao LEGITIMA (mesmo exercicio, outro equipamento - o caso do
   item 7 da ficha do caso, MTS Abdominal Crunch -> Maquina Abdominal Crunch)
   continua funcionando.

M6 (2026-09-21): quando NENHUM equipamento do exercicio existe na academia,
o motor agora troca o EXERCICIO por outro do mesmo grupo (par coerente, com a
ficha tecnica do substituto) antes de cair no usuario
(`generation/exercise_substitution.py`). Na academia do caso isso da: Orbital Row ->
Remada sentada na maquina (Pulley); High Row -> escolha do usuario (a Remada
sentada ja foi usada pelo item 1 - a ficha nao repete exercicio); Rosca
inclinada -> Rosca direta (Barra W). O que a D13 proibe continua proibido: o
exercicio ORIGINAL nunca sai com o equipamento de outro.
"""

from __future__ import annotations

import pytest

from app import services
from app.domain.equipment_pair import IncoherentEquipmentPairError, assert_pair_coherent, is_pair_coherent
from app.models import WorkoutPlanExerciseIn
from tests.support.template_versions import versao_v1

# Nomes do caso real da academia do caso.
ORBITAL_ROW = "Remada curvada no orbital row"
HIGH_ROW = "Remada alta unilateral"
REMADA_MAQUINA = "Remada sentada na maquina"
ROSCA_INCLINADA = "Rosca inclinada na maquina"
ROSCA_DIRETA = "Rosca direta"
ABDOMINAL = "Abdominal crunch na maquina"

EQ_ORBITAL = "Maquina Orbital Row"
EQ_HIGH_ROW = "MTS Iso-Lateral High Row"
EQ_PULLEY = "Estacao de polia / Pulley"
EQ_BICEPS_45 = "Maquina Biceps 45 graus"
EQ_BARRA_W = "Barra W/EZ"
EQ_MTS_CRUNCH = "MTS Abdominal Crunch"
EQ_MAQUINA_CRUNCH = "Maquina Abdominal Crunch"


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """Producao hoje: as duas flags LIGADAS."""
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
def academia_caso(db_conn, catalog):
    """Reproduz o catalogo relevante da academia do caso.

    O ponto essencial: a academia TEM equipamento dos grupos Costas e Biceps,
    mas nenhum deles serve os exercicios que o modelo pediu. E exatamente a
    configuracao em que a substituicao por grupo entrega um par incoerente.
    """
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plans")
        cur.execute("DELETE FROM workout_plan_templates")
        cur.execute("DELETE FROM equipment_technique_compatibility")
        cur.execute("DELETE FROM progression_decisions")
        cur.execute("DELETE FROM progression_tracks")
        cur.execute("DELETE FROM progression_group_states")
        cur.execute("DELETE FROM gym_equipment")

        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Abdomen')")
        catalog.muscle_groups["Abdomen"] = cur.lastrowid

        next_id = max(catalog.exercises.values()) + 1
        for name, group in (
            (ORBITAL_ROW, "Costas"),
            (HIGH_ROW, "Costas"),
            (REMADA_MAQUINA, "Costas"),
            (ROSCA_INCLINADA, "Bíceps"),
            (ROSCA_DIRETA, "Bíceps"),
            (ABDOMINAL, "Abdomen"),
        ):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (next_id, name, name),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (next_id, name))
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
                "VALUES (%s, %s, 'PRIMARY', 1.0, 1.0)",
                (next_id, catalog.muscle_groups[group]),
            )
            catalog.exercises[name] = next_id
            next_id += 1

        # Vinculos do catalogo GLOBAL (equipment_exercises): cada equipamento
        # serve so os exercicios reais dele.
        for equipment, exercises in (
            (EQ_ORBITAL, [ORBITAL_ROW]),
            (EQ_HIGH_ROW, [HIGH_ROW]),
            (EQ_PULLEY, [REMADA_MAQUINA]),
            (EQ_BICEPS_45, [ROSCA_INCLINADA]),
            (EQ_BARRA_W, [ROSCA_DIRETA]),
            (EQ_MTS_CRUNCH, [ABDOMINAL]),
            (EQ_MAQUINA_CRUNCH, [ABDOMINAL]),
        ):
            cur.execute("INSERT INTO equipment (name, max_safe_rpe) VALUES (%s, 10)", (equipment,))
            catalog.equipment[equipment] = cur.lastrowid
            for exercise in exercises:
                cur.execute(
                    "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
                    (catalog.equipment[equipment], catalog.exercises[exercise]),
                )

        # Catalogo INCOMPLETO da academia do caso: nao tem Orbital Row, nem High Row,
        # nem Biceps 45 graus, nem o MTS Abdominal Crunch.
        gym_equipment_ids: dict[str, int] = {}
        for equipment, exercise in (
            (EQ_PULLEY, REMADA_MAQUINA),
            (EQ_BARRA_W, ROSCA_DIRETA),
            (EQ_MAQUINA_CRUNCH, ABDOMINAL),
        ):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[equipment]),
            )
            gym_equipment_ids[equipment] = cur.lastrowid
    db_conn.commit()
    return gym_equipment_ids


def _template_slot_5(conn, catalog):
    """Modelo `equipment_series` com os itens que importam da ficha do caso:
    dois itens de Costas sem equipamento na academia, um de Biceps idem e o
    item 7 (abdominal), cuja substituicao e LEGITIMA."""
    items = [
        (ORBITAL_ROW, EQ_ORBITAL, "Costas"),
        (HIGH_ROW, EQ_HIGH_ROW, "Costas"),
        (ROSCA_INCLINADA, EQ_BICEPS_45, "Bíceps"),
        (ABDOMINAL, EQ_MTS_CRUNCH, "Abdomen"),
    ]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Modelo 21', 'equipment_series', 1, %s)",
            (catalog.user_id,),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
            "VALUES (%s, %s, 5, 'E', 'normal')",
            (versao_v1(cur, template_id), template_id,),
        )
        slot_id = cur.lastrowid
        for order, (exercise, equipment, group) in enumerate(items, start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slot_items "
                "(slot_id, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count) "
                "VALUES (%s, %s, 'equipment', %s, %s, %s, 3)",
                (
                    slot_id,
                    order,
                    catalog.muscle_groups[group],
                    catalog.exercises[exercise],
                    catalog.equipment[equipment],
                ),
            )
    conn.commit()
    template = {"id": template_id, "name": "Modelo 21", "template_type": None, "description": None}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count, "
            "reps_target, rpe_target, advanced_technique "
            "FROM workout_plan_template_slot_items WHERE slot_id = %s ORDER BY item_order",
            (slot_id,),
        )
        slot = {"id": slot_id, "sequence": 5, "name": "E", "slot_type": "normal", "items": list(cur.fetchall())}
    return template, slot


def _generate_and_save(db_conn, catalog):
    from app.generation import mode_b

    template, slot = _template_slot_5(db_conn, catalog)
    draft = mode_b.generate_plan_draft_from_equipment_template(
        template=template,
        slot=slot,
        gym_id=catalog.gym_id,
        gym_name=None,
        plan_date=None,
        user_id=catalog.user_id,
        min_rest_seconds=60,
        max_rest_seconds=90,
    )
    exercises = [
        WorkoutPlanExerciseIn(
            **{key: value for key, value in exercise.items() if key in WorkoutPlanExerciseIn.model_fields}
        ).model_dump(exclude_unset=True)
        for exercise in draft["exercises"]
    ]
    plan = services.create_workout_plan(
        {
            "name": draft["name"],
            "gym_id": catalog.gym_id,
            "template_id": template["id"],
            "template_slot_sequence": 5,
            "exercises": exercises,
            "raw_payload": draft["raw_payload"],
        },
        user_id=catalog.user_id,
    )
    return draft, plan


def _by_name(exercises):
    return {ex["exercise_name"]: ex for ex in exercises}


# ---------------------------------------------------------------------------
# 1. O caso, ponta a ponta
# ---------------------------------------------------------------------------


def test_ficha_do_caso_nao_entrega_equipamento_de_outro_exercicio(db_conn, catalog, academia_caso):
    """Regressao do bug: nenhum item da ficha SALVA aponta para um
    `gym_equipment` de outro exercicio. Com o M6, dois dos tres itens sem
    alternativa viram OUTRO exercicio do grupo (par coerente, nome e
    equipamento do substituto); o terceiro, sem substituto sobrando, sai com
    `requires_user_choice`. O exercicio original com equipamento alheio - o
    bug - nao aparece em lugar nenhum."""
    _, plan = _generate_and_save(db_conn, catalog)
    rows = plan["exercises"]
    exercises = _by_name(rows)

    # Os nomes do modelo que nao tinham equipamento NAO estao na ficha com
    # equipamento emprestado: ou foram substituidos (nome do substituto) ou
    # exigem escolha.
    assert ORBITAL_ROW not in exercises and ROSCA_INCLINADA not in exercises
    orbital_sub, high_row, rosca_sub, abdominal = rows
    assert orbital_sub["mapping_id"] == catalog.exercises[REMADA_MAQUINA]
    assert orbital_sub["exercise_name"] == REMADA_MAQUINA
    assert orbital_sub["equipment_name"] == EQ_PULLEY
    assert orbital_sub["preferred_equipment_id"] == academia_caso[EQ_PULLEY]
    assert orbital_sub["requires_user_choice"] is False
    assert orbital_sub["prescription"]["substitution"]["kind"] == "EXERCISE"
    assert orbital_sub["prescription"]["substitution"]["from_exercise_id"] == catalog.exercises[ORBITAL_ROW]

    assert high_row["mapping_id"] == catalog.exercises[HIGH_ROW]  # sem substituto: exercicio mantido
    assert high_row["preferred_equipment_id"] is None, "par incoerente gravado"
    assert high_row["requires_user_choice"] is True
    assert "EQUIPMENT_UNRESOLVED" in high_row["prescription"]["warnings"]

    assert rosca_sub["mapping_id"] == catalog.exercises[ROSCA_DIRETA]
    assert rosca_sub["exercise_name"] == ROSCA_DIRETA
    assert rosca_sub["equipment_name"] == EQ_BARRA_W
    assert rosca_sub["requires_user_choice"] is False

    assert abdominal["mapping_id"] == catalog.exercises[ABDOMINAL]

    with db_conn.cursor() as cur:
        # Nenhuma linha da ficha aponta para equipamento de outro exercicio.
        for row in rows:
            if row.get("preferred_equipment_id"):
                assert is_pair_coherent(cur, row["mapping_id"], row["preferred_equipment_id"]), row["exercise_name"]


def test_item_7_substituido_pelo_mesmo_exercicio(db_conn, catalog, academia_caso):
    """Item 7 da ficha do caso: MTS Abdominal Crunch ausente na academia, mas a
    Maquina Abdominal Crunch serve o MESMO exercicio -> substituicao
    automatica, `equipment_name` preenchido e coerente, sem exigir escolha."""
    _, plan = _generate_and_save(db_conn, catalog)
    row = _by_name(plan["exercises"])[ABDOMINAL]

    assert row["mapping_id"] == catalog.exercises[ABDOMINAL]
    assert row["preferred_equipment_id"] == academia_caso[EQ_MAQUINA_CRUNCH]
    assert row["equipment_name"] == EQ_MAQUINA_CRUNCH
    assert row["requires_user_choice"] is False
    assert "EQUIPMENT_SUBSTITUTED" in row["prescription"]["warnings"]


def test_equipment_name_nulo_nao_vira_equipamento_arbitrario(db_conn, catalog, academia_caso):
    """A marca diagnostica do bug: `equipment_name` NULL + `mapping_id`
    preenchido nao pode mais virar "o melhor equipamento do grupo". O item
    sem substituto (High Row) fica honesto: sem equipamento, exigindo escolha.
    Os substituidos pelo M6 saem com o equipamento do PROPRIO par."""
    _, plan = _generate_and_save(db_conn, catalog)
    row = _by_name(plan["exercises"])[HIGH_ROW]
    assert row["equipment_name"] is None
    assert row["preferred_equipment_id"] is None
    for other in plan["exercises"]:
        if other["exercise_name"] != HIGH_ROW:
            assert other["equipment_name"] is not None, other["exercise_name"]


# ---------------------------------------------------------------------------
# 2. `resolve_equipment_id` e por exercicio, nunca por grupo
# ---------------------------------------------------------------------------


def test_resolve_equipment_id_nao_cruza_exercicios_sem_hint(db_conn, catalog, academia_caso):
    """Sem hint de nome: a academia do caso tem Pulley (Costas) e Barra W (Biceps),
    mas nenhum deles serve os exercicios pedidos -> None, nao "o melhor do
    grupo"."""
    with db_conn.cursor() as cur:
        for name in (ORBITAL_ROW, HIGH_ROW, ROSCA_INCLINADA):
            assert (
                services.resolve_equipment_id(cur, catalog.exercises[name], catalog.gym_id, user_id=catalog.user_id)
                is None
            ), name


def test_resolve_equipment_id_nao_cruza_exercicios_com_hint(db_conn, catalog, academia_caso):
    """Com hint de nome tambem nao: o nome casa um equipamento do grupo, mas
    de outro exercicio."""
    with db_conn.cursor() as cur:
        assert (
            services.resolve_equipment_id(
                cur, catalog.exercises[ORBITAL_ROW], catalog.gym_id, None, EQ_PULLEY, user_id=catalog.user_id
            )
            is None
        )


def test_resolve_equipment_id_resolve_pelo_mesmo_exercicio(db_conn, catalog, academia_caso):
    """O que a funcao continua fazendo: escolher entre os equipamentos que
    servem AQUELE exercicio na academia."""
    with db_conn.cursor() as cur:
        assert (
            services.resolve_equipment_id(cur, catalog.exercises[ABDOMINAL], catalog.gym_id, user_id=catalog.user_id)
            == academia_caso[EQ_MAQUINA_CRUNCH]
        )
        assert (
            services.resolve_equipment_id(
                cur, catalog.exercises[ABDOMINAL], catalog.gym_id, None, EQ_MAQUINA_CRUNCH, user_id=catalog.user_id
            )
            == academia_caso[EQ_MAQUINA_CRUNCH]
        )


# ---------------------------------------------------------------------------
# 3. Coerencia do par na escrita: falha alto no helper, degrada no runtime
# ---------------------------------------------------------------------------


def test_assert_pair_coherent_falha_alto(db_conn, catalog, academia_caso):
    """Par incoerente e ERRO DO MOTOR: o helper levanta, para que nenhum
    teste (nem nenhum motor novo) consiga gravar uma ficha torta em silencio."""
    with db_conn.cursor() as cur:
        with pytest.raises(IncoherentEquipmentPairError):
            assert_pair_coherent(cur, catalog.exercises[ORBITAL_ROW], academia_caso[EQ_PULLEY])
        # O par legitimo passa.
        assert_pair_coherent(cur, catalog.exercises[ABDOMINAL], academia_caso[EQ_MAQUINA_CRUNCH])


def test_par_incoerente_vindo_do_cliente_e_descartado_no_save(db_conn, catalog, academia_caso):
    """Um PUT que ecoe um `preferred_equipment_id` de outro exercicio (ficha
    antiga, cliente desatualizado, motor com bug) nao grava o par torto: o
    vinculo e limpo.

    Ficha SEM modelo (caminho legado de congelamento): o snapshot fica com
    `EQUIPMENT_UNRESOLVED` (par desconhecido -> default conservador D3). O
    `requires_user_choice` continua sendo decisao dos motores do Modo A/B -
    nao mexemos no contrato do legado por causa desta correcao."""
    plan = services.create_workout_plan(
        {
            "name": "Ficha com par torto",
            "gym_id": catalog.gym_id,
            "exercises": [
                {
                    "sequence": 1,
                    "exercise_name": ORBITAL_ROW,
                    "mapping_id": catalog.exercises[ORBITAL_ROW],
                    "preferred_equipment_id": academia_caso[EQ_PULLEY],
                    "sets_count": 3,
                }
            ],
        },
        user_id=catalog.user_id,
    )
    row = plan["exercises"][0]
    assert row["mapping_id"] == catalog.exercises[ORBITAL_ROW]
    assert row["preferred_equipment_id"] is None
    assert "EQUIPMENT_UNRESOLVED" in row["prescription"]["warnings"]


def test_ficha_ja_gravada_torta_e_corrigida_no_refresh(db_conn, catalog, academia_caso):
    """As fichas que ja existem (como a do caso): o congelamento nao confia mais
    no `preferred_equipment_id` gravado. Par incoerente -> D13 caminho (b)."""
    _, plan = _generate_and_save(db_conn, catalog)
    row_id = _by_name(plan["exercises"])[HIGH_ROW]["workout_plan_exercise_id"]
    # Simula a linha como ela esta na Aurora hoje: apontando para o Pulley.
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_exercises SET preferred_equipment_id = %s, requires_user_choice = 0 WHERE id = %s",
            (academia_caso[EQ_PULLEY], row_id),
        )
    db_conn.commit()

    # Antes mesmo de qualquer refresh, a LEITURA nao entrega o par torto - e
    # isso que tira a foto/etiqueta erradas do modo treino nas fichas que ja
    # existem (nenhuma escrita no banco).
    lido = _by_name(services.get_workout_plan(plan["id"], user_id=catalog.user_id)["exercises"])[HIGH_ROW]
    assert lido["preferred_equipment_id"] is None
    assert lido["equipment_name"] is None
    assert lido["requires_user_choice"] is True

    services.refresh_plan_prescription(plan["id"], user_id=catalog.user_id)
    fresh = _by_name(services.get_workout_plan(plan["id"], user_id=catalog.user_id)["exercises"])[HIGH_ROW]
    assert fresh["requires_user_choice"] is True
    assert "EQUIPMENT_UNRESOLVED" in fresh["prescription"]["warnings"]

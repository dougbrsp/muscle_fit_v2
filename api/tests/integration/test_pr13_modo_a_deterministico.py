"""PR-13 - Modo A deterministico ponta a ponta (PLANO_20260912.md 6.1, 4, 5.8;
decisoes D6, D7; bloco "Geracao Modo A" da secao 13).

Cobre o bloco da secao 13 inteiro:

- slot automatico e explicito;
- ativacao PRIMARY, SECONDARY e STABILIZER (os tres contam no passo 6);
- deficit reportado na auditoria e no resumo de volume;
- excesso secundario bloqueando o candidato, e a reducao de series como ultimo
  recurso;
- empate de estrelas resolvido por recencia do usuario;
- preferido por estrelas incompativel com a tecnica requerida perdendo para o
  compativel menos preferido;
- tecnica filtrada pela matriz de compatibilidade;
- par proibido (sem falha) gerando alvo de RPE seguro;
- academia sem equipamento para o grupo;
- multiplos equipamentos para o mesmo grupo;
- ordenacao por fadiga compartilhada;
- recuperacao reduzida e bloqueada;
- progressao mudando carga e reps;
- ciclos de 3, 4, 5 e 9 slots, com descanso;
- **determinismo**: mesma entrada -> mesma ficha, byte a byte, `llm_used=false`;
- e a prova do rollback: com `GENERATION_ENGINE=legacy` quem roda e o motor
  legado (v2, com LLM).

As funcoes puras (janela, orcamento, ranking, filtro, fadiga) estao em
`tests/unit/test_pr13_modo_a_deterministico.py`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.generation import mode_a
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.workout_plan_templates import _get_template
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.template_versions import versao_v1

ENGINE_DETERMINISTIC = "mode-a-deterministic-v1"


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """DEV do PR-12/13: as duas flags ligadas. O teste de rollback apaga a
    `GENERATION_ENGINE`."""
    monkeypatch.setenv("GENERATION_ENGINE", "deterministic")
    monkeypatch.setenv("PROGRESSION_ENGINE", "v2")


@pytest.fixture
def recovery(monkeypatch):
    """Controla a recuperacao vista pelos DOIS caminhos que a leem: o contexto
    do ciclo (`build_template_cycle_context`, que define o multiplicador de
    dose) e o contrato da progressao (`readiness_by_group`). Sem isso um teste
    de dose reduzida acertaria so metade do motor."""
    items: list[dict] = []

    def fake_overview(self, days=14, limit=24, user_id=None, **_kwargs):
        return list(items)

    monkeypatch.setattr(MuscleRecoveryService, "get_recovery_overview", fake_overview)

    from app import workout_plan_ai

    monkeypatch.setattr(
        workout_plan_ai,
        "gather_ficha_context",
        lambda gym_id, user_id: {"muscle_recovery": list(items), "gyms": [], "recent_exercise_logs": []},
    )

    def set_items(*new_items: dict) -> None:
        items.clear()
        items.extend(new_items)

    return set_items


def pain(group_id: int, *, muscle_pain=None, joint_pain=False) -> dict:
    """Item de recuperacao minimo: sem dose registrada o tempo/densidade nao
    limitam (100%), entao o status sai da DOR - que e o que estes testes
    querem exercitar (D5)."""
    return {
        "muscle_group_id": group_id,
        "muscle_pain": muscle_pain,
        "joint_pain": joint_pain,
        "session_dose": 0,
        "estimated_recovery_hours": 0,
        "hours_since_trained": 0,
    }


# ---------------------------------------------------------------------------
# Fixture de academia/catalogo
# ---------------------------------------------------------------------------


@pytest.fixture
def gym(db_conn, catalog):
    """Academia com varios pares para o mesmo grupo, e contribuicoes cobrindo
    os tres papeis.

    Peitoral (PRIMARY): Supino reto (Banco reto) e Crucifixo (Polia e Banco
    reto). Supino leva Triceps SECONDARY 0.5 e Ombros SECONDARY 0.3;
    Crucifixo ganha aqui um STABILIZER de Ombros 0.2, papel que o catalogo
    minimo nao tem.
    """
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plans")
        cur.execute("DELETE FROM workout_plan_templates")
        # Historico: NAO esta em CATALOG_TABLES, entao sobrevive ao TRUNCATE do
        # catalogo. Sem esta limpeza a ativacao REAL de um teste (a janela do
        # passo 1) vaza para o orcamento do proximo.
        cur.execute("DELETE FROM exercise_sets")
        cur.execute("DELETE FROM exercises")
        cur.execute("DELETE FROM activities")
        cur.execute("DELETE FROM training_type_muscle_group_targets")
        cur.execute("DELETE FROM training_types")
        cur.execute("DELETE FROM equipment_technique_compatibility")
        cur.execute("DELETE FROM user_equipment_exercise_preferences")
        cur.execute("DELETE FROM progression_decisions")
        cur.execute("DELETE FROM progression_tracks")
        cur.execute("DELETE FROM progression_group_states")
        # STABILIZER: papel aceito pelo dominio mas ausente do catalogo minimo.
        cur.execute(
            "INSERT INTO exercise_muscle_contributions "
            "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) VALUES (%s,%s,%s,%s,%s)",
            (catalog.exercises["Crucifixo"], catalog.muscle_groups["Ombros"], "STABILIZER", 0.2, 0.2),
        )
        cur.execute(
            "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
            (catalog.equipment["Banco reto"], catalog.exercises["Crucifixo"]),
        )
        ids = {}
        for equipment, exercise in (
            ("Banco reto", "Supino reto"),
            ("Polia", "Crucifixo"),
            ("Banco reto", "Crucifixo"),
            ("Polia", "Remada baixa"),
        ):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
                (catalog.gym_id, catalog.exercises[exercise], catalog.equipment[equipment]),
            )
            ids[(equipment, exercise)] = cur.lastrowid
        # Base permissiva: teto 10 e as tecnicas auto_applicable compativeis em
        # todo equipamento. Cada teste que fala de RPE/tecnica aperta o que
        # precisa - a restricao aparece no teste que a exercita.
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


def _training_type(conn, catalog, targets: dict[str, tuple[float, float, float]]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO training_types (name, is_active, created_by_user_id) VALUES ('Hipertrofia', 1, %s)",
            (catalog.user_id,),
        )
        training_type_id = cur.lastrowid
        for group, (weekly_min, weekly_target, weekly_max) in targets.items():
            cur.execute(
                "INSERT INTO training_type_muscle_group_targets "
                "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets) "
                "VALUES (%s,%s,%s,%s,%s)",
                (training_type_id, catalog.muscle_groups[group], weekly_min, weekly_target, weekly_max),
            )
    conn.commit()
    return training_type_id


def _template(conn, catalog, training_type_id: int, slots: list[dict], *, name="Modelo A") -> dict:
    """Modelo `percent_budget`. `slots` = lista de
    `{"name":..., "slot_type":..., "items":[{"group":..., "min":..., "max":..., ...}]}`."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, training_type_id, is_active, created_by_user_id) "
            "VALUES (%s, 'percent_budget', %s, 1, %s)",
            (name, training_type_id, catalog.user_id),
        )
        template_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (template_id, catalog.user_id, catalog.user_id),
        )
        for sequence, slot in enumerate(slots, start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
                "VALUES (%s, %s, %s, %s, %s)",
                (versao_v1(cur, template_id), template_id, sequence, slot.get("name") or f"S{sequence}", slot.get("slot_type") or "normal"),
            )
            slot_id = cur.lastrowid
            for order, item in enumerate(slot.get("items") or [], start=1):
                cur.execute(
                    "INSERT INTO workout_plan_template_slot_items "
                    "(slot_id, item_order, item_kind, muscle_group_id, priority_min_pct, priority_max_pct, "
                    " reps_target, rpe_target, prioritize) "
                    "VALUES (%s,%s,'muscle_group',%s,%s,%s,%s,%s,%s)",
                    (
                        slot_id,
                        order,
                        catalog.muscle_groups[item["group"]],
                        item.get("min", 50),
                        item.get("max", 75),
                        item.get("reps_target"),
                        item.get("rpe_target"),
                        1 if item.get("prioritize") else 0,
                    ),
                )
    conn.commit()
    return _get_template(template_id)


def _generate(template, slot, catalog, **kwargs):
    return mode_a.generate_plan_draft_from_template_a(
        template=template,
        slot=slot,
        gym_id=kwargs.pop("gym_id", catalog.gym_id),
        gym_name=None,
        plan_date=kwargs.pop("plan_date", None),
        user_id=catalog.user_id,
        min_rest_seconds=60,
        max_rest_seconds=90,
        **kwargs,
    )


def _simple(db_conn, catalog, **targets_override):
    """Modelo de um slot com Peitoral 50-75% de uma meta de 8 series."""
    targets = {"Peitoral": (4, 8, 12), "Tríceps": (2, 6, 10), "Ombros": (2, 6, 10), "Costas": (4, 8, 12)}
    targets.update(targets_override)
    training_type_id = _training_type(db_conn, catalog, targets)
    template = _template(
        db_conn, catalog, training_type_id, [{"name": "A", "items": [{"group": "Peitoral", "min": 50, "max": 75}]}]
    )
    return template, template["slots"][0]


# ---------------------------------------------------------------------------
# 1. Orcamento, alocacao e os tres papeis
# ---------------------------------------------------------------------------


def test_ficha_basica_sem_ia_com_auditoria_completa(db_conn, catalog, gym, recovery):
    recovery()
    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)

    assert draft["raw_payload"]["engine_version"] == ENGINE_DETERMINISTIC
    assert draft["raw_payload"]["used_ai"] is False
    audit = draft["raw_payload"]["generation_audit"]
    assert audit["llm_used"] is False
    assert audit["volume_mode"] == "percent_budget"
    # secao 14: a auditoria tem as cinco secoes
    assert set(audit) >= {
        "activation",
        "selection",
        "safety_clamps",
        "substitutions",
        "fallbacks",
        "window",
        "llm_used",
    }
    group_key = str(catalog.muscle_groups["Peitoral"])
    activation = audit["activation"][group_key]
    # meta 8, nada realizado nem planejado -> necessidade 8, envelope 4-6 -> 6
    assert activation["target"] == 6.0
    assert activation["target_min"] == 4.0
    assert activation["realized"] == 0.0
    assert activation["planned"] == 0.0
    # trilhas novas = 3 series cada; 3 < 4 (piso) -> entra um segundo exercicio
    assert [ex["sets_count"] for ex in draft["exercises"]] == [3, 3]
    assert activation["result"] == 6.0
    assert activation["deficit"] == 0.0
    # um exercicio por par distinto, sem repetir exercise_id
    assert len({ex["mapping_id"] for ex in draft["exercises"]}) == 2
    assert all(ex["preferred_equipment_id"] for ex in draft["exercises"])
    # janela de 7 dias terminando hoje
    assert audit["window"]["days"] == 7
    assert audit["window"]["target_scale"] == 1.0
    assert audit["window"]["projected_slots"] == []


def _history(db_conn, catalog, gym, exercise: str, equipment: str, *, days_ago: int, sets_count: int) -> None:
    """Uma sessao REAL do usuario, `days_ago` dias atras - o que a janela do
    passo 1 conta como `realizado`."""
    when = hoje_do_usuario() - timedelta(days=days_ago)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, name, local_date, started_at) VALUES (%s, %s, %s, %s)",
            (catalog.user_id, "Treino", when, datetime.combine(when, datetime.min.time())),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, %s, %s, %s, %s)",
            (activity_id, exercise, catalog.exercises[exercise], gym[(equipment, exercise)], sets_count),
        )
    db_conn.commit()


def test_realizado_da_janela_desconta_da_necessidade(db_conn, catalog, gym, recovery):
    """Passo 1/2: 4 series de Supino ha 2 dias = 4,0 de ativacao PRIMARY em
    Peitoral, dentro da janela. A necessidade cai de 8 para 4 e o alvo do slot
    acompanha."""
    recovery()
    _history(db_conn, catalog, gym, "Supino reto", "Banco reto", days_ago=2, sets_count=4)
    template, slot = _simple(db_conn, catalog)
    activation = _generate(template, slot, catalog)["raw_payload"]["generation_audit"]["activation"][
        str(catalog.muscle_groups["Peitoral"])
    ]

    assert activation["realized"] == 4.0
    assert activation["need"] == 4.0
    assert activation["target"] == 4.0  # clamp(4, 4, 6)


def test_historico_fora_da_janela_nao_conta(db_conn, catalog, gym, recovery):
    """A janela e MOVEL e tem 7 dias: uma sessao de 10 dias atras esta fora
    dela e nao desconta nada."""
    recovery()
    _history(db_conn, catalog, gym, "Supino reto", "Banco reto", days_ago=10, sets_count=4)
    template, slot = _simple(db_conn, catalog)
    activation = _generate(template, slot, catalog)["raw_payload"]["generation_audit"]["activation"][
        str(catalog.muscle_groups["Peitoral"])
    ]
    assert activation["realized"] == 0.0
    assert activation["need"] == 8.0


def test_ativacao_conta_primary_secondary_e_stabilizer(db_conn, catalog, gym, recovery):
    """Passo 6: "ativacao do exercicio = soma de sets x stimulus_weight para
    TODOS os grupos". Ombros esta no slot com meta alta e nenhum candidato
    proprio na academia: tudo o que aparece nele vem do SECONDARY do Supino
    (0.3) e do STABILIZER do Crucifixo (0.2)."""
    recovery()
    training_type_id = _training_type(
        db_conn,
        catalog,
        {"Peitoral": (4, 8, 12), "Tríceps": (2, 6, 10), "Ombros": (2, 6, 20), "Costas": (4, 8, 12)},
    )
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [
            {
                "name": "A",
                "items": [
                    {"group": "Peitoral", "min": 50, "max": 75},
                    {"group": "Ombros", "min": 50, "max": 75},
                ],
            }
        ],
    )
    draft = _generate(template, template["slots"][0], catalog)
    audit = draft["raw_payload"]["generation_audit"]
    ombros = audit["activation"][str(catalog.muscle_groups["Ombros"])]
    triceps_in_session = audit["activation"].get(str(catalog.muscle_groups["Tríceps"]))

    # Supino (3 x 0.3 SECONDARY) + Crucifixo (3 x 0.2 STABILIZER) = 1.5
    assert ombros["result"] == 1.5
    # Triceps nao esta no slot: nao aparece na auditoria de ativacao
    assert triceps_in_session is None
    # e o deficit de Ombros e reportado, nao "consertado"
    assert ombros["deficit"] > 0
    assert any(item["reason"] == mode_a.FALLBACK_DEFICIT for item in audit["fallbacks"])


def test_deficit_reportado_quando_o_catalogo_nao_alcanca_o_alvo(db_conn, catalog, gym, recovery):
    """Meta alta e so 2 pares de Peitoral na academia (limite de 3 exercicios
    por grupo nem chega a pesar): o que falta vira `deficit`, com aviso - e
    NUNCA tecnica/pico para compensar (D7)."""
    recovery()
    template, slot = _simple(db_conn, catalog, Peitoral=(20, 40, 60))
    draft = _generate(template, slot, catalog)
    audit = draft["raw_payload"]["generation_audit"]
    activation = audit["activation"][str(catalog.muscle_groups["Peitoral"])]

    assert activation["target_min"] == 20.0  # 40 x 50%
    assert activation["result"] == 6.0
    assert activation["deficit"] == 14.0
    assert len(draft["exercises"]) == 2
    assert all(ex["advanced_technique"] is None for ex in draft["exercises"])
    assert any("déficit" in warning for warning in draft["raw_payload"]["volume_planning"]["warnings"])
    assert draft["insufficient_target_volume_groups"][0]["deficit_equivalent_sets"] == 14.0


def test_insufficient_target_volume_groups_mantem_o_contrato_do_frontend(db_conn, catalog, gym, recovery):
    """Regressao do bug de producao "Cannot read properties of undefined
    (reading 'toFixed')": o Modo A passou a emitir `insufficient_target_volume_
    groups` SEM `equivalent_sets_achieved`/`equivalent_sets_target`, que
    `GenerationWarnings.tsx` acessava direto. A forma exata do item e parte do
    contrato — se mudar de novo, este teste quebra ANTES de a tela quebrar."""
    recovery()
    template, slot = _simple(db_conn, catalog, Peitoral=(20, 40, 60))
    draft = _generate(template, slot, catalog)

    groups = draft["insufficient_target_volume_groups"]
    assert len(groups) == 1
    entry = groups[0]

    # Chaves obrigatorias (as 5 do contrato legado + o extra do motor novo).
    assert set(entry) == {
        "muscle_primary",
        "muscle_group_id",
        "equivalent_sets_achieved",
        "equivalent_sets_target",
        "exercises_added",
        "reason",
        "deficit_equivalent_sets",
    }
    assert entry["muscle_primary"] == "Peitoral"
    assert entry["muscle_group_id"] == catalog.muscle_groups["Peitoral"]
    assert entry["reason"] == "deficit_ativacao"

    # Os numeros batem com a auditoria de ativacao do mesmo grupo, e o deficit
    # e exatamente alvo - alcancado.
    activation = draft["raw_payload"]["generation_audit"]["activation"][
        str(catalog.muscle_groups["Peitoral"])
    ]
    assert entry["equivalent_sets_achieved"] == activation["result"] == 6.0
    assert entry["equivalent_sets_target"] == activation["target_min"] == 20.0
    assert entry["deficit_equivalent_sets"] == 14.0
    assert (
        entry["equivalent_sets_target"] - entry["equivalent_sets_achieved"]
        == entry["deficit_equivalent_sets"]
    )

    # Nenhum campo do contrato pode vir None: e isso que `.toFixed()` nao
    # aguenta do outro lado.
    for key in ("equivalent_sets_achieved", "equivalent_sets_target", "exercises_added"):
        assert isinstance(entry[key], (int, float)), key

    # `exercises_added` = exercicios que o motor de fato colocou para o grupo.
    assert entry["exercises_added"] == len(draft["exercises"]) == 2


def test_maximo_de_3_exercicios_por_grupo(db_conn, catalog, gym, recovery):
    """Com 3 pares de Peitoral disponiveis e meta grande, o motor para em 3."""
    recovery()
    with db_conn.cursor() as cur:
        # um terceiro par de Peitoral: Crucifixo no Rack
        cur.execute(
            "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
            (catalog.equipment["Rack"], catalog.exercises["Crucifixo"]),
        )
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s,%s,%s,1)",
            (catalog.gym_id, catalog.exercises["Crucifixo"], catalog.equipment["Rack"]),
        )
        cur.execute("UPDATE equipment SET max_safe_rpe = 10")
    db_conn.commit()
    template, slot = _simple(db_conn, catalog, Peitoral=(20, 40, 60))
    draft = _generate(template, slot, catalog)
    # 3 exercicios no MAXIMO, e nenhum exercise_id repetido
    assert len(draft["exercises"]) <= mode_a.MAX_EXERCISES_PER_GROUP
    assert len({ex["mapping_id"] for ex in draft["exercises"]}) == len(draft["exercises"])


def test_excesso_secundario_bloqueia_o_candidato(db_conn, catalog, gym, recovery):
    """Passo 6: o Supino leva Triceps SECONDARY 0.5; com o teto projetado de
    Triceps em 0,4 serie, nem UMA serie de Supino cabe, e ele e recusado em
    favor do Crucifixo (que nao toca Triceps). O motivo fica na auditoria."""
    recovery()
    template, slot = _simple(db_conn, catalog, Tríceps=(0, 0.2, 0.4))
    draft = _generate(template, slot, catalog)
    audit = draft["raw_payload"]["generation_audit"]
    selection = audit["selection"][str(catalog.muscle_groups["Peitoral"])]

    recusas = [item for item in selection if item["reason"] == mode_a.REASON_SECONDARY_BUDGET_EXCEEDED]
    assert recusas, selection
    assert recusas[0]["exercise_id"] == catalog.exercises["Supino reto"]
    assert recusas[0]["blocking_muscle_group_id"] == catalog.muscle_groups["Tríceps"]
    assert catalog.exercises["Supino reto"] not in {ex["mapping_id"] for ex in draft["exercises"]}
    assert catalog.exercises["Crucifixo"] in {ex["mapping_id"] for ex in draft["exercises"]}


def test_sem_candidato_reduz_series_ate_1(db_conn, catalog, gym, recovery):
    """"Se nao houver [candidato], reduz series ate 1": com APENAS o Supino
    disponivel e o teto de Triceps em 1.0, 3 series (1.5) nao cabem, 2 (1.0)
    cabem - e a reducao fica registrada como fallback, nunca silenciosa."""
    recovery()
    with db_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM gym_equipment WHERE gym_id = %s AND mapping_id = %s",
            (catalog.gym_id, catalog.exercises["Crucifixo"]),
        )
    db_conn.commit()
    template, slot = _simple(db_conn, catalog, Tríceps=(0, 0.5, 1.0))
    draft = _generate(template, slot, catalog)

    assert [ex["mapping_id"] for ex in draft["exercises"]] == [catalog.exercises["Supino reto"]]
    assert draft["exercises"][0]["sets_count"] == 2
    reducoes = [
        item
        for item in draft["raw_payload"]["generation_audit"]["fallbacks"]
        if item["reason"] == mode_a.FALLBACK_SETS_REDUCED
    ]
    assert reducoes and reducoes[0]["from"] == 3 and reducoes[0]["to"] == 2


def test_academia_sem_equipamento_para_o_grupo(db_conn, catalog, gym, recovery):
    """Costas tem meta e esta no slot, mas a Remada baixa e o unico par - se
    ela sair da academia o grupo fica sem candidato: fallback explicito, ficha
    gerada (o motor nunca inventa exercicio)."""
    recovery()
    with db_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM gym_equipment WHERE gym_id = %s AND mapping_id = %s",
            (catalog.gym_id, catalog.exercises["Remada baixa"]),
        )
    db_conn.commit()
    training_type_id = _training_type(
        db_conn, catalog, {"Peitoral": (4, 8, 12), "Costas": (4, 8, 12), "Tríceps": (2, 6, 10), "Ombros": (2, 6, 10)}
    )
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [
            {
                "name": "A",
                "items": [{"group": "Peitoral", "min": 50, "max": 75}, {"group": "Costas", "min": 50, "max": 75}],
            }
        ],
    )
    draft = _generate(template, template["slots"][0], catalog)
    audit = draft["raw_payload"]["generation_audit"]

    assert any(
        item["reason"] == mode_a.FALLBACK_NO_EQUIPMENT_FOR_GROUP
        and item["muscle_group_id"] == catalog.muscle_groups["Costas"]
        for item in audit["fallbacks"]
    )
    assert draft["exercises"], "o slot continua gerando os grupos que tem equipamento"


def test_multiplos_equipamentos_para_o_mesmo_exercicio(db_conn, catalog, gym, recovery):
    """Crucifixo existe na Polia E no Banco reto: os dois sao candidatos, mas
    a deduplicacao por `exercise_id` garante que so um entra na ficha."""
    recovery()
    template, slot = _simple(db_conn, catalog, Peitoral=(20, 40, 60))
    draft = _generate(template, slot, catalog)
    crucifixos = [ex for ex in draft["exercises"] if ex["mapping_id"] == catalog.exercises["Crucifixo"]]
    assert len(crucifixos) == 1


# ---------------------------------------------------------------------------
# 2. Selecao: seguranca filtra, preferencia ordena
# ---------------------------------------------------------------------------


def _group_state(conn, catalog, group: str, **kwargs) -> None:
    """Estado de fase do grupo gravado pelo PROPRIO escritor da aplicacao
    (`progression_state.save_group_state`) - o teste nunca monta o INSERT na
    mao, senao ele deixa de acompanhar o schema."""
    from app import progression_state as ps
    from tests.support import progression_fixtures as fx

    kwargs.setdefault("phase_entered_at", datetime.now())
    ps.save_group_state(
        conn, catalog.user_id, fx.group(muscle_group_id=catalog.muscle_groups[group], **kwargs)
    )
    conn.commit()


def _track(conn, catalog, exercise: str, equipment: str, **kwargs) -> None:
    from app import progression_state as ps
    from tests.support import progression_fixtures as fx

    ps.save_track(
        conn,
        catalog.user_id,
        fx.track(
            exercise_id=catalog.exercises[exercise],
            equipment_id=catalog.equipment[equipment],
            **kwargs,
        ),
    )
    conn.commit()


def _star(conn, catalog, exercise: str, equipment: str, stars: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM equipment_exercises WHERE equipment_id = %s AND exercise_id = %s",
            (catalog.equipment[equipment], catalog.exercises[exercise]),
        )
        row = cur.fetchone()
        cur.execute(
            "INSERT INTO user_equipment_exercise_preferences (user_id, equipment_exercise_id, stars) "
            "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE stars = VALUES(stars)",
            (catalog.user_id, row["id"], stars),
        )
    conn.commit()


def test_estrelas_do_par_decidem_a_ordem(db_conn, catalog, gym, recovery):
    recovery()
    _star(db_conn, catalog, "Crucifixo", "Polia", 5)
    _star(db_conn, catalog, "Supino reto", "Banco reto", 1)
    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    assert draft["raw_payload"]["generation_audit"]["selection"][str(catalog.muscle_groups["Peitoral"])][0][
        "exercise_id"
    ] == catalog.exercises["Crucifixo"]


def test_empate_de_estrelas_resolve_por_recencia_do_usuario(db_conn, catalog, gym, recovery):
    """Mesmas 5 estrelas nos dois: vence quem o usuario NAO usou recentemente
    (e "nunca usou" vem antes de qualquer data)."""
    recovery()
    _star(db_conn, catalog, "Crucifixo", "Polia", 5)
    _star(db_conn, catalog, "Supino reto", "Banco reto", 5)
    _history(db_conn, catalog, gym, "Crucifixo", "Polia", days_ago=1, sets_count=3)

    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    selection = draft["raw_payload"]["generation_audit"]["selection"][str(catalog.muscle_groups["Peitoral"])]
    # o Crucifixo na Polia foi usado ontem: cai para tras do Supino
    assert selection[0]["exercise_id"] == catalog.exercises["Supino reto"]


def test_preferido_incompativel_com_a_tecnica_requerida_perde(db_conn, catalog, gym, recovery):
    """Caso da secao 13. O grupo esta em F4 com `drop_set` pendente; o par
    preferido (5 estrelas) nao e compativel com `drop_set` e o menos preferido
    (1 estrela) e. Ganha o COMPATIVEL - a seguranca filtra antes do ranking."""
    recovery()
    _star(db_conn, catalog, "Crucifixo", "Polia", 5)
    _star(db_conn, catalog, "Supino reto", "Banco reto", 1)
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE equipment_technique_compatibility SET is_compatible = 0 "
            "WHERE equipment_id = %s AND technique_code = 'drop_set'",
            (catalog.equipment["Polia"],),
        )
    db_conn.commit()
    # F4 com a tecnica ja escolhida: a exigencia e do GRUPO (D2)
    _group_state(
        db_conn,
        catalog,
        "Peitoral",
        phase="F4",
        pending_action="APPLY_TECHNIQUE",
        pending_payload={"technique_code": "drop_set"},
    )

    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    selection = draft["raw_payload"]["generation_audit"]["selection"][str(catalog.muscle_groups["Peitoral"])]
    excluidos = [item for item in selection if item["reason"] == mode_a.REASON_TECHNIQUE_INCOMPATIBLE]

    assert any(item["equipment_id"] == catalog.equipment["Polia"] for item in excluidos)
    escolhidos = {ex["mapping_id"] for ex in draft["exercises"]}
    assert catalog.exercises["Supino reto"] in escolhidos
    # o Crucifixo ainda pode entrar, mas pelo OUTRO equipamento (Banco reto)
    for exercise in draft["exercises"]:
        if exercise["mapping_id"] == catalog.exercises["Crucifixo"]:
            assert exercise["equipment_id"] != catalog.equipment["Polia"]


def test_tecnica_sem_marcacao_na_matriz_exclui_o_par(db_conn, catalog, gym, recovery):
    """D3, conservador: tecnica sem marcacao de compatibilidade e
    INCOMPATIVEL. Com `rest_pause` exigido e a matriz vazia, ninguem passa e o
    grupo fica sem exercicio, com fallback explicito."""
    recovery()
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM equipment_technique_compatibility")
    db_conn.commit()
    _group_state(
        db_conn,
        catalog,
        "Peitoral",
        phase="F4",
        pending_action="APPLY_TECHNIQUE",
        pending_payload={"technique_code": "rest_pause"},
    )
    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    audit = draft["raw_payload"]["generation_audit"]

    assert draft["exercises"] == []
    assert any(item["reason"] == mode_a.FALLBACK_ALL_CANDIDATES_EXCLUDED for item in audit["fallbacks"])
    assert all(
        item["reason"] == mode_a.REASON_TECHNIQUE_INCOMPATIBLE
        for item in audit["selection"][str(catalog.muscle_groups["Peitoral"])]
    )


def test_par_proibido_gera_alvo_seguro(db_conn, catalog, gym, recovery):
    """Equipamento com `max_safe_rpe=8`: o alvo prescrito nunca passa do teto,
    a falha nao e permitida e o clamp fica auditado."""
    recovery()
    with db_conn.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 8")
    db_conn.commit()
    training_type_id = _training_type(db_conn, catalog, {"Peitoral": (4, 8, 12), "Tríceps": (2, 6, 10)})
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [{"name": "A", "items": [{"group": "Peitoral", "min": 50, "max": 75, "rpe_target": "10"}]}],
    )
    draft = _generate(template, template["slots"][0], catalog)

    assert draft["exercises"], draft["raw_payload"]["generation_audit"]
    for exercise in draft["exercises"]:
        assert int(exercise["rpe_target"]) <= 8


# ---------------------------------------------------------------------------
# 3. Recuperacao e progressao
# ---------------------------------------------------------------------------


def test_recuperacao_reduzida_corta_a_dose(db_conn, catalog, gym, recovery):
    """REDUCED_DOSE (5.8): o alvo do slot e multiplicado por 0,75 e as series
    da trilha caem para `ceil(S x 0,75)`. Recuperacao define LIMITE, nunca
    prescricao (D6)."""
    recovery(pain(catalog.muscle_groups["Peitoral"], muscle_pain=5))
    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    activation = draft["raw_payload"]["generation_audit"]["activation"][
        str(catalog.muscle_groups["Peitoral"])
    ]

    assert activation["recovery_status"] == "REDUCED_DOSE"
    assert activation["volume_multiplier"] == 0.75
    assert activation["target"] == pytest.approx(4.5)  # 6 x 0.75
    assert activation["target_min"] == pytest.approx(3.0)  # 4 x 0.75
    assert draft["reduced_dose_groups"] == ["Peitoral"]
    # ceil(3 x 0.75) = 3 series, sem tecnica
    assert all(ex["advanced_technique"] is None for ex in draft["exercises"])


def test_recuperacao_bloqueada_remove_o_grupo(db_conn, catalog, gym, recovery):
    """NO_DIRECT_WORK: alvo 0, grupo fora do slot, ficha sem exercicio dele -
    e nenhum deficit lancado (nao ha o que alcancar)."""
    recovery(pain(catalog.muscle_groups["Peitoral"], joint_pain=True))
    training_type_id = _training_type(
        db_conn, catalog, {"Peitoral": (4, 8, 12), "Costas": (4, 8, 12), "Bíceps": (2, 6, 10)}
    )
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [
            {
                "name": "A",
                "items": [{"group": "Peitoral", "min": 50, "max": 75}, {"group": "Costas", "min": 50, "max": 75}],
            }
        ],
    )
    draft = _generate(template, template["slots"][0], catalog)
    audit = draft["raw_payload"]["generation_audit"]
    peitoral = audit["activation"][str(catalog.muscle_groups["Peitoral"])]

    assert peitoral["target"] == 0.0
    assert peitoral["deficit"] == 0.0
    assert draft["excluded_due_to_recovery"][0]["muscle_group_id"] == catalog.muscle_groups["Peitoral"]
    assert catalog.exercises["Supino reto"] not in {ex["mapping_id"] for ex in draft["exercises"]}
    assert catalog.exercises["Remada baixa"] in {ex["mapping_id"] for ex in draft["exercises"]}


def test_todos_os_grupos_bloqueados_e_erro_explicado(db_conn, catalog, gym, recovery):
    recovery(pain(catalog.muscle_groups["Peitoral"], joint_pain=True))
    template, slot = _simple(db_conn, catalog)
    from app.workout_plan_ai import WorkoutPlanGenerationError

    with pytest.raises(WorkoutPlanGenerationError, match="NO_DIRECT_WORK"):
        _generate(template, slot, catalog)


def test_progressao_manda_em_carga_e_reps(db_conn, catalog, gym, recovery):
    """A trilha do par tem carga 40 kg e o grupo esta na faixa 10-12: a ficha
    sai com esses numeros, nao com o default de calibracao (6-8, sem carga)."""
    recovery()
    _group_state(db_conn, catalog, "Peitoral", phase="F2", reps=(10, 12))
    _track(
        db_conn,
        catalog,
        "Supino reto",
        "Banco reto",
        muscle_group_id=catalog.muscle_groups["Peitoral"],
        load=40,
        sets_count=4,
    )

    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    supino = next(ex for ex in draft["exercises"] if ex["mapping_id"] == catalog.exercises["Supino reto"])

    assert supino["reps_target"] == "10-12"
    assert supino["suggested_load_kg"] == pytest.approx(40.0)
    assert supino["load_target"]
    # series da TRILHA (4), nao o default 3
    assert supino["sets_count"] == 4
    crucifixo = [ex for ex in draft["exercises"] if ex["mapping_id"] == catalog.exercises["Crucifixo"]]
    if crucifixo:
        # trilha nova = calibracao: faixa da fase do grupo, carga ainda nula
        assert crucifixo[0]["suggested_load_kg"] is None


# ---------------------------------------------------------------------------
# 4. Ordenacao por fadiga
# ---------------------------------------------------------------------------


def test_ordenacao_afasta_fadiga_compartilhada(db_conn, catalog, gym, recovery):
    """Dois de Peitoral e um de Costas no mesmo slot: o de Costas entra no
    meio, porque minimiza o overlap de fadiga com o anterior."""
    recovery()
    training_type_id = _training_type(
        db_conn,
        catalog,
        {"Peitoral": (4, 8, 12), "Costas": (4, 8, 12), "Tríceps": (2, 6, 20), "Bíceps": (2, 6, 20), "Ombros": (2, 6, 20)},
    )
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [
            {
                "name": "A",
                "items": [{"group": "Peitoral", "min": 50, "max": 75}, {"group": "Costas", "min": 20, "max": 30}],
            }
        ],
    )
    draft = _generate(template, template["slots"][0], catalog)
    grupos = [ex["muscle_primary"] for ex in draft["exercises"]]

    assert grupos.count("Peitoral") == 2
    assert grupos.count("Costas") == 1
    assert grupos[1] == "Costas", grupos  # nunca dois de peito seguidos


def test_prioritize_abre_a_sessao(db_conn, catalog, gym, recovery):
    recovery()
    training_type_id = _training_type(
        db_conn, catalog, {"Peitoral": (4, 8, 12), "Costas": (4, 8, 12), "Tríceps": (2, 6, 20), "Bíceps": (2, 6, 20)}
    )
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [
            {
                "name": "A",
                "items": [
                    {"group": "Peitoral", "min": 50, "max": 75},
                    {"group": "Costas", "min": 20, "max": 30, "prioritize": True},
                ],
            }
        ],
    )
    draft = _generate(template, template["slots"][0], catalog)
    assert draft["exercises"][0]["muscle_primary"] == "Costas"


# ---------------------------------------------------------------------------
# 5. Ciclos e slots
# ---------------------------------------------------------------------------


def _cycle_template(db_conn, catalog, slot_count: int, *, with_rest: bool = True):
    training_type_id = _training_type(
        db_conn, catalog, {"Peitoral": (4, 8, 12), "Costas": (4, 8, 12), "Tríceps": (2, 6, 20), "Bíceps": (2, 6, 20)}
    )
    slots = []
    for index in range(1, slot_count + 1):
        if with_rest and index == slot_count:
            slots.append({"name": f"S{index}", "slot_type": "descanso", "items": []})
        else:
            group = "Peitoral" if index % 2 else "Costas"
            slots.append({"name": f"S{index}", "items": [{"group": group, "min": 40, "max": 60}]})
    return _template(db_conn, catalog, training_type_id, slots)


@pytest.mark.parametrize("slot_count", [3, 4, 5, 9])
def test_ciclos_de_3_4_5_e_9_slots_com_descanso(db_conn, catalog, gym, recovery, slot_count):
    """Todo slot do ciclo gera - inclusive o 8o e o 9o, que o preview perdia
    antes do PR-13 (G7). O slot de descanso sai como dia de descanso, sem
    exercicio."""
    recovery()
    template = _cycle_template(db_conn, catalog, slot_count)
    for slot in template["slots"]:
        draft = _generate(template, slot, catalog)
        if slot["slot_type"] == "descanso":
            assert draft["is_rest_day"] is True
            assert draft["exercises"] == []
            continue
        assert draft["exercises"], f"slot {slot['sequence']} nao gerou exercicio"
        audit = draft["raw_payload"]["generation_audit"]
        assert audit["slot_sequence"] == slot["sequence"]
        # M76 Lote 4a: a janela e sempre a semana (ciclo > 7 corta o
        # excedente) e a meta entra sem escala.
        assert audit["window"]["days"] == 7
        assert audit["window"]["target_scale"] == 1.0
        assert audit["window"]["cycle_length"] == slot_count


def test_slot_explicito_e_slot_automatico(db_conn, catalog, gym, recovery):
    """`resolve_next_slot`: escolha manual devolve o slot pedido; automatica
    avanca a partir da ultima ficha salva, com wraparound."""
    recovery()
    from app.workout_plan_templates_engine import resolve_next_slot

    template = _cycle_template(db_conn, catalog, 3, with_rest=False)
    explicito = resolve_next_slot(template, catalog.user_id, explicit_slot_sequence=2)
    assert explicito["sequence"] == 2
    assert resolve_next_slot(template, catalog.user_id, explicit_slot_sequence=99) is None
    # sem ficha salva: comeca no primeiro
    assert resolve_next_slot(template, catalog.user_id)["sequence"] == 1

    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plans (user_id, name, plan_date, template_id, template_slot_sequence, status) "
            "VALUES (%s, 'x', %s, %s, 2, 'planned')",
            (catalog.user_id, hoje_do_usuario(), template["id"]),
        )
    db_conn.commit()
    assert resolve_next_slot(template, catalog.user_id)["sequence"] == 3

    draft = _generate(template, explicito, catalog)
    assert draft["template_slot_sequence"] == 2


def test_planejado_do_ciclo_desconta_da_necessidade(db_conn, catalog, gym, recovery):
    """Passo 1: ficha para daqui a TRES dias, ciclo de 3 slots. Os dois dias
    entre hoje e o dia do slot sao ocupados pelos slots anteriores do ciclo, e
    o que eles planejam para o grupo entra como `planejado` - reduzindo a
    necessidade deste slot."""
    recovery()
    training_type_id = _training_type(db_conn, catalog, {"Peitoral": (4, 8, 12), "Tríceps": (2, 6, 20)})
    template = _template(
        db_conn,
        catalog,
        training_type_id,
        [
            {"name": "A", "items": [{"group": "Peitoral", "min": 50, "max": 75}]},
            {"name": "B", "items": [{"group": "Peitoral", "min": 20, "max": 30}]},
            {"name": "C", "slot_type": "descanso", "items": []},
        ],
    )
    plan_date = (hoje_do_usuario() + timedelta(days=3)).isoformat()
    draft = _generate(template, template["slots"][0], catalog, plan_date=plan_date)
    audit = draft["raw_payload"]["generation_audit"]
    activation = audit["activation"][str(catalog.muscle_groups["Peitoral"])]

    # Andando do slot gerado (A) para tras, um slot por dia: hoje+2 recebe C
    # (descanso, zero) e hoje+1 recebe B. Em ordem cronologica: B, C.
    assert [item["slot_id"] for item in audit["window"]["projected_slots"]] == [
        template["slots"][1]["id"],
        template["slots"][2]["id"],
    ]
    # B planeja o ponto medio de 20-30% de 8 = 2.0
    assert activation["planned"] == pytest.approx(2.0)
    assert activation["need"] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# 6. Determinismo e rollback
# ---------------------------------------------------------------------------


def test_determinismo_byte_a_byte(db_conn, catalog, gym, recovery):
    """O criterio de aceite do PR: mesma entrada -> mesma ficha, byte a byte,
    sem LLM. Duas geracoes seguidas serializam identicas."""
    recovery()
    template, slot = _simple(db_conn, catalog)
    primeira = _generate(template, slot, catalog)
    segunda = _generate(template, slot, catalog)

    dump = lambda draft: json.dumps(draft, sort_keys=True, default=str)  # noqa: E731
    assert dump(primeira) == dump(segunda)
    assert primeira["raw_payload"]["generation_audit"]["llm_used"] is False
    assert primeira["raw_payload"]["used_ai"] is False


def test_rollback_volta_ao_motor_legado(db_conn, catalog, gym, recovery, monkeypatch):
    """`GENERATION_ENGINE=legacy` devolve o motor v2 (com LLM) na hora. Aqui a
    IA e substituida por uma falha, para o v2 cair no fallback greedy dele e o
    teste nao depender de rede: o que importa e a `engine_version`."""
    recovery()
    from app.intelligence import template_ai_selector_v2
    from app.workout_plan_ai import WorkoutPlanGenerationError

    monkeypatch.setenv("GENERATION_ENGINE", "legacy")

    def sem_ia(*args, **kwargs):
        raise WorkoutPlanGenerationError("sem rede no teste")

    monkeypatch.setattr(template_ai_selector_v2, "select_exercises_for_slot_v2", sem_ia)
    from app.generation import mode_a_legacy

    monkeypatch.setattr(mode_a_legacy, "select_exercises_for_slot_v2", sem_ia, raising=False)

    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    assert draft["raw_payload"]["engine_version"] == "v2"
    assert "generation_audit" not in draft["raw_payload"]


# ---------------------------------------------------------------------------
# 7. Congelamento (secao 6.3) com a origem do Modo A
# ---------------------------------------------------------------------------


def test_congelamento_grava_a_origem_do_modo_a(db_conn, catalog, gym, recovery):
    """A ficha salva a partir do rascunho do Modo A congela a prescricao com
    `engine_version='mode-a-deterministic-v1'` - e abrir duas vezes devolve o
    mesmo (contrato do PR-11, origem do PR-13)."""
    recovery()
    from app import services

    template, slot = _simple(db_conn, catalog)
    draft = _generate(template, slot, catalog)
    payload = {
        "name": draft["name"],
        "plan_date": hoje_do_usuario().isoformat(),
        "gym_id": catalog.gym_id,
        "template_id": template["id"],
        "template_slot_sequence": slot["sequence"],
        "status": "planned",
        "exercises": [
            {
                "sequence": index,
                "exercise_name": exercise["exercise_name"],
                "mapping_id": exercise["mapping_id"],
                "preferred_equipment_id": exercise["preferred_equipment_id"],
                "equipment_name": exercise["equipment_name"],
                "sets_count": exercise["sets_count"],
                "reps_target": exercise["reps_target"],
                "rpe_target": exercise["rpe_target"],
            }
            for index, exercise in enumerate(draft["exercises"], start=1)
        ],
    }
    plan = services.create_workout_plan(payload, user_id=catalog.user_id)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT engine_version, prescription_json FROM workout_plan_exercises WHERE plan_id = %s "
            "ORDER BY sequence",
            (plan["id"],),
        )
        rows = cur.fetchall()
    assert rows and all(row["engine_version"] == ENGINE_DETERMINISTIC for row in rows)
    snapshot = json.loads(rows[0]["prescription_json"])
    assert snapshot["engine_version"] == ENGINE_DETERMINISTIC
    assert snapshot["decision"]
    assert snapshot["sets_count"] == payload["exercises"][0]["sets_count"]

    primeira = services.get_workout_plan(plan["id"], user_id=catalog.user_id)
    segunda = services.get_workout_plan(plan["id"], user_id=catalog.user_id)
    assert json.dumps(primeira, sort_keys=True, default=str) == json.dumps(segunda, sort_keys=True, default=str)

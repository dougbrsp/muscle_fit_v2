"""PR-13 - Modo A deterministico: as decisoes que NAO precisam de banco.

Cobre, dos nove passos da secao 6.1 do PLANO_20260912.md:

- passo 1: a janela movel de 7 dias, o escalonamento de ciclo longo
  (`meta x ciclo/7`) e a projecao "1 slot por dia de treino";
- passo 2: `necessidade`, o `clamp` no envelope do modelo, o multiplicador de
  recuperacao e `NO_DIRECT_WORK -> 0`;
- passo 4: a seguranca FILTRA antes do ranking, com os tres motivos;
- passo 5: a ordem deterministica completa, criterio por criterio;
- passo 7: o deficit (e a prova de que tecnica/pico nao entram nele, D7);
- passo 8: a ordenacao por fadiga compartilhada;
- e a convivencia com o legado (flag desligada = motor antigo, e nenhuma
  referencia a LLM/RAG/reparo no modulo novo).

A geracao ponta a ponta esta em
`tests/integration/test_pr13_modo_a_deterministico.py`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app import generation
from app import progression_state as ps
from app.domain import progression_engine as pe
from app.generation import activation_window as aw
from app.generation import mode_a
from app.intelligence.template_volume_domain import (
    MuscleSlotBudget,
    TemplateCycleContext,
)
from app.intelligence.template_volume_validator import (
    fatigue_overlap,
    order_exercises_by_fatigue,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(generation.GENERATION_FLAG_ENV, raising=False)
    monkeypatch.delenv(ps.ENGINE_FLAG_ENV, raising=False)


def budget(
    *,
    group_id: int = 1,
    name: str = "Peitoral",
    weekly_target: float = 14.0,
    weekly_max: float = 20.0,
    weekly_min: float = 8.0,
    pct_min: float = 0.5,
    pct_max: float = 0.7,
    recovery_status: str = "READY",
    multiplier: float = 1.0,
    prioritize: bool = False,
) -> MuscleSlotBudget:
    return MuscleSlotBudget(
        muscle_group_id=group_id,
        muscle_group_name=name,
        weekly_min=weekly_min,
        weekly_target=weekly_target,
        weekly_max=weekly_max,
        slot_pct_min=pct_min,
        slot_pct_max=pct_max,
        nominal_slot_min=weekly_target * pct_min,
        nominal_slot_max=weekly_target * pct_max,
        recovery_status=recovery_status,
        recovery_multiplier=multiplier,
        prioritize=prioritize,
    )


# ---------------------------------------------------------------------------
# Passo 1 - a janela
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cycle_length", [0, 1, 3, 4, 5, 6, 7, 8, 9, 24])
def test_janela_de_7_dias_termina_no_dia_do_slot(cycle_length):
    window = aw.resolve_window(date(2026, 9, 18), cycle_length)
    assert window.end == date(2026, 9, 18)
    assert window.start == date(2026, 9, 12)
    assert window.days == 7
    assert window.target_scale == 1.0


def test_ciclo_maior_que_7_nao_alarga_a_janela_nem_escala_a_meta():
    """M76 Lote 4a (E1): a semana e sempre a do dono
    (`methodology_volume.weekly_window_days`). Ciclo de 9 slots corta o
    excedente em vez de virar uma janela de 9 dias com meta `x 9/7`."""
    window = aw.resolve_window(date(2026, 9, 18), 9)
    assert window.days == 7
    assert window.start == date(2026, 9, 12)
    assert window.target_scale == 1.0
    assert window.as_dict()["cycle_length"] == 9


def _context(slot_count: int) -> TemplateCycleContext:
    return TemplateCycleContext(
        template_id=1,
        training_type_id=1,
        slots=[
            {"id": 100 + seq, "sequence": seq, "name": f"S{seq}", "slot_type": "normal", "items": []}
            for seq in range(1, slot_count + 1)
        ],
        muscle_targets={},
        muscle_slot_allocations={},
        recovery_state={},
    )


def test_ficha_de_hoje_nao_tem_planejado():
    """Janela 100% historico real: nao ha dia entre hoje e hoje."""
    context = _context(4)
    assert (
        aw.projected_slot_ids(context, context.slots[1], date(2026, 9, 18), today=date(2026, 9, 18)) == []
    )


def test_ficha_no_passado_nao_tem_planejado():
    context = _context(4)
    assert (
        aw.projected_slot_ids(context, context.slots[1], date(2026, 9, 15), today=date(2026, 9, 18)) == []
    )


def test_planejado_projeta_1_slot_por_dia_com_wraparound():
    """Ficha para 3 dias a frente, ciclo de 4 slots, slot gerado = o 1o: os
    dois dias intermediarios recebem os slots que os antecedem no ciclo (com
    wraparound), um por dia, em ordem cronologica."""
    context = _context(4)
    projected = aw.projected_slot_ids(
        context, context.slots[0], date(2026, 9, 21), today=date(2026, 9, 18)
    )
    assert projected == [(date(2026, 9, 19), 103), (date(2026, 9, 20), 104)]


def test_planejado_ignora_slot_fora_do_ciclo():
    context = _context(3)
    assert (
        aw.projected_slot_ids(
            context, {"id": 999}, date(2026, 9, 21), today=date(2026, 9, 18)
        )
        == []
    )


# ---------------------------------------------------------------------------
# Passo 2 - o orcamento
# ---------------------------------------------------------------------------


def test_sem_historico_o_orcamento_e_o_do_modelo():
    """"Sem historico: so o modelo" - a necessidade fica acima do envelope e o
    `clamp` devolve o teto nominal do slot."""
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets([budget()], window, {}, {})
    assert result.realized == 0.0
    assert result.planned == 0.0
    assert result.need == 14.0
    assert result.target == pytest.approx(9.8)  # nominal_max = 14 x 0.7
    assert result.target_min == pytest.approx(7.0)  # nominal_min = 14 x 0.5


def test_realizado_e_planejado_descontam_da_necessidade():
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets([budget()], window, {1: 6.0}, {1: 2.0})
    assert result.need == pytest.approx(6.0)
    # 6.0 esta dentro do envelope [7.0, 9.8]? nao: abaixo do minimo -> clamp
    assert result.target == pytest.approx(7.0)


def test_necessidade_dentro_do_envelope_e_usada_como_esta():
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets([budget()], window, {1: 5.0}, {1: 1.0})
    assert result.need == pytest.approx(8.0)
    assert result.target == pytest.approx(8.0)


def test_dose_reduzida_multiplica_o_alvo():
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets(
        [budget(recovery_status="REDUCED_DOSE", multiplier=0.75)], window, {}, {}
    )
    assert result.target == pytest.approx(9.8 * 0.75)
    assert result.target_min == pytest.approx(7.0 * 0.75)
    assert result.is_excluded is False


def test_no_direct_work_zera_o_alvo():
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets(
        [budget(recovery_status="NO_DIRECT_WORK", multiplier=0.0)], window, {}, {}
    )
    assert result.target == 0.0
    assert result.is_excluded is True
    assert result.deficit(0.0) == 0.0


def test_meta_sem_escala_no_ciclo_longo():
    """Ciclo de 9 slots: a meta da JANELA e a semanal (`14`), sem `x 9/7` (M76
    Lote 4a). O envelope do SLOT continua sendo a fatia percentual da meta."""
    window = aw.resolve_window(date(2026, 9, 18), 9)
    [result] = aw.build_group_budgets([budget()], window, {}, {})
    assert result.target_weekly == pytest.approx(14.0)
    assert result.max_weekly == pytest.approx(20.0)
    assert result.nominal_max == pytest.approx(9.8)
    assert result.target == pytest.approx(9.8)


def test_deficit_e_a_sobra_do_piso():
    """Passo 7: o que sobra e reportado, nunca compensado com tecnica ou pico
    (D7) - o proprio contrato nao tem por onde fazer isso."""
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets([budget()], window, {}, {})
    assert result.deficit(4.0) == pytest.approx(3.0)
    assert result.deficit(7.0) == 0.0
    assert result.deficit(99.0) == 0.0
    audit = result.as_dict(4.0)
    assert audit["deficit"] == pytest.approx(3.0)
    assert audit["result"] == 4.0
    assert set(audit) >= {"target_min", "target_max", "realized", "planned", "result", "deficit"}


def test_headroom_projetado_desconta_realizado_e_planejado():
    window = aw.resolve_window(date(2026, 9, 18), 3)
    [result] = aw.build_group_budgets([budget()], window, {1: 12.0}, {1: 3.0})
    assert result.projected_headroom == pytest.approx(5.0)  # max 20 - 12 - 3


def test_grupos_saem_ordenados_por_id():
    window = aw.resolve_window(date(2026, 9, 18), 3)
    out = aw.build_group_budgets(
        [budget(group_id=7, name="G7"), budget(group_id=2, name="G2")], window, {}, {}
    )
    assert [item.muscle_group_id for item in out] == [2, 7]


# ---------------------------------------------------------------------------
# Passos 4 e 5 - seguranca filtra, preferencia ordena
# ---------------------------------------------------------------------------


def candidate(
    *,
    exercise_id: int = 1,
    equipment_id: int = 1,
    gym_equipment_id: int = 1,
    equipment_name: str = "Banco",
    stars=None,
    last_used=None,
    stimulus=None,
    fatigue=None,
    is_compound: bool = False,
) -> mode_a.Candidate:
    return mode_a.Candidate(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        gym_equipment_id=gym_equipment_id,
        exercise_name=f"Ex{exercise_id}",
        amazfit_name=None,
        equipment_name=equipment_name,
        stars=stars,
        last_used_by_user_at=last_used,
        stimulus=stimulus or {1: 1.0},
        fatigue=fatigue or {1: 1.0},
        is_compound=is_compound,
    )


def test_estrelas_ausentes_valem_3_e_nao_ganham_de_4():
    sem_nota = candidate(exercise_id=1, equipment_id=1, gym_equipment_id=1)
    quatro = candidate(exercise_id=2, equipment_id=2, gym_equipment_id=2, stars=4)
    ranked = mode_a.rank_candidates([sem_nota, quatro], frozenset())
    assert [item.exercise_id for item in ranked] == [2, 1]


def test_empate_de_estrelas_resolve_por_recencia_nulls_first():
    """"Variar entre os de maior preferencia" = quem o usuario nao usou vem
    antes; entre usados, o mais antigo primeiro."""
    nunca = candidate(exercise_id=1, equipment_id=1, gym_equipment_id=1, stars=5)
    antigo = candidate(
        exercise_id=2, equipment_id=2, gym_equipment_id=2, stars=5, last_used="2026-09-01T10:00:00"
    )
    recente = candidate(
        exercise_id=3, equipment_id=3, gym_equipment_id=3, stars=5, last_used="2026-09-17T10:00:00"
    )
    ranked = mode_a.rank_candidates([recente, antigo, nunca], frozenset())
    assert [item.exercise_id for item in ranked] == [1, 2, 3]


def test_exercicio_ja_na_sessao_cai_no_ranking():
    a = candidate(exercise_id=1, equipment_id=1, gym_equipment_id=1, stars=5)
    b = candidate(exercise_id=2, equipment_id=2, gym_equipment_id=2, stars=5)
    assert [item.exercise_id for item in mode_a.rank_candidates([a, b], frozenset())] == [1, 2]
    assert [item.exercise_id for item in mode_a.rank_candidates([a, b], frozenset({1}))] == [2, 1]


def test_desempate_final_por_nome_e_por_id():
    zebra = candidate(exercise_id=1, equipment_id=1, gym_equipment_id=9, equipment_name="Zebra")
    alfa_9 = candidate(exercise_id=2, equipment_id=2, gym_equipment_id=9, equipment_name="Alfa")
    alfa_1 = candidate(exercise_id=3, equipment_id=3, gym_equipment_id=1, equipment_name="Alfa")
    ranked = mode_a.rank_candidates([zebra, alfa_9, alfa_1], frozenset())
    assert [item.exercise_id for item in ranked] == [3, 2, 1]


def test_seguranca_filtra_antes_do_ranking_e_o_preferido_perde():
    """Caso da secao 13: o preferido por estrelas e INCOMPATIVEL com a tecnica
    requerida e perde para o compativel menos preferido. A preferencia nunca
    "ganha" de uma exclusao de seguranca, porque o passo 4 roda antes."""
    from tests.support import progression_fixtures as fx

    preferido = candidate(exercise_id=1, equipment_id=1, gym_equipment_id=1, stars=5)
    compativel = candidate(exercise_id=2, equipment_id=2, gym_equipment_id=2, stars=1)
    safety_by_pair = {
        (1, 1): fx.safety(exercise_id=1, equipment_id=1, max_safe_rpe=10, techniques={"drop_set": 0}),
        (2, 2): fx.safety(exercise_id=2, equipment_id=2, max_safe_rpe=10, techniques={"drop_set": 1}),
    }
    eligible, excluded = mode_a._filter_candidates(
        [preferido, compativel],
        safety_by_pair,
        required_technique="drop_set",
        track_technique_by_pair={},
    )
    assert [item.exercise_id for item in eligible] == [2]
    assert excluded[0]["reason"] == mode_a.REASON_TECHNIQUE_INCOMPATIBLE
    assert excluded[0]["stars"] == 5
    # e o ranking, rodando depois, so ve o que sobrou
    assert [item.exercise_id for item in mode_a.rank_candidates(eligible, frozenset())] == [2]


def test_tecnica_que_exige_falha_em_par_proibido():
    from tests.support import progression_fixtures as fx

    proibido = candidate(exercise_id=1, equipment_id=1, gym_equipment_id=1)
    safety_by_pair = {(1, 1): fx.safety(exercise_id=1, equipment_id=1, max_safe_rpe=9)}
    eligible, excluded = mode_a._filter_candidates(
        [proibido], safety_by_pair, required_technique="drop_set", track_technique_by_pair={}
    )
    assert eligible == []
    assert excluded[0]["reason"] == mode_a.REASON_FAILURE_REQUIRED_NOT_ALLOWED


def test_par_sem_equipamento_resolvido_e_unavailable():
    orfao = candidate(exercise_id=1, equipment_id=None, gym_equipment_id=None)
    eligible, excluded = mode_a._filter_candidates([orfao], {}, required_technique=None, track_technique_by_pair={})
    assert eligible == []
    assert excluded[0]["reason"] == mode_a.REASON_UNAVAILABLE


def test_par_sem_seguranca_resolvida_e_unavailable():
    """Par que nem aparece no resolvedor de seguranca: nao ha como afirmar que
    ele e seguro, entao ele nao entra (D3, conservador)."""
    eligible, excluded = mode_a._filter_candidates(
        [candidate()], {}, required_technique=None, track_technique_by_pair={}
    )
    assert eligible == []
    assert excluded[0]["reason"] == mode_a.REASON_UNAVAILABLE


def test_sem_tecnica_requerida_nada_e_excluido_por_tecnica():
    from tests.support import progression_fixtures as fx

    safety_by_pair = {(1, 1): fx.safety(exercise_id=1, equipment_id=1, max_safe_rpe=9, techniques={})}
    eligible, excluded = mode_a._filter_candidates(
        [candidate()], safety_by_pair, required_technique=None, track_technique_by_pair={}
    )
    assert [item.exercise_id for item in eligible] == [1]
    assert excluded == []


def test_tecnica_da_trilha_tambem_filtra():
    """A tecnica que a trilha daquele par JA usa precisa continuar executavel
    nele - senao o par nao e elegivel."""
    from tests.support import progression_fixtures as fx

    safety_by_pair = {(1, 1): fx.safety(exercise_id=1, equipment_id=1, max_safe_rpe=10, techniques={"gvt": 0})}
    eligible, excluded = mode_a._filter_candidates(
        [candidate()], safety_by_pair, required_technique=None, track_technique_by_pair={(1, 1): "gvt"}
    )
    assert eligible == []
    assert excluded[0]["reason"] == mode_a.REASON_TECHNIQUE_INCOMPATIBLE


def test_tecnica_requerida_vem_do_pending_apply_technique():
    from tests.support import progression_fixtures as fx

    assert mode_a.required_technique_for_group(None) is None
    assert mode_a.required_technique_for_group(fx.group()) is None
    assert (
        mode_a.required_technique_for_group(
            fx.group(pending_action=pe.APPLY_TECHNIQUE, pending_payload={"technique_code": "rest_pause"})
        )
        == "rest_pause"
    )
    # F4 sem tecnica escolhida ainda nao exige nada de ninguem
    assert (
        mode_a.required_technique_for_group(fx.group(pending_action=pe.APPLY_TECHNIQUE, pending_payload={}))
        is None
    )
    assert mode_a.required_technique_for_group(fx.group(pending_action=pe.ADD_SET)) is None


# ---------------------------------------------------------------------------
# Passo 8 - ordenacao por fadiga
# ---------------------------------------------------------------------------


def exercise(name: str, fatigue: dict[int, float], *, group_id: int = 1, compound: bool = False):
    return {
        "exercise_name": name,
        "primary_group_id": group_id,
        "is_compound": compound,
        "muscle_fatigue": fatigue,
    }


def test_overlap_e_a_soma_dos_minimos():
    assert fatigue_overlap({1: 1.0, 2: 0.5}, {1: 0.3, 2: 0.9}) == pytest.approx(0.8)
    assert fatigue_overlap({1: 1.0}, {2: 1.0}) == 0.0
    assert fatigue_overlap({}, {1: 1.0}) == 0.0


def test_ordem_base_prioriza_prioritize_depois_composto_depois_slot():
    exercises = [
        exercise("iso-normal", {5: 1.0}, group_id=1),
        exercise("comp-normal", {6: 1.0}, group_id=1, compound=True),
        exercise("iso-prioritario", {7: 1.0}, group_id=2),
    ]
    ordered = order_exercises_by_fatigue(exercises, {2})
    assert [item["exercise_name"] for item in ordered] == ["iso-prioritario", "comp-normal", "iso-normal"]


def test_greedy_afasta_exercicios_com_fadiga_compartilhada():
    """Dois de peito (grupo 1) e um de perna (grupo 4): o de perna entra no
    meio, porque minimiza o overlap com o anterior."""
    exercises = [
        exercise("peito A", {1: 1.0, 2: 0.5}),
        exercise("peito B", {1: 1.0, 2: 0.4}),
        exercise("perna", {4: 1.0}),
    ]
    ordered = order_exercises_by_fatigue(exercises, set())
    assert [item["exercise_name"] for item in ordered] == ["peito A", "perna", "peito B"]


def test_empate_de_overlap_resolve_pela_ordem_base():
    exercises = [
        exercise("base", {1: 1.0}),
        exercise("sem overlap 1", {2: 1.0}),
        exercise("sem overlap 2", {3: 1.0}),
    ]
    ordered = order_exercises_by_fatigue(exercises, set())
    assert [item["exercise_name"] for item in ordered] == ["base", "sem overlap 1", "sem overlap 2"]


def test_ordenacao_e_estavel_e_nao_altera_a_composicao():
    exercises = [exercise("a", {1: 1.0}), exercise("b", {2: 1.0})]
    ordered = order_exercises_by_fatigue(exercises, set())
    assert len(ordered) == 2
    assert order_exercises_by_fatigue(exercises, set()) == ordered
    assert order_exercises_by_fatigue([], set()) == []


# ---------------------------------------------------------------------------
# Convivencia com o legado (a promessa da flag)
# ---------------------------------------------------------------------------


def test_flag_desligada_delega_ao_motor_legado(monkeypatch):
    """Com `GENERATION_ENGINE` ausente (producao hoje), quem roda e o motor v2
    com LLM, byte a byte - o motor novo nem comeca."""
    chamado = {}

    def fake_legacy(**kwargs):
        chamado.update(kwargs)
        return {"exercises": [], "raw_payload": {"engine_version": "v2"}}

    from app.generation import mode_a_legacy

    monkeypatch.setattr(mode_a_legacy, "generate_plan_draft_from_template_v2", fake_legacy)
    out = mode_a.generate_plan_draft_from_template_a(
        template={"id": 1, "name": "M"},
        slot={"id": 2, "sequence": 1, "name": "A", "slot_type": "normal", "items": []},
        gym_id=None,
        gym_name=None,
        plan_date=None,
        user_id=7,
    )
    assert out["raw_payload"]["engine_version"] == "v2"
    assert chamado["user_id"] == 7


@pytest.mark.parametrize(
    "generation_engine, progression_engine, deterministico",
    [
        (None, None, False),
        ("deterministic", None, False),
        ("deterministic", "shadow", False),
        ("deterministic", "v2", True),
        ("legacy", "v2", False),
    ],
)
def test_modo_a_usa_a_mesma_guarda_de_flags_do_modo_b(
    monkeypatch, generation_engine, progression_engine, deterministico
):
    """O PR-13 nao cria flag nova: e a MESMA `deterministic_enabled()` do
    PR-12, que exige as duas flags juntas."""
    if generation_engine:
        monkeypatch.setenv(generation.GENERATION_FLAG_ENV, generation_engine)
    if progression_engine:
        monkeypatch.setenv(ps.ENGINE_FLAG_ENV, progression_engine)
    assert generation.deterministic_enabled() is deterministico


def test_motor_novo_nao_tem_llm_rag_nem_reparo():
    """Secao 6.1: `template_ai_selector_v2`, o RAG da geracao e
    `repair_slot_exercises`/`split_concentrated_exercises` saem do caminho. Eles
    existem para consertar saida de LLM e sobrevivem SO no modulo legado, que
    sai no PR-19."""
    novo = Path(mode_a.__file__).read_text(encoding="utf-8")
    for proibido in (
        "template_ai_selector_v2",
        "select_exercises_for_slot_v2",
        "select_rag_text",
        "repair_slot_exercises",
        "split_concentrated_exercises",
        "enforce_minimum_sets_floor",
        "order_exercises_for_session",
        "call_openai",
    ):
        assert proibido not in novo, proibido

    legado = Path(mode_a.__file__).parent / "mode_a_legacy.py"
    texto_legado = legado.read_text(encoding="utf-8")
    assert "select_exercises_for_slot_v2" in texto_legado
    assert "select_rag_text" in texto_legado


def test_engine_version_do_modo_a_e_o_do_congelamento():
    """Uma fonte para o valor: o mesmo texto no rascunho e no snapshot."""
    from app.generation.deterministic_prescription import MODE_A_ENGINE_VERSION

    assert mode_a.ENGINE_VERSION == MODE_A_ENGINE_VERSION == "mode-a-deterministic-v1"
    assert mode_a.ENGINE_VERSION != "mode-b-deterministic-v1"

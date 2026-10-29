"""Motor puro da metodologia de volume por grupo (Fase 4, PR C; Fase 6, PR 2):
faixas celula a celula com origem, cada status, ciclos 3/7/9, agregacao de
fatias, Modo A nas duas formas, intencao mudando so a severidade e
qualidade de dado. Sem banco, sem relogio.

Fase 6: as faixas vem do cadastro de macro-grupos (057). O fixture e o seed
da 057 (`tests.support.methodology_paridade.seed_volume_classes`, lido do
proprio modulo da migracao), entao os numeros aqui sao os mesmos da v1. Os
casos novos: eixo semanal, status combinado (com conflito),
`NO_RANGE_FOR_FREQUENCY`, grupo sem macro-grupo, fatia herdando o macro-grupo
do pai e a paridade com o motor antigo.
"""

from __future__ import annotations

import pytest

import dataclasses
import json
from pathlib import Path

from app.intelligence import methodology_volume as mv
from app.intelligence.dashboard import metrics_registry as reg
from tests.support import methodology_paridade as paridade
from tests.support.methodology_paridade import SIZE_TO_CLASS_ID, grupo, seed_volume_classes

SEED = seed_volume_classes()
GRANDES, PEQUENOS, OUTROS = SIZE_TO_CLASS_ID["large"], SIZE_TO_CLASS_ID["small"], SIZE_TO_CLASS_ID["abs"]
PARIDADE_PATH = Path(__file__).resolve().parent / "data" / "methodology_volume_paridade_pre_f6.json"

# ---------------------------------------------------------------------------
# Cenario: grupos reais da 002 (ids) com porte/pai da 055
# ---------------------------------------------------------------------------

PEITORAL, PEITORAL_SUP = 52, 12
COSTAS_LARG, COSTAS_MIOLO = 6, 7
BICEPS, TRICEPS, ABDOMEN, CORE = 5, 14, 1, 55

GROUPS = (
    grupo(PEITORAL, "Peitoral", "large"),
    grupo(PEITORAL_SUP, "Peitoral superior", "large", PEITORAL),
    grupo(COSTAS_LARG, "Costas - largura dorsal", "large"),
    grupo(COSTAS_MIOLO, "Costas - miolo/densidade", "large", COSTAS_LARG),
    grupo(BICEPS, "Biceps", "small"),
    grupo(TRICEPS, "Triceps", "small"),
    grupo(ABDOMEN, "Abdomen", "abs"),
    grupo(CORE, "Core", "unclassified"),
)

# mapping_id -> grupos com PRIMARY ativa. 108 nao esta aqui de proposito
# (item sem contribuicao PRIMARY). 109 e "puxada": PRIMARY costas; o biceps e
# so SECONDARY, logo NAO aparece.
PRIMARY = {
    100: {PEITORAL},  # supino reto (academia A)
    107: {PEITORAL},  # supino reto (academia B) - mesmo canonico do 100
    101: {PEITORAL_SUP},  # supino inclinado
    110: {PEITORAL},  # crucifixo
    111: {PEITORAL_SUP},  # crossover alto
    112: {PEITORAL},  # paralelas
    113: {PEITORAL},  # peck deck
    102: {COSTAS_LARG},
    103: {COSTAS_MIOLO},
    109: {COSTAS_LARG},
    104: {BICEPS},
    105: {ABDOMEN},
    106: {TRICEPS},
}
CANONICAL = {100: 1000, 107: 1000, 101: 1001, 110: 1002, 111: 1003, 112: 1004, 113: 1005,
             102: 1010, 103: 1011, 109: 1012, 104: 1020, 105: 1030, 106: 1040}


def item(mapping_id: int, sets_count: int | None = 3, canonical: bool = True) -> mv.MethodologyItemInput:
    return mv.MethodologyItemInput(
        mapping_id=mapping_id,
        canonical_exercise_id=CANONICAL.get(mapping_id) if canonical else None,
        sets_count=sets_count,
    )


def treino(seq: int, *items: mv.MethodologyItemInput, slot_type: str = "normal") -> mv.MethodologySlotInput:
    return mv.MethodologySlotInput(sequence=seq, slot_type=slot_type, name=f"S{seq}", items=tuple(items))


def descanso(seq: int) -> mv.MethodologySlotInput:
    return mv.MethodologySlotInput(sequence=seq, slot_type="descanso", name="Descanso")


def week(*training_slots: mv.MethodologySlotInput) -> list[mv.MethodologySlotInput]:
    """Ciclo de 7 dias: os slots dados + descanso ate completar 7."""
    slots = list(training_slots)
    seq = len(slots) + 1
    while len(slots) < 7:
        slots.append(descanso(seq))
        seq += 1
    return slots


def evaluate(slots, intent=None, volume_mode="equipment_series", groups=GROUPS, primary=PRIMARY, classes=None):
    return mv.evaluate_methodology_volume(slots, primary, groups, volume_mode, intent, SEED if classes is None else classes)


def block(result: mv.MethodologyVolumeEvaluation, group_id: int) -> mv.GroupEvaluation:
    found = [g for g in result.groups if g.muscle_group_id == group_id]
    assert len(found) == 1, f"bloco {group_id} ausente ou duplicado: {[g.muscle_group_id for g in result.groups]}"
    return found[0]


# ---------------------------------------------------------------------------
# Tabela de faixas: valor E origem, celula a celula
# ---------------------------------------------------------------------------

M, D = mv.RangeSource.METHODOLOGY, mv.RangeSource.DERIVED


@pytest.mark.parametrize(
    "size_class,bucket,exercises,sets,ex_source,sets_source,evidence",
    [
        ("large", "1x", (4, 5), (15, 20), M, M, "EXG-015"),
        ("large", "2x", (3, 4), (9, 16), M, M, "EXG-016"),
        ("large", "3x_plus", (2, 3), (6, 12), D, D, "proposta D2"),
        ("small", "1x", (3, 4), (9, 16), M, M, "EXG-017"),
        ("small", "2x", (2, 3), (6, 12), M, M, "EXG-018"),
        ("small", "3x_plus", (1, 2), (3, 8), D, D, "proposta D2"),
        ("abs", "2x", (2, 3), (6, 12), M, D, "EXG-020"),
        ("abs", "1x", (2, 3), (6, 12), D, D, "proposta D1"),
        ("abs", "3x_plus", (1, 2), (3, 8), D, D, "proposta D2"),
    ],
)
def test_tabela_de_faixas_celula_a_celula_com_origem(size_class, bucket, exercises, sets, ex_source, sets_source, evidence):
    """O seed da 057 (fixture) diz exatamente o que a tabela da v1 dizia."""
    cell = SEED[SIZE_TO_CLASS_ID[size_class]].range_for(bucket)
    assert cell is not None
    assert (cell.exercises_min, cell.exercises_max) == exercises
    assert (cell.sets_min, cell.sets_max) == sets
    assert cell.exercises_source is ex_source
    assert cell.sets_source is sets_source
    assert evidence in cell.exercises_evidence
    # toda celula `methodology` cita um EXG-NNN; toda `derived` diz a regra
    for source, text in ((cell.exercises_source, cell.exercises_evidence), (cell.sets_source, cell.sets_evidence)):
        if source is M:
            assert text.startswith("EXG-")
        else:
            assert text.startswith("derived (proposta D")


def test_motor_nao_tem_mais_faixa_em_codigo():
    """Fase 6: as faixas saem do motor (vivem no cadastro da 057)."""
    for nome in ("METHODOLOGY_RANGES", "methodology_range", "_derive_3x_plus"):
        assert not hasattr(mv, nome), nome
    # sem cadastro, nenhum grupo e avaliado - nunca uma faixa inventada
    result = evaluate(week(treino(1, item(100, 4))), classes={})
    assert block(result, PEITORAL).direct_sets_axis.reason == "UNCLASSIFIED_GROUP"


def test_abdomen_series_sao_derivadas_da_regra_de_exg_004():
    cell = SEED[OUTROS].range_for("2x")
    assert cell.sets_min == 2 * reg.threshold("methodology_volume_sets_per_exercise_min")
    assert cell.sets_max == 3 * reg.threshold("methodology_volume_sets_per_exercise_max")
    assert "EXG-004" in cell.sets_evidence


def test_seed_tem_meta_semanal_de_2x_a_faixa_por_sessao():
    for spec in SEED.values():
        two = spec.range_for("2x")
        assert (spec.weekly_sets_min, spec.weekly_sets_max) == (2 * two.sets_min, 2 * two.sets_max)
        assert spec.weekly_source is mv.RangeSource.DERIVED


@pytest.mark.parametrize(
    "weekly_frequency,bucket",
    [(0.0, None), (0.33, "1x"), (1.0, "1x"), (1.49, "1x"), (1.5, "2x"), (1.56, "2x"), (2.33, "2x"), (2.5, "3x_plus"), (4.67, "3x_plus")],
)
def test_bucket_de_frequencia(weekly_frequency, bucket):
    assert mv.frequency_bucket_for(weekly_frequency) == bucket


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_metrica_registrada_e_status_do_dominio_proprio():
    result = evaluate(week(treino(1, item(100))))
    assert result.metric == "methodology_volume"
    assert result.metric_version == reg.metric_version("methodology_volume") == "methodology-volume-v5"
    assert mv.METHODOLOGY_VOLUME_STATUS_CODES == (
        "WITHIN_RANGE", "BELOW_RANGE", "ABOVE_RANGE", "NOT_DIRECTLY_TRAINED", "NOT_EVALUABLE",
    )
    # nunca `sets`/`volume` sem unidade no payload
    campos = set(vars(block(result, PEITORAL)))
    assert "direct_sets_per_session" in campos and "direct_exercises_per_session" in campos
    assert not campos & {"sets", "volume", "exercises"}


# ---------------------------------------------------------------------------
# Status: cada um alcancado (Modo B, ciclo de 7 dias, peitoral grande 1x)
# ---------------------------------------------------------------------------


def test_within_range_peitoral_4_exercicios_15_series_em_1x():
    # 100 e 107 sao o MESMO canonico (1000) -> contam 1 exercicio, series somam
    result = evaluate(week(treino(1, item(100, 3), item(107, 3), item(101, 3), item(110, 3), item(111, 3))))
    chest = block(result, PEITORAL)
    assert chest.frequency_raw == 1 and chest.weekly_frequency == 1.0 and chest.frequency_bucket == "1x"
    assert chest.frequency_is_normalized is False
    assert chest.direct_exercises_per_session == 4.0
    assert chest.direct_sets_per_session == 15.0
    assert chest.direct_exercises_axis.status == "WITHIN_RANGE"
    assert chest.direct_sets_axis.status == "WITHIN_RANGE"
    assert chest.direct_exercises_axis.severity is None
    assert (chest.direct_exercises_axis.range_min, chest.direct_exercises_axis.range_max) == (4, 5)
    assert chest.direct_exercises_axis.range_source is M
    assert chest.direct_exercises_axis.range_evidence == "EXG-015"


def test_canonico_repetido_em_dois_mappings_conta_um_exercicio():
    result = evaluate(week(treino(1, item(100, 4), item(107, 4))))
    chest = block(result, PEITORAL)
    assert chest.direct_exercises_cycle_total == 1
    assert chest.direct_sets_cycle_total == 8
    assert chest.sessions == (mv.SessionDirectWork(1, "S1", 8, 1),)


def test_below_range_sem_tolerancia_e_com_warning_no_padrao():
    # 3 exercicios / 12 series em 1x grande: faixa 4-5 / 15-20. Um abaixo
    # (5% de 4 seria 0,2 - se houvesse tolerancia, 3,8 "passaria"; nao passa).
    result = evaluate(week(treino(1, item(100, 4), item(101, 4), item(110, 4))))
    chest = block(result, PEITORAL)
    assert chest.direct_exercises_axis.status == "BELOW_RANGE"
    assert chest.direct_sets_axis.status == "BELOW_RANGE"
    assert chest.direct_exercises_axis.severity == "warning"
    assert chest.direct_sets_axis.severity == "warning"


def test_above_range():
    result = evaluate(week(treino(1, *(item(m, 4) for m in (100, 101, 110, 111, 112, 113)))))
    chest = block(result, PEITORAL)
    assert chest.direct_exercises_per_session == 6.0 and chest.direct_sets_per_session == 24.0
    assert chest.direct_exercises_axis.status == "ABOVE_RANGE"
    assert chest.direct_sets_axis.status == "ABOVE_RANGE"
    assert chest.direct_sets_axis.severity == "warning"


def test_grupo_so_secundario_e_not_directly_trained_nunca_below():
    # 109 (puxada) e PRIMARY de costas; biceps so e SECONDARY -> zero direto
    result = evaluate(week(treino(1, item(109, 4))))
    biceps = block(result, BICEPS)
    assert biceps.frequency_raw == 0 and biceps.frequency_bucket is None
    assert biceps.direct_exercises_axis.status == "NOT_DIRECTLY_TRAINED"
    assert biceps.direct_sets_axis.status == "NOT_DIRECTLY_TRAINED"
    assert biceps.direct_exercises_axis.severity is None
    assert biceps.weekly_projection is None
    assert block(result, TRICEPS).direct_sets_axis.status == "NOT_DIRECTLY_TRAINED"


def test_grupo_nao_classificado_e_not_evaluable_mesmo_com_trabalho_direto():
    primary = {**PRIMARY, 120: {CORE}}
    result = evaluate(week(treino(1, mv.MethodologyItemInput(120, 1200, 3))), primary=primary)
    core = block(result, CORE)
    assert core.direct_sets_per_session == 3.0
    assert core.direct_exercises_axis.status == "NOT_EVALUABLE"
    assert core.direct_exercises_axis.reason == "UNCLASSIFIED_GROUP"
    assert core.direct_sets_axis.reason == "UNCLASSIFIED_GROUP"


def test_sem_sessao_de_treino_tudo_not_evaluable():
    result = evaluate([descanso(1), descanso(2), descanso(3)])
    assert result.training_sessions == 0 and result.cycle_days == 3
    for g in result.groups:
        assert g.direct_sets_axis.status == "NOT_EVALUABLE"
        assert g.direct_sets_axis.reason == "NO_TRAINING_SESSIONS"
        assert g.direct_exercises_axis.reason == "NO_TRAINING_SESSIONS"


def test_slot_treino_do_plano_e_normal_do_modelo_sao_sessao():
    assert mv.MethodologySlotInput(1, "treino").is_training_session
    assert mv.MethodologySlotInput(1, "normal").is_training_session
    assert not mv.MethodologySlotInput(1, "descanso").is_training_session


def test_volume_mode_desconhecido_e_intencao_desconhecida_falham_cedo():
    with pytest.raises(ValueError, match="volume_mode"):
        evaluate(week(), volume_mode="weird")
    with pytest.raises(ValueError, match="intencao"):
        mv.MethodologyIntent(kind="bulking")


# ---------------------------------------------------------------------------
# Janela de 7 dias a partir do primeiro slot de treino (v3)
# ---------------------------------------------------------------------------

PEITO_12 = (item(100, 4), item(101, 4), item(110, 4))  # 3 exercicios / 12 series


def test_janela_ciclo_8_slots_grupo_1x_frequencia_inteira():
    # Relato do usuario: 8 slots com o grupo 1x dava 7/8 = 0,875 (~0,9).
    slots = [treino(1, *PEITO_12), *[descanso(i) for i in range(2, 9)]]
    result = evaluate(slots)
    assert (result.cycle_days, result.weekly_factor, result.frequency_is_normalized) == (8, 1.0, True)
    assert (result.window_days, result.window_start_sequence) == (7, 1)
    chest = block(result, PEITORAL)
    assert chest.frequency_raw == 1 and chest.weekly_frequency == 1.0 and chest.frequency_bucket == "1x"
    assert chest.weekly_sets_axis.value_weekly == 12.0
    assert chest.direct_sets_per_session == 12.0
    assert chest.direct_sets_axis.range_evidence == "EXG-015"


def test_janela_ciclo_8_slots_oitavo_slot_fica_fora():
    slots = [treino(1, *PEITO_12), *[descanso(i) for i in range(2, 8)], treino(8, item(100, 5))]
    chest = block(evaluate(slots), PEITORAL)
    assert chest.frequency_raw == 1 and chest.frequency_bucket == "1x"
    assert chest.weekly_sets_axis.value_weekly == 12.0 and chest.direct_sets_per_session == 12.0
    # o ciclo continua inteiro no payload (card M43 / quebra por sessao)
    assert chest.direct_sets_cycle_total == 17
    assert [s.slot_sequence for s in chest.sessions] == [1, 8]


def test_janela_ciclo_3_slots_da_a_volta():
    # slots 1,2,3,1,2,3,1: o peito (slot 1) cai 3 vezes na janela
    result = evaluate([treino(1, *PEITO_12), descanso(2), descanso(3)])
    assert (result.cycle_days, result.weekly_factor, result.frequency_is_normalized) == (3, 1.0, True)
    chest = block(result, PEITORAL)
    assert chest.frequency_raw == 3 and chest.weekly_frequency == 3.0 and chest.frequency_bucket == "3x_plus"
    assert chest.direct_sets_per_session == 12.0 and chest.direct_exercises_per_session == 3.0
    assert chest.weekly_sets_axis.value_weekly == 36.0
    assert chest.weekly_projection.direct_sets_weekly == 36.0
    assert chest.direct_sets_cycle_total == 12 and len(chest.sessions) == 1


def test_janela_media_pondera_sessao_que_repete():
    # 3 slots A(12), B(6), descanso: janela A,B,-,A,B,-,A -> A 3x, B 2x;
    # media = (3x12 + 2x6) / 5 = 9,6
    slots = [treino(1, *PEITO_12), treino(2, item(100, 3), item(110, 3)), descanso(3)]
    chest = block(evaluate(slots), PEITORAL)
    assert chest.frequency_raw == 5
    assert chest.direct_sets_per_session == 9.6
    assert chest.weekly_sets_axis.value_weekly == 48.0


def test_janela_comeca_no_primeiro_slot_de_treino():
    slots = [descanso(1), descanso(2), treino(3, *PEITO_12), *[descanso(i) for i in range(4, 9)]]
    result = evaluate(slots)
    assert result.window_start_sequence == 3
    assert block(result, PEITORAL).frequency_raw == 1
    # ciclo curto comecando em descanso: 2,3,1,2,3,1,2 -> slot 2 cai 3 vezes
    assert mv.evaluation_window([descanso(1), treino(2), descanso(3)]) == (2, {2: 3})
    # 9 slots comecando no 2: janela 2..8, o slot 9 fica fora (e o 1, descanso)
    eight = [treino(1), treino(2), *[descanso(i) for i in range(3, 9)]]
    assert mv.evaluation_window(eight) == (1, {1: 1, 2: 1})
    eight_late = [descanso(1), treino(2), *[descanso(i) for i in range(3, 8)], treino(8), treino(9)]
    assert mv.evaluation_window(eight_late) == (2, {2: 1, 8: 1})
    assert mv.evaluation_window([descanso(1), descanso(2)]) == (None, {})


def test_janela_ciclo_7_slots_igual_a_v2():
    result = evaluate(week(treino(1, item(100)), treino(2, item(100))))
    assert result.weekly_factor == 1.0 and result.frequency_is_normalized is False
    chest = block(result, PEITORAL)
    assert chest.frequency_raw == 2 and chest.weekly_frequency == 2.0 and chest.frequency_bucket == "2x"
    # comecar em descanso nao muda nada num ciclo de 7: a janela da a volta e
    # cobre cada slot uma vez
    shifted = [descanso(1), treino(2, item(100)), treino(3, item(100)), *[descanso(i) for i in range(4, 8)]]
    chest_shifted = block(evaluate(shifted), PEITORAL)
    assert (chest_shifted.frequency_raw, chest_shifted.direct_sets_per_session) == (2, chest.direct_sets_per_session)


def test_janela_ciclo_9_slots_dois_treinos_dentro():
    slots = [treino(1, *PEITO_12), *[descanso(i) for i in range(2, 6)],
             treino(6, *PEITO_12), descanso(7), descanso(8), descanso(9)]
    result = evaluate(slots)
    assert result.cycle_days == 9 and result.weekly_factor == 1.0
    chest = block(result, PEITORAL)
    assert chest.frequency_raw == 2 and chest.weekly_frequency == 2.0
    assert chest.frequency_is_normalized is True
    assert chest.frequency_bucket == "2x"
    assert chest.direct_sets_cycle_total == 24
    assert chest.weekly_projection.direct_sets_weekly == 24.0


def test_faixa_3x_plus_derivada_e_usada_e_declarada_derived():
    result = evaluate(week(*(treino(i, item(100, 4), item(110, 4)) for i in (1, 2, 3))))
    chest = block(result, PEITORAL)
    assert chest.frequency_bucket == "3x_plus"
    assert chest.direct_exercises_axis.status == "WITHIN_RANGE"
    assert chest.direct_exercises_axis.range_source is D
    assert "proposta D2" in chest.direct_exercises_axis.range_evidence


# ---------------------------------------------------------------------------
# Agregacao de fatias
# ---------------------------------------------------------------------------


def test_fatia_e_pai_somam_no_pai_e_fatia_nao_vira_bloco():
    # 101/111 sao PRIMARY de Peitoral superior (fatia), 100/110 de Peitoral
    result = evaluate(week(treino(1, item(100, 3), item(101, 4), item(110, 3), item(111, 5))))
    ids = [g.muscle_group_id for g in result.groups]
    assert PEITORAL_SUP not in ids and COSTAS_MIOLO not in ids
    chest = block(result, PEITORAL)
    assert chest.member_group_ids == (PEITORAL_SUP, PEITORAL)
    assert chest.direct_exercises_per_session == 4.0
    assert chest.direct_sets_per_session == 15.0
    assert chest.direct_exercises_axis.status == "WITHIN_RANGE"


def test_fatia_sozinha_e_avaliada_no_bloco_do_pai():
    result = evaluate(week(treino(1, item(103, 4))))  # so costas miolo
    back = block(result, COSTAS_LARG)
    assert back.frequency_raw == 1 and back.direct_exercises_cycle_total == 1
    assert back.direct_exercises_axis.status == "BELOW_RANGE"


def test_fatia_com_pai_desconhecido_vira_bloco_proprio_e_conta_na_qualidade():
    groups = (grupo(PEITORAL_SUP, "Peitoral superior", "large", 999),)
    result = evaluate(week(treino(1, item(101, 4))), groups=groups)
    assert [g.muscle_group_id for g in result.groups] == [PEITORAL_SUP]
    assert result.data_quality.groups_with_unknown_parent == 1


# ---------------------------------------------------------------------------
# Modo A (percent_budget)
# ---------------------------------------------------------------------------


def test_modo_a_com_sets_count_avalia_series_e_nao_exercicios():
    slots = week(treino(1, mv.MethodologyItemInput(muscle_group_id=PEITORAL, sets_count=16)))
    result = evaluate(slots, volume_mode="percent_budget")
    chest = block(result, PEITORAL)
    assert chest.direct_exercises_axis.status == "NOT_EVALUABLE"
    assert chest.direct_exercises_axis.reason == "MODE_A_NO_EXERCISES"
    assert chest.direct_sets_axis.status == "WITHIN_RANGE"
    assert chest.direct_sets_per_session == 16.0
    # grupo sem item no Modo A continua musculo zero no eixo de series
    assert block(result, BICEPS).direct_sets_axis.status == "NOT_DIRECTLY_TRAINED"
    assert block(result, BICEPS).direct_exercises_axis.reason == "MODE_A_NO_EXERCISES"


def test_modo_a_so_percentual_nao_e_avaliavel_em_nenhum_eixo():
    slots = week(treino(1, mv.MethodologyItemInput(muscle_group_id=PEITORAL, sets_count=None)))
    result = evaluate(slots, volume_mode="percent_budget")
    for g in result.groups:
        assert g.direct_exercises_axis.reason == "MODE_A_PERCENT_ONLY"
        assert g.direct_sets_axis.reason == "MODE_A_PERCENT_ONLY"
    assert result.data_quality.items_without_sets_count == 1


# ---------------------------------------------------------------------------
# Intencao: severidade muda, status nao
# ---------------------------------------------------------------------------

BELOW_CHEST = week(treino(1, item(100, 4), item(101, 4), item(110, 4)))  # 3 ex / 12 series em 1x
ABOVE_CHEST = week(treino(1, *(item(m, 4) for m in (100, 101, 110, 111, 112, 113))))  # 6 ex / 24


def test_cutting_abaixo_vira_informational_e_acima_continua_warning():
    cutting = mv.MethodologyIntent("cutting")
    below = block(evaluate(BELOW_CHEST, cutting), PEITORAL)
    assert below.direct_sets_axis.status == "BELOW_RANGE"  # status honesto
    assert below.direct_sets_axis.severity == "informational"
    assert below.direct_exercises_axis.severity == "informational"
    # sem piso novo (D5): a faixa comparada e a mesma do padrao
    assert (below.direct_sets_axis.range_min, below.direct_sets_axis.range_max) == (15, 20)
    above = block(evaluate(ABOVE_CHEST, cutting), PEITORAL)
    assert above.direct_sets_axis.status == "ABOVE_RANGE" and above.direct_sets_axis.severity == "warning"


def test_especializacao_alvo_acima_informational_alvo_abaixo_warning():
    spec = mv.MethodologyIntent("specialization", frozenset({PEITORAL_SUP}))  # alvo pela FATIA resolve no bloco
    above = block(evaluate(ABOVE_CHEST, spec), PEITORAL)
    assert above.is_intent_target is True
    assert above.direct_exercises_axis.status == "ABOVE_RANGE"
    assert above.direct_exercises_axis.severity == "informational"
    below = block(evaluate(BELOW_CHEST, spec), PEITORAL)
    assert below.direct_exercises_axis.status == "BELOW_RANGE"
    assert below.direct_exercises_axis.severity == "warning"


def test_especializacao_nao_alvo_abaixo_e_manutencao_esperada():
    spec = mv.MethodologyIntent("specialization", frozenset({BICEPS}))
    below = block(evaluate(BELOW_CHEST, spec), PEITORAL)
    assert below.is_intent_target is False
    assert below.direct_exercises_axis.status == "BELOW_RANGE"
    assert below.direct_exercises_axis.severity == "informational"
    above = block(evaluate(ABOVE_CHEST, spec), PEITORAL)
    assert above.direct_exercises_axis.severity == "warning"


def test_manutencao_com_e_sem_alvo():
    assert block(evaluate(BELOW_CHEST, mv.MethodologyIntent("maintenance")), PEITORAL).direct_sets_axis.severity == "informational"
    only_biceps = mv.MethodologyIntent("maintenance", frozenset({BICEPS}))
    assert block(evaluate(BELOW_CHEST, only_biceps), PEITORAL).direct_sets_axis.severity == "warning"


def test_padrao_e_sempre_warning_e_intencao_nunca_muda_status():
    for kind in mv.INTENT_KINDS:
        intent = mv.MethodologyIntent(kind, frozenset({PEITORAL}))
        assert block(evaluate(BELOW_CHEST, intent), PEITORAL).direct_sets_axis.status == "BELOW_RANGE"
        assert block(evaluate(ABOVE_CHEST, intent), PEITORAL).direct_sets_axis.status == "ABOVE_RANGE"
    assert block(evaluate(BELOW_CHEST), PEITORAL).direct_sets_axis.severity == "warning"


# ---------------------------------------------------------------------------
# Qualidade de dado e coluna semanal
# ---------------------------------------------------------------------------


def test_item_sem_primary_entra_na_qualidade_e_nao_conta_em_nenhum_grupo():
    result = evaluate(week(treino(1, item(100, 4), mv.MethodologyItemInput(108, 1080, 4))))
    assert result.data_quality.items_without_primary_contribution == 1
    assert sum(g.direct_sets_cycle_total for g in result.groups) == 4


def test_item_sem_canonico_conta_pelo_mapping_e_na_qualidade():
    # 100 e 107 sem canonico: viram 2 exercicios (mapping distintos)
    result = evaluate(week(treino(1, item(100, 3, canonical=False), item(107, 3, canonical=False))))
    assert block(result, PEITORAL).direct_exercises_cycle_total == 2
    assert result.data_quality.items_without_canonical == 2


def test_item_sem_mapping_e_sem_sets_count_no_modo_b():
    result = evaluate(week(treino(1, item(100, None), mv.MethodologyItemInput(mapping_id=None))))
    assert result.data_quality.items_without_mapping == 1
    assert result.data_quality.items_without_sets_count == 1
    chest = block(result, PEITORAL)
    assert chest.direct_exercises_cycle_total == 1 and chest.direct_sets_cycle_total == 0


def test_coluna_semanal_e_derivada_e_nao_gera_status():
    chest = block(evaluate(week(treino(1, item(100, 4), item(101, 4), item(110, 4)))), PEITORAL)
    weekly = chest.weekly_projection
    assert weekly.source is D and "proposta D1" in weekly.evidence
    assert weekly.direct_sets_weekly == 12.0 and weekly.direct_exercises_weekly == 3.0
    assert (weekly.range_sets_weekly_min, weekly.range_sets_weekly_max) == (15.0, 20.0)
    assert not hasattr(weekly, "status")


def test_resultado_e_imutavel_e_deterministico():
    a = evaluate(BELOW_CHEST)
    b = evaluate(BELOW_CHEST)
    assert a == b
    with pytest.raises(Exception):
        a.groups[0].direct_sets_axis.status = "WITHIN_RANGE"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fase 6 - paridade com o motor antigo (faixas em codigo)
# ---------------------------------------------------------------------------


def test_paridade_status_por_sessao_identicos_ao_motor_antigo():
    """A garantia do PR: com o seed da 057, cada eixo POR SESSAO (status,
    motivo, severidade, valor, faixa, origem e evidencia) de cada grupo, em
    cada cenario de referencia, e identico ao que o motor v1 produzia.
    O arquivo congelado foi gerado pelo motor v1 com os mesmos cenarios."""
    groups = tuple(paridade.grupo(i, n, s, p) for i, n, s, p in paridade.GRUPOS)
    atual = paridade.instantaneo(
        lambda slots, modo, intent: mv.evaluate_methodology_volume(slots, paridade.PRIMARY, groups, modo, intent, SEED)
    )
    congelado = json.loads(PARIDADE_PATH.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(atual)) == congelado


# ---------------------------------------------------------------------------
# Fase 6 - eixo semanal e status combinado
# ---------------------------------------------------------------------------


def _spec(ranges, weekly=(None, None), class_id=90, name="Teste"):
    return {
        class_id: mv.VolumeClassSpec(
            id=class_id,
            name=name,
            ranges={
                bucket: mv.MethodologyRange(
                    sets_min=s[0], sets_max=s[1], sets_source=mv.RangeSource.ADMIN, sets_evidence="admin",
                    exercises_min=e[0] if e else None, exercises_max=e[1] if e else None,
                    exercises_source=mv.RangeSource.ADMIN if e else None,
                )
                for bucket, (s, e) in ranges.items()
            },
            weekly_sets_min=weekly[0],
            weekly_sets_max=weekly[1],
            weekly_source=mv.RangeSource.ADMIN if weekly[0] is not None else None,
            weekly_evidence="meta do admin" if weekly[0] is not None else None,
        )
    }


def _so_peitoral(class_id=90):
    return (mv.MethodologyGroupInput(PEITORAL, "Peitoral", volume_class_id=class_id),)


def test_eixo_semanal_dentro_e_combinado_within():
    # 2x por semana, 3 exercicios / 12 series por sessao: 2x grandes = 3-4 / 9-16;
    # semanal 24 em 18-32.
    two_x = week(treino(1, item(100, 4), item(101, 4), item(110, 4)), treino(2, item(100, 4), item(101, 4), item(110, 4)))
    chest = block(evaluate(two_x), PEITORAL)
    assert chest.volume_class_id == GRANDES and chest.volume_class_name == "Grandes"
    assert chest.weekly_sets_axis.status == "WITHIN_RANGE"
    assert chest.weekly_sets_axis.value_weekly == 24.0 and chest.weekly_sets_axis.value_per_session is None
    assert (chest.weekly_sets_axis.range_min, chest.weekly_sets_axis.range_max) == (18, 32)
    assert chest.weekly_sets_axis.range_source is D
    assert chest.status == "WITHIN_RANGE" and chest.severity is None and chest.status_conflict is False


def test_eixo_semanal_abaixo_com_sessao_dentro_da_faixa():
    # O caso que o eixo novo acrescenta: 1x com 15 series (dentro de 15-20 por
    # sessao) soma 15 na semana, abaixo da meta semanal 18-32 de Grandes.
    one_x = week(treino(1, item(100, 3), item(107, 3), item(101, 3), item(110, 3), item(111, 3)))
    chest = block(evaluate(one_x), PEITORAL)
    assert chest.direct_sets_axis.status == "WITHIN_RANGE"
    assert chest.direct_exercises_axis.status == "WITHIN_RANGE"
    assert chest.weekly_sets_axis.status == "BELOW_RANGE"
    assert chest.weekly_sets_axis.severity == "warning"
    assert chest.status == "BELOW_RANGE" and chest.severity == "warning" and chest.status_conflict is False


def test_eixo_semanal_soma_a_janela_de_7_dias():
    # ciclo de 3 dias, 12 series no slot 1: a janela passa 3 vezes por ele,
    # 36 por semana (acima de 18-32). Na v2 era 12 x 7/3 = 28.
    chest = block(evaluate([treino(1, *PEITO_12), descanso(2), descanso(3)]), PEITORAL)
    assert chest.weekly_sets_axis.value_weekly == 36.0
    assert chest.weekly_sets_axis.status == "ABOVE_RANGE"


def test_meta_semanal_fracionaria_e_comparacao_seca():
    classes = _spec({"1x": ((10, 20), None)}, weekly=(12.5, 30))
    chest = block(evaluate(week(treino(1, item(100, 12))), groups=_so_peitoral(), classes=classes), PEITORAL)
    assert chest.weekly_sets_axis.status == "BELOW_RANGE"  # 12 < 12,5, sem tolerancia
    assert chest.weekly_sets_axis.range_min == 12.5


def test_conflito_below_num_eixo_e_above_em_outro_vira_below_com_sinal():
    # 14 series numa sessao: ACIMA de 10-12 por sessao, ABAIXO da meta semanal 30-40.
    classes = _spec({"1x": ((10, 12), None)}, weekly=(30, 40))
    chest = block(evaluate(week(treino(1, item(100, 14))), groups=_so_peitoral(), classes=classes), PEITORAL)
    assert chest.direct_sets_axis.status == "ABOVE_RANGE"
    assert chest.weekly_sets_axis.status == "BELOW_RANGE"
    assert chest.status == "BELOW_RANGE"
    assert chest.status_conflict is True
    assert chest.severity == "warning"


def test_combinado_acima_sem_conflito():
    chest = block(evaluate(ABOVE_CHEST), PEITORAL)  # 6 ex / 24 series em 1x; semanal 24 em 18-32
    assert chest.weekly_sets_axis.status == "WITHIN_RANGE"
    assert chest.status == "ABOVE_RANGE" and chest.status_conflict is False


def test_intencao_rebaixa_a_severidade_do_eixo_semanal_e_do_combinado():
    chest = block(evaluate(BELOW_CHEST, mv.MethodologyIntent("cutting")), PEITORAL)
    assert chest.weekly_sets_axis.status == "BELOW_RANGE"  # status honesto
    assert chest.weekly_sets_axis.severity == "informational"
    assert chest.status == "BELOW_RANGE" and chest.severity == "informational"


def test_sem_linha_para_o_bucket_e_no_range_for_frequency_e_semanal_continua():
    classes = _spec({"1x": ((15, 20), (4, 5)), "2x": ((9, 16), (3, 4))}, weekly=(18, 32))  # sem 3x_plus
    three_x = week(*(treino(i, item(100, 4), item(110, 4)) for i in (1, 2, 3)))
    chest = block(evaluate(three_x, groups=_so_peitoral(), classes=classes), PEITORAL)
    assert chest.frequency_bucket == "3x_plus"
    assert chest.direct_sets_axis.status == "NOT_EVALUABLE"
    assert chest.direct_sets_axis.reason == "NO_RANGE_FOR_FREQUENCY"
    assert chest.direct_exercises_axis.reason == "NO_RANGE_FOR_FREQUENCY"
    assert chest.direct_sets_axis.value_per_session == 8.0
    assert chest.weekly_sets_axis.status == "WITHIN_RANGE"  # 24 em 18-32
    assert chest.status == "WITHIN_RANGE"


def test_sem_linha_e_sem_meta_semanal_combinado_not_evaluable_com_motivo():
    classes = _spec({"1x": ((15, 20), None)})
    two_x = week(treino(1, item(100, 4)), treino(2, item(100, 4)))
    chest = block(evaluate(two_x, groups=_so_peitoral(), classes=classes), PEITORAL)
    assert chest.weekly_sets_axis.reason == "NO_WEEKLY_TARGET"
    assert chest.status == "NOT_EVALUABLE"
    assert chest.status_reason == "NO_RANGE_FOR_FREQUENCY"


def test_sem_faixa_de_exercicios_so_o_eixo_de_exercicios_fica_de_fora():
    classes = _spec({"1x": ((10, 20), None)}, weekly=(10, 20))
    chest = block(evaluate(week(treino(1, item(100, 4), item(110, 4), item(112, 4))), groups=_so_peitoral(), classes=classes), PEITORAL)
    assert chest.direct_exercises_axis.status == "NOT_EVALUABLE"
    assert chest.direct_exercises_axis.reason == "NO_EXERCISES_RANGE"
    assert chest.direct_exercises_axis.value_per_session == 3.0
    assert chest.direct_sets_axis.status == "WITHIN_RANGE"
    assert chest.status == "WITHIN_RANGE"


def test_sem_meta_semanal_so_o_eixo_semanal_fica_de_fora():
    classes = _spec({"1x": ((10, 12), None)})
    chest = block(evaluate(week(treino(1, item(100, 14))), groups=_so_peitoral(), classes=classes), PEITORAL)
    assert chest.weekly_sets_axis.status == "NOT_EVALUABLE"
    assert chest.weekly_sets_axis.reason == "NO_WEEKLY_TARGET"
    assert chest.weekly_sets_axis.value_weekly == 14.0
    assert chest.direct_sets_axis.status == "ABOVE_RANGE"
    assert chest.status == "ABOVE_RANGE" and chest.status_conflict is False


def test_grupo_sem_macro_grupo_nao_e_avaliado_em_eixo_nenhum():
    primary = {**PRIMARY, 120: {CORE}}
    core = block(evaluate(week(treino(1, mv.MethodologyItemInput(120, 1200, 3))), primary=primary), CORE)
    assert core.volume_class_id is None and core.volume_class_name is None
    assert core.size_class == "unclassified"  # deprecated, so ecoado
    for axis in (core.direct_exercises_axis, core.direct_sets_axis, core.weekly_sets_axis):
        assert axis.status == "NOT_EVALUABLE" and axis.reason == "UNCLASSIFIED_GROUP"
    assert core.weekly_sets_axis.value_weekly == 3.0
    assert core.status == "NOT_EVALUABLE" and core.status_reason == "UNCLASSIFIED_GROUP"


def test_macro_grupo_inexistente_no_cadastro_e_tratado_como_sem_macro_grupo():
    groups = (mv.MethodologyGroupInput(PEITORAL, "Peitoral", volume_class_id=12345),)
    chest = block(evaluate(week(treino(1, item(100, 4))), groups=groups), PEITORAL)
    assert chest.direct_sets_axis.reason == "UNCLASSIFIED_GROUP"
    assert chest.volume_class_id is None


def test_fatia_usa_o_macro_grupo_do_pai():
    # A fatia aponta para Pequenos (cadastro incoerente); o bloco e o do pai
    # (Grandes) e e a faixa de Grandes que vale.
    groups = (
        mv.MethodologyGroupInput(PEITORAL, "Peitoral", volume_class_id=GRANDES),
        mv.MethodologyGroupInput(PEITORAL_SUP, "Peitoral superior", volume_class_id=PEQUENOS, methodology_parent_group_id=PEITORAL),
    )
    chest = block(evaluate(week(treino(1, item(101, 4), item(111, 4))), groups=groups), PEITORAL)
    assert chest.volume_class_name == "Grandes"
    assert chest.direct_sets_axis.range_evidence == "EXG-015"
    # pai sem macro-grupo: o bloco inteiro fica sem avaliacao, mesmo com a fatia associada
    orfao = (dataclasses.replace(groups[0], volume_class_id=None), groups[1])
    chest = block(evaluate(week(treino(1, item(101, 4))), groups=orfao), PEITORAL)
    assert chest.direct_sets_axis.reason == "UNCLASSIFIED_GROUP"


def test_nao_treinado_e_neutro_nos_tres_eixos_e_no_combinado():
    biceps = block(evaluate(week(treino(1, item(109, 4)))), BICEPS)
    assert biceps.weekly_sets_axis.status == "NOT_DIRECTLY_TRAINED"
    assert biceps.status == "NOT_DIRECTLY_TRAINED" and biceps.severity is None and biceps.status_reason is None


def test_modo_a_avalia_series_e_semanal_e_nao_exercicios():
    slots = week(treino(1, mv.MethodologyItemInput(muscle_group_id=PEITORAL, sets_count=16)))
    chest = block(evaluate(slots, volume_mode="percent_budget"), PEITORAL)
    assert chest.direct_exercises_axis.reason == "MODE_A_NO_EXERCISES"
    assert chest.direct_sets_axis.status == "WITHIN_RANGE"
    assert chest.weekly_sets_axis.status == "BELOW_RANGE"  # 16 < 18
    assert chest.status == "BELOW_RANGE"
    assert block(evaluate(slots, volume_mode="percent_budget"), BICEPS).status == "NOT_DIRECTLY_TRAINED"


def test_payload_mantem_campos_deprecated_por_uma_versao():
    chest = block(evaluate(BELOW_CHEST), PEITORAL)
    assert chest.size_class == "large"
    assert chest.weekly_projection is not None
    campos = {f.name for f in dataclasses.fields(chest)}
    assert {"volume_class_id", "volume_class_name", "weekly_sets_axis", "status", "severity",
            "status_conflict", "status_reason", "size_class", "weekly_projection"} <= campos

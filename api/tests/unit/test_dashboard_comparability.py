"""Os oito casos do §16.1 do plano do Novo Dashboard, sobre
`app/intelligence/dashboard/comparability.py`.

Cada teste abaixo e um deles, nomeado pela PERGUNTA que responde:

1. mesma carga + mais reps = progresso;
2. mais carga + menos reps = avaliar e1RM, nunca concluir automatico;
3. mesmo desempenho com RPE menor = melhoria;
4. maquina diferente = NOT_COMPARABLE (nunca "regrediu");
5. exercicio renomeado por alias mantem o historico canonico;
6. assistido usa a direcao correta (menos kg = progresso);
7. halteres / por lado separam a chave, sem converter valor;
8. RPE ausente nao vira 0 nem e1RM inventado.

Funcoes puras: nenhum banco, nenhum relogio.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.domain import e1rm as dono_e1rm
from app.intelligence.dashboard import comparability as cmp
from app.intelligence.dashboard.metrics_registry import THRESHOLDS, ExerciseProgressionStatus
from app.load_conventions import LoadConvention

EXERCICIO = 1001
OUTRO_EXERCICIO = 1002
MAQUINA_A = 11
MAQUINA_B = 22


def exposicao(
    *,
    exercise_id: int = EXERCICIO,
    equipment_id: int | None = MAQUINA_A,
    convention: str = "total",
    reps: int | None = 30,
    load: float | None = 40.0,
    rpe: float | None = 8.0,
    e1rm: float | None = None,
    e1rm_reps: int | None = 10,
    reps_min: int | None = None,
    reps_max: int | None = None,
    agnostic: bool = False,
    series: list[int] | None = None,
    sets: list[tuple] | None = None,
) -> cmp.ExposureSummary:
    """`series` = reps de cada serie, todas na carga `load` (e `reps` vira a
    soma), SEM RPE por serie. `sets` = (reps, carga) ou (reps, carga, rpe)
    explicitos. Sem nenhum dos dois, a exposicao vale como UMA serie de
    `reps` (DTO so com agregados)."""
    detalhe: tuple[cmp.WorkingSet, ...] = ()
    if series is not None:
        detalhe = tuple(cmp.WorkingSet(reps=r, weight_kg=load) for r in series)
    elif sets is not None:
        detalhe = tuple(cmp.WorkingSet(*item) for item in sets)
    if detalhe:
        reps = sum(item.reps for item in detalhe if item.reps is not None)
    return cmp.ExposureSummary(
        key=cmp.build_key(exercise_id, equipment_id, convention, equipment_agnostic=agnostic),
        sets=detalhe,
        total_reps=reps,
        best_load_kg=load,
        avg_rpe=rpe,
        e1rm=e1rm,
        e1rm_reps=e1rm_reps,
        prescribed_reps_min=reps_min,
        prescribed_reps_max=reps_max,
    )


# ---------------------------------------------------------------------------
# Vocabulario e chave
# ---------------------------------------------------------------------------


def test_convencao_ausente_e_total_e_valor_invalido_e_erro():
    assert cmp.normalize_load_convention(None) is LoadConvention.TOTAL
    assert cmp.normalize_load_convention("") is LoadConvention.TOTAL
    assert cmp.normalize_load_convention("  ASSISTED ") is LoadConvention.ASSISTED
    with pytest.raises(ValueError):
        cmp.normalize_load_convention("por_lado")


def test_chave_sem_equipamento_nao_e_comparavel_salvo_quando_todas_estao_sem():
    sem_equipamento = cmp.build_key(EXERCICIO, None, "total")
    assert not cmp.keys_match(sem_equipamento, sem_equipamento)

    # excecao do §8.1: TODAS as exposicoes sem equipamento -> chave sem equipamento
    assert cmp.all_equipment_missing([None, None]) is True
    assert cmp.all_equipment_missing([None, MAQUINA_A]) is False
    assert cmp.all_equipment_missing([]) is False

    agnostica = cmp.build_key(EXERCICIO, None, "total", equipment_agnostic=True)
    assert cmp.keys_match(agnostica, agnostica)


def test_chave_agnostica_descarta_o_equipamento_informado():
    chave = cmp.build_key(EXERCICIO, MAQUINA_A, "total", equipment_agnostic=True)
    assert chave.equipment_id is None
    assert chave.to_dict()["equipment_agnostic"] is True


# ---------------------------------------------------------------------------
# §16.1 - os oito casos
# ---------------------------------------------------------------------------


def test_1_mesma_carga_e_mais_reps_e_progresso():
    anterior = exposicao(reps=24, load=40.0)
    atual = exposicao(reps=27, load=40.0)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_LOAD_MORE_REPS in reasons


def test_1b_mesma_carga_respeita_a_tolerancia_de_2_por_cento():
    """40,0 -> 40,5 kg (1,25%) ainda e "mesma carga" e a regra de reps vale.
    Com 44 kg (10%) a carga MUDOU: a regra "mesma carga + mais reps" nao se
    aplica mais e o veredito passa a depender da faixa prescrita/e1RM."""
    status, reasons = cmp.classify_exercise_progression(
        [exposicao(reps=24, load=40.0), exposicao(reps=27, load=40.5)]
    )
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_LOAD_MORE_REPS in reasons

    _, reasons = cmp.classify_exercise_progression(
        [exposicao(reps=24, load=40.0, e1rm=None), exposicao(reps=27, load=44.0, e1rm=None)]
    )
    assert cmp.REASON_SAME_LOAD_MORE_REPS not in reasons


def test_2_mais_carga_com_menos_reps_nao_conclui_sozinho():
    """Sem e1RM comparavel, mais carga com menos reps NAO vira progresso nem
    regressao - fica STABLE com a razao explicita."""
    anterior = exposicao(reps=30, load=40.0, e1rm=None)
    atual = exposicao(reps=24, load=50.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_MORE_LOAD_FEWER_REPS in reasons

    # com e1RM disponivel, e o e1RM que decide (aqui: +6% -> progresso)
    status, reasons = cmp.classify_exercise_progression(
        [exposicao(reps=30, load=40.0, e1rm=50.0), exposicao(reps=24, load=50.0, e1rm=53.0)]
    )
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_E1RM_UP in reasons


def test_2b_e1rm_so_conta_com_reps_ate_12():
    """Epley acima de 12 reps deixa de ser estimativa confiavel (§8.2)."""
    status, _ = cmp.classify_exercise_progression(
        [exposicao(reps=30, load=40.0, e1rm=50.0), exposicao(reps=24, load=50.0, e1rm=53.0, e1rm_reps=20)]
    )
    assert status is not ExerciseProgressionStatus.PROGRESSING


def test_3_mesmo_desempenho_com_rpe_menor_e_melhoria():
    anterior = exposicao(reps=30, load=40.0, rpe=9.0)
    atual = exposicao(reps=30, load=40.0, rpe=8.0)  # -1,0 <= -0,75
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_PERFORMANCE_LOWER_RPE in reasons

    # queda de RPE menor que o limiar nao e progresso
    status, _ = cmp.classify_exercise_progression(
        [exposicao(reps=30, load=40.0, rpe=9.0), exposicao(reps=30, load=40.0, rpe=8.5)]
    )
    assert status is ExerciseProgressionStatus.STABLE


def test_4_maquina_diferente_e_not_comparable_e_nao_regressao():
    anterior = exposicao(equipment_id=MAQUINA_A, reps=30, load=60.0)
    atual = exposicao(equipment_id=MAQUINA_B, reps=20, load=30.0)  # "pior" em numeros
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.NOT_COMPARABLE
    assert cmp.REASON_KEY_CHANGED in reasons


def test_4b_equipamento_nao_resolvido_tambem_e_not_comparable():
    anterior = exposicao(equipment_id=MAQUINA_A)
    atual = exposicao(equipment_id=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.NOT_COMPARABLE
    assert cmp.REASON_EQUIPMENT_UNKNOWN in reasons


def test_5_exercicio_renomeado_por_alias_mantem_o_historico_canonico():
    """O alias/nome nao entra na chave: o que identifica o exercicio e o id
    CANONICO (resolvido por `exercise_legacy_id_map` na query). Duas
    exposicoes do mesmo id canonico continuam comparaveis, e ids diferentes
    nao se misturam."""
    mesma = [exposicao(exercise_id=EXERCICIO, reps=24), exposicao(exercise_id=EXERCICIO, reps=27)]
    assert cmp.classify_exercise_progression(mesma)[0] is ExerciseProgressionStatus.PROGRESSING

    outra = [exposicao(exercise_id=EXERCICIO), exposicao(exercise_id=OUTRO_EXERCICIO)]
    assert cmp.classify_exercise_progression(outra)[0] is ExerciseProgressionStatus.NOT_COMPARABLE


def test_6_assistido_usa_a_direcao_correta():
    """No graviton, MENOS quilos de assistencia e progresso."""
    assert cmp.inverts_load_direction(LoadConvention.ASSISTED) is True

    # 30 kg -> 20 kg de assistencia, reps dentro da faixa prescrita: a carga
    # "subiu" na direcao do progresso.
    menos_assistencia = [
        exposicao(convention="assisted", load=30.0, reps=24, e1rm=None, reps_min=20, reps_max=30),
        exposicao(convention="assisted", load=20.0, reps=24, e1rm=None, reps_min=20, reps_max=30),
    ]
    status, reasons = cmp.classify_exercise_progression(menos_assistencia)
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_LOAD_UP_REPS_IN_RANGE in reasons

    # EXATAMENTE os mesmos numeros lidos como carga TOTAL nunca sao progresso:
    # la, 30 -> 20 kg e carga que caiu.
    como_total = [
        exposicao(convention="total", load=30.0, reps=24, e1rm=None, reps_min=20, reps_max=30),
        exposicao(convention="total", load=20.0, reps=24, e1rm=None, reps_min=20, reps_max=30),
    ]
    assert cmp.classify_exercise_progression(como_total)[0] is not ExerciseProgressionStatus.PROGRESSING

    # e a direcao tambem vale para o lado ruim: MAIS assistencia nao e progresso
    mais_assistencia = [
        exposicao(convention="assisted", load=20.0, reps=24, e1rm=None, reps_min=20, reps_max=30),
        exposicao(convention="assisted", load=30.0, reps=24, e1rm=None, reps_min=20, reps_max=30),
    ]
    assert cmp.classify_exercise_progression(mais_assistencia)[0] is not ExerciseProgressionStatus.PROGRESSING


def test_6b_assistido_sem_e1rm_porque_o_peso_alivia_o_movimento():
    assert cmp.supports_e1rm(LoadConvention.ASSISTED) is False
    assert cmp.supports_e1rm(LoadConvention.BODYWEIGHT) is False
    assert cmp.supports_e1rm(LoadConvention.PER_SIDE) is True


def test_7_halteres_e_por_lado_separam_a_chave_sem_converter_valor():
    por_halter = cmp.build_key(EXERCICIO, MAQUINA_A, "per_dumbbell")
    total = cmp.build_key(EXERCICIO, MAQUINA_A, "total")
    por_lado = cmp.build_key(EXERCICIO, MAQUINA_A, "per_side")
    assert not cmp.keys_match(por_halter, total)
    assert not cmp.keys_match(por_halter, por_lado)

    # 20 kg por halter continua 20 - nenhuma multiplicacao acontece
    exposicoes = [
        exposicao(convention="per_dumbbell", load=20.0, reps=24),
        exposicao(convention="per_dumbbell", load=20.0, reps=27),
    ]
    assert exposicoes[0].directional_load == 20.0
    status, reasons = cmp.classify_exercise_progression(exposicoes)
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_LOAD_MORE_REPS in reasons

    # trocar a convencao no meio do historico interrompe a comparacao
    trocou = [exposicao(convention="total", load=20.0), exposicao(convention="per_dumbbell", load=20.0)]
    assert cmp.classify_exercise_progression(trocou)[0] is ExerciseProgressionStatus.NOT_COMPARABLE


def test_8_rpe_ausente_nao_vira_zero_nem_e1rm_inventado():
    sem_rpe = exposicao(rpe=None, e1rm=None)
    assert sem_rpe.avg_rpe is None
    assert sem_rpe.e1rm is None

    status, reasons = cmp.classify_exercise_progression([exposicao(rpe=None, e1rm=None, reps=30), sem_rpe])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_NO_E1RM_WITHOUT_RPE in reasons
    # sem RPE, a regra de "mesmo desempenho com RPE menor" nunca dispara
    assert cmp.REASON_SAME_PERFORMANCE_LOWER_RPE not in reasons


# ---------------------------------------------------------------------------
# Demais estados do §8.2
# ---------------------------------------------------------------------------


def test_menos_de_duas_exposicoes_e_insufficient_data():
    assert cmp.classify_exercise_progression([])[0] is ExerciseProgressionStatus.INSUFFICIENT_DATA
    status, reasons = cmp.classify_exercise_progression([exposicao()])
    assert status is ExerciseProgressionStatus.INSUFFICIENT_DATA
    assert cmp.REASON_NOT_ENOUGH_EXPOSURES in reasons


def test_regressao_por_e1rm_e_por_reps_com_mesma_carga():
    queda_e1rm = [exposicao(e1rm=100.0), exposicao(e1rm=95.0)]  # -5%, 10 reps nos dois lados
    status, reasons = cmp.classify_exercise_progression(queda_e1rm)
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_E1RM_DOWN in reasons

    # mesma carga, MESMO numero de series, menos reps em cada serie
    menos_reps = [
        exposicao(series=[10, 10, 10], load=40.0, e1rm=None),
        exposicao(series=[8, 8, 8], load=40.0, e1rm=None),
    ]
    status, reasons = cmp.classify_exercise_progression(menos_reps)
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_SAME_LOAD_FEWER_REPS in reasons
    assert cmp.REASON_FEWER_SETS not in reasons


# ---------------------------------------------------------------------------
# progression-status-v2: reps por serie emparelhada + e1RM simetrico
# ---------------------------------------------------------------------------


def test_caso_real_agachamento_menos_series_mais_reps_por_serie_e_progresso():
    """Cenario sintetico baseado em caso real, Agachamento livre a 50 kg:
    11/10/10 @9 (e1RM 70,0) contra 14 @9,5 e 13 @9 (e1RM 74,17). A soma caiu (31 -> 27) e a versao antiga
    dizia REGRESSING; emparelhando as 2 melhores series (11+10 contra 14+13)
    e progresso. O e1RM (+5,9%) nao decide sozinho porque a serie atual tem
    14 reps (> 12)."""
    anterior = exposicao(series=[11, 10, 10], load=50.0, rpe=9.0, e1rm=70.0, e1rm_reps=11)
    atual = exposicao(series=[14, 13], load=50.0, rpe=9.25, e1rm=74.17, e1rm_reps=14)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_LOAD_MORE_REPS in reasons
    assert cmp.REASON_E1RM_UP not in reasons
    # menos series aparece como aviso, sem decidir o status
    assert reasons[-1] == cmp.REASON_FEWER_SETS


def test_menos_series_com_as_mesmas_reps_por_serie_nao_e_regressao():
    anterior = exposicao(series=[10, 10, 10], load=40.0, rpe=8.0, e1rm=None)
    atual = exposicao(series=[10, 10], load=40.0, rpe=8.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_SAME_LOAD_FEWER_REPS not in reasons
    assert cmp.REASON_FEWER_SETS in reasons


def test_e1rm_menos_5_por_cento_com_ate_12_reps_e_regressao():
    anterior = exposicao(series=[10, 10], load=40.0, e1rm=60.0, e1rm_reps=10)
    atual = exposicao(series=[10, 10], load=40.0, e1rm=57.0, e1rm_reps=12)  # -5%
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_E1RM_DOWN in reasons


def test_e1rm_e_criterio_principal_mesmo_com_reps_emparelhadas_maiores():
    """Com reps confiaveis nos dois lados, o e1RM decide antes da regra de
    reps: mais reps a um RPE muito mais alto pode ser e1RM em queda."""
    anterior = exposicao(series=[10], load=40.0, e1rm=60.0, e1rm_reps=10)
    atual = exposicao(series=[11], load=40.0, e1rm=56.0, e1rm_reps=11)  # -6,7%
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.REGRESSING
    assert reasons == [cmp.REASON_E1RM_DOWN]


def test_e1rm_mais_5_por_cento_com_14_reps_nao_decide_progresso():
    """+5% de e1RM vindo de serie de 14 reps nao e confiavel: cai para as reps
    por serie, que aqui sao iguais (ou menores) -> nao e PROGRESSING."""
    iguais = [
        exposicao(series=[12, 12], load=40.0, rpe=9.0, e1rm=60.0, e1rm_reps=12),
        exposicao(series=[14, 10], load=40.0, rpe=9.0, e1rm=63.0, e1rm_reps=14),
    ]
    status, reasons = cmp.classify_exercise_progression(iguais)
    assert status is not ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_E1RM_UP not in reasons

    menores = [
        exposicao(series=[12, 12], load=40.0, rpe=9.0, e1rm=60.0, e1rm_reps=12),
        exposicao(series=[14, 8], load=40.0, rpe=9.0, e1rm=63.0, e1rm_reps=14),
    ]
    status, reasons = cmp.classify_exercise_progression(menores)
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_SAME_LOAD_FEWER_REPS in reasons


def test_queda_de_e1rm_acima_de_12_reps_nao_decide_sozinha():
    """A trava de 12 reps vale para a regressao tambem (antes so valia para o
    progresso): -6% de e1RM numa serie de 15 reps nao e regressao se as reps
    por serie na mesma carga sao as mesmas."""
    anterior = exposicao(series=[15, 12], load=40.0, rpe=9.0, e1rm=64.0, e1rm_reps=15)
    atual = exposicao(series=[15, 12], load=40.0, rpe=9.0, e1rm=60.0, e1rm_reps=12)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_E1RM_DOWN not in reasons


def test_series_fora_da_carga_de_topo_nao_entram_no_emparelhamento():
    """So as series na carga de topo (tolerancia de 2%) sao emparelhadas:
    um back-off mais leve nao conta como serie com menos reps."""
    anterior = exposicao(sets=[(10, 40.0), (10, 40.0)], load=40.0, e1rm=None)
    atual = exposicao(sets=[(10, 40.0), (10, 40.0), (6, 30.0)], load=40.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_SAME_LOAD_FEWER_REPS not in reasons


def test_faixa_prescrita_e_avaliada_por_serie_e_nao_pela_soma():
    """Ficha 8-12 e POR SERIE: 3 x 10 (soma 30) esta dentro da faixa."""
    anterior = exposicao(series=[12, 12, 12], load=40.0, e1rm=None, reps_min=8, reps_max=12)
    atual = exposicao(series=[10, 10, 10], load=45.0, e1rm=None, reps_min=8, reps_max=12)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_LOAD_UP_REPS_IN_RANGE in reasons

    # uma serie de topo abaixo da faixa ja tira a condicao
    fora = exposicao(series=[10, 9, 7], load=45.0, e1rm=None, reps_min=8, reps_max=12)
    status, reasons = cmp.classify_exercise_progression([anterior, fora])
    assert cmp.REASON_LOAD_UP_REPS_IN_RANGE not in reasons
    assert status is not ExerciseProgressionStatus.PROGRESSING


def test_o_teto_de_reps_do_e1rm_vem_do_registry():
    assert THRESHOLDS["e1rm_max_reliable_reps"] == 12
    frouxo = dict(THRESHOLDS)
    frouxo["e1rm_max_reliable_reps"] = 15
    anterior = exposicao(series=[11, 10, 10], load=50.0, rpe=9.0, e1rm=70.0, e1rm_reps=11)
    atual = exposicao(series=[14, 13], load=50.0, rpe=9.25, e1rm=74.17, e1rm_reps=14)
    _, reasons = cmp.classify_exercise_progression([anterior, atual], thresholds=frouxo)
    assert cmp.REASON_E1RM_UP in reasons


def test_tres_exposicoes_sem_movimento_viram_suspeita_de_plato():
    iguais = [exposicao(e1rm=100.0) for _ in range(3)]
    status, reasons = cmp.classify_exercise_progression(iguais)
    assert status is ExerciseProgressionStatus.PLATEAU_SUSPECTED
    assert cmp.REASON_PLATEAU_RUN in reasons

    # duas exposicoes iguais ainda sao so STABLE (o limiar e 3)
    assert cmp.classify_exercise_progression(iguais[:2])[0] is ExerciseProgressionStatus.STABLE


def test_progresso_no_meio_do_historico_impede_plato():
    historico = [
        exposicao(reps=24, load=40.0, e1rm=100.0),
        exposicao(reps=27, load=40.0, e1rm=100.0),  # progrediu aqui
        exposicao(reps=27, load=40.0, e1rm=100.0),
    ]
    assert cmp.classify_exercise_progression(historico)[0] is ExerciseProgressionStatus.STABLE


def test_o_run_comparavel_para_na_troca_de_chave():
    historico = [
        exposicao(equipment_id=MAQUINA_B),
        exposicao(equipment_id=MAQUINA_A),
        exposicao(equipment_id=MAQUINA_A),
    ]
    run = cmp.trailing_comparable_run(historico)
    assert len(run) == 2
    assert all(item.key.equipment_id == MAQUINA_A for item in run)


def test_os_limiares_vem_do_registry_e_nao_do_modulo():
    """Nenhuma constante numerica de regra mora em `comparability.py`."""
    fonte = (cmp.__file__ or "").replace("\\", "/")
    assert fonte.endswith("comparability.py")
    for chave in ("same_load_tolerance_pct", "e1rm_progress_pct", "rpe_improvement_delta"):
        assert chave in THRESHOLDS

    # limiar customizado muda o veredito, provando que nada esta hardcoded
    frouxo = dict(THRESHOLDS)
    frouxo["rpe_improvement_delta"] = 0.25
    status, _ = cmp.classify_exercise_progression(
        [exposicao(reps=30, load=40.0, rpe=9.0), exposicao(reps=30, load=40.0, rpe=8.5)],
        thresholds=frouxo,
    )
    assert status is ExerciseProgressionStatus.PROGRESSING


# ---------------------------------------------------------------------------
# M78: reps emparelhadas ajustadas pelo esforco (reps + RIR)
# ---------------------------------------------------------------------------

LEG_PRESS = 120.0  # 120 kg x (1 + 12/30) = 168 kg de e1RM


def _leg_press_20_09() -> cmp.ExposureSummary:
    """Leg press 45 (cenario sintetico baseado em caso real), 20/09: 12 @RPE10 e
    11 @RPE10 - e1RM 168."""
    return exposicao(
        sets=[(12, LEG_PRESS, 10.0), (11, LEG_PRESS, 10.0)], load=LEG_PRESS, rpe=10.0, e1rm=168.0, e1rm_reps=12
    )


def _leg_press_10_10() -> cmp.ExposureSummary:
    """10/10: 11 @RPE9 e 11 @RPE10 - e1RM 168 (11 + RIR 1 = 12)."""
    return exposicao(
        sets=[(11, LEG_PRESS, 9.0), (11, LEG_PRESS, 10.0)], load=LEG_PRESS, rpe=9.5, e1rm=168.0, e1rm_reps=11
    )


def test_m78_caso_leg_press_reps_mais_rir_empata_e_fica_estavel():
    """e1RM empatado (168 x 168, dentro de +-3%): decide a regra de reps.
    Cruas eram 22 x 23 (`same_load_fewer_reps`, "Regredindo"); com reps + RIR
    sao 23 x 23 -> estavel."""
    anterior, atual = _leg_press_20_09(), _leg_press_10_10()
    pareado = cmp.paired_top_reps(anterior, atual, THRESHOLDS["same_load_tolerance_pct"])
    assert pareado.effort_adjusted is True
    assert pareado.previous == (12, 11) and pareado.current == (12, 11)
    assert pareado.previous_reps == (12, 11) and pareado.current_reps == (11, 11)
    assert (pareado.previous_sum, pareado.current_sum, pareado.delta) == (23, 23, 0)

    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_SAME_LOAD_FEWER_REPS not in reasons
    assert cmp.REASON_PAIRED_REPS_EFFORT_ADJUSTED in reasons
    # o RPE medio menor (10 -> 9,5) nao transforma o empate em progresso
    assert cmp.REASON_SAME_PERFORMANCE_LOWER_RPE not in reasons


def test_m78_rpe_faltando_de_um_lado_usa_reps_cruas_nos_dois():
    """Uma serie emparelhada sem RPE -> nenhuma ajustada (nunca misturar)."""
    anterior = _leg_press_20_09()
    atual = exposicao(sets=[(11, LEG_PRESS, 9.0), (11, LEG_PRESS, None)], load=LEG_PRESS, rpe=9.0, e1rm=None)
    pareado = cmp.paired_top_reps(anterior, atual, THRESHOLDS["same_load_tolerance_pct"])
    assert pareado.effort_adjusted is False
    assert pareado.previous == (12, 11) and pareado.current == (11, 11)

    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_SAME_LOAD_FEWER_REPS in reasons
    assert cmp.REASON_PAIRED_REPS_EFFORT_ADJUSTED not in reasons


def test_m78_serie_nao_emparelhada_sem_rpe_nao_desliga_o_ajuste():
    """So as 2N series emparelhadas precisam de RPE: a 3a serie (sem par,
    sem RPE) nao conta."""
    anterior = exposicao(
        sets=[(12, LEG_PRESS, 10.0), (11, LEG_PRESS, 10.0), (9, LEG_PRESS, None)], load=LEG_PRESS, rpe=10.0
    )
    pareado = cmp.paired_top_reps(anterior, _leg_press_10_10(), THRESHOLDS["same_load_tolerance_pct"])
    assert pareado.effort_adjusted is True
    assert pareado.pairs == 2


def test_m78_sem_rpe_nenhum_continua_reps_cruas():
    anterior = exposicao(sets=[(12, 100.0), (11, 100.0)], load=100.0, rpe=None, e1rm=None)
    atual = exposicao(sets=[(11, 100.0), (11, 100.0)], load=100.0, rpe=None, e1rm=None)
    pareado = cmp.paired_top_reps(anterior, atual, THRESHOLDS["same_load_tolerance_pct"])
    assert pareado.effort_adjusted is False
    assert pareado.delta == -1
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_SAME_LOAD_FEWER_REPS in reasons


def test_m78_mesmas_reps_com_rpe_menor_e_progresso_por_reps_mais_rir():
    """10 @9 -> 10 @8 (todas com RPE): 11 -> 12 por serie = mais reps + RIR."""
    anterior = exposicao(sets=[(10, 60.0, 9.0), (10, 60.0, 9.0)], load=60.0, rpe=9.0, e1rm=None)
    atual = exposicao(sets=[(10, 60.0, 8.0), (10, 60.0, 8.0)], load=60.0, rpe=8.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_LOAD_MORE_REPS in reasons
    assert cmp.REASON_PAIRED_REPS_EFFORT_ADJUSTED in reasons


def test_m78_meia_rep_de_rir_e_valor_com_casa_decimal():
    anterior = exposicao(sets=[(10, 60.0, 8.5)], load=60.0, rpe=8.5, e1rm=None)
    atual = exposicao(sets=[(10, 60.0, 9.0)], load=60.0, rpe=9.0, e1rm=None)
    pareado = cmp.paired_top_reps(anterior, atual, THRESHOLDS["same_load_tolerance_pct"])
    assert pareado.previous == (11.5,) and pareado.current == (11,)
    assert pareado.delta == -0.5


def test_m78_e1rm_continua_decidindo_primeiro():
    """e1RM -5% com ate 12 reps decide antes das reps emparelhadas."""
    anterior = exposicao(sets=[(10, 60.0, 8.0)], load=60.0, rpe=8.0, e1rm=100.0, e1rm_reps=10)
    atual = exposicao(sets=[(10, 60.0, 7.0)], load=60.0, rpe=7.0, e1rm=95.0, e1rm_reps=10)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.REGRESSING
    assert reasons[0] == cmp.REASON_E1RM_DOWN


def test_m78_menos_series_com_rpe_continua_so_aviso():
    anterior = exposicao(sets=[(10, 60.0, 8.0)] * 3, load=60.0, rpe=8.0, e1rm=None)
    atual = exposicao(sets=[(10, 60.0, 8.0)] * 2, load=60.0, rpe=8.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert reasons[-1] == cmp.REASON_FEWER_SETS


def test_m78_faixa_prescrita_e_reps_de_topo_continuam_cruas():
    """`top_load_reps` (faixa prescrita, contexto da IA) e o registrado."""
    exp = exposicao(sets=[(11, LEG_PRESS, 9.0), (12, LEG_PRESS, 10.0)], load=LEG_PRESS)
    assert cmp.top_load_reps(exp, THRESHOLDS["same_load_tolerance_pct"]) == [12, 11]


# ---------------------------------------------------------------------------
# P8 (investigacao RPE 10, 2026-10-11): mesmas reps, mesma carga, RPE maior
# = Estavel. O RIR continua valendo quando as reps cruas mudam.
# ---------------------------------------------------------------------------


def _supino_35(rpe: float, *, reps: tuple[int, ...] = (12, 12, 12)) -> cmp.ExposureSummary:
    """35 kg, todas as series com o mesmo RPE; e1RM = Epley+RIR (dono
    `domain/e1rm`) da melhor serie."""
    return exposicao(
        sets=[(r, 35.0, rpe) for r in reps],
        load=35.0,
        rpe=rpe,
        e1rm=dono_e1rm.estimated_1rm(35.0, max(reps), rpe),
        e1rm_reps=max(reps),
    )


def test_p8_mesmas_reps_com_rpe_maior_e_estavel_e_nao_e1rm_down():
    """Cenario A/S3 do harness: 12x35@8 -> 12x35@10. O e1RM com RIR cai
    51,3 -> 49,0 (-4,5%, decisivo com 12 reps) e antes virava `e1rm_down`
    (REGRESSING). Agora: STABLE com `same_reps_higher_rpe`."""
    anterior, atual = _supino_35(8.0), _supino_35(10.0)
    assert round(anterior.e1rm, 1) == 51.3 and round(atual.e1rm, 1) == 49.0

    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert reasons == [cmp.REASON_SAME_REPS_HIGHER_RPE]


def test_p8_reps_mais_rir_menores_com_reps_cruas_iguais_tambem_e_estavel():
    """Acima de 12 reps (e1RM nao decide) as reps + RIR caem (15@7 = 18 ->
    15@10 = 15): antes era `same_load_fewer_reps` (REGRESSING)."""
    anterior = _supino_35(7.0, reps=(15, 15))
    atual = _supino_35(10.0, reps=(15, 15))
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert reasons == [cmp.REASON_SAME_REPS_HIGHER_RPE]


def test_p8_sem_rpe_por_serie_usa_o_rpe_medio():
    """Sem RPE em todas as series emparelhadas: decide o RPE medio das
    exposicoes (8 -> 10) - o e1RM decisivo em queda nao vira regressao."""
    anterior = exposicao(series=[12, 12], load=35.0, rpe=8.0, e1rm=51.33, e1rm_reps=12)
    atual = exposicao(series=[12, 12], load=35.0, rpe=10.0, e1rm=49.0, e1rm_reps=12)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert reasons == [cmp.REASON_SAME_REPS_HIGHER_RPE]


def test_p8_sem_rpe_conhecido_segue_a_regra_atual():
    anterior = exposicao(series=[12, 12], load=35.0, rpe=None, e1rm=None)
    atual = exposicao(series=[12, 12], load=35.0, rpe=None, e1rm=None)
    _, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert cmp.REASON_SAME_REPS_HIGHER_RPE not in reasons


def test_p8_reps_cruas_menores_seguem_a_regra_atual():
    """12/12 -> 12/11 com RPE maior: as reps cruas mudaram, o RIR vale."""
    anterior = _supino_35(8.0, reps=(12, 12))
    atual = _supino_35(10.0, reps=(12, 11))
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.REGRESSING
    assert cmp.REASON_SAME_REPS_HIGHER_RPE not in reasons


def test_p8_reps_cruas_maiores_continuam_progresso():
    anterior = _supino_35(8.0, reps=(12, 12))
    atual = exposicao(sets=[(13, 35.0, 8.0), (12, 35.0, 8.0)], load=35.0, rpe=8.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_REPS_HIGHER_RPE not in reasons


def test_p8_rpe_menor_continua_progresso():
    """A regra so vale para o RPE subindo: 12@10 -> 12@8 segue progresso."""
    status, reasons = cmp.classify_exercise_progression([_supino_35(10.0), _supino_35(8.0)])
    assert status is ExerciseProgressionStatus.PROGRESSING
    assert cmp.REASON_SAME_REPS_HIGHER_RPE not in reasons


def test_p8_rpe_igual_nao_usa_o_motivo_novo():
    status, reasons = cmp.classify_exercise_progression([_supino_35(9.0), _supino_35(9.0)])
    assert status is ExerciseProgressionStatus.STABLE
    assert cmp.REASON_SAME_REPS_HIGHER_RPE not in reasons


def test_p8_carga_diferente_segue_a_regra_atual():
    """Carga fora da tolerancia de "mesma carga": a regra nao se aplica."""
    anterior = _supino_35(8.0)
    atual = exposicao(sets=[(12, 30.0, 10.0)] * 3, load=30.0, rpe=10.0, e1rm=42.0, e1rm_reps=12)
    _, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert cmp.REASON_SAME_REPS_HIGHER_RPE not in reasons


def test_p8_menos_series_com_mesmas_reps_e_rpe_maior_e_estavel_com_aviso():
    anterior = _supino_35(8.0, reps=(12, 12, 12))
    atual = _supino_35(10.0, reps=(12, 12))
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert reasons == [cmp.REASON_SAME_REPS_HIGHER_RPE, cmp.REASON_FEWER_SETS]


def test_p8_peso_corporal_mesmas_reps_rpe_maior_e_estavel():
    anterior = exposicao(convention="bodyweight", sets=[(12, None, 8.0)] * 2, load=None, rpe=8.0, e1rm=None)
    atual = exposicao(convention="bodyweight", sets=[(12, None, 10.0)] * 2, load=None, rpe=10.0, e1rm=None)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.STABLE
    assert reasons == [cmp.REASON_BODYWEIGHT_REPS_ONLY, cmp.REASON_SAME_REPS_HIGHER_RPE]


def test_p8_tres_exposicoes_iguais_com_rpe_subindo_viram_suspeita_de_plato():
    """O motivo novo e "sem movimento": numa sequencia so dele vale a regra
    de plato que ja existia (nunca REGRESSING)."""
    status, reasons = cmp.classify_exercise_progression([_supino_35(8.0), _supino_35(9.0), _supino_35(10.0)])
    assert status is ExerciseProgressionStatus.PLATEAU_SUSPECTED
    assert reasons == [cmp.REASON_SAME_REPS_HIGHER_RPE, cmp.REASON_PLATEAU_RUN]


def test_todo_motivo_do_dono_tem_texto_no_frontend():
    """Paridade dos codigos `REASON_*` com `STATUS_REASON_TEXTS`
    (`frontend/src/lib/exercise-progress.ts`): motivo novo no dono sem texto
    na tela viraria um codigo cru no tooltip."""
    fonte = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "exercise-progress.ts").read_text(
        encoding="utf-8"
    )
    bloco = fonte.split("export const STATUS_REASON_TEXTS", 1)[1].split("};", 1)[0]
    chaves = set(re.findall(r"^\s{2}([a-z0-9_]+):", bloco, flags=re.MULTILINE))
    motivos = {valor for nome, valor in vars(cmp).items() if nome.startswith("REASON_") and isinstance(valor, str)}
    assert motivos - chaves == set()


# ---------------------------------------------------------------------------
# Lote T3 (medidas de serie): serie por tempo / maximo de reps
# ---------------------------------------------------------------------------


def test_motivo_da_medida_e_reexportado_do_dono_e_tem_texto():
    from app.domain import set_measures as sm

    assert cmp.REASON_MEASURE_NOT_REP_BASED == sm.REASON_MEASURE_NOT_REP_BASED
    fonte = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "exercise-progress.ts").read_text(
        encoding="utf-8"
    )
    # O texto do FE e o rotulo do dono lido do JSON gerado (nao escrito a mao).
    assert "measure_not_rep_based: MEASURE_REASON_LABELS[REASON_MEASURE_NOT_REP_BASED]" in fonte


def test_exposicao_com_serie_nao_reps_nao_e_comparavel():
    from dataclasses import replace

    anterior = exposicao(series=[10, 10, 10], load=40.0)
    atual = replace(exposicao(series=[12, 12, 12], load=40.0), has_non_rep_sets=True)
    status, reasons = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.NOT_COMPARABLE
    assert reasons == [cmp.REASON_MEASURE_NOT_REP_BASED]
    # O par direto tambem nao conclui nada (sessao a sessao).
    assert cmp.compare_exposures(anterior, atual) == (None, [cmp.REASON_MEASURE_NOT_REP_BASED])
    assert cmp.compare_exposures(atual, anterior) == (None, [cmp.REASON_MEASURE_NOT_REP_BASED])


def test_sem_serie_nao_reps_a_regra_e_a_de_sempre():
    anterior = exposicao(series=[10, 10, 10], load=40.0)
    atual = exposicao(series=[12, 12, 12], load=40.0)
    status, _ = cmp.classify_exercise_progression([anterior, atual])
    assert status is ExerciseProgressionStatus.PROGRESSING


def test_exposure_service_tira_series_nao_reps_dos_agregados():
    from app.intelligence.dashboard import exposure_service as es

    linhas = [
        {"exercise_row_id": 1, "activity_id": 1, "local_date": "2026-10-01", "started_at": None,
         "position_in_session": 1, "equipment_id": MAQUINA_A, "equipment_name": "M", "load_convention": "total",
         "prescribed_reps_target": "10-12", "set_number": n, "reps": reps, "weight_kg": kg, "rpe": 9,
         "rest_seconds": None, "notes": None, "measure": medida, "duration_seconds": segundos}
        for n, reps, kg, medida, segundos in (
            (1, 10, 40, "reps", None),
            (2, 20, 60, "max_reps", 30),
            (3, None, 10, "duration", 60),
        )
    ]
    (exp,) = es.assemble_exposures(es.build_exposures(linhas), EXERCICIO)
    assert exp["has_non_rep_sets"] is True
    assert exp["total_reps"] == 10 and exp["best_load_kg"] == 40 and exp["tonnage"] == 400
    assert [s["measure_text"] for s in exp["sets"]] == ["10 reps", "20 reps em 30 s", "60 s"]
    (resumo,) = es.to_summaries([exp])
    assert resumo.has_non_rep_sets is True and [s.reps for s in resumo.sets] == [10]

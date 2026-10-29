"""Sinal "Desempenho recente" (`performance_ratio`) pela fonte unica de
progresso por exercicio (`dashboard/comparability`).

Antes: melhor e1RM da sessao / mediana do e1RM dos 28 dias anteriores, com o
exercicio identificado so pelo `mapping_id`. Agora:

- troca de equipamento/convencao nao gera queda (sem par comparavel, sem sinal);
- serie acima de 12 reps nao decide pelo e1RM;
- menos series nao e queda;
- regressao real (mesma chave, e1RM -6% com ate 12 reps) gera o MESMO sinal de
  antes (mesma severidade em `MuscleRecoveryService.classify_readiness`);
- por grupo, a media das razoes so considera exercicios comparaveis.

Tudo sem banco."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.intelligence.dashboard.comparability import ExposureSummary, WorkingSet, build_key
from app.intelligence.exercise_performance import (
    latest_performance_ratios,
    paired_reps_ratio,
    performance_ratio,
)
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.volume_service import VolumeService

AGORA = datetime(2026, 10, 6, 12, 0, 0)


# ---------------------------------------------------------------------------
# Construtores
# ---------------------------------------------------------------------------


def _exposure(sets, *, equipment_id=7, convention="total", rpe=8.0):
    """`ExposureSummary` como o `exposure_service` monta: e1RM = melhor
    Epley+RIR das series, `e1rm_reps` = reps da serie que o gerou."""
    best, best_reps = None, None
    for reps, weight in sets:
        if rpe is None:  # sem RPE nao ha e1RM (como no exposure_service)
            break
        value = MuscleRecoveryService.estimated_1rm(weight, reps, rpe)
        if best is None or value > best:
            best, best_reps = value, reps
    return ExposureSummary(
        key=build_key(1001, equipment_id, convention),
        total_reps=sum(reps for reps, _ in sets),
        best_load_kg=max(weight for _, weight in sets),
        avg_rpe=rpe,
        e1rm=best,
        e1rm_reps=best_reps,
        sets=tuple(WorkingSet(reps=reps, weight_kg=weight, rpe=rpe) for reps, weight in sets),
    )


def _row(set_id, activity_id, days_ago, reps, weight, *, rpe=8.0, name="Supino reto", equipment_id=7,
         exercise_id=None, set_number=1, convention=None, group="Peitoral"):
    trained_at = AGORA - timedelta(days=days_ago, hours=2)
    return {
        "set_id": set_id,
        "exercise_id": exercise_id if exercise_id is not None else activity_id * 10 + len(name),
        "activity_id": activity_id,
        "set_number": set_number,
        "reps": reps,
        "weight_kg": weight,
        "rpe": rpe,
        "set_type": "working",
        "local_date": trained_at.date().isoformat(),
        "trained_at": trained_at.isoformat(),
        "mapping_id": None,
        "original_name": name,
        "primary_group": group,
        "secondary_group": None,
        "primary_group_id": None,
        "secondary_group_id": None,
        "secondary_participation": None,
        "muscle_pain": None,
        "joint_pain": 0,
        "equipment_id": equipment_id,
        "load_convention": convention,
    }


def _sessao(activity_id, days_ago, sets, **kwargs):
    """Uma exposicao: varias series do mesmo exercicio na mesma sessao."""
    return [
        _row(activity_id * 100 + number, activity_id, days_ago, reps, weight, set_number=number, **kwargs)
        for number, (reps, weight) in enumerate(sets, start=1)
    ]


def _service(rows):
    service = VolumeService()
    service._fetch_window_sets = lambda days, user_id=None: [dict(r) for r in rows]
    service._fetch_full_history_sets = lambda user_id=None, history_days=None: [dict(r) for r in rows]

    def _no_db(*_args, **_kwargs):
        raise AssertionError("teste unitario nao pode tocar o banco")

    service.fetch_all = _no_db
    service.fetch_one = _no_db
    return service


def _overview(rows):
    service = _service(rows)
    return {
        item["muscle_group"]: item
        for item in service.get_muscle_group_overview(days=14, limit=10, user_id=1, now_local=AGORA)
    }


def _ratios(rows):
    service = _service(rows)
    items = service._history_items_from_sets(rows)
    return latest_performance_ratios(items, identity=service._exercise_identity, sort_key=service._session_sort_key)


# ---------------------------------------------------------------------------
# Par de exposicoes (funcao pura)
# ---------------------------------------------------------------------------


def test_troca_de_equipamento_nao_gera_queda():
    anterior = _exposure([(8, 100.0)] * 3, equipment_id=7)
    atual = _exposure([(8, 80.0)] * 3, equipment_id=9)  # e1RM -20%, outra maquina

    assert performance_ratio(anterior, atual) is None


def test_troca_de_convencao_nao_gera_queda():
    anterior = _exposure([(10, 30.0)] * 3, convention="total")
    atual = _exposure([(10, 15.0)] * 3, convention="per_side")

    assert performance_ratio(anterior, atual) is None


def test_serie_acima_de_12_reps_nao_decide_por_e1rm():
    """Mesma carga, reps cruas MENORES e RPE maior: o e1RM (Epley+RIR) cai,
    mas com 15 reps ele nao e confiavel. Desde o M78 quem decide sao as reps
    emparelhadas como `reps + RIR`: 15@7 vale 18 e 14@10 vale 14 - a magnitude
    e o Epley desse valor por serie (1,4667 / 1,6), nao o e1RM."""
    anterior = _exposure([(15, 60.0)] * 3, rpe=7.0)
    atual = _exposure([(14, 60.0)] * 3, rpe=10.0)
    assert atual.e1rm / anterior.e1rm < 0.95  # a regra antiga chamaria de queda

    assert performance_ratio(anterior, atual) == pytest.approx((1 + 14 / 30) / (1 + 18 / 30))


def test_p8_mesmas_reps_com_rpe_maior_nao_e_queda():
    """P8 (2026-10-11): mesma carga, mesmas reps cruas, so o RPE subiu ->
    STABLE na fonte unica (`same_reps_higher_rpe`), entao a razao fica em 1,0.
    Antes: 15@7 -> 15@10 dava 0,9375 (reps + RIR) e 8@6 -> 8@10 dava a razao
    do e1RM decisivo (1,2667/1,4 = 0,905 < 0,95, `PERFORMANCE_DROP`)."""
    assert performance_ratio(_exposure([(15, 60.0)] * 3, rpe=7.0), _exposure([(15, 60.0)] * 3, rpe=10.0)) == 1.0
    assert performance_ratio(_exposure([(8, 60.0)] * 3, rpe=6.0), _exposure([(8, 60.0)] * 3, rpe=10.0)) == 1.0


def test_p8_mesmas_reps_com_rpe_maior_nao_gera_performance_drop_na_recuperacao():
    """A recuperacao herda via `performance_ratio`: o par 8x60@6 -> 8x60@10
    nao gera mais o sinal `PERFORMANCE_DROP` nem baixa a prontidao."""
    rows = _sessao(1, 6, [(8, 60.0)] * 3, rpe=6.0) + _sessao(2, 1, [(8, 60.0)] * 3, rpe=10.0)

    ((_, ratio),) = _ratios(rows).items()
    assert ratio == 1.0
    sinais = MuscleRecoveryService.build_blocking_signals(100.0, ratio, False, None, False)
    assert "PERFORMANCE_DROP" not in {sinal["code"] for sinal in sinais}
    assert MuscleRecoveryService.count_negative_signals(ratio, False, None) == 0

    # controle: reps cruas menores com RPE maior continuam caindo
    queda = _sessao(1, 6, [(8, 60.0)] * 3, rpe=6.0) + _sessao(2, 1, [(5, 60.0)] * 3, rpe=10.0)
    ((_, ratio_queda),) = _ratios(queda).items()
    assert ratio_queda < 0.95
    sinais = MuscleRecoveryService.build_blocking_signals(100.0, ratio_queda, False, None, False)
    assert "PERFORMANCE_DROP" in {sinal["code"] for sinal in sinais}


def test_serie_acima_de_12_reps_sem_rpe_nao_cai():
    """Mesmas reps sem RPE: reps cruas dos dois lados, nada caiu."""
    anterior = _exposure([(15, 60.0)] * 3, rpe=None)
    atual = _exposure([(15, 60.0)] * 3, rpe=None)

    assert performance_ratio(anterior, atual) == 1.0


def test_menos_series_nao_e_queda():
    anterior = _exposure([(12, 60.0), (11, 60.0), (10, 60.0)])
    atual = _exposure([(12, 60.0), (11, 60.0)])

    assert paired_reps_ratio(anterior, atual) == 1.0
    assert performance_ratio(anterior, atual) == 1.0


@pytest.mark.parametrize(
    ("carga_atual", "esperado_status"),
    [(94.0, "REDUCED_DOSE"), (88.0, "NO_DIRECT_WORK")],
)
def test_regressao_real_com_e1rm_decisivo_tem_a_mesma_severidade_de_antes(carga_atual, esperado_status):
    """Mesma chave, 8 reps (<= 12): a magnitude e a razao de e1RM - o mesmo
    numero que a regra antiga daria contra uma referencia de 100 kg x 8."""
    anterior = _exposure([(8, 100.0)] * 3)
    atual = _exposure([(8, carga_atual)] * 3)

    ratio = performance_ratio(anterior, atual)

    assert ratio == pytest.approx(carga_atual / 100.0)
    assert MuscleRecoveryService.classify_readiness(100.0, ratio, False, None, False) == esperado_status


def test_queda_leve_de_e1rm_e_regressing_mas_nao_vira_sinal_negativo():
    """-4%: a regra de progresso chama de REGRESSING (limiar 3%), mas o sinal
    da prontidao continua comecando em 0,95 - como antes."""
    ratio = performance_ratio(_exposure([(8, 100.0)] * 3), _exposure([(8, 96.0)] * 3))

    assert ratio == pytest.approx(0.96)
    assert MuscleRecoveryService.count_negative_signals(ratio, False, None) == 0


def test_regressao_por_reps_acima_de_12_usa_equivalente_epley_e_nao_bloqueia():
    """15 -> 13 reps na mesma carga, sem RPE: a razao bruta (0,867) seria
    NO_DIRECT_WORK; o equivalente Epley (1,433/1,5 = 0,956) nem chega a sinal
    leve - duas reps a menos nao bloqueiam o grupo."""
    anterior = _exposure([(15, 40.0), (15, 40.0)], rpe=None)
    atual = _exposure([(13, 40.0), (13, 40.0)], rpe=None)

    ratio = performance_ratio(anterior, atual)

    assert paired_reps_ratio(anterior, atual) == pytest.approx(26 / 30)
    assert ratio == pytest.approx((1 + 13 / 30) / (1 + 15 / 30))
    assert MuscleRecoveryService.classify_readiness(100.0, ratio, False, None, False) != "NO_DIRECT_WORK"


def test_queda_grande_de_reps_ainda_chega_a_no_direct_work():
    """12 -> 7 reps na mesma carga, mesmo RPE 7 (RIR 3 dos dois lados), com a
    serie de 12 sem e1RM decisivo de um lado (13 reps na outra serie):
    (1 + (7,5 + 3)/30) / (1 + (12,5 + 3)/30) = 0,890 < 0,90."""
    anterior = _exposure([(13, 50.0), (12, 50.0)], rpe=7.0)
    atual = _exposure([(8, 50.0), (7, 50.0)], rpe=7.0)
    assert anterior.e1rm_reps > 12  # e1RM nao decisivo -> magnitude por reps

    ratio = performance_ratio(anterior, atual)

    assert ratio == pytest.approx((1 + (7.5 + 3) / 30) / (1 + (12.5 + 3) / 30))
    assert MuscleRecoveryService.classify_readiness(100.0, ratio, False, None, False) == "NO_DIRECT_WORK"


def test_queda_grande_de_reps_sem_rpe_12_para_7():
    """Sem RPE (RIR 0 nos dois lados): 12 -> 7 = 1,233 / 1,4 = 0,881."""
    anterior = _exposure([(12, 50.0)] * 2, rpe=None)
    atual = _exposure([(7, 50.0)] * 2, rpe=None)

    ratio = performance_ratio(anterior, atual)

    assert ratio == pytest.approx((1 + 7 / 30) / (1 + 12 / 30))
    assert MuscleRecoveryService.classify_readiness(100.0, ratio, False, None, False) == "NO_DIRECT_WORK"


def test_rpe_so_de_um_lado_usa_rir_zero_nos_dois():
    anterior = _exposure([(15, 40.0)] * 2, rpe=8.0)
    atual = _exposure([(13, 40.0)] * 2, rpe=None)

    assert performance_ratio(anterior, atual) == pytest.approx((1 + 13 / 30) / (1 + 15 / 30))


def test_progresso_nunca_fica_abaixo_de_1():
    """Mais carga com menos reps e e1RM nao decisivo: inconclusivo na regra
    de progresso, entao nao pode virar queda aqui."""
    anterior = _exposure([(15, 40.0)] * 2)
    atual = _exposure([(13, 50.0)] * 2)

    assert performance_ratio(anterior, atual) == 1.0


# ---------------------------------------------------------------------------
# Historico do VolumeService -> razao da ultima sessao
# ---------------------------------------------------------------------------


def test_historico_sem_par_comparavel_nao_tem_razao():
    rows = _sessao(1, 6, [(8, 100.0)] * 3, equipment_id=7) + _sessao(2, 1, [(8, 80.0)] * 3, equipment_id=9)

    assert _ratios(rows) == {}


def test_historico_regressao_real_tem_razao_na_ultima_sessao():
    rows = _sessao(1, 6, [(8, 100.0)] * 3) + _sessao(2, 1, [(8, 88.0)] * 3)

    ((chave, ratio),) = _ratios(rows).items()
    assert chave[1] == 2
    assert ratio == pytest.approx(0.88)


def test_historico_ignora_aquecimento_e_series_de_tecnica():
    rows = _sessao(1, 6, [(8, 100.0)] * 3) + _sessao(2, 1, [(8, 100.0)] * 3)
    aquecimento = dict(rows[-1], set_id=999, set_number=0, reps=12, weight_kg=40.0, set_type="warmup")
    drop = dict(rows[-1], set_id=998, set_number=9, reps=6, weight_kg=60.0, set_type="drop_set")

    ((_, ratio),) = _ratios(rows + [aquecimento, drop]).items()
    assert ratio == 1.0


def test_historico_fora_da_janela_de_exposicoes_nao_compara():
    rows = _sessao(1, 120, [(8, 100.0)] * 3) + _sessao(2, 1, [(8, 80.0)] * 3)

    assert _ratios(rows) == {}


def test_sem_equipamento_em_todas_as_exposicoes_compara():
    """Excecao do §8.1: historico sem equipamento resolvido e comparavel."""
    rows = _sessao(1, 6, [(8, 100.0)] * 3, equipment_id=None) + _sessao(
        2, 1, [(8, 88.0)] * 3, equipment_id=None
    )

    ((_, ratio),) = _ratios(rows).items()
    assert ratio == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# Overview por grupo
# ---------------------------------------------------------------------------


def test_overview_troca_de_equipamento_nao_baixa_a_prontidao():
    rows = (
        _sessao(1, 9, [(8, 100.0)] * 3)
        + _sessao(2, 6, [(8, 100.0)] * 3)
        + _sessao(3, 3, [(8, 70.0)] * 3, equipment_id=9)
    )

    peito = _overview(rows)["Peitoral"]

    assert peito["performance_ratio"] is None


def test_overview_regressao_real_baixa_como_antes():
    rows = (
        _sessao(1, 9, [(8, 100.0)] * 3)
        + _sessao(2, 6, [(8, 100.0)] * 3)
        + _sessao(3, 3, [(8, 88.0)] * 3)
    )

    peito = _overview(rows)["Peitoral"]

    # Regra antiga: 88 / mediana(100, 100) = 0,88 -> mesma leitura agora.
    assert peito["performance_ratio"] == pytest.approx(0.88)


def test_overview_agrega_so_exercicios_comparaveis():
    """Na ultima sessao do peito: supino regrediu (0,88), crucifixo melhorou
    (e1RM +5%, 1,05) e o supino inclinado trocou de maquina (sem par). A
    media do grupo e (0,88 + 1,05) / 2 - o inclinado nao entra."""
    rows = (
        _sessao(1, 6, [(8, 100.0)] * 3)
        + _sessao(2, 1, [(8, 88.0)] * 3)
        + _sessao(1, 6, [(10, 20.0)] * 3, name="Crucifixo")
        + _sessao(2, 1, [(10, 21.0)] * 3, name="Crucifixo")
        + _sessao(1, 6, [(8, 80.0)] * 3, name="Supino inclinado", equipment_id=7)
        + _sessao(2, 1, [(8, 40.0)] * 3, name="Supino inclinado", equipment_id=11)
    )

    peito = _overview(rows)["Peitoral"]

    assert peito["performance_ratio"] == pytest.approx((0.88 + 1.05) / 2)

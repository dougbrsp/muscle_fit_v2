"""Tendencia da janela do exercicio (issue #176): `comparability.window_trend`,
segunda leitura ao lado do veredito "ultima vs. anterior" (que nao muda)."""

from __future__ import annotations

import pytest

from app.domain.progression_status_labels import (
    EXERCISE_PROGRESSION_LABELS,
    SCOPE_EXERCISE_LAST_VS_PREVIOUS,
    SCOPE_EXERCISE_WINDOW_TREND,
    progression_scope,
)
from app.intelligence.dashboard import comparability as cmp
from app.intelligence.dashboard.contracts import ExerciseProgressResponse
from app.intelligence.dashboard.metrics_registry import THRESHOLDS, ExerciseProgressionStatus as S, metric_version

LIMIAR = THRESHOLDS["e1rm_progress_pct"]


def expo(carga, *, equip=11, conv="total", ctx=None, rpe=8.0, e1rm_reps=10, e1rm="auto", **extra):
    """Uma exposicao de 10 reps; o e1RM acompanha a carga (ou `None` sem RPE)."""
    valor = carga * (1 + 10 / 30) if e1rm == "auto" else e1rm
    return cmp.ExposureSummary(
        key=cmp.build_key(1001, equip, conv, fatigue_context=ctx),
        total_reps=10,
        best_load_kg=carga,
        avg_rpe=rpe,
        e1rm=valor,
        e1rm_reps=e1rm_reps,
        **extra,
    )


def serie(*cargas, **kw):
    return [expo(c, **kw) for c in cargas]


def test_regride_na_ultima_e_progride_na_janela():
    exposicoes = serie(100, 100, 120, 130, 120, 110)
    status, _ = cmp.classify_exercise_progression(exposicoes)
    tendencia = cmp.window_trend(exposicoes)
    assert status is S.REGRESSING
    assert tendencia.status is S.PROGRESSING
    assert tendencia.exposures_used == 6
    assert tendencia.initial_mean == pytest.approx((100 + 100 + 120) * 4 / 3 / 3)
    assert tendencia.reasons == [cmp.REASON_E1RM_UP]


def test_progride_na_ultima_e_regride_na_janela():
    exposicoes = serie(130, 130, 120, 100, 90, 100)
    status, _ = cmp.classify_exercise_progression(exposicoes)
    assert status is S.PROGRESSING
    tendencia = cmp.window_trend(exposicoes)
    assert tendencia.status is S.REGRESSING
    assert tendencia.reasons == [cmp.REASON_E1RM_DOWN]


def test_minimo_de_quatro_exposicoes():
    pouca = cmp.window_trend(serie(100, 100, 120))
    assert pouca.status is S.INSUFFICIENT_DATA
    assert pouca.reasons == [cmp.REASON_NOT_ENOUGH_EXPOSURES]
    assert pouca.change_pct is None and pouca.exposures_used == 3
    assert cmp.window_trend(serie(100, 100, 120, 120)).status is S.PROGRESSING
    assert cmp.window_trend([]).status is S.INSUFFICIENT_DATA


def test_n_impar_deixa_a_do_meio_de_fora():
    base = cmp.window_trend(serie(100, 100, 100, 130, 130))
    com_extremo = cmp.window_trend(serie(100, 100, 9999, 130, 130))
    assert base.status is S.PROGRESSING
    assert (com_extremo.initial_mean, com_extremo.final_mean) == (base.initial_mean, base.final_mean)
    assert com_extremo.change_pct == base.change_pct


@pytest.mark.parametrize(
    "campo",
    [{"equip": 22}, {"conv": "per_side"}, {"ctx": "rested"}],
    ids=["equipamento", "convencao", "contexto_de_fadiga"],
)
def test_so_entram_exposicoes_com_a_chave_da_ultima(campo):
    # as 2 primeiras (chave diferente) tem e1RM alto: se entrassem, a tendencia seria de queda
    antigas = serie(300, 300, **campo)
    atuais = serie(100, 100, 130, 130)
    tendencia = cmp.window_trend([*antigas, *atuais])
    assert tendencia.exposures_used == 4
    assert tendencia.status is S.PROGRESSING
    # so 3 com a chave da ultima: dados insuficientes
    assert cmp.window_trend([*antigas, *atuais[1:]]).status is S.INSUFFICIENT_DATA


def test_sem_e1rm_ou_com_serie_longa_fica_de_fora():
    sem_rpe = expo(100, rpe=None, e1rm=None)
    longa = expo(100, e1rm_reps=20)
    assert cmp.window_trend([sem_rpe, longa, *serie(100, 100, 130)]).exposures_used == 3
    assert cmp.window_trend([sem_rpe, longa, *serie(100, 100, 130)]).status is S.INSUFFICIENT_DATA
    assert cmp.window_trend([sem_rpe, *serie(100, 100, 130, 130)]).exposures_used == 4


def test_limites_no_limiar_e_faixa_estavel():
    def com_variacao(v):
        return cmp.window_trend([expo(100, e1rm=100.0)] * 2 + [expo(100, e1rm=100.0 * (1 + v))] * 2)

    assert com_variacao(LIMIAR).status is S.PROGRESSING
    assert com_variacao(-LIMIAR).status is S.REGRESSING
    estavel = com_variacao(LIMIAR / 2)
    assert estavel.status is S.STABLE and estavel.reasons == [cmp.REASON_NO_CHANGE]
    assert com_variacao(0).status is S.STABLE


def test_nao_altera_o_veredito_da_ultima_vs_anterior():
    exposicoes = serie(100, 100, 120, 130, 120, 110)
    antes = cmp.classify_exercise_progression(exposicoes)
    cmp.window_trend(exposicoes)
    assert cmp.classify_exercise_progression(exposicoes) == antes


def test_contrato_do_detalhe_traz_tendencia_com_escopo_e_versao():
    from tests.unit.test_dashboard_exposure_service import EXERCICIO, JANELA, USUARIO, FakeDb, linha
    from app.intelligence.dashboard import exposure_service as svc

    rows = [
        linha(exercise_row_id=i, activity_id=100 + i, dia=dia, set_number=1, reps=10, weight=carga, rpe=8.0)
        for i, (dia, carga) in enumerate([(1, 40.0), (3, 40.0), (5, 50.0), (7, 50.0)], start=1)
    ]
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    tendencia = payload["window_trend"]
    assert tendencia["status"] == "PROGRESSING"
    assert tendencia["label"] == EXERCISE_PROGRESSION_LABELS["PROGRESSING"]
    assert tendencia["rule_version"] == metric_version("exercise_window_trend") == "exercise-window-trend-v1"
    assert tendencia["scope"] == progression_scope(SCOPE_EXERCISE_WINDOW_TREND)
    assert tendencia["exposures_used"] == 4
    assert payload["status_scope"] == progression_scope(SCOPE_EXERCISE_LAST_VS_PREVIOUS)
    assert payload["status"] == "STABLE"
    ExerciseProgressResponse.model_validate(payload)
    vazio = svc.build_exercise_progress(FakeDb([]), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert vazio["window_trend"] is None


def test_media_inicial_nao_positiva_usa_motivo_de_exposicoes_insuficientes():
    tendencia = cmp.window_trend(serie(100, 100, 100, 100, e1rm=0.0))
    assert tendencia.status is S.INSUFFICIENT_DATA
    assert tendencia.reasons == [cmp.REASON_NOT_ENOUGH_EXPOSURES]

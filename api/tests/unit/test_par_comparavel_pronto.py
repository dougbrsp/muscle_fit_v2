"""Issue #188: o dono da progressao entrega o PAR comparavel pronto.

`exposure_service.latest_comparison` (par, variacoes, direcao) e
`verdict_run_flags`/`in_verdict_run` batem com o que o veredito
(`comparability.progression_outcome`) comparou; os consumidores so leem.
"""

from __future__ import annotations

from app.intelligence import progression_service
from app.intelligence.dashboard import comparability as cmp
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard.contracts import LatestComparisonOut

from tests.unit.test_calibracao_contexto_maquina import ACADEMIA_1, EX, LEG_PRESS, montar, sessao


def _par(exposures):
    return svc.latest_comparison(exposures)


def _metrica(par, code):
    return next(m for m in par["metrics"] if m["code"] == code)


def _item_do_lote(exposures):
    """Item no formato de `classify_batch` (sem banco)."""
    outcome = cmp.progression_outcome(svc.to_summaries(exposures))
    return {
        "exercise_id": EX,
        "name": "Leg press",
        "status": outcome.status.value,
        "status_reasons": outcome.reasons,
        "comparable_exposures": outcome.comparable_count,
        "last_local_date": exposures[-1]["local_date"],
        "_exposures": exposures,
        "_latest_comparison": svc.latest_comparison(exposures),
    }


def test_sem_troca_o_par_e_o_que_o_veredito_comparou():
    exposures = montar(*ACADEMIA_1)
    par = _par(exposures)
    outcome = cmp.progression_outcome(svc.to_summaries(exposures))
    assert par["comparable"] and par["reasons"] == []
    assert (par["previous_index"], par["current_index"]) == (outcome.pair.previous_index, 2) == (1, 2)
    assert (par["previous_activity_id"], par["current_activity_id"]) == (2, 3)
    reps = _metrica(par, "paired_reps")
    # Reps = as emparelhadas que a regra comparou (soma ajustada pelo esforco).
    antes, depois = par["paired_reps"]["previous_sum"], par["paired_reps"]["current_sum"]
    assert (reps["previous"], reps["current"], reps["delta"], reps["better_when"]) == (antes, depois, 2, "up")
    assert _metrica(par, "best_load_kg")["delta"] == 0
    # A variacao percentual reaproveita `_pct_change` do dono (pontos).
    assert abs(reps["delta_pct"] - 100 * (depois - antes) / antes) < 1e-6
    assert LatestComparisonOut.model_validate(par).previous_index == 1


def test_pertence_a_sequencia_e_o_run_do_veredito():
    exposures = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [13, 13]))
    outcome = cmp.progression_outcome(svc.to_summaries(exposures))
    flags = svc.verdict_run_flags(exposures)
    assert [i for i, f in enumerate(flags) if f["in_verdict_run"]] == outcome.run_indices == [0, 1, 2, 4]
    # Fora de calibracao as duas marcacoes coincidem; a outra academia (3) fica fora das duas.
    assert [i for i, f in enumerate(flags) if f["in_context_run"]] == [0, 1, 2, 4]


def test_outra_academia_pulada_par_salta_a_exposicao():
    # Academia 1 ja tem 3 comparaveis: a volta nao calibra; a de outra academia e pulada.
    exposures = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [13, 13]))
    par = _par(exposures)
    assert par["comparable"] and par["previous_index"] == 2 and par["previous_activity_id"] == 3
    assert par["paired_reps"]["previous_reps"] == [12, 12]
    assert cmp.REASON_OTHER_GYM_SKIPPED in svc.progression_verdict(exposures)["status_reasons"]


def test_outro_contexto_de_fadiga_pulado_par_salta_a_exposicao():
    def resumo(reps, fadiga="rested"):
        key = cmp.build_key(EX, LEG_PRESS, "total", fatigue_context=fadiga)
        return cmp.ExposureSummary(key=key, total_reps=reps, best_load_kg=60.0)

    historico = [resumo(10), resumo(30, "pre_fatigued"), resumo(11)]
    pair = cmp.latest_comparable_pair(historico)
    outcome = cmp.progression_outcome(historico)
    assert (pair.previous_index, pair.current_index, pair.reasons) == (0, 2, [])
    assert pair == outcome.pair and outcome.run_indices == [0, 2]


def test_em_calibracao_nao_ha_anterior_nem_sequencia():
    exposures = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2))
    par = _par(exposures)
    assert not par["comparable"] and par["reasons"] == [cmp.REASON_EQUIPMENT_CALIBRATING, cmp.REASON_OTHER_GYM_SKIPPED]
    assert par["previous_index"] is None and par["previous_activity_id"] is None
    assert all(m["delta"] is None for m in par["metrics"])
    flags = svc.verdict_run_flags(exposures)
    assert not any(f["in_verdict_run"] for f in flags)
    # O grafico ainda plota o contexto atual (academia 2), sem destacar nada.
    assert [i for i, f in enumerate(flags) if f["in_context_run"]] == [3, 4]
    assert LatestComparisonOut.model_validate(par).comparable is False


def test_troca_fisica_sem_par_com_o_motivo_do_painel():
    exposures = montar(*ACADEMIA_1, sessao(4, 7, [5, 5], eq=12))
    par = _par(exposures)
    assert par["previous_index"] is None and par["reasons"] == [cmp.REASON_KEY_CHANGED]


def test_progression_service_le_o_par_pronto():
    calibrando = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2))
    [ctx] = progression_service.exercise_progression_context([_item_do_lote(calibrando)])
    assert ctx["previous_comparable"] is None and ctx["paired_top_reps"] is None

    fora = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [13, 13]))
    [ctx] = progression_service.exercise_progression_context([_item_do_lote(fora)])
    assert ctx["previous_comparable"]["local_date"] == fora[2]["local_date"]
    assert ctx["paired_top_reps"] == svc.latest_comparison(fora)["paired_reps"]


def test_direcao_da_carga_segue_a_convencao():
    assert cmp.metric_better_when("best_load_kg", "assisted", e1rm_decisive=True) == "down"
    assert cmp.metric_better_when("best_load_kg", "bodyweight", e1rm_decisive=True) == "neutral"
    assert cmp.metric_better_when("e1rm", "total", e1rm_decisive=False) == "neutral"
    assert cmp.metric_better_when("e1rm", "total", e1rm_decisive=True) == "up"

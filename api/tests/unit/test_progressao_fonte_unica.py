"""Issue #188: a curva de `/analytics/exercise-progress` e o resumo por sessao
de `/analytics/dashboard-progression` saem do DONO da progressao
(`exposure_service`): nenhuma agregacao de carga/RPE/e1RM nas rotas.

Sem banco: as exposicoes sao montadas pelo proprio dono a partir das linhas
planas da query (`FakeDb`)."""

from __future__ import annotations

import inspect
import re
from contextlib import nullcontext

from app import analytics
from app.intelligence.dashboard import exposure_service as svc
from tests.unit.test_progressao_tela_mesma_regra import (
    EXERCICIO,
    JANELA,
    SMITH,
    USUARIO,
    FakeDb,
    exposicao,
)
from tests.unit.test_progressao_unica_m74 import _d

OUTRO = 2002


def _exposures(rows):
    return svc.get_exposures_between(
        FakeDb(rows), EXERCICIO, user_id=USUARIO, date_from=JANELA.date_from, date_to=JANELA.date_to
    )


def _historico():
    return _exposures(
        exposicao(1, 1, _d(10), [(10, 60.0, 8.0)] * 3, equipment_id=SMITH)
        + exposicao(2, 2, _d(14), [(10, 60.0, 8.0), (20, 55.0, 10.0)])
        + exposicao(3, 3, _d(18), [(11, 62.5, 8.0), (10, 60.0, 8.0)])
    )


class _Id:
    user_id = USUARIO


def _rota_curva(monkeypatch, por_exercicio):
    monkeypatch.setattr(
        analytics,
        "_fetch_all",
        lambda sql, params=None: [{"exercise_id": ex, "matched_total": len(por_exercicio)} for ex in por_exercicio][
            : params[-1]
        ]
        if "GROUP BY exercise_id" in sql
        else [{"id": i, "name": f"Treino {i}"} for i in params[1:]],
    )
    monkeypatch.setattr(analytics, "_days_ago", lambda user_id, days: JANELA.date_from)
    monkeypatch.setattr(analytics, "_user_today", lambda user_id: JANELA.date_to)
    monkeypatch.setattr(analytics, "_connect", lambda: nullcontext(object()))
    monkeypatch.setattr(svc, "_fetch_exercise", lambda conn, ex: {"exercise_name": f"Ex {ex}", "exercise_amazfit_name": None})
    monkeypatch.setattr(svc, "get_exposures_between", lambda conn, ex, **kw: por_exercicio[ex])
    return analytics.exercise_progress(name="agach", days=30, goal=None, identity=_Id())


def test_curva_e_o_resumo_de_exposicao_do_dono(monkeypatch):
    exposures = _historico()
    body = _rota_curva(monkeypatch, {EXERCICIO: exposures})

    (serie,) = body["series"]
    assert serie["exercise_id"] == EXERCICIO
    flags = svc.verdict_run_flags(exposures)
    for ponto, exposure, flag in zip(serie["items"], exposures, flags):
        snap = svc.exposure_snapshot(exposure)
        assert ponto["best_weight_kg"] == snap["best_load_kg"] == exposure["best_load_kg"]
        assert ponto["avg_rpe"] == snap["avg_rpe"] and ponto["e1rm"] == snap["e1rm"]
        assert ponto["reps_total"] == exposure["total_reps"] and ponto["volume_kg"] == exposure["tonnage"]
        assert ponto["sets_count"] == snap["working_sets"]
        assert ponto["comparability_key"] == exposure["comparability_key"]
        assert (ponto["in_context_run"], ponto["in_verdict_run"]) == (flag["in_context_run"], flag["in_verdict_run"])
        assert ponto["activity_name"] == f"Treino {exposure['activity_id']}"
    # Carga de TOPO das series de trabalho (62,5), nao a do melhor e1RM.
    assert serie["items"][-1]["best_weight_kg"] == 62.5
    # O Smith (outro contexto) fica marcado fora da sequencia; a tela so separa.
    assert serie["items"][0]["in_context_run"] is False and serie["items"][-1]["in_context_run"] is True
    assert body["items"] == serie["items"]


def test_varios_exercicios_no_filtro_viram_uma_serie_cada(monkeypatch):
    outro = svc.get_exposures_between(
        FakeDb(exposicao(9, 9, _d(12), [(8, 40.0, 8.0)])),
        OUTRO,
        user_id=USUARIO,
        date_from=JANELA.date_from,
        date_to=JANELA.date_to,
    )
    body = _rota_curva(monkeypatch, {EXERCICIO: _historico(), OUTRO: outro})
    assert [s["exercise_id"] for s in body["series"]] == [EXERCICIO, OUTRO]
    assert {p["exercise_id"] for p in body["series"][1]["items"]} == {OUTRO}
    assert len(body["items"]) == 4
    assert [p["local_date"] for p in body["items"]] == sorted(p["local_date"] for p in body["items"])


def test_sem_exercicio_no_filtro_nao_le_exposicoes(monkeypatch):
    body = _rota_curva(monkeypatch, {})
    assert body["series"] == [] and body["items"] == []


def test_rota_da_curva_nao_agrega_series_por_conta_propria():
    fonte = inspect.getsource(analytics.exercise_progress)
    for agregado in ("MAX(", "SUM(", "AVG(", "COUNT("):
        assert agregado not in fonte
    # O SQL de match so escolhe e ordena exercicios: nenhuma agregacao de
    # DESEMPENHO (carga, reps, RPE); `MAX(a.local_date)` so ordena a busca.
    for com_goal in (False, True):
        match = analytics._exercise_progress_match_sql(com_goal)
        for proibido in ("weight_kg", "reps,", "es.reps ", ".rpe", "SUM(", "AVG("):
            assert proibido not in match, proibido
        assert re.findall(r"MAX\([^)]*\)", match) == ["MAX(a.local_date)"]
        # Mesmos predicados da leitura do dono: nenhum exercicio casado sai vazio.
        assert "deload" in match.lower() and "exercise_sets" in match
        assert ("training_goal" in match) is com_goal
    # Deload e series nao-trabalho ficam fora na leitura do dono.
    assert "deload" in svc.EXPOSURES_SQL_TEMPLATE.lower()
    assert "deload" in svc.EXPOSURES_ALL_GOALS_SQL_TEMPLATE.lower()


def _linha(activity_id, dia, reps, kg, rpe, set_type="working", deload=None):
    return {
        "activity_id": activity_id, "local_date": dia, "activity_name": "Treino", "set_type": set_type,
        "reps": reps, "weight_kg": kg, "rpe": rpe, "set_number": 1, "gym_equipment_id": None,
        "mapping_id": EXERCICIO, "exercise_row_id": activity_id * 10, "deload_source": deload,
    }


def test_resumo_por_sessao_do_grafico_e_o_do_dono():
    exposures = _historico()
    rows = [
        _linha(1, "2026-09-10", 10, 60.0, 8.0),
        _linha(2, "2026-09-14", 10, 60.0, 8.0),
        _linha(2, "2026-09-14", 20, 55.0, 10.0),
        _linha(3, "2026-09-18", 11, 62.5, 8.0),
        _linha(3, "2026-09-18", 3, 90.0, 5.0, set_type="warmup"),  # nao-trabalho: fora
        _linha(4, "2026-09-20", 8, 30.0, 6.0, deload="manual"),  # deload: fora da leitura do dono
    ]
    items = analytics._build_progression_sessions(rows, exposures=exposures)
    analytics._attach_session_evolution(items, exposures)
    por_sessao = {i["activity_id"]: i for i in items}

    for exposure in exposures:
        item, snap = por_sessao[exposure["activity_id"]], svc.exposure_snapshot(exposure)
        assert item["best_load_kg"] == item["top_load_kg"] == snap["best_load_kg"]
        assert item["avg_rpe"] == snap["avg_rpe"] and item["e1rm"] == snap["e1rm"]
        assert item["e1rm_reps"] == snap["e1rm_reps"] and item["e1rm_reliable"] == snap["e1rm_reliable"]
        assert item["top_set_reps"] == snap["top_set_reps"]
    assert por_sessao[3]["best_load_kg"] == 62.5 and por_sessao[3]["sets_count"] == 1

    deload = por_sessao[4]
    assert deload["deload_source"] == "manual" and deload["e1rm"] is None and deload["e1rm_reliable"] is False
    assert deload["best_load_kg"] == 30.0  # resumo do dono sobre as series dela
    assert deload["evolution"] is None and deload["in_context_run"] is None


def test_tabela_nao_tem_laco_proprio_de_e1rm():
    fonte = inspect.getsource(analytics._build_progression_sessions)
    assert "estimated_1rm" not in fonte and "MuscleRecoveryService" not in fonte
    assert "top_load_kg" not in inspect.getsource(analytics._attach_session_evolution)


def test_series_de_rpe_alto_vem_do_dono(monkeypatch):
    limiar = svc.HIGH_RPE_MIN
    exposures = _exposures(
        exposicao(1, 1, _d(10), [(10, 60.0, limiar - 1), (10, 60.0, limiar), (8, 60.0, limiar + 1)])
    )
    (ponto,) = _rota_curva(monkeypatch, {EXERCICIO: exposures})["series"][0]["items"]
    assert ponto["high_rpe_sets"] == exposures[0]["_high_rpe_sets"] == 2


# --- Rodada 1 de correcao (#188) --------------------------------------------


def test_curva_limita_exercicios_casados_e_informa_o_corte(monkeypatch):
    por_exercicio = {ex: [] for ex in range(1, analytics.EXERCISE_PROGRESS_MAX_EXERCISES + 4)}
    body = _rota_curva(monkeypatch, por_exercicio)
    lidos = [s["exercise_id"] for s in body["series"]]
    assert lidos == list(range(1, analytics.EXERCISE_PROGRESS_MAX_EXERCISES + 1))
    assert body["truncated"] is True and body["matched_exercises"] == len(por_exercicio)
    match = analytics._exercise_progress_match_sql(False)
    assert "ORDER BY last_date DESC" in match and match.rstrip().endswith("LIMIT %s")
    assert "COUNT(*) OVER ()" in match

    poucos = _rota_curva(monkeypatch, {EXERCICIO: []})
    assert poucos["truncated"] is False and poucos["matched_exercises"] == 1


def test_match_da_curva_tem_a_mesma_data_final_da_leitura(monkeypatch):
    capturado = {}

    def fetch(sql, params=None):
        if "GROUP BY exercise_id" in sql:
            capturado["params"] = params
        return []

    monkeypatch.setattr(analytics, "_fetch_all", fetch)
    monkeypatch.setattr(analytics, "_days_ago", lambda user_id, days: JANELA.date_from)
    monkeypatch.setattr(analytics, "_user_today", lambda user_id: JANELA.date_to)
    analytics.exercise_progress(name="agach", days=30, goal=None, identity=_Id())
    assert capturado["params"][:3] == (USUARIO, JANELA.date_from, JANELA.date_to)
    for com_goal in (False, True):
        match = analytics._exercise_progress_match_sql(com_goal)
        assert match.index("a.local_date >= %s") < match.index("a.local_date <= %s") < match.index("LIKE %s")


def test_sql_de_rpe_alto_usa_o_limiar_do_dono():
    fonte = inspect.getsource(analytics)
    assert "es.rpe >= 9" not in fonte
    assert fonte.count("es.rpe >= {_high_rpe_min()}") == 3
    assert analytics._high_rpe_min() == svc.HIGH_RPE_MIN


def test_seta_por_sessao_e_a_transicao_do_par_do_dono():
    from app.intelligence.dashboard import comparability as cmp

    exposures = _historico()
    summaries = svc.to_summaries(exposures)
    setas = svc.consecutive_comparisons(exposures)
    assert setas[0] is None
    for index in range(1, len(exposures)):
        pair = cmp.progression_outcome(summaries[: index + 1]).pair
        assert setas[index] == {"status": pair.transition_status.value, "reasons": pair.transition_reasons}
    assert "compare_exposures" not in inspect.getsource(svc.consecutive_comparisons).split('"""')[-1]


def test_e1rm_decisivo_vem_do_par_do_dono():
    from app.intelligence.dashboard import comparability as cmp

    exposures = _historico()
    pair = cmp.latest_comparable_pair(svc.to_summaries(exposures))
    assert svc.latest_comparison(exposures)["e1rm_decisive"] is pair.e1rm_decisive


def test_tendencia_de_desempenho_e_do_dono():
    from app.intelligence.dashboard import comparability as cmp
    from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus as S

    assert cmp.performance_trend(S.PLATEAU_SUSPECTED) == cmp.performance_trend("STABLE") == cmp.TREND_MAINTAINING
    assert cmp.performance_trend("PROGRESSING") == cmp.TREND_PROGRESSING
    assert cmp.performance_trend(S.NOT_COMPARABLE) is None and cmp.performance_trend("x") is None


def test_veredito_no_aparelho_e_do_dono():
    exposures = _historico()
    outcome, historico, no_aparelho = svc.outcome_for_equipment(exposures, SMITH)
    assert no_aparelho is True and historico == exposures[:1]
    _, todas, no_aparelho = svc.outcome_for_equipment(exposures, 999999)
    assert no_aparelho is False and todas == exposures
    assert svc.outcome_for_equipment([], None)[1:] == ([], False)

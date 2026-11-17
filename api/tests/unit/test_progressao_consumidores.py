"""Issue #188: as "progressoes" por sessao da lista de treinos
(`/dashboard/treinos`) vem do DONO - a seta da sessao
(`exposure_service.consecutive_comparisons`, transicao do par de
`comparability.progression_outcome`) - com a mesma janela, academia, contexto
de fadiga, objetivo e calibracao do detalhe do exercicio. O recorde continua
em `session_records` (chave fisica, sem fadiga nem academia).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.domain.prior_fatigue import PriorFatigue, PriorFatigueBand
from app.intelligence.dashboard import adherence_service, session_records
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard.adherence_service import compute_records_and_progressions
from app.intelligence.dashboard.comparability import VERDICT_LOOKBACK_EXPOSURES
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus as S

EX = 1001
LEG_PRESS = 11
OUTRA_MAQUINA = 12
FADIGA = PriorFatigue(1, "Peitoral", 4.0, 4.0 / 9, PriorFatigueBand.PRE_FATIGUED)


def sessao(aid, reps, carga=60.0, *, gym=1, eq=LEG_PRESS, tipo="MACHINE", goal="hypertrophy"):
    """Linhas (uma por serie) no formato do lote do dono; `exercise_row_id` = `activity_id`."""
    return [
        {
            "canonical_exercise_id": EX,
            "exercise_name": "Leg press",
            "exercise_row_id": aid,
            "activity_id": aid,
            "local_date": date(2026, 9, aid),
            "started_at": datetime(2026, 9, aid, 19, 0, 0),
            "position_in_session": 1,
            "training_goal": goal,
            "equipment_id": eq,
            "equipment_name": None,
            "gym_id": gym,
            "equipment_type_code": tipo,
            "load_convention": "total",
            "prescribed_reps_target": "8-12",
            "set_number": numero,
            "reps": r,
            "weight_kg": carga,
            "rpe": 8.0,
            "rest_seconds": None,
            "notes": None,
        }
        for numero, r in enumerate(reps, start=1)
    ]


def _rows(*sessoes):
    return [linha for s in sessoes for linha in s]


def lista(rows, pagina, fadiga=None):
    return svc.session_progressions_from_rows(rows, pagina, fadiga)


def setas_progredindo(rows, fadiga=None):
    """Sessoes cuja seta do detalhe (`consecutive_comparisons`) e PROGRESSING."""
    exposures = svc.assemble_exposures(svc.build_exposures(rows), EX, fadiga)
    return {
        exposure["activity_id"]
        for exposure, seta in zip(exposures, svc.consecutive_comparisons(exposures))
        if seta is not None and seta["status"] == S.PROGRESSING.value
    }


def _confere(rows, fadiga=None):
    """A lista conta exatamente as sessoes com seta PROGRESSING."""
    todas = sorted({linha["activity_id"] for linha in rows})
    resultado = lista(rows, todas, fadiga)
    assert set(resultado) == setas_progredindo(rows, fadiga)
    assert all(ids == {EX} for ids in resultado.values())
    return set(resultado)


def test_mesma_chave_e_fadiga_mais_reps_e_progressao():
    rows = _rows(sessao(1, [8, 8, 8]), sessao(2, [10, 10, 10]))
    assert _confere(rows) == {2}


def test_sessao_de_outra_academia_no_meio_e_pulada():
    rows = _rows(sessao(1, [8, 8, 8]), sessao(2, [12, 12, 12], gym=2), sessao(3, [10, 10, 10]))
    progrediu = _confere(rows)
    # A sessao da outra academia nao e comparada com a da academia 1.
    assert 2 not in progrediu


def test_maquina_em_calibracao_nao_conta_progressao():
    rows = _rows(
        sessao(1, [8, 8, 8]),
        sessao(2, [8, 8, 8], eq=OUTRA_MAQUINA),
        sessao(3, [10, 10, 10], eq=OUTRA_MAQUINA),
    )
    exposures = svc.assemble_exposures(svc.build_exposures(rows), EX)
    assert svc.consecutive_comparisons(exposures)[-1]["status"] == S.NOT_COMPARABLE.value
    assert _confere(rows) == set()


def test_outro_contexto_de_fadiga_nao_compara_com_a_sessao_do_outro_contexto():
    rows = _rows(sessao(1, [8, 8, 8]), sessao(2, [10, 10, 10]), sessao(3, [6, 6, 6]))
    fadiga = {3: FADIGA}
    # 2 progrediu contra 1; 3 (pre-fatigado) e a primeira do contexto: nao compara com 2.
    assert _confere(rows, fadiga) == {2}
    rows4 = rows + sessao(4, [12, 12, 12])
    # 4 (descansado) compara com 2, nao com 3.
    assert _confere(rows4, fadiga) == {2, 4}


def test_objetivo_diferente_nao_compara():
    rows = _rows(sessao(1, [8, 8, 8]), sessao(2, [10, 10, 10], goal="strength"))
    assert lista(rows, [1, 2]) == {}


def test_so_as_sessoes_da_pagina_e_janela_de_oito_basta():
    """A lista le ate `VERDICT_LOOKBACK_EXPOSURES` exposicoes antes da sessao
    mais antiga da pagina: o resultado e o mesmo de ler o historico inteiro."""
    sessoes = [sessao(aid, [8 + (aid % 3)] * 3) for aid in range(1, 15)]
    inteiro = _rows(*sessoes)
    pagina = [12, 13, 14]
    recortado = _rows(*sessoes[12 - 1 - VERDICT_LOOKBACK_EXPOSURES :])
    assert lista(inteiro, pagina) == lista(recortado, pagina)
    assert set(lista(inteiro, pagina)) <= set(pagina)
    assert lista(inteiro, pagina) == {
        aid: {EX} for aid in setas_progredindo(inteiro) if aid in pagina
    }


def _hist(aid, carga):
    return {
        "activity_id": aid,
        "exercise_row_id": aid,
        "exercise_id": EX,
        "local_date": None,
        "started_at": None,
        "position_in_session": 1,
        "equipment_id": LEG_PRESS,
        "load_convention": "total",
        "prescribed_reps_target": "8-12",
        "max_weight_kg": carga,
        "min_weight_kg": carga,
    }


def test_recorde_sem_progressao_e_progressao_sem_recorde():
    # 2: mais carga com bem menos reps (e1RM menor) -> recorde de carga, sem progressao.
    # 3: mesma carga de 2 com mais reps -> progressao, sem recorde.
    historico = [_hist(1, 60.0), _hist(2, 70.0), _hist(3, 70.0)]
    rows = _rows(sessao(1, [10, 10, 10], 60.0), sessao(2, [3, 3, 3], 70.0), sessao(3, [5, 5, 5], 70.0))
    progressoes = lista(rows, [2, 3])
    assert compute_records_and_progressions(historico, progressoes, [2, 3]) == {
        2: {"records": 1, "progressions": 0},
        3: {"records": 0, "progressions": 1},
    }
    # O recorde nao muda: e o mesmo de `session_records`.
    assert session_records.record_exercise_ids(session_records.parse_history(historico)) == {2: {EX}}


class _Cursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)

    def cursor(self):
        return self.cursor_obj


def test_leitura_em_lote_uma_consulta_com_a_janela_do_dono(monkeypatch):
    monkeypatch.setattr(svc, "load_fatigue_contexts", lambda *a, **k: {})
    monkeypatch.setattr(svc, "load_convention_select_expr", lambda cursor: "NULL")
    # O SQL devolve as series mais recentes primeiro (ORDER BY ... DESC + LIMIT).
    rows = list(reversed(_rows(sessao(1, [8, 8, 8]), sessao(2, [10, 10, 10]))))
    conn = _Conn(rows)
    resultado = svc.get_session_progressions(
        conn, user_id=7, activity_ids=[2], exercise_ids=[EX, 5], date_from=date(2026, 9, 2), date_to=date(2026, 9, 2)
    )
    assert resultado == {2: {EX}}
    consultas = [sql for sql, _ in conn.cursor_obj.executed if "ROW_NUMBER" in sql]
    assert len(consultas) == 1
    _sql, params = conn.cursor_obj.executed[-1]
    assert params == (
        date(2026, 9, 2), 7, date(2026, 9, 2), 5, EX, date(2026, 9, 2), VERDICT_LOOKBACK_EXPOSURES,
        svc.MAX_SESSION_PROGRESSION_ROWS + 1,
    )
    assert "LIMIT %s" in _sql and "es.id DESC" in _sql
    assert "ranked.local_date >= %s OR ranked.rn <= %s" in _sql


def test_consumidores_nao_decidem_progressao():
    """A lista nao monta chave, nao escolhe a anterior nem compara exposicoes."""
    for modulo in (adherence_service, session_records):
        for nome in ("compare_exposures", "previous_in_key", "progression_exercise_ids", "exposures_to_compare"):
            assert not hasattr(modulo, nome), (modulo.__name__, nome)


# ---------------------------------------------------------------------------
# Recuperacao: a razao de desempenho e a do PAR do dono (issue #188)
# ---------------------------------------------------------------------------

from app.intelligence import exercise_performance as perf  # noqa: E402
from app.intelligence.dashboard.comparability import performance_ratio_of  # noqa: E402

# 12 -> 7 reps na mesma carga, sem RPE: equivalente Epley (1 + 7/30)/(1 + 12/30),
# o mesmo valor que `exercise_performance.paired_reps_epley_ratio` dava antes.
QUEDA_12_PARA_7 = (1 + 7 / 30) / (1 + 12 / 30)


def _item_rec(aid, reps, *, gym=1, eq=LEG_PRESS, tipo="MACHINE"):
    dia = datetime(2026, 11, aid, 18, 0)
    return {
        "activity_id": aid,
        "exercise_id": aid * 10,
        "mapping_id": EX,
        "local_date": dia.date(),
        "trained_at": dia,
        "equipment_id": eq,
        "gym_id": gym,
        "equipment_type_code": tipo,
        "load_convention": "total",
        "training_goal": "hypertrophy",
        "deload_source": None,
        "set_rows": [
            {"set_number": n, "reps": reps, "weight_kg": 60.0, "rpe": None, "set_type": "working"} for n in (1, 2, 3)
        ],
    }


def _razao(items):
    ratios = perf.latest_performance_ratios(
        items, identity=lambda item: item["mapping_id"], sort_key=lambda item: item["trained_at"]
    )
    ultimo = items[-1]
    return ratios.get((ultimo["exercise_id"], ultimo["activity_id"]))


def _razao_do_dono(items):
    summaries = svc.to_summaries(svc.assemble_exposures([perf._raw_exposure(item) for item in items], EX))
    return performance_ratio_of(summaries)


def test_recuperacao_mesma_chave_formula_inalterada():
    items = [_item_rec(1, 12), _item_rec(2, 7)]
    assert _razao(items) == _razao_do_dono(items)
    assert abs(_razao(items) - QUEDA_12_PARA_7) < 1e-9


def test_recuperacao_maquina_usa_a_anterior_da_mesma_academia():
    items = [_item_rec(1, 12), _item_rec(2, 12), _item_rec(3, 5, gym=2), _item_rec(4, 7)]
    assert abs(_razao(items) - QUEDA_12_PARA_7) < 1e-9  # antes: contra a de 5 reps (outra academia)
    assert _razao(items) == _razao_do_dono(items)


def test_recuperacao_em_calibracao_nao_tem_razao():
    items = [_item_rec(1, 12, eq=OUTRA_MAQUINA), _item_rec(2, 12), _item_rec(3, 7)]
    assert _razao(items) is None
    assert _razao_do_dono(items) is None


def test_recuperacao_troca_de_aparelho_nao_tem_razao():
    items = [_item_rec(1, 12), _item_rec(2, 7, eq=OUTRA_MAQUINA)]
    assert _razao(items) is None


def test_recuperacao_peso_livre_em_academias_diferentes_compara():
    items = [_item_rec(1, 12, tipo="FREE_WEIGHT"), _item_rec(2, 7, gym=2, tipo="FREE_WEIGHT")]
    assert abs(_razao(items) - QUEDA_12_PARA_7) < 1e-9


def _passagem(aid, reps, segunda):
    """Mesma sessao, outra linha de exercicio (duas passagens pelo exercicio)."""
    item = _item_rec(aid, reps)
    if segunda:
        item["exercise_id"] = aid * 10 + 1
        item["trained_at"] = item["trained_at"] + timedelta(minutes=30)
    return item


def test_recuperacao_duas_passagens_comparam_com_a_sessao_anterior():
    # Sessao 1 com 10 reps; sessao 2 com 12 e depois 7 (fadiga intra-sessao).
    items = [_item_rec(1, 10), _passagem(2, 12, False), _passagem(2, 7, True)]
    ratios = perf.latest_performance_ratios(
        items, identity=lambda item: item["mapping_id"], sort_key=lambda item: item["trained_at"]
    )
    # Cada passagem contra a sessao 1 (10 reps), nunca passagem 1 x passagem 2
    # (12 -> 7 daria QUEDA_12_PARA_7). A razao e a do PAR do dono.
    assert abs(ratios[(20, 2)] - _razao_do_dono([items[0], items[1]])) < 1e-9
    assert abs(ratios[(21, 2)] - _razao_do_dono([items[0], items[2]])) < 1e-9
    assert abs(ratios[(21, 2)] - (1 + 7 / 30) / (1 + 10 / 30)) < 1e-9
    assert abs(ratios[(21, 2)] - QUEDA_12_PARA_7) > 1e-3
    assert ratios[(20, 2)] != ratios[(21, 2)]


def test_recuperacao_duas_passagens_sem_sessao_anterior_nao_tem_razao():
    items = [_passagem(2, 12, False), _passagem(2, 7, True)]
    ratios = perf.latest_performance_ratios(
        items, identity=lambda item: item["mapping_id"], sort_key=lambda item: item["trained_at"]
    )
    assert ratios == {}


def test_lista_de_treinos_teto_de_linhas_omite_progressoes(monkeypatch, caplog):
    monkeypatch.setattr(svc, "load_fatigue_contexts", lambda *a, **k: {})
    monkeypatch.setattr(svc, "load_convention_select_expr", lambda cursor: "NULL")
    monkeypatch.setattr(svc, "MAX_SESSION_PROGRESSION_ROWS", 5)
    rows = list(reversed(_rows(sessao(1, [8, 8, 8]), sessao(2, [10, 10, 10]))))  # 6 linhas > teto 5
    conn = _Conn(rows)
    with caplog.at_level("WARNING"):
        resultado = svc.get_session_progressions(
            conn, user_id=7, activity_ids=[2], exercise_ids=[EX], date_from=date(2026, 9, 2), date_to=date(2026, 9, 2)
        )
    assert resultado is None  # sem contagem parcial nem "zero": indisponivel
    assert conn.cursor_obj.executed[-1][1][-1] == 6  # LIMIT teto + 1
    assert "truncado" in caplog.text


def test_recuperacao_nao_decide_par_nem_razao():
    import inspect

    fonte = inspect.getsource(perf)
    for proibido in ("compare_exposures(", "keys_match(", "paired_top_reps(", "e1rm_is_decisive(", "min(", "max("):
        assert proibido not in fonte, proibido


# ---------------------------------------------------------------------------
# Passada C (#188): o que os consumidores restantes (insight, corpo, grupo)
# leem pronto do dono, com os MESMOS valores das contas que eles faziam.
# ---------------------------------------------------------------------------

from app import analytics  # noqa: E402
from app.intelligence.dashboard import comparability as cmp  # noqa: E402
from app.intelligence.dashboard import metrics_registry as reg  # noqa: E402

_TODOS = [status.value for status in S]
_ANTIGO_COMPARAVEIS = {S.PROGRESSING.value, S.STABLE.value, S.PLATEAU_SUSPECTED.value, S.REGRESSING.value}
_ANTIGO_OK = {S.PROGRESSING.value, S.STABLE.value}


def test_conjuntos_de_status_iguais_aos_locais_antigos():
    assert {s.value for s in cmp.COMPARABLE_PROGRESSION_STATUSES} == _ANTIGO_COMPARAVEIS
    assert {s.value for s in cmp.PROGRESS_OK_STATUSES} == _ANTIGO_OK
    for status in _TODOS:
        assert cmp.is_comparable_status(status) is (status in _ANTIGO_COMPARAVEIS)
        assert cmp.is_progress_ok_status(S(status)) is (status in _ANTIGO_OK)
    assert cmp.is_comparable_status(None) is False


def test_fracao_da_recomposicao_igual_a_conta_antiga():
    items = [{"status": s} for s in ("PROGRESSING", "STABLE", "PLATEAU_SUSPECTED", "REGRESSING",
                                     "NOT_COMPARABLE", "INSUFFICIENT_DATA", "STABLE")]
    comparable = [i for i in items if i["status"] in _ANTIGO_COMPARAVEIS]
    ok = [i for i in comparable if i["status"] in _ANTIGO_OK]
    assert cmp.progression_share(items) == {
        "comparable": len(comparable),
        "progressing_or_stable": len(ok),
        "share": len(ok) / len(comparable),
    }
    assert cmp.progression_share([{"status": "NOT_COMPARABLE"}]) == {
        "comparable": 0, "progressing_or_stable": 0, "share": None,
    }


def test_evidencia_do_plato_pronta_no_item_do_lote():
    rows = _rows(sessao(1, [10, 10]), sessao(2, [10, 10]), sessao(3, [11, 10]))
    (item,), _ = svc.classify_batch(rows, per_exercise_limit=VERDICT_LOOKBACK_EXPOSURES)
    run = item["_comparable_run"]
    span = item["_run_span"]
    assert len(run) >= 2
    assert span["first"] is run[0] and span["last"] is run[-1]
    assert span["paired"] == svc.paired_top_reps(run[0], run[-1])
    assert span["e1rm_missing"] is any(e.get("e1rm") is None for e in run)
    assert span["basis"] == svc.RUN_SPAN_BASIS
    assert svc.run_span([]) is None


def _grupo(period, best, *, eq=50, gym=None, tipo=None, ex=22):
    return {
        "period": period, "exercise_id": ex, "exercise_name": "Hack", "equipment_id": eq,
        "load_convention": "total", "gym_id": gym, "equipment_type_code": tipo,
        "muscle_group_id": 4, "stimulus_weight": 1.0, "best_e1rm_kg": best,
    }


def test_variacao_por_grupo_mesma_formula_e_limiar():
    # Mesmo caso de test_muscle_group_performance (160 -> 180 = +12,5%).
    (item,) = cmp.group_e1rm_changes([_grupo("previous", 160.0), _grupo("current", 180.0)])
    assert item["change_pct"] == 12.5 and item["outlier"] is False and item["excluded_reason"] is None
    assert (item["previous_e1rm_kg"], item["current_e1rm_kg"], item["muscle_group_id"]) == (160.0, 180.0, 4)
    # Estritamente maior que o limiar: +50% exatos conta; acima e atipico.
    assert reg.GROUP_E1RM_OUTLIER_CHANGE_PCT == 50.0
    (meio,) = cmp.group_e1rm_changes([_grupo("previous", 100.0), _grupo("current", 150.0)])
    assert meio["outlier"] is False
    (fora,) = cmp.group_e1rm_changes([_grupo("previous", 100.0), _grupo("current", 151.0)])
    assert fora["outlier"] is True and fora["excluded_reason"] == cmp.GROUP_E1RM_OUTLIER_REASON


def test_variacao_por_grupo_maquina_separa_academia_peso_livre_nao():
    maquina = [_grupo("previous", 100.0, gym=1, tipo="MACHINE"), _grupo("current", 110.0, gym=2, tipo="MACHINE")]
    assert cmp.group_e1rm_changes(maquina) == []
    livre = [_grupo("previous", 100.0, gym=1, tipo="FREE_WEIGHT"), _grupo("current", 110.0, gym=2, tipo="FREE_WEIGHT")]
    (item,) = cmp.group_e1rm_changes(livre)
    assert item["change_pct"] == 10.0 and item["gym_id"] is None


def test_media_de_rpe_do_periodo_arredonda_como_a_tela():
    # JS `Math.round(72.5) / 10` = 7.3 (o `round` do Python daria 7.2).
    assert svc.average_rpe([{"avg_rpe": 7.5}, {"avg_rpe": 7.0}, {"avg_rpe": None}]) == 7.3
    assert svc.average_rpe([{"avg_rpe": 8.0}, {"avg_rpe": 9.0}]) == 8.5
    assert svc.average_rpe([{"avg_rpe": None}]) is None


def test_limiar_de_rpe_alto_no_registro():
    assert reg.HIGH_RPE_MIN == svc.HIGH_RPE_MIN == analytics._high_rpe_min() == 9


def _resumo(series, *, fadiga=None, nao_reps=False):
    from dataclasses import replace

    sets = tuple(cmp.WorkingSet(reps=r, weight_kg=40.0) for r in series)
    resumo = cmp.ExposureSummary(
        key=cmp.build_key(EX, 50, "total", fatigue_context=fadiga),
        sets=sets,
        total_reps=sum(series),
        best_load_kg=40.0,
        avg_rpe=None,
        e1rm=None,
        e1rm_reps=None,
        prescribed_reps_min=None,
        prescribed_reps_max=None,
    )
    return replace(resumo, has_non_rep_sets=nao_reps)


def test_serie_nao_reps_so_na_exposicao_pulada_nao_bloqueia_o_par():
    """A imediatamente anterior e de outro contexto de fadiga (pulada) e tem
    serie por tempo; o par real (mesmo contexto) e todo em reps: compara."""
    anterior = _resumo([10, 10, 10])
    pulada = _resumo([10], fadiga=PriorFatigueBand.PRE_FATIGUED, nao_reps=True)
    atual = _resumo([12, 12, 12])
    status, reasons = cmp.classify_exercise_progression([anterior, pulada, atual])
    assert status is S.PROGRESSING
    assert cmp.REASON_MEASURE_NOT_REP_BASED not in reasons
    # Na anterior COMPARADA continua bloqueando.
    status, reasons = cmp.classify_exercise_progression([_resumo([10, 10, 10], nao_reps=True), pulada, atual])
    assert status is S.NOT_COMPARABLE and reasons == [cmp.REASON_MEASURE_NOT_REP_BASED]

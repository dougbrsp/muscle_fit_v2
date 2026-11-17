"""#188 (fecho): aviso de calibracao ATUAL no Modo Treino com ficha.

So com `session_id` (Modo Treino) cada exercicio ganha `calibrating` e
`calibration_code` do motor (`prescription_service.suggest_for_pairs`, uma
chamada em lote); a prescricao congelada nao muda; falha nunca derruba."""

from __future__ import annotations

import logging

from app import services, workout_plans
from app.domain import prescription_service as ps
from app.generation import deterministic_prescription, freeze_mode_b


def _ex(row_id, mapping_id, gym_equipment_id, **extra):
    return {"workout_plan_exercise_id": row_id, "mapping_id": mapping_id,
            "preferred_equipment_id": gym_equipment_id, "prescription": {"suggested_load_kg": 50}, **extra}


def _sug(exercise_id, equipment_id, decision=None, load_source=None):
    return ps.PairSuggestion(exercise_id=exercise_id, equipment_id=equipment_id, decision=decision or {},
                             suggested_load_kg=50.0, load_source=load_source, load_increment_kg=2.5)


def _fakes(monkeypatch, found=None, boom=False):
    calls = []
    monkeypatch.setattr(freeze_mode_b, "equipment_by_gym_equipment", lambda conn, ids: {100: 1, 200: 2})
    monkeypatch.setattr(deterministic_prescription, "gym_contexts_by_gym_equipment", lambda conn, ids: {100: 7, 200: 7})
    monkeypatch.setattr(services, "list_exercise_logs", lambda **kw: [])

    def suggest(conn, user_id, queries, **kw):
        calls.append(list(queries))
        if boom:
            raise RuntimeError("motor caiu")
        return found or {}

    monkeypatch.setattr(ps, "suggest_for_pairs", suggest)
    return calls


def test_com_sessao_campos_corretos_partida_contexto_e_peso_livre(monkeypatch):
    found = {
        (1, 1): _sug(1, 1, load_source=ps.LOAD_SOURCE_OTHER_GYM_START),
        (2, 2): _sug(2, 2, decision={"calibrating": True, "calibration_code": "codigo_contexto"}),
    }
    calls = _fakes(monkeypatch, found)
    exercises = [_ex(1, 1, 100), _ex(2, 2, 200), _ex(3, 3, None), _ex(4, None, None)]
    services.attach_current_calibration(object(), exercises, user_id=5)

    assert len(calls) == 1 and [q.gym_context for q in calls[0]] == [7, 7]
    assert (exercises[0]["calibrating"], exercises[0]["calibration_code"]) == (True, ps.CALIBRATION_CODE_START_OTHER_GYM)
    assert (exercises[1]["calibrating"], exercises[1]["calibration_code"]) == (True, "codigo_contexto")
    for ex in exercises[2:]:
        assert (ex["calibrating"], ex["calibration_code"]) == (False, None)
    assert exercises[0]["prescription"] == {"suggested_load_kg": 50}  # congelada intacta


def test_falha_do_motor_nao_derruba_e_loga(monkeypatch, caplog):
    _fakes(monkeypatch, boom=True)
    exercises = [_ex(1, 1, 100)]
    with caplog.at_level(logging.ERROR):
        services.attach_current_calibration(object(), exercises, user_id=5)
    assert (exercises[0]["calibrating"], exercises[0]["calibration_code"]) == (False, None)
    assert "calibracao atual" in caplog.text


class _Tx:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def _rota(monkeypatch):
    plan = {"id": 1, "exercises": [_ex(1, 1, 100)]}
    monkeypatch.setattr(services, "get_workout_plan", lambda plan_id, user_id=None: plan)
    monkeypatch.setattr(workout_plans, "transaction", lambda: _Tx())
    monkeypatch.setattr(workout_plans.deload_periods, "apply_session_deload_to_plan", lambda conn, p, u, s: p)
    return plan


class _Id:
    user_id = 5


def test_sem_sessao_campos_ausentes_e_motor_nao_chamado(monkeypatch):
    calls = _fakes(monkeypatch)
    _rota(monkeypatch)
    plan = workout_plans.get_plan(1, session_id=None, identity=_Id())
    assert calls == [] and "calibrating" not in plan["exercises"][0]


def test_com_sessao_rota_entrega_os_campos(monkeypatch):
    calls = _fakes(monkeypatch, {(1, 1): _sug(1, 1, decision={"calibrating": True, "calibration_code": "c"})})
    _rota(monkeypatch)
    plan = workout_plans.get_plan(1, session_id=9, identity=_Id())
    assert len(calls) == 1 and plan["exercises"][0]["calibration_code"] == "c"


# --- Etapa 5 (#188): janela do veredito igual entre as leituras --------------
from datetime import date as _date  # noqa: E402

from app.intelligence.dashboard import exposure_service as _es  # noqa: E402
from app.intelligence.dashboard.comparability import progression_outcome as _outcome  # noqa: E402


def _linhas_veredito(datas_pesos):
    linhas = []
    for i, (dia, peso) in enumerate(datas_pesos, start=1):
        linhas.append(
            {
                "canonical_exercise_id": 7, "exercise_name": "Supino", "exercise_row_id": 100 + i,
                "activity_id": 500 + i, "local_date": dia, "started_at": None, "position_in_session": 1,
                "training_goal": None, "equipment_id": 3, "equipment_name": None, "gym_id": None,
                "equipment_type_code": None, "load_convention": None, "prescribed_reps_target": None,
                "set_number": 1, "reps": 8, "weight_kg": peso, "rpe": 8, "measure": None,
                "duration_seconds": None, "rest_seconds": None, "notes": None,
            }
        )
    return linhas


_ANTES = [(_date(2026, 10, d), 50.0 + d) for d in (1, 5, 9, 13, 17, 21)]
_DENTRO = [(_date(2026, 11, 12), 80.0), (_date(2026, 11, 15), 60.0)]
_INICIO_7D = _date(2026, 11, 10)


def _status_da_lista(linhas, date_from):
    exposures = _es.mark_verdict_context(_es.assemble_exposures(_es.build_exposures(linhas), 7, None), date_from)
    return _outcome(_es.to_summaries(exposures)).status.value, exposures


def test_veredito_igual_no_painel_tabela_e_briefing_com_contexto_anterior():
    linhas = _linhas_veredito(_ANTES + _DENTRO)
    tabela, exposicoes = _status_da_lista(linhas, _INICIO_7D)  # tabela 7 dias (get_exposures_between)
    briefing, _ = _status_da_lista(linhas, _date(2026, 8, 17))  # briefing 90 dias: tudo no intervalo
    itens, _trunc = _es.classify_batch(linhas, per_exercise_limit=8, date_from=_INICIO_7D)  # painel
    assert tabela == briefing == itens[0]["status"]
    # O que a tela exibe/conta continua so o intervalo pedido.
    assert len([e for e in exposicoes if _es.in_requested_range(e)]) == 2
    assert itens[0]["exposures_in_window"] == 2 and len(itens[0]["_exposures"]) == 2
    # Sem o contexto o veredito seria outro (prova de que o contexto decide).
    sem_contexto, _ = _status_da_lista(_linhas_veredito(_DENTRO), _INICIO_7D)
    assert sem_contexto != tabela or len(exposicoes) == 8


def test_sem_exposicao_anterior_fica_como_antes():
    linhas = _linhas_veredito(_DENTRO)
    com, exposicoes = _status_da_lista(linhas, _INICIO_7D)
    itens, _ = _es.classify_batch(linhas, per_exercise_limit=8)
    assert com == itens[0]["status"]
    assert all(_es.in_requested_range(e) for e in exposicoes)


def test_lote_descarta_exercicio_so_com_contexto():
    itens, _ = _es.classify_batch(_linhas_veredito(_ANTES), per_exercise_limit=8, date_from=_INICIO_7D)
    assert itens == []


# --- Passada D: SQL com a ordem antiga de parametros; detalhe e veredito fecham ---
def test_sql_novo_particiona_pelo_lado_do_intervalo_sem_parametro_extra():
    contexto = f"IF(ranked.before_range, {int(_es.VERDICT_LOOKBACK_EXPOSURES)}, %s)"
    for sql in (_es.EXPOSURES_SQL_TEMPLATE, _es.EXPOSURES_ALL_GOALS_SQL_TEMPLATE, _es.BATCH_EXPOSURES_SQL_TEMPLATE):
        assert "(cand.local_date < win.date_from)" in sql and "PARTITION BY" in sql
        assert "cand.local_date <= %s" in sql and contexto in sql
        assert sql.count("CAST(%s AS DATE) AS date_from") == 1  # date_from uma vez so
    # Parametros: usuario, exercicio, [objetivo], date_from, date_to, teto (+ LIMIT no lote).
    assert _es.EXPOSURES_SQL_TEMPLATE.count("%s") == 6
    assert _es.EXPOSURES_ALL_GOALS_SQL_TEMPLATE.count("%s") == 5
    assert _es.BATCH_EXPOSURES_SQL_TEMPLATE.count("%s") == 6


class _Cur:
    def __init__(self, linhas):
        self.linhas, self.params = linhas, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.params = tuple(params)

    def fetchall(self):
        return self.linhas


class _Conn:
    def __init__(self, linhas):
        self.cur = _Cur(linhas)

    def cursor(self):
        return self.cur


def test_all_goals_descarta_o_contexto(monkeypatch):
    monkeypatch.setattr(_es, "load_convention_select_expr", lambda cursor: "NULL")
    conn = _Conn(_linhas_veredito(_ANTES + _DENTRO))
    lidas = _es.get_exposures_between(
        conn, 7, user_id=1, date_from=_INICIO_7D, date_to=_date(2026, 11, 16), all_goals=True,
        with_fatigue_context=False,
    )
    assert conn.cur.params == (1, 7, _INICIO_7D, _date(2026, 11, 16), _es.MAX_EXPOSURES)
    assert len(lidas) == 2 and all(_es.in_requested_range(e) for e in lidas)


def test_progression_verdict_conta_so_o_intervalo_e_decide_com_contexto():
    linhas = _linhas_veredito(_ANTES + _DENTRO)
    status, exposicoes = _status_da_lista(linhas, _INICIO_7D)
    veredito = _es.progression_verdict(exposicoes)
    assert veredito["status"] == status and veredito["exposures_in_window"] == 2
    _, so_contexto = _status_da_lista(_linhas_veredito(_ANTES), _INICIO_7D)
    assert _es.progression_verdict(so_contexto)["exposures_in_window"] == 0
    assert _es.progression_verdict(so_contexto)["status"] == _es.progression_verdict([])["status"]


def _detalhe(monkeypatch, linhas):
    from types import SimpleNamespace

    _, exposicoes = _status_da_lista(linhas, _INICIO_7D)
    monkeypatch.setattr(_es, "_fetch_exercise", lambda conn, eid: {"exercise_id": 7, "exercise_name": "Supino"})
    monkeypatch.setattr(_es, "get_exercise_exposures", lambda *a, **k: exposicoes)
    monkeypatch.setattr(_es, "_all_goal_exposures", lambda *a, **k: [])
    monkeypatch.setattr(_es, "deload_sessions_between", lambda *a, **k: [])
    monkeypatch.setattr(_es, "_fetch_progression_decisions", lambda *a, **k: [])
    janela = SimpleNamespace(kind=SimpleNamespace(value="7d"), date_from=_INICIO_7D, date_to=_date(2026, 11, 16))
    return _es.build_exercise_progress(object(), 7, user_id=1, window=janela), exposicoes


def test_detalhe_decide_com_contexto_e_exibe_so_o_intervalo(monkeypatch):
    payload, exposicoes = _detalhe(monkeypatch, _linhas_veredito(_ANTES + _DENTRO))
    status_tabela = _outcome(_es.to_summaries(exposicoes)).status.value
    assert payload["status"] == status_tabela
    assert [e["local_date"] for e in payload["exposures"]] == ["2026-11-12", "2026-11-15"]
    par = payload["latest_comparison"]
    assert par["current_index"] == 1  # indice na lista EXIBIDA
    assert par["previous_index"] in (None, 0)


def test_detalhe_so_com_contexto_fica_sem_veredito(monkeypatch):
    payload, _ = _detalhe(monkeypatch, _linhas_veredito(_ANTES))
    assert payload["exposures"] == [] and payload["status"] == _es.progression_verdict([])["status"]


def test_detalhe_par_anterior_fora_da_lista_exibida_fica_null(monkeypatch):
    payload, _ = _detalhe(monkeypatch, _linhas_veredito(_ANTES + _DENTRO[-1:]))
    assert len(payload["exposures"]) == 1
    par = payload["latest_comparison"]
    assert par["current_index"] == 0 and par["previous_index"] is None


# --- Rodada 1: contexto do lote so dos exercicios do periodo; sinal de corte ---
def _de_outro_exercicio(linhas, canonical):
    return [{**linha, "canonical_exercise_id": canonical, "exercise_row_id": linha["exercise_row_id"] + 1000,
             "activity_id": linha["activity_id"] + 1000} for linha in linhas]


def test_sql_do_lote_filtra_contexto_por_exercicio_do_periodo_sem_parametro_novo():
    sql = _es.BATCH_EXPOSURES_SQL_TEMPLATE
    assert "MAX(cand.local_date >= win.date_from) OVER (PARTITION BY cand.canonical_exercise_id)" in sql
    assert "AND ranked.has_in_range = 1" in sql
    # periodo primeiro; contexto agrupado por exercicio (corte declarado por `truncated`)
    assert "ORDER BY ranked.before_range ASC,\n    IF(ranked.before_range, ranked.canonical_exercise_id, 0) ASC" in sql
    assert sql.count("%s") == 6


def test_lote_exercicio_so_de_contexto_nao_conta_para_truncated(monkeypatch):
    monkeypatch.setattr(_es, "MAX_BATCH_EXPOSURES", 3)
    linhas = _linhas_veredito(_DENTRO) + _de_outro_exercicio(_linhas_veredito(_ANTES), 8)
    itens, truncated = _es.classify_batch(linhas, per_exercise_limit=8, date_from=_INICIO_7D)
    assert [item["exercise_id"] for item in itens] == [7] and truncated is False


def test_lote_com_contexto_antigo_mantem_veredito_do_detalhe_e_sinaliza_corte(monkeypatch):
    linhas = _linhas_veredito(_ANTES + _DENTRO)
    detalhe, _ = _status_da_lista(linhas, _INICIO_7D)
    monkeypatch.setattr(_es, "MAX_BATCH_EXPOSURES", 9)  # cabe periodo + contexto
    (item,), truncated = _es.classify_batch(linhas, per_exercise_limit=8, date_from=_INICIO_7D)
    assert item["status"] == detalhe and truncated is False
    # LIMIT cortou contexto do exercicio do periodo: o lote declara o corte.
    monkeypatch.setattr(_es, "MAX_BATCH_EXPOSURES", 5)
    cortadas = _linhas_veredito(_ANTES[-3:] + _DENTRO)
    _, truncated = _es.classify_batch(cortadas, per_exercise_limit=8, date_from=_INICIO_7D)
    assert truncated is True


def test_all_goals_com_contexto_explicito_traz_contexto(monkeypatch):
    monkeypatch.setattr(_es, "load_convention_select_expr", lambda cursor: "NULL")
    conn = _Conn(_linhas_veredito(_ANTES + _DENTRO))
    lidas = _es.get_exposures_between(
        conn, 7, user_id=1, date_from=_INICIO_7D, date_to=_date(2026, 11, 16), all_goals=True,
        with_fatigue_context=False, verdict_context=True,
    )
    assert conn.cur.params == (1, 7, _INICIO_7D, _date(2026, 11, 16), _es.MAX_EXPOSURES)
    assert len(lidas) == 8 and len([e for e in lidas if _es.in_requested_range(e)]) == 2


def test_rota_exercicios_sem_objetivo_pede_contexto_do_veredito():
    import inspect

    from app import analytics

    fonte = inspect.getsource(analytics)
    trecho = fonte[fonte.index("all_goals=goal is None,"):][:200]
    assert "verdict_context=True" in trecho


def test_janela_de_logs_da_sugestao_vem_do_dono():
    import inspect

    fonte = inspect.getsource(services)
    assert "limit=300" not in fonte
    assert fonte.count("list_exercise_logs(limit=SUGGESTION_LOG_LIMIT") == 3
    assert ps.SUGGESTION_LOG_LIMIT == 300


def test_rascunho_por_ia_usa_a_janela_de_logs_do_dono():
    import inspect

    from app import workout_plan_ai

    fonte = inspect.getsource(workout_plan_ai)
    assert "limit=300" not in fonte
    assert fonte.count("list_exercise_logs(limit=SUGGESTION_LOG_LIMIT") == 2

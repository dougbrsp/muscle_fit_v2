"""M97 D: deload (mapa §67, dono `app/domain/deload.py`).

Sem MySQL. Prova que:

- o dono tem os números (60%, RPE 6, piso da faixa por objetivo, ~1/3 das
  séries, mínimo 1) e as regras do período (default, teto, sobreposição);
- a progressão PULA a sessão de deload (hipertrofia e força): nem avança, nem
  regride, nem conta para platô - o estado e as decisões das outras sessões
  são os mesmos do histórico sem ela; no `finish` a avaliação sai neutra;
- sem deload, o replay é o de sempre (fingerprint da F1/F2);
- comparabilidade/exposições, e1RM, recorde e sessão anterior do Modo Treino
  excluem a sessão de deload pelo predicado do dono; o sinal "Desempenho
  recente" a pula;
- "volume abaixo da meta" e regressão ficam em silêncio no deload;
- `start`: período ativo (fuso do usuário) carimba `period`, marca avulsa
  `manual`, sem nada o INSERT é o de sempre; período sobreposto -> 409;
- próxima série numa sessão de deload vem do dono (60% da trilha).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import deload_periods, workout_sessions
from app.domain import deload as dl
from app.domain import effective_intensity as ei
from app.domain import next_set_load as nsl
from app.domain import prescription_defaults as pd
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain import training_goal as tg
from app.domain import training_history as th
from app.engine_decision_context import PairEngineContext
from app.intelligence import exercise_performance as perf
from app.intelligence.dashboard import exposure_service, insight_rules as ir, session_records
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus
from app.security import Identity
from tests.support import progression_fixtures as fx
from tests.support import progression_parity_fixture as parity
from tests.support.fake_db import NullContext, fake_transaction
from tests.unit import test_dashboard_insight_rules as base_ir

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
IDENTITY = Identity(user_id=1, role="regular", source="session")


# ---------------------------------------------------------------------------
# Dono
# ---------------------------------------------------------------------------


def test_constantes_do_dono():
    assert dl.DELOAD_LOAD_FACTOR == Decimal("0.60")
    assert dl.DELOAD_TARGET_RPE == 6
    assert dl.DELOAD_SETS_REDUCTION_FRACTION == Fraction(1, 3)
    assert dl.DELOAD_MIN_SETS == 1
    assert dl.DELOAD_SOURCE_VALUES == ("period", "manual")


@pytest.mark.parametrize(("sets", "esperado"), [(1, 1), (2, 1), (3, 2), (4, 3), (5, 3), (6, 4), (0, None), (None, None)])
def test_series_reduzidas_em_um_terco_com_minimo(sets, esperado):
    assert dl.deload_sets(sets) == esperado


def test_reps_sao_o_piso_da_faixa_do_item_ou_do_objetivo():
    assert dl.deload_reps("10-12", H) == 10
    assert dl.deload_reps("3-5", S) == 3
    # sem faixa no item: o piso do default do objetivo (dono prescription_defaults)
    assert dl.deload_reps(None, H) == th.parse_reps_range(pd.defaults_for(H).reps_target)[0]
    assert dl.deload_reps(None, S) == th.parse_reps_range(pd.defaults_for(S).reps_target)[0] == pe.STRENGTH_REPS[0]


def test_carga_e_60_por_cento_da_trilha_no_passo_do_equipamento():
    assert dl.deload_load_kg(50, 2.5) == 30.0
    assert dl.deload_load_kg(47.5, 2.5) == 27.5  # 28,5 -> passo de 2,5 (meio para cima)
    assert dl.deload_load_kg(None, 2.5) is None
    assert dl.deload_load_kg(0, 2.5) is None


def test_prescricao_de_deload_por_objetivo():
    hip = dl.deload_prescription(working_load_kg=60, sets_count=4, reps_target="8-12", training_goal=None, step_kg=2.5)
    assert hip.to_dict() == {
        "training_goal": H,
        "working_load_kg": 60.0,
        "load_kg": 35.0,  # 36 -> 35 no passo de 2,5
        "load_factor": 0.6,
        "target_rpe": 6,
        "reps_target": 8,
        "sets_count": 3,
    }
    forca = dl.deload_prescription(working_load_kg=100, sets_count=3, reps_target=None, training_goal=S, step_kg=5)
    assert (forca.load_kg, forca.reps_target, forca.sets_count, forca.training_goal) == (60.0, 3, 2, S)


def test_precedencia_das_fontes_do_item_e_alvo_de_esforco_no_dono():
    snapshot = {"suggested_load_kg": 40, "load_target": 45, "sets_count": 4, "reps_target": "10-12"}
    ficha = {"load_target": 50, "sets_count": 6, "reps_target": "6-8", "training_goal": S}
    assert dl.item_deload_prescription(track_working_load_kg=60, snapshot=snapshot, plan_row=ficha).working_load_kg == 60
    sem_trilha = dl.item_deload_prescription(track_working_load_kg=None, snapshot=snapshot, plan_row=ficha)
    assert (sem_trilha.working_load_kg, sem_trilha.sets_count, sem_trilha.reps_target) == (40.0, 3, 10)
    so_ficha = dl.item_deload_prescription(track_working_load_kg=None, snapshot={}, plan_row=ficha)
    assert (so_ficha.working_load_kg, so_ficha.sets_count, so_ficha.reps_target, so_ficha.training_goal) == (50.0, 4, 6, S)
    assert dl.deload_effort_target(None) == dl.DELOAD_TARGET_RPE
    # Fix 2026-10-26: deload sem técnica (`DELOAD_ALLOWED_TECHNIQUES` vazio):
    # o alvo do protocolo da técnica não vale mais na sessão de deload.
    assert dl.deload_effort_target(9) == dl.DELOAD_TARGET_RPE
    assert dl.deload_effort_target(9, "rest_pause") == dl.DELOAD_TARGET_RPE


def test_proxima_serie_em_deload():
    prescricao = dl.deload_prescription(working_load_kg=50, sets_count=3, reps_target="10-12", step_kg=2.5)
    acima = dl.deload_next_set_load(40, prescricao, 2.5)
    assert (acima.direction, acima.load_kg, acima.reason) == (pe.DECREASE_LOAD, 30.0, nsl.REASON_DELOAD)
    abaixo = dl.deload_next_set_load(25, prescricao, 2.5)
    assert (abaixo.direction, abaixo.load_kg) == (pe.HOLD, 25.0)
    assert abaixo.reason_label == nsl.reason_labels()[nsl.REASON_DELOAD] == dl.deload_reason_label()
    assert "60%" in abaixo.reason_label and str(dl.DELOAD_TARGET_RPE) in abaixo.reason_label
    assert dl.deload_next_set_load(None, prescricao, 2.5) is None


def test_origem_da_sessao_periodo_vence_a_marca_avulsa():
    assert dl.session_deload_source(manual=False, period_active=False) is None
    assert dl.session_deload_source(manual=True, period_active=False) == "manual"
    assert dl.session_deload_source(manual=True, period_active=True) == "period"
    assert dl.session_deload_source(manual=False, period_active=True) == "period"
    assert dl.is_deload_session("manual") and dl.is_deload_session("period")
    assert not dl.is_deload_session(None) and not dl.is_deload_session("lixo")


def test_validacao_do_periodo():
    hoje = _dt.date(2026, 10, 25)
    assert dl.validate_period(hoje, None, today=hoje) == (hoje, hoje + _dt.timedelta(days=dl.DELOAD_PERIOD_DEFAULT_DAYS - 1))
    with pytest.raises(dl.InvalidDeloadPeriod):
        dl.validate_period(hoje, hoje - _dt.timedelta(days=1), today=hoje)
    with pytest.raises(dl.InvalidDeloadPeriod):
        dl.validate_period(hoje, hoje + _dt.timedelta(days=dl.DELOAD_PERIOD_MAX_DAYS), today=hoje)
    with pytest.raises(dl.InvalidDeloadPeriod):
        dl.validate_period(hoje - _dt.timedelta(days=10), hoje - _dt.timedelta(days=4), today=hoje)


def test_intervalos_e_supressao():
    d = _dt.date
    ranges = dl.deload_ranges([(d(2026, 10, 20), d(2026, 10, 26))], [d(2026, 10, 1), d(2026, 10, 1)])
    assert ranges == ((d(2026, 10, 1), d(2026, 10, 1)), (d(2026, 10, 20), d(2026, 10, 26)))
    assert dl.active_on(ranges, d(2026, 10, 22)) and not dl.active_on(ranges, d(2026, 10, 27))
    assert dl.suppresses_below_min_volume(ranges, d(2026, 10, 14), d(2026, 10, 21))
    assert not dl.suppresses_below_min_volume(ranges, d(2026, 10, 2), d(2026, 10, 19))
    assert dl.suppresses_regression(ranges, d(2026, 10, 26))
    assert not dl.suppresses_regression(ranges, d(2026, 10, 27))


def test_predicados_sql():
    assert dl.sql_not_deload("a") == "a.deload_source IS NULL"
    assert dl.sql_is_deload("a2") == "a2.deload_source IS NOT NULL"
    assert dl.sql_period_overlaps("dp") == "dp.cancelled_at IS NULL AND dp.starts_on <= %s AND dp.ends_on >= %s"
    with pytest.raises(ValueError):
        dl.sql_not_deload("a; DROP")


# ---------------------------------------------------------------------------
# Progressão: a sessão de deload é PULADA (hipertrofia e força)
# ---------------------------------------------------------------------------


def _deload_copy(session, activity_id, day, *, source="period"):
    """A MESMA sessão em 60% da carga, RPE 6 e reps no piso: contada, seria regressão."""
    execucoes = tuple(
        replace(
            item,
            exercise_row_id=item.exercise_row_id + 70_000,
            sets=tuple(
                replace(s, weight_kg=(s.weight_kg or Decimal(0)) * Decimal("0.6"), rpe=Decimal(6), reps=6)
                for s in item.sets[:2]
            ),
        )
        for item in session.exercises
    )
    moment = _dt.datetime.combine(day, _dt.datetime.min.time()) + _dt.timedelta(hours=7)
    return replace(
        session, activity_id=activity_id, local_date=day, started_at=moment, ended_at=moment,
        exercises=execucoes, deload_source=source,
    )


def _com_deload(sessions):
    """Insere duas sessões de deload (uma de período, uma avulsa) no meio."""
    out = list(sessions)
    out.insert(4, _deload_copy(sessions[3], 99_001, sessions[3].local_date, source="period"))
    out.insert(9, _deload_copy(sessions[7], 99_002, sessions[7].local_date, source="manual"))
    return out


def test_replay_pula_o_deload_e_fica_identico_ao_historico_sem_ele(monkeypatch):
    # 12 sessões (< janela de 21): a janela vê o histórico inteiro dos dois lados.
    curto = parity.history()[:12]
    referencia = parity.run(monkeypatch, sessions=curto)
    com_deload = parity.run(monkeypatch, sessions=_com_deload(curto))
    assert com_deload["fingerprint"] == referencia["fingerprint"]
    assert com_deload["decisions_md5"] == referencia["decisions_md5"]
    assert not {99_001, 99_002} & {item["activity_id"] for item in com_deload["store"].decisions}

    # controle: as MESMAS sessões sem a marca de deload mudariam o estado
    sem_marca = parity.run(monkeypatch, sessions=[replace(item, deload_source=None) for item in _com_deload(curto)])
    assert sem_marca["decisions_md5"] != referencia["decisions_md5"]
    assert {99_001, 99_002} <= {item["activity_id"] for item in sem_marca["store"].decisions}


def _forca(sessions):
    return [
        replace(item, exercises=tuple(replace(ex, training_goal=S) for ex in item.exercises)) for item in sessions
    ]


def test_forca_tambem_pula_o_deload_inclusive_o_plato_de_forca():
    sessions = _forca(parity.history()[:12])
    inputs = fx.inputs(tracks=parity.TRACKS)
    referencia = ev.evaluate_history_by_goal(sessions, inputs=inputs).results[S]
    com_deload = ev.evaluate_history_by_goal(_com_deload(sessions), inputs=inputs).results[S]
    assert com_deload.tracks == referencia.tracks
    assert com_deload.group_states == referencia.group_states
    assert [d.to_dict() for d in com_deload.decisions] == [d.to_dict() for d in referencia.decisions]
    controle = ev.evaluate_history_by_goal(
        [replace(item, deload_source=None) for item in _com_deload(sessions)], inputs=inputs
    ).results[S]
    assert [d.to_dict() for d in controle.decisions] != [d.to_dict() for d in referencia.decisions]


def test_finish_da_sessao_de_deload_e_neutro():
    sessions = _com_deload(parity.history()[:12])
    deload = sessions[4]
    assert deload.is_deload
    result = ev.evaluate_session_by_goal(
        deload, sessions[:5], group_states={}, tracks={}, inputs=fx.inputs(tracks=parity.TRACKS)
    )
    assert result.results == {} and result.decisions == ()
    assert result.skipped_reason == dl.SKIP_REASON_DELOAD
    normal = ev.evaluate_session_by_goal(
        sessions[3], sessions[:4], group_states={}, tracks={}, inputs=fx.inputs(tracks=parity.TRACKS)
    )
    assert normal.skipped_reason is None and normal.results


def test_sessao_de_deload_lida_do_banco():
    row = {
        "activity_id": 1, "local_date": _dt.date(2026, 10, 1), "deload_source": "manual",
        "exercise_row_id": 10, "sequence": 1, "exercise_id": 5, "equipment_id": 7,
    }
    (session,) = th.build_sessions([row])
    assert session.deload_source == "manual" and session.is_deload
    assert session.progression_view(H).exercises == ()
    (normal,) = th.build_sessions([{**row, "deload_source": None}])
    assert not normal.is_deload and normal.progression_view(H) is normal
    assert "a.deload_source" in th.SESSIONS_SQL and "s.deload_source" in th.SESSIONS_SQL


# ---------------------------------------------------------------------------
# Comparabilidade, e1RM, recorde, sessão anterior: fora
# ---------------------------------------------------------------------------


def test_exposicoes_recorde_e_e1rm_excluem_o_deload_no_sql():
    assert dl.sql_not_deload("a2") in exposure_service.EXPOSURES_SQL_TEMPLATE
    assert dl.sql_not_deload("a2") in exposure_service.BATCH_EXPOSURES_SQL_TEMPLATE
    assert dl.sql_not_deload("a") in session_records.HISTORY_SQL_TEMPLATE


def test_sessao_anterior_e_e1rm_do_modo_treino_excluem_o_deload():
    import inspect

    from app import analytics, services

    assert "sql_not_deload('a')" in inspect.getsource(services.get_last_session_sets)
    assert 'sql_not_deload("a")' in inspect.getsource(analytics._fetch_group_e1rm_rows)


def _item(activity_id, day, weight, reps, *, deload=None):
    return {
        "activity_id": activity_id,
        "exercise_id": activity_id * 10,
        "mapping_id": 1,
        "local_date": day,
        "trained_at": _dt.datetime.combine(day, _dt.datetime.min.time()),
        "equipment_id": 7,
        "load_convention": None,
        "deload_source": deload,
        "set_rows": [
            {"set_number": n, "reps": reps, "weight_kg": weight, "rpe": 9, "set_type": "working"} for n in (1, 2, 3)
        ],
    }


def _ratios(items):
    return perf.latest_performance_ratios(
        items, identity=lambda item: item["mapping_id"], sort_key=lambda item: item["trained_at"]
    )


def test_desempenho_recente_pula_a_sessao_de_deload():
    d = _dt.date
    normal = [_item(1, d(2026, 10, 1), 50, 10), _item(2, d(2026, 10, 4), 50, 10)]
    deload = _item(3, d(2026, 10, 7), 30, 6, deload="period")
    assert (30, 3) not in _ratios([*normal, deload])  # a sessão de deload não vira "queda"
    assert _ratios([*normal, deload]) == _ratios(normal)
    # controle: sem a marca, a mesma sessão é queda forte
    assert _ratios([*normal, {**deload, "deload_source": None}])[(30, 3)] < 0.9


# ---------------------------------------------------------------------------
# Insights: silêncio no deload
# ---------------------------------------------------------------------------


def _ranges_na_semana():
    return ((base_ir.DATE_TO - _dt.timedelta(days=3), base_ir.DATE_TO + _dt.timedelta(days=3)),)


def test_volume_abaixo_da_meta_em_silencio_no_deload():
    costas_agora, costas_antes = base_ir._below_both_weeks(6, "Costas")
    outros = [base_ir._on_target_both_weeks(i, f"Grupo {i}") for i in (1, 2, 3)]
    current = [costas_agora, *(a for a, _ in outros)]
    previous = [costas_antes, *(b for _, b in outros)]
    assert ir.rule_muscle_below_min_consecutive_weeks(base_ir._weeks(current, previous))
    assert ir.rule_muscle_below_min_consecutive_weeks(base_ir._weeks(current, previous, deload_ranges=_ranges_na_semana())) == []
    # deload que acabou antes da régua das duas semanas não silencia
    antigo = ((base_ir.DATE_TO - _dt.timedelta(days=40), base_ir.DATE_TO - _dt.timedelta(days=34)),)
    assert ir.rule_muscle_below_min_consecutive_weeks(base_ir._weeks(current, previous, deload_ranges=antigo))
    assert ir.below_min_ruler_start(base_ir.DATE_TO) == base_ir.DATE_TO - _dt.timedelta(days=13)


def test_regressao_em_silencio_durante_o_deload():
    recentes = [base_ir._progression_item(i, ExerciseProgressionStatus.REGRESSING) for i in (1, 2, 3)]
    assert ir.rule_exercises_regressing(base_ir._ctx(progression=recentes))
    assert ir.rule_exercises_regressing(base_ir._ctx(progression=recentes, deload_ranges=_ranges_na_semana())) == []


# ---------------------------------------------------------------------------
# start: período (fuso do usuário) x marca avulsa
# ---------------------------------------------------------------------------


class _StartCursor(NullContext):
    def __init__(self, period=None):
        self.period = period
        self.executed: list[tuple[str, tuple]] = []
        self.lastrowid = 321
        self._pending = None

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        if flat.startswith("SELECT timezone FROM users"):
            self._pending = {"timezone": "America/Sao_Paulo"}
        elif "FROM deload_periods" in flat:
            self._pending = self.period
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


def _start(monkeypatch, payload, period=None):
    cursor = _StartCursor(period)
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "source_id", lambda *a, **k: 7)
    monkeypatch.setattr(workout_sessions, "resolve_gym_id", lambda *a, **k: None)
    out = workout_sessions.start_session(payload, identity=IDENTITY)
    insert = next((sql, params) for sql, params in cursor.executed if sql.startswith("INSERT INTO activities"))
    lookup = next(params for sql, params in cursor.executed if "FROM deload_periods" in sql)
    return out, insert, lookup


def test_start_sem_deload_grava_o_insert_de_sempre(monkeypatch):
    out, (sql, params), _ = _start(monkeypatch, workout_sessions.SessionStartIn(local_date=_dt.date(2026, 10, 25)))
    assert "deload_source" not in sql and len(params) == 12
    assert out["deload"] == {"active": False, "source": None, "period": None}


def test_start_avulso_carimba_manual(monkeypatch):
    out, (sql, params), _ = _start(
        monkeypatch, workout_sessions.SessionStartIn(local_date=_dt.date(2026, 10, 25), deload=True)
    )
    assert "deload_source" in sql and params[-1] == "manual"
    assert out["deload"]["active"] is True and out["deload"]["source"] == "manual"


def test_start_em_periodo_ativo_carimba_period_na_data_da_sessao(monkeypatch):
    periodo = {"id": 4, "starts_on": _dt.date(2026, 10, 20), "ends_on": _dt.date(2026, 10, 26), "cancelled_at": None}
    out, (sql, params), lookup = _start(
        monkeypatch, workout_sessions.SessionStartIn(local_date=_dt.date(2026, 10, 25)), period=periodo
    )
    assert params[-1] == "period"
    assert lookup == (1, _dt.date(2026, 10, 25), _dt.date(2026, 10, 25))
    assert out["deload"]["source"] == "period" and out["deload"]["period"]["id"] == 4


def test_start_sem_data_usa_o_dia_no_fuso_do_usuario(monkeypatch):
    # 01h30 UTC de 26/10 = 22h30 de 25/10 em São Paulo: o período é procurado em 25/10
    from app.intelligence.dashboard import time_windows

    agora = _dt.datetime(2026, 10, 26, 1, 30, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(workout_sessions, "user_now", lambda tz: time_windows.user_now(tz, agora))
    _out, _insert, lookup = _start(monkeypatch, workout_sessions.SessionStartIn())
    assert lookup[1] == _dt.date(2026, 10, 25)


# ---------------------------------------------------------------------------
# /deload-periods: 409 de sobreposição
# ---------------------------------------------------------------------------


class _PeriodCursor(NullContext):
    def __init__(self, overlapping=None):
        self.overlapping = overlapping
        self.executed: list[str] = []
        self.lastrowid = 9
        self._pending = None

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.executed.append(flat)
        if "FROM deload_periods dp" in flat:
            self._pending = self.overlapping
        elif flat.startswith("SELECT id, user_id, starts_on"):
            self._pending = {"id": 9, "user_id": 1, "starts_on": params and None, "ends_on": None,
                             "created_at": None, "cancelled_at": None}
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


def _criar(monkeypatch, payload, overlapping=None):
    cursor = _PeriodCursor(overlapping)
    monkeypatch.setattr(deload_periods, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(deload_periods, "_today", lambda conn, user_id: _dt.date(2026, 10, 25))
    return deload_periods.create_deload_period(payload, identity=IDENTITY), cursor


def test_periodo_sobreposto_responde_409(monkeypatch):
    existente = {"id": 3, "starts_on": _dt.date(2026, 10, 24), "ends_on": _dt.date(2026, 10, 30), "cancelled_at": None}
    with pytest.raises(HTTPException) as erro:
        _criar(monkeypatch, deload_periods.DeloadPeriodIn(starts_on=_dt.date(2026, 10, 26)), overlapping=existente)
    assert erro.value.status_code == 409
    assert erro.value.detail["error"] == "deload_period_overlap"


def test_periodo_novo_trava_o_usuario_e_grava_uma_semana(monkeypatch):
    out, cursor = _criar(monkeypatch, deload_periods.DeloadPeriodIn())
    assert out["status"] == "created"
    assert any(sql.startswith("SELECT id FROM users WHERE id = %s FOR UPDATE") for sql in cursor.executed)
    assert any(sql.startswith("INSERT INTO deload_periods") for sql in cursor.executed)


def test_periodo_invalido_responde_422(monkeypatch):
    with pytest.raises(HTTPException) as erro:
        _criar(
            monkeypatch,
            deload_periods.DeloadPeriodIn(starts_on=_dt.date(2026, 10, 25), ends_on=_dt.date(2026, 10, 20)),
        )
    assert erro.value.status_code == 422


# ---------------------------------------------------------------------------
# Próxima série numa sessão de deload (rota)
# ---------------------------------------------------------------------------


class _SerieCursor(NullContext):
    def __init__(self, row):
        self.row = row
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


def _salvar(monkeypatch, row, payload):
    cursor = _SerieCursor(row)
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "ensure_valid_codes", lambda codes: None)
    monkeypatch.setattr(workout_sessions, "_get_live_session", lambda cur, sid, uid: {"id": sid})
    monkeypatch.setattr(workout_sessions, "_refresh_exercise_aggregates", lambda cur, eid: None)
    monkeypatch.setattr(workout_sessions, "_refresh_activity_avg_rpe", lambda cur, sid: None)
    monkeypatch.setattr(workout_sessions, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        workout_sessions.effective_intensity,
        "resolve_for_logged_exercise",
        lambda conn, eid: ei.EffectiveIntensity(rpe_target_effective=9, ceiling=10, failure_allowed=True),
    )
    monkeypatch.setattr(workout_sessions, "load_pair_step_context", lambda conn, m, g: PairEngineContext(step_kg=Decimal("2.5")))
    return workout_sessions.add_session_set(1, 5, payload, identity=SimpleNamespace(user_id=1)), cursor


_ROW = {
    "id": 5, "plan_technique": None, "plan_reps_target": "10-12", "prescription_json": None,
    "mapping_id": 11, "gym_equipment_id": 2, "working_load_kg": Decimal("50"),
}


def test_proxima_serie_em_deload_vem_do_dono(monkeypatch):
    payload = workout_sessions.SessionSetIn(set_number=1, reps=17, weight_kg=Decimal(40), rpe=Decimal(6))
    resposta, cursor = _salvar(monkeypatch, {**_ROW, "deload_source": "manual"}, payload)
    proxima = resposta["next_set_load"]
    assert (proxima["direction"], proxima["load_kg"], proxima["reason"]) == (pe.DECREASE_LOAD, 30.0, nsl.REASON_DELOAD)
    assert any("FROM progression_tracks t" in sql for sql in cursor.executed)


def test_contexto_do_par_em_deload_decide_a_proxima_serie_tambem_na_ia():
    """`next_set_load_for` (salvamento da série e avaliação por IA) usa o dono
    do deload quando o contexto do par traz a prescrição de deload."""
    from app.engine_decision_context import next_set_load_for

    prescricao = dl.deload_prescription(working_load_kg=50, sets_count=3, reps_target="10-12", step_kg=2.5)
    contexto = PairEngineContext(step_kg=Decimal("2.5"), deload=prescricao)
    serie = {"set_type": "working", "reps": 12, "weight_kg": 40, "rpe": 6}
    proxima = next_set_load_for(serie, contexto)
    assert (proxima.direction, proxima.load_kg, proxima.reason) == (pe.DECREASE_LOAD, 30.0, nsl.REASON_DELOAD)
    assert next_set_load_for({**serie, "set_type": "warmup"}, contexto) is None
    assert next_set_load_for({**serie, "measure": "duration"}, contexto) is None
    assert next_set_load_for(serie, PairEngineContext(step_kg=Decimal("2.5"))).reason != nsl.REASON_DELOAD


def test_sem_deload_a_proxima_serie_e_a_regra_de_sempre(monkeypatch):
    payload = workout_sessions.SessionSetIn(set_number=1, reps=17, weight_kg=Decimal(40), rpe=Decimal(10))
    resposta, cursor = _salvar(monkeypatch, {**_ROW, "deload_source": None}, payload)
    assert resposta["next_set_load"]["reason"] == nsl.REASON_REPS_ABOVE_LIMIT
    assert not any("FROM progression_tracks t" in sql for sql in cursor.executed)

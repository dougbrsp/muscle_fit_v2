"""#196: aprimoramento de tecnica fora da progressao (dono `app/domain/form_practice.py`,
mapa §105, ADR 0017).

Regra: `.claude/CLAUDE.md` "Motores: fonte unica da verdade". Sem MySQL. Prova que:

- PROPRIEDADE CENTRAL: o historico H com execucoes marcadas da o MESMO
  resultado do motor (decisoes, trilhas, grupos, plato, contadores,
  calibracao) que H sem essas execucoes, nos dois avaliadores
  (`evaluate_history_by_goal`, pelo replay de producao, e `evaluate_history`);
  a sessao continua na janela;
- marcada abaixo da referencia nao regride, acima nao avanca; os outros
  exercicios da sessao seguem avaliados; sessao toda marcada e pulada;
- teto de ativacao e fadiga previa CONTINUAM vendo a execucao marcada;
- `form_practice` ausente/None/0 = comportamento de sempre.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain import form_practice as fp
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain import training_goal as tg
from app.domain import training_history as th
from app.domain.equipment_context import CALIBRATION_MIN_COMPARABLE_SESSIONS
from app.domain.session_fatigue import executions_from_session
from tests.support import progression_fixtures as fx
from tests.support import progression_parity_fixture as parity

H = tg.TrainingGoal.HYPERTROPHY.value


# ---------------------------------------------------------------------------
# Dono
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "marcada"),
    [
        (None, False), (0, False), (1, True), (False, False), (True, True), ("0", False), ("1", True),
        (" true ", True), ("lixo", False), (Decimal("0"), False), (Decimal("1"), True), (b"\x01", False),
    ],
)
def test_leitura_tolerante(valor, marcada):
    assert fp.is_form_practice(valor) is marcada
    assert fp.counts_for_progression(valor) is (not marcada)


def test_predicado_sql_e_alias_validado():
    assert fp.FORM_PRACTICE_COLUMN == "form_practice"
    assert fp.sql_counts_for_progression("e") == "e.form_practice = 0"
    assert fp.sql_counts_for_progression("ex_2") == "ex_2.form_practice = 0"
    for ruim in ("e; DROP", "", "1e", "e.x", None):
        with pytest.raises(ValueError):
            fp.sql_counts_for_progression(ruim)


def test_heranca_fragmento_sql_e_alias_validado():
    assert fp.sql_plan_item_marked("e") == "MAX(e.form_practice)"
    for ruim in ("e; DROP", "", "1e", "e.x", None):
        with pytest.raises(ValueError):
            fp.sql_plan_item_marked(ruim)


@pytest.mark.parametrize(
    ("enviado", "herdado", "esperado"),
    [
        (False, None, False),  # treino livre / 1a passagem sem marca
        (True, None, True),  # treino livre: vale o cliente
        (None, 1, True),  # continuacao sem o campo herda (fila offline antiga, relogio)
        (False, "1", True),  # continuacao nao desmarca
        (True, 0, True),
        (None, None, False),  # item sem linha anterior (MAX sem linha = NULL)
        (0, Decimal("0"), False),
    ],
)
def test_heranca_enviado_ou_herdado(enviado, herdado, esperado):
    assert fp.inherit_form_practice(enviado, herdado) is esperado


def test_leitura_neutra_preserva_seguranca_e_zera_veredito():
    from app.domain.effective_intensity import SetIntensityReading

    leitura = SetIntensityReading(
        status="ABOVE_TARGET", safety_event="RPE_ABOVE_CEILING", rpe=10.0, target=8, ceiling=9,
        failure_allowed=False, failure_denied_by="goal", reps=12, reps_min=8, reps_max=10, reps_position="ABOVE",
    )
    neutra = fp.neutral_set_reading(leitura)
    assert (neutra.status, neutra.reps, neutra.reps_min, neutra.reps_max, neutra.reps_position) == (None,) * 5
    assert neutra == replace(
        leitura, status=None, reps=None, reps_min=None, reps_max=None, reps_position=None
    )
    assert (neutra.safety_event, neutra.rpe, neutra.target, neutra.ceiling) == ("RPE_ABOVE_CEILING", 10.0, 8, 9)
    assert (neutra.failure_allowed, neutra.failure_denied_by) == (False, "goal")
    assert leitura.status == "ABOVE_TARGET"  # imutavel: a leitura original nao muda


def test_desfecho_neutro_e_leitura_neutra_sem_proxima_carga():
    from app.domain.effective_intensity import SetIntensityReading

    leitura = SetIntensityReading(
        status="ON_TARGET", safety_event=None, rpe=8.0, target=8, ceiling=9, failure_allowed=True,
        reps=10, reps_min=8, reps_max=12, reps_position="IN_RANGE",
    )
    assert fp.neutral_set_outcome(leitura) == (fp.neutral_set_reading(leitura), None)


def test_contexto_neutro_sem_decisao_deload_nem_conjunto_de_regras():
    from app.engine_decision_context import EngineDecision, PairEngineContext

    deload = object()  # o dono so troca o campo; o tipo nao importa aqui
    original = PairEngineContext(
        step_kg=Decimal("2.5"), decision=EngineDecision(decision=pe.INCREASE_LOAD, load_kg=50.0),
        exercise_row_id=10, load_convention="total", training_goal=H, deload=deload,
        max_load_kg=Decimal("80"), form_practice_marked=True,
    )
    neutro, ruleset = fp.neutral_engine_context(original)
    assert ruleset is None
    assert (neutro.decision, neutro.deload) == (None, None)
    assert neutro == replace(original, decision=None, deload=None)
    assert original.decision is not None and original.deload is deload  # original intacto


def test_saida_da_ia_neutra_sem_carga_reps_nem_aumento():
    from app.domain.set_evaluation_categories import HOLD, INCREASE_LOAD, category_label

    entrada = {
        "category": INCREASE_LOAD, "category_label": category_label(INCREASE_LOAD), "note": "Boa execucao.",
        "suggested_next_load_kg": 50.0, "suggested_next_reps": 10, "suggested_next_rpe": 8,
        "clamps": [{"field": "rpe"}],
        "load_adjustments": [{"field": "load_kg", "from": 51.0, "to": 50.0, "reason": "equipment_step"}],
    }
    saida = fp.neutral_ai_suggestion(entrada)
    assert (saida["suggested_next_load_kg"], saida["suggested_next_reps"]) == (None, None)
    assert (saida["category"], saida["category_label"]) == (HOLD, category_label(HOLD))
    assert saida["category_from_ai"] == INCREASE_LOAD
    assert {k: saida[k] for k in ("note", "suggested_next_rpe", "clamps", "load_adjustments")} == {
        "note": "Boa execucao.", "suggested_next_rpe": 8, "clamps": [{"field": "rpe"}], "load_adjustments": [],
    }
    assert entrada["category"] == INCREASE_LOAD and entrada["suggested_next_load_kg"] == 50.0  # copia
    assert fp.neutral_ai_suggestion(saida) == saida  # idempotente


def test_saida_da_ia_ja_neutra_fica_igual():
    from app.domain.set_evaluation_categories import HOLD, category_label

    neutra = {
        "category": HOLD, "category_label": category_label(HOLD), "note": "ok",
        "suggested_next_load_kg": None, "suggested_next_reps": None, "load_adjustments": [],
    }
    assert fp.neutral_ai_suggestion(neutra) == neutra


# ---------------------------------------------------------------------------
# Historico: coluna, campo e visoes
# ---------------------------------------------------------------------------

_ROW = {
    "activity_id": 1, "local_date": _dt.date(2026, 11, 1), "exercise_row_id": 10, "sequence": 1,
    "exercise_id": 5, "equipment_id": 7,
}


def test_sql_projeta_a_coluna_pelo_dono():
    assert f"e.{fp.FORM_PRACTICE_COLUMN}" in th.SESSIONS_SQL and "AS form_practice" in th.SESSIONS_SQL


@pytest.mark.parametrize("valor", ["ausente", None, 0, "0", False])
def test_row_sem_marca_e_o_de_sempre(valor):
    row = dict(_ROW) if valor == "ausente" else {**_ROW, "form_practice": valor}
    (session,) = th.build_sessions([row])
    assert session.exercises[0].form_practice is False
    assert session.progression_view(H) is session
    assert session.progression_view_all_goals() is session


def test_row_marcada_sai_da_visao_da_progressao_e_fica_na_sessao():
    rows = [{**_ROW, "form_practice": 1}, {**_ROW, "exercise_row_id": 11, "sequence": 2, "form_practice": 0}]
    (session,) = th.build_sessions(rows)
    assert [e.form_practice for e in session.exercises] == [True, False]
    assert [e.exercise_row_id for e in session.progression_view(H).exercises] == [11]
    assert [e.exercise_row_id for e in session.progression_view_all_goals().exercises] == [11]
    assert len(session.exercises) == 2  # a sessao inteira continua igual
    # deload continua esvaziando tudo
    assert replace(session, deload_source="manual").progression_view_all_goals().exercises == ()


# ---------------------------------------------------------------------------
# Propriedade central (nos dois avaliadores)
# ---------------------------------------------------------------------------


def _marcada(item, **sets):
    return replace(item, form_practice=True, sets=tuple(fx.sets(**sets)) if sets else item.sets)


def _supino(session):
    return next(e for e in session.exercises if e.exercise_id == parity.SUPINO)


def _variantes():
    """(com marcadas, sem elas, controle com as mesmas execucoes sem marca)."""
    base = parity.history()[:12]
    com, sem, controle = list(base), list(base), list(base)
    # sessao 4: supino marcado bem abaixo da referencia (seria regressao)
    s = base[4]
    baixo = _marcada(_supino(s), reps=[4, 4, 3], rpe=10, weight=25)
    com[4] = replace(s, exercises=tuple(baixo if e.exercise_id == parity.SUPINO else e for e in s.exercises))
    sem[4] = replace(s, exercises=tuple(e for e in s.exercises if e.exercise_id != parity.SUPINO))
    controle[4] = replace(com[4], exercises=tuple(replace(e, form_practice=False) for e in com[4].exercises))
    # sessao 7: segunda passagem de supino, marcada e muito acima (seria avanco)
    s = base[7]
    alto = replace(
        _marcada(_supino(s), reps=[15, 15, 15], rpe=7, weight=80), exercise_row_id=999_001, sequence=9
    )
    com[7] = replace(s, exercises=(*s.exercises, alto))
    controle[7] = replace(s, exercises=(*s.exercises, replace(alto, form_practice=False)))
    return com, sem, controle


def test_replay_de_producao_com_marcadas_e_identico_ao_historico_sem_elas(monkeypatch):
    com, sem, controle = _variantes()
    referencia = parity.run(monkeypatch, sessions=sem)
    marcado = parity.run(monkeypatch, sessions=com)
    assert marcado["fingerprint"] == referencia["fingerprint"]
    assert marcado["decisions_md5"] == referencia["decisions_md5"]
    # controle: as mesmas execucoes sem a marca mudam o estado
    sem_marca = parity.run(monkeypatch, sessions=controle)
    assert sem_marca["decisions_md5"] != referencia["decisions_md5"]


def _iguais(a, b):
    assert a.tracks == b.tracks
    assert a.group_states == b.group_states
    assert [d.to_dict() for d in a.decisions] == [d.to_dict() for d in b.decisions]


def test_por_objetivo_identico_ao_historico_sem_as_marcadas():
    com, sem, controle = _variantes()
    inputs = fx.inputs(tracks=parity.TRACKS)
    referencia = ev.evaluate_history_by_goal(sem, inputs=inputs).results[H]
    _iguais(ev.evaluate_history_by_goal(com, inputs=inputs).results[H], referencia)
    outro = ev.evaluate_history_by_goal(controle, inputs=inputs).results[H]
    assert [d.to_dict() for d in outro.decisions] != [d.to_dict() for d in referencia.decisions]


def test_evaluate_history_identico_ao_historico_sem_as_marcadas():
    com, sem, controle = _variantes()
    inputs = fx.inputs(tracks=parity.TRACKS)
    referencia = ev.evaluate_history(sem, inputs=inputs)
    marcado = ev.evaluate_history(com, inputs=inputs)
    _iguais(marcado, referencia)
    assert [s.activity_id for s in marcado.session_decisions] == [s.activity_id for s in com]
    outro = ev.evaluate_history(controle, inputs=inputs)
    assert [d.to_dict() for d in outro.decisions] != [d.to_dict() for d in referencia.decisions]
    # o passo a passo (replay de diagnostico) e o mesmo encadeamento
    passos = list(ev.evaluate_history_steps(com, inputs=inputs))
    assert [s.activity_id for s, _ in passos] == [s.activity_id for s in com]
    ultimo = next(r for _, r in reversed(passos) if r is not None)
    assert (ultimo.tracks, ultimo.group_states) == (marcado.tracks, marcado.group_states)
    # cada passo traz so as decisoes da sua sessao; somadas, sao as do replay
    assert [d.to_dict() for _, r in passos if r is not None for d in r.decisions] == [
        d.to_dict() for d in marcado.decisions
    ]


def test_marcada_intercalada_preserva_plato_e_contadores_por_trilha():
    """Supino marcado na sessao 4: plato da trilha e historico do grupo sao os
    de 3 -> 5 consecutivas (o historico sem a execucao)."""
    com, sem, _ = _variantes()
    inputs = fx.inputs(tracks=parity.TRACKS)
    estado = ev.evaluate_history_by_goal(sem[:6], inputs=inputs).results[H]
    for key in [k for k in estado.tracks if k[0] == parity.SUPINO]:
        assert ev.track_plateau_history(
            [s.progression_view(H) for s in com[:6]], key, estado.group_states, estado.tracks, inputs
        ) == ev.track_plateau_history(sem[:6], key, estado.group_states, estado.tracks, inputs)
    assert ev.group_session_history(
        [s.progression_view(H) for s in com[:6]], parity.PEITO, estado.group_states, estado.tracks, inputs
    ) == ev.group_session_history(sem[:6], parity.PEITO, estado.group_states, estado.tracks, inputs)


# ---------------------------------------------------------------------------
# finish (evaluate_session_by_goal)
# ---------------------------------------------------------------------------


def _finish(sessions, index):
    inputs = fx.inputs(tracks=parity.TRACKS)
    antes = ev.evaluate_history_by_goal(sessions[:index], inputs=inputs).results[H]
    resultado = ev.evaluate_session_by_goal(
        sessions[index], sessions[: index + 1], group_states=antes.group_states, tracks=antes.tracks, inputs=inputs
    )
    return antes, resultado


@pytest.mark.parametrize(
    "sets", [dict(reps=[4, 4, 3], rpe=10, weight=25), dict(reps=[15, 15, 15], rpe=7, weight=80)]
)
def test_finish_marcada_nao_regride_nem_avanca_e_o_resto_e_avaliado(sets):
    base = parity.history()[:12]
    s = base[10]
    marcado = replace(s, exercises=tuple(_marcada(e, **sets) if e.exercise_id == parity.SUPINO else e for e in s.exercises))
    sem = replace(s, exercises=tuple(e for e in s.exercises if e.exercise_id != parity.SUPINO))
    antes, com = _finish([*base[:10], marcado], 10)
    _, ref = _finish([*base[:10], sem], 10)
    assert com.skipped_reason is None
    assert com.results[H].tracks == ref.results[H].tracks
    assert [d.to_dict() for d in com.decisions] == [d.to_dict() for d in ref.decisions]
    # a trilha do supino nao se mexe (nem adota a carga, nem regride, nem avanca)
    for key, track in antes.tracks.items():
        if key[0] == parity.SUPINO:
            assert com.results[H].tracks[key] == track
    assert not any(k[0] == parity.SUPINO for k in com.results[H].track_outcomes)
    # crucifixo e remada da MESMA sessao seguem avaliados
    avaliados = {k[0] for k in com.results[H].track_outcomes}
    assert {parity.CRUCIFIXO, parity.REMADA} <= avaliados


def test_finish_sessao_toda_marcada_e_pulada():
    base = parity.history()[:12]
    tudo = replace(base[10], exercises=tuple(replace(e, form_practice=True) for e in base[10].exercises))
    _, result = _finish([*base[:10], tudo], 10)
    assert result.results == {} and result.decisions == ()
    assert result.skipped_reason == fp.SKIP_REASON_FORM_PRACTICE


def test_historico_com_sessao_toda_marcada_e_o_da_sessao_vazia():
    base = parity.history()[:12]
    inputs = fx.inputs(tracks=parity.TRACKS)
    tudo = [*base[:5], replace(base[5], exercises=tuple(replace(e, form_practice=True) for e in base[5].exercises)), *base[6:]]
    vazia = [*base[:5], replace(base[5], exercises=()), *base[6:]]
    _iguais(ev.evaluate_history_by_goal(tudo, inputs=inputs).results[H], ev.evaluate_history_by_goal(vazia, inputs=inputs).results[H])
    marcado = ev.evaluate_history(tudo, inputs=inputs)
    _iguais(marcado, ev.evaluate_history(vazia, inputs=inputs))
    assert marcado.session_decisions[5].decisions == ()


# ---------------------------------------------------------------------------
# Calibracao de maquina ignora a marcada
# ---------------------------------------------------------------------------

A, B = 10, 20


def _maquina(gym, weight, *, marcada=False):
    item = fx.execution(set_list=fx.sets(reps=[10, 10, 10], weight=weight, rpe=[8, 8, 8]))
    return replace(item, gym_id=gym, equipment_type_code="MACHINE", form_practice=marcada)


def _calibracao(marcas):
    plano = [(A, "60")] * 3 + [(B, "40")] * CALIBRATION_MIN_COMPARABLE_SESSIONS
    sessions = [
        fx.session(activity_id=i, day=f"2026-09-{i:02d}", executions=[_maquina(*step, marcada=i in marcas)])
        for i, step in enumerate(plano, start=1)
    ]
    return sessions


def _trilha_b(result):
    return next(t for t in result.tracks.values() if t.gym_context == B)


def test_calibracao_nao_conta_a_execucao_marcada():
    inputs = fx.inputs(tracks=[pe.TrackState(exercise_id=1, equipment_id=1, muscle_group_id=7)])
    # sem marca: o minimo de sessoes na academia B fecha a calibracao
    assert _trilha_b(ev.evaluate_history(_calibracao(set()), inputs=inputs)).load_status == pe.ACTIVE
    # a 1a sessao em B marcada nao conta: B continua calibrando, nos dois avaliadores
    marcada = _calibracao({4})
    sem = [replace(s, exercises=()) if s.activity_id == 4 else s for s in marcada]
    for avaliar in (
        lambda h: ev.evaluate_history(h, inputs=inputs),
        lambda h: ev.evaluate_history_by_goal(h, inputs=inputs).results[H],
    ):
        com, ref = avaliar(marcada), avaliar(sem)
        assert com.tracks == ref.tracks
        assert _trilha_b(com).in_context_calibration


# ---------------------------------------------------------------------------
# Teto de ativacao e fadiga previa CONTINUAM vendo a marcada
# ---------------------------------------------------------------------------


def _sonda():
    vistos: list[set[int]] = []

    def probe(window, session):
        vistos.append({e.exercise_row_id for s in window for e in s.exercises})
        return {}

    return vistos, probe


def test_teto_de_ativacao_le_a_execucao_marcada():
    com, _, _ = _variantes()
    marcadas = {e.exercise_row_id for s in com for e in s.exercises if e.form_practice}
    assert marcadas
    for avaliar in (
        lambda inputs: ev.evaluate_history(com, inputs=inputs),
        lambda inputs: ev.evaluate_history_by_goal(com, inputs=inputs),
        lambda inputs: ev.evaluate_session_by_goal(com[7], com[:8], group_states={}, tracks={}, inputs=inputs),
    ):
        vistos, probe = _sonda()
        avaliar(replace(fx.inputs(tracks=parity.TRACKS), activation_probe=probe))
        assert marcadas & set().union(*vistos)


def test_fadiga_previa_le_a_execucao_marcada():
    com, _, _ = _variantes()
    assert {item.row_id for item in executions_from_session(com[7])} == {e.exercise_row_id for e in com[7].exercises}
    assert 999_001 in {item.row_id for item in executions_from_session(com[7])}

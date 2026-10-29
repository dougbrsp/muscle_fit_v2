"""M97 F1: isolamento da progressao, da comparabilidade e das leituras por
objetivo de treino (dono `domain/training_goal.py`, mapa §66).

Sem MySQL. Prova:
- o objetivo esta na chave da trilha (`track_key`/`TrackState.key`) e do
  estado do grupo (`GroupState.key`), com o formato de sempre no default
  (rotulo da evidencia e alvo do `pending_payload`);
- `resolve_track` so ve as trilhas do objetivo pedido;
- uma trilha de FORCA nao altera a trilha de HIPERTROFIA do mesmo exercicio,
  equipamento e contexto (avaliacao por objetivo);
- a exposicao de outro objetivo e PULADA na sequencia comparavel; recorde/e1RM (chave
  fisica) seguem unicos;
- `progression_state` grava e le com o objetivo no WHERE/INSERT;
- o carimbo do objetivo na ficha e do SERVIDOR (sem campo do cliente).
"""

from __future__ import annotations

from dataclasses import replace
import pytest

from app import progression_state as ps
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain import training_goal as tg
from app.domain.training_history import build_sessions
from app.generation import plan_goal_stamp
from app.intelligence.dashboard import comparability as cmp
from tests.support import progression_fixtures as fx

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
PEITO = 10
SUPINO, BANCO = 1, 100


def _goal(execution, goal):
    return replace(execution, training_goal=goal)


# ---------------------------------------------------------------------------
# Dono: helpers novos
# ---------------------------------------------------------------------------


def test_goals_in_ordem_do_vocabulario_e_leitura_tolerante():
    assert tg.goals_in([S, None, "x", H]) == (H, S)
    assert tg.goals_in([]) == ()


def test_sql_goal_join_valida_alias():
    assert tg.sql_goal_join("g", "t") == "g.training_goal = t.training_goal"
    with pytest.raises(ValueError):
        tg.sql_goal_join("g; DROP", "t")


# ---------------------------------------------------------------------------
# Chave da trilha e do grupo
# ---------------------------------------------------------------------------


def test_track_key_tem_o_objetivo_e_o_default_e_hipertrofia():
    assert pe.track_key(SUPINO, BANCO) == (SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT, H)
    assert pe.track_key(SUPINO, BANCO, None, S) != pe.track_key(SUPINO, BANCO)
    track = pe.TrackState(exercise_id=SUPINO, equipment_id=BANCO, training_goal=S)
    assert track.key == (SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT, S)


def test_rotulo_e_alvo_do_payload_mantem_o_formato_no_default():
    default = pe.track_key(SUPINO, BANCO)
    assert pe.track_label(default) == f"{SUPINO}:{BANCO}"
    assert pe.track_key_payload(default) == [SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT]
    forca = pe.track_key(SUPINO, BANCO, None, S)
    assert pe.track_label(forca) == f"{SUPINO}:{BANCO}:{pe.DEFAULT_FATIGUE_CONTEXT}:{S}"
    assert pe.track_key_payload(forca) == [SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT, S]


def test_target_matches_compara_o_objetivo():
    hip = pe.TrackState(exercise_id=SUPINO, equipment_id=BANCO)
    forca = replace(hip, training_goal=S)
    alvo_legado = [SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT]  # 3 posicoes = default
    assert pe.target_matches(alvo_legado, hip)
    assert not pe.target_matches(alvo_legado, forca)
    assert pe.target_matches(pe.track_key_payload(forca.key), forca)
    assert pe.technique_target_from_payload({"track_key": pe.track_key_payload(forca.key)}) == (SUPINO, BANCO)


def test_group_state_key_e_novo_estado_no_objetivo():
    estado = pe.new_group_state(PEITO, fx.session().ended_at, S)
    assert estado.key == (PEITO, S)
    assert pe.GroupState(muscle_group_id=PEITO).key == (PEITO, H)


def test_resolve_track_so_ve_o_objetivo_pedido():
    hip = fx.track(exercise_id=SUPINO, equipment_id=BANCO, load=40)
    forca = replace(hip, training_goal=S, working_load_kg=hip.working_load_kg + 20)
    tracks = {hip.key: hip, forca.key: forca}
    assert pe.resolve_track(tracks, SUPINO, BANCO)[0] is hip
    assert pe.resolve_track(tracks, SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT, S)[0] is forca
    # sem trilha de forca: nao herda a de hipertrofia
    assert pe.resolve_track({hip.key: hip}, SUPINO, BANCO, None, S) == (None, False)


# ---------------------------------------------------------------------------
# Avaliacao por objetivo
# ---------------------------------------------------------------------------


def _sessions_hibridas():
    """Supino no banco em hipertrofia (12 reps a 40 kg) em todas as sessoes e,
    na mesma sessao, um supino de FORCA no mesmo par (5 reps a 70 kg)."""
    sessions = []
    for index, day in enumerate(("2026-08-01", "2026-08-04", "2026-08-07", "2026-08-10")):
        hip = fx.execution(exercise_id=SUPINO, equipment_id=BANCO, sequence=1, set_list=fx.sets(reps=12, rpe=9))
        forca = _goal(
            replace(
                fx.execution(
                    exercise_id=SUPINO, equipment_id=BANCO, sequence=2, set_list=fx.sets(reps=5, rpe=9, weight=70)
                ),
                exercise_row_id=9000 + index,
            ),
            S,
        )
        sessions.append(fx.session(activity_id=100 + index, day=day, executions=[hip, forca]))
    return sessions


def _inputs():
    return fx.inputs(tracks=[fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)])


def test_trilha_de_forca_nao_altera_a_de_hipertrofia_do_mesmo_par():
    sessions = _sessions_hibridas()
    so_hip = [replace(session, exercises=session.exercises[:1]) for session in sessions]

    misto = ev.evaluate_history_by_goal(sessions, inputs=_inputs())
    referencia = ev.evaluate_history(so_hip, inputs=_inputs())

    assert set(misto.results) == {H, S}
    hip = misto.results[H]
    # hipertrofia identica ao historico SEM as execucoes de forca
    assert hip.tracks == referencia.tracks
    assert hip.group_states == referencia.group_states
    assert [d.to_dict() for d in hip.decisions] == [d.to_dict() for d in referencia.decisions]
    # forca tem trilha e estado proprios, no objetivo dela
    forca = misto.results[S]
    assert set(forca.tracks) == {pe.track_key(SUPINO, BANCO, pe.DEFAULT_FATIGUE_CONTEXT, S)}
    assert {state.key for state in forca.group_states.values()} == {(PEITO, S)}
    assert all(track.training_goal == S for track in forca.tracks.values())


def test_evaluate_session_by_goal_avalia_cada_objetivo_com_os_seus_estados():
    sessions = _sessions_hibridas()
    historico = ev.evaluate_history_by_goal(sessions[:-1], inputs=_inputs())
    estados = {state.key: state for result in historico.results.values() for state in result.group_states.values()}
    trilhas = {key: track for result in historico.results.values() for key, track in result.tracks.items()}

    sessao = ev.evaluate_session_by_goal(
        sessions[-1], sessions, group_states=estados, tracks=trilhas, inputs=_inputs()
    )
    completo = ev.evaluate_history_by_goal(sessions, inputs=_inputs())
    for goal in (H, S):
        assert sessao.results[goal].tracks == completo.results[goal].tracks
        assert sessao.results[goal].group_states == completo.results[goal].group_states
    assert set(sessao.group_outcomes) == {PEITO, f"{PEITO}:{S}"}


def test_historico_so_de_hipertrofia_e_identico_ao_nucleo():
    from tests.support.progression_parity_fixture import TRACKS, history

    sessions = history()
    inputs = fx.inputs(tracks=TRACKS)
    por_objetivo = ev.evaluate_history_by_goal(sessions, inputs=inputs)
    nucleo = ev.evaluate_history(sessions, inputs=inputs)
    assert list(por_objetivo.results) == [H]
    result = por_objetivo.results[H]
    assert result.tracks == nucleo.tracks
    assert result.group_states == nucleo.group_states
    assert [d.to_dict() for d in result.decisions] == [d.to_dict() for d in nucleo.decisions]
    assert [b.activity_id for b in result.session_decisions] == [b.activity_id for b in nucleo.session_decisions]


def test_teto_de_ativacao_le_a_sessao_inteira():
    sessions = _sessions_hibridas()
    vistos: list[int] = []

    def probe(window, session):
        vistos.append(len(session.exercises))
        return {}

    inputs = replace(_inputs(), activation_probe=probe)
    ev.evaluate_history_by_goal(sessions, inputs=inputs)
    assert vistos and set(vistos) == {2}


# ---------------------------------------------------------------------------
# Historico com o objetivo
# ---------------------------------------------------------------------------


def _row(**extra):
    base = {
        "activity_id": 1,
        "local_date": fx.session().local_date,
        "exercise_row_id": 11,
        "sequence": 1,
        "exercise_id": SUPINO,
        "equipment_id": BANCO,
        "set_row_id": None,
    }
    base.update(extra)
    return base


def test_build_sessions_le_o_objetivo_e_only_goal_filtra():
    sessions = build_sessions([_row(), _row(exercise_row_id=12, sequence=2, training_goal=S)])
    session = sessions[0]
    assert [item.training_goal for item in session.exercises] == [H, S]
    assert session.training_goals == (H, S)
    assert [item.exercise_row_id for item in session.only_goal(S).exercises] == [12]
    so_hip = build_sessions([_row()])[0]
    assert so_hip.only_goal(H) is so_hip


# ---------------------------------------------------------------------------
# Comparabilidade
# ---------------------------------------------------------------------------


def _exposure(goal, reps=10, load=40.0):
    return cmp.ExposureSummary(
        key=cmp.build_key(SUPINO, BANCO, None, training_goal=goal),
        total_reps=reps * 3,
        best_load_kg=load,
        avg_rpe=9.0,
        e1rm=None,
        sets=tuple(cmp.WorkingSet(reps=reps, weight_kg=load, rpe=9.0) for _ in range(3)),
    )


def test_exposicao_de_outro_objetivo_e_pulada_sem_quebrar_a_sequencia():
    """Decisao do Tech Lead (PR #627): como o contexto de fadiga, a exposicao
    de OUTRO objetivo e pulada - nao entra na sequencia nem a interrompe."""
    exposures = [_exposure(H), _exposure(S, reps=5, load=70.0), _exposure(H, reps=11)]
    indices, skipped = cmp.trailing_comparable_indices(exposures)
    assert indices == [0, 2] and skipped == 0
    status, _reasons = cmp.classify_exercise_progression(exposures)
    referencia, _ = cmp.classify_exercise_progression([exposures[0], exposures[2]])
    assert status == referencia
    # ultima de forca: a sequencia e so a de forca
    indices, _ = cmp.trailing_comparable_indices([*exposures, _exposure(S, reps=5, load=72.5)])
    assert indices == [1, 3]


def test_chave_fisica_sem_objetivo_recorde_unico():
    hip, forca = cmp.build_key(SUPINO, BANCO, training_goal=H), cmp.build_key(SUPINO, BANCO, training_goal=S)
    assert not cmp.keys_match(hip, forca)
    assert cmp.physical_keys_match(hip, forca)
    assert hip.physical == forca.physical
    assert cmp.mismatch_with_current(forca, hip) == cmp.MISMATCH_TRAINING_GOAL
    # o contrato da API nao muda nesta fase
    assert "training_goal" not in hip.to_dict()


def test_mesmo_objetivo_mantem_o_comportamento_de_antes():
    sem = [cmp.ExposureSummary(**{**vars(_exposure(None, reps=r))}) for r in (10, 11)]
    com = [_exposure(H, reps=r) for r in (10, 11)]
    assert cmp.classify_exercise_progression(sem) == cmp.classify_exercise_progression(com)


# ---------------------------------------------------------------------------
# progression_state: objetivo no WHERE/INSERT
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self, rows=None, rowcount=0):
        self.statements: list[tuple[str, tuple]] = []
        self.rows = rows or []
        self.rowcount = rowcount
        self.lastrowid = 1

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), tuple(params or ())))

    def fetchone(self):
        return {"id": 42}

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        cursor = self._cursor

        class _Ctx:
            def __enter__(self):
                return cursor

            def __exit__(self, *exc):
                return False

        return _Ctx()


def test_save_track_grava_e_procura_pelo_objetivo():
    cursor = _RecordingCursor(rowcount=0)
    track = replace(fx.track(exercise_id=SUPINO, equipment_id=BANCO), training_goal=S)
    assert ps.save_track(_Conn(cursor), 7, track) == 42
    update, insert, select = cursor.statements
    assert "progression_tracks.training_goal = %s" in update[0] and update[1][-1] == S
    assert "training_goal" in insert[0] and S in insert[1]
    assert "progression_tracks.training_goal = %s" in select[0] and select[1][-1] == S


def test_save_group_state_e_decisao_com_o_objetivo():
    cursor = _RecordingCursor(rowcount=0)
    state = replace(fx.group(muscle_group_id=PEITO), training_goal=S)
    ps.save_group_state(_Conn(cursor), 7, state)
    update, insert, select = cursor.statements
    assert "progression_group_states.training_goal = %s" in update[0] and update[1][-1] == S
    assert insert[1][:3] == (7, PEITO, S)
    decision = pe.ProgressionDecision(group_state_id=None, muscle_group_id=PEITO, track_id=None)
    cursor = _RecordingCursor()
    ps.insert_decision(_Conn(cursor), 7, decision, training_goal=S)
    sql, params = cursor.statements[0]
    assert "training_goal" in sql and params[-1] == S


def test_load_group_states_filtra_pelo_objetivo_e_load_all_separa():
    linha = {
        "id": 1, "muscle_group_id": PEITO, "training_goal": S, "phase": "F1", "phase_status": "ACTIVE",
        "reps_min": 6, "reps_max": 8, "pending_action": None, "pending_payload": None,
        "phase_entered_at": None, "last_rep_range_change_at": None, "last_load_change_at": None,
        "last_sets_change_at": None, "technique_applied_at": None, "next_eligible_progression_at": None,
        "technique_target_exercise_id": None, "technique_target_equipment_id": None,
    }
    cursor = _RecordingCursor(rows=[linha])
    estados = ps.load_group_states(_Conn(cursor), 7, S)
    sql, params = cursor.statements[0]
    assert "progression_group_states.training_goal = %s" in sql and params == (7, S)
    assert estados[PEITO].training_goal == S
    cursor = _RecordingCursor(rows=[linha, {**linha, "id": 2, "training_goal": H}])
    assert set(ps.load_all_group_states(_Conn(cursor), 7)) == {(PEITO, S), (PEITO, H)}


# ---------------------------------------------------------------------------
# Carimbo pelo servidor
# ---------------------------------------------------------------------------


class _StampCursor:
    def __init__(self, plan, items):
        self.plan, self.items = plan, items
        self.updates: list[tuple] = []
        self._last = None

    def execute(self, sql, params=()):
        self._last = sql
        if sql.startswith("UPDATE workout_plan_exercises"):
            self.updates.append(tuple(params))

    def fetchone(self):
        return self.plan

    def fetchall(self):
        return self.items


def _rows():
    # o cliente NAO mandou `training_goal`: a linha veio com o default
    return [
        {"workout_plan_exercise_id": 501, "sequence": 1, "mapping_id": SUPINO, "training_goal": H},
        {"workout_plan_exercise_id": 502, "sequence": 2, "mapping_id": 2, "training_goal": H},
    ]


def test_carimbo_modo_b_resolve_override_do_item_sem_campo_do_cliente():
    plan = {
        "template_id": 9, "template_slot_sequence": 1, "version_id": 3,
        "volume_mode": plan_goal_stamp.VOLUME_MODE_EQUIPMENT_SERIES, "template_training_goal": H,
    }
    items = [
        {"item_order": 1, "item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": SUPINO,
         "training_goal": S},
        {"item_order": 2, "item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": 2,
         "training_goal": None},
    ]
    cursor = _StampCursor(plan, items)
    rows = plan_goal_stamp.stamp_plan_row_goals(_Conn(cursor), 77, _rows())
    assert [row["training_goal"] for row in rows] == [S, H]
    assert cursor.updates == [(S, 501)]  # so a linha que MUDA


def test_carimbo_modelo_de_forca_vale_para_item_que_herda():
    plan = {
        "template_id": 9, "template_slot_sequence": 1, "version_id": 3,
        "volume_mode": plan_goal_stamp.VOLUME_MODE_EQUIPMENT_SERIES, "template_training_goal": S,
    }
    items = [
        {"item_order": 1, "item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": SUPINO,
         "training_goal": None},
        {"item_order": 2, "item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": 2,
         "training_goal": H},
    ]
    cursor = _StampCursor(plan, items)
    rows = plan_goal_stamp.stamp_plan_row_goals(_Conn(cursor), 77, _rows())
    assert [row["training_goal"] for row in rows] == [S, H]


def test_carimbo_modo_a_pelo_grupo_primary(monkeypatch):
    plan = {
        "template_id": 9, "template_slot_sequence": 1, "version_id": 3,
        "volume_mode": plan_goal_stamp.VOLUME_MODE_PERCENT_BUDGET, "template_training_goal": H,
    }
    items = [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITO, "mapping_id": None,
              "training_goal": S}]
    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({SUPINO: PEITO, 2: 20}, {}))
    cursor = _StampCursor(plan, items)
    rows = plan_goal_stamp.stamp_plan_row_goals(_Conn(cursor), 77, _rows())
    assert [row["training_goal"] for row in rows] == [S, H]


def test_ficha_sem_modelo_nao_e_carimbada():
    cursor = _StampCursor({"template_id": None}, [])
    rows = plan_goal_stamp.stamp_plan_row_goals(_Conn(cursor), 77, _rows())
    assert [row["training_goal"] for row in rows] == [H, H] and cursor.updates == []


def test_modelo_sem_objetivo_nao_gera_update(monkeypatch):
    plan = {
        "template_id": 9, "template_slot_sequence": 1, "version_id": 3,
        "volume_mode": plan_goal_stamp.VOLUME_MODE_EQUIPMENT_SERIES, "template_training_goal": H,
    }
    items = [{"item_order": 1, "item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": SUPINO,
              "training_goal": None}]
    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({}, {}))
    cursor = _StampCursor(plan, items)
    plan_goal_stamp.stamp_plan_row_goals(_Conn(cursor), 77, _rows())
    assert cursor.updates == []

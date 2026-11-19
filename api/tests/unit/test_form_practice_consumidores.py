"""Aprimoramento de tecnica nos consumidores de leitura (issue #196, ADR 0017,
mapa §105).

A execucao marcada (`exercises.form_practice = 1`) fica FORA de exposicoes,
recorde, painel de forca, tela de progresso, e1RM por grupo, e1RM de
referencia da recuperacao e "Desempenho recente"; CONTINUA no volume e na
recuperacao/fadiga. O predicado e sempre o do dono
(`domain/form_practice.py`), montado com o alias de `exercises` da consulta.
"""

from __future__ import annotations

import datetime as _dt
import inspect
import re

from app import analytics
from app.domain.form_practice import sql_counts_for_progression
from app.intelligence import exercise_performance as perf
from app.intelligence import recovery_read_model as rrm
from app.intelligence import volume_service as vs
from app.intelligence.dashboard import exposure_service, session_records, strength_panel

# ---------------------------------------------------------------------------
# SQL: o predicado do dono com o alias certo de `exercises`
# ---------------------------------------------------------------------------


def test_exposicoes_excluem_a_execucao_marcada_com_o_alias_e2():
    pred = sql_counts_for_progression("e2")
    for sql in (
        exposure_service.EXPOSURES_SQL_TEMPLATE,
        exposure_service.EXPOSURES_ALL_GOALS_SQL_TEMPLATE,
        exposure_service.BATCH_EXPOSURES_SQL_TEMPLATE,
        exposure_service.SESSION_PROGRESSION_SQL_TEMPLATE,
    ):
        assert pred in sql


def test_marcacao_de_deload_dos_graficos_nao_muda():
    assert "form_practice" not in exposure_service.DELOAD_SESSIONS_SQL


def test_recorde_e_painel_de_forca_excluem_com_o_alias_e():
    assert sql_counts_for_progression("e") in session_records.HISTORY_SQL_TEMPLATE
    assert sql_counts_for_progression("e") in strength_panel.MAIN_LIFTS_SQL
    # As series sao lidas pelos ids do historico ja filtrado.
    assert "form_practice" not in session_records.SETS_SQL


def test_tela_de_progresso_escolhe_so_exercicio_com_ponto():
    for with_goal in (True, False):
        assert sql_counts_for_progression("e") in analytics._exercise_progress_match_sql(with_goal)


def test_e1rm_por_grupo_exclui_a_execucao_marcada(monkeypatch):
    capturado: list[str] = []

    def _fake(sql, params=None):
        capturado.append(sql)
        return []

    monkeypatch.setattr(analytics, "_fetch_all", _fake)
    d = _dt.date(2026, 11, 1)
    analytics._fetch_group_e1rm_rows(
        1, {"current_from": d, "current_to": d, "previous_from": d, "previous_to": d}, load_convention_expr="NULL"
    )
    (sql,) = capturado
    assert sql_counts_for_progression("e") in sql


def test_leitura_das_series_da_progressao_traz_a_coluna():
    assert "e.form_practice AS form_practice" in inspect.getsource(analytics._fetch_exercise_set_rows)


# ---------------------------------------------------------------------------
# Tabela de progressao: sessao marcada fica, mas fora do e1RM
# ---------------------------------------------------------------------------


def _linha(activity_id, *, form_practice=None):
    row = {
        "activity_id": activity_id,
        "local_date": _dt.date(2026, 11, activity_id),
        "activity_name": "Treino",
        "mapping_id": 900,
        "set_number": 1,
        "set_type": "working",
        "reps": 8,
        "weight_kg": 60.0,
        "rpe": 8,
        "measure": "reps",
        "exercise_row_id": activity_id * 10,
        "training_goal": "hypertrophy",
        "deload_source": None,
    }
    if form_practice is not None:
        row["form_practice"] = form_practice
    return row


def test_sessao_marcada_fica_na_tabela_sem_e1rm():
    normal, marcada = analytics._build_progression_sessions([_linha(1), _linha(2, form_practice=1)])
    assert normal["e1rm"] is not None
    assert marcada["e1rm"] is None and marcada["e1rm_reps"] is None and marcada["e1rm_reliable"] is False
    assert marcada["sets_count"] == 1  # a sessao continua listada


def test_sessao_mista_usa_so_a_serie_normal_no_e1rm():
    normal = _linha(1)
    marcada = {**_linha(1, form_practice=1), "set_number": 2, "weight_kg": 100.0, "exercise_row_id": 11}
    (so_normal,) = analytics._build_progression_sessions([normal])
    (mista,) = analytics._build_progression_sessions([normal, marcada])
    for chave in ("e1rm", "e1rm_reps", "best_load_kg", "top_set_reps", "avg_rpe"):
        assert mista[chave] == so_normal[chave]
    assert mista["sets_count"] == 2  # a contagem de series segue com todas


def test_campo_form_practice_da_sessao_rotula_so_a_sessao_toda_marcada():
    normal, marcada = analytics._build_progression_sessions([_linha(1), _linha(2, form_practice=1)])
    assert marcada["form_practice"] is True and marcada["e1rm"] is None
    assert normal["form_practice"] is False
    misto = {**_linha(3, form_practice=1), "set_number": 2, "exercise_row_id": 31}
    (mista,) = analytics._build_progression_sessions([_linha(3), misto])
    assert mista["form_practice"] is False
    (deload,) = analytics._build_progression_sessions([{**_linha(4, form_practice=1), "deload_source": "manual"}])
    assert deload["form_practice"] is False
    (sem_coluna,) = analytics._build_progression_sessions([_linha(5)])
    assert sem_coluna["form_practice"] is False


def test_linha_antiga_sem_a_coluna_vale_nao_marcada():
    (sem_coluna,) = analytics._build_progression_sessions([_linha(1)])
    (zero,) = analytics._build_progression_sessions([_linha(1, form_practice=0)])
    assert sem_coluna == zero
    assert sem_coluna["e1rm"] is not None


# ---------------------------------------------------------------------------
# Recuperacao: formato empacotado com a marca; dose conta, referencia nao
# ---------------------------------------------------------------------------


def test_historico_empacotado_traz_a_marca_e_a_dose_nao_filtra():
    assert "CAST(e.form_practice AS CHAR)" in rrm.HISTORY_PACKED_SQL
    assert sql_counts_for_progression("e") not in rrm.HISTORY_PACKED_SQL
    assert rrm._parse_exercise("11|900|||1|1:10:8.00:60.00:0::|||||1|Supino")["form_practice"] == 1
    assert rrm._parse_exercise("11|900|||1|1:10:8.00:60.00:0::|||||0|Supino")["form_practice"] == 0


def test_volume_full_history_seleciona_a_marca_e_janela_nao():
    assert "e.form_practice AS form_practice" in vs.VolumeService._FULL_HISTORY_EXERCISE_SELECT
    assert "form_practice" not in vs.VolumeService._WINDOW_EXERCISE_SELECT


def _hist(activity_id, day, e1rm, *, form_practice=0):
    return {
        "activity_id": activity_id,
        "exercise_id": activity_id * 10,
        "mapping_id": 900,
        "local_date": day,
        "trained_at": _dt.datetime.combine(day, _dt.time(19)),
        "best_e1rm": e1rm,
        "deload_source": None,
        "form_practice": form_practice,
        "set_rows": [],
    }


def test_referencia_de_e1rm_ignora_a_marcada_mas_a_dose_mantem_o_e1rm_dela():
    d = _dt.date
    items = [
        _hist(1, d(2026, 11, 1), 100.0),
        _hist(2, d(2026, 11, 4), 104.0),
        _hist(3, d(2026, 11, 7), 40.0, form_practice=1),
        _hist(4, d(2026, 11, 10), 102.0),
    ]
    ctx = vs.VolumeService()._compute_session_dose_context(items)
    ident = vs.VolumeService._exercise_identity(items[0])
    # a marcada (40) nao entra na mediana da sessao 4
    assert ctx["reference_e1rm_by_identity_session"][(ident, 4)] == 102.0
    # mas a dose dela continua vendo o e1RM proprio (load spike)
    assert ctx["best_e1rm_by_identity_session"][(ident, 3)] == 40.0
    # controle: sem a marca, a mesma sessao puxa a referencia para baixo
    sem_marca = [{**it, "form_practice": 0} for it in items]
    ctx2 = vs.VolumeService()._compute_session_dose_context(sem_marca)
    assert ctx2["reference_e1rm_by_identity_session"][(ident, 4)] == 100.0


# ---------------------------------------------------------------------------
# "Desempenho recente": a marcada e pulada
# ---------------------------------------------------------------------------


def _item(activity_id, day, weight, reps, *, form_practice=None):
    item = {
        "activity_id": activity_id,
        "exercise_id": activity_id * 10,
        "mapping_id": 1,
        "local_date": day,
        "trained_at": _dt.datetime.combine(day, _dt.datetime.min.time()),
        "equipment_id": 7,
        "load_convention": None,
        "deload_source": None,
        "set_rows": [
            {"set_number": n, "reps": reps, "weight_kg": weight, "rpe": 9, "set_type": "working"} for n in (1, 2, 3)
        ],
    }
    if form_practice is not None:
        item["form_practice"] = form_practice
    return item


def _ratios(items):
    return perf.latest_performance_ratios(
        items, identity=lambda item: item["mapping_id"], sort_key=lambda item: item["trained_at"]
    )


def test_desempenho_recente_pula_a_execucao_marcada():
    d = _dt.date
    normal = [_item(1, d(2026, 10, 1), 50, 10), _item(2, d(2026, 10, 4), 50, 10)]
    marcada = _item(3, d(2026, 10, 7), 30, 6, form_practice=1)
    assert _ratios([*normal, marcada]) == _ratios(normal)
    # controle: sem a marca, a mesma execucao e queda forte
    assert _ratios([*normal, {**marcada, "form_practice": 0}])[(30, 3)] < 0.9


# ---------------------------------------------------------------------------
# MANTEM: SQLs de volume/recuperacao do ownership nao filtram a marca
# ---------------------------------------------------------------------------

_PREDICADO = re.compile(r"form_practice\s*(=|<>|!=)")


def test_sqls_de_volume_e_recuperacao_nao_filtram_a_marca():
    assert not _PREDICADO.search(rrm.WINDOW_SETS_SQL)
    assert not _PREDICADO.search(rrm.HISTORY_PACKED_SQL)
    fonte_volume = inspect.getsource(vs)
    # o volume_service so seleciona a coluna (referencia de e1RM), nunca filtra
    assert not _PREDICADO.search(fonte_volume)
    assert "sql_counts_for_progression" not in fonte_volume
    assert "form_practice" not in vs.VolumeService._WINDOW_EXERCISE_SELECT


# ---------------------------------------------------------------------------
# Recorde: a consulta REAL do historico num SQLite em memoria, sessao MISTA
# ---------------------------------------------------------------------------

_RECORDE_SCHEMA = """
CREATE TABLE activities (id INTEGER PRIMARY KEY, user_id INTEGER, local_date TEXT, started_at TEXT,
                         deload_source TEXT);
CREATE TABLE exercises (id INTEGER PRIMARY KEY, activity_id INTEGER, mapping_id INTEGER, sequence INTEGER,
                        equipment_id INTEGER, workout_plan_exercise_id INTEGER,
                        form_practice INTEGER NOT NULL DEFAULT 0);
CREATE TABLE exercise_sets (id INTEGER PRIMARY KEY, exercise_id INTEGER, set_number INTEGER, set_type TEXT,
                            reps INTEGER, weight_kg REAL, rpe REAL, measure TEXT NOT NULL DEFAULT 'reps',
                            reps_pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE exercise_legacy_id_map (old_exercise_id INTEGER, canonical_exercise_id INTEGER);
CREATE TABLE gym_equipment (id INTEGER PRIMARY KEY, equipment_id INTEGER);
CREATE TABLE equipment_exercises (equipment_id INTEGER, exercise_id INTEGER, is_active INTEGER);
CREATE TABLE workout_plan_exercises (id INTEGER PRIMARY KEY, reps_target INTEGER);
"""


def _historico_real(execucoes, *, marcadas):
    """`execucoes`: [(activity_id, local_date, mapping_id, weight_kg)]; `marcadas`: ids de activity+mapping
    `(activity_id, mapping_id)` marcados como aprimoramento de tecnica. Devolve `parse_history` da consulta real."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_RECORDE_SCHEMA)
    conn.execute("INSERT INTO gym_equipment VALUES (7, 50)")
    conn.execute("INSERT INTO gym_equipment VALUES (8, 51)")
    conn.execute("INSERT INTO equipment_exercises VALUES (50, 22, 1)")
    conn.execute("INSERT INTO equipment_exercises VALUES (51, 23, 1)")
    for n, (activity_id, local_date, mapping_id, weight) in enumerate(execucoes, start=1):
        conn.execute(
            "INSERT OR IGNORE INTO activities VALUES (?, 1, ?, NULL, NULL)", (activity_id, local_date.isoformat())
        )
        conn.execute(
            "INSERT INTO exercises VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (n, activity_id, mapping_id, n, 7 if mapping_id == 22 else 8, int((activity_id, mapping_id) in marcadas)),
        )
        conn.execute(
            "INSERT INTO exercise_sets (id, exercise_id, set_number, set_type, reps, weight_kg, rpe) "
            "VALUES (?, ?, 1, 'working', 8, ?, 8)",
            (n, n, weight),
        )
    sql = session_records.history_sql("NULL").replace("{placeholders}", "?, ?")
    rows = [
        dict(r)
        for r in conn.execute(sql.replace("%s", "?"), (1, "2026-12-31", 22, 23)).fetchall()
    ]
    conn.close()
    return session_records.parse_history(rows)


_SESSOES_MISTAS = [
    (1, _dt.date(2026, 11, 1), 22, 100.0),
    (2, _dt.date(2026, 11, 8), 22, 200.0),  # marcada: nao pode virar recorde nem referencia
    (2, _dt.date(2026, 11, 8), 23, 50.0),  # mesma sessao, nao marcada
    (3, _dt.date(2026, 11, 15), 22, 110.0),
    (3, _dt.date(2026, 11, 15), 23, 60.0),
]


def test_recorde_em_sessao_mista_ignora_a_marcada_e_conta_a_outra():
    historico = _historico_real(_SESSOES_MISTAS, marcadas={(2, 22)})
    assert [(h.activity_id, h.exercise_id) for h in historico] == [(1, 22), (3, 22), (2, 23), (3, 23)]

    eventos = {(e.exposure.activity_id, e.exposure.exercise_id): e for e in session_records.record_events(historico)}
    # Referencia do exercicio marcado: 100 (nao 200); 110 supera 100 e e recorde.
    assert eventos[(3, 22)].previous_best == 100.0
    assert (2, 22) not in eventos
    # O exercicio nao marcado da mesma sessao 2 entra normalmente: 60 supera 50.
    assert eventos[(3, 23)].previous_best == 50.0
    chave = historico[0].key
    assert session_records.best_load(historico, chave).load_kg == 110.0


def test_recorde_controle_sem_marca_a_execucao_de_200_e_recorde():
    historico = _historico_real(_SESSOES_MISTAS, marcadas=set())
    eventos = {(e.exposure.activity_id, e.exposure.exercise_id) for e in session_records.record_events(historico)}
    assert (2, 22) in eventos
    assert (3, 22) not in eventos  # 110 < 200

"""M97 F4 - evolucao e dashboard por objetivo (mapa §66.4).

O que estes testes protegem:

- `?goal=` opcional nas leituras que nunca filtraram objetivo (ausente = a
  resposta de sempre; invalido = 422);
- drill-down: `records` e a linha de e1RM UNICOS (todos os objetivos, sem
  deload), `deload_sessions` marcadas; com um objetivo so, o bloco `records`
  e o de antes;
- tabela de `/dashboard-progression`: deload fora do e1RM por sessao e
  marcado; sem deload, os campos de sempre identicos;
- paineis de volume secundarios: a serie de forca pelo peso do dono
  (`methodology_volume`), hipertrofia identica, fadiga cheia;
- painel de forca (dono `strength_panel`) e relatorio Markdown (blocos por
  objetivo, deload sinalizado).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi import HTTPException

from app import load_conventions
from app.domain import training_goal as tg
from app.intelligence import methodology_volume as mv
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard import strength_panel as sp
from app.intelligence.dashboard.contracts import ExerciseProgressResponse, StrengthPanelResponse, WindowInfo, WindowKind
from app.training_goal_query import optional_goal_query_value

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value

JANELA = WindowInfo(
    kind=WindowKind.ROLLING_90D,
    date_from=date(2026, 7, 1),
    date_to=date(2026, 9, 28),
    timezone="America/Sao_Paulo",
    days=90,
)
USUARIO = 7
EXERCICIO = 1001


@pytest.fixture(autouse=True)
def _sem_cache_de_coluna():
    load_conventions.reset_load_convention_column_cache()
    yield
    load_conventions.reset_load_convention_column_cache()


# ---------------------------------------------------------------------------
# Parametro `goal` opcional
# ---------------------------------------------------------------------------


def test_goal_opcional_ausente_e_none_invalido_e_422():
    assert optional_goal_query_value(None) is None
    assert optional_goal_query_value("") is None
    assert optional_goal_query_value("Strength") == S
    with pytest.raises(HTTPException) as exc:
        optional_goal_query_value("cardio")
    assert exc.value.status_code == 422


def test_sql_goal_case_do_dono_e_peso_sql_do_volume():
    assert tg.sql_goal_case("e", S, "0.5", "1") == "(CASE WHEN e.training_goal = 'strength' THEN 0.5 ELSE 1 END)"
    with pytest.raises(ValueError):
        tg.sql_goal_case("e; DROP", S, "1", "1")
    with pytest.raises(ValueError):
        tg.sql_goal_case("e", S, "1 OR 1=1", "1")
    with pytest.raises(tg.InvalidTrainingGoal):
        tg.sql_goal_case("e", "cardio", "1", "1")
    expr = mv.sql_hypertrophy_volume_weight("e")
    assert "THEN 0.5 ELSE 1" in expr and mv.STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT == 0.5
    assert expr.endswith("ELSE 1 END)")


def test_rotulos_do_dono():
    assert tg.goal_label(None) == tg.GOAL_LABELS[tg.DEFAULT_GOAL]
    assert set(tg.GOAL_LABELS) == set(tg.TrainingGoal)
    from app.domain.deload import DELOAD_SOURCE_LABELS, DeloadSource, deload_source_label

    assert deload_source_label(None) is None
    assert deload_source_label("manual") == DELOAD_SOURCE_LABELS[DeloadSource.MANUAL]


# ---------------------------------------------------------------------------
# Drill-down: recorde e e1RM unicos, deload marcado
# ---------------------------------------------------------------------------


def _linha(exercise_row_id, activity_id, dia, set_number, *, weight=40.0, reps=5, rpe=8.0, goal=None):
    row = {
        "exercise_row_id": exercise_row_id,
        "activity_id": activity_id,
        "local_date": date(2026, 9, dia),
        "started_at": datetime(2026, 9, dia, 19, 0, 0),
        "position_in_session": 1,
        "equipment_id": 11,
        "equipment_name": "Gaiola",
        "load_convention": None,
        "prescribed_reps_target": "3-5",
        "set_number": set_number,
        "reps": reps,
        "weight_kg": weight,
        "rpe": rpe,
        "rest_seconds": 180,
        "notes": None,
    }
    if goal is not None:
        row["training_goal"] = goal
    return row


class _Cursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.db.executed.append((normalized, tuple(params)))
        self._result = self.db.respond(normalized, tuple(params))

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class _Db:
    """Exposicoes: com o predicado do objetivo devolve so as linhas daquele
    objetivo; sem ele (leitura do recorde), todas."""

    def __init__(self, rows, deloads=()):
        self.rows = list(rows)
        self.deloads = list(deloads)
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self):
        return _Cursor(self)

    def respond(self, sql, params):
        if sql.startswith("SELECT DATABASE()"):
            return [{"db": "musclefit"}]
        if "information_schema.COLUMNS" in sql:
            return [{"n": 1}]
        if sql.startswith("SELECT sel.exercise_row_id"):
            if "e2.training_goal = %s" in sql:
                goal = params[2]
                return [r for r in self.rows if tg.goal_value(r.get("training_goal")) == goal]
            return self.rows
        if "a.deload_source AS deload_source" in sql and "FROM activities a" in sql:
            return self.deloads
        if "FROM exercise_catalog ec" in sql:
            return [{"exercise_id": EXERCICIO, "exercise_name": "Agachamento", "exercise_amazfit_name": None}]
        return []


def _exposicoes_sql(db):
    return [(sql, params) for sql, params in db.executed if sql.startswith("SELECT sel.exercise_row_id")]


def test_template_de_todos_os_objetivos_tira_so_o_predicado_do_objetivo():
    com = svc.EXPOSURES_SQL_TEMPLATE
    sem = svc.EXPOSURES_ALL_GOALS_SQL_TEMPLATE
    assert tg.sql_goal_is("e2") in com and tg.sql_goal_is("e2") not in sem
    # deload fora nas duas leituras
    from app.domain.deload import sql_not_deload

    assert sql_not_deload("a2") in com and sql_not_deload("a2") in sem
    assert com.replace(f"\n    AND {tg.sql_goal_is('e2')}", "") == sem


def test_recorde_vem_de_todos_os_objetivos_e_a_progressao_so_do_pedido():
    rows = [
        _linha(1, 100, 1, 1, weight=80.0, goal=H),
        _linha(2, 101, 8, 1, weight=120.0, goal=S),  # recorde numa sessao de forca
        _linha(3, 102, 15, 1, weight=82.5, goal=H),
    ]
    db = _Db(rows)
    payload = svc.build_exercise_progress(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    leituras = _exposicoes_sql(db)
    assert len(leituras) == 2
    assert leituras[0][1][2] == H, "a progressao le o objetivo default"
    assert H not in leituras[1][1] and S not in leituras[1][1], "o recorde le sem parametro de objetivo"
    # progressao: so as de hipertrofia
    assert [e["activity_id"] for e in payload["exposures"]] == [100, 102]
    # recorde: o de forca (unico entre objetivos)
    assert payload["records"]["best_load_kg"]["activity_id"] == 101
    assert payload["records"]["best_e1rm"]["activity_id"] == 101
    # linha unica de e1RM com o objetivo de cada ponto
    assert [(p["activity_id"], p["training_goal"]) for p in payload["e1rm_series"]] == [(100, H), (101, S), (102, H)]
    assert payload["training_goal"] == H
    ExerciseProgressResponse.model_validate({**payload})


def test_com_um_objetivo_so_o_bloco_records_e_o_de_antes():
    rows = [_linha(1, 100, 1, 1, weight=80.0), _linha(2, 101, 8, 1, weight=85.0, reps=4)]
    db = _Db(rows)
    payload = svc.build_exercise_progress(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    exposicoes = svc.get_exercise_exposures(_Db(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["records"] == svc._records(exposicoes, exposicoes[-1]["_key"])
    assert payload["deload_sessions"] == []


def test_sessoes_de_deload_marcadas_com_rotulo_do_dono():
    deloads = [{"activity_id": 200, "local_date": date(2026, 9, 10), "deload_source": "period"}]
    db = _Db([_linha(1, 100, 1, 1)], deloads=deloads)
    payload = svc.build_exercise_progress(db, EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["deload_sessions"] == [
        {"activity_id": 200, "local_date": "2026-09-10", "deload_source": "period", "deload_label": "Deload (período)"}
    ]
    sql, params = next((s, p) for s, p in db.executed if "a.deload_source AS deload_source" in s)
    from app.domain.deload import sql_is_deload

    assert sql_is_deload("a") in sql
    assert params[0] == USUARIO and EXERCICIO in params


# ---------------------------------------------------------------------------
# Tabela de /dashboard-progression: deload fora do e1RM por sessao
# ---------------------------------------------------------------------------


def _serie(activity_id, dia, *, weight=60.0, reps=8, rpe=8.0, deload=None, goal=None):
    row = {
        "activity_id": activity_id,
        "local_date": f"2026-09-{dia:02d}",
        "activity_name": "Treino A",
        "mapping_id": EXERCICIO,
        "set_number": 1,
        "set_type": "working",
        "reps": reps,
        "weight_kg": weight,
        "rpe": rpe,
        "measure": None,
        "rpe_target_effective": None,
        "safety_event": None,
        "gym_equipment_id": 3,
        "exercise_row_id": activity_id * 10,
    }
    if deload is not None or goal is not None:
        row["deload_source"] = deload
        row["training_goal"] = goal
    return row


def test_tabela_sem_deload_mantem_os_campos_de_sempre():
    from app import analytics

    antes = [_serie(1, 1), _serie(2, 8, weight=62.5)]
    depois = [dict(r, deload_source=None, training_goal=H) for r in antes]
    sem_colunas = analytics._build_progression_sessions(antes)
    com_colunas = analytics._build_progression_sessions(depois)
    novos = ("training_goal", "deload_source", "deload_label")
    for a, b in zip(sem_colunas, com_colunas):
        assert {k: v for k, v in b.items() if k not in novos} == {k: v for k, v in a.items() if k not in novos}
        assert a["e1rm"] is not None and b["e1rm"] == a["e1rm"]
        for item in (a, b):
            assert item["deload_source"] is None and item["deload_label"] is None and item["training_goal"] == H


def test_sessao_de_deload_fica_fora_do_e1rm_e_marcada():
    from app import analytics

    rows = [_serie(1, 1), _serie(2, 8, weight=36.0, rpe=6.0, deload="manual"), _serie(3, 15, goal=S)]
    sessions = {s["activity_id"]: s for s in analytics._build_progression_sessions(rows)}
    assert sessions[2]["e1rm"] is None and sessions[2]["e1rm_reps"] is None
    assert sessions[2]["e1rm_reliable"] is False
    assert sessions[2]["deload_source"] == "manual" and sessions[2]["deload_label"] == "Deload (avulso)"
    assert sessions[2]["best_load_kg"] == 36.0, "a tabela continua mostrando a carga da sessao"
    assert sessions[1]["e1rm"] is not None and sessions[3]["training_goal"] == S


def test_exercise_progress_sem_goal_e_o_sql_de_sempre_e_com_goal_filtra(monkeypatch):
    from app import analytics

    chamadas = []
    monkeypatch.setattr(analytics, "_fetch_all", lambda sql, params=None: chamadas.append((sql, params)) or [])
    monkeypatch.setattr(analytics, "_days_ago", lambda user_id, days: date(2026, 1, 1))

    class _Id:
        user_id = USUARIO

    analytics.exercise_progress(name="agach", days=30, goal=None, identity=_Id())
    analytics.exercise_progress(name="agach", days=30, goal=S, identity=_Id())
    (sql_sem, params_sem), (sql_com, params_com) = chamadas
    assert tg.sql_goal_is("e") not in sql_sem
    assert params_sem == (date(2026, 1, 1), USUARIO, "%agach%", "%agach%")
    assert tg.sql_goal_is("e") in sql_com
    assert params_com == (*params_sem, S)


def test_rota_chamada_direto_sem_goal_nao_filtra(monkeypatch):
    """Regressao do CI (paridade de rotas): chamar a funcao da rota sem `goal`
    entrega o OBJETO default do `Depends`; ele vale como ausente, nunca como
    filtro (que zeraria o comparativo por slot)."""
    from app import analytics
    from app.intelligence import volume_service as vs
    from app.training_goal_query import optional_goal_param

    assert optional_goal_param(None) is None
    assert optional_goal_param(S) == S
    with pytest.raises(HTTPException):
        optional_goal_param("cardio")

    recebido = {}

    def fake(self, user_id, limit=400, training_goal="x"):
        recebido["goal"] = training_goal
        return {}

    monkeypatch.setattr(vs.VolumeService, "get_slot_session_comparison", fake)

    class _Id:
        user_id = USUARIO

    analytics.muscle_groups_slot_comparison(limit=400, identity=_Id())
    assert recebido["goal"] is None
    analytics.muscle_groups_slot_comparison(limit=400, goal=S, identity=_Id())
    assert recebido["goal"] == S


def test_fetch_set_rows_filtra_objetivo_so_quando_pedido(monkeypatch):
    from app import analytics

    chamadas = []
    monkeypatch.setattr(analytics, "_fetch_all", lambda sql, params=None: chamadas.append((sql, params)) or [])
    monkeypatch.setattr(analytics, "_days_ago", lambda user_id, days: date(2026, 1, 1))
    analytics._fetch_exercise_set_rows(USUARIO, 30, mapping_id=EXERCICIO)
    analytics._fetch_exercise_set_rows(USUARIO, 30, mapping_id=EXERCICIO, training_goal=S)
    (sql_sem, params_sem), (sql_com, params_com) = chamadas
    assert tg.sql_goal_is("e") not in sql_sem and params_sem == (date(2026, 1, 1), USUARIO, EXERCICIO)
    assert tg.sql_goal_is("e") in sql_com and params_com == (date(2026, 1, 1), USUARIO, EXERCICIO, S)
    assert "a.deload_source AS deload_source" in sql_sem and "e.training_goal AS training_goal" in sql_sem


# ---------------------------------------------------------------------------
# Paineis de volume secundarios: forca pelo peso do dono
# ---------------------------------------------------------------------------

_SEM = object()


def _servico_janela(goal, *, deload=None, mapping_id=None):
    from app.intelligence.volume_service import VolumeService
    from tests.unit import test_fase5_volume_realizado as t5

    rows = t5._overview_rows(t5.TRABALHO)
    for row in rows:
        row["activity_name"] = "Treino A"
        row["activity_type"] = "Musculação"
        row["duration_seconds"] = 3600
        row["mapping_id"] = mapping_id
        if goal is not _SEM:
            row["training_goal"] = goal
            row["deload_source"] = deload
    service = VolumeService()
    service._technique_catalog = t5._catalog()
    service._fetch_window_sets = lambda days, user_id=None: [dict(r) for r in rows]
    service._fetch_full_history_sets = lambda user_id=None, history_days=None: [dict(r) for r in rows]
    service._fetch_contributions_by_mapping = lambda ids: {}
    service.fetch_all = lambda *a, **k: [{"id": mapping_id, "name": "Remada"}] if mapping_id else []
    return service


def test_linha_do_tempo_estimulo_ponderado_fadiga_cheia_e_deload_marcado():
    antes = _servico_janela(_SEM).get_workout_sessions_timeline(user_id=1, days=14)[0]
    hip = _servico_janela(H).get_workout_sessions_timeline(user_id=1, days=14)[0]
    forca = _servico_janela(S, deload="manual").get_workout_sessions_timeline(user_id=1, days=14)[0]
    novos = ("deload_source", "deload_label", "training_goals")
    assert {k: v for k, v in hip.items() if k not in novos} == {k: v for k, v in antes.items() if k not in novos}
    assert hip["deload_source"] is None and hip["training_goals"] == [H]
    assert forca["stimulus_equivalent_sets"] == pytest.approx(
        antes["stimulus_equivalent_sets"] * mv.STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT
    )
    assert forca["fatigue_equivalent_sets"] == antes["fatigue_equivalent_sets"]
    assert forca["sets_count"] == antes["sets_count"]
    assert forca["deload_label"] == "Deload (avulso)" and forca["training_goals"] == [S]


def test_ranking_de_exercicios_e_razao_estimulo_fadiga_ponderam_so_o_estimulo():
    antes = _servico_janela(_SEM, mapping_id=10)
    hip = _servico_janela(H, mapping_id=10)
    forca = _servico_janela(S, mapping_id=10)
    rank = lambda s: s._compute_top_exercises_by_muscle_group(14, 1)  # noqa: E731
    assert rank(hip) == rank(antes)
    for grupo, itens in rank(antes).items():
        assert rank(forca)[grupo][0]["stimulus_equivalent_sets"] == pytest.approx(
            itens[0]["stimulus_equivalent_sets"] * mv.STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT
        )
        assert rank(forca)[grupo][0]["sets_count"] == itens[0]["sets_count"]
    ratio = lambda s: s.get_stimulus_fatigue_ratio_by_exercise(days=14, user_id=1)[0]  # noqa: E731
    assert ratio(hip) == ratio(antes)
    assert ratio(forca)["fatigue_equivalent_sets"] == ratio(antes)["fatigue_equivalent_sets"]
    assert ratio(forca)["stimulus_equivalent_sets"] == pytest.approx(
        ratio(antes)["stimulus_equivalent_sets"] * mv.STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT, abs=0.01
    )


def _equivalentes(monkeypatch, goal, training_goal_filter=None):
    from app.intelligence import muscle_contribution_resolver as resolver
    from app.intelligence.volume_service import VolumeService

    row = {"exercise_id": 1, "activity_id": 500, "local_date": "2026-09-28", "mapping_id": 10, "sets_count": 4,
           "technique_rows": 0}
    if goal is not _SEM:
        row["training_goal"] = goal
    chamadas = []
    service = VolumeService()
    service._technique_catalog = {}

    def fetch_all(sql, params=()):
        chamadas.append((sql, params))
        return [dict(row)]

    service.fetch_all = fetch_all
    monkeypatch.setattr(
        resolver,
        "_resolve_muscle_contributions_for_mappings",
        lambda ids: {10: [{"muscle_group_name": "Costas", "stimulus_weight": 1.0}]},
    )
    out = service.get_equivalent_sets_by_activity(limit=50, user_id=1, training_goal=training_goal_filter)
    return out, chamadas


def test_series_equivalentes_por_atividade_ponderadas_e_filtro_opcional(monkeypatch):
    antes, chamadas_sem = _equivalentes(monkeypatch, _SEM)
    hip, _ = _equivalentes(monkeypatch, H)
    forca, _ = _equivalentes(monkeypatch, S)
    assert hip == antes
    assert forca[0]["equivalent_sets"] == pytest.approx(antes[0]["equivalent_sets"] * 0.5)
    assert tg.sql_goal_is("e") not in chamadas_sem[0][0] and chamadas_sem[0][1] == (1, 50)
    _, chamadas_com = _equivalentes(monkeypatch, S, training_goal_filter=S)
    assert tg.sql_goal_is("e") in chamadas_com[0][0] and chamadas_com[0][1] == (1, S, 50)


def test_resumo_de_musculos_e_aderencia_ponderam_o_estimulo_no_sql():
    from app.intelligence.dashboard import adherence_service as ad
    from app.intelligence.dashboard import muscle_summary_service as mss

    peso = mv.sql_hypertrophy_volume_weight("e")
    assert f"SUM({mss._WORK} * {peso})" in mss.ROLE_TOTALS_SQL
    assert f"SUM({mss._WORK} * emc.fatigue_weight)" in mss.ROLE_TOTALS_SQL, "fadiga cheia"
    for sql in (mss.DAILY_GROUP_TOTALS_SQL, mss.GROUP_SESSIONS_SQL, mss.GROUP_EXERCISES_SQL, mss.SESSION_EQUIPMENT_SQL):
        assert f"emc.stimulus_weight * {peso}" in sql
    assert f"emc.stimulus_weight * {peso}" in ad.SESSION_EFFECTIVE_SQL
    assert "emc.fatigue_weight)     AS fatigue_sets" in ad.SESSION_EFFECTIVE_SQL
    assert "a.deload_source     AS deload_source" in ad.SESSIONS_PAGE_SQL


# ---------------------------------------------------------------------------
# Painel de forca (dono `strength_panel`)
# ---------------------------------------------------------------------------


def test_selecao_dos_compostos_principais():
    rows = [
        {"exercise_id": 2, "exercise_name": "Supino", "sessions": 3, "last_local_date": date(2026, 9, 1)},
        {"exercise_id": 1, "exercise_name": "Agachamento", "sessions": 3, "last_local_date": date(2026, 9, 5)},
        {"exercise_id": 3, "exercise_name": "Terra", "sessions": 5, "last_local_date": date(2026, 8, 1)},
        {"exercise_id": 4, "exercise_name": "Desenvolvimento", "sessions": 1, "last_local_date": date(2026, 9, 6)},
        {"exercise_id": 5, "exercise_name": "Remada", "sessions": 1, "last_local_date": date(2026, 9, 2)},
    ]
    escolhidos = sp.select_main_lifts(rows)
    assert len(escolhidos) == sp.STRENGTH_PANEL_MAX_EXERCISES
    assert [item["exercise_id"] for item in escolhidos] == [3, 1, 2, 4]


def test_sql_do_painel_usa_os_predicados_dos_donos():
    from app.domain.deload import sql_not_deload

    assert tg.sql_goal_is("e") in sp.MAIN_LIFTS_SQL
    assert sql_not_deload("a") in sp.MAIN_LIFTS_SQL
    assert "xc.is_compound = 1" in sp.MAIN_LIFTS_SQL


class _PainelDb(_Db):
    def respond(self, sql, params):
        if "AS sessions" in sql:
            return [{"exercise_id": EXERCICIO, "exercise_name": "Agachamento", "sessions": 2,
                     "last_local_date": date(2026, 9, 15)}]
        return super().respond(sql, params)


def test_painel_de_forca_top_sets_so_de_forca_e_linha_unica():
    rows = [
        _linha(1, 100, 1, 1, weight=80.0, reps=10, goal=H),
        _linha(2, 101, 8, 1, weight=100.0, reps=5, goal=S),
        _linha(2, 101, 8, 2, weight=100.0, reps=4, goal=S),
        _linha(3, 102, 15, 1, weight=105.0, reps=3, goal=S),
    ]
    db = _PainelDb(rows)
    payload = sp.build_strength_panel(db, user_id=USUARIO, window=JANELA)
    StrengthPanelResponse.model_validate(payload)
    (lift,) = payload["lifts"]
    assert [p["training_goal"] for p in lift["e1rm_series"]] == [H, S, S]
    assert [(t["activity_id"], t["load_kg"], t["reps"], t["top_sets_count"]) for t in lift["top_sets"]] == [
        (101, 100.0, 5, 2),
        (102, 105.0, 3, 1),
    ]
    assert payload["training_goal"] == S and payload["max_exercises"] == sp.STRENGTH_PANEL_MAX_EXERCISES
    painel_sql = next(params for sql, params in db.executed if "AS sessions" in sql)
    assert painel_sql == (USUARIO, S, JANELA.date_from, JANELA.date_to)


def test_painel_sem_forca_vem_vazio():
    class _Vazio(_Db):
        def respond(self, sql, params):
            return [] if "AS sessions" in sql else super().respond(sql, params)

    payload = sp.build_strength_panel(_Vazio([]), user_id=USUARIO, window=JANELA)
    assert payload["lifts"] == [] and payload["limitations"]
    StrengthPanelResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# Relatorio Markdown: blocos por objetivo e deload sinalizado
# ---------------------------------------------------------------------------


def _equipamento(day, exercise_row_id, goal=None, sets=3):
    row = {
        "local_date": day,
        "exercise_row_id": exercise_row_id,
        "exercise_id": 1,
        "exercise_name": "Agachamento",
        "equipment_id": 1,
        "equipment_name": "Gaiola",
        "gym_name": None,
        "load_conv": None,
        "physical_sets": sets,
        "work_units": sets,
        "technique_rows": 0,
        "rpe_sum": 8 * sets,
        "rpe_n": sets,
        "working_sets": sets,
        "reps_sum": 5 * sets,
        "tonnage_kg": 500.0,
        "duration_seconds_sum": 0,
        "load_max": 100.0,
        "load_sum": 100.0 * sets,
        "load_n": sets,
    }
    if goal is not None:
        row["training_goal"] = goal
    return row


def test_equipamento_com_um_objetivo_e_o_bloco_de_sempre():
    from app.intelligence.report import daily_breakdown as daily

    rows = [_equipamento(date(2026, 9, 1), 1), _equipamento(date(2026, 9, 3), 2)]
    out = daily.build_equipment(rows, {})
    # so hipertrofia: o dict de sempre, sem chave nova (paridade com a integracao)
    assert out == daily._build_equipment_rows(rows, {})
    assert set(out) == {"days", "period"}
    so_forca = daily.build_equipment([_equipamento(date(2026, 9, 1), 1, S)], {})
    assert so_forca["goals"] == [S] and so_forca["by_goal"] == {}


def test_equipamento_com_dois_objetivos_separa_os_blocos_e_o_relatorio_os_mostra():
    from app.intelligence.report import daily_breakdown as daily
    from app.intelligence.report.markdown_renderer import render_markdown

    rows = [_equipamento(date(2026, 9, 1), 1, H), _equipamento(date(2026, 9, 3), 2, S, sets=5)]
    out = daily.build_equipment(rows, {})
    assert out["goals"] == [H, S]
    assert out["by_goal"][S]["period"][0]["physical_sets"] == 5
    assert out["period"][0]["physical_sets"] == 8, "o resumo geral continua com todas as linhas"
    texto = render_markdown({"equipment": {"available": True, **out}})
    assert f"### Objetivo: {tg.goal_label(S)} — resumo do período" in texto
    assert f"### Objetivo: {tg.goal_label(H)} — detalhamento diário por equipamento" in texto
    so_um = render_markdown({"equipment": {"available": True, **daily.build_equipment(rows[:1], {})}})
    assert "### Objetivo:" not in so_um


def test_linha_do_tempo_do_relatorio_sinaliza_deload():
    from app.intelligence.report import daily_breakdown as daily

    sessoes = [
        {"activity_id": 1, "local_date": date(2026, 9, 1), "name": "A", "deload_source": None},
        {"activity_id": 2, "local_date": date(2026, 9, 3), "name": "B", "deload_source": "period"},
    ]
    linhas = daily.build_cycle_timeline(sessoes, [], [])
    assert linhas[0]["deload_label"] is None
    assert linhas[1]["deload_label"] == "Deload (período)"
    from app.intelligence.report.markdown_renderer import _timeline_session

    assert _timeline_session(linhas[0]) == "A"
    assert _timeline_session(linhas[1]) == "B (Deload (período))"
    assert "a.deload_source" in daily.CYCLE_SESSIONS_SQL


def _tendencia(goal):
    from app.intelligence.volume_service import VolumeService

    row = {
        "bucket_index": 0, "local_date": "2026-09-28", "exercise_row_id": 1, "set_id": 1, "set_number": 1,
        "set_type": "working", "technique_code": None, "anchor_set_number": None, "reps": 5, "weight_kg": 100,
        "rpe": 8, "measure": None, "primary_group": "Quadríceps", "secondary_group": "Glúteos",
        "primary_group_id": None, "secondary_group_id": None, "mapping_id": None, "secondary_participation": 0.5,
    }
    if goal is not _SEM:
        row["training_goal"] = goal
    service = VolumeService()
    service._technique_catalog = {}
    service._user_today = lambda user_id: date(2026, 9, 28)
    service.fetch_all = lambda sql, params=(): [dict(row)]
    items = service.get_rolling_weekly_trend_by_muscle_group(days=28, user_id=1)
    return {item["muscle_group"]: item for item in items}


def test_tendencia_semanal_pondera_diretas_indiretas_e_estimulo_e_mantem_os_brutos():
    antes, hip, forca = _tendencia(_SEM), _tendencia(H), _tendencia(S)
    assert hip == antes
    peso = mv.STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT
    assert forca["Quadríceps"]["direct_sets"] == pytest.approx(antes["Quadríceps"]["direct_sets"] * peso)
    assert forca["Glúteos"]["stimulus_equivalent_sets"] == pytest.approx(
        antes["Glúteos"]["stimulus_equivalent_sets"] * peso
    )
    for campo in ("sets_count", "reps_total", "volume_kg"):
        assert forca["Quadríceps"][campo] == antes["Quadríceps"][campo]


def test_ativacao_da_atividade_pondera_series_e_mantem_a_media_de_carga():
    from app.intelligence.volume_service import VolumeService

    contribs = {10: [{"muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0}]}
    service = VolumeService()
    base = [{"mapping_id": 10, "sets_count": 4, "avg_weight_kg": 80.0}]
    antes = service._build_muscle_activation_rows(base, contribs)
    forca = service._build_muscle_activation_rows([dict(base[0], hypertrophy_units=2.0)], contribs)
    assert forca[0]["primary_series"] == antes[0]["primary_series"] * 0.5
    assert forca[0]["primary_equivalent"] == antes[0]["primary_equivalent"] * 0.5
    assert forca[0]["primary_avg_weight_kg"] == antes[0]["primary_avg_weight_kg"]

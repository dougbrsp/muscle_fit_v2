"""PERF-02 e PERF-04 (auditoria 2026-10-08): menos idas ao banco, mesma resposta.

PERF-02 - `/analytics/dashboard/insights` reusa `build_overview`, mas o veredito
de qualidade dos dados (`build_data_quality`, ~10 statements) so alimenta
`data_quality_overall`/`data_quality_issues` da visao geral. Os testes fixam:
`build_insights` nao o chama, e as regras/cooldown recebem EXATAMENTE o mesmo
contexto com e sem ele.

PERF-04 - `/exercises/suggested-progression`:
- `list_exercise_logs(only_mapping_id=...)` mantem a MESMA janela de linhas e
  le as series so do exercicio pedido (1 statement em vez de ceil(n/400));
- `advanced_techniques` ativas e `equipment WHERE id IN` sao lidas UMA vez por
  requisicao GET, compartilhadas entre `training_safety` e `progression_state`
  (fora de requisicao, cada chamada continua lendo o banco).

Medido no DEV: ver o corpo do PR.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import catalog_reads, request_memo, services
from app import progression_state as ps
from app.domain import training_safety as ts
from app.intelligence.dashboard import overview_service as ov
from app.intelligence.dashboard.contracts import DataQuality, TargetSource, WindowInfo, WindowKind

DATE_TO = date(2026, 9, 22)


def _window() -> WindowInfo:
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=DATE_TO - timedelta(days=27),
        date_to=DATE_TO,
        timezone="America/Sao_Paulo",
        days=28,
    )


# ---------------------------------------------------------------------------
# PERF-02
# ---------------------------------------------------------------------------


@pytest.fixture
def overview_stubs(monkeypatch):
    """Todos os leitores de `build_overview` trocados por valores fixos; registra
    o contexto que as regras receberam e quantas vezes o veredito de qualidade
    foi calculado."""
    seen = {"ctx": [], "dq": 0}
    block = {"data_quality": DataQuality.HIGH}
    muscles = {
        "items": [{"muscle_group_id": 1}],
        "target_source": TargetSource.FALLBACK_GENERIC,
        "training_type_id": None,
    }

    def fake_dq(conn, **kwargs):
        seen["dq"] += 1
        return {"overall": "OK", "items": [1, 2]}

    def fake_rules(ctx):
        seen["ctx"].append(ctx)
        return [{"fingerprint": "f1", "data_quality": DataQuality.HIGH}], {"R1": 1}

    stubs = {
        "build_adherence": lambda conn, **k: {"cycle_length_days": 7, "has_template": True},
        "resolve_template_plan": lambda conn, owner: None,
        "muscle_group_names": lambda conn: {1: "Peito"},
        "build_muscle_summary": lambda conn, **k: dict(muscles),
        "get_exercises_progression_batch": lambda conn, **k: ([{"exercise_id": 1}], False, {1: 2}),
        "build_recovery_signals": lambda conn, **k: {"groups": []},
        "load_weight_rows": lambda conn, **k: ([], []),
        "load_bioimpedance_latest": lambda conn, **k: None,
        "load_measurement_rows": lambda conn, **k: ([], True),
        "body_block": lambda **k: {
            "data_quality": DataQuality.HIGH,
            "weight_ma7_change_pct": None,
            "weight_ma7_latest": None,
            "_weight_ma7_first": None,
            "_span_days": 0,
            "limitations": [],
            "_waist": {"limitations": []},
        },
        "load_context_events": lambda conn, **k: ([], True, []),
        "load_training_gap": lambda conn, **k: {
            "days_since_last_session": 1,
            "last_session_local_date": DATE_TO,
            "sessions_before": 3,
        },
        "_load_skipped_items": lambda conn, **k: [],
        "_load_unmapped_sets": lambda conn, **k: (10, 0),
        "build_data_quality": fake_dq,
        "evaluate_insights": fake_rules,
        "apply_cooldown": lambda conn, **k: {
            "visible": list(k["insights"]),
            "suppressed": [],
            "available": True,
            "limitations": [],
        },
        "consistency_block": lambda adherence: dict(block),
        "progression_block": lambda *a, **k: dict(block),
        "priority_muscles_block": lambda *a, **k: dict(block),
        "recovery_block": lambda *a, **k: dict(block),
        "next_actions_block": lambda *a, **k: {**block, "items": []},
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(ov, name, fn)
    return seen


def _kwargs():
    return {"user_id": 1, "window": _window(), "now_local": datetime(2026, 9, 22, 12)}


def test_insights_nao_calcula_o_veredito_de_qualidade(overview_stubs):
    payload = ov.build_insights(object(), **_kwargs())

    assert overview_stubs["dq"] == 0
    assert [item["fingerprint"] for item in payload["items"]] == ["f1"]


def test_regras_recebem_o_mesmo_contexto_com_e_sem_o_veredito(overview_stubs):
    completo = ov.build_overview(object(), **_kwargs())
    enxuto = ov.build_overview(object(), include_data_quality=False, **_kwargs())

    assert overview_stubs["dq"] == 1
    primeiro, segundo = overview_stubs["ctx"]
    assert primeiro == segundo
    for chave in ("_insights", "_suppressed_insights", "_cooldown_available", "_cooldown_limitations"):
        assert completo[chave] == enxuto[chave]
    # So a visao geral exibe o veredito.
    assert (completo["data_quality_overall"], completo["data_quality_issues"]) == ("OK", 2)
    assert "data_quality_overall" not in enxuto and "data_quality_issues" not in enxuto


def test_insights_igual_ao_calculado_sobre_a_visao_geral_completa(overview_stubs, monkeypatch):
    enxuto = ov.build_insights(object(), include_suppressed=True, **_kwargs())

    original = ov.build_overview
    monkeypatch.setattr(
        ov, "build_overview", lambda conn, **k: original(conn, **{**k, "include_data_quality": True})
    )
    completo = ov.build_insights(object(), include_suppressed=True, **_kwargs())

    assert enxuto == completo


# ---------------------------------------------------------------------------
# PERF-04 - fake de conexao que registra cada statement
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((" ".join(sql.split()), tuple(params or ())))
        self._rows = [dict(row) for row in self.conn.responder(sql, tuple(params or ()))]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, responder):
        self.responder = responder
        self.statements: list[tuple[str, tuple]] = []

    def cursor(self):
        return _Cursor(self)


def _logs_responder(sql, params):
    if "FROM exercises e" in sql:
        return [
            {"exercise_id": 31, "mapping_id": 7, "equipment_catalog_id": 27},
            {"exercise_id": 30, "mapping_id": 9, "equipment_catalog_id": 5},
            {"exercise_id": 21, "mapping_id": 7, "equipment_catalog_id": 27},
            {"exercise_id": 20, "mapping_id": None, "equipment_catalog_id": None},
        ]
    if "FROM exercise_sets" in sql:
        return [
            {"exercise_id": exercise_id, "set_number": 1, "set_type": None, "reps": 10, "weight_kg": 50, "rpe": 8}
            for exercise_id in params
        ]
    raise AssertionError(sql)


@pytest.fixture
def logs_conn(monkeypatch):
    conn = _Conn(_logs_responder)

    @contextmanager
    def fake_transaction():
        yield conn

    monkeypatch.setattr(services, "transaction", fake_transaction)
    return conn


def test_logs_de_um_exercicio_mesma_janela_e_series_so_dele(logs_conn):
    rows = services.list_exercise_logs(limit=300, user_id=1, only_mapping_id=7)

    assert [row["exercise_id"] for row in rows] == [31, 21]
    assert all(len(row["sets"]) == 1 for row in rows)
    principal, *series = logs_conn.statements
    # Mesma janela (mesmo WHERE/LIMIT) da chamada sem filtro.
    assert principal[1] == (1, 2000)
    assert len(series) == 1 and series[0][1] == (31, 21)


def test_logs_sem_filtro_continuam_lendo_series_de_todos(logs_conn):
    rows = services.list_exercise_logs(limit=300, user_id=1)

    assert [row["exercise_id"] for row in rows] == [31, 30, 21, 20]
    assert logs_conn.statements[1][1] == (31, 30, 21, 20)


def test_logs_de_exercicio_sem_historico_na_janela_nao_leem_series(logs_conn):
    assert services.list_exercise_logs(limit=300, user_id=1, only_mapping_id=99) == []
    assert len(logs_conn.statements) == 1


def _catalog_responder(sql, params):
    if "FROM equipment WHERE" in sql:
        return [{"id": 27, "max_safe_rpe": 9, "load_increment_kg": Decimal("2.5")}]
    if "FROM advanced_techniques" in sql:
        return [
            {
                "code": "DROP_SET",
                "failure_requirement": "failure_required",
                "auto_applicable": 1,
                "min_experience": "intermediate",
                "set_type": "drop_set",
                "is_active": 1,
                "activation_set_equivalent": 0.5,
                "max_equivalent_per_anchor": 1.0,
                "default_sets_count": None,
            }
        ]
    return []


def _prescricao_por_par(conn):
    """Os tres leitores que a prescricao por par chama na mesma requisicao."""
    safety = ts.load_safety_inputs_bulk(conn, [(7, 27)])[(7, 27)]
    catalogo = ps.load_technique_catalog(conn)
    passos = ps.load_equipment_steps(conn, [27])
    return safety, catalogo, passos


def _contagem(conn, trecho):
    return sum(1 for sql, _ in conn.statements if trecho in sql)


def test_catalogo_e_equipamento_lidos_uma_vez_por_requisicao():
    conn = _Conn(_catalog_responder)
    with request_memo.request_scope():
        safety, catalogo, passos = _prescricao_por_par(conn)

    assert _contagem(conn, "FROM advanced_techniques") == 1
    assert _contagem(conn, "FROM equipment WHERE") == 1
    # Mesmos valores que as leituras separadas produziam.
    assert safety["equipment_max_safe_rpe"] == 9
    assert safety["technique_failure_requirements"] == {"DROP_SET": "failure_required"}
    assert catalogo["DROP_SET"].failure_requirement == "failure_required"
    assert catalogo["DROP_SET"].activation_set_equivalent == 0.5
    assert passos == {27: Decimal("2.5")}


def test_fora_de_requisicao_cada_leitor_le_o_banco():
    conn = _Conn(_catalog_responder)
    _prescricao_por_par(conn)

    assert _contagem(conn, "FROM advanced_techniques") == 2
    assert _contagem(conn, "FROM equipment WHERE") == 2


def test_catalogo_ausente_continua_vazio_no_motor():
    def sem_tabela(sql, params):
        raise RuntimeError("Table 'advanced_techniques' doesn't exist")

    with request_memo.request_scope():
        assert ps.load_technique_catalog(_Conn(sem_tabela)) == {}


def test_equipamento_sem_passo_fica_fora_dos_passos():
    conn = _Conn(lambda sql, params: [{"id": 27, "max_safe_rpe": None, "load_increment_kg": None}])
    with request_memo.request_scope():
        assert ps.load_equipment_steps(conn, [27]) == {}
        linha = {"id": 27, "max_safe_rpe": None, "load_increment_kg": None}
        assert catalog_reads.equipment_rows(conn, [27]) == {27: linha}
    assert len(conn.statements) == 1

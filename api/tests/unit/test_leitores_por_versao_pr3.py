"""PR3 do versionamento de Modelo - as pecas puras dos leitores por data.

A prova de banco (periodo anterior a edicao igual byte a byte) esta em
`tests/integration/dataapi/test_leitores_por_versao_pr3.py`.
"""

from __future__ import annotations

from datetime import date, datetime

from app import template_assignments as ta
from app import training_types
from app.intelligence.dashboard import adherence_service, engine_service
from app.intelligence.report import daily_breakdown
from app.template_versions import plan_version_expr, version_at_condition


def test_versao_da_ficha_por_data_usa_a_gravada_depois_a_do_plan_date_depois_a_vigente():
    expr = plan_version_expr("wp", by_date=True)
    assert expr.startswith("COALESCE(wp.template_version_id, ")
    # Fim EXCLUSIVO do dia do usuario no relogio do banco: plan_date + 24h + 3h
    # (Sao Paulo, UTC-3) - M76 Lote 2b.
    assert "pva.effective_from < DATE_ADD(wp.plan_date, INTERVAL 1620 MINUTE)" in expr
    assert "pva.superseded_at >= DATE_ADD(wp.plan_date, INTERVAL 1620 MINUTE)" in expr
    assert "INTERVAL 1440 MINUTE" in plan_version_expr("wp", by_date=True, timezone_name="UTC")
    assert expr.rstrip(")").endswith("pvc.current_template_id = wp.template_id")
    # Sem literal com ':' (o shim da Data API trataria como parametro).
    assert ":" not in expr and "%" not in expr


def test_versao_vigente_nao_le_a_versao_gravada_na_ficha():
    expr = plan_version_expr("wp", by_date=False)
    assert "template_version_id" not in expr and "current_template_id = wp.template_id" in expr


def test_condicao_de_versao_num_instante_tem_dois_parametros():
    cond = version_at_condition("v")
    assert cond.count("%s") == 2
    assert "v.superseded_at > %s" in cond and "v.effective_from <= %s" in cond


def test_os_leitores_resolvem_a_versao_por_data():
    for sql in (
        engine_service.PLAN_HEADER_SQL,
        adherence_service.SESSIONS_PAGE_SQL,
        adherence_service.SESSION_BY_ID_SQL,
        daily_breakdown.CYCLE_SESSIONS_SQL,
    ):
        assert "COALESCE(wp.template_version_id" in sql


def test_atribuicao_num_instante_le_a_versao_do_mesmo_instante(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ta, "_query_one", lambda sql, params: chamadas.append((sql, params)) or None)
    ta.resolve_assignment_at(1, date(2026, 9, 1))
    fim = datetime(2026, 9, 2, 2, 59, 59)  # fim do dia 01/09 de Brasilia, em UTC
    assert chamadas == [(ta.ASSIGNMENT_AT_SQL, (fim, fim, 1, fim, fim))]


def test_historico_com_fim_de_janela_le_a_versao_do_fim_de_cada_trecho(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ta, "_query_all", lambda sql, params: chamadas.append((sql, params)) or [])
    ta.list_assignment_history(1)
    ta.list_assignment_history(1, window_end=date(2026, 9, 1))
    fim = datetime(2026, 9, 2, 2, 59, 59)  # fim do dia 01/09 de Brasilia, em UTC
    assert chamadas == [(ta.HISTORY_SQL, (1,)), (ta.HISTORY_AT_END_SQL, (fim, fim, fim, fim, 1))]


def test_segment_window_resolve_a_versao_do_fim_da_janela(monkeypatch):
    pedidos = []
    monkeypatch.setattr(ta, "list_assignment_history", lambda user_id, window_end=None: pedidos.append(window_end) or [])
    ta.segment_window(1, date(2026, 8, 1), date(2026, 8, 31))
    assert pedidos == [date(2026, 8, 31)]


def test_tipo_de_treino_da_janela(monkeypatch):
    monkeypatch.setattr(training_types, "resolve_active_training_type_for_user", lambda user_id: 5)
    monkeypatch.setattr(
        ta, "resolve_assignment_at", lambda user_id, moment: ta.Assignment(
            id=1, template_id=2, user_id=user_id, assigned_at=None, unassigned_at=None, training_type_id=9
        )
    )
    assert training_types.resolve_training_type_for_window(1, date(2026, 9, 1)) == 9
    assert training_types.resolve_training_type_for_window(1, None) == 5

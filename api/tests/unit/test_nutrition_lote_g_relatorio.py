"""Lote G: modo adaptativo na seção "7. Nutrição" do relatório Markdown.

O resumo é do motor (`report_window.adaptive_report_summary`); o serviço só carrega os estados do MESMO histórico da meta
(fechamentos e correções inclusive) e o renderer só apresenta. Cobre: estados só com plano adaptativo vigente, falha de
leitura que não derruba o relatório, o resumo contando o dia fechado à mão e a apresentação (com e sem o bloco).

Rodar: cd api && python -m pytest tests/unit/test_nutrition_lote_g_relatorio.py
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import nutrition_energy_service as energy_svc
from app import nutrition_targets_service as svc
from app.domain.nutrition import adaptive_energy as adaptive
from app.domain.nutrition import report_window
from app.intelligence.report.markdown_renderer import render_markdown
from tests.unit.test_nutrition_energy_detail import (  # noqa: F401  (db é fixture)
    DESDE,
    ONTEM,
    _plano_adaptativo_publicado,
    _plano_legado_publicado,
    db,
)
from tests.unit.test_nutrition_lote_g_fechamento_correcao import _ingestao_do_dia
from tests.unit.test_nutrition_modo_adaptativo_servico import ALUNO, HOJE, _historico
from tests.unit.test_reports_markdown_renderer import full_report_data


def _estados(db):  # noqa: F811 - fixture do pytest importada
    cursor = db.conn().cursor()
    em_vigor = svc.load_published_plans(cursor, ALUNO).for_day(HOJE)
    series = svc.load_user_inputs(cursor, ALUNO, HOJE, HOJE, HOJE).series
    return energy_svc.adaptive_states_for_report(cursor, ALUNO, HOJE, em_vigor[1] if em_vigor else None, series)


def test_plano_adaptativo_vigente_entrega_os_estados_do_historico(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    estados = _estados(db)
    assert len(estados) == adaptive.REPLAY_HISTORY_DAYS and estados[-1].day == HOJE


def test_plano_legado_ou_sem_plano_nao_carrega_historico_nenhum(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    assert _estados(db) == ()  # sem plano
    _plano_legado_publicado(db, DESDE)
    consultas: list[str] = []
    cursor = db.conn().cursor()
    original = cursor.execute
    cursor.execute = lambda sql, params=None: (consultas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
    em_vigor = svc.load_published_plans(cursor, ALUNO).for_day(HOJE)
    consultas.clear()
    assert energy_svc.adaptive_states_for_report(cursor, ALUNO, HOJE, em_vigor[1], lambda: pytest.fail("não lê o perfil")) == ()
    assert consultas == []  # legado: nenhuma consulta nova


def test_falha_na_leitura_do_historico_degrada_sem_derrubar_o_relatorio(db, monkeypatch, caplog):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)

    def quebra(*args, **kwargs):
        raise RuntimeError("valor-sigiloso")

    monkeypatch.setattr(energy_svc, "load_history", quebra)
    assert _estados(db) == ()
    assert "RuntimeError" in caplog.text and "valor-sigiloso" not in caplog.text


def test_resumo_do_motor_conta_o_dia_fechado_a_mao(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _ingestao_do_dia(db, ONTEM, 600.0)  # abaixo dos 70% da TMB: só o fechamento completa o dia
    db.closures[(ALUNO, ONTEM)] = datetime(2026, 11, 5, 12, 0)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    estados = _estados(db)
    dias = [HOJE - timedelta(days=i) for i in range(10, -1, -1)]
    resumo = report_window.adaptive_report_summary(dias, estados)
    assert resumo is not None and resumo["manually_closed_days"] == 1 and resumo["complete_days"] >= 1
    sem_fechar = report_window.adaptive_report_summary(dias, [])
    assert sem_fechar is None


def _secao(**adaptativo):
    secao = {"available": True, "has_records": True, "window_days": 30, "logged_days": 3, "no_record_days": 5,
             "incomplete_days": 0, "summary": {"nutrients": {}}, "weeks": [], "days": []}
    if adaptativo:
        secao["adaptive"] = adaptativo
    return secao


def _trecho(secao) -> str:
    dados = full_report_data()
    dados["nutrition"] = secao
    return render_markdown(dados).split("## 7. Nutrição")[1].split("## 8.")[0]


def test_renderer_mostra_o_bloco_adaptativo_com_os_valores_prontos():
    trecho = _trecho(_secao(
        as_of=HOJE, base_kcal=2750.0, base_source="adaptive", base_source_label="Gasto adaptativo (aprendido com ingestão e peso)",
        confidence=0.62, confidence_level="medium", confidence_avg=0.5, days_with_state=30, complete_days=21,
        manually_closed_days=4, activity_adjustment_kcal=120.0, activity_average_kcal=380.0,
    ))
    assert "### Gasto adaptativo" in trecho
    assert "Gasto adaptativo (aprendido com ingestão e peso)" in trecho
    assert "2750 kcal" in trecho or "2.750 kcal" in trecho
    assert "Média" in trecho and "62%" in trecho and "50%" in trecho
    assert "21 de 30" in trecho and "4 fechado(s) manualmente" in trecho
    assert "+120" in trecho and "380" in trecho


def test_renderer_sem_o_bloco_nao_inventa_nada():
    assert "Gasto adaptativo" not in _trecho(_secao())

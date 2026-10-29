"""Contratos semanticos do Novo Dashboard (F0.1, plano §8.4/§12):
`null` != 0, natureza obrigatoria, envelope serializavel.

Sem banco. Rodar: cd api && python -m pytest tests/unit/test_dashboard_contracts.py
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.intelligence.dashboard.contracts import (
    Comparison,
    DashboardEnvelope,
    DataNature,
    DataQuality,
    Evidence,
    Insight,
    Metric,
    Severity,
    WindowInfo,
    WindowKind,
)


def test_metric_aceita_value_none_como_nao_calculavel_e_nao_vira_zero():
    metric = Metric(nature=DataNature.OBSERVED, metric_version="sets-v1")
    assert metric.value is None
    assert metric.model_dump()["value"] is None
    assert json.loads(metric.model_dump_json())["value"] is None
    # zero observado e outra coisa: preservado como 0
    zero = Metric(value=0, nature=DataNature.OBSERVED, metric_version="sets-v1")
    assert zero.value == 0 and zero.value is not None


def test_metric_defaults_qualidade_alta_e_sem_limitacoes():
    metric = Metric(value=4.6, unit="sets", nature=DataNature.DERIVED, metric_version="effective-sets-v1")
    assert metric.data_quality is DataQuality.HIGH
    assert metric.limitations == []


def test_metric_rejeita_nature_invalida_e_nature_ausente():
    with pytest.raises(ValidationError):
        Metric(value=1, nature="GUESSED", metric_version="sets-v1")
    with pytest.raises(ValidationError):
        Metric(value=1, metric_version="sets-v1")


def test_metric_rejeita_value_nao_numerico_e_campo_desconhecido():
    with pytest.raises(ValidationError):
        Metric(value="muito", nature=DataNature.OBSERVED, metric_version="sets-v1")
    with pytest.raises(ValidationError):
        Metric(value=1, nature=DataNature.OBSERVED, metric_version="sets-v1", score=99)


def test_enums_serializam_como_string():
    metric = Metric(value=1, nature=DataNature.ESTIMATED, metric_version="epley-rir-v1", data_quality=DataQuality.MEDIUM)
    payload = json.loads(metric.model_dump_json())
    assert payload["nature"] == "ESTIMATED"
    assert payload["data_quality"] == "MEDIUM"
    assert set(n.value for n in DataNature) == {
        "OBSERVED", "DERIVED", "PLANNED", "PROJECTED", "ESTIMATED", "INFERRED"
    }
    assert set(q.value for q in DataQuality) == {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}
    assert set(s.value for s in Severity) == {"INFO", "ATTENTION", "ACTION"}


def test_insight_serializa_com_todos_os_campos_do_plano_12():
    insight = Insight(
        code="exercise_plateau_suspected",
        question="Estou evoluindo neste exercicio?",
        status="PLATEAU_SUSPECTED",
        severity=Severity.ATTENTION,
        answer="4 exposicoes sem progresso no supino reto (barra).",
        evidence=[
            Evidence(metric="e1rm", from_value=100.0, to_value=99.5, unit="kg", nature=DataNature.ESTIMATED,
                     observed_at=date(2026, 9, 20)),
            Evidence(metric="reps_total", from_value=24, to_value=24, nature=DataNature.OBSERVED),
        ],
        comparison=Comparison(basis="previous_exposure", window="90d", keys={"comparability_key": "12:3:total"}),
        data_quality=DataQuality.HIGH,
        limitations=["e1RM e estimativa (Epley+RIR)"],
        alternative_explanations=["sono ruim na semana"],
        suggested_action="Trocar faixa de reps ou variar o equipamento.",
        reassess_after=date(2026, 10, 6),
        source_screen="/dashboard/exercicios/12",
        fingerprint="exercise_plateau_suspected:12:3:total",
    )
    payload = json.loads(insight.model_dump_json())
    esperado = {
        "code", "question", "status", "severity", "answer", "evidence", "comparison", "data_quality",
        "limitations", "alternative_explanations", "suggested_action", "reassess_after", "source_screen",
        "fingerprint",
        # F2.2: a decisao de cooldown (`dashboard_alert_states`, migracao 051)
        # viaja COM o insight - um cartao que desaparece sem explicacao e
        # indistinguivel de defeito. `None` aqui e "nao houve decisao de
        # cooldown" (antes da 051, ou com a flag desligada), nunca "nao
        # silenciado".
        "cooldown",
    }
    assert set(payload) == esperado
    assert payload["severity"] == "ATTENTION"
    assert payload["evidence"][0]["from_value"] == 100.0
    assert payload["evidence"][1]["unit"] is None
    assert payload["comparison"]["keys"] == {"comparability_key": "12:3:total"}
    assert payload["reassess_after"] == "2026-10-06"
    assert payload["cooldown"] is None


def test_insight_opcionais_tem_default_vazio_e_comparison_pode_faltar():
    insight = Insight(
        code="dq_low", question="Da pra concluir?", status="LOW", severity=Severity.INFO, answer="...",
        data_quality=DataQuality.LOW, source_screen="/dashboard/data-quality", fingerprint="dq_low",
    )
    assert insight.evidence == [] and insight.comparison is None
    assert insight.limitations == [] and insight.alternative_explanations == []
    assert insight.suggested_action is None and insight.reassess_after is None


def test_envelope_base_com_window_e_versoes():
    window = WindowInfo(kind=WindowKind.ROLLING_7D, date_from=date(2026, 9, 16), date_to=date(2026, 9, 22),
                        timezone="America/Sao_Paulo", days=7)
    envelope = DashboardEnvelope(window=window, metric_versions={"physical_sets": "sets-v1"},
                                 data_quality=DataQuality.MEDIUM, limitations=["sem meta configurada"])
    payload = json.loads(envelope.model_dump_json())
    assert payload["window"] == {
        "kind": "7d", "date_from": "2026-09-16", "date_to": "2026-09-22", "timezone": "America/Sao_Paulo", "days": 7,
    }
    assert payload["metric_versions"] == {"physical_sets": "sets-v1"}
    assert payload["data_quality"] == "MEDIUM"


def test_window_info_rejeita_days_zero_e_kind_desconhecida():
    with pytest.raises(ValidationError):
        WindowInfo(kind="7d", date_from=date(2026, 9, 22), date_to=date(2026, 9, 22), timezone="UTC", days=0)
    with pytest.raises(ValidationError):
        WindowInfo(kind="fortnight", date_from=date(2026, 9, 22), date_to=date(2026, 9, 22), timezone="UTC", days=1)

"""Um `build_overview` SINTETICO, para testar o pacote de inteligencia (F3.2)
sem banco.

Por que existe: `intelligence_package.package_from_overview` e uma funcao
pura sobre o dicionario que `build_overview` devolve. Testar o achatamento, o
teto de 40 KB e a ordem de truncamento contra um MySQL seria caro e, pior,
lento demais para ser exercitado com variacoes (pacote minusculo, pacote que
estoura, pacote sem evidencia nenhuma). Aqui o dicionario e montado a mao, no
MESMO formato dos servicos reais - os campos foram copiados de
`overview_service.metric()`, `context_service._event()` e do contrato
`Insight`.

Isto NAO substitui o teste de integracao: `tests/integration/dataapi/
test_dashboard_intelligence_package.py` continua provando que o `build_overview`
de verdade produz um pacote valido e que ele custa os mesmos statements do
overview. O que vive aqui e a variacao de FORMA, que so o dublê torna barata.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.intelligence.dashboard.contracts import (
    DataNature,
    DataQuality,
    Severity,
    TargetSource,
    WindowInfo,
    WindowKind,
)

JANELA = WindowInfo(
    kind=WindowKind.ROLLING_28D,
    date_from=date(2026, 8, 30),
    date_to=date(2026, 9, 26),
    timezone="America/Sao_Paulo",
    days=28,
)


def metric(value, *, unit=None, nature=DataNature.DERIVED, version="v1", quality=DataQuality.HIGH):
    return {
        "value": value,
        "unit": unit,
        "nature": nature,
        "metric_version": version,
        "data_quality": quality,
        "limitations": [],
    }


def evidencia(metrica: str, de, para, *, unit=None, quando: date | None = None, datada: bool = True):
    """Uma evidencia como `insight_rules._evidence` a produz.

    `datada=False` reproduz as 12 (de 26) chamadas de `_evidence` que NAO
    informam `observed_at` - os agregados de janela e as constantes de plano.
    Elas existem no produto e o pacote precisa saber o que fazer com elas; sem
    este cenario, o dublê seria mais bem-comportado que a realidade, que e
    exatamente como o defeito do #241 passou pelo teste de unidade e so
    apareceu na integracao."""
    return {
        "metric": metrica,
        "from_value": de,
        "to_value": para,
        "unit": unit,
        "nature": DataNature.OBSERVED,
        "observed_at": (quando or date(2026, 9, 20)) if datada else None,
    }


def insight(code: str, *, severity=Severity.ATTENTION, evidence=None):
    return {
        "code": code,
        "question": "?",
        "status": "TRIGGERED",
        "severity": severity,
        "answer": "…",
        "evidence": list(evidence or []),
        "comparison": None,
        "data_quality": DataQuality.HIGH,
        "limitations": [],
        "alternative_explanations": [],
        "suggested_action": None,
        "reassess_after": None,
        "source_screen": "/dashboard",
        "fingerprint": f"fp-{code}",
        "cooldown": None,
    }


def evento(event_type: str, dia: date, *, notes: str | None = None, source: str = "manual"):
    return {
        "id": 1 if source == "manual" else None,
        "event_type": event_type,
        "event_type_label": event_type.replace("_", " ").title(),
        "local_date": dia,
        "end_local_date": None,
        "intensity": None,
        "notes": notes,
        "source": source,
        "derived": source != "manual",
        "editable": source == "manual",
    }


def grupo(group_id: int, nome: str, *, series=12.0, status="WITHIN_TARGET", sessoes=2.0, horas=30.0):
    return {
        "muscle_group_id": group_id,
        "muscle_group": nome,
        "status": status,
        "targets": {
            "min": metric(10.0, unit="sets", nature=DataNature.PLANNED),
            "max": metric(20.0, unit="sets", nature=DataNature.PLANNED),
            "target": metric(15.0, unit="sets", nature=DataNature.PLANNED),
            "source": TargetSource.ASSIGNED_TEMPLATE,
        },
        "realized": {
            "weekly_effective_sets": metric(series, unit="sets"),
            "physical_sets": metric(series, unit="sets"),
            "direct_sets": metric(series, unit="sets"),
            "indirect_sets": metric(0.0, unit="sets"),
            "effective_sets_stimulus": metric(series, unit="sets"),
        },
        "frequency": {"direct_sessions": metric(sessoes, unit="sessions")},
        "hours_since_last_stimulus": metric(horas, unit="hours"),
        "last_direct_local_date": date(2026, 9, 24),
    }


def _bloco(pergunta: str, **extra) -> dict[str, Any]:
    base = {
        "question": pergunta,
        "answer": "…",
        "drilldown_href": "/dashboard",
        "data_quality": DataQuality.HIGH,
        "limitations": [],
    }
    base.update(extra)
    return base


def overview_sintetico(
    *,
    insights: list[dict[str, Any]] | None = None,
    context_events: list[dict[str, Any]] | None = None,
    muscle_items: list[dict[str, Any]] | None = None,
    context_available: bool = True,
    context_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """O dicionario que `build_overview` devolve, no mesmo formato, com os
    blocos preenchidos com valores plausiveis."""
    grupos = muscle_items if muscle_items is not None else [
        grupo(1, "Peito", series=8.0, status="BELOW_MIN"),
        grupo(2, "Costas", series=18.0),
        grupo(3, "Perna", series=22.0, status="ABOVE_MAX"),
    ]
    return {
        "window": JANELA,
        "timezone": JANELA.timezone,
        "metric_versions": {
            "adherence": "adherence-v2",
            "progression_summary": "progression-summary-v1",
            "exercise_progression_status": "exercise-progression-v1",
            "weekly_volume_status": "weekly-volume-v1",
            "body_weight_ma7": "weight-ma-v1",
            "recovery_signals": "recovery-signals-v1",
            "data_quality": "data-quality-v1",
            "insight_rules": "insight-rules-v1",
            "alert_cooldown": "alert-cooldown-v1",
        },
        "data_quality": DataQuality.MEDIUM,
        "limitations": ["Cada bloco carrega a própria confiança e limitações."],
        "blocks": {
            "consistency": _bloco(
                "Cumpri o planejado?",
                has_template=True,
                template_name="ABC",
                cycle_length_days=21,
                next_slot=None,
                cycle_slots_done=2,
                cycle_slots_total=3,
                expected_sessions=metric(12.0, unit="sessions"),
                completed_sessions=metric(9.0, unit="sessions"),
                planned_sessions=metric(8.0, unit="sessions"),
                unplanned_sessions=metric(1.0, unit="sessions"),
                missed_sessions=metric(3.0, unit="sessions"),
                rate=metric(0.75, unit="ratio"),
                cycle_rate=metric(0.67, unit="ratio"),
            ),
            "progression": _bloco(
                "Estou evoluindo?",
                exercises_total=24,
                comparable_total=18,
                counts=[
                    {"status": "PROGRESSED", "label": "Progrediu", "count": 7, "exercises": []},
                    {"status": "STAGNANT", "label": "Estagnou", "count": 9, "exercises": []},
                    {"status": "REGRESSED", "label": "Regrediu", "count": 2, "exercises": []},
                ],
                exposures_per_exercise=5,
                truncated=False,
            ),
            "priority_muscles": _bloco(
                "Quais grupos estão atrasados ou em excesso?",
                groups_total=len(grupos),
                groups_with_target=len(grupos),
                target_source=TargetSource.ASSIGNED_TEMPLATE,
                below_min=[_priority(g) for g in grupos if g["status"] == "BELOW_MIN"],
                above_max=[_priority(g) for g in grupos if g["status"] == "ABOVE_MAX"],
                frequency_lagging=[],
            ),
            "body": _bloco(
                "Como está o corpo?",
                weight_latest=metric(81.2, unit="kg", nature=DataNature.OBSERVED),
                weight_ma7_latest=metric(81.0, unit="kg"),
                weight_ma7_change=metric(-0.6, unit="kg"),
                weight_ma7_change_pct=metric(-0.73, unit="%"),
                weighings_count=14,
                weighings_per_week=metric(3.5, unit="weighings/week"),
                weight_points=[],
                body_fat_percent=metric(18.4, unit="%", nature=DataNature.ESTIMATED),
                muscle_mass_kg=metric(36.1, unit="kg", nature=DataNature.ESTIMATED),
                bioimpedance_local_date=date(2026, 9, 21),
                waist_cm=metric(None, unit="cm", quality=DataQuality.INSUFFICIENT),
            ),
            "recovery": _bloco(
                "Estou recuperando bem?",
                window=JANELA,
                level=Severity.INFO,
                readiness_reading="READY",
                headline="…",
                uncertainty=DataQuality.MEDIUM,
                allow_intensification=True,
                intensification_blocked_reason=None,
                groups_total=3,
                groups_ready=2,
                groups_reduced_dose=1,
                groups_no_direct_work=0,
                signals_agreeing=3,
                signals_diverging=1,
                signals_unavailable=2,
                reassess_after_hours=24.0,
            ),
            "next_actions": _bloco("Próxima ação", items=[], total_triggered=0, max_items=2),
        },
        "data_quality_overall": DataQuality.MEDIUM,
        "data_quality_issues": 4,
        "target_source": TargetSource.ASSIGNED_TEMPLATE,
        "training_type_id": 3,
        "_insights": insights if insights is not None else [
            insight(
                "MUSCLE_BELOW_MIN",
                evidence=[
                    evidencia("weekly_effective_sets", 12.0, 8.0, unit="sets"),
                    # Agregado de janela e constante de plano: sem `observed_at`,
                    # como `insight_rules` realmente as emite.
                    evidencia("weekly_effective_sets[Peito]", 12.0, 8.0, unit="sets", datada=False),
                    evidencia("target_min_weekly_sets", 10.0, 10.0, unit="sets", datada=False),
                ],
            ),
            insight(
                "EXERCISE_STAGNANT",
                severity=Severity.ACTION,
                evidence=[evidencia("e1rm", 100.0, 99.0, unit="kg", quando=date(2026, 9, 18))],
            ),
        ],
        "_suppressed_insights": [],
        "_cooldown_available": True,
        "_cooldown_limitations": [],
        "_rule_counts": {},
        "_timings": {},
        "_context_events": context_events if context_events is not None else [
            evento("alcohol", date(2026, 9, 12), notes="final de semana"),
            evento("plan_change", date(2026, 9, 1), source="derived"),
        ],
        "_context_events_available": context_available,
        "_context_limitations": list(context_limitations or []),
        "_muscle_items": grupos,
        "_training_gap": {
            "days_since_last_session": 2,
            "last_session_local_date": date(2026, 9, 24),
            "sessions_before": 9,
        },
    }


def _priority(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "muscle_group_id": g["muscle_group_id"],
        "muscle_group": g["muscle_group"],
        "status": g["status"],
        "weekly_effective_sets": g["realized"]["weekly_effective_sets"],
        "target_min": g["targets"]["min"],
        "target_max": g["targets"]["max"],
        "target_source": g["targets"]["source"],
        "direct_sets": g["realized"]["direct_sets"],
        "indirect_sets": g["realized"]["indirect_sets"],
        "direct_sessions": g["frequency"]["direct_sessions"],
        "hours_since_last_stimulus": g["hours_since_last_stimulus"],
        "last_direct_local_date": g["last_direct_local_date"],
    }


__all__ = [
    "JANELA",
    "evento",
    "evidencia",
    "grupo",
    "insight",
    "metric",
    "overview_sintetico",
]

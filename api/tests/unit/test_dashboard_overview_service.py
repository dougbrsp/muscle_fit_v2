"""Blocos da visao geral (`overview_service`, F1.6) e o caminho em lote de
progressao (`exposure_service.classify_batch`) - tudo sem banco.

O que cada bloco protege:

1. **Corpo**: media movel de 7 dias so sobre dias COM pesagem (dia sem
   pesagem NUNCA vira zero), prioridade por dia entre as tres fontes e a
   limitacao explicita da cintura (F1.4).
2. **Progressao**: contagem por status decomponivel, sem score; lote ==
   individual (o MESMO `classify_exercise_progression`).
3. **Proxima acao**: no maximo 2 itens; estado positivo real quando nada
   dispara (nao inventa sugestao).
4. **Musculos prioritarios / consistencia / recuperacao**: cada bloco
   responde uma pergunta, aponta o drill-down e carrega a propria qualidade.
5. **Ownership**: `require_user_id` recusa `None` antes de qualquer SQL.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.intelligence.dashboard import overview_service as ov
from app.intelligence.dashboard.comparability import classify_exercise_progression
from app.intelligence.dashboard.contracts import (
    BodyBlockOut,
    ConsistencyBlockOut,
    DataNature,
    DataQuality,
    NextActionsBlockOut,
    PriorityMusclesBlockOut,
    ProgressionBlockOut,
    RecoveryBlockOut,
    Severity,
    TargetSource,
    WindowInfo,
    WindowKind,
)
from app.intelligence.dashboard.exposure_service import (
    assemble_exposures,
    build_exposures,
    classify_batch,
    get_exercises_progression_batch,
    split_batch_rows,
    to_summaries,
)
from app.intelligence.dashboard.insight_rules import WAIST_UNAVAILABLE_LIMITATION
from app.intelligence.dashboard.metrics_registry import THRESHOLDS, ExerciseProgressionStatus

DATE_TO = date(2026, 9, 22)


def _window(days: int = 28) -> WindowInfo:
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=DATE_TO - timedelta(days=days - 1),
        date_to=DATE_TO,
        timezone="America/Sao_Paulo",
        days=days,
    )


# ---------------------------------------------------------------------------
# 1) Corpo - peso com media movel
# ---------------------------------------------------------------------------


def _weight_row(day: date, kg: float, prio: int, fonte: str):
    return {"local_date": day, "weight_kg": str(kg), "prio": prio, "fonte": fonte}


def test_prioridade_por_dia_entre_as_tres_fontes():
    dia = DATE_TO
    rows = [
        _weight_row(dia, 81.0, 3, "intervals_wellness"),
        _weight_row(dia, 80.0, 1, "body_composition"),
        _weight_row(dia, 80.5, 2, "daily_biometrics"),
        _weight_row(dia - timedelta(days=1), 79.0, 2, "daily_biometrics"),
    ]
    diario = ov.pick_daily_weights(rows)
    assert [(p["local_date"], p["weight_kg"], p["source"]) for p in diario] == [
        (dia - timedelta(days=1), 79.0, "daily_biometrics"),
        (dia, 80.0, "body_composition"),
    ]


def test_media_movel_ignora_dias_sem_pesagem_e_nunca_zera():
    """Tres pesagens em 7 dias: a media e das TRES, nao de 7 dias com zeros."""
    diario = [
        {"local_date": DATE_TO - timedelta(days=6), "weight_kg": 80.0, "source": "s"},
        {"local_date": DATE_TO - timedelta(days=3), "weight_kg": 82.0, "source": "s"},
        {"local_date": DATE_TO, "weight_kg": 81.0, "source": "s"},
    ]
    pontos = ov.moving_average_points(diario, ma_days=7, date_from=DATE_TO - timedelta(days=27), date_to=DATE_TO)
    assert [p["ma7_kg"] for p in pontos] == [80.0, 81.0, 81.0]
    assert [p["ma7_points"] for p in pontos] == [1, 2, 3]
    # Pesagem de 8 dias atras sai da janela da media do ultimo ponto.
    diario.insert(0, {"local_date": DATE_TO - timedelta(days=8), "weight_kg": 50.0, "source": "s"})
    pontos = ov.moving_average_points(diario, ma_days=7, date_from=DATE_TO - timedelta(days=27), date_to=DATE_TO)
    assert pontos[-1]["ma7_kg"] == 81.0
    # Pontos fora de [date_from, date_to] nao aparecem (so alimentam a media).
    assert all(p["local_date"] >= DATE_TO - timedelta(days=27) for p in pontos)


def test_bloco_corpo_sem_pesagem_nao_vira_zero():
    bloco = ov.body_block(weight_rows=[], bioimpedance=None, window=_window())
    BodyBlockOut.model_validate({k: v for k, v in bloco.items() if not k.startswith("_")})
    assert bloco["data_quality"] is DataQuality.INSUFFICIENT
    assert bloco["weight_latest"]["value"] is None
    assert bloco["weight_ma7_latest"]["value"] is None
    assert bloco["weighings_count"] == 0
    assert bloco["weight_points"] == []
    assert bloco["waist_cm"]["value"] is None
    assert WAIST_UNAVAILABLE_LIMITATION in bloco["limitations"]
    assert bloco["drilldown_href"] == "/dashboard/corpo"


def test_bloco_corpo_com_pesagens_e_bioimpedancia():
    rows = [
        _weight_row(DATE_TO - timedelta(days=d), 80.0 + (0.1 * (7 - d)), 1, "body_composition") for d in range(0, 21, 1)
    ]
    bio = {"local_date": DATE_TO, "weight_kg": "80.7", "body_fat_percent": "0.21", "muscle_mass_kg": "38.2"}
    bloco = ov.body_block(weight_rows=rows, bioimpedance=bio, window=_window())
    publico = {k: v for k, v in bloco.items() if not k.startswith("_")}
    BodyBlockOut.model_validate(publico)
    assert bloco["data_quality"] is DataQuality.HIGH  # 21 pesagens em 28 dias >= 4/sem
    assert bloco["weighings_count"] == 21
    assert bloco["weight_ma7_latest"]["nature"] is DataNature.DERIVED
    assert bloco["body_fat_percent"]["value"] == 21.0  # fracao normalizada para percentual
    assert bloco["body_fat_percent"]["nature"] is DataNature.ESTIMATED
    assert bloco["muscle_mass_kg"]["value"] == 38.2
    assert bloco["_span_days"] == 20
    assert bloco["weight_ma7_change"]["value"] is not None


def test_bloco_corpo_declara_fonte_do_intervals_ausente():
    bloco = ov.body_block(weight_rows=[], bioimpedance=None, window=_window(), extra_limitations=["Intervals indisponível"])
    assert "Intervals indisponível" in bloco["limitations"]


# ---------------------------------------------------------------------------
# 2) Progressao - lote == individual, contagem sem score
# ---------------------------------------------------------------------------


def _batch_rows(canonical: int, name: str, exposures: list[tuple[int, date, int, float, float, float]]):
    """`exposures`: (exercise_row_id, dia, activity_id, carga, reps, rpe) com 3 series iguais."""
    rows = []
    for row_id, dia, activity_id, carga, reps, rpe in exposures:
        for numero in (1, 2, 3):
            rows.append(
                {
                    "canonical_exercise_id": canonical,
                    "exercise_name": name,
                    "exercise_row_id": row_id,
                    "activity_id": activity_id,
                    "local_date": dia,
                    "started_at": datetime.combine(dia, datetime.min.time()),
                    "position_in_session": 1,
                    "equipment_id": 7,
                    "equipment_name": None,
                    "load_convention": None,
                    "prescribed_reps_target": "8-12",
                    "set_number": numero,
                    "reps": reps,
                    "weight_kg": str(carga),
                    "rpe": str(rpe),
                    "rest_seconds": None,
                    "notes": None,
                }
            )
    return rows


def _serie(canonical: int, name: str, cargas: list[float], reps: int = 10):
    exposures = []
    for indice, carga in enumerate(cargas):
        dia = DATE_TO - timedelta(days=(len(cargas) - indice) * 3)
        exposures.append((canonical * 100 + indice, dia, 1000 + indice, carga, reps, 8.0))
    return _batch_rows(canonical, name, exposures)


def test_lote_classifica_igual_ao_individual():
    rows = _serie(1, "Supino", [60, 62, 64, 66]) + _serie(2, "Remada", [50, 50, 50, 50]) + _serie(3, "Rosca", [20])
    itens, truncated = classify_batch(rows, per_exercise_limit=8)
    assert not truncated
    por_id = {item["exercise_id"]: item for item in itens}
    assert por_id[1]["status"] == ExerciseProgressionStatus.PROGRESSING.value
    assert por_id[2]["status"] == ExerciseProgressionStatus.PLATEAU_SUSPECTED.value
    assert por_id[3]["status"] == ExerciseProgressionStatus.INSUFFICIENT_DATA.value
    assert por_id[2]["comparable_exposures"] == 4

    # Mesmo `classify_exercise_progression`, exercicio a exercicio.
    por_exercicio, nomes = split_batch_rows(rows)
    for canonical, linhas in por_exercicio.items():
        exposures = assemble_exposures(build_exposures(linhas), canonical)
        status, reasons = classify_exercise_progression(to_summaries(exposures))
        assert por_id[canonical]["status"] == status.value
        assert por_id[canonical]["status_reasons"] == reasons
        assert por_id[canonical]["name"] == nomes[canonical]


def test_split_ignora_linhas_sem_exercicio_canonico():
    rows = _serie(1, "Supino", [60, 62])
    rows.append({**rows[0], "canonical_exercise_id": None})
    por_exercicio, _ = split_batch_rows(rows)
    assert set(por_exercicio) == {1}


def test_lote_exige_user_id():
    with pytest.raises(ValueError):
        get_exercises_progression_batch(object(), user_id=None, window=_window())


def test_bloco_progressao_conta_por_status_sem_score():
    rows = _serie(1, "Supino", [60, 62, 64, 66]) + _serie(2, "Remada", [50, 50, 50, 50]) + _serie(3, "Rosca", [20])
    itens, _ = classify_batch(rows, per_exercise_limit=8)
    bloco = ov.progression_block(itens, truncated=False, exposures_per_exercise=8, window=_window())
    ProgressionBlockOut.model_validate(bloco)
    assert bloco["exercises_total"] == 3
    assert bloco["comparable_total"] == 2
    contagens = {c["status"]: c for c in bloco["counts"]}
    assert set(contagens) == {s.value for s in ExerciseProgressionStatus}
    assert contagens["PROGRESSING"]["count"] == 1
    assert contagens["PROGRESSING"]["exercises"][0]["exercise_id"] == 1
    assert contagens["PLATEAU_SUSPECTED"]["count"] == 1
    assert contagens["INSUFFICIENT_DATA"]["count"] == 1
    assert "score" not in bloco
    assert "1 de 2" in bloco["answer"]
    assert bloco["drilldown_href"] == "/dashboard/progressao"


def test_bloco_progressao_lista_todos_os_exercicios_do_status():
    rows = []
    for exercise_id in range(1, 15):
        rows += _serie(exercise_id, f"Exercicio {exercise_id:02d}", [60, 62, 64, 66])
    itens, _ = classify_batch(rows, per_exercise_limit=8)
    bloco = ov.progression_block(itens, truncated=False, exposures_per_exercise=8, window=_window())
    ProgressionBlockOut.model_validate(bloco)
    progredindo = next(c for c in bloco["counts"] if c["status"] == "PROGRESSING")
    assert progredindo["count"] == 14
    assert len(progredindo["exercises"]) == 14
    assert sorted(e["exercise_id"] for e in progredindo["exercises"]) == list(range(1, 15))


def test_bloco_progressao_vazio_e_insufficient():
    bloco = ov.progression_block([], truncated=False, exposures_per_exercise=8, window=_window())
    assert bloco["data_quality"] is DataQuality.INSUFFICIENT
    assert all(c["count"] == 0 for c in bloco["counts"])


def test_bloco_progressao_declara_o_corte_do_lote():
    itens, _ = classify_batch(_serie(1, "Supino", [60, 62, 64]), per_exercise_limit=8)
    bloco = ov.progression_block(itens, truncated=True, exposures_per_exercise=8, window=_window())
    assert bloco["truncated"] is True
    assert any("1 MB" in item for item in bloco["limitations"])


# ---------------------------------------------------------------------------
# 3) Proxima acao
# ---------------------------------------------------------------------------


def _insight(code: str, severity: Severity = Severity.INFO):
    return {
        "code": code,
        "question": "?",
        "status": "X",
        "severity": severity,
        "answer": "a",
        "evidence": [],
        "comparison": None,
        "data_quality": DataQuality.HIGH,
        "limitations": [],
        "alternative_explanations": [],
        "suggested_action": None,
        "reassess_after": None,
        "source_screen": "/dashboard",
        "fingerprint": f"{code}:",
    }


def test_next_actions_maximo_dois_e_total_declarado():
    insights = [_insight(f"R{i}") for i in range(5)]
    bloco = ov.next_actions_block(insights, max_items=THRESHOLDS["overview_max_next_actions"])
    NextActionsBlockOut.model_validate(bloco)
    assert len(bloco["items"]) == 2
    assert bloco["total_triggered"] == 5
    assert bloco["max_items"] == 2


def test_next_actions_vazio_e_estado_positivo_real():
    bloco = ov.next_actions_block([], max_items=2)
    NextActionsBlockOut.model_validate(bloco)
    assert bloco["items"] == []
    assert bloco["total_triggered"] == 0
    assert "Nenhuma ação sugerida agora" in bloco["answer"]
    assert bloco["data_quality"] is DataQuality.HIGH


# ---------------------------------------------------------------------------
# 4) Consistencia, musculos prioritarios, recuperacao
# ---------------------------------------------------------------------------


def _metric(value, unit=None, nature=DataNature.OBSERVED, quality=DataQuality.HIGH, limitations=()):
    return {"value": value, "unit": unit, "nature": nature, "metric_version": "adherence-v2", "data_quality": quality, "limitations": list(limitations)}


def _adherence(has_template=True, rate=0.75):
    slots = [
        {"sequence": 1, "name": "A", "slot_type": "training", "weekday": None, "status": "done", "last_activity_id": 1, "last_local_date": DATE_TO},
        {"sequence": 2, "name": "B", "slot_type": "training", "weekday": None, "status": "next", "last_activity_id": None, "last_local_date": None},
        {"sequence": 3, "name": "C", "slot_type": "training", "weekday": None, "status": "pending", "last_activity_id": None, "last_local_date": None},
    ]
    return {
        "data_quality": DataQuality.HIGH if has_template else DataQuality.LOW,
        "limitations": [] if has_template else ["Sem Modelo atribuído."],
        "has_template": has_template,
        "template_name": "PPL" if has_template else None,
        "cycle_length_days": 21 if has_template else None,
        "slots": slots if has_template else [],
        "next_slot": slots[1] if has_template else None,
        "expected_sessions": _metric(8.0 if has_template else None, "sessoes", DataNature.PLANNED),
        "completed_sessions": _metric(7.0, "sessoes"),
        "planned_sessions": _metric(6.0, "sessoes"),
        "unplanned_sessions": _metric(1.0, "sessoes"),
        "missed_sessions": _metric(2.0 if has_template else None, "sessoes", DataNature.DERIVED),
        "rate": _metric(rate if has_template else None, None, DataNature.DERIVED),
        "cycle_rate": _metric(0.33 if has_template else None, None, DataNature.DERIVED),
    }


def test_bloco_consistencia_responde_com_ciclo_e_proximo_slot():
    bloco = ov.consistency_block(_adherence())
    ConsistencyBlockOut.model_validate(bloco)
    assert bloco["cycle_slots_done"] == 1 and bloco["cycle_slots_total"] == 3
    assert "75%" in bloco["answer"] and "próximo: B" in bloco["answer"]
    assert bloco["drilldown_href"] == "/dashboard/treinos"


def test_bloco_consistencia_sem_modelo_nao_inventa_denominador():
    bloco = ov.consistency_block(_adherence(has_template=False))
    ConsistencyBlockOut.model_validate(bloco)
    assert bloco["rate"]["value"] is None
    assert bloco["data_quality"] is DataQuality.LOW
    assert "sem Modelo atribuído" in bloco["answer"]


def _m(value, nature=DataNature.DERIVED):
    return {"value": value, "unit": None, "nature": nature, "metric_version": "v", "data_quality": DataQuality.HIGH, "limitations": []}


def _group(group_id, name, *, status, weekly, source=TargetSource.ASSIGNED_TEMPLATE, hours=24.0, physical=10.0):
    return {
        "muscle_group_id": group_id,
        "muscle_group": name,
        "status": status,
        "targets": {"min": _m(8.0, DataNature.PLANNED), "target": _m(12.0, DataNature.PLANNED), "max": _m(16.0, DataNature.PLANNED), "source": source},
        "realized": {"physical_sets": _m(physical, DataNature.OBSERVED), "weekly_effective_sets": _m(weekly), "direct_sets": _m(5.0, DataNature.OBSERVED), "indirect_sets": _m(2.0, DataNature.OBSERVED)},
        "frequency": {"direct_sessions": _m(2.0)},
        "hours_since_last_stimulus": _m(hours),
        "last_direct_local_date": DATE_TO,
        "data_quality": DataQuality.HIGH,
        "limitations": [],
    }


def test_bloco_musculos_prioritarios_separa_abaixo_acima_e_atrasados():
    muscles = {
        "items": [
            _group(1, "Peito", status="BELOW_MIN", weekly=4.0),
            _group(2, "Costas", status="ABOVE_MAX", weekly=20.0),
            _group(3, "Pernas", status="ON_TARGET", weekly=12.0, hours=200.0),
            _group(4, "Ombro", status="ON_TARGET", weekly=12.0, source=TargetSource.FALLBACK_GENERIC),
        ],
        "target_source": TargetSource.ASSIGNED_TEMPLATE,
        "data_quality": DataQuality.HIGH,
        "limitations": [],
    }
    bloco = ov.priority_muscles_block(muscles, window=_window())
    PriorityMusclesBlockOut.model_validate(bloco)
    assert bloco["groups_total"] == 4 and bloco["groups_with_target"] == 3
    assert [g["muscle_group"] for g in bloco["below_min"]] == ["Peito"]
    assert [g["muscle_group"] for g in bloco["above_max"]] == ["Costas"]
    assert [g["muscle_group"] for g in bloco["frequency_lagging"]] == ["Pernas"]
    assert "1 abaixo do mínimo" in bloco["answer"] and "1 acima do máximo" in bloco["answer"]


def test_bloco_musculos_sem_meta_e_low_e_diz_por_que():
    muscles = {"items": [_group(4, "Ombro", status="ON_TARGET", weekly=12.0, source=TargetSource.FALLBACK_GENERIC)], "target_source": TargetSource.FALLBACK_GENERIC, "data_quality": DataQuality.LOW, "limitations": []}
    bloco = ov.priority_muscles_block(muscles, window=_window())
    assert bloco["data_quality"] is DataQuality.LOW
    assert bloco["below_min"] == [] and bloco["above_max"] == []
    assert "Sem meta cadastrada" in bloco["answer"]


def test_bloco_recuperacao_conta_grupos_e_sinais():
    recovery = {
        "data_quality": DataQuality.MEDIUM,
        "limitations": ["x"],
        "groups": [
            {"muscle_group_id": 1, "readiness_status": "READY"},
            {"muscle_group_id": 2, "readiness_status": "REDUCED_DOSE"},
            {"muscle_group_id": 3, "readiness_status": "NO_DIRECT_WORK"},
        ],
        "assessment": {
            "level": Severity.ATTENTION,
            "readiness_reading": "STRAINED",
            "headline": "Dois sinais negativos concordam.",
            "uncertainty": DataQuality.MEDIUM,
            "allow_intensification": False,
            "intensification_blocked_reason": "dor articular",
            "agreeing_codes": ["hrv", "resting_hr"],
            "diverging_codes": ["sleep_duration"],
            "unavailable_codes": ["form_tsb"],
            "reassess_after_hours": 36.0,
        },
    }
    bloco = ov.recovery_block(recovery, window=_window(7))
    RecoveryBlockOut.model_validate(bloco)
    assert (bloco["groups_ready"], bloco["groups_reduced_dose"], bloco["groups_no_direct_work"]) == (1, 1, 1)
    assert (bloco["signals_agreeing"], bloco["signals_diverging"], bloco["signals_unavailable"]) == (2, 1, 1)
    assert bloco["answer"] == "Dois sinais negativos concordam."
    assert bloco["drilldown_href"] == "/dashboard/recovery"


def test_worst_quality():
    assert ov.worst_quality(DataQuality.HIGH, "MEDIUM", DataQuality.LOW) is DataQuality.LOW
    assert ov.worst_quality() is DataQuality.HIGH


# ---------------------------------------------------------------------------
# 5) Itens pulados (reusa `build_session_items`) e ownership
# ---------------------------------------------------------------------------


def test_skipped_items_conta_por_item_da_ficha():
    plano = [
        {"plan_id": 10, "plan_exercise_id": 101, "plan_sequence": 1, "exercise_name": "Supino", "mapping_id": 1, "sets_count": 3},
        {"plan_id": 10, "plan_exercise_id": 102, "plan_sequence": 2, "exercise_name": "Panturrilha", "mapping_id": 2, "sets_count": 3},
    ]
    sessoes = [{"activity_id": a, "local_date": DATE_TO - timedelta(days=i), "workout_plan_id": 10} for i, a in enumerate((1, 2, 3))]
    executado = {
        1: [{"activity_id": 1, "exercise_row_id": 1, "position_in_session": 1, "original_name": "Supino", "workout_plan_exercise_id": 101, "exercise_id": 1, "total_sets": 3, "working_sets": 3}],
        2: [{"activity_id": 2, "exercise_row_id": 2, "position_in_session": 1, "original_name": "Supino", "workout_plan_exercise_id": 101, "exercise_id": 1, "total_sets": 3, "working_sets": 3}],
        3: [
            {"activity_id": 3, "exercise_row_id": 3, "position_in_session": 1, "original_name": "Supino", "workout_plan_exercise_id": 101, "exercise_id": 1, "total_sets": 3, "working_sets": 3},
            {"activity_id": 3, "exercise_row_id": 4, "position_in_session": 2, "original_name": "Panturrilha", "workout_plan_exercise_id": 102, "exercise_id": 2, "total_sets": 3, "working_sets": 3},
        ],
    }
    itens = ov.skipped_items_from_sessions(sessoes, {10: plano}, executado)
    por_id = {i["plan_exercise_id"]: i for i in itens}
    assert por_id[102]["skipped"] == 2 and por_id[102]["sessions"] == 3
    assert por_id[102]["plan_sequence"] == 2 and por_id[102]["plan_items_total"] == 2
    assert por_id[101]["skipped"] == 0
    assert itens[0]["plan_exercise_id"] == 102  # mais pulado primeiro


def test_overview_exige_user_id():
    with pytest.raises(ValueError):
        ov.build_overview(object(), user_id=None, window=_window(), now_local=datetime(2026, 9, 22, 12))
    with pytest.raises(ValueError):
        ov.build_insights(object(), user_id=0, window=_window(), now_local=datetime(2026, 9, 22, 12))

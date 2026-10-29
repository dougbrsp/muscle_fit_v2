"""Progressao por exercicio com UMA fonte de verdade (M74 e alinhamentos).

`comparability.classify_exercise_progression` (via `exposure_service`) e a
unica regra de progresso por exercicio. Estes testes provam que os outros
consumidores passaram a usa-la:

- contexto da "Analise por IA" (`progression_service`, `report_ai`): leva o
  status pronto e o prompt proibe a IA de inferir progresso;
- drill-down: reps emparelhadas nas series de topo e e1RM decisivo ou nao;
- `/dashboard/progressao`: sessoes marcadas com a chave do veredito, carga de
  topo da exposicao e e1RM > 12 reps marcado como pouco confiavel.

Sem banco: as linhas planas sao as mesmas das queries do `exposure_service`.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import analytics, load_conventions, report_ai
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard.contracts import ExerciseProgressResponse
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus
from app.intelligence.progression_service import RECENT_RECORDS_DAYS, exercise_progression_context
from tests.unit.test_progressao_tela_mesma_regra import (
    EXERCICIO,
    JANELA,
    SMITH,
    SQUAT_RACK,
    USUARIO,
    FakeDb,
    agachamento_real,
    exposicao,
)

NOT_COMPARABLE = ExerciseProgressionStatus.NOT_COMPARABLE.value


@pytest.fixture(autouse=True)
def _sem_cache_de_coluna():
    load_conventions.reset_load_convention_column_cache()
    yield
    load_conventions.reset_load_convention_column_cache()


def _d(dia: int) -> date:
    return date(2026, 9, dia)


def _plato() -> list[dict]:
    return (
        exposicao(1, 1, _d(10), [(10, 60.0, 8.0)] * 3)
        + exposicao(2, 2, _d(14), [(10, 60.0, 8.0)] * 3)
        + exposicao(3, 3, _d(18), [(10, 60.0, 8.0)] * 2)
        + exposicao(4, 4, _d(22), [(10, 60.0, 8.0)] * 3)
    )


def _plato_e_troca_de_maquina() -> list[dict]:
    """O mesmo plato, mas a ultima sessao foi em OUTRA maquina, com menos
    carga - a regra antiga (media de e1RM sem chave) chamaria isso de
    regressao/estagnacao."""
    return _plato() + exposicao(5, 5, _d(26), [(10, 40.0, 8.0)] * 3, equipment_id=SMITH)


def _lote(rows) -> list[dict]:
    itens, _truncated = svc.classify_batch(rows, per_exercise_limit=svc.MAX_BATCH_EXPOSURES_PER_EXERCISE)
    return itens


# ---------------------------------------------------------------------------
# M74 - regra antiga de tendencia removida (o alerta de estagnacao que a
# consumia saiu com `dashboard_alert_service` no M76 Lote 2c)
# ---------------------------------------------------------------------------


def test_m74_classify_progression_trend_foi_removida():
    from app.intelligence import progression_service

    assert not hasattr(progression_service, "classify_progression_trend")


# ---------------------------------------------------------------------------
# Contexto da IA de evolucao
# ---------------------------------------------------------------------------


def test_contexto_da_ia_leva_o_status_da_regra_unica():
    (item,) = exercise_progression_context(_lote(agachamento_real()), {SQUAT_RACK: "Gaiola"})

    assert item["status"] == ExerciseProgressionStatus.PROGRESSING.value
    assert item["status_label"]
    assert "same_load_more_reps" in item["status_reasons"]
    assert item["equipment_name"] == "Gaiola"
    assert item["load_convention"] == "total"
    assert item["latest"]["local_date"] == "2026-10-06"
    assert item["latest"]["best_load_kg"] == 50.0
    assert item["latest"]["top_set_reps"] == [14, 13]
    assert item["latest"]["e1rm_reliable"] is False  # serie do e1RM com 14 reps
    assert item["previous_comparable"]["local_date"] == "2026-10-02"
    assert item["previous_comparable"]["e1rm_reliable"] is True
    # M78: RPE em todas as series emparelhadas -> cada uma vale reps + RIR
    # (11@9 -> 12, 10@9 -> 11; 14@9,5 -> 14,5, 13@9 -> 14).
    assert item["paired_top_reps"] == {
        "pairs": 2,
        "previous": [12, 11],
        "current": [14.5, 14],
        "previous_sum": 23,
        "current_sum": 28.5,
        "delta": 5.5,
        "effort_adjusted": True,
        "previous_reps": [11, 10],
        "current_reps": [14, 13],
    }


def test_contexto_da_ia_troca_de_maquina_sem_anterior_comparavel():
    (item,) = exercise_progression_context(_lote(_plato_e_troca_de_maquina()))
    assert item["status"] == NOT_COMPARABLE
    assert item["previous_comparable"] is None
    assert item["paired_top_reps"] is None
    assert item["equipment_id"] == SMITH


def test_prompt_manda_usar_o_status_e_nao_chama_carga_maxima_de_progressao(monkeypatch):
    capturado: dict = {}
    monkeypatch.setattr(report_ai, "_call_openai", lambda prompt, *_a, **_k: capturado.setdefault("prompt", prompt))
    training = {
        "days": 90,
        "recent_records": [{"exercise_name": "Supino", "record_weight_kg": 80.0, "local_date": "2026-10-01"}],
        "exercise_progression": exercise_progression_context(_lote(agachamento_real())),
    }

    report_ai.analyze_training_evolution(training, days=90)

    prompt = capturado["prompt"]
    # A secao de progressao e a frase da janela dos recordes sao prosa do
    # template (M103 P2): `api/privado/tests/unit/test_workouts_analysis_prompt.py`.
    assert "exercise_progression" in prompt and '"status": "PROGRESSING"' in prompt
    # M76 Lote 3b: recordes pela regra unica, janela interpolada do dono.
    assert "recent_records" in prompt and '"record_weight_kg": 80.0' in prompt
    assert report_ai.prompt_values()["dias_recordes_recentes"] == f"{RECENT_RECORDS_DAYS}"
    assert "{{" not in prompt
    assert "max_load_by_exercise_60d" not in prompt and "progression_highlights" not in prompt
    # o payload devolvido a tela nao muda
    assert "recent_records" in training


# ---------------------------------------------------------------------------
# Drill-down: anterior vs atual
# ---------------------------------------------------------------------------


def test_drill_down_expoe_reps_emparelhadas_e_e1rm_nao_decisivo():
    payload = svc.build_exercise_progress(FakeDb(agachamento_real()), EXERCICIO, user_id=USUARIO, window=JANELA)

    comparacao = payload["latest_comparison"]
    assert comparacao["comparable"] is True
    assert comparacao["paired_reps"]["previous_sum"] == 23  # reps + RIR (M78)
    assert comparacao["paired_reps"]["current_sum"] == 28.5
    assert comparacao["paired_reps"]["effort_adjusted"] is True
    assert comparacao["paired_reps"]["previous_reps"] == [11, 10]
    assert comparacao["e1rm_decisive"] is False  # 14 reps no atual
    assert comparacao["e1rm_max_reliable_reps"] == 12
    assert [e["e1rm_reliable"] for e in payload["exposures"]] == [True, False]
    assert payload["exposures"][-1]["e1rm_reps"] == 14

    # contrato fechado (`extra="forbid"`) aceita os campos novos
    ExerciseProgressResponse.model_validate(payload)


def test_drill_down_troca_de_maquina_nao_emparelha():
    rows = exposicao(1, 1, _d(10), [(8, 60.0, 8.0)] * 3) + exposicao(
        2, 2, _d(14), [(8, 40.0, 8.0)] * 3, equipment_id=SMITH
    )
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["latest_comparison"]["comparable"] is False
    assert payload["latest_comparison"]["paired_reps"] is None
    assert payload["latest_comparison"]["e1rm_decisive"] is False


def test_drill_down_e1rm_decisivo_com_poucas_reps():
    rows = exposicao(1, 1, _d(10), [(6, 80.0, 8.0)] * 3) + exposicao(2, 2, _d(14), [(6, 85.0, 8.0)] * 3)
    payload = svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)
    assert payload["latest_comparison"]["e1rm_decisive"] is True


# ---------------------------------------------------------------------------
# /dashboard/progressao: grafico pela chave do veredito
# ---------------------------------------------------------------------------


def _set_row(activity_id: int, dia: str, reps: int, kg: float, rpe: float) -> dict:
    return {"activity_id": activity_id, "local_date": dia, "activity_name": "Treino", "set_type": "working",
            "reps": reps, "weight_kg": kg, "rpe": rpe, "set_number": 1, "gym_equipment_id": None}


def test_tabela_marca_a_chave_do_veredito_e_usa_a_carga_de_topo(monkeypatch):
    rows = (
        exposicao(1, 1, _d(10), [(10, 60.0, 8.0)] * 3, equipment_id=SMITH)
        + exposicao(2, 2, _d(14), [(10, 60.0, 8.0), (20, 55.0, 10.0)])
        + exposicao(3, 3, _d(18), [(11, 60.0, 8.0), (10, 60.0, 8.0)])
    )
    exposures = svc.get_exposures_between(
        FakeDb(rows), EXERCICIO, user_id=USUARIO, date_from=JANELA.date_from, date_to=JANELA.date_to
    )
    monkeypatch.setattr(analytics, "_progression_exposures", lambda *a, **k: exposures)
    items = analytics._build_progression_sessions(
        [_set_row(1, "2026-09-10", 10, 60.0, 8.0),
         _set_row(2, "2026-09-14", 10, 60.0, 8.0), _set_row(2, "2026-09-14", 20, 55.0, 10.0),
         _set_row(3, "2026-09-18", 11, 60.0, 8.0)]
    )

    analytics._attach_session_evolution(items, USUARIO, EXERCICIO, None, 30, None)

    assert [i["in_verdict_key"] for i in items] == [False, True, True]  # Smith fora do grafico
    sessao_2 = items[1]
    # Carga de TOPO da exposicao (60 kg), nao a da serie do melhor e1RM:
    # 55 kg x 20 @ 10 = 91,7 > 60 kg x 10 @ 8 = 84.
    assert sessao_2["best_load_kg"] == 55.0  # campo legado: serie do melhor e1RM
    assert sessao_2["e1rm_reliable"] is False  # e veio de 20 reps
    assert sessao_2["top_load_kg"] == 60.0
    assert sessao_2["top_set_reps"] == [10]


def test_e1rm_acima_de_12_reps_e_marcado_pouco_confiavel():
    items = analytics._build_progression_sessions(
        [_set_row(1, "2026-09-10", 15, 50.0, 9.0), _set_row(2, "2026-09-14", 8, 60.0, 8.0)]
    )
    assert items[0]["e1rm_reps"] == 15 and items[0]["e1rm_reliable"] is False
    assert items[1]["e1rm_reps"] == 8 and items[1]["e1rm_reliable"] is True


def test_sem_exposicoes_os_campos_novos_ficam_nulos(monkeypatch):
    monkeypatch.setattr(analytics, "_progression_exposures", lambda *a, **k: [])
    items = analytics._build_progression_sessions([_set_row(1, "2026-09-10", 10, 60.0, 8.0)])
    analytics._attach_session_evolution(items, USUARIO, EXERCICIO, None, 30, None)
    assert items[0]["in_verdict_key"] is None and items[0]["top_load_kg"] is None

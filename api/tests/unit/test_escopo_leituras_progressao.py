"""Issue #152: cada resposta diz O QUE a leitura de progressao compara.

Nenhum status muda: so ha descritores (dono: `domain/progression_status_labels`)
e o status individual do exercicio nos itens de `performance_exercises`."""

from __future__ import annotations

import re
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics, security
from app.domain import progression_status_labels as dono
from app.intelligence.dashboard.contracts import (
    ExerciseProgressResponse,
    ProgressionBlockOut,
    WindowInfo,
    WindowKind,
)
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus as S
from app.intelligence.dashboard.metrics_registry import metric_version
from app.intelligence.dashboard.overview_service import progression_block
from app.intelligence.muscle_group_performance import build_group_performance, insufficient

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

HOJE = date(2026, 10, 2)
CHAVES = {"code", "entity", "metric", "comparator", "label", "description"}


def _janela():
    return WindowInfo(kind=WindowKind.ROLLING_28D, date_from=date(2026, 9, 5), date_to=HOJE, timezone="UTC", days=28)


def test_cinco_descritores_do_dono():
    assert {c: dono.progression_scope(c)["label"] for c in dono.PROGRESSION_SCOPES} == {
        dono.SCOPE_EXERCISE_LAST_VS_PREVIOUS: "Última exposição vs. anterior",
        dono.SCOPE_GROUP_WEIGHTED_EXERCISES: "Grupo: soma ponderada dos exercícios",
        dono.SCOPE_GROUP_VOLUME_WINDOWS: "Volume: janela atual vs. anterior",
        dono.SCOPE_E1RM_WINDOWS: "e1RM: janela atual vs. anterior",
        dono.SCOPE_EXERCISE_WINDOW_TREND: "Tendência da janela",
    }
    for codigo, d in dono.PROGRESSION_SCOPES.items():
        assert set(d) == CHAVES and d["code"] == codigo and d["description"]
    assert dono.PROGRESSION_SCOPES[dono.SCOPE_EXERCISE_LAST_VS_PREVIOUS]["entity"] == "exercise"
    assert dono.PROGRESSION_SCOPES[dono.SCOPE_GROUP_VOLUME_WINDOWS]["entity"] == "muscle_group"


def test_textos_de_escopo_nao_reescrevem_rotulos_de_status():
    proibido = re.compile(r"[\"'](?:Progredindo|Regredindo|Est[aá]vel|Suspeita de plat[oô]|N[aã]o compar[aá]vel)[\"']")
    for d in dono.PROGRESSION_SCOPES.values():
        assert not proibido.search(d["label"]) and not proibido.search(d["description"])


def test_bloco_do_inicio_traz_escopo_regra_e_janela():
    bloco = progression_block([], truncated=False, exposures_per_exercise=5, window=_janela())
    ProgressionBlockOut.model_validate(bloco)
    assert bloco["status_scope"] == dono.progression_scope(dono.SCOPE_EXERCISE_LAST_VS_PREVIOUS)
    assert bloco["rule_version"] == metric_version("exercise_progression_status") == "progression-status-v2"
    assert bloco["window"] == _janela()


def test_contrato_do_detalhe_exige_o_escopo():
    assert "status_scope" in ExerciseProgressResponse.model_fields


# ---------------------------------------------------------------------------
# Regressao da issue: exercicio REGREDINDO dentro de grupo PROGREDINDO
# ---------------------------------------------------------------------------

AGACHAMENTO, LEG_PRESS, EXTENSORA, QUADRICEPS = 1, 2, 3, 4


def _linha(periodo, ex, nome, e1rm, peso):
    return {
        "period": periodo, "exercise_id": ex, "exercise_name": nome, "equipment_id": None,
        "load_convention": None, "muscle_group_id": QUADRICEPS, "stimulus_weight": peso, "best_e1rm_kg": e1rm,
    }


def _cenario_agachamento():
    # e1RM do agachamento sobe 37,5% (80 -> 110) mesmo com o STATUS do exercicio REGRESSING
    rows = [
        _linha("previous", AGACHAMENTO, "Agachamento livre", 80.0, 0.4),
        _linha("current", AGACHAMENTO, "Agachamento livre", 110.0, 0.4),
        _linha("previous", LEG_PRESS, "Leg press", 200.0, 1.0),
        _linha("current", LEG_PRESS, "Leg press", 210.0, 1.0),
    ]
    itens = [
        {"exercise_id": AGACHAMENTO, "status": S.REGRESSING.value},
        {"exercise_id": LEG_PRESS, "status": S.PROGRESSING.value},
        {"exercise_id": EXTENSORA, "status": S.PROGRESSING.value},
    ]
    pesos = {QUADRICEPS: {AGACHAMENTO: 0.4, LEG_PRESS: 1.0, EXTENSORA: 0.8}}
    return rows, itens, pesos


def test_exercicio_regredindo_em_grupo_progredindo_e_variacao_de_e1rm_positiva():
    rows, itens, pesos = _cenario_agachamento()
    grupo = build_group_performance(rows, exercise_items=itens, group_weights=pesos)[QUADRICEPS]

    assert grupo["performance_status"] == "PROGREDINDO"
    assert grupo["progression_counts"]["progressing"] == 2 and grupo["progression_counts"]["regressing"] == 1
    por_ex = {e["exercise_id"]: e for e in grupo["performance_exercises"]}
    assert por_ex[AGACHAMENTO]["status"] == S.REGRESSING.value  # status (a) do exercicio
    assert por_ex[AGACHAMENTO]["change_pct"] == 37.5  # variacao (d), positiva
    assert por_ex[LEG_PRESS]["status"] == S.PROGRESSING.value


def test_status_individual_ausente_do_lote_vira_none_sem_reclassificar():
    rows, _itens, pesos = _cenario_agachamento()
    grupo = build_group_performance(rows, exercise_items=[], group_weights=pesos)[QUADRICEPS]
    assert all(e["status"] is None for e in grupo["performance_exercises"])
    assert grupo["performance_status"] == "DADOS_INSUFICIENTES"
    assert grupo["progression_counts"] == insufficient()["progression_counts"]


@pytest.fixture
def client(monkeypatch):
    rows, itens, pesos = _cenario_agachamento()
    monkeypatch.setattr(analytics, "_user_today", lambda user_id: HOJE)
    monkeypatch.setattr(
        analytics,
        "_fetch_group_tonnage_rows",
        lambda *a, **k: [
            {"period": "current", "muscle_group_id": QUADRICEPS, "muscle_group_name": "Quadríceps",
             "tonnage_kg": 1000.0, "sets_count": 8, "sessions_count": 4}
        ],
    )
    monkeypatch.setattr(analytics, "_fetch_group_e1rm_rows", lambda *a, **k: rows)
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: "NULL")
    monkeypatch.setattr(analytics, "_group_phases", lambda user_id, *_: {})
    monkeypatch.setattr(analytics, "_exercise_progression_items", lambda *a, **k: (itens, False))
    monkeypatch.setattr(analytics, "_fetch_exercise_group_weights", lambda user_id, windows: pesos)
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[security.get_current_identity] = lambda: security.Identity(
        user_id=7, role="regular", source="session"
    )
    return TestClient(app)


def test_rota_do_grupo_traz_os_escopos_no_topo_e_o_status_por_exercicio(client):
    body = client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()

    assert body["performance_status_scope"] == dono.progression_scope(dono.SCOPE_GROUP_WEIGHTED_EXERCISES)
    assert body["volume_status_scope"] == dono.progression_scope(dono.SCOPE_GROUP_VOLUME_WINDOWS)
    assert body["performance_change_scope"] == dono.progression_scope(dono.SCOPE_E1RM_WINDOWS)
    assert body["exercise_status_scope"] == dono.progression_scope(dono.SCOPE_EXERCISE_LAST_VS_PREVIOUS)
    assert body["metric_versions"] == {
        "exercise_progression_status": metric_version("exercise_progression_status"),
        "tonnage": metric_version("tonnage"),
        "e1rm": metric_version("e1rm"),
    }
    # o que ja existia segue igual
    assert body["performance_status_basis"] == "weighted_exercise_progression_status"
    assert body["comparison_basis"] == "tonnage_per_session"

    (grupo,) = body["items"]
    assert grupo["performance_status"] == "PROGREDINDO"
    assert grupo["progression_counts"]["regressing"] == 1
    agach = next(e for e in grupo["performance_exercises"] if e["exercise_id"] == AGACHAMENTO)
    assert agach["status"] == "REGRESSING" and agach["change_pct"] == 37.5


def test_rota_do_grupo_degradada_mantem_contagem_zerada_e_escopos(client, monkeypatch):
    def falha(*a, **k):
        raise RuntimeError("lote indisponivel")

    monkeypatch.setattr(analytics, "_exercise_progression_items", falha)
    body = client.get("/analytics/dashboard-progression-by-muscle-group?days=28").json()
    assert body["degraded"] is True and body["performance_status_scope"]["code"] == dono.SCOPE_GROUP_WEIGHTED_EXERCISES
    assert body["items"][0]["progression_counts"] == insufficient()["progression_counts"]

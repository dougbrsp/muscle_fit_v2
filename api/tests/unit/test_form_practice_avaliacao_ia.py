"""#196 (ADR 0017, D1): a avaliação de série por IA (job `evaluate_set`) de uma
execução marcada como aprimoramento de técnica recebe o mesmo contrato neutro
da rota da série: leitura do dono `neutral_set_reading`, sem carga da próxima
série e sem decisão/prescrição de carga do motor. Execução normal: igual a antes.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from app import engine_decision_context as edc
from app import workout_ai_generation_core as core
from app.domain import effective_intensity as ei
from app.domain import progression_engine as pe
from app.domain.set_evaluation_categories import SET_EVALUATION_CATEGORY_LABELS
from app.engine_decision_context import PairEngineContext

DECISAO = SimpleNamespace(load_kg=Decimal(40), decision=pe.INCREASE_LOAD, reps_min=10, reps_max=12)
MOTOR = PairEngineContext(step_kg=Decimal("2.5"), decision=DECISAO)
INTENSIDADE = ei.EffectiveIntensity(rpe_target_effective=8, ceiling=9, failure_allowed=False)
PAYLOAD = {
    "exercise_id": 7, "exercise_name": "Remada", "set_number": 1, "sets_count": 3, "reps_target": "10-12",
    "load_target": "40kg", "rpe_target": "8", "reps": 17, "weight_kg": 40, "rpe": 10, "set_type": "working",
}


RESPOSTA_NEUTRA = {
    "category": pe.HOLD, "suggested_next_load_kg": None, "suggested_next_reps": None, "suggested_next_rpe": None,
}
# A IA "teimosa": sugere carga, reps e categoria de aumento.
RESPOSTA_AUMENTO = {
    "category": pe.INCREASE_LOAD, "category_label": SET_EVALUATION_CATEGORY_LABELS[pe.INCREASE_LOAD],
    "suggested_next_load_kg": 50, "suggested_next_reps": 12, "suggested_next_rpe": 8,
}


def _dispatch(monkeypatch, engine, resposta=RESPOSTA_NEUTRA, saida=None):
    recebido, lidos = {}, []

    def fake_evaluate_set(payload, **kwargs):
        recebido.update(kwargs)
        return dict(resposta)

    def publicado():
        lidos.append(1)
        return core.DEFAULT_RULESET

    monkeypatch.setattr(core, "load_published_ruleset_autocommit", publicado)
    monkeypatch.setattr(core, "evaluate_set", fake_evaluate_set)
    monkeypatch.setattr(core, "_evaluate_set_engine", lambda payload, user_id: engine)
    monkeypatch.setattr(core, "_evaluate_set_intensity", lambda payload: INTENSIDADE)
    monkeypatch.setattr(core, "_evaluate_set_logged_intensity", lambda engine: None)
    monkeypatch.setattr(core, "apply_engine_load_bound", lambda result, *a, **k: {**result, "clamps": []})
    resultado = core.dispatch_evaluate_set(dict(PAYLOAD), user_id=1)
    if saida is not None:
        saida.update(resultado)
    return recebido, lidos


def test_execucao_marcada_entrega_fatos_neutros_a_ia(monkeypatch):
    marcado = replace(MOTOR, form_practice_marked=True, deload=SimpleNamespace(load_kg=Decimal(24)))
    recebido, lidos = _dispatch(monkeypatch, marcado)
    leitura = recebido["set_reading"]
    assert leitura.status is None and leitura.reps is None and leitura.reps_position is None
    assert leitura.safety_event == ei.set_intensity_reading("working", 10, INTENSIDADE).safety_event
    assert recebido["next_set"] is None
    assert recebido["engine"].decision is None and recebido["engine"].deload is None
    assert lidos == []  # nem o conjunto publicado é lido


def test_execucao_normal_segue_igual(monkeypatch):
    recebido, lidos = _dispatch(monkeypatch, MOTOR)
    assert recebido["set_reading"].status is not None
    assert recebido["next_set"] is not None
    assert recebido["engine"] is MOTOR and lidos == [1]


def test_saida_da_ia_marcada_sai_neutra_mesmo_com_aumento(monkeypatch):
    saida = {}
    _dispatch(monkeypatch, replace(MOTOR, form_practice_marked=True), RESPOSTA_AUMENTO, saida)
    assert saida["suggested_next_load_kg"] is None and saida["suggested_next_reps"] is None
    assert saida["category"] == pe.HOLD
    assert saida["category_label"] == SET_EVALUATION_CATEGORY_LABELS[pe.HOLD]
    assert saida["category_from_ai"] == pe.INCREASE_LOAD


def test_saida_da_ia_normal_mantem_carga_reps_e_categoria(monkeypatch):
    saida = {}
    _dispatch(monkeypatch, MOTOR, RESPOSTA_AUMENTO, saida)
    assert saida["suggested_next_load_kg"] == 50 and saida["suggested_next_reps"] == 12
    assert saida["category"] == pe.INCREASE_LOAD
    assert saida["category_label"] == SET_EVALUATION_CATEGORY_LABELS[pe.INCREASE_LOAD]


def test_engine_marca_pela_linha_do_payload(monkeypatch):
    @contextmanager
    def fake_tx():
        yield object()

    monkeypatch.setattr(core, "transaction", fake_tx)
    monkeypatch.setattr(core, "load_pair_context", lambda conn, user_id, mapping_id, equipment_id: MOTOR)
    vistos = []

    def leitor(conn, user_id, exercise_id):
        vistos.append(exercise_id)
        return exercise_id == 7

    monkeypatch.setattr(core, "read_exercise_form_practice", leitor)
    assert core._evaluate_set_engine({"exercise_id": 7}, 1).form_practice_marked is True
    assert core._evaluate_set_engine({"exercise_id": 8}, 1).form_practice_marked is False
    assert core._evaluate_set_engine({}, 1) is MOTOR and vistos == [7, 8]


def test_leitor_da_marcacao_le_a_coluna_do_dono(monkeypatch):
    linhas = {(7, 1): {"form_practice": 1}, (8, 1): {"form_practice": 0}}
    monkeypatch.setattr(edc, "_fetchone", lambda conn, sql, params: linhas.get(params))
    assert edc.read_exercise_form_practice(None, 1, 7) is True
    assert edc.read_exercise_form_practice(None, 1, 8) is False
    assert edc.read_exercise_form_practice(None, 2, 7) is False  # de outro usuário
    assert edc.read_exercise_form_practice(None, 1, None) is False

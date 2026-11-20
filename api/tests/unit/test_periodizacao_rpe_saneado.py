"""RPE-alvo da LLM saneado ANTES do schema do Modelo (#183): o dono
(`training_history.sanitize_rpe_target`) limita e arredonda ao passo; o validador
registra o auto-fix `RPE_SANITIZED` em vez de terminar o job em `SCHEMA_INVALID`."""

from __future__ import annotations

import contextlib

import pytest

from app.models import PeriodizationDraftIn
from app.openai_client import StructuredResult
from app.periodization import core, validation
from app.domain.training_history import sanitize_rpe_target


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("8", ("8", False)),
        ("7-8-9", ("7-8-9", False)),
        ("8.5", ("8.5", False)),
        ("8.3", ("8.5", True)),  # mais proximo do passo
        ("8.25", ("8", True)),  # empate para baixo (lado seguro)
        ("11", ("10", True)),
        ("0", ("1", True)),
        ("7-8.3-12", ("7-8.5-10", True)),
        ("8,3", ("8.5", True)),
        ("8-8", ("8", False)),  # colapso de cauda e formato, nao correcao
        ("sem numero", (None, True)),
        ("", (None, False)),
        (None, (None, False)),
    ],
)
def test_sanitize_rpe_target(entrada, esperado):
    assert sanitize_rpe_target(entrada) == esperado


def _saida(rpe):
    item = {
        "item_kind": "equipment",
        "mapping_id": 10,
        "equipment_id": 100,
        "muscle_group_id": 1,
        "prioritize": False,
        "sets_count": 3,
        "reps_target": "8-12",
        "rpe_target": rpe,
    }
    return {
        "template": {
            "name": "M",
            "description": None,
            "volume_mode": "equipment_series",
            "slots": [{"sequence": 1, "name": "A", "slot_type": "normal", "weekday": None, "items": [item]}],
        },
        "rationale": "r",
        "source_citations": [],
    }


def _validar(monkeypatch, rpe):
    monkeypatch.setattr(validation, "_validate_strength", lambda conn, request, template, report: template)
    request = PeriodizationDraftIn(volume_mode="equipment_series", days_per_cycle=1)
    return validation.validate_draft(None, request, _saida(rpe))


def test_rpe_fora_do_passo_nao_gera_schema_invalid_e_registra_o_auto_fix(monkeypatch):
    draft, report, _ = _validar(monkeypatch, "8.3")
    assert not report["errors"]
    assert draft["slots"][0]["items"][0]["rpe_target"] == "8.5"
    fix = [f for f in report["auto_fixes"] if f["code"] == validation.RPE_SANITIZED]
    assert fix == [{"code": "RPE_SANITIZED", "slot": 1, "slot_name": "A", "item": 1, "from": "8.3", "to": "8.5"}]


def test_texto_sem_numero_vira_nulo_com_auto_fix(monkeypatch):
    # Escolha: lixo vira nulo (o item segue sem alvo) em vez de derrubar o job pago.
    draft, report, _ = _validar(monkeypatch, "pesado")
    assert not report["errors"]
    assert draft["slots"][0]["items"][0]["rpe_target"] is None
    assert [f["to"] for f in report["auto_fixes"] if f["code"] == validation.RPE_SANITIZED] == [None]


def test_rpe_valido_nao_gera_auto_fix(monkeypatch):
    _, report, _ = _validar(monkeypatch, "7-8-9")
    assert not [f for f in report["auto_fixes"] if f["code"] == validation.RPE_SANITIZED]


def test_run_devolve_rpe_targets_by_set(monkeypatch):
    draft = {"slots": [{"sequence": 1, "items": [{"rpe_target": "7-8-9", "sets_count": 3}]}]}

    monkeypatch.setenv(core.MODEL_ENV_VAR, "gpt-x")
    monkeypatch.setattr(
        core, "call_openai_structured", lambda *a, **k: StructuredResult(data={}, model="gpt-x", tokens_in=1, tokens_out=1)
    )
    monkeypatch.setattr(core, "transaction", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(core, "build_context", lambda conn, request, user_id: {"request": {}, "muscle_groups": [], "gym_catalog": {}})
    monkeypatch.setattr(core, "build_prompt", lambda context: "prompt")
    monkeypatch.setattr(core, "build_prompt_cache_prefix", lambda context: None)
    monkeypatch.setattr(core, "finish_log", lambda *a, **k: None)
    monkeypatch.setattr(
        core,
        "validate_draft",
        lambda conn, request, data, **kw: (draft, {"errors": [], "warnings": [], "auto_fixes": []}, {"rationale": "", "source_citations": []}),
    )
    resultado = core.run_periodization_draft({"volume_mode": "equipment_series", "days_per_cycle": 1}, user_id=None)
    assert resultado["draft"]["slots"][0]["items"][0]["rpe_targets_by_set"] == [7.0, 8.0, 9.0]
    assert "rpe_targets_by_set" not in draft["slots"][0]["items"][0]  # nao muta o original

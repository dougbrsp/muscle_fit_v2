"""`POST /workout-plan-templates/cardio-prescription-check` (M98 C1): o editor
mostra no card a mensagem do dono (`cardio_prescription.validation_error`),
a mesma regra do PUT do Modelo; nada é gravado.

Rodar: cd api && python -m pytest tests/unit/test_cardio_prescription_check.py
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.domain import cardio_prescription as cp
from app.security import require_admin_or_pt


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    app.dependency_overrides[require_admin_or_pt] = lambda: None
    return TestClient(app)


def _check(body):
    resp = _client().post("/workout-plan-templates/cardio-prescription-check", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["error"]


def test_padroes_do_dono_sao_validos():
    for mode, defaults in cp.DEFAULTS.items():
        assert _check({**defaults, "cardio_mode": mode.value}) is None


def test_item_antigo_so_com_duracao_e_valido():
    assert _check({"cardio_duration_minutes": 20}) is None


def test_mensagens_do_dono():
    hiit = {**cp.DEFAULTS[cp.CardioMode.HIIT], "cardio_mode": "hiit"}
    assert _check({**hiit, "hiit_low_zone": 4, "hiit_high_zone": 4}) == (
        "no HIIT a zona alta precisa ser maior que a zona baixa."
    )
    assert _check({"cardio_mode": "hiit"}) == "cardio HIIT exige tempo em zona baixa (s)."
    low, high = cp.FIELD_LIMITS[cp.ROUNDS_FIELD]
    assert _check({**hiit, "hiit_rounds": high + 1}) == f"nº de séries precisa estar entre {low} e {high}."
    assert _check({"cardio_mode": "fartlek"}).startswith("tipo de cardio inválido")


def test_mesma_regra_do_put():
    """A mensagem do check é a do `_validate_template_payload` (sem o prefixo do slot)."""
    corpo = {"cardio_mode": "continuous", "cardio_duration_minutes": 0}
    assert _check(corpo) == cp.validation_error({**corpo, "item_kind": "cardio"})

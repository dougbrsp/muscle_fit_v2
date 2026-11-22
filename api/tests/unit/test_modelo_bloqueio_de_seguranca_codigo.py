"""#237: o 422 de seguranca do Modelo traz `code` estavel; `detail` segue texto."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.security import (
    MUST_CHANGE_PASSWORD_CODE,
    MUST_CHANGE_PASSWORD_DETAIL,
    MustChangePasswordRequired,
    must_change_password_handler,
)
from app.template_safety_validation import TEMPLATE_SAFETY_BLOCKED_CODE, TemplateSafetyBlocked


def _client(monkeypatch):
    # Mesmo registro de `main.py`; o handler so le status/detail/code.
    app = FastAPI()
    app.add_exception_handler(MustChangePasswordRequired, must_change_password_handler)
    app.add_exception_handler(TemplateSafetyBlocked, must_change_password_handler)

    @app.get("/bloqueio")
    def _bloqueio():
        monkeypatch.setattr(wpt, "has_safety_relevant_input", lambda slots: True)
        monkeypatch.setattr(wpt, "transaction", _tx)
        monkeypatch.setattr(wpt, "build_safety_report", lambda conn, slots: [{"message": "RPE 10 acima do teto."}])
        monkeypatch.setattr(wpt, "split_report_by_authorship", lambda report, new, old: (report, []))
        wpt._enforce_template_safety(_Payload(), None)

    @app.get("/senha")
    def _senha():
        raise MustChangePasswordRequired()

    return TestClient(app)


class _Payload:
    volume_mode = "equipment_series"
    slots: list = []


class _tx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def test_bloqueio_devolve_detail_texto_e_code(monkeypatch):
    resp = _client(monkeypatch).get("/bloqueio")
    assert resp.status_code == 422
    assert resp.json() == {
        "detail": "Itens novos ou alterados com incompatibilidade de segurança: RPE 10 acima do teto.",
        "code": TEMPLATE_SAFETY_BLOCKED_CODE,
    }
    assert TEMPLATE_SAFETY_BLOCKED_CODE == "template_safety_blocked"


def test_troca_de_senha_segue_igual(monkeypatch):
    resp = _client(monkeypatch).get("/senha")
    assert resp.status_code == 403
    assert resp.json() == {"detail": MUST_CHANGE_PASSWORD_DETAIL, "code": MUST_CHANGE_PASSWORD_CODE}

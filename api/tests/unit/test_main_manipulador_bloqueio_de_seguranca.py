"""#237: a aplicacao real (`app.main.app`) registra o manipulador de
`TemplateSafetyBlocked`; sem ele o bloqueio de seguranca viraria 500."""

from __future__ import annotations

from app.main import app
from app.template_safety_validation import TemplateSafetyBlocked


def test_app_real_trata_bloqueio_de_seguranca():
    handler = app.exception_handlers.get(TemplateSafetyBlocked)
    assert handler is not None
    assert handler is app.exception_handlers[
        next(c for c in app.exception_handlers if getattr(c, "__name__", "") == "MustChangePasswordRequired")
    ]

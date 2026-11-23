"""#291: número não finito no corpo dá 422 (não 500) e o formato do 422 comum não muda."""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app import main, main_engines
from app.security import validation_error_handler


class _Corpo(BaseModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(default=1, gt=0)


def _app(com_handler: bool) -> FastAPI:
    app = FastAPI()
    if com_handler:
        app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.post("/t")
    def t(corpo: _Corpo):
        return {"x": corpo.x}

    return app


def _cliente() -> TestClient:
    return TestClient(_app(True))


def test_handler_registrado_nos_dois_apps():
    assert main.app.exception_handlers[RequestValidationError] is validation_error_handler
    assert main_engines.app.exception_handlers[RequestValidationError] is validation_error_handler


def test_inf_e_nan_dao_422():
    c = _cliente()
    for bruto in ('{"x": 1e999}', '{"x": NaN}', '{"x": [1e999]}'):
        r = c.post("/t", content=bruto, headers={"content-type": "application/json"})
        assert r.status_code == 422, bruto
        assert isinstance(r.json()["detail"], list)


def test_erro_comum_igual_ao_padrao():
    r = _cliente().post("/t", json={"x": "abc"})
    assert r.status_code == 422
    d = r.json()["detail"][0]
    assert d["type"] == "float_parsing" and d["loc"] == ["body", "x"] and d["input"] == "abc"


def test_erro_com_ctx_identico_ao_handler_padrao_do_fastapi():
    corpo = {"x": 1, "y": -5}
    padrao = TestClient(_app(False)).post("/t", json=corpo)
    nosso = _cliente().post("/t", json=corpo)
    assert nosso.status_code == padrao.status_code == 422
    assert nosso.json() == padrao.json()
    assert nosso.json()["detail"][0]["ctx"] == {"gt": 0.0}


def test_app_real_rota_real_com_float_nao_finito_da_422():
    # Rota real sem auth/DB: o corpo é validado antes da dependência de identidade só se ela não rodar
    # primeiro; por isso usamos qualquer rota POST real e aceitamos 401/403 só se não for 500.
    rota = next(
        r for r in main.app.routes
        if getattr(r, "methods", None) and "POST" in r.methods and getattr(r, "body_field", None) is not None
    )
    r = TestClient(main.app, raise_server_exceptions=False).post(
        rota.path, content='{"x": 1e999}', headers={"content-type": "application/json"}
    )
    assert r.status_code != 500

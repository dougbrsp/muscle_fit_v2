"""FastAPI 0.141 + Starlette 1.x com o comportamento HTTP da 0.115.x (app/fastapi_compat.py).

Rodar: cd api && python -m pytest tests/unit/test_fastapi_compat.py
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.fastapi_compat import fastapi_compat_kwargs


class _Corpo(BaseModel):
    valor: float


class _Saida(BaseModel):
    valor: float
    nome: str


def _app(**kwargs) -> FastAPI:
    app = FastAPI(**kwargs)
    router = APIRouter(prefix="/r")

    @router.post("/eco")
    def eco(corpo: _Corpo):
        return {"valor": corpo.valor}

    @router.get("/modelo", response_model=_Saida)
    def modelo():
        return {"valor": 1e16, "nome": "supino inclinado çã"}

    app.include_router(router)
    return app


def test_json_sem_content_type_continua_aceito():
    client = TestClient(_app(**fastapi_compat_kwargs()))
    resp = client.post("/r/eco", content=b'{"valor": 2.5}')
    assert resp.status_code == 200
    assert resp.json() == {"valor": 2.5}


def test_sem_o_compat_o_padrao_novo_recusaria():
    # Documenta o que o compat evita: a FastAPI nova recusa JSON sem Content-Type.
    client = TestClient(_app())
    assert client.post("/r/eco", content=b'{"valor": 2.5}').status_code == 422


def test_response_model_serializa_pelo_json_dumps_como_antes():
    client = TestClient(_app(**fastapi_compat_kwargs()))
    resp = client.get("/r/modelo")
    assert resp.status_code == 200
    # Bytes da 0.115.x (JSONResponse: json.dumps, ensure_ascii=False, sem espacos).
    assert resp.content == '{"valor":1e+16,"nome":"supino inclinado çã"}'.encode("utf-8")
    assert resp.headers["content-type"] == "application/json"


def test_on_event_startup_continua_rodando():
    # Starlette 1.0 removeu `on_event`; a FastAPI >= 0.128.3 o reimplementa.
    # main.py usa `@app.on_event("startup")` (seed do admin, migracao de chaves).
    app = _app(**fastapi_compat_kwargs())
    chamadas: list[str] = []

    @app.on_event("startup")
    def _inicio() -> None:
        chamadas.append("startup")

    with TestClient(app) as client:
        assert client.get("/r/modelo").status_code == 200
    assert chamadas == ["startup"]


def test_apps_reais_usam_o_compat():
    from app.main import app as api_core
    from app.main_engines import app as api_engines

    from fastapi.responses import JSONResponse

    for app in (api_core, api_engines):
        assert app.router.strict_content_type is False
        assert app.router.default_response_class is JSONResponse

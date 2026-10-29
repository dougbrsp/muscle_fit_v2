"""COD-02/COD-03: helpers extraidos para `app/db_helpers.py` e
`security.resolve_identity_from_credentials` respondem como as copias que
existiam nos modulos (menos o texto da excecao no 503, removido no SEG-12, e a
trava SEG-06, coberta em test_seg06_seg12_identidade_e_erros.py).

Rodar: cd api && python -m pytest tests/unit/test_db_helpers.py
"""

from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import db_helpers, security
from app.db import DataApiError
from tests.support.fake_db import SharedCursorConn

# Corpo do 503 de cada grupo, copiado do codigo de antes da extracao.
SEM_MENSAGEM = ["body_measurements", "context_events", "daily_checkins", "effort_metrics", "reports", "sleep_metrics"]
MYSQL = ["exercise_catalog_admin", "exercise_mapping_catalog", "intervals_integration", "muscle_group_equipment"]
RDS = ["analytics", "analytics_dashboard", "equipment_exercises"]


def _detail_esperado(modulo: str) -> dict:
    # SEG-12: sem a chave `exception` (o texto vai para o log); quem nao tinha
    # mensagem propria recebe a generica do projeto.
    if modulo in SEM_MENSAGEM:
        return {"error": "database_unavailable", "message": "Banco de dados indisponível."}
    mensagem = (
        "Não foi possível conectar ao MySQL da Fitness API."
        if modulo in MYSQL
        else "Não foi possível conectar ao banco (RDS Data API) da Fitness API."
    )
    return {"error": "database_unavailable", "message": mensagem}


def _quebrado():
    raise DataApiError("fora do ar")


@pytest.mark.parametrize("nome", SEM_MENSAGEM + MYSQL + RDS)
def test_connect_de_cada_modulo_mantem_o_503(monkeypatch, nome):
    modulo = importlib.import_module(f"app.{nome}")
    monkeypatch.setattr(modulo, "get_conn", _quebrado)
    with pytest.raises(HTTPException) as erro:
        modulo._connect()
    assert erro.value.status_code == 503
    assert erro.value.detail == _detail_esperado(nome)
    # mesma ordem de chaves -> mesmo JSON byte a byte
    assert list(erro.value.detail) == list(_detail_esperado(nome))
    assert isinstance(erro.value.__context__, DataApiError)


@pytest.mark.parametrize("nome", SEM_MENSAGEM + MYSQL + RDS)
def test_connect_de_cada_modulo_devolve_a_conexao(monkeypatch, nome):
    modulo = importlib.import_module(f"app.{nome}")
    sentinela = object()
    monkeypatch.setattr(modulo, "get_conn", lambda: sentinela)
    assert modulo._connect() is sentinela


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (Decimal("12.50"), 12.5),
        (date(2026, 10, 9), "2026-10-09"),
        (datetime(2026, 10, 9, 7, 30, 5), "2026-10-09T07:30:05"),
        (timedelta(minutes=5), timedelta(minutes=5)),  # passa igual (como antes)
        ("texto", "texto"),
        (None, None),
        (3, 3),
    ],
)
def test_jsonable(valor, esperado):
    assert db_helpers.jsonable(valor) == esperado


@pytest.mark.parametrize("nome", ["analytics", "exercise_catalog_admin", "exercise_mapping_catalog", "intervals_integration", "users"])
def test_modulos_usam_o_jsonable_comum(nome):
    assert importlib.import_module(f"app.{nome}")._jsonable is db_helpers.jsonable


class _Cursor:
    def __init__(self, linhas):
        self.linhas = linhas
        self.executado = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.executado = (sql, params)

    def fetchall(self):
        return self.linhas


def _conn(linhas) -> SharedCursorConn:
    return SharedCursorConn(_Cursor(linhas))


@pytest.mark.parametrize("linhas, esperado", [([{"a": 1}], [{"a": 1}]), ((), []), (None, [])])
def test_rows(linhas, esperado):
    conn = _conn(linhas)
    resultado = db_helpers.rows(conn, "SELECT 1", (7,))
    assert resultado == esperado and isinstance(resultado, list)
    assert conn.cur.executado == ("SELECT 1", (7,))


@pytest.mark.parametrize(
    "modulo, nome",
    [
        ("intelligence.dashboard.adherence_service", "_rows"),
        ("intelligence.dashboard.muscle_summary_service", "_rows"),
        ("intelligence.dashboard.recovery_signals_service", "_rows"),
        ("intelligence.dashboard.engine_service", "_rows"),
        ("intelligence.report.daily_breakdown", "_fetchall"),
        ("intelligence.report.markdown_report_service", "_fetchall"),
        ("engine_decision_context", "_fetchall"),
    ],
)
def test_modulos_usam_o_rows_comum(modulo, nome):
    assert getattr(importlib.import_module(f"app.{modulo}"), nome) is db_helpers.rows


@pytest.mark.parametrize("nome", ["exercise_catalog_admin", "exercise_mapping_catalog", "gyms", "equipment_exercises"])
def test_require_identity_header_tem_prioridade_sobre_cookie(monkeypatch, nome):
    modulo = importlib.import_module(f"app.{nome}")
    vistos = []
    ident = security.Identity(user_id=1, role="regular", source="session")
    monkeypatch.setattr(security, "resolve_identity", lambda token: vistos.append(token) or ident)
    assert modulo._require_identity("hdr", "cookie") is ident
    assert modulo._require_identity(None, "cookie") is ident
    assert modulo._require_identity("", "cookie") is ident
    assert vistos == ["hdr", "cookie", "cookie"]


def test_require_identity_sem_credencial_e_401(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    with pytest.raises(HTTPException) as erro:
        security.resolve_identity_from_credentials(None, None)
    assert erro.value.status_code == 401

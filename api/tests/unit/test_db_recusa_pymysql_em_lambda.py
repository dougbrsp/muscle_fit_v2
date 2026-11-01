"""Anti-vazamento dev/prod: db.py recusa DB_BACKEND=pymysql dentro de Lambda real."""

import importlib
import sys

import pytest

from app import runtime_guard


def _recarregar_db(monkeypatch, *, lambda_real, backend):
    if lambda_real:
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-api")
    else:
        monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    if backend is None:
        monkeypatch.delenv("DB_BACKEND", raising=False)
    else:
        monkeypatch.setenv("DB_BACKEND", backend)
    sys.modules.pop("app.db", None)
    return importlib.import_module("app.db")


@pytest.fixture(autouse=True)
def _restaura_db(monkeypatch):
    yield
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    sys.modules.pop("app.db", None)


def test_lambda_real_com_pymysql_e_recusada(monkeypatch):
    with pytest.raises(RuntimeError, match="recusando executar"):
        _recarregar_db(monkeypatch, lambda_real=True, backend="pymysql")


def test_lambda_real_com_backend_desconhecido_e_recusada(monkeypatch):
    with pytest.raises(RuntimeError):
        _recarregar_db(monkeypatch, lambda_real=True, backend="qualquer")


def test_fora_de_lambda_com_pymysql_funciona(monkeypatch):
    db = _recarregar_db(monkeypatch, lambda_real=False, backend="pymysql")
    assert db.get_conn.__module__.endswith("db_pymysql")


@pytest.mark.parametrize("backend", [None, "data-api"])
def test_lambda_real_com_data_api_funciona(monkeypatch, backend):
    db = _recarregar_db(monkeypatch, lambda_real=True, backend=backend)
    assert db.get_conn.__module__.endswith("db_dataapi")


def test_dev_sem_marcador_de_lambda_nao_e_lambda_real(monkeypatch):
    # O emulador de Lambdas do dev nao define AWS_LAMBDA_FUNCTION_NAME.
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("DB_BACKEND", "pymysql")
    assert runtime_guard.em_lambda_real() is False
    assert runtime_guard.backend_vazado_para_lambda() is False
    runtime_guard.exigir_data_api_em_lambda()


def test_helper_usa_o_tipo_de_erro_do_chamador(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "x")
    monkeypatch.setenv("DB_BACKEND", "pymysql")

    class MeuErro(Exception):
        pass

    with pytest.raises(MeuErro, match=runtime_guard.MENSAGEM_RECUSA):
        runtime_guard.exigir_data_api_em_lambda(MeuErro)

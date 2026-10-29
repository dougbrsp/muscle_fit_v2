"""Ciclo de vida do schema de teste contra o MySQL de verdade.

Prova, no servidor real (e nao num dublê), que:
  - o schema criado por esta execucao e removido pelo mesmo codigo que o
    teardown de sessao usa;
  - nenhum schema que ja exista no servidor fora do padrao de teste
    (`fitness`, `mysql`, `sys`, ...) e selecionavel pela varredura.
"""

from __future__ import annotations

import os

import pytest

from tests.support import schema_lifecycle as sl

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_conn(mysql_schema):
    conn = sl.connect(**sl.mysql_kwargs_from_env())
    try:
        yield conn
    finally:
        conn.close()


def _exists(conn, name: str) -> bool:
    return name in sl.list_schemas(conn)


def _require_schema_por_execucao() -> str:
    """Token desta execucao, ou skip se o modo schema-por-execucao esta
    desligado (`TEST_MYSQL_UNIQUE_SCHEMA=0`, usado so para depuracao)."""
    token = sl.token_from_env()
    if not token or sl.run_token_of(os.environ["MYSQL_DATABASE"], stem=sl.stem_from_env()) != token:
        pytest.skip("execucao sem schema-por-execucao (TEST_MYSQL_UNIQUE_SCHEMA=0)")
    return token


def test_schema_desta_execucao_e_criado_e_derrubado(admin_conn) -> None:
    stem = sl.stem_from_env()
    token = _require_schema_por_execucao()

    # Mesmo formato dos schemas auxiliares das fixtures de integracao.
    base = os.environ["MYSQL_DATABASE"]
    name = f"{base[: -len('_test')]}_tdown_test"
    assert sl.run_token_of(name, stem=stem) == token, "schema auxiliar sem o token da execucao"

    with admin_conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
    assert _exists(admin_conn, name)

    assert sl.drop_schemas(admin_conn, [name], stem=stem) == [name]
    assert not _exists(admin_conn, name)


def test_schema_principal_da_execucao_seria_recolhido_no_teardown(mysql_schema) -> None:
    token = _require_schema_por_execucao()
    stem = sl.stem_from_env()
    assert sl.is_disposable_test_schema(mysql_schema, stem=stem)
    assert sl.run_token_of(mysql_schema, stem=stem) == token


def test_varredura_nunca_seleciona_schema_de_verdade_do_servidor(admin_conn) -> None:
    """No servidor real (o MySQL do compose em DEV, que hospeda `fitness`),
    nenhum schema fora do padrao de teste entra na lista de candidatos."""
    stem = sl.stem_from_env()
    for protegido in sl.PROTECTED_SCHEMAS:
        assert not sl.is_disposable_test_schema(protegido, stem=stem), protegido
    assert not sl.is_disposable_test_schema("musclefit_prod", stem=stem)

    for name in sl.list_schemas(admin_conn):
        if sl.is_disposable_test_schema(name, stem=stem):
            assert name.startswith(f"{stem}_") or name == f"{stem}_test"
            assert name.endswith("_test")
            assert name not in sl.PROTECTED_SCHEMAS

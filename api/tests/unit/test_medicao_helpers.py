"""As afirmacoes de `tests/support/medicao.py` tem logica propria (forma
canonica do SQL, contagem, diagnostico), e sao elas que passaram a decidir se
uma regressao de desempenho reprova. Se o helper estiver errado, os testes de
medicao viram verde permanente - a falha mais silenciosa possivel.

Estes testes sao unitarios (sem banco, milissegundos) e rodam no caminho
critico do CI, ao contrario das medicoes em si (marcador `slow`).
"""

from __future__ import annotations

import pytest

from tests.support.medicao import (
    REDE_ORDEM_DE_GRANDEZA_S,
    assert_no_maximo_statements,
    assert_ordem_de_grandeza,
    assert_reduziu_statements,
    forma,
    repetidos,
)

SELECT_POR_GRUPO = "SELECT nome FROM exercise_catalog WHERE muscle_group_id = %s AND user_id = 7"


def test_forma_apaga_literais_e_colapsa_espacos():
    """Duas leituras da MESMA consulta com ids diferentes tem a mesma forma -
    e por forma que se enxerga um N+1."""
    a = "SELECT x FROM t WHERE g = 3 AND nome = 'peitoral'"
    b = "SELECT x\n  FROM t\n  WHERE g = 11 AND nome = 'costas'"
    assert forma(a) == forma(b)


def test_forma_distingue_consultas_diferentes():
    assert forma("SELECT a FROM t") != forma("SELECT b FROM t")


def test_repetidos_aponta_a_consulta_em_laco():
    statements = [SELECT_POR_GRUPO.replace("%s", str(g)) for g in range(12)]
    statements.append("SELECT 1")
    (vezes, _sql), *_ = repetidos(statements)
    assert vezes == 12


def test_ordem_de_grandeza_passa_abaixo_do_teto():
    assert_ordem_de_grandeza([("overview", 1.5), ("muscles", 3.2)], backend="pymysql")


def test_ordem_de_grandeza_nao_reprova_por_milissegundos():
    """O ponto da conversao: 3,07s contra um aceite de 3,0s deixou de ser
    falha. So regressao de ordem de grandeza reprova."""
    assert_ordem_de_grandeza([("overview", 3.07)], backend="pymysql")


def test_ordem_de_grandeza_reprova_regressao_de_ordem_de_grandeza():
    with pytest.raises(AssertionError) as erro:
        assert_ordem_de_grandeza(
            [("overview", REDE_ORDEM_DE_GRANDEZA_S + 1.0)], backend="data-api"
        )
    assert "overview" in str(erro.value)
    assert "API Gateway" in str(erro.value)


def test_no_maximo_statements_passa_dentro_do_teto():
    assert_no_maximo_statements(["SELECT 1"] * 4, teto=8, label="x", backend="pymysql")


def test_no_maximo_statements_reprova_n_mais_1_e_mostra_a_consulta_repetida():
    statements = [SELECT_POR_GRUPO.replace("%s", str(g)) for g in range(24)]
    with pytest.raises(AssertionError) as erro:
        assert_no_maximo_statements(statements, teto=8, label="alerts", backend="pymysql")
    mensagem = str(erro.value)
    assert "24 statements" in mensagem
    # O diagnostico tem que apontar a consulta que voltou a rodar em laco.
    assert "24x" in mensagem
    assert "exercise_catalog" in mensagem


def test_reduziu_statements_passa_quando_o_ganho_existe():
    assert_reduziu_statements(
        ["s"] * 18, ["s"] * 4, label="M20", backend="pymysql"
    )


def test_reduziu_statements_reprova_quando_o_ganho_some():
    """E este o cenario da regressao artificial do PR: a leitura agregada
    deixa de agregar e as duas contagens empatam."""
    with pytest.raises(AssertionError) as erro:
        assert_reduziu_statements(["s"] * 18, ["s"] * 18, label="M20", backend="pymysql")
    assert "18 statements contra 18" in str(erro.value)


def test_reduziu_statements_pode_exigir_um_fator():
    assert_reduziu_statements(["s"] * 18, ["s"] * 4, label="M20", backend="pymysql", fator=4.0)
    with pytest.raises(AssertionError):
        assert_reduziu_statements(["s"] * 18, ["s"] * 10, label="M20", backend="pymysql", fator=4.0)

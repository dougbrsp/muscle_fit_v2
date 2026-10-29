"""`require_user_id` (F0.1, plano §15): nenhum servico novo aceita `None`."""

from __future__ import annotations

import pytest

from app.intelligence.dashboard.identity import require_user_id


def test_none_e_rejeitado_com_mensagem_clara():
    with pytest.raises(ValueError) as exc:
        require_user_id(None)
    assert str(exc.value) == "user_id obrigatorio"


@pytest.mark.parametrize("valor, esperado", [(1, 1), (42, 42), ("7", 7), (3.0, 3)])
def test_valores_validos_viram_int(valor, esperado):
    resultado = require_user_id(valor)
    assert resultado == esperado
    assert type(resultado) is int


@pytest.mark.parametrize("ruim", [0, -1, True, False, "abc", "", 2.5, [], {}])
def test_valores_invalidos_sao_rejeitados(ruim):
    with pytest.raises(ValueError, match="user_id invalido"):
        require_user_id(ruim)

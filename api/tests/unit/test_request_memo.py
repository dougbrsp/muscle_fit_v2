"""`app.request_memo`: memo POR REQUISICAO (PERF-01/02/04).

O contrato que importa: nunca serve valor velho para a requisicao seguinte,
nunca memoiza fora de um GET e nunca entrega o mesmo objeto mutavel duas vezes.
"""

from __future__ import annotations

import asyncio

from app import request_memo


def test_sem_escopo_le_a_cada_chamada():
    chamadas = []

    def loader():
        chamadas.append(1)
        return {"n": len(chamadas)}

    assert request_memo.memoize(("k", 1), loader) == {"n": 1}
    assert request_memo.memoize(("k", 1), loader) == {"n": 2}


def test_dentro_do_escopo_le_uma_vez_e_chaves_diferentes_nao_se_misturam():
    chamadas = []

    def loader(valor):
        def run():
            chamadas.append(valor)
            return valor

        return run

    with request_memo.request_scope():
        assert request_memo.memoize(("k", 1), loader("a")) == "a"
        assert request_memo.memoize(("k", 1), loader("b")) == "a"
        assert request_memo.memoize(("k", 2), loader("c")) == "c"
    assert chamadas == ["a", "c"]


def test_escopo_novo_nao_enxerga_o_anterior():
    with request_memo.request_scope():
        request_memo.memoize(("k", 1), lambda: "velho")
    with request_memo.request_scope():
        assert request_memo.memoize(("k", 1), lambda: "novo") == "novo"


def test_devolve_copia_para_o_chamador_poder_mutar():
    with request_memo.request_scope():
        primeiro = request_memo.memoize(("k",), lambda: {"slots": [1, 2]})
        primeiro["slots"].append(3)
        assert request_memo.memoize(("k",), lambda: {"slots": []}) == {"slots": [1, 2]}


def test_forget_apaga_so_o_nome_escrito():
    with request_memo.request_scope():
        request_memo.memoize(("trilhas", 1), lambda: "t1")
        request_memo.memoize(("trilhas", 2), lambda: "t2")
        request_memo.memoize(("outro", 1), lambda: "o1")
        request_memo.forget("trilhas")
        assert request_memo.memoize(("trilhas", 1), lambda: "novo") == "novo"
        assert request_memo.memoize(("trilhas", 2), lambda: "novo2") == "novo2"
        assert request_memo.memoize(("outro", 1), lambda: "x") == "o1"


def test_excecao_do_loader_nao_e_memoizada():
    def falha():
        raise RuntimeError("banco")

    with request_memo.request_scope():
        try:
            request_memo.memoize(("k",), falha)
        except RuntimeError:
            pass
        assert request_memo.memoize(("k",), lambda: "ok") == "ok"


def _chamar(metodo: str):
    """Chama o middleware ASGI e devolve o que o app interno viu."""
    visto = {}

    async def app(scope, receive, send):
        visto["calls"] = [
            request_memo.memoize(("k",), lambda: "primeiro"),
            request_memo.memoize(("k",), lambda: "segundo"),
        ]

    mw = request_memo.RequestMemoMiddleware(app)
    asyncio.run(mw({"type": "http", "method": metodo}, None, None))
    return visto["calls"]


def test_middleware_so_memoiza_get_e_head():
    assert _chamar("GET") == ["primeiro", "primeiro"]
    assert _chamar("HEAD") == ["primeiro", "primeiro"]
    for metodo in ("POST", "PUT", "PATCH", "DELETE"):
        assert _chamar(metodo) == ["primeiro", "segundo"]


def test_middleware_descarta_o_escopo_ao_fim_da_requisicao():
    _chamar("GET")
    assert request_memo.memoize(("k",), lambda: "fora") == "fora"
    assert request_memo.memoize(("k",), lambda: "fora2") == "fora2"

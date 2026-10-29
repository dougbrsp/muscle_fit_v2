"""Memo POR REQUISICAO para leituras que a mesma chamada repete (PERF-01/02/04).

Problema medido (DEV, usuario 1): `/analytics/dashboard/overview` relia o Modelo
inteiro (~6 statements) e `users.timezone` duas vezes; `/reports/markdown`, tres
vezes o Modelo, quatro as atribuicoes e duas os nomes de grupos;
`/exercises/suggested-progression`, `timezone`, trilhas e equipamento duas vezes.
Cada statement a mais e uma ida ao banco (na RDS Data API, 1 HTTP).

Contrato (o que impede este modulo de virar cache de dados velhos):

- O memo vive SO durante uma requisicao `GET`/`HEAD`: `RequestMemoMiddleware`
  abre o escopo antes da rota e o descarta no fim. Nunca e global - o "vinculo
  vivo" do Modelo (docstring de `get_template_if_assigned`) continua valendo: a
  proxima requisicao le o banco de novo.
- Fora de escopo (POST/PUT/DELETE, jobs, Lambdas de fila, scripts, testes) nada
  e memoizado: `memoize` apenas chama o `loader`. Escrita e leitura-apos-escrita
  nunca passam por aqui.
- Quem escreve algo que este memo guarda chama `forget(<nome>)` (ex.:
  `progression_state.save_track`), para uma leitura posterior da mesma
  requisicao nao ver o valor antigo.
- O valor guardado e devolvido por COPIA PROFUNDA: o chamador pode mutar a
  resposta (os servicos anexam campos ao Modelo) sem contaminar o proximo.

Nenhuma regra mora aqui: so a decisao de "ja li isto nesta requisicao". As
regras continuam nos motores donos (`docs/motores/FONTES_DA_VERDADE.md`).
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Hashable, Iterator, Optional, TypeVar

T = TypeVar("T")

_MEMO: ContextVar[Optional[dict[Hashable, Any]]] = ContextVar("musclefit_request_memo", default=None)


def memoize(key: Hashable, loader: Callable[[], T]) -> T:
    """`loader()` uma vez por escopo para a mesma `key`. A `key` deve comecar por
    um nome (str) - e esse nome que `forget` usa. Sem escopo ativo, so chama o
    `loader`. Excecao do `loader` nao e memoizada."""
    store = _MEMO.get()
    if store is None:
        return loader()
    if key in store:
        return copy.deepcopy(store[key])
    value = loader()
    store[key] = copy.deepcopy(value)
    return value


def forget(name: str) -> None:
    """Esquece tudo que foi memoizado sob `name` (primeiro elemento da chave).
    Chamado por quem ESCREVE o dado, no mesmo escopo."""
    store = _MEMO.get()
    if store is None:
        return
    for key in [k for k in store if k == name or (isinstance(k, tuple) and k and k[0] == name)]:
        store.pop(key, None)


@contextmanager
def request_scope() -> Iterator[dict[Hashable, Any]]:
    """Abre um escopo (middleware, testes). Escopos aninhados reaproveitam o de fora."""
    if _MEMO.get() is not None:
        yield _MEMO.get()  # type: ignore[misc]
        return
    store: dict[Hashable, Any] = {}
    token = _MEMO.set(store)
    try:
        yield store
    finally:
        _MEMO.reset(token)


class RequestMemoMiddleware:
    """Middleware ASGI puro: escopo de memo so para `GET`/`HEAD` (leitura).

    O contexto e copiado para a thread da rota sincrona (anyio), entao rota e
    dependencias enxergam o mesmo escopo."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        with request_scope():
            await self.app(scope, receive, send)


__all__ = ["RequestMemoMiddleware", "forget", "memoize", "request_scope"]

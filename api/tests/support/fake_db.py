"""Dubles de conexao/cursor reaproveitaveis nos testes unitarios (COD-13 da
auditoria de 2026-10-08, arquivo privado).

So o "encanamento" que se repetia, IDENTICO, em varios arquivos: a conexao
que entrega um cursor, o `with` que nao faz nada, o cursor que explode e o
`transaction()` falso. O cursor ROTEIRIZADO de cada teste (quais linhas cada
SQL devolve) continua no proprio teste: e ele que diz o que o teste verifica.

    cur = _MeuCursor(...)
    monkeypatch.setattr(modulo, "transaction", fake_transaction(cur))
    conn = SharedCursorConn(cur)      # conn.cursor() is cur; conn.cur is cur
    conn = CursorFactoryConn(_MeuCursor, linhas)  # cursor novo a cada chamada
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator


class NullContext:
    """`with x as y:` com `y is x` e sem engolir excecao (o `__enter__`/
    `__exit__` minimo de cursor e conexao)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class SharedCursorConn:
    """Conexao cujo `cursor()` devolve SEMPRE o mesmo cursor, exposto em
    `.cur` para o teste inspecionar o que foi executado."""

    def __init__(self, cur: Any) -> None:
        self.cur = cur

    def cursor(self) -> Any:
        return self.cur


class CursorFactoryConn:
    """Conexao cujo `cursor()` devolve um cursor NOVO a cada chamada:
    `factory(*args)`."""

    def __init__(self, factory: Callable[..., Any], *args: Any) -> None:
        self._factory = factory
        self._args = args

    def cursor(self) -> Any:
        return self._factory(*self._args)


class RaisingCursor(NullContext):
    """Cursor cujo `execute` levanta `make_error()` (uma excecao NOVA a cada
    chamada, como o `raise RuntimeError(...)` escrito no corpo)."""

    def __init__(self, make_error: Callable[[], BaseException]) -> None:
        self._make_error = make_error

    def execute(self, sql: str, params: Any = None) -> None:
        raise self._make_error()


class LoopingExecuteMany:
    """`executemany` como N `execute`, na ordem recebida: o contrato dos dois
    shims (`db_dataapi`/`db_pymysql`, COD-12) visto de um cursor roteirizado.
    Herde dele no cursor do teste que passa por um caminho em lote."""

    def executemany(self, sql: str, seq_of_params: Any) -> None:
        for params in seq_of_params:
            self.execute(sql, params)


def fake_transaction(cur: Any) -> Callable[[], Any]:
    """Substituto de `app.db.transaction` para `monkeypatch.setattr`: cada
    `with transaction() as conn:` recebe uma `SharedCursorConn(cur)`. Sem
    commit/rollback: excecao dentro do `with` sobe igual."""

    @contextmanager
    def _transaction() -> Iterator[SharedCursorConn]:
        yield SharedCursorConn(cur)

    return _transaction


__all__ = [
    "CursorFactoryConn",
    "LoopingExecuteMany",
    "NullContext",
    "RaisingCursor",
    "SharedCursorConn",
    "fake_transaction",
]

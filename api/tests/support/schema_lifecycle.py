"""Ciclo de vida dos schemas descartaveis da suite (criar/varrer/derrubar).

Contexto da divida que este modulo paga: a suite de integracao cria schemas
MySQL (`musclefit_test`, `musclefit_fk023_test`, `musclefit_p028_test`, ...)
com DROP+CREATE no inicio, mas NUNCA os derruba no final. No MySQL do DEV
local (onde o banco de verdade se chama `fitness`) eles se acumulavam a cada
execucao.

Tres peças:

1. `run_scoped_database()` - torna o nome base unico por execucao
   (`musclefit_run<token>_test`). Como todos os schemas derivados sao
   construidos a partir de `MYSQL_DATABASE` (padrao
   `f"{base[:-len('_test')]}_<tag>_test"`), a unicidade se propaga sozinha
   para TODOS eles: duas execucoes simultaneas nunca compartilham schema, logo
   o teardown de uma nao derruba o schema da outra.

2. `is_disposable_test_schema()` - o FILTRO DE SEGURANCA. Um bug aqui apaga o
   banco de desenvolvimento do dono do produto, entao ele e deliberadamente
   restritivo: alem da allowlist por regex (prefixo do stem + sufixo `_test`
   obrigatorios), ha uma denylist explicita e inegociavel
   (`PROTECTED_SCHEMAS`). `fitness`, `musclefit`, `musclefit_prod`, `mysql`,
   `information_schema`, `performance_schema` e `sys` NUNCA sao selecionados.

3. `sweep_orphan_schemas()` - varredura de orfaos no inicio da execucao,
   que limpa o passivo historico. Uma execucao viva sinaliza que esta viva
   segurando um LOCK NOMEADO do MySQL (`GET_LOCK`) com o proprio token: a
   varredura so derruba o schema de outra execucao quando o lock dela nao
   esta mais em uso. O sinal e barato, nao exige DDL nenhum e o proprio
   servidor o libera quando a conexao morre (Ctrl-C, kill, queda de rede) -
   e por isso que nao existe daemon nem arquivo de estado aqui.

Nada aqui roda fora do processo de testes.
"""

from __future__ import annotations

import os
import re
import secrets
from typing import Iterable

import pymysql

# Denylist inegociavel: nunca derrubado, aconteca o que acontecer com o regex.
# `fitness` = banco do ambiente DEV local; `musclefit` = nome de producao.
PROTECTED_SCHEMAS = frozenset(
    {
        "fitness",
        "musclefit",
        "mysql",
        "information_schema",
        "performance_schema",
        "sys",
    }
)

TEST_SUFFIX = "_test"

# Token de execucao: `run` + hex. O prefixo `run` evita confusao com tags
# legitimas de teste que ja comecam com `r` (ex.: `musclefit_r029_test`).
_RUN_TOKEN_RE = re.compile(r"^run[0-9a-f]{10}$")

# Prefixo dos locks nomeados que sinalizam "esta execucao ainda esta viva".
RUN_LOCK_PREFIX = "musclefit_test_run:"


def _stem(database: str) -> str:
    """`musclefit_test` -> `musclefit`."""
    if not database.endswith(TEST_SUFFIX):
        raise ValueError(f"Schema de teste precisa terminar em {TEST_SUFFIX!r}: {database!r}")
    return database[: -len(TEST_SUFFIX)]


def new_run_token() -> str:
    return "run" + secrets.token_hex(5)


def run_scoped_database(base_database: str, token: str) -> str:
    """`musclefit_test` + `run<hex>` -> `musclefit_run<hex>_test`."""
    if not _RUN_TOKEN_RE.match(token):
        raise ValueError(f"Token de execucao invalido: {token!r}")
    return f"{_stem(base_database)}_{token}{TEST_SUFFIX}"


def family_regex(stem: str) -> re.Pattern[str]:
    """Regex dos schemas descartaveis derivados de `stem`.

    Casa `<stem>_test` e `<stem>_<tag>[_<tag>...]_test` - exatamente o que as
    fixtures constroem. NAO casa `<stem>` sozinho, nem qualquer nome que nao
    termine em `_test`.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_]*", stem):
        raise ValueError(f"Stem de schema de teste invalido: {stem!r}")
    return re.compile(rf"^{re.escape(stem)}(?:_[A-Za-z0-9]+)*{re.escape(TEST_SUFFIX)}$")


def is_disposable_test_schema(name: str, *, stem: str) -> bool:
    """True so para nomes que sao INEQUIVOCAMENTE schema de teste.

    Regras (todas precisam valer):
      - nao esta em `PROTECTED_SCHEMAS`;
      - termina em `_test`;
      - comeca pelo stem da execucao corrente (default `musclefit`);
      - o miolo entre stem e `_test` so tem segmentos alfanumericos.
    """
    if name in PROTECTED_SCHEMAS:
        return False
    if not name.endswith(TEST_SUFFIX):
        return False
    return bool(family_regex(stem).match(name))


def run_token_of(name: str, *, stem: str) -> str | None:
    """Token da execucao dona do schema, quando houver.

    `musclefit_run0123456789_p028_test` -> `run0123456789`.
    Schemas legados (criados antes deste modulo) nao tem token -> None.
    """
    if not is_disposable_test_schema(name, stem=stem):
        return None
    middle = name[len(stem) : -len(TEST_SUFFIX)].strip("_")
    if not middle:
        return None
    first = middle.split("_", 1)[0]
    return first if _RUN_TOKEN_RE.match(first) else None


def connect(*, host: str, port: int, user: str, password: str) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=host, port=port, user=user, password=password, autocommit=True, charset="utf8mb4"
    )


def list_schemas(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SHOW DATABASES")
        return [row[0] for row in cur.fetchall()]


def drop_schemas(conn, names: Iterable[str], *, stem: str) -> list[str]:
    """Derruba os schemas informados, revalidando o filtro de seguranca item a
    item (defesa em profundidade: mesmo chamado com uma lista errada, este
    ponto se recusa a tocar em qualquer nome fora do padrao)."""
    dropped: list[str] = []
    with conn.cursor() as cur:
        for name in names:
            if not is_disposable_test_schema(name, stem=stem):
                raise ValueError(f"Recusando derrubar schema fora do padrao de teste: {name!r}")
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
            dropped.append(name)
    return dropped


def run_lock_name(token: str) -> str:
    return f"{RUN_LOCK_PREFIX}{token}"


def acquire_run_lock(conn, token: str) -> bool:
    """Marca esta execucao como viva enquanto `conn` estiver aberta.

    O lock morre com a conexao - inclusive quando o pytest e interrompido com
    Ctrl-C ou morto a sinal -, entao nenhum schema fica "protegido para
    sempre" por uma execucao que nao existe mais.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0)", (run_lock_name(token),))
        row = cur.fetchone()
    return bool(row and row[0] == 1)


def run_is_alive(conn, token: str) -> bool:
    """True se alguma conexao ainda segura o lock da execucao `token`."""
    with conn.cursor() as cur:
        cur.execute("SELECT IS_USED_LOCK(%s)", (run_lock_name(token),))
        row = cur.fetchone()
    return bool(row and row[0] is not None)


def schema_in_use(conn, name: str) -> bool:
    """True se alguma conexao esta com este schema selecionado agora.

    Rede de seguranca para os schemas LEGADOS (sem token no nome, criados
    antes deste modulo): eles nao tem lock de execucao, entao a varredura nao
    tem como saber se uma suite antiga ainda esta usando o schema. Uma conexao
    com o schema selecionado e o melhor sinal disponivel - e derrubar um schema
    em uso travaria no metadata lock de qualquer forma.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.processlist WHERE db = %s LIMIT 1", (name,)
        )
        return cur.fetchone() is not None


def sweep_orphan_schemas(conn, *, stem: str, keep_token: str | None = None) -> list[str]:
    """Derruba schemas de teste deixados para tras por execucoes anteriores.

    - schemas da execucao corrente (`keep_token`) sao preservados;
    - schemas de outra execucao que ainda segura seu lock nomeado sao
      preservados (suite rodando em paralelo);
    - o resto - inclusive os schemas legados, sem token no nome, que sao o
      passivo historico acumulado no MySQL de DEV - cai, exceto se alguma
      conexao estiver com o schema selecionado (ver `schema_in_use`).
    """
    candidates: list[str] = []
    for name in list_schemas(conn):
        if not is_disposable_test_schema(name, stem=stem):
            continue
        token = run_token_of(name, stem=stem)
        if token:
            if token == keep_token or run_is_alive(conn, token):
                continue
        elif schema_in_use(conn, name):
            continue
        candidates.append(name)
    return drop_schemas(conn, candidates, stem=stem)


def drop_run_schemas(conn, *, stem: str, token: str) -> list[str]:
    """Derruba TODOS os schemas criados pela execucao corrente."""
    names = [
        name
        for name in list_schemas(conn)
        if is_disposable_test_schema(name, stem=stem) and run_token_of(name, stem=stem) == token
    ]
    return drop_schemas(conn, names, stem=stem)


def stem_from_env() -> str:
    """Stem base da familia de schemas de teste (`musclefit` por default).

    Vem de `TEST_MYSQL_DATABASE` (o nome CRU, sem o token da execucao), para
    que a varredura tambem alcance os orfaos legados criados antes de existir
    token - o passivo historico acumulado no MySQL de DEV.
    """
    return _stem(os.environ.get("TEST_MYSQL_DATABASE", "musclefit_test"))


def token_from_env() -> str | None:
    return os.environ.get("TEST_MYSQL_RUN_TOKEN")


def mysql_kwargs_from_env() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )

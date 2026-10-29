"""Monta schemas de teste isolados a partir de api/db/migrations/.

Duas portas de entrada, com proposito diferente:

- `recreate_schema_with_migrations`: BASELINE (so os `.sql`, 001..021). E o
  ponto de partida dos testes DE MIGRACAO, que precisam do estado ANTERIOR a
  migracao que estao exercitando.
- `recreate_schema_with_all_migrations`: baseline + TODAS as `.py` na ordem de
  versao, pelo mesmo `up(db)` que roda em producao. E o que a fixture
  `mysql_schema` (schema compartilhado da suite de integracao) usa, para que
  esse schema reflita PRODUCAO sem lista escrita a mao.

As migrations `.sql` foram escritas para o `docker-entrypoint-initdb.d` do MySQL
(cliente `mysql`), entao usam `DELIMITER` (diretiva do cliente, nao SQL) e
fixam `CREATE DATABASE IF NOT EXISTS fitness` / `USE fitness;`. Este runner
(so de testes) traduz as duas coisas:

- `fitness` nesses dois comandos vira o schema de teste;
- blocos com `DELIMITER` customizado sao enviados um comando por vez; o
  restante vai inteiro com CLIENT.MULTI_STATEMENTS (o proprio servidor faz o
  parsing de strings/comentarios, sem split ingenuo por `;`).

Nao e o runner de migracoes da aplicacao (PR-02) - so infraestrutura de teste.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymysql
from pymysql.constants import CLIENT

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_DELIMITER_RE = re.compile(r"^\s*DELIMITER\s+(\S+)\s*$", re.IGNORECASE)
_USE_FITNESS_RE = re.compile(r"^(\s*USE\s+)`?fitness`?(\s*;)", re.IGNORECASE | re.MULTILINE)
_CREATE_FITNESS_RE = re.compile(
    r"(CREATE\s+DATABASE\s+IF\s+NOT\s+EXISTS\s+)`?fitness`?\b", re.IGNORECASE
)


def migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"Nenhuma migration encontrada em {MIGRATIONS_DIR}")
    return files


# ---------------------------------------------------------------------------
# Migracoes `.py` e o schema de teste compartilhado
# ---------------------------------------------------------------------------
#
# Da 022 em diante TODA migracao e `.py`. Antes do M25 nenhuma delas era
# aplicada automaticamente: quem entrava no schema compartilhado era uma lista
# escrita a mao na fixture `mysql_schema`. Isso mordeu tres vezes - a 040 e a
# 043 ficaram de fora (notado e deixado passar) e a 048 derrubou o CI do PR
# #203 depois de uma hora de suite (`Table '..._test.context_events' doesn't
# exist`).
#
# Agora `recreate_schema_with_all_migrations` DESCOBRE as `.py` no diretorio e
# aplica TODAS em ordem de versao, com o mesmo `up(db)` de producao. Uma
# migracao nova entra no schema compartilhado sozinha: nao ha lista para
# esquecer.
#
# O dicionario abaixo e a unica excecao possivel, e existe para ser lido:
# migracao que por algum motivo nao pode ser aplicada no schema compartilhado
# fica aqui COM O MOTIVO. Hoje esta VAZIO - as 28 migracoes `.py` (022..049)
# aplicam em sequencia num schema recem-criado, exatamente como em producao.
#
# Em particular, NAO precisam mais estar de fora as que sao "testadas a partir
# do estado anterior a elas" (022, 023, 024, 025, 027, 028, 030): cada um
# desses testes ja monta o PROPRIO schema isolado com
# `recreate_schema_with_migrations` (baseline 001..021), e por isso nao e
# afetado pelo que o schema compartilhado recebe.
PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE: dict[str, str] = {}

# Nome de migracao: NNN_descricao.py (mesmo formato de `app.migrate`).
_PY_MIGRATION_NAME_RE = re.compile(r"^(?P<version>\d{3})_[A-Za-z0-9_]+\.py$")


def py_migration_files() -> list[Path]:
    """As `.py` aplicadas no schema compartilhado, em ordem de VERSAO.

    Descoberta pelo diretorio (nao por lista): acrescentar uma migracao basta
    para ela entrar. Arquivo com cara de migracao e nome invalido levanta erro
    aqui, e nao no primeiro teste que fosse usar a tabela."""
    ordered: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        match = _PY_MIGRATION_NAME_RE.match(path.name)
        if not match:
            raise RuntimeError(
                f"Arquivo .py com nome invalido em {MIGRATIONS_DIR}: {path.name} "
                "(esperado NNN_descricao.py)"
            )
        if path.name in PY_MIGRATIONS_FORA_DO_SCHEMA_DE_TESTE:
            continue
        ordered.append((int(match.group("version")), path))
    ordered.sort(key=lambda item: item[0])
    return [path for _, path in ordered]


def _retarget_schema(sql: str, database: str) -> str:
    sql = _USE_FITNESS_RE.sub(rf"\g<1>`{database}`\g<2>", sql)
    return _CREATE_FITNESS_RE.sub(rf"\g<1>`{database}`", sql)


def _split_blocks(sql: str) -> list[str]:
    """Quebra o arquivo em blocos executaveis. Texto com delimitador `;` sai
    como UM bloco multi-statement; texto sob `DELIMITER $$` sai um bloco por
    comando (corpo de procedure com `;` internos preservado)."""
    blocks: list[str] = []
    delimiter = ";"
    buffer: list[str] = []

    def flush_default() -> None:
        text = "\n".join(buffer).strip()
        if text:
            blocks.append(text)
        buffer.clear()

    for line in sql.splitlines():
        match = _DELIMITER_RE.match(line)
        if match:
            if delimiter == ";":
                flush_default()
            else:
                leftover = "\n".join(buffer).strip()
                if leftover:
                    blocks.append(leftover)
                buffer.clear()
            delimiter = match.group(1)
            continue

        if delimiter == ";":
            buffer.append(line)
            continue

        stripped = line.rstrip()
        if stripped.endswith(delimiter):
            buffer.append(stripped[: -len(delimiter)])
            statement = "\n".join(buffer).strip()
            if statement:
                blocks.append(statement)
            buffer.clear()
        else:
            buffer.append(line)

    if delimiter == ";":
        flush_default()
    elif "\n".join(buffer).strip():
        raise ValueError(f"Bloco sem terminador {delimiter!r} no fim do arquivo")
    return blocks


def _execute_block(cursor: pymysql.cursors.Cursor, block: str) -> None:
    cursor.execute(block)
    while cursor.nextset():
        pass


def recreate_schema_with_migrations(
    *, host: str, port: int, user: str, password: str, database: str
) -> list[str]:
    """DROP + CREATE do schema de teste e aplica o BASELINE (`.sql`, 001..021)
    em ordem. Retorna os nomes dos arquivos aplicados.

    E deliberadamente so o baseline: os testes DE MIGRACAO partem daqui porque
    precisam do estado ANTERIOR a migracao que exercitam. Para um schema no
    formato de producao use `recreate_schema_with_all_migrations`."""
    if not database.endswith("_test"):
        raise ValueError(f"Recusando recriar schema que nao termina em '_test': {database!r}")

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS,
        charset="utf8mb4",
    )
    applied: list[str] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cursor.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
            cursor.execute(f"USE `{database}`")
            for path in migration_files():
                sql = _retarget_schema(path.read_text(encoding="utf-8"), database)
                try:
                    for block in _split_blocks(sql):
                        _execute_block(cursor, block)
                except pymysql.err.Error as exc:
                    raise RuntimeError(f"Falha aplicando migration {path.name}: {exc}") from exc
                applied.append(path.name)
    finally:
        conn.close()
    return applied


def apply_py_migrations(*, host: str, port: int, user: str, password: str, database: str) -> list[str]:
    """Aplica as `.py` de `py_migration_files()` no schema dado, em ordem de
    versao, pelo MESMO `up(db)` que o runner de producao chama
    (`app.migrate.load_python_migration` + `MigrationDB`).

    Commit por migracao, como no runner: DDL no MySQL ja faz commit implicito, e
    assim uma falha na migracao N nao desfaz o backfill da N-1 (o erro aponta o
    arquivo exato)."""
    # Import tardio: `tests/support/migrations.py` e importado por teste
    # unitario, que nao deve depender de `app` nem de conexao.
    from app import migrate as m
    from app.db_pymysql import Connection as PyMySQLConnection

    applied: list[str] = []
    conn = PyMySQLConnection(host, port, user, password, database)
    try:
        for path in py_migration_files():
            try:
                m.load_python_migration(path)(m.MigrationDB(conn))
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - re-erguido nomeando o arquivo
                raise RuntimeError(f"Falha aplicando migration {path.name}: {exc}") from exc
            applied.append(path.name)
    finally:
        conn.close()
    return applied


def recreate_schema_with_all_migrations(
    *, host: str, port: int, user: str, password: str, database: str
) -> list[str]:
    """Schema de teste no formato de PRODUCAO: baseline (`.sql`) + TODAS as
    `.py` aplicaveis, descobertas no diretorio. Retorna os arquivos aplicados,
    na ordem."""
    kwargs = dict(host=host, port=port, user=user, password=password, database=database)
    return [*recreate_schema_with_migrations(**kwargs), *apply_py_migrations(**kwargs)]


# Migracoes que MUDAM uma forma que uma migracao anterior confere por igualdade
# quando reexecuta:
# - a 068 troca a UK `uk_ptr_user_pair` de `progression_tracks` (criada e
#   conferida pela 036) por `uk_ptr_user_pair_context`;
# - a 075 acrescenta `neck`, `forearm_l`, `forearm_r` no fim do ENUM
#   `body_measurements.site`, que a 047 confere por igualdade com o ENUM dela
#   ("047: body_measurements.site e enum(...), esperado enum(...)");
# - a 093 acrescenta `training_goal` em `uk_ptr_user_pair_context` (conferida
#   pela 068) e em `uk_pgs_user_group` (conferida pela 036). O `down` dela roda
#   PRIMEIRO (ordem inversa), devolvendo as chaves a forma que a 068 confere.
# Em producao a 036 e a 047 nunca reexecutam: ficam registradas em
# `schema_migration_files` e o runner pula arquivo registrado (e recusa
# checksum divergente, entao a migracao antiga NAO pode ser editada). So os
# testes que APAGAM a tabela de controle e rodam o runner de novo sobre um
# schema ja convergido a reexecutam - e precisam voltar a forma anterior a
# essas migracoes (pelo `down` delas), como `fk023.reset_to_pre_023` faz com a
# 023. O `down` da 075 recusa voltar o ENUM se alguma linha usa os locais novos.
RESHAPING_MIGRATIONS: tuple[str, ...] = (
    "068_progression_track_fatigue_context.py",
    "075_body_measurements_sites.py",
    "093_training_goal.py",
)


def undo_reshaping_migrations(conn) -> list[str]:
    """`down` das `RESHAPING_MIGRATIONS` (da mais nova para a mais antiga) na
    conexao dada; sem a tabela, nada. Idempotente. Devolve os statements."""
    from app import migrate as m

    executed: list[str] = []
    for name in reversed(RESHAPING_MIGRATIONS):
        db = m.MigrationDB(conn)
        m.load_python_migration(MIGRATIONS_DIR / name).__globals__["down"](db)
        executed.extend(db.statements)
    if hasattr(conn, "commit"):
        conn.commit()
    return executed

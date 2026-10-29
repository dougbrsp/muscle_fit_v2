"""Configuracao raiz do pytest (vale para tests/unit e tests/integration).

Regra anti-vazamento dev/prod: a configuracao de banco abaixo existe SO
dentro do processo de testes. Nada aqui altera defaults da aplicacao
(`app/db.py` continua com `data-api` como default) nem infra/Lambdas.

Por que forcar `DB_BACKEND=pymysql` ja na raiz (inclusive para unit):
`app/db.py` escolhe o backend no momento do import. Forcando pymysql antes de
qualquer `import app...`, um teste unitario que por engano toque o banco falha
com erro de conexao TCP local, em vez de tentar a RDS Data API (boto3) com as
credenciais AWS que por acaso estejam no ambiente de quem roda os testes.

As variaveis `MYSQL_*` sao SEMPRE derivadas de `TEST_MYSQL_*` (nunca herdadas
de um `.env` de dev), para que o schema de teste descartavel nunca aponte para
o banco `fitness` de desenvolvimento.

Nome UNICO por execucao: `MYSQL_DATABASE` recebe `<stem>_run<token>_test` em
vez do `TEST_MYSQL_DATABASE` cru. Como todos os schemas auxiliares da suite
sao derivados de `MYSQL_DATABASE` (`f"{base[:-len('_test')]}_<tag>_test"`), a
unicidade se propaga para todos eles e duas execucoes simultaneas nunca
disputam o mesmo schema - requisito para o teardown de
`tests/integration/conftest.py` poder derrubar o que a execucao criou sem
atropelar outra. `TEST_MYSQL_UNIQUE_SCHEMA=0` desliga (util para depurar o
schema deixado por uma execucao).
"""

from __future__ import annotations

import os
import re

import pytest

from tests.support import schema_lifecycle

TEST_MYSQL_HOST = os.environ.get("TEST_MYSQL_HOST", "127.0.0.1")
TEST_MYSQL_PORT = os.environ.get("TEST_MYSQL_PORT", "3306")
TEST_MYSQL_USER = os.environ.get("TEST_MYSQL_USER", "root")
TEST_MYSQL_PASSWORD = os.environ.get("TEST_MYSQL_PASSWORD", "musclefit-root")
TEST_MYSQL_DATABASE = os.environ.get("TEST_MYSQL_DATABASE", "musclefit_test")

# O schema de teste e DROPado e recriado a cada sessao de integracao: exige
# sufixo `_test` para nunca apagar um banco de verdade por engano.
if not re.fullmatch(r"[A-Za-z0-9_]+_test", TEST_MYSQL_DATABASE):
    raise pytest.UsageError(
        f"TEST_MYSQL_DATABASE={TEST_MYSQL_DATABASE!r} invalido: precisa terminar em '_test'."
    )

# Stem compartilhado por TODOS os schemas desta execucao (`musclefit` para o
# default `musclefit_test`). E o prefixo que a varredura de orfaos usa como
# filtro - nunca casa `fitness` nem `musclefit` (ver schema_lifecycle).
TEST_SCHEMA_STEM = TEST_MYSQL_DATABASE[: -len("_test")]

# Token desta execucao (herdado por workers de execucao paralela via env).
TEST_RUN_TOKEN = os.environ.get("TEST_MYSQL_RUN_TOKEN") or schema_lifecycle.new_run_token()
os.environ["TEST_MYSQL_RUN_TOKEN"] = TEST_RUN_TOKEN

if os.environ.get("TEST_MYSQL_UNIQUE_SCHEMA", "1") == "0":
    RUN_MYSQL_DATABASE = TEST_MYSQL_DATABASE
else:
    RUN_MYSQL_DATABASE = schema_lifecycle.run_scoped_database(TEST_MYSQL_DATABASE, TEST_RUN_TOKEN)

# Custo do bcrypt DENTRO do processo de teste. O default da aplicacao (12, em
# `app/auth.py`) e deliberadamente caro: ~0,3 s por hash. Os testes que criam
# usuarios e exercitam login/lockout/reset chamam `hash_password` dezenas de
# vezes, e no CI isso custava ~17 s dos 92 s do passo `pytest integration`
# quase so em hash - tempo que nao prova NADA sobre o produto (nenhum teste
# afere o custo do hash; o que eles afirmam e o COMPORTAMENTO: senha certa
# entra, errada nao, 5 erros bloqueiam).
#
# 4 e o minimo do algoritmo. O bcrypt continua o MESMO (o hash gerado aqui e
# verificado aqui), so mais barato.
#
# Mesma regra anti-vazamento do `DB_BACKEND` acima: isto vale SO neste
# processo. `BCRYPT_ROUNDS` nao existe em `infra/` (Lambdas), no
# `docker-compose.yml` nem no `.env.example` - `tests/unit/test_bcrypt_rounds.py`
# reprova o PR que colocar. `setdefault`: quem quiser medir com o custo real
# roda `BCRYPT_ROUNDS=12 python -m pytest ...`.
os.environ.setdefault("BCRYPT_ROUNDS", "4")

os.environ["DB_BACKEND"] = "pymysql"
os.environ["MYSQL_HOST"] = TEST_MYSQL_HOST
os.environ["MYSQL_PORT"] = TEST_MYSQL_PORT
os.environ["MYSQL_USER"] = TEST_MYSQL_USER
os.environ["MYSQL_PASSWORD"] = TEST_MYSQL_PASSWORD
os.environ["MYSQL_DATABASE"] = RUN_MYSQL_DATABASE


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Todo teste sob tests/integration/ recebe o marker `integration`
    automaticamente (permite `-m "not integration"`); sob
    tests/integration/dataapi/ recebe tambem `dataapi`."""
    for item in items:
        parts = item.path.parts
        if "tests" in parts and "integration" in parts[parts.index("tests") :]:
            item.add_marker(pytest.mark.integration)
            if "dataapi" in parts[parts.index("tests") :]:
                item.add_marker(pytest.mark.dataapi)

"""A camada de dados (`app/db.py` -> `db_pymysql.py` / `db_dataapi.py`) so
aceita placeholders `%s` posicionais, nos dois backends. Placeholder nomeado
(`%(campo)s`) com dict de parametros quebra nos dois: PyMySQL recebe a lista
de chaves do dict (`TypeError`) e o shim Data API conta 0 placeholders `%s`
para N parametros (`DataApiError`). Foi assim que POST/PUT
/exercise-mapping-catalog e /import respondiam 500 em qualquer ambiente.

Nao se cria suporte a nomeado so num backend (paridade dev/prod): este teste
barra o padrao no codigo da aplicacao. Unica excecao: strings de formato do
`logging` (`%(message)s`), que nao sao SQL.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
NAMED_PLACEHOLDER = re.compile(r"%\(\w+\)s")
LOGGING_FORMAT = re.compile(r"logging\.(basicConfig|Formatter)\(")


def test_app_sem_placeholder_nomeado_fora_do_logging():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NAMED_PLACEHOLDER.search(line) and not LOGGING_FORMAT.search(line):
                offenders.append(f"{path.relative_to(APP_DIR)}:{number}: {line.strip()}")
    assert offenders == []

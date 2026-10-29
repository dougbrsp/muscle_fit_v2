"""Leitura do bloco de um servico do `docker-compose.yml` para os testes.

Extraido de `tests/unit/test_pr12_modo_b_flags_e_autoridade.py`, que ja fazia
exatamente isto para conferir as flags de motor: com mais de um teste
verificando paridade DEV x producao servico a servico, o parser passa a ser
compartilhado em vez de copiado.

O parser e deliberadamente textual e burro (mesmo espirito de
`tests/support/infra_flags.py`): PyYAML nao esta em `requirements`, e o que
precisa ser verificado e sintaticamente simples - saber se o bloco de um
servico menciona uma env var ou um volume.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_NEXT_SERVICE_RE = re.compile(r"^  [a-z][a-z0-9_-]*:$", re.MULTILINE)


def compose_path() -> Path:
    return ROOT / "docker-compose.yml"


def service_block(nome: str) -> str:
    """Trecho do `docker-compose.yml` que pertence ao servico `nome`
    (da linha `  <nome>:` ate o proximo servico de mesma indentacao)."""
    compose = compose_path().read_text(encoding="utf-8")
    inicio = compose.index(f"\n  {nome}:\n")
    resto = compose[inicio + 1 :]
    proximo = _NEXT_SERVICE_RE.search(resto[1:])
    return resto[: proximo.start() + 1] if proximo else resto


def service_names() -> list[str]:
    """Nomes dos servicos declarados sob `services:`."""
    compose = compose_path().read_text(encoding="utf-8")
    inicio = compose.index("services:")
    fim = compose.find("\nvolumes:", inicio)
    trecho = compose[inicio : fim if fim != -1 else len(compose)]
    return [linha.strip().rstrip(":") for linha in _NEXT_SERVICE_RE.findall(trecho)]

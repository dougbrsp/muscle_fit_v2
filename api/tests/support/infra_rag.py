"""Quais Lambdas de `infra/*.tf` recebem a configuracao de RAG.

Mesma tecnica (e mesmas limitacoes assumidas) de `tests/support/infra_flags.py`:
parser textual, sem `terraform` disponivel no CI de testes de unidade. O que
se quer saber e simples: para cada `resource "aws_lambda_function"`, o
`environment { variables = ... }` dele termina com `RAG_MARKDOWN_DIR` definida?
A variavel pode vir escrita direto no bloco OU via `local.<nome>` (o caso de
`musclefit-plan-generator` / `musclefit-cardio-agent`, que compartilham
`local.ai_generators_env`), entao os locals referenciados tambem sao olhados.

Este modulo NAO altera nada em `infra/` - so le, para que o CI consiga
comparar o mapa de producao com o do `docker-compose.yml`.
"""

from __future__ import annotations

import re
from pathlib import Path

RAG_ENV_NAME = "RAG_MARKDOWN_DIR"

_RESOURCE_RE = re.compile(
    r'^resource\s+"aws_lambda_function"\s+"([A-Za-z0-9_]+)"\s*\{', re.MULTILINE
)
_FUNCTION_NAME_RE = re.compile(r'^\s*function_name\s*=\s*"([^"]+)"', re.MULTILINE)
_LOCAL_REF_RE = re.compile(r"\blocal\.([A-Za-z0-9_]+)\b")


def infra_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "infra"


def _tf_texts() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(infra_dir().rglob("*.tf"))]


def _balanced_block(texto: str, abre_idx: int) -> str:
    """Trecho de `texto` a partir da chave em `abre_idx` ate a chave que a
    fecha. Suficiente aqui: os arquivos nao tem `{` dentro de string."""
    profundidade = 0
    for i in range(abre_idx, len(texto)):
        if texto[i] == "{":
            profundidade += 1
        elif texto[i] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[abre_idx : i + 1]
    return texto[abre_idx:]


def _locals_com_rag() -> set[str]:
    """Nomes de locals cujo corpo define `RAG_MARKDOWN_DIR`."""
    nomes: set[str] = set()
    for texto in _tf_texts():
        for match in re.finditer(r"^\s*([A-Za-z0-9_]+)\s*=\s*\{", texto, re.MULTILINE):
            corpo = _balanced_block(texto, match.end() - 1)
            if re.search(rf"^\s*{RAG_ENV_NAME}\s*=", corpo, re.MULTILINE):
                nomes.add(match.group(1))
    return nomes


def lambdas_com_rag() -> set[str]:
    """`function_name` de cada Lambda que recebe `RAG_MARKDOWN_DIR`."""
    com_rag = _locals_com_rag()
    funcoes: set[str] = set()
    for texto in _tf_texts():
        for match in _RESOURCE_RE.finditer(texto):
            bloco = _balanced_block(texto, match.end() - 1)
            nome = _FUNCTION_NAME_RE.search(bloco)
            if not nome:
                continue
            env = re.search(r"environment\s*\{", bloco)
            if not env:
                continue
            env_bloco = _balanced_block(bloco, env.end() - 1)
            direto = re.search(rf"^\s*{RAG_ENV_NAME}\s*=", env_bloco, re.MULTILINE)
            por_local = any(ref in com_rag for ref in _LOCAL_REF_RE.findall(env_bloco))
            if direto or por_local:
                funcoes.add(nome.group(1))
    return funcoes

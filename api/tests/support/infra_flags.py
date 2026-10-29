"""Leitura do contrato de flags em `infra/*.tf` para os testes de unidade.

Ate o PR-14 os testes garantiam o CONTRARIO: que `PROGRESSION_ENGINE` e
`GENERATION_ENGINE` nao existiam em `infra/` (producao nos defaults `v1` /
`legacy`, deploy sem mudanca de comportamento). Com o replay ja executado em
producao, o PR-14 liga as duas — e a verificacao nao foi apagada, foi
invertida: agora ela impede o cenario oposto, a **configuracao pela metade**.

Os dois cenarios que este modulo torna detectaveis no CI:

1. uma Lambda que LE a flag e nao a recebe (bug silencioso: cai no legado, ou
   pior, prescreve sobre estado vazio se as duas se dessincronizarem);
2. uma flag definida em dois lugares com valores diferentes — por isso o
   valor sai de um `locals` unico
   (`infra/lambda_motores_deterministicos.tf`) e nenhuma outra `.tf` pode
   escrever o nome da flag.

O parser e deliberadamente textual e burro: nao ha `terraform` disponivel no
CI de testes de unidade, e o que precisa ser verificado e sintaticamente
simples (um `locals` de duas chaves literais e quais blocos
`resource "aws_lambda_function"` referenciam esse local).
"""

from __future__ import annotations

import re
from pathlib import Path

FLAG_NAMES = ("PROGRESSION_ENGINE", "GENERATION_ENGINE")
LOCAL_NAME = "deterministic_engines_env"

_RESOURCE_RE = re.compile(r'^resource\s+"aws_lambda_function"\s+"([A-Za-z0-9_]+)"\s*\{', re.MULTILINE)


def infra_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "infra"


def tf_files() -> list[Path]:
    return sorted(infra_dir().rglob("*.tf"))


def flag_values() -> dict[str, str]:
    """As flags como definidas no `locals` unico. Retorna {} se o local nao
    existir (o que qualquer teste deve tratar como falha de contrato)."""
    valores: dict[str, str] = {}
    for path in tf_files():
        texto = path.read_text(encoding="utf-8")
        bloco = re.search(
            rf"{LOCAL_NAME}\s*=\s*\{{(.*?)\}}",
            texto,
            re.DOTALL,
        )
        if not bloco:
            continue
        for flag in FLAG_NAMES:
            valor = re.search(rf'^\s*{flag}\s*=\s*"([^"]+)"', bloco.group(1), re.MULTILINE)
            if valor:
                valores[flag] = valor.group(1)
    return valores


def lambda_blocks() -> dict[str, str]:
    """{nome do resource aws_lambda_function: corpo do bloco}."""
    blocos: dict[str, str] = {}
    for path in tf_files():
        texto = path.read_text(encoding="utf-8")
        marcas = list(_RESOURCE_RE.finditer(texto))
        for indice, marca in enumerate(marcas):
            fim = marcas[indice + 1].start() if indice + 1 < len(marcas) else len(texto)
            blocos[marca.group(1)] = texto[marca.start() : fim]
    return blocos


def lambdas_com_flags() -> set[str]:
    """Lambdas cujo `environment` recebe `local.deterministic_engines_env`."""
    return {
        nome
        for nome, corpo in lambda_blocks().items()
        if f"local.{LOCAL_NAME}" in corpo
    }


def tf_files_que_escrevem_flag() -> dict[str, list[str]]:
    """Arquivos `.tf` que mencionam o NOME de cada flag. O contrato e que so
    `lambda_motores_deterministicos.tf` faca isso — qualquer outro arquivo e
    uma segunda fonte de verdade esperando divergir (comentarios nao contam)."""
    achados: dict[str, list[str]] = {flag: [] for flag in FLAG_NAMES}
    for path in tf_files():
        linhas = [
            linha
            for linha in path.read_text(encoding="utf-8").splitlines()
            if not linha.lstrip().startswith("#")
        ]
        codigo = "\n".join(linhas)
        for flag in FLAG_NAMES:
            if flag in codigo:
                achados[flag].append(path.name)
    return achados

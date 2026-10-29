#!/usr/bin/env python3
"""Lista, a partir do Terraform (infra/*.tf), as Lambdas que usam uma imagem ECR.

Uso: lambda_image_handlers.py <recurso_ecr_terraform> [--infra-dir infra]
Ex.: lambda_image_handlers.py musclefit_ai_generators

Saida (uma linha por Lambda, separada por TAB):
    <nome_do_recurso_lambda>\t<handler do image_config.command, ou vazio>

Vazio = a funcao nao sobrescreve o command e usa o CMD da propria imagem.

Motivo de existir: o smoke de CI (smoke-lambda-image.sh) importa EXATAMENTE os
handlers que o Terraform configura para cada imagem, em vez de uma lista
copiada a mao que poderia divergir da infra real. Parser propositalmente
simples (regex por bloco `resource "aws_lambda_function"`), sem dependencia
de terraform/hcl2 no runner - so leitura de arquivo, nenhum acesso a AWS.
Sai com codigo 1 se nenhuma Lambda usar o recurso ECR informado (drift).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RESOURCE_RE = re.compile(r'^resource\s+"aws_lambda_function"\s+"(?P<name>[\w-]+)"\s*\{', re.MULTILINE)
NEXT_BLOCK_RE = re.compile(r'^(resource|data|module|locals|variable|output)\s', re.MULTILINE)
ECR_REF_RE = re.compile(r"aws_ecr_repository\.(?P<repo>\w+)\.repository_url")
COMMAND_RE = re.compile(r"^\s*command\s*=\s*\[(?P<items>[^\]]*)\]", re.MULTILINE)


def lambdas_for_repo(infra_dir: Path, ecr_resource: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for tf in sorted(infra_dir.glob("*.tf")):
        text = tf.read_text(encoding="utf-8")
        for match in RESOURCE_RE.finditer(text):
            nxt = NEXT_BLOCK_RE.search(text, match.end())
            block = text[match.start(): nxt.start() if nxt else len(text)]
            ref = ECR_REF_RE.search(block)
            if not ref or ref.group("repo") != ecr_resource:
                continue
            cmd = COMMAND_RE.search(block)
            handler = ""
            if cmd:
                items = [i.strip().strip('"') for i in cmd.group("items").split(",") if i.strip()]
                if len(items) != 1:
                    raise SystemExit(f"{tf.name}:{match.group('name')}: command com {len(items)} itens nao suportado")
                handler = items[0]
            found.append((match.group("name"), handler))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ecr_resource")
    parser.add_argument("--infra-dir", type=Path, default=Path("infra"))
    args = parser.parse_args()
    rows = lambdas_for_repo(args.infra_dir, args.ecr_resource)
    if not rows:
        print(f"nenhuma aws_lambda_function usa aws_ecr_repository.{args.ecr_resource}", file=sys.stderr)
        return 1
    for name, handler in rows:
        print(f"{name}\t{handler}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

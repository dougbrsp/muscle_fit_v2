#!/usr/bin/env python3
"""Garante que toda Lambda de imagem do Terraform recebe imagem nova no deploy.

Motivo (incidente 2026-09-21 -> 2026-10-01): `musclefit-intervals-sync` foi criada em
infra/lambda_intervals_sync.tf (PR #155) mas nunca entrou no MAP do step
"update-function-code" de .github/workflows/deploy-producao.yml. O terraform apply a
criou apontando para `musclefit-ai-generators:latest` ANTES do build publicar a imagem
com o modulo novo; como nenhum deploy posterior chamava update-function-code nela, a
funcao ficou presa ao digest antigo e falhou toda execucao com
Runtime.ImportModuleError. (Mesmo mecanismo do incidente de musclefit-migrations em
2026-09-14.)

Regras verificadas (sem AWS, sem dependencias, so stdlib):
  1. todo `resource "aws_lambda_function"` com `package_type = "Image"` em infra/*.tf
     esta no MAP do deploy OU em EXCEPTIONS (e, se em EXCEPTIONS, o workflow tem um
     `update-function-code --function-name <nome>` proprio para ela);
  2. o repositorio ECR do MAP e o mesmo do `image_uri` no Terraform;
  3. o MAP nao lista funcao que nao existe no Terraform (entrada orfa).

Uso: python scripts/check-deploy-lambda-coverage.py [--repo-root DIR]
Saida != 0 em qualquer violacao.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

WORKFLOW = Path(".github/workflows/deploy-producao.yml")

# Funcoes de imagem atualizadas FORA do MAP, em step proprio do deploy-producao.yml.
EXCEPTIONS = {
    "musclefit-migrations": (
        "step proprio 'migracoes de banco - atualizar codigo de musclefit-migrations', "
        "que precisa rodar ANTES do migrate (o MAP roda depois)"
    ),
}

RESOURCE_RE = re.compile(r'^resource\s+"(aws_lambda_function|aws_ecr_repository)"\s+"([^"]+)"\s*\{', re.M)


def _block(text: str, start: int) -> str:
    """Retorna o corpo HCL entre a '{' em text[start-1] e a '}' correspondente."""
    depth = 1
    i = start
    in_str = False
    while i < len(text) and depth:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "#":
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return text[start : i - 1]


def _attr(body: str, name: str) -> str | None:
    # So atributos de nivel superior (indentacao de 2 espacos) para nao pegar sub-blocos.
    m = re.search(rf'^\s{{2}}{name}\s*=\s*(.+?)\s*$', body, re.M)
    return m.group(1) if m else None


def parse_terraform(infra: Path) -> tuple[dict[str, str | None], list[str]]:
    """{function_name: ecr_repo_name|None} das Lambdas de imagem + erros de parse."""
    lambdas: list[tuple[str, str, str]] = []  # (arquivo, tf_name, body)
    ecr: dict[str, str] = {}  # tf_name -> repository name
    errors: list[str] = []
    for tf in sorted(infra.glob("*.tf")):
        text = tf.read_text(encoding="utf-8")
        for m in RESOURCE_RE.finditer(text):
            body = _block(text, m.end())
            if m.group(1) == "aws_ecr_repository":
                name = _attr(body, "name")
                if name and name.startswith('"'):
                    ecr[m.group(2)] = name.strip('"')
            else:
                lambdas.append((tf.name, m.group(2), body))

    result: dict[str, str | None] = {}
    for fname, tf_name, body in lambdas:
        pkg = (_attr(body, "package_type") or '"Zip"').strip('"')
        if pkg != "Image":
            continue
        fn = _attr(body, "function_name")
        if not fn or not re.fullmatch(r'"[^"$]+"', fn):
            errors.append(
                f"{fname}: aws_lambda_function.{tf_name} tem function_name nao literal ({fn!r}); "
                "o check precisa do nome literal para comparar com o MAP do deploy"
            )
            continue
        uri = _attr(body, "image_uri") or ""
        rm = re.search(r"aws_ecr_repository\.([A-Za-z0-9_-]+)\.repository_url", uri)
        result[fn.strip('"')] = ecr.get(rm.group(1)) if rm else None
    return result, errors


def parse_workflow(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r'^\s*MAP="\n(.*?)^\s*"\s*$', text, re.M | re.S)
    if not m:
        raise SystemExit(f"ERRO: bloco MAP=\"...\" nao encontrado em {path}")
    entries: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        fn, _, repo = line.partition(":")
        entries[fn] = repo
    return entries, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=Path(__file__).resolve().parent.parent, type=Path)
    args = ap.parse_args()
    root: Path = args.repo_root

    tf_lambdas, errors = parse_terraform(root / "infra")
    deploy_map, wf_text = parse_workflow(root / WORKFLOW)

    if not tf_lambdas:
        errors.append("nenhuma aws_lambda_function de imagem encontrada em infra/*.tf (parse quebrado?)")

    for fn, repo in sorted(tf_lambdas.items()):
        if fn in EXCEPTIONS:
            if not re.search(rf"update-function-code\s*\\?\s*\n?\s*--function-name\s+{re.escape(fn)}\b", wf_text):
                errors.append(
                    f"{fn} esta em EXCEPTIONS ({EXCEPTIONS[fn]}), mas {WORKFLOW.as_posix()} nao tem "
                    f"`aws lambda update-function-code --function-name {fn}`"
                )
            continue
        if fn not in deploy_map:
            errors.append(
                f"{fn} (package_type=Image, repo {repo}) NAO esta no MAP do step "
                f"'update-function-code' de {WORKFLOW.as_posix()}: nunca recebera imagem nova no deploy. "
                f"Adicione `{fn}:{repo}` ao MAP (ou a EXCEPTIONS deste script com um step proprio)."
            )
        elif repo and deploy_map[fn] != repo:
            errors.append(f"{fn}: MAP usa repo '{deploy_map[fn]}', mas o image_uri do Terraform usa '{repo}'")

    for fn in sorted(set(deploy_map) - set(tf_lambdas)):
        errors.append(f"MAP do deploy lista '{fn}', que nao e uma aws_lambda_function de imagem em infra/*.tf")
    for fn in sorted(set(deploy_map) & set(EXCEPTIONS)):
        errors.append(f"'{fn}' esta no MAP e em EXCEPTIONS ao mesmo tempo; escolha um")

    print(f"Lambdas de imagem no Terraform: {len(tf_lambdas)}; no MAP: {len(deploy_map)}; excecoes: {len(EXCEPTIONS)}")
    for fn in sorted(tf_lambdas):
        where = "excecao" if fn in EXCEPTIONS else ("MAP" if fn in deploy_map else "FALTANDO")
        print(f"  {fn:<28} {tf_lambdas[fn] or '?':<26} {where}")
    if errors:
        print()
        for e in errors:
            print(f"ERRO: {e}")
        return 1
    print("OK: toda Lambda de imagem e atualizada pelo deploy-producao.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

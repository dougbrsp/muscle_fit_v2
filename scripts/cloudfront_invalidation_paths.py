#!/usr/bin/env python3
"""Paths de invalidacao CloudFront do deploy do frontend (M68).

Antes: o job "(3) frontend deploy" de .github/workflows/deploy-producao.yml
invalidava "/*" na musclefit-cdn, expulsando da borda tambem as fotos
(/equipment/*, /gyms/*) e os cards ilustrados (/exercise-cards/*), que sao
imutaveis e vem do origin musclefit-uploads por ordered_cache_behavior proprio.

Agora: invalida so o que o frontend ocupa. Para cada entrada de PRIMEIRO NIVEL
do site (diretorio de rota ou arquivo da raiz), tirada da UNIAO de
  - frontend/out/ (o que vai ser publicado: cobre rota NOVA) e
  - a listagem da raiz do bucket ANTES do sync (cobre rota APAGADA pelo
    `--delete`, que senao continuaria servindo o HTML velho da borda),
escolhe o MENOR prefixo "/<p>*" que nao casa com nenhum prefixo de uploads.
Ex.: "/a*" cobre academias/ e agente-cardio/; "exercicios" e "exercise-cards"
dividem "exerci", entao a entrada vira "/exercic*". Isso deixa a lista curta
(~20 paths, estavel), o que importa porque so os 1.000 primeiros paths do mes
sao gratis e cada wildcard conta como 1 path.

Fica de fora so "_next/": next export so poe ali assets com hash/buildId no
nome (_next/static/...), que nunca mudam de conteudo sob a mesma URL.

Os prefixos protegidos vem do proprio infra/cloudfront.tf (todo path_pattern
de ordered_cache_behavior) e o resultado passa por uma verificacao final que
aborta o deploy se algum path casar com um deles. Roda ANTES do `aws s3 sync`,
entao uma falha aqui nao deixa o site meio publicado.

Uso:
  python3 scripts/cloudfront_invalidation_paths.py \
      --out-dir frontend/out \
      --bucket-listing bucket-root.json \
      --cloudfront-tf infra/cloudfront.tf
      [--batches-out invalidation-batches.txt]
Imprime um path por linha. Com --batches-out, grava tambem os LOTES (um por
linha, paths separados por espaco) que o workflow envia um de cada vez.
Testes: scripts/test_cloudfront_invalidation_paths.py.

Lotes (hotfix do M68, run 37227083220): a cota da CloudFront e de 15 PATHS com
wildcard EM ANDAMENTO ao mesmo tempo na distribuicao (somando todas as
invalidacoes InProgress), nao 15 invalidacoes. Uma unica requisicao com 18
wildcards falha com TooManyInvalidationsInProgress. Por isso cada lote leva no
maximo MAX_WILDCARDS_PER_BATCH wildcards e o workflow espera cada lote concluir
antes de mandar o proximo. Paths exatos (ex.: "/") nao contam para essa cota
(contam na de 3.000 arquivos) e vao no primeiro lote.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Rede de seguranca: mesmo que o parse do .tf venha incompleto, estes nunca
# podem ser invalidados pelo deploy do frontend.
REQUIRED_PROTECTED = ("equipment/", "gyms/", "exercise-cards/")

# Entradas de primeiro nivel que nao precisam de invalidacao (assets com hash).
SKIPPED_ENTRIES = ("_next/",)

# Nome de entrada aceito sem escape (o path de invalidacao vai cru para a API).
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._~-]+/?$")

# Limite da API: ate 3.000 paths por invalidacao com wildcard. Bem abaixo
# disso de proposito: se passar daqui algo mudou de forma e merece olhar humano.
MAX_PATHS = 100

# Cota da CloudFront: "Concurrent invalidation request maximum ... wildcard
# paths: 15" (paths com * em andamento ao mesmo tempo na distribuicao).
MAX_WILDCARDS_PER_BATCH = 15


class InvalidationError(Exception):
    pass


def protected_prefixes_from_tf(tf_text: str) -> list[str]:
    """Prefixos de S3 key (sem "/" inicial) dos ordered_cache_behavior."""
    prefixes = []
    for block in re.findall(r"ordered_cache_behavior\s*\{(.*?)\n\s*\}", tf_text, re.S):
        m = re.search(r'path_pattern\s*=\s*"([^"]+)"', block)
        if not m:
            continue
        pattern = m.group(1).lstrip("/")
        prefix = re.split(r"[*?]", pattern, maxsplit=1)[0]
        if not prefix:
            raise InvalidationError(
                f"path_pattern {m.group(1)!r} comeca com wildcard: impossivel "
                "escopar a invalidacao do frontend sem casar com ele"
            )
        prefixes.append(prefix)
    missing = [p for p in REQUIRED_PROTECTED if p not in prefixes]
    if missing:
        raise InvalidationError(
            f"cloudfront.tf sem ordered_cache_behavior esperado para {missing}; "
            "atualize REQUIRED_PROTECTED se o behavior foi removido de proposito"
        )
    return sorted(set(prefixes))


def entries_from_out_dir(out_dir: str) -> set[str]:
    entries = set()
    for name in os.listdir(out_dir):
        full = os.path.join(out_dir, name)
        entries.add(name + "/" if os.path.isdir(full) else name)
    if "index.html" not in entries:
        raise InvalidationError(f"{out_dir} sem index.html: build incompleto?")
    return entries


def entries_from_bucket_listing(listing: dict) -> set[str]:
    """Saida de `aws s3api list-objects-v2 --delimiter / --output json`."""
    entries = set()
    for cp in listing.get("CommonPrefixes") or []:
        entries.add(cp["Prefix"])
    for obj in listing.get("Contents") or []:
        entries.add(obj["Key"])
    return entries


def _collides(prefix: str, protected: str) -> bool:
    """Wildcard "/<prefix>*" casaria alguma key "<protected>..."?"""
    return protected.startswith(prefix) or prefix.startswith(protected)


def path_for_entry(entry: str, protected: list[str]) -> str:
    if not _SAFE_NAME.match(entry):
        raise InvalidationError(f"entrada de primeiro nivel com nome inesperado: {entry!r}")
    for p in protected:
        if entry.startswith(p):
            raise InvalidationError(
                f"o frontend publica {entry!r}, sombreado pelo behavior de uploads {p!r}"
            )
    for size in range(1, len(entry) + 1):
        prefix = entry[:size]
        if not any(_collides(prefix, p) for p in protected):
            return "/" + prefix + "*"
    # So acontece com arquivo cujo nome inteiro e prefixo de um protegido
    # (ex.: arquivo "gym" vs "gyms/"): invalida o arquivo exato, sem wildcard.
    return "/" + entry


def compute_paths(entries: set[str], protected: list[str]) -> list[str]:
    candidates = set()
    for entry in entries:
        if entry in SKIPPED_ENTRIES or entry == "":
            continue
        candidates.add(path_for_entry(entry, protected))
    # Remove wildcard coberto por outro wildcard mais curto.
    wildcards = sorted(p[:-1] for p in candidates if p.endswith("*"))
    result = {"/"}  # raiz exata (default_root_object), barato e explicito
    for p in candidates:
        if p.endswith("*"):
            body = p[:-1]
            if any(body != w and body.startswith(w) for w in wildcards):
                continue
        elif any(p.startswith(w) for w in wildcards):
            continue
        result.add(p)
    paths = sorted(result)
    verify_paths(paths, protected)
    return paths


def verify_paths(paths: list[str], protected: list[str]) -> None:
    """Falha se algum path invalidaria (ou poderia invalidar) um upload."""
    if not paths:
        raise InvalidationError("lista de paths vazia")
    if len(paths) > MAX_PATHS:
        raise InvalidationError(f"{len(paths)} paths (> {MAX_PATHS}): revise antes de publicar")
    for path in paths:
        if not path.startswith("/"):
            raise InvalidationError(f"path sem / inicial: {path!r}")
        body = path[1:]
        if "*" in body[:-1]:
            raise InvalidationError(f"wildcard fora do final: {path!r}")
        if path in ("/*", "*"):
            raise InvalidationError("'/*' invalidaria os uploads")
        for p in protected:
            if body.endswith("*"):
                bad = _collides(body[:-1], p)
            else:
                bad = body.startswith(p)
            if bad:
                raise InvalidationError(f"{path!r} casa com o prefixo de uploads /{p}")


def batch_paths(paths: list[str], max_wildcards: int = MAX_WILDCARDS_PER_BATCH) -> list[list[str]]:
    """Divide os paths em lotes com no maximo `max_wildcards` wildcards cada.

    Ordem estavel: os paths mantem a ordem de entrada; paths exatos (sem "*")
    nao contam para a cota de wildcard e vao todos no primeiro lote.
    """
    if max_wildcards < 1:
        raise InvalidationError("max_wildcards precisa ser >= 1")
    exact = [p for p in paths if not p.endswith("*")]
    wildcards = [p for p in paths if p.endswith("*")]
    batches: list[list[str]] = []
    for i in range(0, len(wildcards), max_wildcards):
        batches.append(wildcards[i:i + max_wildcards])
    if not batches:
        batches.append([])
    batches[0] = exact + batches[0]
    batches[0].sort()
    for b in batches:
        if sum(1 for p in b if p.endswith("*")) > max_wildcards:  # defesa
            raise InvalidationError(f"lote com wildcards demais: {b}")
    return [b for b in batches if b]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--bucket-listing", required=True,
                    help="JSON de `aws s3api list-objects-v2 --delimiter /` (pode estar vazio)")
    ap.add_argument("--cloudfront-tf", required=True)
    ap.add_argument("--batches-out",
                    help="grava os lotes (um por linha, paths separados por espaco)")
    args = ap.parse_args(argv)
    try:
        with open(args.cloudfront_tf, encoding="utf-8") as f:
            protected = protected_prefixes_from_tf(f.read())
        entries = entries_from_out_dir(args.out_dir)
        with open(args.bucket_listing, encoding="utf-8") as f:
            raw = f.read().strip()
        entries |= entries_from_bucket_listing(json.loads(raw) if raw else {})
        paths = compute_paths(entries, protected)
        batches = batch_paths(paths)
        for b in batches:
            verify_paths(b, protected)
    except InvalidationError as exc:
        print(f"::error::invalidacao CloudFront: {exc}", file=sys.stderr)
        return 1
    print("prefixos protegidos (uploads): " + ", ".join("/" + p for p in protected), file=sys.stderr)
    if args.batches_out:
        with open(args.batches_out, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(" ".join(b) + "\n" for b in batches))
        print(f"{len(batches)} lote(s) de invalidacao (<= {MAX_WILDCARDS_PER_BATCH} wildcards cada)",
              file=sys.stderr)
    print("\n".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())

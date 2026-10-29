"""Hidratacao em runtime do conteudo de RAG (rag/markdown/*.md) a partir de
um bucket S3, para as Lambdas de container.

Contexto: o codigo Python que le RAG (`rag_chunking.py`,
`exercise_set_evaluation_ai.py`, `workout_plan_ai.py`) sempre leu um
diretorio local `RAG_MARKDOWN_DIR` (default `/app/rag`, populado por bind
mount no ambiente on-premise). Nenhuma Lambda tem disco persistente nem
imagem com o conteudo embutido — a decisao foi manter o conteudo versionado
no submodulo privado `api/privado` (repositorio muscle_fit_privado,
`rag/markdown/`; M103 P0) e publicado em S3 via Terraform
(`infra/s3_rag.tf`), buscado em runtime para `/tmp` (unico disco gravavel de
uma Lambda) na primeira invocacao de um container "frio". Isso permite
atualizar a metodologia sem rebuildar/republicar imagens.

Uso: `RAG_MARKDOWN_DIR = ensure_rag_local() / <arquivo>` (ou, como em
`rag_chunking.py`, chamar `ensure_rag_local()` no inicio de cada funcao de
entrada que toca o disco, mantendo a constante de modulo `RAG_MARKDOWN_DIR`
para nao quebrar monkeypatch em testes).
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

_SYNC_MARKER_NAME = ".synced"

# Flag de modulo: evita ate o `stat` do marcador em disco em invocacoes
# subsequentes do mesmo container quente (o caso comum - cold start e raro).
_SYNCED = False
_SYNC_LOCK = threading.Lock()


def _env_config() -> tuple[str | None, str, Path]:
    """Le a config no MOMENTO da chamada (nao em import time), para respeitar
    monkeypatch de env vars em testes e para nao fixar um valor stale caso o
    ambiente Lambda reuse o modulo entre invocacoes com env diferente (nao
    acontece na pratica, mas e mais barato que documentar a excecao)."""
    bucket = os.environ.get("RAG_S3_BUCKET") or None
    prefix = os.environ.get("RAG_S3_PREFIX", "markdown/")
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    local_dir = Path(os.environ.get("RAG_MARKDOWN_DIR", "/app/rag"))
    return bucket, prefix, local_dir


def _is_safe_relative_key(relative_key: str) -> bool:
    """Protecao anti path-traversal: recusa chaves S3 com componentes '..'
    ou caminho absoluto antes de resolver contra `local_dir`. Chaves vindas
    de `list_objects_v2` sao confiaveis por padrao (o bucket e nosso), mas
    esta checagem custa nada e evita qualquer surpresa se o bucket algum dia
    ganhar objetos fora do controle do Terraform."""
    if not relative_key or relative_key.startswith("/"):
        return False
    parts = Path(relative_key).parts
    return ".." not in parts


def _download_one(s3_client, bucket: str, key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_suffix(dest.suffix + ".part")
    s3_client.download_file(bucket, key, str(tmp_dest))
    tmp_dest.replace(dest)


def _sync_from_s3(bucket: str, prefix: str, local_dir: Path) -> bool:
    """Baixa todos os objetos sob `prefix` para `local_dir`, em paralelo.
    Devolve True somente se TODOS os objetos foram baixados com sucesso
    (condicao para escrever o marcador `.synced` - download parcial nunca
    deve ser tratado como sincronizado, para a proxima invocacao tentar de
    novo)."""
    import boto3  # import local: nao pagar custo de import em cold start

    s3_client = boto3.client("s3")

    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # "diretorio" vazio, nao um arquivo
            keys.append(key)

    if not keys:
        logger.warning(
            "rag_storage: nenhum objeto encontrado em s3://%s/%s - RAG ficara vazio.",
            bucket,
            prefix,
        )
        return False

    local_dir.mkdir(parents=True, exist_ok=True)

    downloads: list[tuple[str, Path]] = []
    for key in keys:
        relative_key = key[len(prefix):] if key.startswith(prefix) else key
        if not _is_safe_relative_key(relative_key):
            logger.warning("rag_storage: chave S3 suspeita ignorada: %s", key)
            continue
        dest = (local_dir / relative_key).resolve()
        if local_dir.resolve() not in dest.parents and dest != local_dir.resolve():
            logger.warning("rag_storage: chave S3 resolveria fora do diretorio local, ignorada: %s", key)
            continue
        downloads.append((key, dest))

    if len(downloads) != len(keys):
        # Alguma chave foi rejeitada pela checagem anti path-traversal -
        # nao considerar sincronizado (nunca escrever o marcador).
        return False

    ok = True
    with ThreadPoolExecutor(max_workers=min(8, len(downloads)) or 1) as executor:
        futures = {
            executor.submit(_download_one, s3_client, bucket, key, dest): key
            for key, dest in downloads
        }
        for future in futures:
            key = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - qualquer falha de download individual
                logger.warning("rag_storage: falha ao baixar s3://%s/%s: %s", bucket, key, exc)
                ok = False

    return ok


def ensure_rag_local() -> Path:
    """Garante que `RAG_MARKDOWN_DIR` exista localmente com o conteudo de
    RAG, sincronizando de S3 se necessario, e devolve o `Path` resultante
    (sempre o mesmo caminho de `RAG_MARKDOWN_DIR`, sincronizado ou nao).

    - `RAG_S3_BUCKET` nao definido: comportamento local/on-premise
      preservado, nao toca em nada, devolve `Path(RAG_MARKDOWN_DIR)` direto.
    - `RAG_S3_BUCKET` definido: sincroniza uma unica vez por container
      (marcador `.synced` em disco + flag `_SYNCED` em memoria evitam
      re-sincronizar em invocacoes subsequentes do mesmo container quente).
    - QUALQUER excecao (sem credencial, bucket inexistente, rede) e
      capturada aqui: loga warning e devolve o diretorio local (possivelmente
      vazio) - a geracao de ficha nunca pode quebrar por causa do RAG, que ja
      degrada silenciosamente para vazio hoje quando o diretorio nao existe.
    """
    global _SYNCED

    bucket, prefix, local_dir = _env_config()

    if not bucket:
        return local_dir

    if _SYNCED:
        return local_dir

    marker = local_dir / _SYNC_MARKER_NAME

    with _SYNC_LOCK:
        if _SYNCED:
            return local_dir
        try:
            if marker.exists():
                _SYNCED = True
                return local_dir

            success = _sync_from_s3(bucket, prefix, local_dir)
            if success:
                try:
                    marker.write_text("ok", encoding="utf-8")
                except OSError as exc:
                    logger.warning("rag_storage: nao foi possivel escrever marcador %s: %s", marker, exc)
                else:
                    _SYNCED = True
            else:
                logger.warning(
                    "rag_storage: sincronizacao de s3://%s/%s incompleta - marcador NAO escrito, "
                    "proxima invocacao tenta de novo.",
                    bucket,
                    prefix,
                )
        except Exception as exc:  # noqa: BLE001 - degradacao silenciosa obrigatoria
            logger.warning(
                "rag_storage: falha ao sincronizar RAG de s3://%s/%s para %s: %s",
                bucket,
                prefix,
                local_dir,
                exc,
            )

    return local_dir

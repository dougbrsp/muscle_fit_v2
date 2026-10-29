"""Snapshots de prompt guardados no repositório privado (M103 P1).

O texto literal dos prompts (esqueletos renderizados, corpos HTTP com o
`input`) mora em `api/privado/tests/fixtures/` (submódulo `muscle_fit_privado`);
o público guarda só hashes (`tests/unit/fixtures/llm_prompts/index.json`).

O log do GitHub Actions do repositório público é público: a comparação NUNCA
imprime o texto do prompt. Mudou? A falha diz só o nome e o tamanho; para ver o
diff, rode localmente com `LLM_PROMPT_SNAPSHOT_DIFF=1`.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[2]
PRIVADO = API / "privado"
FIXTURES_PRIVADAS = PRIVADO / "tests" / "fixtures"
DIFF_ENV = "LLM_PROMPT_SNAPSHOT_DIFF"


def fixture_privada(*partes: str) -> Path:
    """Caminho em `api/privado/tests/fixtures/`; falha alto sem o submódulo."""
    if not FIXTURES_PRIVADAS.is_dir():
        pytest.fail(
            "fixtures privadas ausentes (api/privado/tests/fixtures): rode "
            "`git submodule update --init api/privado`",
            pytrace=False,
        )
    return FIXTURES_PRIVADAS.joinpath(*partes)


def comparar_sem_vazar(atual: str, esperado: str, rotulo: str) -> None:
    """Falha se diferente, sem pôr o texto na mensagem (nem o diff, salvo
    `LLM_PROMPT_SNAPSHOT_DIFF=1` local)."""
    if atual == esperado:
        return
    detalhe = ""
    if os.environ.get(DIFF_ENV):
        diff = difflib.unified_diff(
            esperado.splitlines(keepends=True), atual.splitlines(keepends=True), "snapshot", "atual"
        )
        detalhe = "\n" + "".join(list(diff)[:80])
    linhas_esperado, linhas_atual = esperado.split("\n"), atual.split("\n")
    primeira = next(
        (i for i, (a, b) in enumerate(zip(linhas_esperado, linhas_atual), start=1) if a != b),
        min(len(linhas_esperado), len(linhas_atual)) + 1,
    )
    pytest.fail(
        f"{rotulo} mudou ({len(esperado)} -> {len(atual)} chars; 1a linha diferente: {primeira}). "
        f"Texto omitido (log público); diff local: {DIFF_ENV}=1{detalhe}",
        pytrace=False,
    )


def exigir(condicao: bool, rotulo: str) -> None:
    """`assert` que não põe o prompt no log (M103 P3): o `assert trecho in
    prompt` do pytest imprime os dois textos na falha. Aqui a falha diz só o
    rótulo; o detalhe se vê localmente (`PRIVADO_TB=1` nos testes do privado,
    ou um print na sua máquina)."""
    if not condicao:
        pytest.fail(f"{rotulo} (texto do prompt omitido: log público)", pytrace=False)


def comparar_arquivo_sem_vazar(atual: str, referencia: Path, rotulo: str, *, regerar: str) -> None:
    if not referencia.exists():
        pytest.fail(f"sem snapshot {rotulo}: gere com {regerar}=1", pytrace=False)
    comparar_sem_vazar(atual, referencia.read_text(encoding="utf-8"), rotulo)

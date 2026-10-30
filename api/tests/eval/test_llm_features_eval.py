"""Eval PAGO das 8 features de texto (IA-11) - **fora do CI**.

Marcador `llm_eval` (excluido do `addopts` de `pytest.ini`): cada execucao
chama a OpenAI de verdade (custa dinheiro) e a saida nao e deterministica.
Roda sob demanda, antes e depois de mexer num prompt, num modelo ou no RAG
(IA-02, IA-05, IA-06, IA-08, IA-09):

    cd api && OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py
    # uma feature so:
    cd api && OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py -k cardio_agent

Entradas: os casos congelados de `tests/eval/casos/` (sinteticos,
com a estrutura da coleta do DEV - M103 L2) - os MESMOS do snapshot do CI, passando pelo MESMO executor
(`tests/support/llm_casos.py`), agora com o RAG real de `api/privado/rag/markdown` (submodulo privado).
Criterios: `tests/eval/criterios_llm.py` (objetivos; nada de estilo). Custo
por rodada e como comparar versoes: `docs/ia/EVALS.md`.

Sem `OPENAI_API_KEY` todo caso da SKIP (nunca falha por falta de chave).

Provedor/modelo por feature (GPT x Claude): `LLM_EVAL_PROVIDER`,
`LLM_EVAL_MODEL`, `LLM_EVAL_EFFORT` (`tests/support/llm_eval_provider.py`);
com `anthropic`, a chave exigida e a `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import sys

import pytest

from tests.eval.criterios_llm import AVALIADORES, texto_da_saida
from tests.support import llm_casos, llm_eval_provider

pytestmark = pytest.mark.llm_eval

CASOS = llm_casos.todos_os_casos()


def _stdout_utf8() -> None:
    """No Windows o console de `-s` costuma ser cp1252: o `print` da resposta
    quebrava com `UnicodeEncodeError` (ex.: "→") e o texto do caso se perdia
    (eval do #442). Forca UTF-8 com `errors="replace"`; sob captura do pytest
    o stream ja e UTF-8 e nada muda."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None or (sys.stdout.encoding or "").lower().replace("-", "") == "utf8":
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


@pytest.mark.parametrize(("feature", "caso"), CASOS, ids=[f"{f}/{c['id']}" for f, c in CASOS])
def test_feature_cumpre_os_criterios(monkeypatch, feature, caso):
    llm_eval_provider.apply(monkeypatch, feature)
    saida = llm_casos.executar(feature, caso, monkeypatch, rag="real")
    problemas = AVALIADORES[feature](saida, caso)
    _stdout_utf8()
    print(f"\n[{feature}/{caso['id']}] problemas={len(problemas)}")
    for problema in problemas:
        print(f"  - {problema}")
    print(texto_da_saida(feature, saida))
    assert not problemas, "\n".join(problemas)

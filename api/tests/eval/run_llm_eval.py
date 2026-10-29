"""Roda a avaliacao com LLM sob demanda e imprime um relatorio - sem pytest.

Existe porque a metade com LLM nao e um portao (ela nao roda no CI): ela e
uma MEDIDA que alguem tira de tempos em tempos, tipicamente depois de mexer
no prompt ou de trocar de modelo. Para isso, um relatorio lado a lado vale
mais que um `assert` - da para ver POR QUE a resposta ficou pior, nao so que
ficou.

O leitor das respostas e o mesmo do teste (`test_llm_insights_eval.avaliar`):
duas formas de execucao, um criterio so.

    cd api
    OPENAI_API_KEY=... python -m tests.eval.run_llm_eval
    OPENAI_API_KEY=... python -m tests.eval.run_llm_eval --id volume_abaixo_do_minimo

Cada execucao custa uma chamada a OpenAI por pergunta do conjunto. O RAG NAO
e injetado por padrao (`--rag` liga): a avaliacao mede disciplina sobre o
pacote, e 200 mil caracteres de metodologia so encareceriam a medicao sem
mudar o que esta sendo medido.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.insights_ai import generate_insight  # noqa: E402
from app.insights_prompt import PROMPT_VERSION  # noqa: E402
from app.intelligence.dashboard.intelligence_package import package_from_overview  # noqa: E402
from tests.eval.test_llm_insights_eval import avaliar  # noqa: E402
from tests.support.pacote_inteligencia import overview_sintetico  # noqa: E402

DATASET = Path(__file__).with_name("questions.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Avaliação com LLM das respostas de /insights.")
    parser.add_argument("--id", action="append", help="roda só a(s) pergunta(s) com este id")
    parser.add_argument("--rag", action="store_true", help="injeta o RAG filtrado (mais caro)")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY não configurada.", file=sys.stderr)
        return 2

    conjunto = json.loads(DATASET.read_text(encoding="utf-8"))
    if conjunto["prompt_version"] != PROMPT_VERSION:
        print(
            f"AVISO: o conjunto foi escrito para {conjunto['prompt_version']} e o prompt atual é "
            f"{PROMPT_VERSION}. O resultado mede duas coisas ao mesmo tempo.",
            file=sys.stderr,
        )

    overview = overview_sintetico()
    perguntas = [p for p in conjunto["questions"] if not args.id or p["id"] in args.id]
    falhas = 0

    for pergunta in perguntas:
        pacote = package_from_overview(overview, question=pergunta["question"])
        rag_text = ""
        if args.rag:
            from app.intelligence.dashboard.intelligence_package import rag_target_groups
            from app.rag_chunking import select_rag_text

            rag_text = select_rag_text(rag_target_groups(pacote), has_training_type=True)

        resultado = generate_insight(pergunta["question"], pacote, rag_text)
        problemas = avaliar(resultado["insight"], pacote, pergunta)
        falhas += bool(problemas)

        print("=" * 78)
        print(f"[{pergunta['id']}] {pergunta['question']}")
        print(f"modelo={resultado['model']} prompt={resultado['prompt_version']} "
              f"prompt_chars={resultado['prompt_chars']} pacote={pacote['size_bytes']}B")
        print("-" * 78)
        print(resultado["insight"])
        print("-" * 78)
        print("OK" if not problemas else "PROBLEMAS:\n  - " + "\n  - ".join(problemas))

    print("=" * 78)
    print(f"{len(perguntas) - falhas}/{len(perguntas)} perguntas sem problema.")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Avaliacao COM LLM das respostas de `/insights` (F3.2) - **fora do CI**.

Marcador proprio (`llm_eval`), excluido do `addopts` de `pytest.ini`. Ele
nunca roda por acidente, e a razao e direta: cada execucao chama a OpenAI de
verdade (custa dinheiro) e a saida de um modelo nao e deterministica (a mesma
pergunta pode reprovar hoje e passar amanha sem nenhuma mudanca de codigo).
Um teste assim no caminho critico nao mede qualidade - ele so ensina a equipe
a ignorar vermelho.

O que ele mede, quando alguem decide roda-lo: se a resposta CITA a evidencia
que o pacote forneceu (pela data) e se ela NAO traz numero que nao esta no
pacote. As duas coisas sao verificaveis sem julgar estilo.

Como rodar: ver `tests/eval/README.md`. Resumo:

    cd api && OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval

Nada aqui roda no CI. Se este arquivo um dia aparecer numa execucao de CI, o
bug esta no `addopts` ou no workflow - nao aqui.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from app.insights_ai import generate_insight
from app.insights_prompt import PROMPT_VERSION
from app.intelligence.dashboard.intelligence_package import package_from_overview
from tests.support.pacote_inteligencia import overview_sintetico

pytestmark = pytest.mark.llm_eval

DATASET = Path(__file__).with_name("questions.json")
CONJUNTO = json.loads(DATASET.read_text(encoding="utf-8"))
PERGUNTAS = CONJUNTO["questions"]

# Regex de numero com decimal ou milhar - o que o modelo costuma "produzir"
# quando decide calcular. Inteiros pequenos (contagens) sao ignorados de
# proposito: eles aparecem em texto corrido legitimo ("nas 3 sessões").
NUMERO = re.compile(r"\d+[.,]\d+")


@pytest.fixture(scope="module")
def cenario():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY não configurada: a avaliação com LLM precisa da chave real.")
    return overview_sintetico()


def numeros_do_pacote(pacote: dict) -> set[str]:
    """Todo numero que o pacote realmente contem, normalizado.

    E contra este conjunto que a resposta e conferida. Um numero na resposta
    que nao esta aqui so pode ter vindo de uma conta que o prompt proibiu."""
    texto = json.dumps(pacote, default=str, ensure_ascii=False)
    achados = {n.replace(",", ".") for n in NUMERO.findall(texto)}
    # a mesma grandeza escrita com virgula (pt-BR) conta como presente
    return achados | {n.replace(".", ",") for n in achados}


def avaliar(resposta: str, pacote: dict, pergunta: dict) -> list[str]:
    """Leitor da resposta: devolve a lista de problemas encontrados.

    Separado do teste de proposito - `run_llm_eval.py` usa a MESMA funcao para
    produzir o relatorio sob demanda, sem pytest. Um leitor so, duas formas de
    execucao."""
    problemas: list[str] = []
    baixo = resposta.lower()

    for proibido in pergunta.get("forbidden_in_answer") or []:
        if proibido.lower() in baixo:
            problemas.append(f"causalidade/assunto proibido: {proibido!r}")

    if pergunta.get("expect_evidence_codes"):
        # Aceita a data PONTUAL e, para agregado de janela, qualquer uma das
        # duas pontas do periodo - o prompt manda citar `observed_from` e
        # `observed_at` juntos, e exigir uma string exata aqui mediria
        # formatacao, nao disciplina.
        esperados_codigos = set(pergunta["expect_evidence_codes"])
        datas: set[str] = set()
        for e in pacote["evidence"]:
            if e["insight_code"] not in esperados_codigos:
                continue
            for valor in (e.get("observed_at"), e.get("observed_from")):
                if valor:
                    datas.add(str(valor)[:10])
        formatos = set()
        for iso in datas:
            ano, mes, dia = iso.split("-")
            formatos |= {iso, f"{dia}/{mes}/{ano}", f"{dia}/{mes}"}
        if formatos and not any(f in resposta for f in formatos):
            problemas.append(f"nenhuma data da evidência citada (esperava uma de {sorted(formatos)})")

    conhecidos = numeros_do_pacote(pacote)
    inventados = sorted({n for n in NUMERO.findall(resposta) if n not in conhecidos})
    if inventados:
        problemas.append(f"números que não estão no pacote: {inventados}")

    if pergunta.get("expect_answer_admits_missing_data"):
        marcas = ("não há dado", "sem dado", "não disponível", "não foi registrad", "indisponível")
        if not any(m in baixo for m in marcas):
            problemas.append("dado ausente não foi admitido na resposta")

    return problemas


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_resposta_cita_evidencia_e_nao_inventa_numero(cenario, pergunta):
    pacote = package_from_overview(cenario, question=pergunta["question"])
    resultado = generate_insight(pergunta["question"], pacote, "")
    problemas = avaliar(resultado["insight"], pacote, pergunta)
    print(f"\n[{pergunta['id']}] prompt={PROMPT_VERSION} problemas={len(problemas)}")
    print(resultado["insight"])
    assert not problemas, "\n".join(problemas)

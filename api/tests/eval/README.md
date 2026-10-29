# Avaliação de respostas de `/insights` (F3.2)

> As outras 7 features de IA (avaliação de série, resumos, análise por IA,
> cardio, periodização, descrição de execução) e casos reais do insights têm
> snapshot de prompt no CI e eval pago próprio desde o IA-11: ver
> `docs/ia/EVALS.md` (`test_llm_features_eval.py`, `casos/`,
> `criterios_llm.py`, `coletar_casos_llm.py`).

Duas metades, deliberadamente separadas. A separação é a decisão de projeto
mais importante desta pasta: a única maneira de uma avaliação com LLM ser útil
é ela **não** estar no caminho crítico.

## (a) Determinística — roda em todo PR

`../unit/test_package_eval.py`, sem marcador. O arquivo mora em `tests/unit/`
(e não aqui) por um motivo prático: o CI roda `python -m pytest tests/unit` e
`tests/integration` explicitamente — um teste dentro de `tests/eval/` não seria
coletado, e a metade que deve rodar em todo PR não rodaria em nenhum. O
conjunto de perguntas continua aqui, um só, compartilhado pelas duas metades.

Ela não pergunta "a IA respondeu bem?". Pergunta **"a IA tinha como responder
bem?"**: para cada pergunta de `questions.json`, o `intelligence_package`
montado precisa conter as métricas, os baselines, a evidência (com data) e os
eventos de contexto que uma boa resposta citaria — caber no teto de 40 KB e
não carregar identificador de usuário nenhum.

```bash
cd api && python -m pytest tests/unit/test_package_eval.py
```

Não chama LLM. Não toca no banco (o cenário é o overview sintético de
`tests/support/pacote_inteligencia.py`). O isolamento contra dado **real** de
outro usuário é afirmado em
`tests/integration/dataapi/test_dashboard_intelligence_package.py`.

## (b) Com LLM — sob demanda, fora do CI

`test_llm_insights_eval.py`, marcador `llm_eval`, excluído do `addopts` de
`pytest.ini`.

Por que fora do CI, em uma frase cada:

- **custa dinheiro** — uma chamada real à OpenAI por pergunta, em todo PR;
- **não é determinística** — a mesma pergunta pode reprovar hoje e passar
  amanhã sem mudança de código, e um vermelho que ninguém consegue reproduzir
  é um vermelho que a equipe aprende a ignorar.

O que ela verifica (nada de julgar estilo):

1. a resposta **cita** a data da evidência que o pacote forneceu;
2. a resposta **não traz número** que não está no pacote (o leitor compara
   todo decimal da resposta contra os decimais do pacote);
3. a resposta não usa linguagem de **causalidade** nem entra em assunto que o
   pacote não mede (dieta, suplemento, exame);
4. quando o dado está ausente, a resposta **admite** a ausência em vez de
   estimar.

### Como rodar

```bash
cd api
# via pytest (assert por pergunta)
OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval

# via script (relatório lado a lado, melhor para comparar versões de prompt)
OPENAI_API_KEY=... python -m tests.eval.run_llm_eval
OPENAI_API_KEY=... python -m tests.eval.run_llm_eval --id volume_abaixo_do_minimo
OPENAI_API_KEY=... python -m tests.eval.run_llm_eval --rag   # injeta o RAG filtrado (mais caro)
```

Sem `OPENAI_API_KEY` o teste dá **skip** (nunca falha por falta de chave) e o
script sai com código 2.

## `questions.json`

Conjunto **versionado**: ele declara `prompt_version` e `package_version`.
`tests/unit/test_package_eval.py::test_conjunto_declara_as_versoes_com_que_foi_escrito`
falha quando o prompt muda e o conjunto não é revisto — de propósito. Sem isso,
uma piora de qualidade depois de uma edição de prompt seria indistinguível de
variação do modelo, e ninguém saberia contra o que está comparando.

Ao acrescentar uma pergunta, preencha o que ela **exigiria** do pacote
(`expect_metric_keys`, `expect_baseline_keys`, `expect_evidence_codes`,
`expect_context_event_types`) e o que a resposta **não pode** dizer
(`forbidden_in_answer`). Uma pergunta sem expectativa não avalia nada.

# Benchmark de modelos: foto do prato (issue #326)

Status: **preparado, pendente de execução** (decisão do usuário em 26/11: não executar chamadas
pagas agora; o DEV não tem `ANTHROPIC_API_KEY`). Ferramenta: `tools/benchmark_meal_photo/`.

## Pergunta

A leitura da foto do prato (`meal_photo`, hoje `claude-haiku-5-5` esforço `medium` com fallback
OpenAI, `infra/llm_providers.tf`) ganha materialmente com outro modelo, a ponto de compensar
custo e latência?

## Metodologia

- Dataset: Nutrition5k (Google, CC BY 4.0), foto de cima (`realsense_overhead/<dish>/rgb.png`) e
  metadados com massa e nutrientes por ingrediente; base
  `https://storage.googleapis.com/nutrition5k_dataset/nutrition5k_dataset`. Amostra sorteada
  (padrão 30, semente 326) do split `rgb_test_ids`.
- Mesma chamada de produção (`read_meal_photo`, prompt `nutrition-photo-v3`) e mesma revisão do
  motor (`meal_photo.review_reading`); só provedor/modelo/esforço trocados por env no processo,
  fallback desligado.
- Configurações: Haiku 5.5 `medium` (atual), Sonnet 5.5 `medium`, OpenAI do fallback configurado.
- Nutrientes: catálogo do DEV pelo casamento do worker (`match_items` + `build_result`) quando
  rodado com `--dev-env-file`; senão a estimativa por 100 g da IA. Cálculo pelo `meal_math`.
- Sem escrita em banco; cache e resultados fora do repositório.

## Métricas (`tools/benchmark_meal_photo/metricas.py`)

| Métrica | Definição |
|---|---|
| Identificação | pareamento guloso por similaridade de palavras (glossário pt→en, limiar 0,6); acertos, omissões (real visível sem par), invenções (lido sem par); precisão e recall visível micro |
| Peso por item | erro absoluto em g nos pares |
| Peso total | erro absoluto e relativo da soma lida contra a massa do prato |
| kcal/P/C/G | erro absoluto e relativo do total após o catálogo (ou da estimativa da IA) |
| Schema | fração de respostas válidas no `MealPhotoOut` |
| Latência | p50/p95 da chamada |
| Custo | por leitura, tokens reais × preço de `ai_usage.estimate_cost_usd` |

Distribuições em p50/p90/máx, não só média. Casos difíceis: os 5 piores por erro relativo de kcal.

## Critério de decisão

Trocar o modelo de `meal_photo` só se o candidato tiver **ganho material** sobre o Haiku atual e
o ganho pagar o custo e a latência. Proposta, a confirmar pelo usuário antes da execução:

- ganho material: erro relativo de kcal p50 pelo menos 10 pontos percentuais menor **e** p90 não
  pior, ou recall visível pelo menos 10 pontos maior sem subir invenções;
- schema válido igual ou melhor, onde schema válido = leituras com resposta válida / leituras que
  chegaram a ter resposta. Resposta cortada, JSON inválido e recusa contam como inválidas;
  falha de transporte, autenticação, cobrança, rede ou timeout (`falhas_por_codigo`) fica fora do
  denominador e deve ser comparada à parte (ok/total e falhas por código em cada config);
- comparação de kcal/P/C/G só vale informando, por config, quantas leituras ok tiveram o total
  desconhecido (coluna "desconhecido kcal/P/C/G"): elas somem das distribuições de erro;
- latência p95 dentro do timeout da feature com folga;
- custo por leitura compatível com a cota diária de IA da nutrição.

Sem ganho material, mantém o Haiku. A troca, se aprovada, é uma entrada em
`infra/llm_providers.tf` só para `meal_photo`, por PR, com rollback pela reversão da entrada.

## Resultados

**Pendente de execução.** Preencher com a tabela de `resultado.md` (por modelo: ok/total, schema,
precisão, recall visível, erro de massa total, erro de kcal, erro de P/C/G, latência, custo).

Limites da medição: leituras falhas ficam fora de precisão, recall e erros (ver ok/total); o custo
de falha sem tokens informados não é contado (`custo_total` subestima); em falha do processo só o
`resultado.json` parcial é gravado, não o markdown.

## Casos difíceis

**Pendente de execução.**

## Conclusão e recomendação

**Pendente de execução.**

## Limitações

Dataset de refeitório dos EUA, sem pratos brasileiros típicos; foto de cima sem escala de
referência; temperos de poucos gramas fora das omissões; glossário pt→en curto; catálogo do DEV
pode diferir do de produção; PNG do dataset convertido para JPEG.

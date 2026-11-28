# Benchmark de modelos: foto do prato (issue #326)

Status: **executado em 28/11/2026**; decisão do usuário registrada no
[ADR 0038](../adr/0038-foto-do-prato-openai-com-fallback-haiku.md). Ferramenta:
`tools/benchmark_meal_photo/`.

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

Execução de 28/11/2026, prompt `nutrition-photo-v3`, semente 326, nutrientes pelo catálogo do DEV.
Dos 30 pratos sorteados, 18 tinham a foto de cima disponível (12 sem foto utilizável, HTTPError);
a comparação vale para esses 18. Custo total da rodada: cerca de US$ 0,33. Distribuições em
p50 / p90 / máx.

| Config | ok/total | schema válido | precisão | recall visível | erro massa total | erro kcal | erro P g | erro C g | erro G g | latência s p50/p95 | custo médio US$ | custo total US$ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 medium (anterior; agora fallback) | 18/18 | 100% | 45% | 37% | 87% / 190% / 266% | 47% / 190% / 1187% | 5.4 / 15.6 / 31.9 | 10.4 / 23.6 / 36.0 | 3.9 / 11.5 / 27.3 | 6.9 / 11.0 | 0.0010 | 0.0183 |
| claude-sonnet-5-5 medium | 18/18 | 100% | 58% | 44% | 52% / 119% / 128% | 34% / 73% / 712% | 1.7 / 10.5 / 22.6 | 6.8 / 22.5 / 34.2 | 1.7 / 11.2 / 33.7 | 3.5 / 7.3 | 0.0143 | 0.2570 |
| gpt-5.4-mini (nova primária) | 18/18 | 100% | 38% | 29% | 33% / 70% / 132% | 26% / 75% / 234% | 1.8 / 10.8 / 23.8 | 4.4 / 14.3 / 32.4 | 1.0 / 9.2 / 38.7 | 2.5 / 3.8 | 0.0031 | 0.0561 |

Nenhuma falha por código; nenhuma leitura ok com kcal/P/C/G desconhecido (0 de 18 em todas).
Latência só das leituras ok (p50 / p95): Haiku 6,9 / 11,0 s; Sonnet 3,5 / 7,3 s; OpenAI 2,5 / 3,8 s.

Limites da medição: leituras falhas ficam fora de precisão, recall e erros (ver ok/total); o custo
de falha sem tokens informados não é contado (`custo_total` subestima); em falha do processo só o
`resultado.json` parcial é gravado, não o markdown.

## Casos difíceis

- O prato `dish_1557861216` ficou com kcal desconhecido nas três configurações (sem omitidos nem inventados).
- A salada caesar (`dish_1559934477`) foi o pior caso nos três: kcal de 1187% (Haiku), 712% (Sonnet) e 234% (OpenAI). Todos omitiram `caesar salad` como item e inventaram componentes (alface, crouton, molho caesar e, conforme o modelo, frango, parmesão ou abóbora).
- Haiku: também `dish_1559239151` (206%, inventou cenoura), `dish_1558718587` (180%) e `dish_1567007848` (98%, omitiu `berries` e inventou morango, mirtilo e amora).
- Sonnet: `dish_1559239151` (80%), `dish_1558718587` (68%) e `dish_1560444967` (53%, cinco omitidos e cinco inventados, como melão e batata-doce).
- OpenAI: `dish_1558718587` (77%), `dish_1561574815` (74%, omitiu vinaigrette, pepino, rúcula e alface) e `dish_1566502573` (69%, 15 omitidos e inventou grão-de-bico, espinafre e peixe).

## Conclusão e recomendação

O OpenAI (gpt-5.4-mini) teve o menor erro de kcal (p50 26%) e de massa total (33%) e a menor
latência (2,5 s), a cerca de 3 vezes o custo do Haiku por leitura (0,0031 contra 0,0010 US$). O
Haiku, atual, foi o pior em kcal (47%), massa (87%) e latência (6,9 s). O Sonnet ficou no meio em
erro (34% e 52%) e foi o melhor em identificação (precisão 58%, recall 44%), a cerca de 14 vezes o
custo do Haiku. Na identificação de itens o OpenAI foi o pior (precisão 38%, recall 29%).

Critério de decisão atendido: o OpenAI tem ganho material sobre o Haiku (kcal p50 26% contra 47%;
p90 75% contra 190%).

Decisão do usuário (28/11/2026): OpenAI primária só na `meal_photo`, com fallback Haiku. Registro,
alternativas e rollback no [ADR 0038](../adr/0038-foto-do-prato-openai-com-fallback-haiku.md).

## Limitações

Só 18 pratos com foto utilizável (12 dos 30 sorteados falharam por HTTPError), então p90 e máximo
são instáveis; dataset de refeitório dos EUA, sem pratos brasileiros típicos; identificação medida
por glossário pt→en aproximado; catálogo do DEV pode diferir do de produção; foto de cima sem
escala de referência; temperos de poucos gramas fora das omissões; PNG do dataset convertido para
JPEG.

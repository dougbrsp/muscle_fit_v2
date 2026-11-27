# Benchmark de modelos da foto do prato (issue #326)

Compara, na mesma leitura de produção (`api/app/nutrition_meal_photo_ai.read_meal_photo`,
prompt `nutrition-photo-v3`, revisão do motor `meal_photo.review_reading`), três configurações:

| Config | Provedor / modelo | Esforço |
|---|---|---|
| `haiku` | Anthropic `claude-haiku-5-5` (atual) | `medium` |
| `sonnet` | Anthropic `claude-sonnet-5-5` | `medium` |
| `openai` | OpenAI, modelo de `OPENAI_NUTRITION_VISION_MODEL` (padrão do código: o fallback configurado) | - |

Os nomes vêm das constantes de `api/app/llm_anthropic.py`; nada é escrito à mão no script.
Relatório-modelo: `docs/benchmarks/foto-do-prato-modelos.md`.

## Como a troca de modelo é feita (sem mexer em produção)

O adaptador já lê provedor, modelo e esforço por feature em variáveis de ambiente
(`llm_anthropic.provider_for`, `anthropic_model_for`, `anthropic_effort_for`). O script só
define essas variáveis **no próprio processo**:

- `LLM_PROVIDER_MEAL_PHOTO` = `anthropic` ou `openai`
- `ANTHROPIC_MODEL_MEAL_PHOTO`, `ANTHROPIC_EFFORT_MEAL_PHOTO`
- `LLM_FALLBACK_PROVIDER_MEAL_PHOTO` vazio e `LLM_FALLBACK_PROVIDER` removido: cada linha mede o
  modelo pedido, sem resposta silenciosa do fallback (o script também avisa se `model` da
  resposta não for o esperado).

Nenhum código de `api/` ou `infra/` muda. Trocar o modelo **da produção** só para `meal_photo`
é decisão do usuário, via `infra/llm_providers.tf` (mapas de provedor, modelo e esforço por
feature) em PR; o rollback é reverter a mesma entrada.

## Variáveis (por nome)

- `ANTHROPIC_API_KEY`: configs `haiku` e `sonnet`. Sem ela, as duas são puladas com aviso.
- `OPENAI_API_KEY`: config `openai`. Sem ela, é pulada com aviso.
- `OPENAI_NUTRITION_VISION_MODEL` (opcional): modelo OpenAI; sem ela, o padrão do código.
- `MUSCLEFIT_BENCHMARK_CACHE` (opcional): pasta de cache; padrão `%LOCALAPPDATA%\musclefit-benchmark`
  ou `~/.cache/musclefit-benchmark`. Cache dentro do repositório é recusado.
- O submódulo privado `api/privado` precisa estar presente (template do prompt).

## Como rodar

```bash
# Validação, sem rede e sem LLM: configs, chaves presentes, URLs e custo estimado.
PYTHONUTF8=1 python tools/benchmark_meal_photo/metricas.py
PYTHONUTF8=1 python tools/benchmark_meal_photo/benchmark.py --dry-run

# Execução paga (só com aval): 30 pratos, as três configs, nutrientes pela estimativa da IA.
PYTHONUTF8=1 python tools/benchmark_meal_photo/benchmark.py --amostra 30

# Com o casamento do catálogo (worker de produção) no MySQL do DEV local, só leitura.
PYTHONUTF8=1 python tools/benchmark_meal_photo/benchmark.py --dev-env-file .env --user-id 1
```

Opções: `--configs haiku,sonnet`, `--semente`, `--cache-dir`, `--saida` (cache e saída ficam fora do
repositório), `--massa-min-visivel`. Chave `replace_me` conta como ausente. `--dev-env-file` só aceita o
MySQL do compose local (127.0.0.1:3306/fitness, nenhum valor com "prod"); senão recusa antes de conectar.
Se o modelo que respondeu difere do pedido, o console e o markdown mostram "respondeu X, esperado Y".
O `resultado.json` é regravado a cada configuração e no `finally` (parcial se o processo cair). Latência:
a tabela traz a geral (inclui falhas); o markdown lista também p50/p95 só das leituras ok.
`--amostra` vai de 1 a 100. Leituras falhas ficam fora de precisão, recall e erros (ver ok/total); o custo de
falha sem tokens não é contado (`custo_total` subestima); em falha do processo só o JSON parcial é gravado
(o markdown não). Schema válido: resposta cortada, JSON inválido e recusa = inválido; transporte, autenticação,
cobrança, rede e timeout = fora do denominador (ver `falhas_por_codigo`). Nutrientes desconhecidos somem das
distribuições de erro: a tabela traz a contagem "desconhecido kcal/P/C/G".
Saída: `resultado.json` (bruto, por leitura) e `resultado.md` (tabela por modelo com p50/p90/máx e
casos difíceis) em `<cache>/resultados/<data>/`.

## Custo e tempo estimados

O `--dry-run` imprime, por config, o custo **típico** (saída no maior observado herdado,
`LLM_OUTPUT_OBSERVED_MAX_TOKENS`) e o **teto** (saída no teto da feature, `LLM_MAX_OUTPUT_TOKENS`),
com os preços de `api/app/domain/nutrition/ai_usage.py`. Medido em 26/11 (30 pratos, entrada
estimada em ~1,9 mil tokens): Haiku ~US$ 0,01 típico / US$ 0,25 teto; Sonnet ~US$ 0,24 / US$ 5,03;
OpenAI teto US$ 2,25. O raciocínio adaptativo da Anthropic conta como saída, então o real fica
entre os dois. Tempo: ~30 leituras por config em série, alguns segundos cada (latência medida
no próprio benchmark); download inicial ~3 CSV pequenos + 30 PNG de 640x480.

## Limitações

- Nutrition5k é de um refeitório dos EUA: nada de prato brasileiro típico (feijão, farofa, PF).
- Foto de cima, sem objeto de escala de referência: o erro de peso reflete isso.
- Ingredientes com menos de `--massa-min-visivel` g (sal, óleo, temperos) não contam como omissão.
- Identificação compara o nome lido (pt-BR) com o do dataset (inglês) por um glossário curto
  (`metricas.GLOSSARIO_PT_EN`) e similaridade de palavras; nome fora dele pode virar falsa omissão.
- Sem `--dev-env-file`, kcal/P/C/G vêm da estimativa da IA, não do catálogo; com ele, o catálogo
  do DEV pode diferir do de produção.
- A foto do dataset é PNG convertida para JPEG (o app manda JPEG comprimido do celular).

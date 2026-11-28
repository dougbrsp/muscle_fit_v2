# ADR 0038: Foto do prato com OpenAI como primária e fallback Haiku (issue #326)

- **Status**: Aceita, 2026-11-28 (decisão do usuário após o benchmark da #326).
- **Data**: 2026-11-28
- **Relaciona-se a**: [ADR 0037](0037-foto-do-prato-v2-evidencia-por-item.md) (foto do prato; a comparação de modelos foi deixada para a #326); `docs/benchmarks/foto-do-prato-modelos.md` (metodologia e resultado completo); `infra/llm_providers.tf` (tabela de provedores por feature).

## Contexto

- A `meal_photo` rodava em `claude-haiku-5-5` esforço `medium`, com fallback OpenAI configurado em `infra/llm_providers.tf`. A #326 perguntou se outro modelo ganha o bastante para compensar custo e latência.
- Benchmark de 28/11/2026 (`tools/benchmark_meal_photo/`, prompt `nutrition-photo-v3`, semente 326, nutrientes do catálogo do DEV): 18 pratos do Nutrition5k com foto de cima utilizável, dos 30 sorteados (12 sem foto utilizável, HTTPError). Custo total da rodada aproximadamente US$ 0,33. Todas as leituras ok, schema 100% nas três configurações.

| Config | erro kcal p50 | erro de massa total p50 | latência p50 | custo por leitura (US$) |
|---|---|---|---|---|
| gpt-5.4-mini | 26% | 33% | 2,5 s | 0,0031 |
| claude-haiku-5-5 (atual) | 47% | 87% | 6,9 s | 0,0010 |
| claude-sonnet-5-5 | 34% | 52% | 3,5 s | 0,0143 |

- Ressalva: na identificação de itens o OpenAI foi o pior (precisão 38% e recall visível 29%, contra 45%/37% do Haiku e 58%/44% do Sonnet). O pior caso nos três foi a salada caesar (`dish_1559934477`).
- Limitações da amostra (18 pratos, sem pratos brasileiros, glossário pt→en aproximado, catálogo do DEV) estão no documento do benchmark.

## Alternativas consideradas

- **Manter o Haiku.** Recusada: erro de kcal p50 de 47% e de massa de 87%, e a maior latência (6,9 s). É o mais barato, mas o erro de quantidade é o que mais pesa no diário.
- **Sonnet 5.5.** Recusada: melhor identificação e erro intermediário, mas cerca de 14 vezes o custo por leitura do Haiku e 4,6 vezes o do OpenAI, e ainda pior que o OpenAI em kcal e massa.
- **OpenAI sem fallback.** Recusada: a feature ficaria sem alternativa em indisponibilidade do provedor. O fallback para o Haiku cobre 429, 5xx e erro de rede (não lentidão, ver "Impacto em produção").

## Decisão

1. `meal_photo` passa a ter `openai` (gpt-5.4-mini) como provedor primário e `anthropic` (Haiku 5.5) como fallback, por entrada em `infra/llm_providers.tf`, só para essa feature. Deploy por Terraform via PR.
2. Fallback OpenAI para Anthropic, novo e genérico, em `api/app/openai_client._dispatch`. Elegível só em 429, 5xx, timeout e erro de rede. Não é elegível em 4xx de conteúdo, 401/403, chave ausente ou resposta inválida: esses casos não melhoram trocando de provedor, ou escondem erro de configuração.
3. O fallback fica ligado só na `meal_photo`. As demais features seguem inalteradas.

## Consequências

- Custo por leitura da foto cerca de 3 vezes maior (0,0031 contra 0,0010 US$), dentro da cota diária de IA da nutrição.
- Leitura mais rápida (p50 de 2,5 s contra 6,9 s) e erro de kcal e de massa menores.
- Pior identificação de itens no benchmark (recall visível 29% contra 37%): a revisão pela pessoa antes de salvar continua necessária.
- Demais features inalteradas. O mecanismo de fallback OpenAI para Anthropic existe, mas só a `meal_photo` o usa.

## Impacto em produção

- **Deploy**: exige `run_terraform=true` (mudança em `infra/llm_providers.tf`) além do backend.
- **Plan esperado**: só 2 envs mudam, nas 4 Lambdas que consomem `local.llm_provider_env` (`musclefit-api-core`, `musclefit-plan-generator`, `musclefit-workout-ai`, `musclefit-cardio-agent`): `LLM_PROVIDER_MEAL_PHOTO` anthropic para openai e `LLM_FALLBACK_PROVIDER_MEAL_PHOTO` openai para anthropic. Orçamento de envs com variação líquida de 0 B nas 4; a cardio-agent não usa a `meal_photo`, então não há efeito funcional nela.
- **Ordem**: o pipeline (`.github/workflows/deploy-producao.yml`) aplica o Terraform antes da imagem (o backend tem `needs: terraform-apply`). Nessa janela curta, o código antigo já usa a OpenAI e fica sem fallback, em silêncio: o código antigo nem lê o fallback quando o primário é `openai`, portanto não há aviso `llm.config_invalid`. Quando a imagem nova sobe, o fallback passa a valer.
- **Chaves e modelos**: a plan-generator já tem os segredos de OpenAI e Anthropic e `OPENAI_NUTRITION_VISION_MODEL` (default gpt-5.4-mini). As envs de modelo e esforço do Claude seguem publicadas para o fallback.
- **Timeout**: tentativa primária e fallback correm em série dentro de `LLM_TIMEOUT_SECONDS` (90 s), abaixo dos 420 s da Lambda. Em timeout da OpenAI o tempo restante costuma ficar abaixo do mínimo de uma tentativa, então o fallback quase não dispara. Ele cobre bem 429, 5xx e erro de rede; não promete cobertura em lentidão.
- **Custo**: cerca de 3 vezes por leitura.
- **DEV**: usa os defaults do código (já OpenAI) e não exercita o fallback.
- **Smoke pós-deploy**: uma foto real e conferir a coluna `model` de `ai_usage_log` (migração `085_ai_usage_log.py`; a tabela não tem coluna de provedor): nas linhas de feature `meal_photo` deve ser `gpt-5.4-mini`. Uma linha com `claude-*` indica que o fallback disparou (o `model` gravado é o do provedor que respondeu). Consulta de leitura de exemplo: `SELECT DATE(started_at) AS dia, model, COUNT(*) FROM ai_usage_log WHERE feature = 'meal_photo' AND started_at >= NOW() - INTERVAL 3 DAY GROUP BY dia, model ORDER BY dia`. O evento `llm.fallback` aparece nos logs da Lambda `musclefit-plan-generator` (CloudWatch Logs Insights), não no banco.
- **Prova do plan**: "só 2 envs mudam" e "variação líquida 0 B" vêm da leitura do código; a prova é o `terraform plan` do PR (workflow `terraform.yml`), que deve mostrar só essas 2 envs alteradas (`~`) em `musclefit-api-core`, `musclefit-plan-generator`, `musclefit-workout-ai` e `musclefit-cardio-agent`, sem outras mudanças. O plan é conferido no PR antes do merge.
- **Imagem**: a mudança de código toca só arquivos em `api/app`; nenhum arquivo novo a liberar no `.dockerignore`.
- **Janela Terraform/imagem**: `run_terraform=true` roda no mesmo deploy da imagem (`deploy-producao.yml`), então a janela "Terraform antes da imagem" é curta e benigna. Com a OpenAI primária, um 5xx/429 que esgote os retries ainda deixa tempo para o Claude dentro dos 90 s.

## Rollback

Não é rollback só de imagem: exige Terraform. Voltar `meal_photo` para `anthropic` com fallback `openai` na entrada de `infra/llm_providers.tf` e fazer o deploy por Terraform via PR. O código do fallback novo pode ficar: sem entrada que o use, não é acionado. Se o código for revertido com o Terraform novo ainda aplicado, o código antigo fica sem fallback e sem aviso (o aviso `llm.config_invalid` só existe se o primário for `anthropic` com fallback `anthropic`); a foto segue funcionando, não quebra.

## O que medir após a promoção

- `ai_usage_log` da `meal_photo` por provedor e modelo (volume, custo, tokens).
- Taxa de fallback pelo evento `llm.fallback`.
- Taxa de `reading_invalid`.
- Tokens por leitura, para conferir o custo estimado.

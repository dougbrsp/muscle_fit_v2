# Dados pessoais enviados à Anthropic (Claude): minimização e retenção

Data: 2026-10-30. Espelho de [`RETENCAO_DADOS_OPENAI.md`](RETENCAO_DADOS_OPENAI.md) para o provedor Anthropic,
que entra como opção **por feature** no cliente único (`api/app/openai_client.py` → `api/app/llm_anthropic.py`).
Com os padrões de hoje, **nenhuma** feature chama a Anthropic: toda feature continua na OpenAI até um PR ligar
`LLM_PROVIDER_<FEATURE>=anthropic` (ver `infra/llm_providers.tf`). Os cards ilustrados (imagem) ficam na OpenAI.

## Regra

As regras 1, 2 e 4 da política da OpenAI valem iguais, porque o prompt é o MESMO (o Claude recebe byte a byte o
texto que iria para a OpenAI; teste `api/tests/unit/test_llm_provider_anthropic_features.py`):

1. **Nenhum identificador direto entra no prompt.** O `user_id` vai só para o log (`llm.usage`) e nunca para o
   corpo da requisição. O cliente não manda `metadata.user_id` à Anthropic.
2. **Só entra o que a tarefa usa.** O inventário por feature é o da política da OpenAI, sem mudança.
3. **Sem armazenamento do lado da Anthropic que nós controlemos.** A Messages API não tem parâmetro equivalente ao
   `store` da OpenAI. Não usamos Files API, Batches, Managed Agents nem nada que guarde conteúdo por nossa conta: cada
   resposta é lida na hora e o que importa é gravado no nosso banco.
4. **Nada de prompt nem de resposta em log.** `llm.usage`, `llm.error`, `llm.output_incomplete` e `llm.fallback`
   levam só metadados, com `provider=anthropic`. Na recusa, o diagnóstico leva só a categoria (`stop_details.category`),
   nunca a explicação nem o texto. A chave (`sk-ant-...`) é mascarada pelo mesmo `redact_diagnostic` da OpenAI.

## Retenção na Anthropic

Fonte principal: Privacy Center da Anthropic, artigo "How long do you store my data?"
(<https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-data>, última atualização 2026-07-01,
consultado em 2026-10-30). Uso para treino: Commercial Terms of Service
(<https://www.anthropic.com/legal/commercial-terms>, consultado em 2026-10-30).

| Onde | O quê | Quanto tempo | Fonte |
|---|---|---|---|
| Backend da API (padrão) | Entradas e saídas | "we automatically delete inputs and outputs on our backend within 30 days of receipt or generation" | Privacy Center |
| Conversa marcada pelos sistemas de *trust and safety* | Entradas e saídas | Até 2 anos | Privacy Center |
| Idem | Notas de classificação de *trust and safety* | Até 7 anos | Privacy Center |
| Exigência legal ou aplicação da Usage Policy | Entradas e saídas | Pelo tempo necessário | Privacy Center |
| Retenção zero (ZDR) | — | Só por acordo com a Anthropic. **Não temos acordo** | Privacy Center |
| "Covered Models" | — | A Anthropic exige retenção limitada e revisão como parte do trabalho de segurança. **A confirmar** se `claude-sonnet-5-5` e `claude-haiku-5-5` são "Covered Models" e qual o prazo | Privacy Center |
| Treino de modelos | Conteúdo enviado pela API | "Anthropic may not train models on Customer Content from Services" | Commercial Terms |
| Região de processamento | — | **A confirmar.** O cliente não manda `inference_geo`; onde a inferência roda fica no padrão da conta | — |

Do nosso lado, nada muda: DynamoDB `async_jobs` (TTL de 6 h), `cardio_recommendations.context_snapshot`,
`template_generation_logs.request_json` e `activities.ai_summary` seguem a tabela da política da OpenAI.

## Pendências

- Confirmar na Console da Anthropic (organização do `musclefit/claude-api-key`) o prazo de retenção vigente e se
  algum dos modelos usados é "Covered Model", e citar a Anthropic como suboperador na política de privacidade
  **antes** do PR que liga a primeira feature.
- Avaliar se algum dado de saúde do inventário deve sair do prompt antes da virada de cada feature (mesmo critério
  do IA-17: só com o eval pago da feature).

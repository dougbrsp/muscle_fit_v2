# Dados pessoais enviados à Anthropic (Claude): minimização e retenção

Data: 2026-10-30, revisado em 2026-10-31 (virada). Espelho de [`RETENCAO_DADOS_OPENAI.md`](RETENCAO_DADOS_OPENAI.md)
para o provedor Anthropic, que entra **por feature** no cliente único (`api/app/openai_client.py` →
`api/app/llm_anthropic.py`).

**Desde a virada de 2026-10-31** (`infra/llm_providers.tf`), seis features mandam dados à Anthropic, todas no
`claude-haiku-5-5` e com fallback para a OpenAI (que então recebe o mesmo prompt, como antes):

| Feature | O que vai para a Anthropic (inventário igual ao da política da OpenAI) |
|---|---|
| `cardio_agent` | contexto compacto de cardio (plano, recuperação, wellness do Intervals, cardio realizado) + RAG |
| `label_photo` | **foto** do rótulo nutricional (bloco `image` base64) + instruções |
| `insights` | pacote de visão geral montado pelo servidor + pergunta do usuário + RAG |
| `meal_parse` | texto livre da refeição digitado pelo usuário (delimitado como dado) |
| `exercise_description` | dados do exercício do catálogo (nome, equipamento, músculos) |
| `workout_summary` | séries, notas e decisões do motor de um treino (delimitados como dado) |

`periodization`, `set_evaluation`, `workouts_analysis`, `exercise_summary` e `workout_plan` seguem só na OpenAI. Os
cards ilustrados (imagem) também.

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
consultado em 2026-10-30 e de novo em 2026-10-31). Uso para treino: Commercial Terms of Service
(<https://www.anthropic.com/legal/commercial-terms>, consultado em 2026-10-30). "API and data retention"
(<https://platform.claude.com/docs/en/manage-claude/api-and-data-retention>) e "Data residency"
(<https://platform.claude.com/docs/en/manage-claude/data-residency>), consultados em 2026-10-31.

| Onde | O quê | Quanto tempo | Fonte |
|---|---|---|---|
| Backend da API (padrão) | Entradas e saídas | "we automatically delete inputs and outputs on our backend within 30 days of receipt or generation" | Privacy Center |
| Conversa marcada pelos sistemas de *trust and safety* | Entradas e saídas | Até 2 anos | Privacy Center |
| Idem | Notas de classificação de *trust and safety* | Até 7 anos | Privacy Center |
| Exigência legal ou aplicação da Usage Policy | Entradas e saídas | Pelo tempo necessário | Privacy Center |
| Retenção zero (ZDR) | — | Só por acordo com a Anthropic. **Não temos acordo** (a confirmar na Console) | Privacy Center |
| "Covered Models" | — | Exigem 30 dias de retenção. São só "Claude Fable 5.1, Claude Mythos 5.1, Claude Fable 5, and Claude Mythos 5": `claude-haiku-5-5` e `claude-sonnet-5-5` **não** são | API and data retention |
| Schema da saída estruturada | Só o JSON schema (sem prompt nem resposta) | "Only the JSON schema is cached, for up to 24 hours since last use". Nossos schemas não levam dado de usuário | API and data retention |
| Cache de prompt | Representações KV e hashes, em memória | "Your prompts and Claude's outputs are not stored" (o cliente não manda mais `cache_control`, ver `llm_anthropic.ANTHROPIC_CACHE_CONTROL_FEATURES`) | API and data retention |
| Treino de modelos | Conteúdo enviado pela API | "Anthropic may not train models on Customer Content from Services" | Commercial Terms |
| Região de processamento | Inferência | O cliente não manda `inference_geo`: vale o `default_inference_geo` do workspace; sem ele, `"global"` ("Inference may run in any available geography"). Só existem `"global"` e `"us"`; não há região no Brasil | Data residency |
| Região de armazenamento | Dados em repouso | "Currently, `"us"` is the only available workspace geo" | Data residency |

Do nosso lado, nada muda: DynamoDB `async_jobs` (TTL de 6 h), `cardio_recommendations.context_snapshot`,
`template_generation_logs.request_json` e `activities.ai_summary` seguem a tabela da política da OpenAI.

## Pendências (checklist do usuário; não bloqueiam o PR)

- [ ] Console da Anthropic (organização/workspace do `musclefit/claude-api-key`): conferir o prazo de retenção
      vigente e se há acordo de ZDR (hoje presumimos que **não**: 30 dias).
- [ ] Console: conferir `default_inference_geo`/`allowed_inference_geos` do workspace. Sem configuração, a inferência
      é `"global"`; `"us"` custa 1,1× (página de preços).
- [ ] Política de privacidade: o app não tem página publicada que liste suboperadores (nada em `frontend/src`). Ao
      publicar uma, citar OpenAI **e** Anthropic (transferência internacional para os EUA, LGPD art. 33) — texto
      jurídico a revisar pelo usuário.
- [ ] Avaliar se algum dado de saúde do inventário deve sair do prompt (mesmo critério do IA-17: só com o eval pago
      da feature).

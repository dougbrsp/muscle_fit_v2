# Eval da periodização v15: escolha guiada pelo motor (2026-10-31)

Continuação de `docs/ia/EVAL_PERIODIZACAO_2026-10-31.md` (rodadas 1 e 2, prompt `periodization-v14`). Mesmos 10 casos congelados (`api/tests/eval/casos/periodization.json`), RAG real de `api/privado`, cliente único de produção (`run_periodization_draft` → `call_openai_structured` → `llm_anthropic`), teto de 150 s e `max_tokens` de 30.720.

## O que mudou (PR #17)

- **Referência do catálogo no lugar do trio.** `periodization/catalog_refs.py` numera as opções válidas da academia (`E1`, `E2`, …). Cada referência embute grupo PRIMARY + exercício + equipamento.
  - O prompt lista as referências, sem os ids.
  - No Modo B, o item da saída tem só `catalog_ref`, restrito no schema a uma `enum` por pedido.
  - O motor monta o trio antes de qualquer validação.
- **`rest_seconds` fora do schema da LLM** nos dois modos. O validador já zerava o campo.
- **Critério do trio adaptado ao formato novo.**
  - O critério avalia a saída RESOLVIDA pelo motor (o que a LLM escolheu), antes dos auto-fixes.
  - A conferência contra o catálogo é a mesma.
  - Referência inválida reprova como "trio fora do catálogo (referência inválida …)".
  - Nenhum outro critério mudou.

## Braços

| Braço | Provedor e modelo | Raciocínio / esforço | Rodadas |
|---|---|---|---|
| A15 | OpenAI `gpt-5.4-mini-2026-03-17` | sem raciocínio | 3 × 10 |
| H15-off | `claude-haiku-5-5` | `disabled`, `low` | 5 × 10 |
| H15-ad | `claude-haiku-5-5` | `adaptive`, `low` | 3 × 10 |

Os três braços rodaram em paralelo (no máximo 3 chamadas simultâneas).

## Resultado contra a v14

Aprovação = casos sem nenhum problema nos critérios. Latência = `latency_ms` do `llm.usage`, com p95 pelo posto mais próximo. Custo pela tabela do dono (`app.domain.nutrition.ai_usage`). As linhas da v14 vêm do relatório anterior.

| Braço | Prompt | Aprovação | Cortadas | Timeouts | p50 / p95 (s) | 6 dias (s) | Saída p50 / máx. (tokens) | US$/geração |
|---|---|---|---|---|---|---|---|---|
| A gpt-5.4-mini | v14 | 8/20 (40%) | 0 | 0 | 7,5 / 13,4 | 10,0–19,2 | 2,9 mil / 4,9 mil | 0,0204 |
| A15 gpt-5.4-mini | **v15** | **26/30 (87%)** | 0 | 0 | 7,8 / 13,2 | 9,3–13,4 | 2,4 mil / 4,1 mil | 0,0167 |
| (c) Haiku `low` sem raciocínio | v14 | 27/50 (54%) | 0 | 0 | 12,1 / 30,2 | 17,6–41,2 | 4,0 mil / 11,3 mil | 0,0059 |
| H15-off Haiku `low` sem raciocínio | **v15** | **47/50 (94%)** | **0** | **0** | 11,9 / **26,1** | 14,7–27,5 | 3,2 mil / 8,4 mil | **0,0055** |
| E' Haiku `low` com raciocínio | v14 | 26/30 (87%) | 2 | 0 | 24,6 / 125,3 | 73,5–141,0 | 5,7 mil / ~30 mil | 0,0100 |
| H15-ad Haiku `low` com raciocínio | **v15** | 30/30 (100%) | 0 | 0 | 30,4 / **92,8** | 61,5–94,6 | 9,4 mil / 23,0 mil | 0,0087 |

Falhas por critério na v15 (problema × ocorrências):
- **A15:**
  - sequência incompleta ×2;
  - 4 priorizados (máx. 3) ×1;
  - HIIT na véspera de perna ×1.
- **H15-off:**
  - sequência incompleta ×2 (`modo_b_6_dias_abc_ponderado`, `cardio_pedido_modo_b_4_dias`);
  - 4 priorizados ×1 (`cardio_hiit_vespera_de_perna`).
- **H15-ad:** nenhuma.

**Trio fora do catálogo: zero em 110 gerações** (contra 18 de 20 falhas da OpenAI v14 e 18 de 50 do Haiku sem raciocínio na v14). **Referências inválidas: 0 em 110**, porque a `enum` restringe a saída nos dois provedores.

Pelo desenho, `EQUIPMENT_REPLACED` deixa de ter origem na escolha da IA:
- toda referência válida é um par ativo da academia sob o grupo PRIMARY, a mesma leitura de `_load_gym_pairs`;
- a referência desconhecida vira `ITEM_REMOVED` com `CATALOG_REF_UNKNOWN`, não troca de par;
- o validador não roda no eval (precisa de banco).

**Tokens.**
- Entrada: o prompt do Modo B encolheu 4% a 6% (snapshot com RAG fixo: 2,6 mil a 1,7 mil caracteres por caso). A `enum` de 104 referências entra no schema.
- Saída: encolheu, porque saíram 6 campos por item no Modo B. Na OpenAI, o p50 caiu de 2,9 mil para 2,4 mil tokens; no Haiku sem raciocínio, o máximo caiu de 11,3 mil para 8,4 mil.
- Custo por geração: −18% na OpenAI e −7% no Haiku sem raciocínio.

**A `enum` compila na Anthropic.** Foram 80 chamadas Claude com o schema adaptado (`llm_anthropic_schema.adapt_schema`), `enum` de 104 referências (69 no caso de 5 dias), com e sem cardio. Nenhum 400 e nenhum erro (`status: ok` em todas). A maior academia de produção tem 107 referências (leitura abaixo).

## Decisão: a periodização vai para o Haiku sem raciocínio, esforço `low`, fallback `openai`

Critério do pedido: aprovação ≥ 80% e acima da OpenAI v15, zero saída cortada, zero timeout e p95 ≤ 90 s.

| Braço | ≥ 80% e > OpenAI v15 (87%) | Cortada = 0 | Timeout = 0 | p95 ≤ 90 s |
|---|---|---|---|---|
| H15-off | sim (94%) | sim | sim | sim (26,1 s) |
| H15-ad | sim (100%) | sim | sim | **não (92,8 s)** |

O H15-off cumpre tudo e entra em `infra/llm_providers.tf` com:
- `periodization = "anthropic"`;
- modelo `claude-haiku-5-5`;
- esforço `low`;
- `anthropic_thinking_by_feature = { periodization = "disabled" }`;
- fallback por feature `openai`.

O orçamento de envs das Lambdas continua dentro do teste (`test_bloco_de_envs_de_llm_cabe_no_limite_de_4kb_da_lambda`).

Ressalva estatística: 94% contra 87% são 3 contra 4 reprovações em amostras de 50 e 30. A vantagem do Haiku é pequena. O ganho grande é dos dois provedores contra a v14, e o Haiku custa um terço por geração.

Rollback, janela da promoção e plan esperado: `docs/motores/FONTES_DA_VERDADE.md` §77.

## Medição de produção (leitura, 2026-10-31)

- **Catálogo da academia:** `gym_equipment` ativo × grupo PRIMARY, com no máximo `MAX_CATALOG_ITEMS_PER_GROUP` (8) por grupo, a mesma regra de `load_gym_catalog`.
  - A maior academia tem 107 referências (20 grupos, 134 pares); as seguintes, 73, 58, 52 e 43.
  - O teto teórico é 20 grupos × 8 = 160, abaixo de `MAX_ENUM_REFS` (250).
  - Nenhuma academia cai em string livre.
- **`EQUIPMENT_REPLACED` nos rascunhos:** `template_generation_logs` tem 8 gerações desde 2026-09-20 (4 no Modo B), e todos os `validation_report.auto_fixes` estão vazios. Nenhum `EQUIPMENT_REPLACED`, `MUSCLE_GROUP_RELABELED` ou `ITEM_REMOVED`.
  - A única geração no Modo B depois que o auto-fix existe (2026-09-28) não teve troca.
  - A amostra de produção é pequena demais para medir a taxa. A medida que vale é a do eval, e daqui em diante o relatório grava `catalog_refs` em cada geração.

## Custo do eval

| Braço | Gerações | US$ medido (`llm.usage`) |
|---|---|---|
| A15 | 30 | 0,500 |
| H15-off | 50 | 0,274 |
| H15-ad | 30 | 0,261 |
| **Total** | 110 | **≈ 1,04** (orçamento: US$ 3) |

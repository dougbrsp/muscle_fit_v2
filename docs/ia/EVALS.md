# Evals e snapshots de prompt das features de IA (IA-11)

Duas metades, com as mesmas entradas e o mesmo executor:

| Metade | Arquivo | Roda onde | Custa |
|---|---|---|---|
| Snapshot do prompt renderizado | `api/tests/unit/test_llm_prompt_renderizado_snapshot.py` | CI (unitário), todo PR | nada (rede mockada) |
| Criterios do eval provados sem LLM | `api/tests/unit/test_llm_eval_criterios.py` | CI (unitário), todo PR | nada |
| Eval pago | `api/tests/eval/test_llm_features_eval.py` (`-m llm_eval`) | manual, nunca no CI | ver tabela abaixo |

- **Casos congelados:** `api/tests/eval/casos/<feature>.json`, de 5 a 8 por feature: entradas SINTÉTICAS (M103 L2, 2026-10-29): a estrutura veio da coleta no DEV, mas medidas de saúde e treino (FC, HRV, sono, peso/cintura, cargas, reps, RPE), datas, notas livres e ids foram trocados por valores inventados, mantendo os cenários. Uma nova coleta (`coletar_casos_llm.py`) grava dado real e nunca vai para o repositório sem passar pela mesma troca. Foram escolhidos para cobrir progresso, queda de reps, platô, RPE alto, técnica avançada, dor e notas.
- **Executor único:** `api/tests/support/llm_casos.py` chama a função REAL de cada feature (`evaluate_set`, `summarize_exercise`, `generate_workout_summary`, `generate_insight`, `analyze_training_evolution`, `generate_cardio_recommendation`, `run_periodization_draft`, `generate_execution_description`). Datas são fixas: o cardio usa a data e a hora da coleta.
- **Coletor (somente leitura):** `api/tests/eval/coletar_casos_llm.py`. Ele recusa rodar fora de `DB_BACKEND=pymysql` com host local e abre toda conexão com `SET SESSION TRANSACTION READ ONLY`.

## Features, versões e casos

| Feature (`llm.usage.feature`) | `PROMPT_VERSION` (módulo) | Casos |
|---|---|---|
| `set_evaluation` | `set-eval-v8` (`exercise_set_evaluation_ai.py`; v8 = a seção da próxima série cita a regra que casou (`NextSetLoad.rule_text`) e o conjunto vigente na ordem com a versão (`next_set_load.describe_ruleset`), no lugar da regra escrita das constantes; conjunto publicado repassado por `dispatch_evaluate_set`, padrão sem publicado; 2026-10-20, R3. Eval v7 × v8 em 2026-10-20 (gpt-5.4-mini, `-k set_evaluation`): 10/10 e 10/10, mesma categoria nos 10 casos; só o conjunto padrão: o harness chama `evaluate_set` sem `ruleset`, o cadastrado tem unitário; v7 = linha da rampa de RPE planejada na seção da próxima série, 2026-10-12; v6: carga da PRÓXIMA série pronta pelo motor — `domain/next_set_load.py`: direção, carga no passo e motivo, limiares interpolados — e a carga final é a da regra; 2026-10-12). Histórico: v5 = status de **esforço** + reps contra a faixa, regra de dor muscular alta com o limiar do motor de recuperação, sem copiar a linha de status; v4 = categorias = ações do motor (RPE 10 Lote 6; regressão no caso `dor_muscular_alta_com_nota`: HOLD com dor 9/10) | 10 (v6: +`reps_acima_do_limite_rpe_10` e `falha_antes_de_6_reps`, séries reais do DEV pelo coletor; expectativas pela direção da regra; critério novo `DIRECAO_DIFERENTE_DO_MOTOR`: categoria/carga da IA ≠ direção/carga da regra) |
| `exercise_summary` | `exercise-summary-v3` (`exercise_summary_ai.py`; v3 = séries rotuladas pelo nome do catálogo `advanced_techniques` (`set_labels.set_display_label`, pelo `technique_code`/`anchor_set_number` lidos em `services.exercise_summary_context`) e aquecimento/ajuste com rótulo legível, no lugar do `set_type` cru; M95b, 2026-10-26) | 6 |
| `workout_summary` | `workout-summary-v5` (`workout_summary_ai.py`) | 6 |
| `insights` | `insights-prompt-v6` (`insights_prompt.py`) | 5 (+8 sintéticos em `questions.json`) |
| `workouts_analysis` | `workouts-analysis-v5` (`report_ai.py`) | 5 |
| `cardio_agent` | `cardio-agent-v4` (`cardio_agent_ai.py`; v4 = decisões do usuário de 27/10/2026, interpoladas de `cardio_envelope`: HIIT vetado só na véspera do dia de perna (no próprio dia, permitido depois da força) e cortes do TSB -25/-35; mesmo schema e contexto; snapshot regerado (2 linhas do prompt + o envelope do contexto); eval pago v3 × v4 não rodado no PR, ver `FONTES_DA_VERDADE.md` §69.8. v3 = saída JSON strict (`cardio_recommendation_v3`), validada por `cardio_prescription` e pelo envelope de `cardio_envelope` depois do LLM; contexto compacto v3; RAG `CARDIO.md`; M98 C3a, 2026-10-26. Critério novo: sessão rejeitada pelo dono ou ajustada pelo envelope conta como problema. 2026-10-27: o padrão do HIIT no prompt passou a sair com as séries (`default_prescription`; antes `describe(DEFAULTS[HIIT])` lia o item como Normal), sem subir a versão porque a v3 não tinha chegado à produção. Eval pago v2 × v3 em 2026-10-27, com a correção: 5/5 e 6/6, ver §2.1) | 6 (v3: os 5 da v2 convertidos para o contexto v3 + `tsb_baixo_hiit_vetado`) |
| `periodization` | `periodization-v14` (`periodization/core.py`; v14 = na seção CARDIO NO MODELO, HIIT vetado só na véspera do dia de perna (decisão de 27/10/2026); sem pedido de cardio, prompt igual ao da v13/v12 (`prompt_sha256` igual; só a `prompt_cache_key` muda); eval pago v13 × v14 não rodado no PR. v13 = cardio no Modelo quando o pedido tem cardio — `include_cardio` ou preferências que falam de cardio —: seção CARDIO NO MODELO com os números dos donos, recorte compacto de `CARDIO.md` e `cardio_items` por slot no schema; sem pedido, prompt e schema da v12 letra por letra. M98 C5, 2026-10-27. Critério novo `_avaliar_cardio_da_periodizacao`: cardio sem pedido, cardio ausente/presente contra `expect.cardio`, prescrição inválida pelo dono, mais de uma sessão por slot, HIIT vetado pelo envelope do Modelo, descanso com cardio. Eval pago v12 × v13 em 2026-10-27: mesmos 2/6 nos casos comuns, 2/4 nos de cardio, ver §2.1) | 10 (6 de antes + 4 de cardio derivados deles: `cardio_pedido_modo_b_4_dias`, `cardio_proibido_pelo_usuario`, `cardio_hiit_vespera_de_perna`, `cardio_no_dia_de_descanso`) |
| `exercise_description` | `exec-desc-v1` (`exercise_description_ai.py`) | 6 |
| `meal_parse` | `nutrition-parse-v1` (`nutrition_meal_parse_ai.py`; nutrição L5b, 2026-10-18: leitura da refeição em itens, saída estruturada strict; o eval mede o RESULTADO do worker — IA + motor `meal_parse` + TACO real em memória, `tests/support/meal_parse_eval.py`) | 9 (escritos à mão, sem dado de usuário; critério `avaliar_parse_refeicao`: item esperado presente, proposta aceita — pré-seleção ou escolha com o alimento certo da TACO, ou estimativa na faixa —, quantidade na faixa, nenhum item a mais, nenhuma pré-seleção errada) |

A versão vai **só** no evento `llm.usage` (campo `prompt_version`), nunca no corpo enviado à OpenAI. A exceção é o insights, cujo texto já citava a versão antes do IA-11.

Desde o PR `feat/llm-cache-key-e-teto-saida`, o cliente único (`api/app/openai_client.py`) acrescenta ao corpo dois parâmetros do endpoint Responses, decididos pela `feature`:

- `max_output_tokens`: teto de saída por feature (`LLM_MAX_OUTPUT_TOKENS`). O critério é o maior entre o piso `LLM_OUTPUT_CAP_FLOOR_TOKENS` e `LLM_OUTPUT_CAP_HEADROOM_FACTOR` × a maior saída observada (`LLM_OUTPUT_OBSERVED_MAX_TOKENS`). O teto conta também os tokens de raciocínio. Resposta com `status: "incomplete"` vira erro (evento `llm.output_incomplete` + `llm.usage` com `status: "incomplete"`), sem retry e sem gravar o texto cortado. `workout_plan` (legado) segue sem teto.
- `prompt_cache_key`: `<feature>:<PROMPT_VERSION>`, só nas features com prefixo cacheável (`insights`, `workouts_analysis`, `periodization`). A chave muda junto com a versão do prompt e nunca leva usuário.

Desde o IA-17, toda chamada de texto também leva `store: false` (`LLM_STORE_RESPONSES`): nenhuma feature reabre resposta pela API, então ela não fica guardada na OpenAI. Política em `docs/ia/RETENCAO_DADOS_OPENAI.md`.

Os três aparecem no `index.json` do snapshot (`max_output_tokens`, `prompt_cache_key`, `store`). Mudar um teto não muda o prompt (`prompt_sha256` e `.txt` iguais), só o `body_sha256`.

Falhas da OpenAI (SEG-12): a exceção (`OpenAIGenerationError`) leva ao usuário só uma das mensagens genéricas `USER_MESSAGE_*` (indisponível, tempo esgotado, resposta inválida, limite), que é o que chega ao `detail` dos 502/503, ao `error` dos jobs e ao `error_message` dos cards. O diagnóstico (status, corpo de erro truncado em `DIAGNOSTIC_MAX_CHARS`, `request_id`, motivo do corte/recusa) fica em `exc.diagnostic` e sai no evento `llm.error` (uma linha por tentativa, com `will_retry`), com chave e `Bearer` mascarados.

## 1. Snapshot (CI)

```bash
cd api && python -m pytest tests/unit/test_llm_prompt_renderizado_snapshot.py tests/unit/test_llm_eval_criterios.py
```

Para cada caso, o teste compara o corpo HTTP que iria para a OpenAI com:

- `api/privado/tests/fixtures/llm_prompts/<feature>/<caso>.txt` (repositório privado desde o M103 P1): o **esqueleto** do prompt. Na falha, o log do CI público não mostra o texto; o diff sai localmente com `LLM_PROMPT_SNAPSHOT_DIFF=1`. É o texto inteiro, com cada bloco JSON de topo e cada linha com mais de 2000 caracteres trocados por `<<JSON n linhas, c chars, sha256 …>>`. O diff deste arquivo no PR mostra uma seção mudando de lugar, uma regra mudando de texto ou um número aparecendo;
- `api/tests/unit/fixtures/llm_prompts/index.json`: sha256 do prompt **completo** e do corpo inteiro (modelo e `text.format`), mais `prompt_chars`.

O RAG entra como marcador fixo. Ele é conteúdo versionado em `rag/markdown` e o recorte é medido à parte (`rag_chars`). O snapshot trava o template e os dados.

Para ver os prompts completos de um caso e comparar os blocos JSON, use `LLM_PROMPT_SNAPSHOT_DUMP=<pasta>`.

## 2. Eval pago (manual)

```bash
cd api
OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py
OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py -k set_evaluation
```

**Windows:** rode com `PYTHONIOENCODING=utf-8` (ou `PYTHONUTF8=1`). No eval do #442, sem isso, o `print` da resposta quebrou com `UnicodeEncodeError` (caractere "→") em 4 de 5 casos da análise e o texto se perdeu, embora tokens e custo tenham sido cobrados. O teste também força UTF-8 no `stdout` antes de imprimir, mas a variável continua sendo o caminho garantido (vale para o log do pytest e para redirecionamento `> arquivo`):

```bash
cd api
PYTHONIOENCODING=utf-8 OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py -k insights > depois.txt 2>&1
```

### Braço Anthropic (Claude) e registro de uso

O mesmo eval roda no Claude pelo cliente único (`app.llm_anthropic`), com os mesmos casos e critérios. `LLM_EVAL_PROVIDER`, `LLM_EVAL_MODEL` e `LLM_EVAL_EFFORT` viram, só durante o teste, as envs de produção da feature (`LLM_PROVIDER_<FEATURE>`, `ANTHROPIC_MODEL_<FEATURE>`, `ANTHROPIC_EFFORT_<FEATURE>`; `tests/support/llm_eval_provider.py`). Sem `LLM_EVAL_MODEL`, vale o padrão do PR #11: Haiku 5.5 com esforço `low` em `set_evaluation`, `exercise_summary` e `workout_summary`, e Sonnet 5.5 com esforço `medium` nas demais (`DEFAULT_ANTHROPIC_MODEL`/`DEFAULT_ANTHROPIC_EFFORT`).

```bash
cd api
# Claude, modelo padrão de cada feature (texto + rótulo):
LLM_EVAL_PROVIDER=anthropic ANTHROPIC_API_KEY=... LLM_EVAL_USAGE_JSONL=uso_claude.jsonl PYTHONUTF8=1 \
  python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py tests/eval/test_nutrition_label_eval.py
# Haiku numa feature que por padrão é Sonnet:
LLM_EVAL_PROVIDER=anthropic LLM_EVAL_MODEL=claude-haiku-5-5 ANTHROPIC_API_KEY=... LLM_EVAL_USAGE_JSONL=uso_haiku.jsonl \
  python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py -k periodization
# OpenAI (linha de base), com o mesmo registro:
OPENAI_API_KEY=... LLM_EVAL_USAGE_JSONL=uso_openai.jsonl python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py
# Resumo por feature e modelo (aprovação, erros, p50/p95, tokens, custo):
python -m tests.eval.resumo_uso_eval uso_openai.jsonl uso_claude.jsonl uso_haiku.jsonl
```

- `LLM_EVAL_USAGE_JSONL` (`tests/eval/conftest.py`): grava cada `llm.usage`, `llm.error` e `llm.output_incomplete` do caso, com o `nodeid`, e uma linha `eval.case` com o resultado. Só metadados: nunca prompt, resposta ou chave. Sem a env, nada muda.
- `resumo_uso_eval` usa o preço do dono (`app.domain.nutrition.ai_usage.estimate_cost_usd`). A escrita no cache da Anthropic entra a preço de entrada comum (a tabela do dono não tem o adicional de escrita), então o custo das features com `cache_control` é um piso.
- As chaves vêm do Secrets Manager (`musclefit/claude-api-key`, `musclefit/openai-api-key`), lidas direto para a env do processo do pytest; nunca impressas nem gravadas.
- O tokenizador do Claude conta de 1,35× a 1,8× mais tokens de entrada que o da OpenAI para o mesmo prompt em português (medido no eval de 2026-10-30; menor nos prompts longos de JSON, maior nos curtos em prosa). Estime o custo do braço Claude com esse fator.
- Resultado comparativo de 2026-10-30: `docs/ia/EVAL_ANTHROPIC_2026-10-30.md`.

Sem `OPENAI_API_KEY`, todo caso dá skip. Os critérios ficam em `api/tests/eval/criterios_llm.py` e são objetivos: categoria aceita ou proibida, primeira linha igual à categoria, JSON de sugestão presente, RPE dentro do teto do par, carga dentro da decisão do motor, número de frases, ausência de markdown, seções `##` obrigatórias, número decimal que não existe na entrada (lido em pt-BR: "26,6", "13.894", "41.112,5"; floats longos citados inteiros também casam), regras duras da periodização (slots, sequência, priorizados, trio do catálogo) e descrição sem séries, reps, carga ou RPE. O eval imprime a resposta de cada caso para revisão humana lado a lado.

### Custo estimado por rodada completa (50 casos)

Preços do relatório de 2026-10-08 (gpt-5.4-mini: US$ 0,75 por 1M de tokens de entrada e US$ 4,50 por 1M de saída). Tokens estimados como caracteres ÷ 4, medidos nos casos congelados **com o RAG real**. A saída é a estimada no relatório. A estimativa não inclui cache nem tokens de raciocínio.

| Feature | Prompt (chars/caso) | Entrada (tokens/rodada) | Saída (tokens/rodada) | US$/rodada |
|---|---|---|---|---|
| set_evaluation | ≈ 11,7 mil | ≈ 23 mil | ≈ 2,4 mil | ≈ 0,03 |
| exercise_summary | ≈ 1,5 mil | ≈ 2 mil | ≈ 1,5 mil | ≈ 0,01 |
| workout_summary | 2,5–3,7 mil | ≈ 5 mil | ≈ 2,4 mil | ≈ 0,01 |
| insights | ≈ 445 mil | ≈ 555 mil | ≈ 4 mil | ≈ 0,43 |
| workouts_analysis | 738–908 mil | ≈ 1,05 milhão | ≈ 7,5 mil | ≈ 0,82 |
| cardio_agent | 31–56 mil (v3; v2: 268–293 mil) | ≈ 60 mil | ≈ 6 mil | ≈ 0,07 |
| periodization | 37–110 mil (v13: 10 casos; os 4 de cardio têm 42–82 mil) | ≈ 176 mil | ≈ 67 mil | ≈ 0,43 |
| exercise_description | ≈ 1,5 mil | ≈ 2 mil | ≈ 1,8 mil | ≈ 0,01 |
| **Total** | | ≈ 2,16 milhões | ≈ 91 mil | **≈ US$ 2,03** |

A leitura da refeição (`meal_parse`, L5b) é **medida**, não estimada: ≈ 10,7 mil tokens de entrada e ≈ 2,3 mil de saída por rodada (9 casos), US$ 0,018; latência de 0,9 a 4,5 s por caso; maior saída 426 tokens (o `LLM_OUTPUT_OBSERVED_MAX_TOKENS`). Resultado de 2026-10-18 (`gpt-5.4-mini-2026-03-17`, duas rodadas seguidas): 9 de 9 casos, 27 de 27 itens com proposta aceita, 23 casados com a TACO, 0 pré-seleção errada. Limiar: todos os casos sem problema e nenhuma pré-seleção errada (`resumo_parse_refeicao` dá o agregado).

Uma comparação antes/depois custa duas rodadas, ≈ US$ 4,06 (só a periodização, `-k periodization`: ≈ US$ 0,86). Uma feature barata sozinha (`-k set_evaluation`) custa centavos. Insights, análise e cardio respondem por mais de 80% do custo, então rode só a feature que mudou.

### 2.1 Resultado de 2026-10-27 (M98: `cardio-agent` v2 × v3, `periodization` v12 × v13)

Medido no `llm.usage` de cada chamada (`gpt-5.4-mini-2026-03-17`, RAG real). Custo com US$ 0,75/1M de entrada, 0,075/1M de entrada em cache e 4,50/1M de saída; sem o desconto do cache seria US$ 0,88. A base de cada comparação é o código do commit anterior à versão (`8a2adab5^` para a v2 e `14713280^` para a v12, extraídos com `git archive` para uma pasta temporária), com os casos daquele commit. A v3 roda com a correção do padrão do HIIT.

| Rodada | Casos aprovados | Entrada (tokens) | Em cache | Saída | US$ |
|---|---|---|---|---|---|
| cardio_agent v2 | 5/5 | 440.375 (84–94 mil/caso) | 89.856 | 3.903 | 0,287 |
| cardio_agent v3 | 6/6 | 78.976 (9,5–19,4 mil/caso) | 21.120 | 5.695 | 0,071 |
| periodization v12 | 2/6 | 127.565 | 0 | 18.353 | 0,178 |
| periodization v13 | 4/10 (2/6 comuns + 2/4 de cardio) | 198.611 | 17.920 | 26.943 | 0,258 |
| **Total** | | 845.527 | 128.896 | 54.894 | **0,794** |

**cardio_agent:** nenhum problema nas duas versões. A v3 gasta ≈ 82% menos entrada por caso, e a saída sobe ≈ 30% (JSON estruturado). Os casos não pediram HIIT: as sessões propostas foram contínuas em Z2 (20–30 min). Nos dias de véspera de perna, o texto cita o veto do envelope.

**periodization, 6 casos comuns:** na v12 e na v13 a entrada é igual token a token em todos os casos (sem cardio pedido, prompt e schema são os da v12). A diferença entre as rodadas é variação da LLM, não regressão. Por caso, v12 → v13:

| Caso | v12 | v13 |
|---|---|---|
| `modo_b_6_dias_abc` | 3 (trio fora do catálogo ×3) | 5 (4 priorizados ×3, trio ×2) |
| `modo_b_6_dias_abc_ponderado` | 3 (trio ×3) | 4 (sequência 1..7 em vez de 1..6, trio ×3) |
| `modo_b_4_dias_instrucoes` | 2 (sequência 1..5 em vez de 1..4, trio ×1) | 2 (trio ×2) |
| `modo_b_5_dias_sem_perfil` | 3 (trio ×3) | 2 (4 priorizados ×2) |
| `modo_a_5_dias` | 0 | 0 |
| `modo_a_3_dias_sem_perfil` | 0 | 0 |

Os problemas de força nos dois lados vêm da saída crua da LLM, antes dos auto-fixes do validador: trio fora do catálogo, priorizados acima do teto e slot a mais.

**periodization, 4 casos de cardio (só v13):**

| Caso | Problemas |
|---|---|
| `cardio_pedido_modo_b_4_dias` | 1: trio fora do catálogo (força). O cardio veio como pedido. |
| `cardio_proibido_pelo_usuario` | 0: nenhum cardio. |
| `cardio_hiit_vespera_de_perna` | 1: HIIT na véspera de perna (slot 3, `eve_of_leg_day`) na saída crua. O validador (`apply_template_envelope`) remove a sessão e registra `CARDIO_REMOVED`, então o Modelo salvo não leva esse HIIT. |
| `cardio_no_dia_de_descanso` | 0 |

## 3. Mudar um prompt de propósito

1. Antes de mexer, rode o eval da feature e guarde a saída (`-s … > antes.txt`).
2. Mude o prompt **no módulo dono**. Se a mudança envolver regra ou número de domínio, ele vem do motor dono e é interpolado; ver `docs/motores/FONTES_DA_VERDADE.md` e a guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`.
3. Suba o `PROMPT_VERSION` da feature (tabela acima). No insights, atualize também `prompt_version` em `api/tests/eval/questions.json`.
4. Regere o snapshot e revise o diff dos `.txt` no PR:
   ```bash
   cd api && LLM_PROMPT_SNAPSHOT_WRITE=1 python -m pytest tests/unit/test_llm_prompt_renderizado_snapshot.py
   ```
5. Rode o eval de novo (`> depois.txt`) e compare a taxa de aprovação e as respostas lado a lado. O resultado entra no PR, em "Impacto em produção".

Um PR que **não** pretende mudar o prompt (refatoração, cliente, log) não regenera nada: o snapshot verde é a prova.

Para acrescentar ou trocar casos, edite as listas no topo de `coletar_casos_llm.py`, rode o coletor contra o DEV (comando no docstring dele, com as credenciais do `.env` no ambiente) e regere o snapshot. Cada feature deve ter de 5 a 10 casos, e o teste de snapshot confere isso.

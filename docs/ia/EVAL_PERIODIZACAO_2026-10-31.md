# Eval da periodização: timeout, adaptador de cardio e Haiku 5.5 (2026-10-31)

Continuação de `docs/ia/EVAL_ANTHROPIC_2026-10-30.md` (pendências 2 e 3 e a linha da periodização na recomendação). Só a periodização, com os 10 casos congelados de `api/tests/eval/casos/periodization.json`, o RAG real de `api/privado` e os critérios de `api/tests/eval/criterios_llm.py`, sem nenhuma mudança neles. Cada braço passou pelo cliente único de produção (`run_periodization_draft` → `call_openai_structured` → `llm_anthropic`), com o provedor ligado só durante o teste (`tests/support/llm_eval_provider.py`). O código avaliado já é o deste PR: teto de 150 s (`core.LLM_TIMEOUT_SECONDS`) e adaptador com `output_schema.ANTHROPIC_OPTIONAL_FIRST`.

## O que mudou antes do eval

1. **Teto da chamada: 90 s → 150 s.** `core.LLM_TIMEOUT_SECONDS` = timeout da `musclefit-plan-generator` (`async_jobs.LAMBDA_TIMEOUT_SECONDS`, 180 s, paridade com o Terraform) − `LAMBDA_RESERVED_SECONDS` (30 s). Se o contexto demorar (retomada do Aurora), `llm_timeout_for` encolhe a chamada para ainda sobrar `POST_LLM_RESERVED_SECONDS` (15 s) para validar e gravar o job.
2. **Adaptador de schema.** Com cardio, o schema tem 30 anuláveis e o limite da Anthropic é 16 uniões: 14 viram opcionais. Antes, pela ordem do schema, entravam os 9 de `cardio_items`, inclusive os que o dono `cardio_prescription` exige por modo (duração da sessão contínua, séries do HIIT). Agora a feature indica a ordem (`optional_first`): 11 campos do item de força que costumam vir nulos (técnica, aquecimento, série de ajuste, pico, descanso), os 2 de cardio em que "ausente" = 0 para o dono (`ZERO_WHEN_MISSING_FIELDS`: aquecimento e desaquecimento) e a descrição do Modelo. Os 6 campos de `REQUIRED_FIELDS` e a zona-alvo seguem anuláveis obrigatórios. O schema sem cardio sai idêntico ao de antes, e o da OpenAI não muda.

## Braços

| Braço | Provedor e modelo | Esforço | Rodadas |
|---|---|---|---|
| A | OpenAI `gpt-5.4-mini-2026-03-17` (produção) | (sem raciocínio) | 2 × 10 |
| E' | `claude-haiku-5-5` | `low` | 3 × 10 |
| C' | `claude-haiku-5-5` | `medium` | 2 × 10 |

Prompt `periodization-v14` em todos. Não houve versão nova (ver "Prompt").

## Resultado

Aprovação = casos sem nenhum problema nos critérios. Latência = `latency_ms` do `llm.usage`, com p95 pelo posto mais próximo. Custo pela tabela do dono (`app.domain.nutrition.ai_usage`); sem `cache_control`, como em produção.

| Braço | Aprovação | Timeouts | Saída cortada (`max_tokens`) | p50 / p95 (s) | 6 dias (s, todas as rodadas) | US$/geração |
|---|---|---|---|---|---|---|
| A gpt-5.4-mini | 8/20 (40%) | 0 | 0 | 7,5 / 13,4 | 10,0 · 11,0 · 13,4 · 19,2 | 0,0204 |
| E' Haiku `low` | **26/30 (87%)** | **0** | 2 (ambos de 6 dias) | 24,6 / **125,3** | 73,5 · 73,5 · 103,9 · 119,4 · 125,3 · 141,0 | 0,0100 |
| C' Haiku `medium` | 15/20 (75%) | 0 | 3 (dois de 6 dias, um de 5) | 26,1 / 128,6 | 89,6 · 108,9 · 128,6 · 137,7 | 0,0103 |

Falhas por critério (problema × ocorrências):
- A: trio fora do catálogo ×18 (em 12 casos), 4 priorizados ×3, HIIT na véspera de perna ×2. É o padrão conhecido do eval de 30/10.
- E': trio fora do catálogo ×7 (2 casos, os dois `modo_b_6_dias_abc_ponderado`). Mais 2 casos sem saída: a resposta bateu no teto de 30.720 tokens (`LLM_MAX_OUTPUT_TOKENS["periodization"]`, que conta raciocínio) e foi descartada.
- C': faixa percentual 95–105 ×10 (1 caso), HIIT na véspera de perna ×1. Mais 3 respostas cortadas.

Tokens de saída por geração (inclui raciocínio): A p50 2,9 mil e máx. 4,9 mil. E' p50 5,7 mil, mas os Modelos de 6 dias gastam de 18 a 30 mil. C' p50 7,8 mil.

**Adaptador de cardio resolvido.** Nos 4 casos de cardio, em 5 rodadas Claude (20 gerações), nenhuma omissão de duração ou de séries do HIIT. No eval de 30/10 foram 2 em 4 rodadas, com os campos convertidos pela ordem do schema.

**Timeout resolvido, latência não.** Com 150 s não houve timeout. Antes, com 90 s, houve 2 timeouts no Haiku `low` e 5 no `medium`. Mas os Modelos de 6 dias levam de 74 a 141 s no Haiku `low`, e 2 de 6 estouraram o teto de saída.

## Decisão: a periodização segue na OpenAI

Critério do pedido: ligar só se o Haiku aprovar mais que a OpenAI, sem timeout, e com p95 abaixo de 60% do novo teto (90 s de 150 s).

- Aprovação: passou (87% contra 40%).
- Timeout: passou (0).
- p95: **não passou**. Foi 125,3 s no geral; nos casos de 6 dias, a mediana é ~112 s e o máximo 141 s. Fica a 9 s do teto.
- Além disso, 2 de 6 gerações de 6 dias não tiveram saída (`max_tokens`): é um Modelo que não sai, e o fallback não refaz saída cortada (já cobrada).

Subir mais o teto não resolve sem subir o timeout da Lambda, e o problema de fundo é o raciocínio de 18 a 30 mil tokens nos Modelos grandes, mesmo em `low`. `infra/llm_providers.tf` não muda.

## Prompt

Sem `periodization-v15`. As duas sugestões de 30/10 (faixa percentual até 100 e sessão contínua sempre com duração) não aparecem nas falhas do braço candidato (E'). A duração ausente sumiu com o adaptador, e a faixa 95–105 apareceu uma vez, só no `medium`. As falhas da OpenAI são outras (trio fora do catálogo). Mudar o prompt agora não mudaria a decisão.

## Custo do eval

| Braço | US$ medido (`llm.usage`) |
|---|---|
| A (20 gerações) | 0,41 |
| E' (30) | 0,30 |
| C' (20) | 0,21 |
| **Total** | **≈ 0,91** (orçamento: US$ 8) |

Sem timeout, toda chamada devolveu `usage`, inclusive as cortadas.

## Pendências

1. ~~**Raciocínio nos Modelos grandes.** Testar o Haiku 5.5 sem raciocínio.~~ Feito na mesma data (seção abaixo): sem raciocínio, a aprovação cai para o nível da OpenAI.
2. Reavaliar a virada quando o p95 dos casos de 6 dias ficar abaixo de 60% do teto.
3. `modo_b_6_dias_abc_ponderado` segue o caso mais difícil nos dois provedores (trio fora do catálogo).

## Rodada 2: Haiku 5.5 sem raciocínio (2026-10-31)

**Capacidade nova:** `ANTHROPIC_THINKING_<FEATURE>` (`adaptive`, o padrão, ou `disabled`), lida por `llm_anthropic.thinking_config`, e o mapa Terraform `anthropic_thinking_by_feature` (padrão `{}`). A API só aceita `disabled` com esforço em `EFFORTS_WITHOUT_THINKING` (`low`/`medium`/`high`). Com `xhigh`/`max`, a chamada falha antes da rede com `ANTHROPIC_CONFIG_INVALID`, que é elegível ao fallback. Sem env nova, o corpo das 6 features em produção é idêntico ao de antes (snapshot em `test_llm_thinking_por_feature.py`). No eval, a env é `LLM_EVAL_THINKING`.

Mesmos 10 casos, critérios, prompt (`periodization-v14`), teto de 150 s e `max_tokens` de 30.720. Todos os braços usaram `claude-haiku-5-5` com `thinking: disabled`, 5 rodadas × 10 casos cada. A OpenAI não foi repetida: a linha de base é o braço A acima. A primeira rodada do `high` rodou sozinha. As outras 14 rodaram com os 3 braços em paralelo (no máximo 3 chamadas simultâneas).

| Braço | Aprovação | Saída cortada | Timeouts | p50 / p95 (s) | 6 dias (s, 10 gerações) | Saída p50 / máx. (tokens) | US$/geração |
|---|---|---|---|---|---|---|---|
| A gpt-5.4-mini (linha de base) | 8/20 (40%) | 0 | 0 | 7,5 / 13,4 | 10,0–19,2 | 2,9 mil / 4,9 mil | 0,0204 |
| E' Haiku `low` com raciocínio (rodada 1) | 26/30 (87%) | 2 | 0 | 24,6 / 125,3 | 73,5–141,0 | 5,7 mil / ~30 mil | 0,0100 |
| (a) Haiku `high`, sem raciocínio | 25/50 (50%) | 0 | 0 | 13,8 / 36,8 | 16,1–45,3 (mediana 25,0) | 4,6 mil / 13,8 mil | 0,0062 |
| (b) Haiku `medium`, sem raciocínio | 25/50 (50%) | 0 | 0 | 13,1 / 29,5 | 17,7–39,0 (mediana 27,1) | 4,2 mil / 13,9 mil | 0,0060 |
| (c) Haiku `low`, sem raciocínio | 27/50 (54%) | 0 | 0 | 12,1 / 30,2 | 17,6–41,2 (mediana 25,7) | 4,0 mil / 11,3 mil | 0,0059 |

Falhas por critério. O teste para no primeiro problema de cada caso, então a contagem é de casos reprovados pelo primeiro critério:
- (a): trio fora do catálogo ×18, `rest_seconds` preenchido com regra nula ×7.
- (b): trio fora do catálogo ×19, `rest_seconds` preenchido ×5, 4 priorizados (máx. 3) ×1.
- (c): trio fora do catálogo ×18, `rest_seconds` preenchido ×4, sequência incompleta ×1.

Por caso, nos 3 braços (15 gerações cada), as reprovações se concentram em 5 casos: `modo_b_6_dias_abc_ponderado` 15/15, `modo_b_4_dias_instrucoes` 15/15, `cardio_pedido_modo_b_4_dias` 15/15, `modo_b_6_dias_abc` 14/15 e `modo_b_5_dias_sem_perfil` 14/15. Os outros 5 casos passaram em todas as gerações.

**Leitura.** Desligar o raciocínio resolveu a latência e o teto. O p95 caiu de 125 s para 30–37 s, nenhum caso de 6 dias passou de 46 s e não houve saída cortada (máximo de 14 mil tokens contra 30.720). Também ficou 40% mais barato. A qualidade, porém, voltou ao nível da OpenAI: sem raciocínio, o Haiku volta a montar trios (grupo, exercício, equipamento) fora do catálogo do RAG e a preencher `rest_seconds` onde a regra pede nulo. Ao que tudo indica, é o raciocínio que fazia a checagem contra o catálogo. O esforço quase não muda o resultado com o raciocínio desligado (50–54%).

### Decisão: a periodização segue na OpenAI

Critério do pedido: aprovação ≥ 80% e acima da OpenAI, zero saída cortada, zero timeout e p95 ≤ 90 s.

| Braço | Aprovação ≥ 80% | Cortada = 0 | Timeout = 0 | p95 ≤ 90 s |
|---|---|---|---|---|
| (a) `high` | não (50%) | sim | sim | sim (36,8 s) |
| (b) `medium` | não (50%) | sim | sim | sim (29,5 s) |
| (c) `low` | não (54%) | sim | sim | sim (30,2 s) |

Nenhum braço cumpre tudo, e `infra/llm_providers.tf` não liga a periodização. Fica só a capacidade de configurar o raciocínio por feature: `anthropic_thinking_by_feature = {}`, sem nenhuma env nova publicada.

**Custo da rodada 2:** US$ 0,906 medidos (`llm.usage` pela tabela do dono): (a) 0,309, (b) 0,302 e (c) 0,294, para 150 gerações. O orçamento era US$ 3.

### Pendências da rodada 2

1. **Raciocínio só onde importa.** O dilema é qualidade com raciocínio contra latência e corte sem ele. Os caminhos possíveis são:
   - raciocínio `adaptive` em `low` com `LLM_MAX_OUTPUT_TOKENS["periodization"]` maior, o que resolve o corte mas não a latência;
   - deixar a checagem do trio no motor e pedir ao modelo só a escolha, com o catálogo validado depois (é domínio de motor: a regra fica no dono, não no prompt).
2. A falha dominante sem raciocínio (trio fora do catálogo) é a mesma da OpenAI. Um `periodization-v15` focado em trio e `rest_seconds` poderia beneficiar os dois provedores, mas exige outro eval.

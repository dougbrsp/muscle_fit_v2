# Eval comparativo GPT-5.4-mini × Claude Sonnet/Haiku 5.5 (2026-10-30)

Eval pago das features de texto e do rótulo, com os casos congelados de `api/tests/eval/casos/` (sintéticos, M103 L2), o RAG real de `api/privado` e os critérios de `api/tests/eval/criterios_llm.py`, sem nenhuma mudança neles. Cada braço passou pelo cliente único de produção (`app.openai_client` → `app.llm_anthropic`), com o provedor ligado por feature só durante o teste (`tests/support/llm_eval_provider.py`). Como rodar: `docs/ia/EVALS.md`, seção "Braço Anthropic".

Nenhuma feature foi ligada na Anthropic. A virada fica para outro PR, com aval do usuário.

## Braços

| Braço | Provedor e modelo | Esforço | Features |
|---|---|---|---|
| A | OpenAI `gpt-5.4-mini-2026-03-17` (produção) | (padrão da OpenAI, sem raciocínio) | todas |
| B | padrão do PR #11: `claude-haiku-5-5` nas leves, `claude-sonnet-5-5` nas demais | `low` nas leves, `medium` nas demais | todas |
| C | `claude-haiku-5-5` onde B é Sonnet | `medium` (padrão da feature) | as de Sonnet |
| D | `claude-sonnet-5-5` | `low` | periodization |
| E | `claude-haiku-5-5` | `low` | periodization, workouts_analysis |
| F | `claude-haiku-5-5` | `medium` | set_evaluation, exercise_summary |

D, E e F não estavam no plano. Rodaram porque sobrou orçamento e porque B e C deram timeout na periodização e reprovaram nas features leves: a pergunta passou a ser se outro esforço resolvia.

## Resultado por feature

Aprovação = casos sem nenhum problema nos critérios. Latência = `latency_ms` do `llm.usage` (p95 pelo posto mais próximo; com 5 a 10 casos, o p95 é o pior ou o segundo pior caso). Custo por chamada = média do braço com a tabela de preços do dono (`app.domain.nutrition.ai_usage`). Custo mensal = custo por chamada × volume real de produção de 17 a 30/10 (14 dias) × 30/14. Volume de 14 dias: set_evaluation 277, exercise_summary 110, workout_summary 11, e 1 chamada cada para cardio_agent, periodization, insights e exercise_description. workouts_analysis, meal_parse e label_photo não tiveram chamada no período ("—").

| Feature | Braço | Aprovação | Falhas (critério × casos) | p50 / p95 (s) | US$/chamada | US$/mês |
|---|---|---|---|---|---|---|
| **periodization** | A gpt-5.4-mini | 4/10 | trio fora do catálogo ×5 casos; 4 priorizados ×1; HIIT na véspera de perna ×1 | 9,0 / 13,3 | 0,0258 | 0,055 |
| | B Sonnet `medium` | 6/10 | **timeout de 90 s ×2** (os de 6 dias); faixa % 95–105 ×1; cardio sem duração ×1 | 38,2 / 90,1 | 0,1005 | 0,215 |
| | C Haiku `medium` | 4/10 | **timeout ×5**; cardio sem duração/séries ×1 | 28,0 / 90,1 | 0,0025¹ | 0,005¹ |
| | D Sonnet `low` | 8/10 | timeout ×1; trio fora do catálogo ×1 | 27,4 / 90,0 | 0,1109 | 0,238 |
| | E Haiku `low` | 8/10 | timeout ×2; nenhuma falha de critério nas 8 respondidas | 22,6 / 90,0 | 0,0057 | 0,012 |
| **cardio_agent** | A | 6/6 | — | 4,9 / 6,0 | 0,0118 | 0,025 |
| | B Sonnet | 6/6 | — | 10,0 / 11,9 | 0,0447 | 0,096 |
| | C Haiku | 6/6 | — | 14,8 / 18,1 | 0,0032 | 0,007 |
| **label_photo** (rótulo, visão) | A | 8/8 | — | 1,8 / 4,0 | 0,0032 | — |
| | B Sonnet, antes da correção | 0/8 | **400 do servidor: schema recusado ×8** | — | 0 | — |
| | B Sonnet, com a correção deste PR | 8/8 | — | 3,5 / 19,8 | 0,0128 | — |
| | C Haiku, com a correção | 8/8 | — | 4,8 / 6,9 | 0,0010 | — |
| **set_evaluation** (ao vivo, orçamento de 50 s) | A | 10/10 | — | 1,6 / 2,6 | 0,0019 | 1,14 |
| | B Haiku `low` | 8/10 | nota não usa o status do motor ×2 | 2,8 / 10,7 | 0,0008 | 0,47 |
| | F Haiku `medium` | 8/10 | nota não usa o status do motor ×2 (outros 2 casos) | 3,9 / 12,1 | 0,0009 | 0,56 |
| **insights** | A | 4/5 | dado ausente não admitido ×1 | 2,2 / 2,6 | 0,0079 | 0,017 |
| | B Sonnet | 4/5 | dado ausente não admitido ×1 (o mesmo caso) | 6,5 / 8,5 | 0,0360 | 0,077 |
| | C Haiku | 5/5 | — | 11,0 / 21,2 | 0,0027 | 0,006 |
| **workouts_analysis** (teto de 50 s) | A | 5/5 | — | 16,5 / 20,7 | 0,1035 | — |
| | B Sonnet | 4/5 | número que não está no JSON ×1 | 32,8 / 33,4 | 0,3462 | — |
| | C Haiku `medium` | 3/5 | timeout de 50 s ×1; número que não está no JSON ×1 | 47,2 / 50,0 | 0,0783¹ | — |
| | E Haiku `low` | 5/5 | — | 39,1 / 42,7 | 0,0958 | — |
| **meal_parse** | A | 9/9 | — | 1,7 / 2,3 | 0,0014 | — |
| | B Sonnet | 9/9 | — | 3,0 / 5,9 | 0,0088 | — |
| | C Haiku | 9/9 | — | 2,0 / 6,1 | 0,0005 | — |
| **exercise_description** | A | 6/6 | — | 1,6 / 2,2 | 0,0014 | 0,003 |
| | B Sonnet | 6/6 | — | 4,5 / 5,7 | 0,0060 | 0,013 |
| | C Haiku | 6/6 | — | 4,5 / 6,5 | 0,0004 | 0,001 |
| **exercise_summary** | A | 6/6 | — | 1,1 / 1,4 | 0,0008 | 0,20 |
| | B Haiku `low` | 2/6 (1/6 numa rodada de teste) | repete carga em kg ×4 | 1,9 / 3,8 | 0,0002 | 0,05 |
| | F Haiku `medium` | 3/6 | repete carga em kg ×3 | 3,5 / 5,3 | 0,0004 | 0,10 |
| **workout_summary** | A | 6/6 | — | 1,5 / 1,8 | 0,0018 | 0,042 |
| | B Haiku `low` | 6/6 | — | 3,9 / 5,1 | 0,0005 | 0,012 |
| workout_plan (legado) | — | sem casos no eval | — | — | — | — |

¹ Média só das chamadas que responderam. Chamada com timeout não emite tokens no `llm.usage`, então o custo dela não entra (ver "Custo do eval").

Projeção mensal com o volume medido: OpenAI hoje ≈ US$ 1,5/mês nessas features. Com a recomendação abaixo, ≈ US$ 1,4/mês. O custo não decide nada aqui: o volume é pequeno, e set_evaluation responde por mais de 70% do total.

## Custo do eval

| Provedor | US$ medido (`llm.usage`) | Observação |
|---|---|---|
| OpenAI | 0,97 | braço A completo |
| Anthropic | 5,72 | 5,55 pela tabela do dono + 0,17 do adicional de escrita no cache (25% sobre a entrada, fora da tabela do dono) |
| **Total** | **≈ 6,69** | orçamento: US$ 15 |

Não medido: as 11 chamadas da Anthropic que deram timeout (3 no Sonnet e 8 no Haiku) não devolveram `usage`. Se o servidor terminou de gerar e cobrou, o teto estimado é de ≈ US$ 0,80 (entrada inteira + saída no teto, pelo preço de cada modelo). Com isso, o total fica ≤ US$ 7,5.

O tokenizador do Claude contou de 1,35× a 1,8× os tokens de entrada da OpenAI no mesmo prompt (set_evaluation 1,76×, cardio 1,70×, exercise_summary 1,66×, workout_summary 1,56×, periodization 1,48×, workouts_analysis 1,35×). O preço por token da tabela não mostra esse fator.

## Erros encontrados

1. **Schema do rótulo recusado pelo servidor (bug, corrigido neste PR).** Os 8 casos de `label_photo` deram 400 `invalid_request_error`: `output_config.format.schema: Invalid schema: Enum value 'g' does not match declared type '['string', 'null']'`. O campo `basis` (`{"type": ["string", "null"], "enum": ["g", "ml", null]}`) é válido no strict da OpenAI, mas o servidor da Anthropic recusa `enum` junto de lista de tipos. Correção em `app/llm_anthropic_schema.py` (`_split_typed_enum`): esse formato vira `anyOf` de `{"type": "string", "enum": ["g", "ml"]}` com `{"type": "null"}`. O conjunto de valores é o mesmo, e a contagem de uniões não muda (lista de tipos e `anyOf` contam como uma união cada). O schema da OpenAI não é tocado. Depois da correção, o rótulo passou em 8/8 no Sonnet e 8/8 no Haiku, o que prova que o schema compila no servidor e que o `restore` entrega ao validador determinístico (`label_photo`) a forma original. Testes: `test_schema_adaptado_nao_tem_enum_com_lista_de_tipos` (todos os schemas reais) e `test_basis_do_rotulo_vira_anyof_e_volta_igual`.
2. **Periodização com cardio: o schema adaptado compila e o `restore` funciona**, com uma ressalva de qualidade. Os 4 casos de cardio rodaram nos 4 braços Claude, sem 400 e sem JSON inválido. O `restore` devolveu `None` aos campos convertidos, e o validador do dono os leu como sempre. Ressalva: para caber no limite de 16 uniões, o adaptador converte os 14 últimos anuláveis em opcionais, e isso inclui os 9 campos anuláveis de `cardio_items` (`cardio_duration_minutes`, `hiit_rounds` e outros). Em 2 de 4 rodadas, o Claude omitiu a duração de uma sessão contínua ou as séries de um HIIT, e o dono (`cardio_prescription`) rejeitou o item. O OpenAI, com todo campo obrigatório, não teve essa falha. Não mexi na heurística porque não há prova suficiente (as rodadas `low` passaram nesse caso). Fica como pendência.
3. **Timeout da periodização (90 s, `periodization/core.py`).** Os dois casos de 6 dias (`modo_b_6_dias_abc*`) passam de 90 s no Claude em quase toda configuração. Foram 11 timeouts no total: B ×2, C ×5, D ×1, E ×2 e workouts_analysis no Haiku `medium` ×1 (teto de 50 s). O OpenAI responde em 7 a 13 s porque não gasta raciocínio. O Claude, com thinking adaptativo, gera de 6 a 7 mil tokens de saída por caso.
4. **Nenhum** caso de recusa (`refusal`), saída cortada (`max_tokens`/`incomplete`), erro de billing, 429 ou 5xx.
5. **Contabilidade de custo:**
   - `estimate_cost_usd` não tem o adicional de escrita no cache da Anthropic. O `cost_usd` fica abaixo do real nas features com `cache_control`: aqui, US$ 0,17 em US$ 5,72.
   - A tabela do dono tem a leitura de cache do Sonnet 5.5 a US$ 0,10/1M. A referência de modelos do SDK consultada hoje diz US$ 0,20/1M. Reconferir na página oficial antes da virada (o efeito aqui é de centavos).
6. **Cache em feature de volume baixo.** periodization, insights e workouts_analysis levam `cache_control` no prefixo. No eval, a periodização escreveu de 129 a 155 mil tokens no cache e leu 25 mil. Em produção, com 1 chamada a cada 14 dias, o cache (5 min) nunca é lido: cada chamada paga 25% a mais no prefixo, sem retorno. Sugestão para a virada: tirar o `cache_control` dessas features, ou ligá-lo só acima de um volume.

Critérios: nenhum depende do provedor. Os avaliadores leem a saída da função da feature (texto ou dicionário já validado), nunca `output_text` nem o corpo HTTP, então nenhum foi alterado.

## Recomendação por feature

| Feature | Recomendação | Por quê |
|---|---|---|
| periodization | **Manter OpenAI** por enquanto. Na virada: Haiku `low` com o timeout acima de 90 s, e um novo eval | Na saída crua, o Claude erra menos: Haiku `low` e Sonnet `low` com 8/10, contra 4/10 da OpenAI, que repete o "trio fora do catálogo" conhecido. Mas os casos de 6 dias estouram os 90 s, e um timeout é um Modelo que não sai. Haiku `low` custa ≈ 1/5 da OpenAI por chamada. Subir o teto exige conferir também o timeout da Lambda de IA. |
| cardio_agent | **Ligar com Haiku** (`medium`) | 6/6 nos três braços. Custa 1/4 da OpenAI. Latência de 15–18 s num job assíncrono. |
| label_photo | **Ligar com Haiku**, depois deste PR | 8/8 com a correção do schema. Custa 1/3 da OpenAI. p95 de 6,9 s, dentro do teto de 90 s. Sonnet também deu 8/8, mas custa 13×. |
| set_evaluation | **Manter OpenAI** | O Haiku perde 2 de 10 casos em `low` e em `medium` (a nota não usa o status do motor), e o p95 sobe de 2,6 s para 11–12 s numa tela ao vivo. A economia seria de ≈ US$ 0,7/mês. |
| insights | **Ligar com Haiku** (`medium`) | 5/5, contra 4/5 da OpenAI e do Sonnet. Custa 1/3 da OpenAI. p95 de 21 s. Amostra pequena (5 casos). |
| workouts_analysis | **Manter OpenAI** | O Haiku `low` dá 5/5, mas custa o mesmo (prompt acima de 100 mil tokens cai na faixa longa) e tem p95 de 42,7 s contra o teto de 50 s. O Sonnet errou 1 caso e custa 3,3×. |
| meal_parse | **Ligar com Haiku** (`medium`) | 9/9 em todos. Custa 1/3 da OpenAI. p95 de 6,1 s contra 2,3 s: aceitável para a leitura de refeição. |
| exercise_description | **Ligar com Haiku** (`medium`) | 6/6 em todos. Custa 1/3 da OpenAI. |
| exercise_summary | **Manter OpenAI** | O Haiku repete a carga em kg (critério do prompt "não repetir números crus") em 3 a 5 de 6 casos, em `low` e em `medium`. |
| workout_summary | **Ligar com Haiku** (`low`, padrão) | 6/6 nos dois. Custa 1/3. Volume e economia mínimos: a decisão é de consolidação, não de custo. |
| workout_plan (legado) | **Manter OpenAI** | Sem casos no eval. Não dá para comparar. |

O padrão do PR #11 (Sonnet nas features de raciocínio) não se pagou em nenhuma. Com o esforço certo (`low` na periodização e na análise), o Haiku empatou ou ganhou do Sonnet em todas as features de Sonnet, por 1/13 a 1/20 do preço por chamada (1/3,6 na análise, por causa da faixa de prompt longo). O padrão leve (Haiku `low`) reprovou em set_evaluation e exercise_summary.

## Sugestões de prompt (fora do escopo; só anotadas)

- set_evaluation: exigir que a nota comece pelo status do motor, escrito como vem (`Esforço atingido` etc.), sem paráfrase.
- exercise_summary: repetir a proibição de citar a carga em kg (hoje o Haiku cita "52,5 kg").
- periodization: reforçar `priority_max_pct` ≤ 100 (o Sonnet propôs 95–105) e "sessão contínua sempre com `cardio_duration_minutes`". Este segundo cobre também o efeito dos campos opcionais do adaptador.

## Pendências

- Virada (outro PR, com aval do usuário): ligar cardio_agent, label_photo, insights, meal_parse, exercise_description e workout_summary no Haiku. Para isso, mudar `DEFAULT_ANTHROPIC_MODEL` ou definir as envs por feature no Terraform, e tirar o `cache_control` das features de volume baixo.
- Periodização: subir o timeout de 90 s (e conferir a Lambda), depois refazer o eval com Haiku `low`.
- Adaptador: estudar como evitar que os campos de `cardio_items` virem opcionais (por exemplo, converter primeiro os anuláveis de força, que costumam vir nulos), com um eval antes/depois.
- Custo: incluir o adicional de escrita no cache e reconferir o preço da leitura de cache do Sonnet 5.5 na tabela do dono (`ai_usage`).

**Atualização de 2026-10-31:** as pendências 2 (adaptador de cardio) e 3 (timeout de 90 s) foram tratadas e a periodização foi reavaliada em `docs/ia/EVAL_PERIODIZACAO_2026-10-31.md`. Ela segue na OpenAI: o p95 do Haiku `low` nos Modelos de 6 dias passa de 60% do novo teto, e 2 de 6 gerações bateram no teto de saída.

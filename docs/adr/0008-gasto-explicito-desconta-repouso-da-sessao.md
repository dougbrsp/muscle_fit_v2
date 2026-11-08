# ADR 0008: O gasto explícito/automático desconta o repouso da janela e soma toda atividade do Intervals

- **Status**: Aceita (decisão do usuário, 08/11/2026: "corrija a dupla contagem descontando a TMB da sessão").
- **Data**: 2026-11-08

## Contexto

No modo `explicit_training` (e no `auto_intervals` sincronizado, que vira explícito), `energy_expenditure.tdee` calcula `TMB × fator de NÃO-exercício + kcal do treino`. A kcal do treino entrava inteira: tanto a informada pelo relógio (`activities.calories_kcal`) quanto a estimada por MET (`MET × 3,5 × kg / 200 × min`), e as duas são brutas (incluem o repouso). O fator de rotina já cobre a TMB das 24 h; a TMB dos minutos do treino era contada duas vezes. O motor adaptativo (`activity_energy`, decisão D4) já descontava: `kcal_ativa = max(0, kcal − TMB/1440 × minutos)`, com `unknown` tratada como bruta.

Caso auditado em produção (só leitura, 08/11): plano 10, usuário 1, 07/11: TMB 2.020,24 × 1,3 = 2.626 + 874 (treino de 107,2 min) = 3.500 kcal.

## Decisão

- Regra única em `activity_energy.net_of_resting_kcal` (consumida por `normalize_activity`, do adaptativo, e por `energy_expenditure`): `active` entra como veio; `gross`/`unknown` descontam `resting_kcal = TMB/1440 × minutos`; piso 0.
- `energy_expenditure` aplica a regra à kcal de CADA sessão (`resolve_sessions_kcal`, `resolve_training_kcal`) e de cada atividade do Intervals (`extra_activity_kcal`, que recebe a duração decorrida, com o movimento de reserva). A kcal por MET é sempre bruta e sempre desconta.
- **Toda atividade do Intervals conta** (decisão do usuário, 08/11: "tudo que entra pelo Intervals com o respectivo tipo, exemplo 'Morning Walk', 'Weight Training', deve ser considerado como gasto de atividade"), no explícito e no automático sincronizado, com a mesma regra (`TDEE_MODES_WITH_EXTRA_ACTIVITIES`; o que difere é só o fallback do automático sem sync). O classificador `intervals_activity_kind` só dá o tipo para a tela: `EXTRA_ACTIVITY_KINDS` foi removida e nada filtra por tipo (Walk, VirtualRun, WeightTraining, RockClimbing, Elliptical e tipos desconhecidos somam). Causa do caso 08/11: o plano 10 é explícito e o motor só somava extras no automático (VirtualRun de 2.601 s e 436 kcal ficou de fora).
- **Sem dupla contagem com o treino do app**, na origem e sem regra nova: a lista de atividades do Intervals que o serviço entrega ao motor é a de `intervals_activities` SEM vínculo em `activity_intervals_link`. A atividade vinculada É a sessão de `activities` (cuja kcal já veio dela) e soma uma vez, pela sessão (kcal e duração da sessão). Sessão do app sem atividade correspondente continua contando (informada, senão MET), líquida. Não há deduplicação nova; vale o vínculo existente.
- Atividade do Intervals sem kcal não soma e não é estimada (`extra_activity_without_kcal`); sem duração soma a kcal como veio (`training_rest_not_discounted`).
- Dia só com atividade do Intervals (sem sessão do app): o gasto inclui a atividade, mas `is_training_day` e o tipo de dia do ciclo de carbo NÃO mudam (o ciclo parte da base sem treino).
- Recálculo: o `inputs_hash` já inclui as atividades do contexto do dia; a meta de hoje (não congelada) é recalculada na leitura seguinte à chegada da atividade, sem gatilho novo. O serviço passa a ler as atividades do Intervals também nos planos explícitos (1 SELECT a mais; sem as consultas de conexão e frescor, que são só do automático).
- Modo do relógio no explícito/automático: `DEFAULT_TDEE_CALORIE_MODE = unknown` (o plano só guarda `watch_calorie_mode` no adaptativo).
- Sem duração (ou sem TMB, que já tem a limitação própria): a kcal entra como está, o treino não é descartado e sai a limitação `training_rest_not_discounted` (rótulo em `labels.py`).
- `included_in_factor` e `adaptive_balance` não mudam. `tdee` não muda: recebe a kcal já líquida. `tdee_breakdown` mostra a base, o treino do app líquido ("Treino de força (sem o repouso da sessão)") e UMA parcela por atividade do Intervals, com o nome do Intervals (`activity_<n>`, rótulo dinâmico); a soma das parcelas fecha o total. A bruta e o desconto saem em `training_kcal`/`extra_activity` (`gross_kcal`, `resting_discount_kcal`) só quando houve desconto.
- O diário (`nutrition_days._training`) passa o `bmr_kcal` gravado na meta, para a linha "informado × estimado" mostrar o usado líquido; informada e estimada continuam brutas lado a lado.
- `ENGINE_VERSION` passa a `nutrition-v3`: a materialização regrava as metas NÃO congeladas (D-1, hoje e futuras), como na ADR 0007. Dias congelados não mudam. Sem migração.

## Consequências

- Gasto e meta do dia de treino caem (a meta cai o mesmo que o gasto: o déficit por ritmo é absoluto em kcal). Dia sem treino, embutido, fallback do automático e adaptativo ficam iguais.
- `inputs_hash` muda em todas as metas regravadas (versão do motor entra no hash). Os testes-ouro de `test_nutrition_modos_legados_ouro.py` foram regravados de propósito nos cenários explícito e automático com treino; os demais mantêm os valores de antes, só com hash novo.
- Limitação: correções manuais de atividade (`activity_energy_corrections`) não se aplicam ao gasto explícito/automático.

## Impacto em produção

Só motor, rótulo e leitura das atividades do Intervals nos planos explícitos (SELECT existente, colunas já presentes em `intervals_activities`); sem migração e sem escrita nova. **Medido em produção em 08/11/2026 (só SELECT):** só o plano 10 do usuário 1 (explícito, fator 1,3, corte de 0,7%/semana, ciclo de carbo) é afetado; metas não congeladas (07/11, D-1, 08/11 e futuras; ontem só congela com `TARGET_FREEZE_LAG_DAYS` = 2) são regravadas na primeira leitura após o deploy; 02 a 06/11 estão congelados. Tabela por dia, calculada com o motor puro (`activity_energy`/`energy_expenditure`), "antes" = o que o motor fazia (TMB × 1,3 + kcal bruta da sessão do app, sem as atividades extras no explícito):

| dia | TMB | sessão do app (s / kcal) | repouso da sessão | sessão líquida | atividades do Intervals sem vínculo (s / kcal) | extras líquidos | gasto antes | gasto depois | variação |
|---|---|---|---|---|---|---|---|---|---|
| 02/11 | 2020,24 | 7943 / 763 | 185,7 | 577,3 | VirtualRun 604/36; RockClimbing 1201/206 | 199,8 | 3.389 | 3.403 | +14 |
| 03/11 | 2015,92 | 6357 / 707 | 148,3 | 558,7 | Walk 680/74; Walk 678/56 | 98,3 | 3.328 | 3.278 | −50 |
| 05/11 | 2020,24 | 4859 / 428 | 113,6 | 314,4 | Walk 749/51; VirtualRun 1446/265; Walk 815/79 | 324,6 | 3.054 | 3.265 | +211 |
| 06/11 | 2014,00 | 5304 / 508 | 123,6 | 384,4 | Elliptical 310/25; VirtualRun 1360/233 | 219,1 | 3.126 | 3.222 | +95 |
| 07/11 | 2020,24 | 6433 / 874 | 150,4 | 723,6 | Walk 718/36; Walk 782/73 | 73,9 | 3.500 | 3.424 | −76 |
| 08/11 | 2020,24 | sem sessão | 0 | 0 | VirtualRun 2601/436 | 375,2 | 2.626 | 3.001 | +375 |

A musculação do Intervals de cada dia (02/11 7.812 s 763 kcal, 03/11 6.292 s 707, 05/11 4.946 s 428, 06/11 5.046 s 508, 07/11 6.245 s 874) é a atividade vinculada à sessão do app e entra uma vez, pela sessão. Fator 1,3 (não-exercício). A meta kcal desses dias varia o mesmo valor da coluna "variação" (o déficit por ritmo é absoluto em kcal; o ciclo de carbo redistribui sobre a base sem treino, que não muda). Efeito prático: dias só com cardio e dias de treino com bastante atividade extra passam a ter meta maior; dias em que a sessão pesa mais que os extras (07/11) ficam com meta menor. Só 07/11 e 08/11 são regravados (os demais estão congelados). Rollback: reverter o código; o motor regrava as metas não congeladas.

Limitações e riscos: (1) uma musculação do Intervals AINDA NÃO vinculada à sessão do app no mesmo dia soma além da sessão até o vínculo existir (o vínculo é criado no sync, por horário); (2) correções manuais de atividade (`activity_energy_corrections`) não se aplicam ao gasto explícito/automático.

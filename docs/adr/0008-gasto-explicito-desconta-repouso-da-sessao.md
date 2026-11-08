# ADR 0008: O gasto explícito/automático desconta o repouso da janela do treino

- **Status**: Aceita (decisão do usuário, 08/11/2026: "corrija a dupla contagem descontando a TMB da sessão").
- **Data**: 2026-11-08

## Contexto

No modo `explicit_training` (e no `auto_intervals` sincronizado, que vira explícito), `energy_expenditure.tdee` calcula `TMB × fator de NÃO-exercício + kcal do treino`. A kcal do treino entrava inteira: tanto a informada pelo relógio (`activities.calories_kcal`) quanto a estimada por MET (`MET × 3,5 × kg / 200 × min`), e as duas são brutas (incluem o repouso). O fator de rotina já cobre a TMB das 24 h; a TMB dos minutos do treino era contada duas vezes. O motor adaptativo (`activity_energy`, decisão D4) já descontava: `kcal_ativa = max(0, kcal − TMB/1440 × minutos)`, com `unknown` tratada como bruta.

Caso auditado em produção (só leitura, 08/11): plano 10, usuário 1, 07/11: TMB 2.020,24 × 1,3 = 2.626 + 874 (treino de 107,2 min) = 3.500 kcal.

## Decisão

- Regra única em `activity_energy.net_of_resting_kcal` (consumida por `normalize_activity`, do adaptativo, e por `energy_expenditure`): `active` entra como veio; `gross`/`unknown` descontam `resting_kcal = TMB/1440 × minutos`; piso 0.
- `energy_expenditure` aplica a regra à kcal de CADA sessão (`resolve_sessions_kcal`, `resolve_training_kcal`) e de cada atividade extra do `auto_intervals` (`extra_activity_kcal`, que passa a receber a duração decorrida, com o movimento de reserva). A kcal por MET é sempre bruta e sempre desconta.
- Modo do relógio no explícito/automático: `DEFAULT_TDEE_CALORIE_MODE = unknown` (o plano só guarda `watch_calorie_mode` no adaptativo).
- Sem duração (ou sem TMB, que já tem a limitação própria): a kcal entra como está, o treino não é descartado e sai a limitação `training_rest_not_discounted` (rótulo em `labels.py`).
- `included_in_factor` e `adaptive_balance` não mudam. `tdee` não muda: recebe a kcal já líquida. `tdee_breakdown` mostra a parcela líquida, com os rótulos "Treino de força (sem o repouso da sessão)" e "Cardio e caminhadas do Intervals (sem o repouso)". A bruta e o desconto saem em `training_kcal`/`extra_activity` (`gross_kcal`, `resting_discount_kcal`) só quando houve desconto.
- O diário (`nutrition_days._training`) passa o `bmr_kcal` gravado na meta, para a linha "informado × estimado" mostrar o usado líquido; informada e estimada continuam brutas lado a lado.
- `ENGINE_VERSION` passa a `nutrition-v3`: a materialização regrava as metas NÃO congeladas (D-1, hoje e futuras), como na ADR 0007. Dias congelados não mudam. Sem migração.

## Consequências

- Gasto e meta do dia de treino caem (a meta cai o mesmo que o gasto: o déficit por ritmo é absoluto em kcal). Dia sem treino, embutido, fallback do automático e adaptativo ficam iguais.
- `inputs_hash` muda em todas as metas regravadas (versão do motor entra no hash). Os testes-ouro de `test_nutrition_modos_legados_ouro.py` foram regravados de propósito nos cenários explícito e automático com treino; os demais mantêm os valores de antes, só com hash novo.
- Limitação: correções manuais de atividade (`activity_energy_corrections`) não se aplicam ao gasto explícito/automático.

## Impacto em produção

Só motor, rótulo e leitura da duração dos extras (SELECT existente ganha duas colunas já presentes em `intervals_activities`); sem migração e sem SQL novo de escrita. **Medido em produção em 08/11/2026 (só SELECT):** só o plano 10 do usuário 1 (explícito, fator 1,3, corte de 0,7%/semana, ciclo de carbo) é afetado; metas não congeladas (08/11 e futuras; ontem só congela com `TARGET_FREEZE_LAG_DAYS` = 2) são regravadas na primeira leitura após o deploy. Dias até 06/11 estão congelados e não mudam; a tabela abaixo mostra o efeito nos treinos reais, calculado com o motor puro (`test_tabela_de_impacto_do_adr_0008`):

| dia | duração (s) | kcal relógio | TMB do dia | repouso descontado | treino líquido | gasto antes | gasto depois | variação |
|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | 7943 | 763 | 2020,24 | 185,7 | 577,3 | 3.389 | 3.204 | −186 |
| 2026-11-03 | 6357 | 707 | 2015,92 | 148,3 | 558,7 | 3.328 | 3.179 | −148 |
| 2026-11-05 | 4859 | 428 | 2020,24 | 113,6 | 314,4 | 3.054 | 2.941 | −114 |
| 2026-11-06 | 5304 | 508 | 2014,00 | 123,6 | 384,4 | 3.126 | 3.003 | −124 |
| 2026-11-07 | 6433 | 874 | 2020,24 | 150,4 | 723,6 | 3.500 | 3.350 | −150 |

Fator 1,3 (não-exercício). Os dias 02, 03, 05 e 06 estão congelados e não são regravados; o de 07/11 (D-1) e 08/11 em diante são. A meta kcal desses dias cai o mesmo valor da coluna "variação" (o ciclo de carbo redistribui carbo/gordura sobre a base sem treino, que não muda). Efeito prático: a meta dos dias de treino de força cai cerca de 115 a 190 kcal (o gasto presumido estava inflado pelo repouso contado duas vezes). Rollback: reverter o código; o motor regrava as metas não congeladas de volta.

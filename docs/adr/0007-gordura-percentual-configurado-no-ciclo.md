# ADR 0007: Gordura segue a fração configurada das kcal finais do dia (inclusive no ciclo de carbo)

- **Status**: Aceita (decisão do usuário, 08/11/2026).
- **Data**: 2026-11-08

## Contexto

Plano Cutting com proteína 2,6 g/kg de MLG, gordura 20% das kcal, prioridade `fixed_protein` e ciclo por dias. O sábado (dia alto) saía com 28,5% de gordura. Duas causas, ambas no motor `api/app/domain/nutrition/`:

1. `macros._fat_share_for_priority` com `FIXED_PROTEIN` ignorava `fat_kcal_share` e usava metade do que sobra depois da proteína, preso na faixa da estratégia (cutting 15-30%).
2. `carb_cycling.apply_cycle_over_base` mantinha a gordura em gramas e somava só carbo; o percentual do dia final desviava (caía no dia alto, subia no baixo).

## Decisão

- `FIXED_PROTEIN` usa a `fat_kcal_share` configurada (fica igual a `BALANCED`). O código do enum permanece (planos persistidos); o rótulo deixa de prometer "partes iguais". `FIXED_PROTEIN_FAT_SHARE_OF_REMAINDER` foi removida.
- Ciclo com `cycle_kinds` e sem gramas manuais (`keep_fat_share=True`): proteína fixa pela base; kcal final = kcal do dia + `base.carb × (mult − 1) × 4`; gordura = kcal final × fração efetiva ÷ 9; carbo = resto ÷ 4. O delta soma zero na semana, então a média semanal de kcal e de carbo é preservada. Sem arredondamento intermediário.
- Gramas manuais (D6) e o ciclo legado `carb_cycling_enabled` não mudam. O piso de carbo do dia baixo continua decidindo o multiplicador; como a gordura também cede, o carbo do dia baixo cai menos.
- `MacroTargets.fat_kcal_share_effective` (no `to_dict`) devolve a fração efetiva pronta; HIGH_CARB/HIGH_FAT mantêm a semântica de preset.
- `ENGINE_VERSION` passa a `nutrition-v2`: `_unchanged` deixa de reconhecer as metas gravadas e a materialização regrava as NÃO congeladas (ontem, que só congela com `TARGET_FREEZE_LAG_DAYS` = 2, hoje e futuras) na próxima leitura/publicação. Dias congelados não são recalculados. Sem migração.

## Consequências

- Metas de ontem, hoje e futuras de planos com ciclo (e de `fixed_protein`) mudam; históricos congelados ficam como estão.
- O hash das metas (`inputs_hash`) inclui a versão do motor: muda para todas as metas regravadas (valores de ouro em `test_nutrition_modos_legados_ouro.py` atualizados; os macros desses cenários não mudaram).

## Impacto em produção

Só motor e rótulo; sem migração e sem SQL. Muda para quem tem plano com `macro_priority = fixed_protein` (fração aplicada passa da "metade do que sobra" para a configurada) e/ou ciclo por dias sem gramas manuais (gordura passa a acompanhar a kcal final do dia). Metas não congeladas (ontem, hoje e futuras) são regravadas pela troca de `ENGINE_VERSION`; passadas congeladas ficam. Exemplo reconstruído do caso relatado (Cutting, 2,6 g/kg de MLG 76,4 kg, gordura 20%, pré-ciclo 2940 kcal, base sem treino ~2037 kcal): sábado alto passa de 3094 kcal / 199 P / 355 C / 98 G (28,5%) para 3149 kcal / 199 P / 431 C / 70 G (20%); dia baixo 2731 kcal / 199 P / 348 C / 61 G; normal 2940 / 199 / 389 / 65. **Medido em produção em 2026-11-08 (só SELECT):** 1 plano vigente (id 10, usuário 1: cutting, `fixed_protein`, gordura 0,20, treino explícito, ciclo por dias, sem gramas manuais) — é o único afetado; 5 planos publicados no histórico (3 `fixed_protein`, 2 `balanced`), nenhum em bulking vigente. Metas não congeladas gravadas: 07/11 (sáb, alto) 3093,55 kcal / 198,64 P / 354,84 C / 97,73 G (28,4%) e 08/11 (dom, baixo) 1896,52 / 198,64 / 121,14 / 68,60 (32,6%), ambas `nutrition-v1`; as duas serão regravadas na próxima leitura. Dias até 06/11 estão congelados e não mudam.

Notas: (1) `ENGINE_VERSION` é compartilhada com refeições, check-ins e planos: registros novos passam a sair carimbados `nutrition-v2` mesmo sem mudança de regra neles; nenhuma lógica compara a versão fora de `_unchanged`. (2) Em bulking, planos `fixed_protein` podem ficar com carbo abaixo de 3 g/kg, agora só com o aviso `carb_below_min_g_per_kg` (o preset não troca mais a fração). (3) Se a proteína + gordura já passam da kcal do dia baixo, o carbo vai a zero e a meta sai com `carb_floor_prevents_cycle`; a fração e a média semanal não valem nesse dia. (4) A amplitude do carbo no ciclo é `(1 − fração)` do delta nominal (~±20% com 20% de gordura).

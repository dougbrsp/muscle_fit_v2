# ADR 0009: A proteína do corte é g/kg de peso corporal total

- **Status**: Aceita (decisão do usuário, 08/11/2026: "o cálculo da meta de proteína está sendo feito em cima da massa magra, mas deveria estar sendo feito em cima do peso total"; escolhas: regra do motor para todo plano de corte, fator 2,6 g/kg mantido).
- **Data**: 2026-11-08

## Contexto

O corte usava `ProteinBasis.FAT_FREE_MASS` (2,6 g/kg de massa livre de gordura, faixa 2,3 a 3,1); manutenção e ganho usam o peso total (1,6 a 2,2). Para o plano 10 (peso 91,0 kg, MLG 76,4 kg) isso dava 198,64 g contra 236,6 g pelo peso.

## Decisão

- `StrategyParams.defaults_for(CUTTING)` usa `ProteinBasis.BODY_WEIGHT`. Constantes renomeadas: `CUT_PROTEIN_G_PER_KG_RANGE = (1.8, 2.7)` (peso total em déficit; Phillips & Van Loon 2011, Helms 2014) e `CUT_PROTEIN_G_PER_KG_DEFAULT = 2.6` (mantido).
- `ProteinBasis.FAT_FREE_MASS` é mantido: nenhuma estratégia o usa, mas `inputs_json` antigos trazem `"protein_basis":"fat_free_mass"` e o rótulo e o ramo legado de `manual_targets` continuam lendo. A base não é gravada no plano (vem da estratégia), então nenhum plano precisa de migração.
- Plano gravado com `protein_g_per_kg` fora da nova faixa: o motor só avisa (`param_out_of_range:protein_g_per_kg`); a gravação do plano continua validando na faixa (400). Plano 10 (2,60) está dentro.
- Proteína por refeição (`PROTEIN_PER_MEAL_G_PER_KG_RANGE`, 0,40 a 0,55 g/kg) já era sobre o peso: sem mudança.
- `ENGINE_VERSION` passa a `nutrition-v4`: a materialização regrava as metas NÃO congeladas (D-1, hoje, futuras).
- Espelho do frontend (`motor-constants.generated.json`) regenerado do backend; prompts do privado só interpolam a faixa.

## Consequências

- A proteína do corte sobe (+19% no plano 10) e, como o plano é `fixed_protein` com gordura em fração das kcal, o carboidrato cai o mesmo em kcal. O swing do ciclo é 25% do carbo-base, que fica menor: o dia alto sobe menos e o dia baixo cai menos.
- `inputs_hash` muda (versão do motor); ouros de `test_nutrition_modos_legados_ouro.py` só mudam o hash (macros dos cenários de bulking não mudam).
- Risco: déficit grande com peso alto e kcal baixa aperta o carbo (piso de carbo do ciclo, `carb_floor_prevents_cycle`); não ocorre nos dias do plano 10 (ver tabela).

## Impacto em produção

Só motor, rótulo e espelho do frontend; sem migração e sem escrita nova. Só o plano 10 do usuário 1 (cutting, `fixed_protein`, 2,60 g/kg, gordura 20% das kcal finais, ciclo de carbo, explícito, fator 1,3). Medido com o motor puro (`macro_split` + `apply_cycle_over_base`, peso 91,0 kg, MLG 76,4 kg; base do plano sem treino 2.058,3 kcal; gasto e déficit de 568 kcal do ADR 0008). Antes = base MLG (v3), depois = base peso (v4):

| dia | kcal antes | kcal depois | proteína antes | proteína depois | carbo antes | carbo depois | gordura antes | gordura depois |
|---|---|---|---|---|---|---|---|---|
| 07/11 (alto, com treino) | 3.068,8 | 3.030,9 | 198,6 | 236,6 | 415,1 | 369,6 | 68,2 | 67,4 |
| 08/11 (baixo, VirtualRun) | 2.220,5 | 2.258,4 | 198,6 | 236,6 | 245,5 | 215,1 | 49,3 | 50,2 |
| dia baixo sem treino (base 2.058,3) | 1.845,3 | 1.883,2 | 198,6 | 236,6 | 170,4 | 140,0 | 41,0 | 41,8 |

O dia baixo mais restrito (1.883 kcal, sem treino) fica com 140 g de carbo (1,54 g/kg): não bate o piso de carbo do ciclo e não zera. O carbo do dia alto cai ~45 g e o do baixo 30 a 45 g. Sensibilidade (motor puro, dia baixo sem treino, base = alvo): com alvo de 1.500 kcal o piso prende o ciclo (`carb_floor_prevents_cycle`, carbo 63 g, dia sem swing); com 1.700 kcal o carbo é 93,5 g. Nenhum dia do plano 10 está nessa faixa. Metas congeladas (até D-2) não mudam; D-1, hoje e futuras são regravadas na primeira leitura após o deploy.

**Prova lida em produção (08/11/2026, só SELECT:** `SELECT id, user_id, status, strategy, protein_g_per_kg, archived_at, superseded_at FROM nutrition_plans`**).** 5 planos, todos do usuário 1, todos `cutting` e `published`: 2 (2,60), 3 (2,70), 4 (2,30) e 5 (2,70) arquivados e substituídos; 10 (2,60) vigente. Nenhum rascunho e nenhum fora de 1,8 a 2,7 (o limite superior é inclusivo: `_within` usa `piso <= valor <= teto`, então 2,70 está dentro). Logo "só o plano 10" está provado.

**Validade da tabela.** Os dias acima valem na data da medição (08/11). Se a promoção atrasar, os dias não congelados mudam de situação e os números mudam junto.

## Limitações

- Rascunho de corte com proteína entre 2,7 e 3,1 g/kg (válido antes) passa a receber HTTP 400 na criação/edição (`api/app/nutrition_plans.py`, `_validate_payload`/`_invalid_plan`, ~L223-233) até o usuário ajustar. O usuário vê o erro `invalid_plan`, "Parâmetros do plano fora das regras do motor de nutrição.", com o item `param_out_of_range:protein_g_per_kg` ("fora da faixa da fonte"). Hoje não há nenhum rascunho.
- Plano publicado fora da faixa só gera o aviso `param_out_of_range:protein_g_per_kg` no motor; não bloqueia.

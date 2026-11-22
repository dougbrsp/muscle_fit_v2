# ADR 0034: Prioridade nutricional do dia no diário da nutrição (issue #272)

- **Status**: Aceita. Branch `feat/prioridade-dia-l30`. Não publicada nem promovida a produção.
- **Data**: 2026-11-22
- **Relaciona-se a**: [ADR 0029](0029-ciclo-de-carboidratos-com-compensacao-em-gordura.md) e [ADR 0033](0033-politica-do-ciclo-em-coluna-propria-e-lembrada-com-o-ciclo-desligado.md) (a política de compensação em gordura, que decide se "Priorizar gordura" aparece).

## Contexto

- O usuário pediu (#272) que o diário da nutrição indique a prioridade nutricional do dia, antes do registro das refeições.
- Escolhas do usuário em 22/11/2026: sem ciclo ativo, mostrar "Distribuição normal" (a issue admitia duas leituras; a recomendação técnica era não mostrar nada); selo no cabeçalho do dia e no cartão de meta, na web e no celular.
- Restrições: a regra mora no dono do ciclo (`api/app/domain/nutrition/carb_cycling.py`), pela regra permanente dos motores (`docs/motores/FONTES_DA_VERDADE.md`); a tela não decide nada; o histórico não pode mudar retroativamente.

## Alternativas consideradas

- **Derivar na leitura**, a partir do tipo de dia e dos códigos de limitação já gravados. Recusada: a meta pré-ciclo não fica gravada, e os códigos de limitação são ambíguos (o mesmo código aparece com a semana plana e com o carboidrato cortado em zero).
- **Decidir só pelo tipo de dia** (alto/baixo). Recusada: o rótulo tem de refletir os macros finais; um dia baixo sem efeito não pode dizer "reduzido".
- **Decidir só pelo delta**. Recusada: o dia normal pode ter multiplicador diferente de 1 e não pode virar "reduzido".
- **Coluna nova com migração**. Recusada: o JSON de entradas já guarda o resultado inteiro do cálculo.
- **Não subir a versão do motor**. Recusada: os dias em aberto ficariam sem o selo até alguma entrada mudar.
- **Exportar os rótulos ao espelho do frontend**. Recusada: a API entrega o texto pronto, como já faz com outros rótulos da meta.
- **Sem ciclo ativo, não mostrar nada**. Era a recomendação técnica; o usuário escolheu mostrar "Distribuição normal".

## Decisão

1. **Regra no dono do ciclo.** `carb_cycling.day_priority(kind, before, after)` compara as metas antes e depois do ciclo, dentro do próprio cálculo, com tolerância na constante `DAY_PRIORITY_CARB_TOLERANCE_G` (0,5 g). Enum `DayPriority`:
   - dia alto com carboidrato maior que a tolerância: `carb`;
   - dia baixo com carboidrato menor que a tolerância e gordura maior que a tolerância: `fat`;
   - dia baixo com carboidrato menor e gordura sem aumento: `reduced_carb`;
   - dia normal, ciclo sem efeito nos macros finais (piso, compensação inviável, semana plana) e plano sem ciclo: `normal`;
   - meta não calculável (antes ou depois): `unavailable`.
2. **Cálculo.** `daily_target.compute_daily_target` chama `day_priority(tipo_dia, metas_pre_ciclo, metas)` e grava o resultado em `DailyTarget.nutrition_priority`. É saída: não entra no hash das entradas.
3. **Rótulos.** `labels.DAY_PRIORITY_LABELS` ("Priorizar carboidrato", "Priorizar gordura", "Carboidrato reduzido", "Distribuição normal") e `DAY_PRIORITY_EXPLANATIONS`. `unavailable` não tem rótulo.
4. **Persistência.** O código vai dentro do JSON de entradas já existente (`nutrition_daily_targets.inputs_json`, chave `nutrition_priority`), sem coluna nova nem migração. A leitura usa alias JSON (`JSON_EXTRACT(inputs_json, '$.nutrition_priority')` em `nutrition_targets_service.py`).
5. **Contrato.** Três campos aditivos na meta do dia (`nutrition_priority`, `nutrition_priority_label`, `nutrition_priority_explanation`), montados por `priority_public` e incluídos por `target_row_public` e `day_public`, no diário, na semana e na prévia. Dia antigo sem a chave: os três campos vêm nulos e a tela não mostra o selo.
6. **Versão do motor.** `ENGINE_VERSION` passa de `nutrition-v5` para `nutrition-v6`, só para as metas dos dias em aberto serem recalculadas e ganharem o indicador. Nenhum valor de meta muda.
7. **Tela.** `frontend/src/components/nutrition/DayPriorityBadge.tsx` usa só rótulo e explicação (tooltip, `aria-label`); cor única; sem rótulo não há selo. Aparece no cabeçalho do dia (`DayHeader.tsx`) e no cartão de meta (`TargetVsIntakeCard.tsx`, `MobileTargetVsIntake.tsx`, `NutricaoDiarioScreen.tsx`).
8. **Guarda dos motores.** Dois padrões novos em `api/tests/unit/test_guarda_fontes_da_verdade.py` (`rotulo_prioridade_do_dia`, `prioridade_do_dia_literal`), com allowlist vazia. Mapa atualizado em `docs/motores/FONTES_DA_VERDADE.md`.

## Trade-offs

- Ganha: a regra tem um dono só, olha os macros finais e a tela não calcula nada; sem migração.
- Ganha: o histórico não muda retroativamente.
- Paga: a primeira leitura após o deploy regrava as metas dos dias em aberto (a versão do motor mudou).
- Paga: dias já fechados ficam sem selo para sempre.
- Paga: a tolerância de 0,5 g foi decidida na implementação, não pelo usuário.

## Consequências

- **Dados.** Uma chave nova em `nutrition_daily_targets.inputs_json`. Nenhuma coluna, nenhuma migração.
- **Contrato.** Três campos aditivos e opcionais na meta do dia. Clientes antigos os ignoram.
- **Operação.** Sem ordem especial de deploy e sem `RUN_DB_MIGRATIONS` por causa desta mudança.
- **Mapa.** Regra nova no dono do ciclo, registrada no mapa dos motores.

## Impacto em produção

Medição do Tech Lead em 22/11/2026, só leitura no banco de desenvolvimento (cópia da produção do usuário 1):

- 36 metas diárias gravadas (versões `nutrition-v1`, `v4` e `v5`; as de `v5` somam 7 dias).
- Nenhum valor de meta muda: os 14 hashes de referência dos modos legados reproduzem com a versão anterior forçada.
- No deploy, as metas dos dias em aberto são regravadas uma vez, na primeira leitura, só para ganhar o indicador. Dias já fechados ficam sem o indicador.

**Não medido:** nada em tela; a regravação em produção; a integração só roda no CI.

## Limites

- A tolerância de 0,5 g é decisão da implementação.
- A tabela da semana, a prévia do plano e o dashboard recebem os campos pela API, mas não mostram o selo.
- No celular o selo aparece duas vezes na mesma tela (cabeçalho e cartão), por escolha do usuário.
- Refeed, modo adaptativo e pausa de fase passam pela mesma regra, sem teste dedicado de ponta a ponta.
- "Priorizar gordura" depende da política de compensação em gordura (#242, ADR 0029 e 0033).
- Nada conferido no navegador nem com leitor de tela.

## Verificação

Arquivos de teste da entrega (este ADR não registra resultado de execução):

- `api/tests/unit/test_nutrition_prioridade_do_dia.py` (novo).
- `frontend/src/components/nutrition/DayPriorityBadge.test.ts` (novo).
- Caso novo em `api/tests/integration/dataapi/test_nutrition_diary_dataapi.py` (só roda no CI).
- Guarda: `api/tests/unit/test_guarda_fontes_da_verdade.py`.

Não verificado neste ADR: a execução desses testes; tela no navegador ou no iPhone.

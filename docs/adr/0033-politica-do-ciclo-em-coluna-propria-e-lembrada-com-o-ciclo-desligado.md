# ADR 0033: Política do ciclo em coluna própria e lembrada com o ciclo desligado (issue #248)

- **Status**: Aceita. Branch `feat/politica-ciclo-l28`. Não publicada nem promovida a produção. Limpeza da chave antiga feita no lote 33 (ver a seção de atualização no fim).
- **Data**: 2026-11-22
- **Relaciona-se a**: [ADR 0029](0029-ciclo-de-carboidratos-com-compensacao-em-gordura.md) (superada em partes por esta decisão, com os trechos marcados no corpo dela) e [ADR 0032](0032-permissao-propria-para-acrescentar-equipamento-a-academia.md) (migração 122 do mesmo conjunto de deploy, que também exige `RUN_DB_MIGRATIONS=true`).

## Contexto

- A política do ciclo (`CarbCyclePolicy` em `api/app/domain/nutrition/carb_cycling.py`: `vary_kcal` padrão e `fat_compensation`) ficava só como chave irmã `policy` dentro de `nutrition_plans.carb_cycling_json`, pela ADR 0029.
- Essa chave só era gravada com ciclo: `carb_cycling.join_stored_cycle` devolve `None` quando não há mapa de dias. Por isso a política sumia ao desligar o ciclo, e religar voltava ao padrão.
- Escolha do usuário em 22/11/2026 (#248): (1) coluna própria para a política, por migração, no lugar da chave dentro do JSON; (2) o editor lembra a política ao desligar e religar o ciclo.
- Restrições reais: há planos em produção com ciclo, e o resultado deles não pode mudar; o código novo pode subir antes ou depois da migração; a regra permanente de motores (`docs/motores/FONTES_DA_VERDADE.md`) exige que a leitura e a validação tenham um dono só.

## Alternativas consideradas

- **Corte seco**: ler só a coluna e apagar a chave do JSON na mesma migração. Recusada. Entre a migração e as Lambdas novas, o código antigo leria a política padrão; e no rollback o código antigo não enxerga a coluna.
- **Lembrar só no estado do editor**, sem persistir. Recusada: não sobrevive a salvar com o ciclo desligado.
- **ENUM no lugar de VARCHAR**. Recusada: as colunas de código de `nutrition_plans` são VARCHAR, e a validação fica no dono (`plan._enum_or_error`).
- **Coluna sem migração** (a recusa da ADR 0029). Superada por esta decisão do usuário.

## Decisão

1. **Coluna própria** `nutrition_plans.carb_cycle_policy VARCHAR(24) NULL DEFAULT NULL`, pela migração 123 (`api/db/migrations/123_nutrition_plans_carb_cycle_policy.py`, `ALGORITHM=INSTANT`). `down` remove a coluna.
2. **Preenchimento idempotente**: `backfill_sql` copia a chave `policy` do JSON só quando ela é um dos códigos válidos (passados como parâmetros) e a coluna ainda é nula. A chave do JSON não é apagada.
3. **Leitura com reserva** (`nutrition_targets_service.settings_from_row`): a coluna quando não é nula; senão a chave do JSON (`split_stored_cycle`); senão a política padrão. **[Superado no lote 33 (23/11/2026, #248): a leitura usa só a coluna; a chave antiga é descartada por `stored_cycle_days`, sem ser lida.]**
4. **Escrita dupla** (`nutrition_plans._settings_write_values`): a coluna recebe sempre o código explícito, inclusive o padrão. A chave do JSON continua gravada só com ciclo e política fora do padrão (`join_stored_cycle`). **[Superado no lote 33: a chave do JSON deixou de ser gravada; a escrita grava só a coluna, e `join_stored_cycle` foi removida.]**
5. **Política aceita sem ciclo** (`plan.build_plan_settings`): a recusa `param_not_applicable:carb_cycle_policy` saiu. Sem ciclo, a política não entra na conta nem na impressão digital (`plan.PlanSettings.fingerprint_dict` só a inclui com ciclo). Nenhuma meta existente é regravada por isso.
6. **Herança na edição** (`nutrition_plans._validate_payload`): campo omitido herda a política guardada, com ou sem ciclo; `null` explícito volta à padrão; valor explícito vale.
7. **Valor desconhecido**: na escrita continua sendo erro de validação (`invalid_code:carb_cycle_policy`, em `plan._enum_or_error`). Na leitura, o plano é lido como padrão e o log registra só ids (`nutrition.plan_carb_cycle_policy_invalid`).
8. **Sem a coluna** (código novo sem a migração 123): leitura e escrita usam as variantes SQL sem a coluna, escolhidas por `nutrition_schema.has_plan_policy_column` (`nutrition_plans._INSERT_NO_POLICY_SQL`, `_UPDATE_DRAFT_NO_POLICY_SQL`, `_PLAN_COLUMNS_NO_POLICY`). Nessa janela a política não é lembrada com o ciclo desligado.
9. **Espelho**: `DayKind.NORMAL` de `carb_cycling.py` é exportado como `default_day_kind` (`domain/nutrition/__init__.py::export_for_frontend`). O frontend lê `DEFAULT_DAY_KIND` (`frontend/src/lib/nutrition-constants.ts`) no lugar do literal do dia normal.
10. **Frontend** (`frontend/src/components/nutrition/PlanForm.tsx`, `frontend/src/lib/nutrition-cycle-policy.ts`): desligar o ciclo não zera a política no editor. O envio manda a política sempre (`cyclePolicyPayload`). O resumo continua sem mostrar a política quando não há ciclo (`planPolicyLabel`). Nenhum cálculo novo no frontend.

## Trade-offs

- Ganha: a política sobrevive a salvar com o ciclo desligado; o rollback do código sem reverter a migração é seguro, porque o código antigo ignora a coluna e lê a chave, que continua gravada com ciclo.
- Ganha: sem ciclo, nenhuma meta muda (a política fica fora da conta e da impressão digital).
- Paga: duas fontes para a mesma política (coluna e chave) até a limpeza. A escrita dupla e a leitura com reserva são temporárias. **[Superado no lote 33: a limpeza foi feita; ver a seção de atualização abaixo.]**
- Paga: a promoção exige `RUN_DB_MIGRATIONS=true` antes das Lambdas novas.
- Paga: `ALTER TABLE` a mais no deploy (só metadado, `ALGORITHM=INSTANT`).

## Consequências

- **Dados.** Uma coluna nova em `nutrition_plans`. A chave `policy` do JSON segue sendo gravada com ciclo.
- **Contrato.** `PUT /nutrition/plans/{id}` aceita `carb_cycle_policy` sem ciclo; `param_not_applicable:carb_cycle_policy` deixa de existir. Campo omitido herda a política guardada.
- **Operação.** Ordem de deploy: migração 123 antes das Lambdas. Rollback do código sem reverter a migração é seguro; perde-se só a política lembrada com o ciclo desligado.
- **Próximo lote.** Migração que remove a chave do JSON, e código que deixa de gravar a chave e de ler a reserva. Só depois de um ciclo de deploy. **[Feito no lote 33, sem migração: ver a atualização abaixo.]**

## Impacto em produção

Medição do Tech Lead em 22/11/2026, só leitura no banco de desenvolvimento (cópia da produção do usuário 1):

- 5 planos publicados, 4 com ciclo; nenhum com a chave `policy` gravada (todos na política padrão).
- O preenchimento da migração 123 não altera nenhuma linha hoje. Nenhuma meta muda.

**Não medido:** nada em tela (nenhum navegador nem iPhone); a migração 123 não foi executada em banco (roda pela pipeline com `RUN_DB_MIGRATIONS=true`); o caso de integração só roda no CI (rótulo `ci-completo`).

## Limites

- Com a coluna ausente e o ciclo desligado, a política não é lembrada (só na janela sem a migração).
- Escrita dupla e leitura com reserva são temporárias (um ciclo de deploy).
- O envio do botão de salvar com o ciclo desligado só tem teste de unidade da função que monta o payload.
- Rollback para código anterior à ADR 0029 segue com o risco descrito nela.

## Verificação

Arquivos de teste da entrega (este ADR não registra resultado de execução):

- `api/tests/unit/test_migration_123_plano.py` (novo): plano da migração.
- `api/tests/unit/test_nutrition_ciclo_politica_persistencia.py`: herança na edição (com e sem ciclo), valor desconhecido e política sem ciclo aceita.
- `api/tests/unit/test_nutrition_ciclo_compensacao_gordura.py`: `test_politica_sem_ciclo_nao_muda_impressao_digital_nem_metas`.
- `frontend/src/lib/nutrition-cycle-policy.test.ts` e `frontend/src/components/nutrition/PlanForm.ciclo.test.ts` (novo).

Não verificado neste ADR: a execução desses testes; a migração contra MySQL real (roda no CI e pelo `migrate` da promoção); tela no navegador ou no iPhone.

## Atualização de 23/11/2026 (lote 33, #248): limpeza feita

Feito, sem migração. A política segue só em `nutrition_plans.carb_cycle_policy`.

- **Escrita**: `carb_cycling_json` recebe só o mapa de dias do ciclo (`nutrition_plans._settings_write_values`); a política vai só na coluna. `join_stored_cycle` foi removida.
- **Leitura**: só a coluna; nula = política padrão (`nutrition_targets_service.settings_from_row`). `split_stored_cycle` virou `stored_cycle_days` (`api/app/domain/nutrition/carb_cycling.py`), que descarta sem erro uma chave `policy` antiga. A conta e a impressão digital (`fingerprint_dict`) não mudam com ou sem a chave.
- **Aviso** `nutrition.plan_carb_cycle_policy_invalid`: no máximo uma vez por plano por processo.
- **Testes** (unitários): `api/tests/unit/test_nutrition_ciclo_politica_persistencia.py` (digital igual com e sem a chave; leitura só da coluna; aviso único) e `api/tests/unit/test_nutrition_ciclo_compensacao_gordura.py` (código único no piso de gordura; independência dos dias).

Mantido de propósito: as variantes para banco sem a coluna da migração 123 (`nutrition_schema.has_plan_policy_column` e as consultas "sem política"). Nessa janela a política passa a ser a padrão, não mais a chave do JSON.

Superados nas seções acima: itens 3 e 4 da Decisão, a linha "duas fontes" das Trade-offs e o "Próximo lote" das Consequências. O histórico foi mantido.

Impacto em produção: medido pelo Tech Lead em 23/11/2026, só leitura, no banco de desenvolvimento (cópia de produção do usuário 1): 6 planos, 5 com ciclo, 0 com a chave `policy` no JSON, 0 com a coluna preenchida. Nenhuma leitura muda e nenhuma meta é regravada.

Rollback: o código anterior lê a coluna primeiro; plano salvo depois desta mudança tem a coluna preenchida, então segue correto. Rollback para antes do lote 28 segue com o risco descrito na ADR 0029.

Não medido: planos de outros usuários (a cópia do DEV traz só o usuário 1); a integração, que roda só no CI (`ci-completo`).

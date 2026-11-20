# ADR 0024: RPE por série com saneamento da saída da IA e acabamentos da ficha e do Modelo (issues #183 e #184)

- **Status**: Aceita. Branch `fix/rpe-por-serie-183-184`. Não publicada nem promovida a produção.
- **Data**: 2026-11-20
- **Complementa**: ADR 0013 (RPE esperado por série no Modelo, issue #146). Nenhuma decisão anterior é substituída. A rampa em texto (`"8-9-10"`) continua canônica; a lista por série é a forma de entrada e de saída que a tela e a IA usam.

## Contexto

As duas issues são continuação da #146 (ADR 0013). Três problemas reais apareciam depois dela:

1. **#183, saída da IA com RPE fora da escala.** A periodização por IA devolvia `rpe_target` com valor fora da escala do dono (1 a 10) ou do passo (meio ponto), como `8.3` ou `11`, ou texto sem número. A validação de schema (`WorkoutPlanTemplateIn`) recusava o rascunho inteiro depois da geração já paga: o job terminava `SCHEMA_INVALID`. Não havia registro de quantas vezes isso acontecia.
2. **#184, acabamentos do RPE por série na ficha e no Modelo.**
   - O PUT parcial da ficha (`services._replace_plan_exercises`) zerava `sets_count`, `reps_target`, `load_target`, `rpe_target` e `rest_seconds` quando o cliente não os enviava numa linha já salva. O aplicativo atual sempre envia os cinco, mas qualquer cliente que omitisse um campo apagava a prescrição gravada.
   - A lista por série (`rpe_targets_by_set`) não era validada contra o `sets_count` gravado quando o corpo não trazia `sets_count`.
   - A ficha não tinha edição por série; só a rampa em texto.
   - O erro 422 do salvamento do Modelo aparecia como mensagem genérica, sem título de segurança nem localização (slot e item).
   - Havia cópias de critério: `next_set_load.planned_ramp_rpe` tinha o próprio critério de rampa (a duplicação estava registrada no mapa, com "unificar quando o arquivo for tocado"), e `effective_intensity` tinha um embrulho `_format_rpe` sobre o formatador do dono.
   - O campo de RPE por série aceitava valor fora do passo e tinha `decimalScale` escrito à mão.

Restrições: sem migração (o banco de produção não muda); a regra de RPE é do dono `training_history` (`docs/motores/FONTES_DA_VERDADE.md`, regra "Regras só nos motores"); a integração (MySQL real) roda só no CI.

Medição de 20/11/2026, por leitura (só SELECT): `template_generation_logs` com 8 gerações, todas `succeeded`, nenhuma com `SCHEMA_INVALID` nem com "fora da escala" no relatório; nenhum item de Modelo (`workout_plan_template_slot_items`) com letras ou parêntese no texto de RPE; `workout_plan_exercises` com 761 linhas, 0 com `sets_count` nulo e 17 com `rpe_target` nulo. Também por leitura: `workout_plan_template_slot_items` com 2.131 itens, 1.890 com `rpe_target` preenchido, nenhum com número acima de 10, igual a 0 ou com decimal fora do passo de 0,5; `workout_plan_exercises` com 761 linhas e nenhuma com RPE acima de 10 ou com decimal fora do passo. Ou seja, não há RPE fora da escala ou do passo gravado em Modelos nem em fichas.

## Alternativas consideradas

- **Recusar a saída da IA com erro (manter `SCHEMA_INVALID`).** Descartada: a geração já foi paga e o usuário perde o rascunho por um degrau de 0,3 de diferença.
- **Arredondar para cima ou para o valor mais próximo sem regra de empate.** Descartada: o empate para baixo (`ROUND_HALF_DOWN`) é o lado seguro, porque nunca prescreve mais esforço do que a IA pediu (8,25 vira 8).
- **Saneamento na tela ou no serviço.** Descartada: a regra de escala e passo tem um dono só; a tela e o serviço não podem arredondar por conta própria.
- **Endurecer `parse_rpe_ramp` para ignorar o RIR em textos como `"7-8 (RIR 2)"`.** Descartada: o parser alimenta 22 usos em 6 arquivos, inclusive a progressão. Em produção não há texto de RPE com letras, então o problema não aparece hoje. O teste `test_texto_com_numero_a_mais_fica_como_o_parser_le_hoje` (`api/tests/unit/test_rpe_alvo_por_serie.py`, L202) continua travando o comportamento atual.
- **Aceitar RPE legado fora da escala no rascunho do Modelo (`PUT /workout-plan-templates/{id}/draft`).** Descartada: a validação roda no parse do corpo (`WorkoutPlanTemplateIn`), antes do handler. Aceitar exigiria um segundo modelo só para o rascunho. O erro passa a aparecer na tela com título e item apontados.
- **Ficha sem edição por série (só texto).** Descartada: o pedido era editar a rampa por série nos editores da ficha. A lista é a fonte quando o usuário a define; o texto continua quando ele não mexe.
- **Teste de renderização de `RpePerSetField` nos editores da ficha.** Não feito nesta entrega (ver Limites).

## Decisão

1. **Saneamento da saída da IA no dono.** `api/app/domain/training_history.py::sanitize_rpe_target(value) -> (texto|None, mudou)`: cada degrau é limitado a [mínimo, máximo] de `rpe_target_scale()` e arredondado ao passo mais próximo com `ROUND_HALF_DOWN`; texto sem número vira nulo; o colapso de cauda (`8-8` → `8`) é formato e não conta como mudança. Reutiliza `parse_rpe_ramp`, `rpe_target_scale` e `compose_rpe_target`.
2. **Periodização corrige em vez de falhar.** `api/app/periodization/validation.py`: a constante `RPE_SANITIZED = "RPE_SANITIZED"` (L117) e `_sanitize_rpe_targets` (L381) rodam em `validate_draft` (L238) logo depois de `_null_optional_zeros` (L237) e antes de `_to_template_in` (L396). Cada correção vai para `auto_fixes` com `code`, `slot`, `slot_name`, `item`, `from` e `to`. O relatório é persistido em `template_generation_logs.validation_report` por `periodization/logs.py::finish_log` (L136), em qualquer status.
3. **Saída com a lista por série.** `api/app/periodization/core.py::run_periodization_draft` (L180) devolve o `draft` decorado por `decorate_draft_payload` (`api/app/workout_plan_templates.py`, L888), que acrescenta `rpe_targets_by_set` em cada item. A decoração é só de saída e não volta ao banco.
4. **PUT parcial preserva a prescrição gravada.** `api/app/workout_plan_exercise_merge.py::PRESCRIPTION_EXERCISE_FIELDS` (L42) lista os cinco campos. `merge_unsent_advanced_fields(incoming, existing_row, fields)` (L51) ganhou o parâmetro `fields`, com o padrão antigo. `services._replace_plan_exercises` (L3344) aplica a preservação só em linha já salva (casamento por `id`, `_saved_row_of`); `null` explícito continua limpando; linha nova não muda.
5. **Lista por série contra o `sets_count` gravado.** `services._compose_rpe_for_saved_sets` (L3216) recusa com 422 `rpe_targets_by_set_exceeds_sets` quando a lista é maior que o `sets_count` gravado e recompõe o texto com `compose_rpe_target`. `api/app/models.py::_resolve_rpe_target` (L327) ganhou `keep_steps_if_sets_unsent`: sem `sets_count` no corpo, a lista segue no modelo até o save.
6. **Um só critério de rampa e um só formatador.** `api/app/domain/next_set_load.py::planned_ramp_rpe` (L871) usa `training_history.is_rpe_ramp` (L877). `api/app/domain/effective_intensity.py` não tem mais o embrulho `_format_rpe`; `clamp_rpe_text` (L532) chama `format_rpe_step` do dono (L561).
7. **Teste da ida e da volta.** `test_uniforme_sem_series_e_ida_e_volta_estavel` (`api/tests/unit/test_rpe_alvo_por_serie.py`, L264) passa a executar a ida e a volta da composição e da expansão.
8. **Notificação de erro no Modelo.** `frontend/src/lib/template-save-error.ts::templateSaveError` (L26) separa o bloqueio de segurança (título "Bloqueado por segurança", reconhecido pelo prefixo `SAFETY_PREFIX` do texto do servidor, L14) da validação (título "Dados inválidos"; mensagem do servidor; "Slot N, item M: …" quando o erro traz a localização, L40-41). Usada em `frontend/src/app/(app)/modelos-treino/[id]/WorkoutPlanTemplateBuilderClient.tsx` no salvar (`saveMutation`, L813), publicar (mesmo corpo do PUT, comentário L690) e salvar rascunho (`saveDraftMutation`, L732).
9. **Campo por série com passo do dono.** `frontend/src/lib/workout-plan-template-form.ts`: `RPE_TARGET_SCALE` (L42, espelho de `motor-constants.generated.json`, bloco `rpe_target_scale`), `stepDecimalScale` (L45) e `isOnRpeStep` (L54). `RpePerSetField.tsx` deriva as casas decimais do passo (L81) e recusa valor fora do passo (L85), sem arredondar na tela. Prop opcional `hint` (L27) com a dica de RPE por objetivo, passada por `PercentBudgetItemCard.tsx` (L211) e `EquipmentSeriesItemCard.tsx` (L479).
10. **Ficha com lista por série.** `frontend/src/lib/plan-rpe-per-set.ts`: `rpePerSetView` (L10), `rpePerSetPatch` (L16) e `rpePayloadFields` (L24). Os editores `frontend/src/app/(app)/planos-treino/ExercisesTable.tsx` (L690-695) e `frontend/src/mobile/screens/telas/PlanExercisesEditor.tsx` (L342-347) usam `RpePerSetField` quando a linha tem `sets_count`. Quem não mexeu no RPE continua enviando o texto; quem definiu por série envia só `rpe_targets_by_set` e o servidor compõe o texto. Aplicar a sugestão de progressão volta a linha ao modo texto (`rpe_targets_by_set: []`, `rpe_edited: false`; L181 e L421).
11. **Teste do realizado.** `frontend/src/app/(fullscreen)/modo-treino/SetLoggerCard.test.ts` (L74) ganha o teste "o RPE realizado não nasce com o alvo". O teste frágil citado na issue #184 não existia no arquivo.

Resultado por item das issues:

| Issue | Item | Resultado | Onde |
|---|---|---|---|
| #183 | Saída da IA com RPE fora da escala ou do passo derruba o job (`SCHEMA_INVALID`) | Corrigido: ajuste com auto-fix `RPE_SANITIZED`, job segue | `training_history.py` L162; `validation.py` L117, L381, L238 |
| #183 | Saneamento antes do schema | Corrigido: roda antes de `_to_template_in` | `validation.py` L237-238, L396 |
| #183 | Texto sem número | Corrigido: vira nulo com auto-fix | `training_history.py` L162-173 |
| #183 | Arredondamento de empate | Corrigido: empate para baixo | `training_history.py` L175 |
| #183 | Medir a frequência do ajuste | Mensurável pelos auto-fixes, com a consulta `SELECT status, COUNT(*) FROM template_generation_logs WHERE validation_report LIKE '%RPE_SANITIZED%' GROUP BY status`; medição atual: nenhuma geração com "fora da escala" nem `SCHEMA_INVALID` nas 8 gerações de 20/11 | `logs.py` L136 |
| #183 | Lista por série no resultado do job | Corrigido (aditivo): `rpe_targets_by_set` por `decorate_draft_payload` | `core.py` L180, L284-287; `workout_plan_templates.py` L888 |
| #183 | Endurecer a normalização para ignorar RIR (`"7-8 (RIR 2)"`) | Descartado: 22 usos em 6 arquivos, inclusive progressão; sem texto com letras em produção; comportamento travado por teste | `training_history.py` L184 (shortcut); `test_rpe_alvo_por_serie.py` L202 |
| #184 | PUT parcial apaga `sets_count`, `reps_target`, `load_target`, `rpe_target` e `rest_seconds` não enviados | Corrigido e ampliado para os cinco campos; `null` explícito limpa | `workout_plan_exercise_merge.py` L42, L51; `services.py` L3432 |
| #184 | Lista por série sem `sets_count` no corpo não é validada | Corrigido: 422 `rpe_targets_by_set_exceeds_sets` contra o gravado | `services.py` L3216-3226, L3434; `models.py` L327-343 |
| #184 | Edição por série nos editores da ficha | Corrigido: `RpePerSetField` nas duas telas quando há `sets_count` | `ExercisesTable.tsx` L690-695; `PlanExercisesEditor.tsx` L342-347; `plan-rpe-per-set.ts` |
| #184 | Erro 422 do Modelo sem localização | Corrigido na notificação (slot e item, título); sem destaque no card | `template-save-error.ts` L26-52; `WorkoutPlanTemplateBuilderClient.tsx` L732, L813 |
| #184 | Campo aceita valor fora do passo e `decimalScale` escrito à mão | Corrigido: passo e escala vêm do espelho; valor fora do passo ou fora da escala é recusado na digitação (`isValidRpeTarget`); no commit, campo preenchido e inválido volta ao último valor válido (`sanitizeRpeDraft`), sem arredondar na tela | `workout-plan-template-form.ts` L42-54, L73-84; `RpePerSetField.tsx` L44, L82, L86 |
| #184 | Dica de RPE por objetivo | Corrigido: prop `hint` | `RpePerSetField.tsx` L27; `PercentBudgetItemCard.tsx` L211; `EquipmentSeriesItemCard.tsx` L479 |
| #184 | `RpePerSetField` sem teste de renderização | Corrigido: `RpePerSetField.test.ts` renderiza o campo e cobre o estado "Alvo gravado" com a ação "Definir por série", o modo com um campo por série e a dica. Os editores da ficha que reutilizam o campo não têm teste de renderização próprio (ver Limites, d) | `RpePerSetField.test.ts` |
| #184 | Salvar rascunho promete gravar mesmo inválido | Descartado no servidor (a validação do corpo é anterior ao handler); comentário e mensagem da tela corrigidos | `WorkoutPlanTemplateBuilderClient.tsx` L732; `workout_plan_templates.py` L2119 |
| #184 | `planned_ramp_rpe` com critério próprio | Corrigido: usa `is_rpe_ramp` | `next_set_load.py` L871-880 |
| #184 | Teste de `SetLoggerCard` frágil | O teste citado não existia; teste novo prova que o RPE realizado não nasce com o alvo | `SetLoggerCard.test.ts` L74 |
| #184 | `test_uniforme…` sem ida e volta | Corrigido: executa a ida e a volta | `test_rpe_alvo_por_serie.py` L264 |
| #184 | Embrulho `_format_rpe` em `effective_intensity` | Corrigido: removido; `clamp_rpe_text` usa `format_rpe_step` | `effective_intensity.py` L532-561 |

## Trade-offs

- Ganha: a geração paga não se perde por um degrau fora da escala; o job registra o que foi corrigido e o valor de origem, então a frequência pode ser medida.
- Ganha: a prescrição da ficha deixa de depender de o cliente reenviar os cinco campos; a lista por série é validada contra o que está gravado.
- Ganha: um critério de rampa e um formatador, ambos no dono; a tela não arredonda nem recorta RPE.
- Paga: a correção silenciosa para o valor mais baixo (empate para baixo) muda o alvo que a IA pediu. O relatório registra a diferença, mas o usuário vê o valor corrigido.
- Paga: o bloqueio de segurança é reconhecido pelo texto do servidor, o que acopla a tela ao prefixo da mensagem.
- Paga: o RIR em texto de RPE ainda é lido como degrau (ver Limites, c).

## Consequências

- **Dados.** Sem migração. Nenhum dado gravado muda no deploy. O resultado do job de periodização ganha `rpe_targets_by_set` (aditivo).
- **Contrato.** `PUT /workout-plans/{id}` (ficha): campos de prescrição não enviados em linha salva são preservados; lista por série maior que `sets_count` gravado responde 422 `rpe_targets_by_set_exceeds_sets`. O código novo não está no catálogo `workout_plan_edit_rules.py` (ver Limites, e).
- **Operação.** A frequência de ajustes de RPE sai de `template_generation_logs.validation_report` (`auto_fixes` com `RPE_SANITIZED`). Não há painel.
- **Próximas decisões.** Novo limite ou passo de RPE entra em `training_history.py` (`RPE_TARGET_STEP`, `rpe_target_scale`) e no espelho gerado; a tela e a IA não copiam número. Mudar a regra de empate ou endurecer o parser (RIR) é decisão de motor, com replay dos casos com RIR.

## Limites e pendências

- (a) O bloqueio de segurança é reconhecido pelo começo do texto devolvido pelo servidor (`SAFETY_PREFIX` em `template-save-error.ts` L14; texto em `workout_plan_templates.py` L1290). Não há código estruturado para isso.
- (b) O rascunho do Modelo (`PUT /workout-plan-templates/{id}/draft`) continua recusando RPE legado fora da escala, porque a validação roda no parse do corpo (`WorkoutPlanTemplateIn`).
- (c) RIR em texto de RPE ainda é lido como degrau (`parse_rpe_ramp`; shortcut em `training_history.py` L184).
- (d) Os editores da ficha (`ExercisesTable.tsx` e `PlanExercisesEditor.tsx`) não têm teste de renderização.
- (e) O código `rpe_targets_by_set_exceeds_sets` não está catalogado em `api/app/workout_plan_edit_rules.py`; ele é levantado em `services.py` L3222-3226.
- (f) As telas de detalhe da ficha (`frontend/src/app/(app)/planos-treino/[id]/PlanDetailClient.tsx`, `errorMessage` L59-61, e `frontend/src/mobile/screens/telas/PlanoDetalheScreen.tsx`, L42-44) mostram só a mensagem genérica quando o servidor responde 422 com `detail` em lista (erro de validação do Pydantic) ou em objeto (`{reason, message}`, como `rpe_targets_by_set_exceeds_sets` em `workout_plans.py` L92). Só `detail` em string aparece na tela (`api.ts` L198-199). O campo de RPE por série não dispara mais o 422 de lista por série, porque o número de campos vem de `sets_count` (`RpePerSetField.tsx` L35); outros erros de salvar da ficha continuam sem o motivo. Pendência.

## Verificação

- Suíte unitária inteira do backend, local: 17.862 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Frontend: 301 testes Vitest dos diretórios tocados passaram; `tsc --noEmit` sem erro.
- Revisão independente em duas rodadas: a primeira achou que o campo de RPE por série podia enviar valor fora da escala e que a ficha não mostraria o motivo da recusa do servidor; o campo foi corrigido.
- Verificação dirigida: guarda dos motores, testes de backend e de frontend do lote, `ruff` e `tsc`.
- Não verificado: nenhuma tela foi vista em navegador nem em iPhone (em especial a largura da coluna de RPE na tabela da ficha com vários campos por série); o SELECT com as colunas novas contra o MySQL real (integração no CI); a periodização por IA com resposta real do modelo.

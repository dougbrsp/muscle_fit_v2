# ADR 0013: RPE esperado por série no Modelo de treino (issue #146)

- **Status**: Aceita (código da entrega na branch `feat/146-rpe-esperado-por-serie`; PR e promoção ainda não feitos; não promovida a produção).
- **Data**: 2026-11-15

## Contexto

A issue #146 pede RPE esperado por série no Modelo de treino. Antes da entrega:

- o `rpe_target` já era um texto por exercício, lido como rampa por série (`training_history.parse_rpe_ramp`, `"8-9-10"` = 8, 9 e 10 nas séries 1, 2 e 3), mas sem validação na gravação e sem edição por série na tela;
- o Modo de treino pré-preenchia o RPE realizado com o primeiro número do alvo.

O código atual já tem a entrega: a validação e a composição da rampa moram em `api/app/domain/training_history.py` (`normalize_rpe_target`, `compose_rpe_target`, `RpeTargetError`); o Modo de treino recebe o esperado pronto (`PlanExecutionScreen.tsx`, prop `expectedRpe` de `SetLoggerCard.tsx`) e o RPE inicial do campo de RPE realizado é escolhido por `initialRpe` (`workout-mode-execution-view.ts`), conforme a decisão (f).

Restrições reais:

- o motor de carga da próxima série e a leitura da ficha já leem a rampa em texto (`next_set_load.planned_ramp_rpe`, `prescription.parse_rpe`); uma segunda fonte de verdade para as séries planejadas criaria divergência;
- a ficha congela o `rpe_target` no snapshot (`prescription_json`), e fichas já geradas não podem mudar quando o Modelo muda;
- os motores são a única fonte de regra (`docs/motores/FONTES_DA_VERDADE.md`, guarda em `api/tests/unit/test_guarda_fontes_da_verdade.py`): frontend e API não podem ter regra própria de escala nem de rampa.

## Alternativas consideradas

- **Tabela própria de séries planejadas** (uma linha por série com o alvo). Descartada: exige migração e cria uma segunda fonte ao lado da rampa que os motores já leem. A decisão (a) evita as duas coisas.
- **Aceitar qualquer texto na gravação e validar só na leitura.** Descartada: a entrada da API é fronteira de confiança; um valor inválido gravado no Modelo passaria a ser lido pelo motor sem aviso. A leitura de dado legado continua tolerante (decisão b).
- **Validar a escala no frontend e montar a rampa na tela.** Descartada pela regra dos motores: o frontend consome o valor pronto e o espelho gerado (`motor-constants.generated.json`, chave `rpe_target_scale`), sem regra própria.
- **Continuar pré-preenchendo o RPE realizado com o alvo.** Descartada na primeira versão (PR #181), porque confunde o que se planejou com o que foi feito; adotada na revisão de 2026-11-16, com o esperado da série à frente da cadeia anterior (decisão f).

## Decisão

a. **A rampa em texto continua sendo o armazenamento canônico.** Não há tabela de séries planejadas e não há migração. A API expande a rampa em lista por série só na leitura e na entrada, pelo dono.

b. **Escala 1 a 10 em passos de 0,5, validada na escrita pelo dono.** `normalize_rpe_target` (entrada única) e `compose_rpe_target` (lista por série) recusam valor sem número, fora da escala ou fora do passo, com `RpeTargetError` (422 na API). Mínimo e máximo vêm de `next_set_load.RULE_RPE_MIN` e `training_safety.FAILURE_RPE`; o passo é `RPE_TARGET_STEP`. A leitura de dado legado ilegível não quebra: o validador só roda quando o campo é enviado na escrita (`models.py`, `_resolve_rpe_target`).

c. **A API aceita e devolve `rpe_targets_by_set`.** Na entrada, `WorkoutPlanExerciseIn` e `WorkoutPlanTemplateSlotItemIn` (`models.py`) aceitam a lista e a compõem em `rpe_target`; lista maior que `sets_count` é erro. Na saída, o Modelo (`workout_plan_templates.py`) e a ficha (`services.py`) devolvem `rpe_targets_by_set`. A ficha exibida devolve também `prescription.charged_rpe_targets_by_set`: o esperado de cada série já cortado pelo teto de hoje (`effective_intensity.charged_rpe_targets_by_set`).
   Item sem número de séries não tem os degraus repetidos do fim colapsados na escrita (`collapse_tail=False`), porque o tamanho da rampa define as séries para o dono de intensidade/densidade (`intensity_density.item_intensity`); com número de séries definido o texto é colapsado.
   A lista derivada `rpe_targets_by_set` existe só na resposta da API e nunca realimenta um schema de entrada: duplicar, reverter versão e abrir rascunho a partir da versão vigente copiam o `rpe_target` gravado como está, sem revalidar (`workout_plan_templates._slot_in_as_stored`), porque dado já gravado não é entrada nova. Na saída do item do Modelo a lista vem de `training_history.template_item_rpe_targets`: com número de séries definido, uma posição por série (último degrau repetido; rampa mais longa que as séries sai truncada na lista, mas o texto gravado fica inteiro); com número de séries nulo, a rampa gravada como está.
   As respostas que devolvem rascunho do Modelo ao editor (`get_template_draft` e a resposta de `save_template_draft`) também trazem `rpe_targets_by_set`, preenchido só na saída por `workout_plan_templates.decorate_draft_payload` (cópia, sem alterar o payload persistido). `draft_payload_from_template` e `apply_cardio_sessions_to_draft` não são decorados, porque regravam no banco.

d. **Nenhuma regra do motor mudou.** A decisão D4 de 2026-09-18 é mantida: a carga da ficha mira o último degrau (`prescription.parse_rpe` devolve `ramp[-1]`, e a progressão cobra a última série de trabalho; `progression_engine.py`, comentário "so nas series cobradas (por padrao, a ultima `working`)"). A regra da próxima série usa o degrau da própria série (`next_set_load.planned_ramp_rpe`).

e. **O rascunho do Modo B preserva a rampa.** `effective_intensity.draft_rpe_target` mantém a rampa do Modelo quando `training_history.is_rpe_ramp` (dois ou mais degraus diferentes) é verdadeiro, cortada pelo alvo efetivo do motor com `effective_intensity.clamp_rpe_text`. `generation/mode_b.py` só chama essa função. Sem rampa, continua valendo o alvo do motor, como antes. Antes, o rascunho recebia um inteiro único.

f. **O Modo de treino mostra o RPE esperado e o usa como valor inicial do campo de RPE realizado.** O esperado da série aparece como "RPE esperado: N" (`workout-mode-execution-view.ts`, `expectedRpeLine`), separado do campo. O valor inicial do campo é escolhido por `initialRpe`, nesta ordem: o RPE esperado da série (`prescription.charged_rpe_targets_by_set`, já cortado pelo teto no backend); no modo avançado, o alvo efetivo prescrito; a sugestão da IA (`suggested_next_rpe`); o RPE da última série da sessão; e o alvo numérico do exercício. O valor inicial escolhido passa pelo teto exibido (`clampRpeSuggestion`), venha de onde vier. Séries de técnica e demais cards usam a mesma cadeia, sem o esperado da série; o modo livre usa a cadeia anterior ao PR #181. A função só escolhe entre valores prontos. O campo só nasce vazio quando não há esperado, prescrito, sugestão da IA, série anterior nem alvo numérico. Revisada em 2026-11-16 a pedido do usuário, após o teste do PR #181 no DEV: a versão original desta decisão (e a issue #146) pediam que o realizado não fosse preenchido com o esperado.

g. **O editor do Modelo só envia a lista quando o usuário editou o RPE do item.** `rpe_edited` em `lib/workout-plan-template-form.ts` marca a edição; item não editado reenvia o texto recebido. Motivo: reenviar a lista truncada regravaria "7-8-9" de um item de 2 séries como "7-8", e a carga da ficha, que mira o último degrau, mudaria de 9 para 8 sem o usuário mexer. O painel de Intensidade × Densidade do editor lê a lista `rpe_targets_by_set` do item editado (`intensity-density.ts`, `itemIntensity`), sem compor texto na tela.
   Quando o editor recebe um item com `rpe_target` em texto e sem a lista (ex.: semente da periodização por IA, guardada no navegador), mostra "Alvo gravado: <texto>" e só abre os campos por série depois da ação "Definir por série", com aviso de que o alvo gravado será substituído (`storedRpeTextOnly` em `lib/workout-plan-template-form.ts`; prop `storedText` de `RpePerSetField.tsx`). A tela não interpreta o texto.

## Como o alvo se aplica

Confirmado no código:

- **Séries preparatórias e aplicações de técnica não têm alvo.** `effective_intensity.set_intensity_reading` devolve `status` `None` quando a série não é de trabalho (`is_working_set`). A lista de alvos cobre só as séries de trabalho de `sets_count`: `expand_rpe_targets` e `charged_rpe_targets_by_set` geram uma posição por série de trabalho, e a série de aquecimento ou de técnica não entra na contagem.
- **Série unilateral tem um alvo único por série, não por lado.** O alvo é um número por série. Os lados (E/D) registram o RPE realizado de cada lado (`domain/set_sides.py`, `rpe_left`/`rpe_right`); o oficial da série é o maior dos lados (`set_sides.canonical_rpe`). O Modo de treino inicia os dois lados com o mesmo valor inicial do campo (`SetLoggerCard.tsx`, `sides`), que pode ser o RPE esperado da série (decisão f).
- **Séries por tempo e de máximo de repetições têm o mesmo alvo de RPE.** A medida só tira a posição na faixa de repetições: `set_intensity_reading` devolve `reps_position` `None` quando `is_rep_based` é falso, e o status pelo RPE continua o mesmo.
- **Por série na sessão, por último na progressão.** O status de cada série é contra o degrau dela (`set_intensity_reading`, com `planned_rpe` de `planned_ramp_rpe`). A decisão de progressão continua olhando só a última série de trabalho (D4). Por isso a ficha exibe um esperado por série, mas a decisão de carga não muda.

## Snapshot

- A ficha congela o `rpe_target` no snapshot. Os modos de geração montam o dicionário com a chave `rpe_target` (`generation/freeze_mode_a.py`, linha 145; `generation/freeze_mode_b.py`, linha 446), e o `UPDATE` de `workout_plan_exercises.prescription_json` está em `services.py` (por volta da linha 3386).
- A sessão lê o snapshot antes da coluna: `effective_intensity.plan_rpe_target` devolve `prescription_json.rpe_target` e só cai na coluna `workout_plan_exercises.rpe_target` quando o snapshot não tem o campo. A leitura da sessão passa por ele em `workout_sessions.py` (por volta da linha 976).
- Alterar o Modelo depois não muda fichas já geradas: o snapshot é o que a sessão lê.

## Impacto em produção

Medido pelo Tech Lead em 2026-11-15, por leitura de produção (não reexecutada por este documento):

- todos os valores distintos de `rpe_target` gravados em itens de Modelo e exercícios de ficha estão dentro da escala nova (nenhum seria recusado ao salvar de novo);
- há 56 itens de Modelo com dois degraus (`6-8` em 31, `8-10` em 15, `9-10` em 10). O editor novo passa a mostrá-los expandidos por série; a leitura do motor para eles não muda.

Segunda medição, do Tech Lead em 2026-11-15, por leitura de produção na tabela `workout_plan_exercises` (717 linhas):

- 444 linhas têm snapshot, e todas têm `rpe_target` dentro dele: a sessão lê o snapshot, então o valor novo da coluna do rascunho do Modo B não chega a nenhuma decisão de carga dessas fichas;
- 273 são fichas antigas sem snapshot: leem a coluna, e esta entrega não as regrava;
- 24 têm snapshot diferente da coluna, todas com número único (`10` no snapshot e `9` em 14 ou `8` em 10 na coluna, corte de segurança); nenhuma tem rampa no snapshot com inteiro na coluna, que é o caso que a mudança do Modo B altera.

Conclusão: a mudança do Modo B afeta só o valor exibido e gravado na coluna de rascunhos novos de Modelos com rampa. Uma ficha NOVA gerada pelo Modo B a partir de Modelo com rampa passa a ter a rampa também na coluna do rascunho; na sessão, o degrau por série já era lido do snapshot (`planned_ramp_rpe`), então a regra aplicada é a D4 existente, sem regra nova.

O RPE realizado gravado alimenta a progressão, a recuperação e a regra da carga da próxima série. Com o campo nascendo igual ao esperado, uma série aceita sem ajuste grava o planejado como realizado, o que tende a aproximar o realizado do planejado. Em produção (onde o PR #181 não foi promovido até 2026-11-16) o campo já nascia preenchido pelo alvo; o que muda em relação à produção é o valor usado: o degrau da série (esperado por série, cortado pelo teto) no lugar do primeiro número do alvo. Isso não foi medido.

Nenhuma regra, status, carga ou contagem de motor muda por esta decisão (a frase fala das regras do motor; o dado de entrada, o RPE realizado, pode mudar pelo motivo do parágrafo anterior). Mudam a apresentação e o rascunho: o editor do Modelo mostra a rampa por série; o rascunho do Modo B passa a manter a rampa do Modelo cortada pelo alvo, em vez de um inteiro único (decisão e); o Modo de treino mostra o esperado e o usa como valor inicial do campo de RPE realizado (decisão f).

## Trade-offs

- Ganha: a rampa continua sendo a única fonte de verdade para os motores; não há migração nem tabela nova; o dado inválido é recusado na entrada, com mensagem em português.
- Paga: a rampa em texto continua exigindo o parser único para ser lida (`parse_rpe_ramp`); quem precisar de consulta SQL por série tem de parsear o texto, ou esperar uma decisão nova.
- Paga: o esperado por série é uma informação a mais na tela de execução; o campo de RPE realizado nasce com o valor planejado, então uma série registrada sem ajuste grava o esperado como realizado. Quando o realizado difere do planejado, o atleta precisa ajustar o campo; o sistema não distingue um valor aceito sem ajuste de um valor digitado.

## Consequências

- A guarda não ganha padrão novo nem allowlist nova. A linha do mapa (`docs/motores/FONTES_DA_VERDADE.md`, §3.1, "RPE-alvo por série (#146)") registra o dono, os consumidores e o proibido; a coluna da guarda não nomeia padrão novo, e a allowlist de `rpe_texto_paralelo` segue vazia.
- O espelho `frontend/src/lib/motor-constants.generated.json` (chave `rpe_target_scale`) é regenerado pelo teste de paridade `api/tests/unit/test_m76_lote1b_espelhos_fe.py` (`python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`) quando a escala muda.
- Testes que cobrem a entrega: `api/tests/unit/test_rpe_alvo_por_serie.py`, `frontend/src/app/(app)/modelos-treino/[id]/RpePerSetField.test.ts`, `frontend/src/lib/workout-plan-template-form.test.ts`, `frontend/src/lib/workout-mode-execution-view.test.ts` e `frontend/src/app/(fullscreen)/modo-treino/SetLoggerCard.test.ts`.
- Qualquer tela nova que mostre RPE alvo deve ler `rpe_targets_by_set` ou `charged_rpe_targets_by_set` prontos; calcular o degrau na tela é regra fora do dono.

## Limites conhecidos

- A normalização lê todo número do texto ("7-8 (RIR 2)" vira "7-8-2"), como o parser já fazia; não foi endurecida nesta entrega (comentário `shortcut` em `normalize_rpe_target`).
- A periodização por IA usa o mesmo schema do Modelo (`periodization/validation.py`, `_to_template_in`, antes dos cortes `clamp_rpe_text`): um `rpe_target` da LLM fora da escala ou do passo termina o job como `failed` com relatório `SCHEMA_INVALID`, sem exceção, em vez de ser cortado. Saneamento desse caso fica para uma entrega seguinte.
- O resultado do job de periodização por IA (`api/app/periodization/core.py`, retorno de `run_periodization_draft`, devolvido por `jobs.py`) não traz `rpe_targets_by_set`; o editor cobre esse caso pela decisão (g). Preencher a lista nessa saída e sanear o RPE da LLM antes do schema ficam para a mesma entrega seguinte.
- O critério "dois degraus diferentes" também está escrito em `next_set_load.planned_ramp_rpe`, com a mesma semântica de `is_rpe_ramp`; unificar quando esse arquivo for tocado.
- O erro 422 de validação aparece no editor do Modelo pela notificação geral do salvamento, sem mensagem por campo.
- Resíduo do fallback `parseNumericTarget`: `parseNumericTarget(effectiveRpeTarget)` (`PlanExecutionScreen.tsx`) e `parseNumericTarget(suggested?.rpe_target)` (`FreeformScreen.tsx`) leem o primeiro número do alvo em texto como último recurso, quando o par foi trocado na sessão ou não há prescrição; comportamento anterior ao PR #181, restaurado a pedido do usuário em 2026-11-16; sai quando o backend expuser o alvo numérico pronto para esses casos.

## Fora desta entrega

1. Comparação esperado × realizado em histórico e relatórios (a sessão ainda não grava o alvo planejado na série).
2. Gravar o alvo planejado na própria série da sessão.
3. Edição por série nos editores da ficha (`workout_plan_exercises`); a edição por série é só no Modelo.

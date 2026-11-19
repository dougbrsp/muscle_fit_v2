# ADR 0021: Continuação por troca de equipamento cria exercício próprio (issues #225 e #204)

- **Status**: Aceita. Branch `fix/continuacao-equipamento-225-204`, sem commit no checkout no momento da redação. Nada foi publicado nem promovido a produção.
- **Data**: 2026-11-19
- **Complementa**: ADR 0020 (marca de técnica até a 1ª série de trabalho; ressalva na decisão 5 abaixo). Nenhuma decisão anterior é substituída; o 0020 não é reescrito.

## Contexto

Na revisão do lote do ADR 0020 apareceu um defeito que já existia antes dele (issue #225). No Modo Treino pela ficha, a "continuação" é o exercício que nasce quando o usuário troca de equipamento no meio de um exercício, depois de já ter séries gravadas. Ela é um item sintético (id negativo, `isSyntheticPlanExerciseId` em `frontend/src/lib/workout-mode-types.ts`) que aponta para o item original da ficha.

O problema tinha duas partes:

1. **Chave de idempotência.** A criação do exercício usava a mesma `client_key` do item original. O servidor deduplica por `(activity_id, client_key)` (`api/app/workout_sessions.py::add_session_exercise`, com `_find_exercise_by_client_key`). Resultado:
   - com o mesmo exercício de catálogo, as séries do equipamento novo eram gravadas na linha do exercício original, ou seja, sob o equipamento antigo;
   - com outro exercício de catálogo, a criação devolvia a linha original (`duplicate`) e as séries batiam em conflito de numeração (409).
2. **Resolução da linha.** `findLoggedExerciseForPlanItem` (`workout-mode-types.ts`) casava a continuação pelo `mapping_id`, o que também devolvia a linha original. Por isso a marca de "Aprimoramento de técnica" enviada na criação da continuação era ignorada.

Consequência para o usuário: histórico, recorde, e1RM e progressão por equipamento misturavam os dois equipamentos.

Em paralelo, a issue #204 registrava sobras de teste do cadastro de equipamento e a origem do "erro de API" relatado na #186.

Restrição que moldou a decisão: `frontend/src/lib/workout-mode-core.golden.json` é contrato compartilhado com o aplicativo do relógio (cabeçalho de `frontend/src/lib/workout-mode-core.test.ts`). A fábrica `buildSyntheticContinuation` é verificada contra esse golden, então não pode ganhar campos novos.

## Alternativas consideradas

- **Corrigir no servidor (criar linha nova por `mapping_id` ou por item).** Descartada: o servidor já cria linha própria quando a chave é nova (`add_session_exercise`). O erro está na chave enviada pelo cliente, não na regra do servidor.
- **Chave de continuação por `mapping_id`.** Descartada: repete a colisão quando o exercício de catálogo é o mesmo.
- **Guardar o `exerciseId` dentro de `buildSyntheticContinuation`.** Descartada: a fábrica está presa ao golden do relógio. O campo nulo nasce na tela (`handleSubstituteSelect`).
- **Manter a resolução por `mapping_id` para todas as continuações.** Descartada: é exatamente o que mistura os equipamentos. Fica só como fallback para continuações antigas (ver decisão 2).
- **Corrigir os dados já gravados.** Descartada: não há como identificar as séries gravadas sob o equipamento errado (ver "Itens descartados").

## Decisão

1. **Chave da criação do exercício por execução.** `frontend/src/lib/workout-mode-exercise-key.ts::exerciseKeySlot` monta o slot `sessão#id`: o id do item real da ficha, ou, na continuação, o id sintético dela. Item sem ficha continua usando o nome. É usada em `frontend/src/app/(fullscreen)/modo-treino/PlanExecutionScreen.tsx::ensureExerciseMutation`. O corpo da criação continua levando `workout_plan_exercise_id` do item ORIGINAL.
2. **A continuação guarda o exercício que criou.** `MidExerciseExtra.exerciseId` (`workout-mode-types.ts`) é o id real da linha criada; nulo significa ainda sem linha; campo ausente significa continuação anterior à correção. Para o campo ausente, `findLoggedExerciseForPlanItem` mantém a resolução antiga por `mapping_id`, de propósito, para não quebrar um treino em andamento na atualização do aplicativo. Para o campo presente, a linha é a que a continuação criou, nunca a do original.
3. **Quem grava o id.** `withContinuationExerciseId` (criação confirmada) e `withQueueConfirmedContinuationIds` (fila offline sincronizada), ambas em `frontend/src/lib/workout-mode-plan-flow.ts`.
4. **O campo nulo nasce na tela.** Em `PlanExecutionScreen.tsx::handleSubstituteSelect`, e não em `buildSyntheticContinuation`, para preservar o contrato do golden.
5. **Servidor sem mudança.** `add_session_exercise` já cria linha própria para chave nova. Coberto por `api/tests/unit/test_client_key_sessao_idempotente.py::test_continuacao_mesmo_item_da_ficha_com_chave_nova_cria_linha_propria`, que mostra a mesma `workout_plan_exercise_id` (77) em duas linhas com `mapping_id` e chaves diferentes.
6. **Marca de técnica na continuação.** Com a continuação criando exercício próprio, desmarcar a continuação antes da 1ª série de trabalho passa a gravar a continuação desmarcada. `frontend/src/lib/form-practice.ts::reconcileMarkAfterResponse` decide o estado após a resposta de uma série em voo: se o usuário mexeu na marca durante o envio e o servidor ainda não travou, vale a escolha mais recente (vai no próximo POST); travado, vale o servidor.
7. **Ressalva ao ADR 0020.** O 0020 diz que a tela "só lê" a decisão de trava. Essa afirmação vale com duas exceções, já registradas no mapa `docs/motores/FONTES_DA_VERDADE.md`: o fallback do resolvedor e a previsão local ao enfileirar uma série de trabalho offline.
8. **#204.** Os testes do cadastro de equipamento (`api/tests/unit/test_gyms_equipamento_nome_duplicado.py`) verificam que o INSERT do cadastro não contém `ON DUPLICATE` (`api/app/gyms.py` ainda usa `ON DUPLICATE KEY UPDATE` em outras rotas, como as de `gym_equipment`). A mensagem de erro da tela é extraída para `frontend/src/lib/equipment-save-error.ts::equipmentSaveErrorMessage`, usada em `frontend/src/app/(app)/equipamentos-exercicios/EquipamentosExerciciosClient.tsx`, com teste em `equipment-save-error.test.ts` (409 exibe a mensagem do servidor).
9. **Continuação criada offline.** Enquanto a criação da continuação está só na fila, `MidExerciseExtra.exerciseId` fica nulo e `frontend/src/lib/workout-mode-plan-flow.ts::queuedContinuationExerciseIds` monta os candidatos da linha (o id real da resposta da operação, se houver, e o id local da operação); `findLoggedExerciseForPlanItem` (parâmetro `queuedIds`, `workout-mode-types.ts`) usa esses candidatos para achar a linha da fila, de modo que as séries enfileiradas na continuação seguem a numeração 1, 2, 3. O id local não é persistido, o id real persistido tem prioridade, a continuação anterior à correção (campo ausente) não muda, e continuação nova nunca é resolvida por `mapping_id`.

## Trade-offs

- Ganha: a linha de cada equipamento é própria no servidor; histórico, recorde, e1RM e progressão deixam de misturar os equipamentos a partir do deploy.
- Ganha: a correção não muda o servidor nem o contrato do golden do relógio, e não exige migração.
- Paga: a resolução por `mapping_id` continua no código para continuações anteriores à correção. É uma segunda forma de casar a linha, mantida até que nenhum treino em andamento dependa dela.
- Paga: a tela tem mais estado (`exerciseId` em três pontos: criação, fila offline e reconciliação), e a parte da fila offline na tela não tem teste de componente (ver "Limites conhecidos").

## Consequências

- **Dados.** Sem migração; nenhum dado gravado muda no deploy. Medição de produção feita por leitura, só SELECT, em 19/11/2026: a `client_key` do exercício aparece desde 03/11/2026; desde então, 13 sessões, 132 exercícios, 124 com `client_key` e item da ficha. Pares (sessão, item da ficha) com mais de uma linha de exercício: 4, todos com `client_key` nula, o mais recente de 02/11/2026; nenhum depois de 03/11/2026.
- **Operação.** A partir do deploy, a troca de equipamento no meio do exercício grava as séries do equipamento novo numa linha própria, com o `equipment_id` novo.
- **Próximas decisões.** Qualquer nova forma de continuação deve dar ao exercício criado uma chave própria e registrar o id criado no extra; não usar `mapping_id` para casar linha. Quando o fallback por `mapping_id` puder ser removido, a remoção fica para um ADR próprio.
- **Mapa.** O mapa `docs/motores/FONTES_DA_VERDADE.md` registra o lote (itens (a), (b) e (c) do impacto em produção); este ADR não o altera.

## Itens descartados ou não resolvidos, com o motivo

- **Séries gravadas sob o equipamento errado entre 03/11/2026 e o deploy.** Não há como identificá-las (ficaram na linha do exercício original, sem marca que as distinga) nem como contar quantas trocas de equipamento no meio do exercício ocorreram. Nenhuma correção de dado é feita.
- **Origem do "erro de API" da #186.** Foram examinados a mutation de salvar, as queries refeitas depois do salvamento, o fluxo de foto e as rotas de equipamento em `api/app/gyms.py`. Não há chamada encadeada ao salvamento nem caminho de erro identificado no código atual. Hipótese não verificada: o erro vinha do próprio caminho de nome duplicado antigo. Item encerrado sem mudança de código.
- **409 de nome duplicado na tela real.** Provado por teste (`apiFetch` real com `fetch` simulado e a função da mensagem), não em navegador.
- **Cadeia de continuações (original → continuação 1 → continuação 2).** A marca de técnica proposta para a continuação 2 é herdada da primeira linha do item, não da continuação 1. Só afeta a marca inicial proposta, que o usuário pode mudar.

## Verificação

- Testes unitários: `api/tests/unit/test_client_key_sessao_idempotente.py`, `api/tests/unit/test_gyms_equipamento_nome_duplicado.py`, e os `.test.ts` de `frontend/src/lib` citados acima.
- Ponta a ponta do Modo Treino (`frontend/e2e/workout-resilience`, Playwright, projeto `chromium-390`, build estático com API falsa): 23 passaram, 0 falharam. Não rodam no CI. Na primeira execução houve 5 falhas, todas por cenário desatualizado; só os cenários foram corrigidos.
- Provado em navegador: a troca de equipamento no meio do exercício gera um segundo POST de criação com chave diferente, sem duplicata, no mesmo item da ficha; a série 1 da continuação vai para o exercício novo, sem 409; a marca de técnica na apresentação vai na criação (`form_practice`) e no envio da série (`exercise_form_practice`). Cenário offline da continuação (`workout-resilience.spec.ts`, teste "troca de equipamento (#225) com a continuacao criada e registrada OFFLINE"): série 1 online no exercício original; troca de equipamento; duas séries offline na continuação; ao voltar a rede, a criação da continuação sai com chave diferente, o exercício novo recebe as séries 1 e 2, o original fica só com a série 1, e nenhum 409.

## Limites conhecidos

- Nenhuma verificação em iPhone real.
- Os e2e rodaram só no projeto `chromium-390` e não rodam no CI.
- Treino em andamento na hora da atualização do aplicativo segue o comportamento antigo para as continuações já criadas (conferido por leitura e por teste de função pura).
- O efeito da fila offline na tela e a lógica de `postSet` não têm teste de componente; as decisões estão em funções puras testadas.

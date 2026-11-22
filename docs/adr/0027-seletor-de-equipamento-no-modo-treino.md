# ADR 0027: Seletor de equipamento no Modo Treino, validação do equipamento da sessão e convenção de carga nas opções (issue #163, primeira parte)

- **Status**: Aceita. Branch `feat/seletor-equipamento-163a`. Não publicada nem promovida a produção. A segunda parte da #163 (catálogo global e permissão para acrescentar equipamento à academia) não está neste ADR.
- **Data**: 2026-11-20
- **Complementa**: ADR 0021 (continuação por troca de equipamento cria exercício próprio). Nenhuma decisão anterior é substituída.

## Contexto

A issue #163 pede que o usuário troque o equipamento de um exercício durante o treino, escolhendo entre os equipamentos da academia do treino, sem depender do Registro para corrigir. Antes deste lote:

- a troca de equipamento antes da 1ª série já existia e ficava guardada no aparelho, sem ir ao servidor. A continuação da troca depois de séries lançadas segue o ADR 0021;
- o histórico e a carga de partida já eram separados por equipamento (#137, `domain/prescription_service.py`, linha "Carga de partida e 'última vez' do par por equipamento" em `docs/motores/FONTES_DA_VERDADE.md`).

O modelo de dados que a entrega usa:

- `equipment`: catálogo global de equipamentos (`api/db/migrations/001_schema.sql:178`). Não há a noção de equipamento "público": o catálogo inteiro é global.
- `equipment_exercises`: vínculo equipamento × exercício, com a convenção de carga (`load_convention`; `api/db/migrations/002_reestruturacao.sql:120`, leitura pelo dono `api/app/load_conventions.py`).
- `gym_equipment_catalog`: o que cada academia tem (`api/db/migrations/001_schema.sql:270`).
- `gym_equipment`: cache academia × exercício × equipamento (`api/db/migrations/001_schema.sql:194`). `exercises.equipment_id` aponta para ele (`api/app/workout_sessions.py:668`, `e.equipment_id AS gym_equipment_id`).

Restrições reais:

- o `equipment_id` que o Modo Treino envia é entrada do cliente, então é fronteira de confiança. Antes deste lote o servidor aceitava qualquer id que existisse, sem conferir academia nem coerência com o exercício;
- a coerência do par exercício × equipamento é regra do dono (`api/app/domain/equipment_pair.py::is_pair_coherent`, L102), e a rota não pode decidi-la por conta própria;
- a convenção de carga (`assisted`, `per_side`, `total` etc.) muda o sentido do número registrado. Escolher um aparelho com convenção diferente muda a carga que será gravada, então o usuário precisa saber disso antes de confirmar;
- a regra dos motores (`docs/motores/FONTES_DA_VERDADE.md`; guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`) exige que a validação e a convenção fiquem no dono, e que as telas só exibam o valor pronto.

## Alternativas consideradas

- **Guardar a troca no servidor antes da 1ª série.** Recusada: o que ainda não foi treinado não precisa de registro no servidor, e a troca antes da 1ª série já funcionava no aparelho. Uma gravação nova criaria estado a sincronizar sem ganho para o usuário.
- **Troca retroativa no Modo Treino** (trocar o equipamento das séries já lançadas dentro do treino). Recusada: a correção das séries já lançadas fica pelo Registro, com confirmação (decisão D3).
- **Bloquear a troca quando a convenção de carga for diferente.** Recusada: o bloqueio impediria um aparelho válido só por causa da forma de registrar a carga. A decisão do usuário foi avisar e permitir.
- **Recusar associação inativa.** Recusada: uma ficha em andamento pode referenciar uma associação desativada depois de criada. Recusar quebraria treinos já em curso. A associação inativa é aceita e registrada em log.

## Decisão

1. **Validação do equipamento explícito no servidor.** `api/app/workout_sessions.py::ensure_session_equipment(cur, session, mapping_id, equipment_id)` (L914) é chamada em `add_session_exercise` (L962) só quando `equipment_id` vem explícito, e depois da busca por `client_key` (L947-949). Assim, o reenvio idempotente de um exercício já criado não revalida. Regra final, na ordem em que o código a aplica:
   - associação inexistente: HTTP 422 `equipment_not_found` (L922);
   - sessão com academia e associação de outra academia: 422 `equipment_not_in_session_gym` (L927);
   - sessão sem academia: a academia não é conferida, pois não há contra o que comparar; o evento `session_equipment_accepted_without_gym`, só com ids, vai a log (L925);
   - par recusado por `is_pair_coherent`, com ou sem academia: 422 `equipment_pair_incoherent` (L928-929);
   - associação INATIVA: aceita, e o evento `session_equipment_inactive_accepted` vai a log (L930-931).
2. **Dono da coerência.** `api/app/domain/equipment_pair.py::is_pair_coherent` (L102). A rota só chama o predicado e não repete a regra.
3. **Convenção de carga nas opções.** `GET /gym-equipment` (`api/app/gyms.py::list_gym_equipment`, L737) devolve em cada item `load_convention`. A consulta fica no corpo da rota. A expressão e o join vêm do dono (`load_conventions.load_convention_select_expr`, L344, e `equipment_exercises_join_sql`, L181), chamados em `gyms.list_gym_equipment` (L765 e L788). O campo é lido com `row.get`, e o nulo ou ausente é normalizado por `load_conventions.normalize_load_convention` (L71; `gyms.py:818`). `GET /gym-equipment/recommend` e `/recommend-all` não trazem o campo (`api/app/equipment_recommender.py` não lê a convenção).
4. **Seletor no Modo Treino (web e celular).** `frontend/src/app/(fullscreen)/modo-treino/EquipmentSubstituteModal.tsx` tem busca e as seções "Mesmo exercício" e "Mesmo grupo muscular", montadas por `frontend/src/lib/equipment-picker.ts` (`buildEquipmentPickerSections`, L41; `normalizeSearchText`, L22; `currentLoadConvention`, L65; `needsLoadConventionWarning`, L74). Cada opção mostra a convenção pelo rótulo do dono (`loadConventionLabel`, `frontend/src/lib/equipment-exercises-types.ts:52`). Ao escolher convenção diferente, a tela avisa e permite.
5. **Nome do equipamento tocável.** `frontend/src/app/(fullscreen)/modo-treino/ui/ExerciseTitleRow.tsx` (prop `onEquipmentClick`, L35 e L59-64) é usado na apresentação do exercício (`ExerciseBriefingScreen.tsx:548`) e na tela de registro de série (`SetLoggerCard.tsx:1406`).6. **Confirmação da troca com séries lançadas.** `frontend/src/lib/equipment-change-confirm.ts`: `equipmentChangeConfirmation` (L26), `equipmentChangeForSave` (L43) e `SWAP_REMAINING_NOTE` (L63). `FinishExerciseModal.tsx` (L51) e `EquipmentSubstituteModal.tsx` (L195) mostram a nota `SWAP_REMAINING_NOTE`: as séries já lançadas continuam no equipamento atual, a troca vale só para as séries restantes, e a correção das já lançadas é pelo Registro. `WorkoutDetailClient.tsx` (L408, web) e `TreinoDetalheScreen.tsx` (L242, celular) pedem confirmação ao mudar o equipamento de um exercício com séries.
7. **Histórico e falha da sugestão.** `frontend/src/lib/workout-mode-previous-session.ts::previousSessionLabels` (L196): "Sem histórico neste equipamento" quando há equipamento definido; sem equipamento, o rótulo é "Treino anterior (qualquer equipamento):" com "Ainda não há execução comparável". `frontend/src/lib/workout-mode-plan-flow.ts::targetsWhenSuggestionFails` (L825) e `SUGGESTION_FAILED_MESSAGE` (L816): quando a sugestão do equipamento novo falha, a carga alvo herdada é removida e o usuário é avisado (`PlanExecutionScreen.tsx:2283-2284`).

As quatro decisões do usuário de 20/11/2026, registradas aqui:

- **D1. Catálogo e permissão de acrescentar equipamento: fica para o lote seguinte.** Neste lote o seletor lista só os equipamentos da academia do treino. O catálogo global inteiro e a permissão para acrescentar à academia um equipamento que ela não tem (a princípio só o grupo admin) é a segunda parte da #163, decidida no [ADR 0028](0028-catalogo-global-no-seletor-de-equipamento.md).
- **D2. Troca antes da 1ª série fica no aparelho.** Não vai ao servidor antes da 1ª série. Como já era, e este lote não muda esse caminho.
- **D3. Sem troca retroativa no Modo Treino.** A correção das séries já lançadas é pelo Registro, com confirmação.
- **D4. Convenção de carga diferente: avisar e permitir.**

## Trade-offs

- Ganha: o servidor deixa de aceitar um equipamento de outra academia, inexistente ou incoerente com o exercício, sem nenhuma mudança nos dados já gravados.
- Ganha: a convenção de carga aparece antes da escolha, e o cálculo fica no dono.
- Paga: a validação é mais uma consulta por exercício criado com equipamento explícito.
- Paga: a associação inativa passa, e fica só o log como sinal. Uma associação desativada continua aceita até ser trocada.
- Paga: o seletor depende de a sessão ter academia. Sessão sem academia não tem seletor na tela, e o servidor aceita equipamento existente e coerente sem comparar academia.

## Consequências

- **Dados.** Nenhuma coluna nova e nenhuma migração. Nenhum registro existente muda (ver `docs/motores/FONTES_DA_VERDADE.md`, "Lote 10 de 20/11/2026 (#163, primeira parte)").
- **Contrato.** `POST /workout-sessions/{id}/exercises` pode responder 422 com os três códigos novos. `GET /gym-equipment` ganha `load_convention` por item (campo aditivo).
- **Medições (20/11/2026).** Em produção, por leitura: 110 atividades, 12 sem academia (nenhuma do Modo Treino); 85 fichas, nenhuma sem academia; nenhuma ficha com equipamento de outra academia; nenhum treino feito em academia diferente da academia da ficha. No banco de desenvolvimento, cópia de produção: 67 exercícios com par incoerente (49 pares), o mais recente de 19/09/2026, nenhum criado pelo Modo Treino; os 108 criados pelo Modo Treino (desde 03/11/2026) são todos coerentes. Conclusão: a recusa de par incoerente não atinge o uso atual do Modo Treino; os pares incoerentes antigos vieram de importação e edição, que não são validadas. Detalhes no mapa, "Lote 10 de 20/11/2026".
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md` ganha duas linhas na tabela de regras: "Equipamento explícito da sessão no Modo Treino" (logo após a linha "Carga da sessão e adoção pela trilha", antes da linha "Trilha", L119) e "Convenção de carga nas opções de academia" (logo após a convenção de carga). O bloco de impacto do Lote 10 fica depois do bloco do Lote 9.
- **Próximas decisões.** Catálogo global e permissão de acrescentar equipamento (segunda parte da #163). Validação dos outros caminhos que gravam equipamento (importação e Registro), se o usuário quiser.

## Limites e pendências

- (a) O catálogo global e a permissão de acrescentar equipamento à academia ficam para a segunda parte da #163.
- (b) RESOLVIDO em 22/11/2026 (#244): o nome do equipamento agora é tocável no descanso entre séries e no bloco intensivo (ver "Atualização de 22/11/2026"). Restam os limites listados nela.
- (c) Com série já lançada, tocar no nome abre o "Encerrar exercício" (`SetLoggerCard.tsx:1406`, `onSubstituteClick ?? onFinishExercise`), que oferece a troca para as séries restantes.
- (d) `recommend` e `recommend-all` não trazem a convenção de carga.
- (e) Sessão sem academia não tem seletor na tela. O servidor aceita equipamento existente e coerente sem comparar academia e registra em log (`session_equipment_accepted_without_gym`).
- (f) No Registro, o aviso de convenção só aparece quando a convenção do equipamento antigo é conhecida: ele precisa estar na lista do grupo muscular do formulário (`equipment-change-confirm.ts`, L52: sem o vínculo na lista, a convenção vira nula).
- (g) Os outros caminhos que gravam equipamento (importação e Registro, via `services.resolve_equipment_id`) continuam sem a validação do Modo Treino.
- (h) Treino offline cujo equipamento foi REMOVIDO da academia antes da sincronização é recusado com `equipment_not_found`.
- (i) Nenhuma tela vista em celular real.
- (j) Os 67 exercícios antigos com par incoerente (49 pares, medidos no banco de desenvolvimento, cópia de produção) não são corrigidos. A validação vale só para o POST novo.
- (k) O aviso de convenção do seletor (`EquipmentSubstituteModal.tsx`, L316) não recebe foco nem rolagem automática. Se a lista estiver rolada, o aviso pode ficar fora da área visível ao tocar numa opção.
- (l) Sem academia, a lista do seletor não carrega e o seletor mostra a mensagem genérica de lista vazia ("Nenhum outro equipamento cadastrado para este grupo muscular nesta academia.", `EquipmentSubstituteModal.tsx` L399-402).

## Verificação

- Frontend: 454 testes Vitest dos diretórios tocados passaram; `tsc --noEmit` sem erro.
- Testes de ponta a ponta do Modo Treino (`frontend/e2e/workout-resilience`, Playwright, projeto `chromium-390`), executados em navegador fora do CI: 25 passaram, 0 falharam, incluindo a seleção inicial (busca, aviso de convenção, recarga da página e envio do equipamento escolhido na 1ª série) e o equipamento sem histórico.
- Não verificado: nenhuma tela vista em celular real; a confirmação do Registro contra o backend real; o join da convenção de carga contra o MySQL real (integração no CI).
- Suíte unitária inteira do backend, local, depois da última correção: 17.916 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Revisão independente em duas rodadas: a primeira achou três testes existentes quebrados pela mudança em `GET /gym-equipment` e a recusa de equipamento em atividade sem academia; os dois foram corrigidos.

## Atualização de 22/11/2026 (#244, nome tocável no descanso e no bloco intensivo)

**Escolha do usuário (22/11/2026).** O nome do equipamento fica tocável também durante o descanso entre séries e no bloco intensivo, abrindo o mesmo seletor. Isso resolve o limite (b).

**Implementação** (`frontend/src/app/(fullscreen)/modo-treino/`):

- `openEquipmentSwap` (em `PlanExecutionScreen.tsx`) foi extraída; o mesmo corpo estava repetido nos três botões "Trocar". Com série já lançada, vai direto ao seletor em modo de continuação (séries restantes), sem passar pelo "Encerrar exercício".
- `RestEquipmentSwap` (em `ui/ExerciseTitleRow.tsx`, testId `rest-equipment-swap`) é renderizado dentro do `RestTimerHero`.
- No bloco intensivo, `IntensiveBriefing.tsx`, `IntensiveSetEntry.tsx`, `IntensiveRest.tsx` (inclusive no relógio contínuo) e `IntensiveBlock.tsx` usam `EquipmentSwapButton` com testId `intensive-equipment-swap`.
- O botão do nome no título (`EquipmentSwapButton`) deixou o estilo inline por classe em `workout-mode.module.css`.

| Fase | O nome aparece? | O que o toque faz |
|---|---|---|
| Antes da 1ª série (título) | Sim | Abre o seletor (troca limpa) |
| Registro da série, com série lançada | Sim | Abre o "Encerrar exercício" (um passo a mais), que oferece a troca das séries restantes |
| Descanso entre séries do mesmo exercício | Sim, se há equipamento definido | Abre direto o seletor em modo de continuação, se há série lançada |
| Descanso na transição para o próximo exercício | Não | Não se aplica |
| Bloco intensivo (briefing, série, descanso e relógio contínuo) | Sim | Abre o seletor via `openEquipmentSwap` |
| Aplicação da técnica (`IntensiveApplicationCard`) | Não | Segue o ícone de troca existente |

**Verificação.** Vitest da pasta do Modo Treino: 151 passando. Testes de ponta a ponta do Modo Treino sobre build novo: 30 passando, com caso novo de troca pelo nome durante o descanso com série lançada.

**Limites.**

- Na aplicação da técnica intensiva (`IntensiveApplicationCard`) o nome não foi acrescentado; segue o ícone de troca existente.
- Com série lançada, o toque no nome no registro da série continua abrindo "Encerrar exercício", enquanto no descanso e no bloco intensivo vai direto ao seletor. O comportamento difere por fase, como já diferia nos botões "Trocar".
- Depois de trocar durante o descanso, o cronômetro continua e o painel da última série salva ainda é o do exercício anterior até o descanso acabar (lido no código, não medido); o mesmo vale para o botão "Trocar" que já existia.
- A troca durante o descanso do bloco intensivo não foi exercitada por teste de ponta a ponta.
- Nada conferido no navegador nem em celular real.

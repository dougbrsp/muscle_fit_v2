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
   - associação inexistente, ou de outra academia numa sessão com academia: HTTP 422 `equipment_not_in_session_gym`, com a mensagem "Equipamento não disponível nesta academia." (L921-923). Inexistente é recusado também sem academia na sessão. Lote 44 (#284): `equipment_not_found` deixou de existir, para não revelar se o equipamento existe em outra academia;
   - sessão sem academia: a academia não é conferida, pois não há contra o que comparar; o evento `session_equipment_accepted_without_gym`, só com ids, vai a log (L926);
   - par recusado por `is_pair_coherent`, com ou sem academia: 422 `equipment_pair_incoherent` (L927-928);
   - associação INATIVA: aceita, e o evento `session_equipment_inactive_accepted` vai a log (L929-930).
Lote 44 (#284), academia do corpo em Importação e Registro (`services.resolve_active_gym_id`): a academia já gravada na atividade é sempre aceita, mesmo inativa, para que reenvio offline e edição de treino antigo não falhem; o 422 `gym_not_found` vale só para MUDANÇA para academia inexistente ou inativa; nome sem match mantém a gravada, e sem gravada dá 422; a reimportação preserva a academia lida antes do DELETE; a adoção de academia pelo equipamento só vale para academia ativa (`services.validate_explicit_equipment`); o LIKE do nome escapa `\ % _`.
2. **Dono da coerência.** `api/app/domain/equipment_pair.py::is_pair_coherent` (L102). A rota só chama o predicado e não repete a regra.
3. **Convenção de carga nas opções.** `GET /gym-equipment` (`api/app/gyms.py::list_gym_equipment`, L737) devolve em cada item `load_convention`. A consulta fica no corpo da rota. A expressão e o join vêm do dono (`load_conventions.load_convention_select_expr`, L344, e `equipment_exercises_join_sql`, L181), chamados em `gyms.list_gym_equipment` (L765 e L788). O campo é lido com `row.get`, e o nulo ou ausente é normalizado por `load_conventions.normalize_load_convention` (L71; `gyms.py:818`). `GET /gym-equipment/recommend` e `/recommend-all` também trazem o campo desde o lote 32 (ver a atualização de 22/11/2026, #244).
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
- **Contrato.** `POST /workout-sessions/{id}/exercises` pode responder 422 com `equipment_not_in_session_gym` ou `equipment_pair_incoherent` (no Lote 44, `equipment_not_found` foi retirado). `GET /gym-equipment` ganha `load_convention` por item (campo aditivo).
- **Medições (20/11/2026).** Em produção, por leitura: 110 atividades, 12 sem academia (nenhuma do Modo Treino); 85 fichas, nenhuma sem academia; nenhuma ficha com equipamento de outra academia; nenhum treino feito em academia diferente da academia da ficha. No banco de desenvolvimento, cópia de produção: 67 exercícios com par incoerente (49 pares), o mais recente de 19/09/2026, nenhum criado pelo Modo Treino; os 108 criados pelo Modo Treino (desde 03/11/2026) são todos coerentes. Conclusão: a recusa de par incoerente não atinge o uso atual do Modo Treino; os pares incoerentes antigos vieram de importação e edição, que não são validadas. Detalhes no mapa, "Lote 10 de 20/11/2026".
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md` ganha duas linhas na tabela de regras: "Equipamento explícito da sessão no Modo Treino" (logo após a linha "Carga da sessão e adoção pela trilha", antes da linha "Trilha", L119) e "Convenção de carga nas opções de academia" (logo após a convenção de carga). O bloco de impacto do Lote 10 fica depois do bloco do Lote 9.
- **Próximas decisões.** Catálogo global e permissão de acrescentar equipamento (segunda parte da #163). A validação dos outros caminhos (importação e Registro) foi feita no lote 32.

## Limites e pendências

- (a) O catálogo global e a permissão de acrescentar equipamento à academia ficam para a segunda parte da #163.
- (b) RESOLVIDO em 22/11/2026 (#244): o nome do equipamento agora é tocável no descanso entre séries e no bloco intensivo (ver "Atualização de 22/11/2026"). Restam os limites listados nela.
- (c) Com série já lançada, tocar no nome abre o "Encerrar exercício" (`SetLoggerCard.tsx:1406`, `onSubstituteClick ?? onFinishExercise`), que oferece a troca para as séries restantes.
- (d) RESOLVIDO no lote 32: `recommend` e `recommend-all` trazem `load_convention` em cada item.
- (e) Sessão sem academia não tem seletor na tela. O servidor aceita equipamento existente e coerente sem comparar academia e registra em log (`session_equipment_accepted_without_gym`).
- (f) No Registro, o aviso de convenção só aparece quando a convenção do equipamento antigo é conhecida: ele precisa estar na lista do grupo muscular do formulário (`equipment-change-confirm.ts`, L52: sem o vínculo na lista, a convenção vira nula).
- (g) RESOLVIDO no lote 32: importação e Registro validam o equipamento explícito pela mesma função do Modo Treino, com a regra de compatibilidade da atualização. O caminho por nome (`equipment_name`, `resolve_equipment_id`) não é validado.
- (h) Treino offline cujo equipamento foi REMOVIDO da academia antes da sincronização é recusado com `equipment_not_in_session_gym` (Lote 44, #284; antes, `equipment_not_found`).
- (i) Nenhuma tela vista em celular real.
- (j) Os 67 exercícios antigos com par incoerente (49 pares, medidos no banco de desenvolvimento, cópia de produção) não são corrigidos. A validação vale só para o POST novo. RESOLVIDO em 22/11/2026 (#244) por escolha do usuário: 23 são corrigidos pela migração 124 e os outros 44 ficam como registrados (ver "Atualização de 22/11/2026 (#244, correção dos 23 registros antigos)").
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

## Atualização de 22/11/2026 (#244, correção dos 23 registros antigos)

**Escolha do usuário (22/11/2026).** Dos 67 exercícios antigos com par exercício × equipamento incoerente (limite (j)), corrigir só os 23 que têm, na mesma academia, exatamente um vínculo do mesmo aparelho físico com o exercício do registro. Os outros 44 ficam como foram registrados; a pendência está encerrada para eles. Alternativa não adotada: corrigir os 67, o que exigiria escolher aparelho sem evidência para 44 deles.

**Medição em produção** (Tech Lead, só leitura, 22/11/2026): 67 exercícios em 49 pares, um usuário, o mais recente de 19/09/2026, nenhum criado pelo Modo Treino, nenhum com vínculo inexistente. Os 23: de 23/07 a 05/09/2026, academias 1 (20 registros) e 10 (3 registros); 17 apontam para vínculo novo ativo e 6 para vínculo novo inativo (aceitos).

**O que a migração faz** (`api/db/migrations/124_corrige_vinculo_equipamento_de_exercicios_antigos.py`, só dados, molde da 117): para cada um dos 23 (tuplas em `FIXES`), `UPDATE exercises SET equipment_id = <vínculo novo>`, guardado pelos valores exatos do registro (id, atividade, exercício, vínculo antigo) e pela existência do vínculo novo com academia, aparelho e exercício exatos. Cada linha também exige que o vínculo ANTIGO exista com a mesma academia e o mesmo aparelho físico da medição; o `down` confere o vínculo antigo por id, academia e aparelho. Idempotente; plano vazio em banco sem as linhas; `down` devolve o vínculo antigo. Só `exercises` é escrita. `up` e `down` registram no log quantas linhas estavam no plano, quantas foram aplicadas e quantas puladas, de 23.

**Por que histórico, progressão e carga não mudam.** `exercises.equipment_id` guarda o vínculo (`gym_equipment.id`), mas o histórico e a progressão usam o aparelho físico do vínculo e o exercício do registro (`api/app/domain/training_history.py`; chave de comparação em `api/app/intelligence/dashboard/comparability.py`). Os dois não mudam; portanto a chave, a convenção de carga, o e1RM e as trilhas ficam iguais. Nenhuma trilha derivada é apagada (diferente da 117).

**O que muda: recência por vínculo no recomendador.** `equipment_recommender.load_user_recency` (`api/app/equipment_recommender.py`) agrupa por `exercises.equipment_id`, o id do vínculo. O "último uso" desses 23 registros (treinos de 23/07 a 05/09/2026) passa dos vínculos antigos para os novos. Efeito: só o desempate de rodízio e a recência na escolha de equipamento ao gerar fichas, para esses vínculos; é o efeito pretendido (o uso passa para o vínculo que de fato serve o exercício). Não medido em números.

**Não medido.** A migração não foi executada em banco (roda pela pipeline; no DEV, pelo script de atualização). Nada em tela. No deploy de produção, conferir no log a linha "migracao 124: plano=… aplicadas=… puladas=… de 23".

**Limites.**

- Os 44 restantes continuam com par incoerente.
- 6 registros passam a apontar para vínculo inativo; eles não somem do histórico (nenhum leitor do histórico filtra por vínculo ativo).
- A promoção exige `RUN_DB_MIGRATIONS=true` (já exigido pelas migrações 122 e 123).

## Atualização de 22/11/2026 (lote 32, #244: validação na Importação e no Registro, convenção nas recomendações)

**Validação do equipamento explícito fora do Modo Treino.** `services.validate_explicit_equipment` (`api/app/services.py`) aplica a MESMA validação do Modo Treino (`workout_sessions.ensure_session_equipment`), com os mesmos 422 (`equipment_not_in_session_gym`, `equipment_pair_incoherent`; ver Lote 44 abaixo). Chamadores:

- `upsert_activity` (`POST /activities`, e `PUT /activities/{id}` por `update_activity`), para cada exercício com `equipment_id` explícito;
- `_update_exercise_log_tx` (`PUT /exercises/logs/{id}`, o Registro).

**Regra de compatibilidade.** A validação não roda quando o equipamento informado é igual ao já gravado:

- na importação, o gravado é o do exercício de mesma sequência e nome na atividade anterior (`prev_equipment`); sem esse exercício anterior, todo equipamento explícito é validado;
- no Registro, o gravado é o do exercício; sem equipamento gravado, o explícito é validado.

Com isso, os registros antigos de par incoerente seguem aceitos enquanto ninguém troca o equipamento. Atividade sem academia segue a regra da primeira parte: aceita o equipamento existente e coerente, sem comparar academia.

**Convenção de carga nas recomendações.** `GET /gym-equipment/recommend` e `/recommend-all` trazem `load_convention` em cada item (`api/app/equipment_recommender.py`, `_load_candidates`, com a expressão do dono de `load_conventions.py`; normalizada em `_enrich_and_rank` por `normalize_load_convention`). Campo aditivo; a tela não calcula.

**Limites.**

- Não medido: quantos clientes enviam `equipment_id` na Importação (a validação só recusa id inexistente, de outra academia ou incoerente, e só quando o equipamento muda).
- Nenhuma tela foi vista em navegador nem em celular para este caminho (a forma como a tela mostra o 422 não foi conferida).
- O caminho por nome (`equipment_name`) segue por `resolve_equipment_id` sem validação.

## Atualização de 23/11/2026 (lote 34, #244: recomendado fora da lista, nome na técnica intensiva e painel do descanso)

**Recomendado fora da lista.** A escolha do recomendado usa `resolveRecommendedCandidate` (`frontend/src/lib/equipment-catalog-add.ts`): se o item não está na lista exibida, o próprio recomendado vira o candidato. Ele passa pelo mesmo aviso de convenção de carga dos itens da lista (`chooseItem` em `frontend/src/app/(fullscreen)/modo-treino/EquipmentSubstituteModal.tsx`, L248, com `needsLoadConventionWarning` de `frontend/src/lib/equipment-picker.ts`). A convenção vem de `load_convention` de `GET /gym-equipment/recommend`, entregue no lote 32.

**Estilo.** O seletor não tem estilo inline (conferido por busca em 23/11/2026 em `EquipmentSubstituteModal.tsx`); as classes ficam em `workout-mode.module.css`.

**Nome tocável na aplicação da técnica.** `IntensiveApplicationCard.tsx` usa `EquipmentSwapButton` (`ui/ExerciseTitleRow.tsx`) com testId `intensive-equipment-swap` e abre o mesmo seletor. Fecha o item da seção de 22/11 que dizia que o nome não tinha sido acrescentado a essa tela.

**Painel da última série após a troca no descanso.** Depois da troca no descanso, o painel da última série não mostra mais a recomendação de carga do equipamento anterior.

**Limites da seção de 22/11 que este lote fecha.** Três itens: a aplicação da técnica sem o nome; a troca no descanso do bloco intensivo sem teste de ponta a ponta; o painel da última série com a recomendação do equipamento anterior. Continuam abertos: a diferença por fase (com série lançada, o toque no nome abre "Encerrar exercício") e a falta de conferência em navegador e em celular real.

**Verificação.** Relatado pelo lote 34 e executado fora do CI; não reexecutado nesta atualização de documentação: suíte de resiliência do Modo Treino (`frontend/e2e/workout-resilience`, chromium 390) com 31 casos passando, incluindo a troca pelo nome no descanso do bloco intensivo. Vitest das alterações: não reexecutado aqui.

**Pendente de usuário.** Validação em celular real (seletor, nome tocável, tela baixa) e em navegador.

## Lote 44 (#284, segurança da Importação e do Registro, 24/11/2026)

Sem migração. Regras novas e motivo:

- **Código unificado.** Equipamento inexistente e de outra academia respondem o mesmo 422 `equipment_not_in_session_gym`, com a mensagem "Equipamento não disponível nesta academia.". `equipment_not_found` deixou de existir. Motivo: a resposta não pode revelar se o equipamento existe em outra academia. Par incoerente segue `equipment_pair_incoherent`.
- **Academia do corpo validada.** `services.resolve_active_gym_id` recusa com 422 `gym_not_found` a academia (id ou nome) inexistente ou inativa, antes de gravar. Academias são compartilhadas por desenho (`api/app/gyms.py:279-297`), então não há checagem de posse.
- **Academia adotada do equipamento.** Treino sem academia adota a do primeiro equipamento explícito válido (`services.validate_explicit_equipment`; Importação e Registro). Depois disso, equipamento de outra academia no mesmo treino recusa com `equipment_not_in_session_gym`.

Impacto em produção: medido em 24/11/2026 pelo Tech Lead, em produção, só leitura (SELECT pela RDS Data API):

- 112 atividades; 12 sem academia, nenhuma com equipamento (nenhuma delas adotaria academia);
- 0 atividades com academia inexistente ou inativa;
- 0 atividades sem academia com equipamentos de academias diferentes;
- 0 exercícios com equipamento de outra academia;
- 0 exercícios com equipamento inexistente;
- 1 academia inativa no cadastro.

Conclusão: nenhum dado existente passa a falhar em reenvio ou edição. Nenhuma migração e nenhum dado gravado muda.

Smoke da promoção (pós-deploy, obrigatório): `POST /activities` com academia inexistente deve responder 422 `gym_not_found`; `PUT /exercises/logs/{id}` com equipamento de outra academia deve responder 422 `equipment_not_in_session_gym`.

## Lote 49 (fechamento da #284, branch `fix/sobras-284-l49`, sem commit)

Não verificado: execução dos testes nesta entrega (o lote só registra o que está no código; a integração roda no CI).

**(a) O que mudou**

- Testes do Registro em `api/tests/unit/test_registro_importacao_academia_validada.py`: equipamento por nome resolvido na academia gravada (`test_registro_sem_academia_no_corpo_resolve_equipamento_por_nome_na_gravada`); adoção de academia pelo equipamento gravada no UPDATE (`test_registro_adocao_grava_a_academia_do_equipamento_no_update`, que confere o `gym_id` enviado ao `UPDATE activities`); e os casos de academia gravada inativa repetida e de equipamento de outra academia (`test_registro_equipamento_valida_na_academia_do_corpo_nao_na_gravada`).
- `services._update_exercise_log_tx` (`api/app/services.py`, L1329): a academia da atividade vem junto com o exercício, no SELECT já existente (`a.gym_id AS activity_gym_id`, L1335), sem leitura extra de `gym_id`; o teste do arquivo acima confere que o caminho não faz SELECT extra.
- `user_id` é obrigatório (sem valor padrão) em `upsert_activity` (L518), `update_activity` (L756) e `delete_activity` (L782), em `api/app/services.py`. Nenhum chamador passa a depender de padrão.

**(b) Decisões de não mudar, com o motivo**

1. **Cores do seletor no Modo Treino.** O seletor usa as classes de `frontend/src/app/(fullscreen)/modo-treino/workout-mode.module.css`, que lê os tokens `--wm-*` (surface, border, orange, text, success etc.; 101 ocorrências de `var(--` no arquivo). Esses tokens são o tema escuro próprio do Modo Treino e não seguem o tema global do app. Não mudamos: a tela inteira do Modo Treino usa o mesmo conjunto. Conferência visual fica com o usuário, no aparelho. Correção ao pedido original: o arquivo não usa cores fixas em todo o conteúdo; a razão é o tema próprio via tokens.
2. **Travas de envio e de confirmação sob concorrência.** Cobertas pela transação do servidor e pelos testes com fake. Sem teste em banco real: a integração roda só no CI e não tem cenário de concorrência. Risco aceito e registrado.
3. **Aviso de convenção de carga no Registro.** O aviso lê `load_convention` da API (`GET /gym-equipment`, lote 32) e não calcula nada no frontend. Os rótulos de convenção são espelhados do dono (`frontend/src/lib/equipment-exercises-types.ts`, `loadConventionLabel`, L52), com teste de espelho. Limite (f) acima segue valendo: sem o vínculo na lista do grupo muscular, a convenção antiga vira nula. Não reconferido nesta passada: o teste de espelho.

## Impacto em produção

Lote 49: nenhuma mudança de dado, de migração nem de contrato. Os testes e a leitura do SELECT não alteram nada gravado; `user_id` obrigatório só afeta chamada interna sem o argumento, que o código atual não tem (conferido por busca em `api/app`). Sem medição nova.

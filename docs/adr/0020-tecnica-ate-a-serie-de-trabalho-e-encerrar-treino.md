# ADR 0020: Marca de técnica até a 1ª série de trabalho, encerrar treino entre séries e dor zero sem dor (issues #213 e #211)

- **Status**: Aceita. Branch `feat/tecnica-encerrar-213-211`, sem commit no checkout no momento da redação. Nada foi publicado nem promovido a produção.
- **Data**: 2026-11-19
- **Complementa**: ADR 0017 (D1 mantida; D2 revista pela #205 e revista de novo aqui) e ADR 0019 (sobra de dor por série). Nenhuma das duas é substituída; o 0017 não é reescrito.

## Contexto

Duas sobras de entregas anteriores ficaram abertas.

1. **Aprimoramento de técnica** (ADR 0017, issue #196; revisão pela #205). A marca `exercises.form_practice` (migração 120) tira a execução da progressão, calibração, recorde, e1RM e referência de carga. O 0017 dizia que a marca entra na criação do exercício, antes da 1ª série, e que no Modo Treino trava depois da 1ª série. Na prática, o usuário ficava sem como marcar um exercício que já começou pelo aquecimento, e um exercício marcado por engano só saía pelo Registro.
2. **Encerrar treino** (issue #206). O encerramento existia só fora do treino em andamento: a tela da série e o descanso não tinham a ação, e o encerramento pelo histórico não pedia confirmação.

Além disso, a dor por série com valor zero (`muscle_pain = 0`) era consolidada no exercício como zero, o que contava como dor registrada. Essa sobra veio do PR #223 (ADR 0019).

Em 19/11/2026 o usuário decidiu:

- a marcação pode ser feita ou desfeita até a 1ª série de **trabalho** do exercício; aquecimento, ajuste e séries preparatórias não travam; depois da 1ª série de trabalho, só o Registro muda a marca;
- o treino livre ganha a marcação;
- "Encerrar treino" passa a existir também entre séries, com a mesma confirmação, e o encerramento pelo histórico pede a mesma confirmação;
- dor igual a zero conta como sem dor.

## Alternativas consideradas

- **Manter a trava na 1ª série (qualquer tipo).** Descartada: um aquecimento travaria a marca antes de o exercício de trabalho começar, e o usuário não teria como corrigir um engano sem sair do Modo Treino.
- **Liberar a marca até o fim do exercício no Modo Treino.** Descartada: mudaria a progressão de uma série já avaliada e exigiria reavaliação no meio da sessão. A regra de travar na 1ª série de trabalho é a que fecha o efeito sobre a avaliação da própria série.
- **Rota nova para marcar o exercício.** Descartada: a marca já viaja pela criação do exercício e pelo POST de série, que a fila offline já transporta. Uma rota a mais duplicaria a regra de trava.
- **Regra de trava na tela.** Descartada: a regra ficaria duplicada no frontend. O servidor decide e responde `form_practice_locked`; a tela só lê.
- **Erro 422 quando o pedido chega depois da trava.** Descartada: a fila offline pode reenviar um pedido tarde. O pedido é ignorado sem erro e vale a marca gravada.
- **Confirmação própria para o encerramento pelo histórico.** Descartada: o `FinishWorkoutConfirmModal` já existe e serve às duas telas.
- **Corrigir a dor zero na tela.** Descartada: a regra de máximo tem de bater com o backend. A consolidação passa a ser um único `NULLIF(MAX(...), 0)` no dono.

## Decisão

1. **Dono da marca** (`api/app/domain/form_practice.py`):
   - `form_practice_locked(working_sets_recorded)`: travada quando já há pelo menos uma série de trabalho gravada.
   - `resolve_form_practice_before_work(requested, stored, working_sets_recorded)`: se o pedido não veio (`None`) ou o exercício está travado, vale a marca gravada, sem erro; senão vale o pedido.
   - `sql_working_sets_recorded(alias)`: conta as séries de trabalho pelo predicado de `api/app/set_types.py` (`sql_is_working`).
   - `inherit_form_practice(sent, inherited)`: o valor enviado vale, inclusive `False`; sem valor enviado, herda a marca do item da ficha na sessão.
   - Esta regra substitui, para a trava do Modo Treino, a formulação "antes da 1ª série" do 0017 (D2 revista pela #205). D1 não muda.
   - Concorrência (`api/app/workout_sessions.py::add_session_set`, bloco `if payload.exercise_form_practice is not None`): a função faz `SELECT ... FOR UPDATE` na linha do exercício, relê a marca gravada e conta as séries de trabalho com `sql_working_sets_recorded(locking=True)` (`FOR SHARE`), porque no REPEATABLE READ a leitura comum enxergaria o snapshot de antes do lock. Assim, dois envios simultâneos com marcas diferentes não gravam marcas distintas. Sem o campo, não há trava: a resposta usa uma contagem sem `FOR SHARE` (`_working_sets_recorded(..., locking=False)`) só para informar `form_practice_locked`. Conferido no MySQL 8.0 do DEV pelo Tech Lead (19/11/2026): `COUNT(*) ... FOR SHARE` e `SELECT ... FOR UPDATE` são aceitos dentro de transação; a serialização com dois envios realmente concorrentes não foi exercitada em banco real.

2. **Rota de série** (`api/app/workout_sessions.py`): o corpo do POST de série aceita `exercise_form_practice` opcional (campo com comentário no modelo de entrada). A marca é aplicada antes de avaliar a série, via `resolve_form_practice_before_work` com `_working_sets_recorded`. Depois da 1ª série de trabalho o pedido é ignorado sem erro. As respostas de série trazem `form_practice` e `form_practice_locked`. Não há rota nova.

3. **Telas do Modo Treino** (`frontend/src/app/(fullscreen)/modo-treino/`):
   - a ação de marcar aparece na apresentação do exercício, na tela da série (`SetLoggerCard.tsx`) e no bloco intensivo (`IntensiveBlock.tsx`), e consome `form_practice_locked`;
   - no treino livre (`FreeformScreen.tsx`), a marcação segue a regra da ficha; a tela propõe para a continuação do mesmo `mapping_id` a marca vigente do exercício anterior, enviada explícita em `form_practice` na criação (`freeContinuationMark`); outro exercício começa desmarcado. O servidor não tem item da ficha para herdar, então vale o valor enviado (`inherit_form_practice`);
   - pendências locais da marca são limpas ao criar, pular ou terminar o exercício;
   - os helpers ficam em `frontend/src/lib/form-practice.ts`; o estado da marca recebido do servidor (`FormPracticeServerState`) carrega o `exerciseId` do exercício a que se refere;
   - `ExerciseBriefingScreen.tsx::resolveFormPracticeState` só usa o estado do servidor quando o `exerciseId` bate com a linha do exercício atual (`loggedRow.id`) ou com `currentExerciseId`, o id criado ou enfileirado guardado em `createdExerciseIds` antes de a linha existir. `markCreated` grava esse estado antes de tirar a pendência local. A continuação por troca de equipamento ainda não criada nunca aparece travada; a escolha do usuário para ela fica em `continuationChoices` (`PlanExecutionScreen.tsx`) e vai explícita na criação, inclusive `false`. Na ficha, a tela propõe para essa continuação a marca do item original (pela linha local ou pela pendência); a escolha do usuário a substitui, e quem aceita é o servidor (o valor enviado vale).

4. **Encerrar treino**: a ação aparece na tela da série e no descanso, com o mesmo fluxo: reps pendentes, confirmação pelo `FinishWorkoutConfirmModal`, encerramento. O detalhe do treino (web `frontend/src/app/(app)/registro-exercicios/[activityId]/WorkoutDetailClient.tsx`, mobile `frontend/src/mobile/screens/telas/TreinoDetalheScreen.tsx`) passa pelo mesmo modal.

5. **Reimportação** (`api/app/services.py::upsert_activity`): ao recriar os exercícios de uma atividade, preserva `form_practice` e `training_goal` casando por `(sequence, original_name)`. O mapa de marcas é lido antes da recriação (`kept_marks`).

6. **Contexto de IA**:
   - `api/app/intelligence/progression_service.py` filtra as sessões recentes por `sql_counts_for_progression`, então exercícios marcados saem do contexto de progressão;
   - `api/app/services.py::exercise_summary_context` devolve a marca (`is_form_practice`) como informação no contexto; nenhum prompt do submódulo `api/privado` a interpola (nenhum arquivo dele cita `form_practice`), e o texto gerado pela IA não muda por causa dessa marca. O efeito atual sobre a IA é `get_recent_sessions` excluir a execução marcada;
   - o job de avaliação por IA lê a marca do exercício pelo `read_exercise_form_practice` (`api/app/workout_ai_generation_core.py`) e falhar nessa leitura não derruba a avaliação (ver itens descartados).

7. **Guarda dos motores**: o padrão `aprimoramento_tecnica_filtro_sql` de `api/tests/unit/test_guarda_fontes_da_verdade.py` já cobre `COALESCE`/`IFNULL(form_practice, ...)` e comparação com parâmetro em predicado (`AND/OR/WHERE/ON/HAVING form_practice = %s`). `api/app/domain/form_practice.py` é o único dono permitido.

8. **Progresso por exercício**: `frontend/src/lib/progression-chart.ts::formPracticeSessionsNote` informa a quantidade de sessões de técnica; o marcador no gráfico mobile e o traço na coluna de carga de topo da tabela web (linha de técnica, `DashboardProgressaoClient.tsx`) usam o mesmo dado.

9. **Dor zero** (`api/app/workout_sessions.py::_read_pain_rollup`): `NULLIF(MAX(muscle_pain), 0)` e `NULLIF(MAX(joint_pain), 0)`. Dor zero não é consolidada. Os dois chamadores têm semântica diferente quando nenhuma série tem dor > 0: o Modo Treino (`_refresh_exercise_aggregates`, chamado por `add_session_set` e `update_session_set`) regrava a dor do exercício sem COALESCE, então grava NULL e corrigir a dor de uma série para zero limpa a dor do exercício; a edição pelo Registro (`rollup_exercise_pain(..., keep_stored_when_empty=True)`, chamada por `services._update_exercise_log_tx`) preserva o campo do exercício, porque no Registro esse campo é editável à mão. No Modo Treino a dor do exercício é sempre derivada das séries: o corpo de criação do exercício no frontend não envia dor (`exerciseBody` em `PlanExecutionScreen.tsx`), então sem dor > 0 nas séries ela fica NULL, e corrigir uma série para zero limpa a do exercício. A paridade com o frontend é a fixture `frontend/src/lib/pain-rollup.parity.json`, lida por `frontend/src/lib/exercise-set-edit.test.ts`, e o lado do backend está em `api/tests/unit/test_dor_zero_sem_dor.py`. `SET_TYPE_LABELS` fica em `frontend/src/lib/set-types.ts`.

## Trade-offs

- Ganha: a marca pode ser corrigida no momento em que o usuário percebe o engano, sem sair do Modo Treino, até a primeira série de trabalho.
- Ganha: a trava continua numa única função, e o pedido atrasado da fila offline não gera erro.
- Ganha: o encerramento tem a mesma confirmação em todas as entradas (série, descanso e histórico).
- Ganha: dor zero deixa de ser tratada como dor registrada, sem migração.
- Paga: a marca passa a poder mudar até a 1ª série de trabalho. O efeito sobre séries já avaliadas não foi medido.
- Paga: a continuação pode ser desmarcada depois de já ter sido marcada, o que antes não era possível no Modo Treino.
- Paga: a regra de trava vive no servidor e o frontend mantém uma cópia local até a próxima resposta, com a previsão de trava ao enfileirar uma série de trabalho (ver limites).

## Consequências

- Sem migração. Nenhum dado gravado muda no deploy. A medição de produção abaixo é leitura do Tech Lead, feita por SELECT em 19/11/2026:
  - 877 exercícios, 5 marcados como técnica;
  - dor do exercício igual a zero em 714 (articular) e 23 (muscular);
  - 4 pares (sessão, item da ficha) com mais de uma execução na mesma sessão;
  - 86 sessões encerradas, 23 sem status, nenhuma em andamento na leitura.
- Mudanças de semântica para quem consome a API e os relatórios:
  - a marca pode mudar até a 1ª série de trabalho, e a continuação pode ser desmarcada;
  - exercício novo cujas séries só têm dor zero fica com dor nula em vez de zero; os consumidores lidos tratam nulo e zero da mesma forma;
  - o contexto de IA de sessões recentes deixa de incluir os exercícios marcados;
  - a reimportação preserva marca e objetivo.
- A regra de trava e de marca continua fonte única em `api/app/domain/form_practice.py`. A guarda cobre o padrão SQL, e a lista explícita de `api/tests/unit/test_form_practice_consumidores.py` continua sendo o inventário de consumidores.

## Itens das issues #213 e #211 descartados, com o motivo

- **Texto livre da IA pode citar carga e os prompts não conhecem a marcação.** O RPE sugerido para a próxima série continua como está: decisão já registrada no ADR 0017. Os prompts ficam no submódulo privado `api/privado` e não mudam neste lote.
- **Resumo do treino por IA.** Descreve só o treino do dia, sem média nem comparação histórica, então não há média a corrigir. O contexto do exercício passou a informar a marca, mas o texto do prompt não foi alterado.
- **Falha na leitura da marca no job de avaliação.** A série continua sendo avaliada como normal, com o log `evaluate_set.form_practice_failed` (`api/app/workout_ai_generation_core.py`). Comportamento deliberado: a avaliação não pode cair por causa da marca.
- **Usuário do job de IA com personal trainer e aluno.** Apurado: nenhuma rota do Modo Treino registra série no treino de outro usuário, pois todas escopam pelo usuário autenticado. O job sempre carrega o dono da sessão.
- **Caminho legado do volume (`_history_items_from_sets`, `api/app/intelligence/volume_service.py`).** O agrupamento já é pela linha da execução; provado por teste, sem mudança de código.
- **`category_from_ai` na resposta neutra.** Mantido, porque o frontend lê o campo (`frontend/src/lib/workout-mode-execution-view.ts`).
- **Guarda sobre SQL fora de `api/app`.** Não ampliada. Migrações são histórico imutável e scripts são ferramentas de operação. Fora de `api/app`, só a migração 120 e os testes citam o campo.
- **Guarda exigindo o predicado em consumidor novo de e1RM e recorde por auto-descoberta.** Descartada. Mais de 20 módulos leem `exercise_sets` legitimamente sem excluir a marca (volume, recuperação, fadiga, adesão, relatório), e distinguir por regex daria falso positivo. A lista explícita de `test_form_practice_consumidores.py` continua.
- **Script e saída do replay de impacto em produção da #196 não versionados.** Artefatos de execução ficam locais, fora do Git, por regra do projeto. Os números estão no ADR 0017.
- **"A provar na promoção" (dry-run da migração 120, `RUN_DB_MIGRATIONS`, smoke pós-deploy).** Superado: a promoção ocorreu em 18/11/2026 (PR #220), com a migração 120 aplicada e validada pelo usuário em produção.
- **Replay de progressão medido só para o usuário 1.** Mantido: produção tem 2 usuários com histórico e a entrega foi validada em produção.
- **Ação de marcar para quem sai da apresentação sem registrar série, e marcação travando no aquecimento.** Resolvidos por este ADR (itens 1 e 3 da Decisão), não são descartes.

## Limites conhecidos

- Nenhuma tela foi verificada em navegador ou iPhone.
- Entre a resposta do servidor a uma criação feita offline e a próxima chamada da criação, a tela ainda usa o id local e pode mostrar a ação destravada até a linha da atividade recarregar (o servidor ignora a mudança depois da trava).
- O efeito de `get_recent_sessions` excluir a execução marcada sobre o texto gerado pela IA não foi medido.
- Os e2e (`frontend/e2e/workout-resilience`) ganharam o cenário de marcação, mas não rodam no CI e não foram executados.
- A integração do modal de encerramento nas telas tem teste só no nível de função, sem renderização em navegador.
- O estado `form_practice_locked` recebido do servidor não é persistido localmente: depois de recarregar a tela vale a linha local até a próxima resposta.
- A fila offline real não foi exercitada.
- A reimportação perde marca e objetivo se o exercício for renumerado ou renomeado no payload, pois a chave é `(sequence, original_name)`.
- A fixture de paridade da dor não cobre série pendente com repetições preenchidas.
- Aplicativo antigo em cache: na continuação por troca de equipamento ele envia a marca da linha do item original, ou a pendência local. Pelo resolvedor deste ramo, só enviaria `false` para um item marcado se a linha original não estivesse no cache local e a pendência local também não (criação offline ainda não sincronizada, cache defasado ou sessão reaberta em outro aparelho). Nesse caso raro a continuação nasce desmarcada, porque o valor enviado vale. A versão antiga do código não foi verificada.
- A escolha do usuário para a continuação (`continuationChoices`) fica só em memória: ao recarregar a tela antes de criar a continuação, ela volta a herdar a marca do original.
- Os testes unitários de marcação (`api/tests/unit/test_form_practice_marcacao_ate_serie_de_trabalho.py`) usam `_FakeDB` e não verificam o SQL de `FOR UPDATE` nem de `FOR SHARE`; a integração com MySQL roda no CI. A previsão de trava ao enfileirar (`PlanExecutionScreen.tsx::postSet`) não tem teste de componente, porque o componente não é renderizado em teste; o resolvedor tem testes em `ExerciseBriefingScreen.test.ts`.
- O POST de criação do exercício do Modo Treino (`add_session_exercise`) aceita `muscle_pain`/`joint_pain` no corpo, mas o frontend não envia esses campos. Se algum cliente enviasse, a primeira série gravada substituiria esse valor pela consolidação das séries (Modo Treino sem COALESCE).
- Depois de uma criação feita offline, a tela grava o estado da marca com `locked: false` e o id local (`nextServerStateOnCreated`, `PlanExecutionScreen.tsx`). Esse estado só troca de id na próxima resposta de série (`setFormPracticeServer` com `exerciseId`, `PlanExecutionScreen.tsx`, por volta da linha 1582). Até lá a ação pode aparecer destravada; o servidor ignora a mudança depois da trava.
- Ao confirmar a criação de uma continuação (resposta online), `markCreated` remove a pendência local de marca do item original (`removeFormPracticePending`, `PlanExecutionScreen.tsx`). A pendência só existe quando o original foi criado offline e ainda não foi confirmado. Não verificado: se isso tira o selo de técnica do original da tela até a sincronização; a pendência só é lida em `resolveFormPracticeState` quando não há linha do exercício. O corpo enfileirado do original continua com a marca explícita.

# ADR 0017: Aprimoramento de técnica fora da progressão (issue #196)

- **Status**: Aceita; D2 revista pela issue #205 (lote A, branch `feat/tecnica-no-registro-205-lote-a`, lote B1, branch `feat/registro-reavalia-progressao-205-lote-b1`, e lote B2, branch `feat/deload-no-registro-205-lote-b2`, ver "Revisão pela #205"). Lote 1 (motor) implementado na branch `feat/tecnica-fora-da-progressao-196-lote1`; lote 2 (frontend) na branch `feat/tecnica-marcacao-196-lote2`. Nada foi publicado nem promovido a produção.
- **Data**: 2026-11-18

## Contexto

O usuário às vezes faz um exercício para treinar a execução: carga baixa, foco no movimento. Hoje o motor trata essa execução como qualquer outra. Uma carga abaixo da referência conta como regressão, uma série fácil pode contar como sessão qualificada, a calibração de máquina conta a sessão, e recorde, e1RM e carga de partida passam a usar esses números.

## Decisão

Coluna `exercises.form_practice` (`TINYINT(1) NOT NULL DEFAULT 0`, migração 120). Dono da regra: `api/app/domain/form_practice.py` (mapa §105).

- **D1.** A execução marcada fica neutra para progressão (avanço, regressão, platô, sequências de sessões qualificadas, mudança de fase, ajustes), calibração, recorde, e1RM e referência de carga. Continua contando para volume semanal, recuperação/fadiga, tonelagem, tempo de treino e aderência.
- **D2 (revista pela #205).** A marca entra na criação do exercício da sessão (antes da 1ª série). No Modo Treino ela fica travada depois da 1ª série. Pelo Registro de exercícios, a marca pode ser posta e retirada depois, inclusive em treino encerrado, e a progressão é reavaliada na mesma transação. D1 não muda.

No motor, a execução marcada sai das visões da progressão (`SessionRecord.progression_view` e `progression_view_all_goals`). A sessão continua na janela, e o teto de ativação e a fadiga leem a sessão inteira. A sessão toda marcada é pulada como o deload (`SKIP_REASON_FORM_PRACTICE`). Propriedade testada: um histórico com execuções marcadas dá o mesmo resultado que o mesmo histórico sem elas, nos dois avaliadores.

Duas regras do Modo Treino também moram no dono, para a rota e a IA só consumirem:

- **Leitura neutra da série** (`neutral_set_reading`): a série de execução marcada não recebe veredito de esforço nem posição de reps, e não há carga sugerida para a próxima série. O evento de segurança (RPE contra o teto) continua. Usada pela rota de registro da série (`workout_sessions._evaluate_set`) e pela avaliação por IA (`workout_ai_generation_core`, job `evaluate_set`).
- **Desfecho neutro da série** (`neutral_set_outcome`): devolve a leitura neutra junto com a carga da próxima série ausente; é a única fonte desse `None` para a rota e o job.
- **Contexto neutro do motor para a IA** (`neutral_engine_context`): cópia do `PairEngineContext` sem `decision` e sem `deload`, junto com o conjunto de regras da próxima série ausente (não é lido nem enviado).
- **Saída neutra da IA** (`neutral_ai_suggestion`): carga e reps sugeridas para a próxima série saem `None`; a categoria vira `HOLD` ("manter", de `domain/set_evaluation_categories.py`) com o rótulo desse dono, e a categoria original da IA fica em `category_from_ai`; `load_adjustments` sai vazio (todas as entradas são sobre a carga sugerida, que não existe aqui). Mantido de propósito: a IA ainda pode sugerir o RPE da próxima série (`suggested_next_rpe`), sujeito ao teto de segurança, porque a decisão do usuário trata de carga, repetições, séries e técnicas. Efeito para o usuário: numa execução de técnica a avaliação por IA comenta a série, mas não sugere carga nem reps para a próxima e não a classifica como aumentar ou reduzir.
- **Herança na continuação** (`sql_plan_item_marked` + `inherit_form_practice`): marcada a 1ª passagem de um item da ficha na sessão, as continuações do mesmo item na mesma sessão (troca de equipamento) herdam a marca. No treino livre (sem item da ficha) vale o que o cliente envia.

Consumidores deste PR (lista completa no mapa §105):

- Excluem a execução marcada: o motor (`progression_view`, `progression_view_all_goals`, `evaluate_history`, calibração), `exercise_briefing` (última execução), `exposure_service` (3 templates), `session_records`, `strength_panel`, `analytics` (`_exercise_progress_match_sql`, `_fetch_group_e1rm_rows` e o e1RM de `_build_progression_sessions`: a sessão toda marcada aparece no progresso sem e1RM; em sessão mista as séries marcadas não entram no e1RM), `volume_service` (só o e1RM de referência), `recovery_read_model` + `exercise_performance` ("Desempenho recente"; o histórico empacotado ganhou um 12º campo), `services.get_last_session_sets` e `prescription_service.last_execution_from_logs`.
- Só transportam a marca: `workout_sessions` (criação, herança, repetição por `client_key`), `services.list_exercise_logs` e `services.activity_detail`.
- Não avaliam a série marcada: `workout_sessions._evaluate_set` e o job `evaluate_set`, pelo desfecho neutro; o job também pelo contexto neutro e pela saída neutra da IA. A marcação chega ao job pelo leitor `engine_decision_context.read_exercise_form_practice`, porque o job não tem a linha de `exercises` em mãos.
- `analytics`: a sessão toda marcada aparece na tabela de progresso sem e1RM, e cada sessão do resultado traz o campo `form_practice` (consumidor `analytics._build_progression_sessions`), verdadeiro só quando nenhuma série de trabalho da sessão conta para a progressão por ser de execução marcada e a sessão não é de deload; falso em sessão normal, mista (as séries marcadas ficam fora do snapshot de carga/reps/RPE e do e1RM), de deload e em linha antiga sem a coluna; na sessão toda marcada, carga de topo, reps e RPE mostrados são os da execução de técnica e `e1rm`/`e1rm_reps`/`e1rm_reliable` saem vazios; a tela usa o campo para rotular o ponto (ver "Frontend (lote 2)" abaixo).
- Mantêm: volume, recuperação, fadiga, tonelagem, tempo, aderência e teto de ativação.

## Frontend (lote 2)

Só apresentação: o frontend não decide elegibilidade, e1RM, veredito nem herança; isso fica no dono acima.

- **Rótulo.** `FORM_PRACTICE_LABEL` ("Aprimoramento de técnica") e `FORM_PRACTICE_HELP` ficam em `frontend/src/lib/form-practice.ts`, com os helpers da marcação. É texto fixo de apresentação, sem fonte no backend, como os de "Laboral" e "Deload".
- **Marcação pendente local.** Antes da 1ª série o exercício não existe no servidor, então a marca fica em `form_practice_plan_exercise_ids` do estado do Modo Treino (`frontend/src/lib/workout-mode-types.ts`), por `workout_plan_exercise_id` original do item da ficha. `persistSession` em `frontend/src/app/(fullscreen)/modo-treino/PlanExecutionScreen.tsx` grava essa lista, e ela sobrevive a recarregar o aplicativo.
- **Envio.** `ensureExerciseMutation` envia `form_practice` no `POST /workout-sessions/{id}/exercises`. A fila offline usa o mesmo payload, e `frontend/src/lib/offline/overlay.ts` copia o campo para a linha local criada offline.
- **Regra de exibição, coerente com D2.** `resolveFormPracticeState` (`ExerciseBriefingScreen.tsx`): sem linha do servidor, vale a marcação pendente e a ação pode ser alternada; com linha, vale a `form_practice` da linha e a ação trava. Numa continuação por troca de equipamento, a regra usa o item original.
- **O que as telas mostram.** A ação e o aviso na apresentação do exercício (`ExerciseBriefingScreen.tsx`); o selo no cabeçalho das séries (`SetLoggerCard.tsx`); o rótulo no detalhe do treino (`WorkoutDetailClient.tsx` e `TreinoDetalheScreen.tsx`, de `GET /activities/{id}`); o rótulo na tabela e no gráfico de progresso (`DashboardProgressaoClient.tsx`, `ProgressaoChart.tsx` e `ProgressaoScreen.tsx`, de `GET /analytics/dashboard-progression`). No gráfico (`frontend/src/lib/progression-chart.ts`) o ponto marcado sai da linha de Carga, e o deload tem precedência sobre a técnica.
- **Limitações do lote 2.** O treino livre (`FreeformScreen.tsx`) não tem a marcação e fica para outra entrega. A ação só aparece na apresentação do exercício, então quem sai dela sem registrar série não marca mais aquele exercício. Trocar o equipamento antes da 1ª série mantém a marca feita no item da ficha, que aparece e pode ser desmarcada. O gráfico mobile não tem rótulo de marcador (o deload também não tem). O corpo do `POST` não tem teste automatizado.
- **Promoção.** Este frontend só pode ir para produção junto com o backend e a migração 120. Uma API sem o campo ignoraria `form_practice` e a marca sumiria em silêncio. Os três vão na mesma promoção, com `RUN_DB_MIGRATIONS=true` como descrito em Consequências.

## Alternativas descartadas

- **Tratar como série laboral e tirar do volume.** Descartada: o esforço aconteceu e precisa contar no volume, na recuperação e na fadiga.
- **Permitir marcar a qualquer momento ou no histórico.** Descartada no lote 1 (D2): mudaria resultados já gravados e exigiria replay das trilhas e do estado dos grupos. A #205 reabriu o Registro, com reavaliação só a partir da sessão corrigida (ver "Revisão pela #205").
- **Usar o desfecho `NEUTRAL` do motor.** Descartada: a sessão neutra ainda adota a carga executada e conta para a calibração da trilha.

## Consequências

- Sem marca, nada muda: a coluna nasce 0 em todo dado existente. Medido em produção em 2026-11-18 por SELECT: `exercises` tem 867 linhas, `TOTAL_ROW_VERSIONS` 2 de 64, MySQL 8.0.39 / Aurora 3.08.2, coluna ainda inexistente.
- Migração 120 no MySQL do DEV local: 1 statement, 329 ms, coluna `tinyint(1)`, `NOT NULL`, default 0. Replay `api/scripts/replay_diff_progressao.py --user-id 1` no DEV (cópia de produção até 2026-11-16), `origin/develop` (c994597e) contra a branch: 82 sessões em cada versão, 0 diferenças (QUALIFY 213, RESET 116, NEUTRAL 362 nas duas). O Tech Lead repetiu o replay do usuário 1 sobre o estado final do lote, depois das correções de revisão, com o mesmo resultado (82 sessões, 0 diferenças).
- Ordem de deploy obrigatória: o código novo referencia a coluna em todas as rotas de treino e de progressão (inclusive o INSERT do POST de exercício). A promoção exige `RUN_DB_MIGRATIONS=true` (o pipeline roda a migração antes de atualizar as Lambdas e para se ela falhar), e a variável volta a `false` depois. Sem isso, iniciar exercício, registrar série, progressão, recordes, home e recuperação respondem 500. O código antigo é compatível com a coluna presente. Rollback: reverter o código primeiro; a coluna só depois. PR com rótulo `ci-completo`.
- Não medido: replay para outros usuários além do 1; execução do ALTER em produção.
- Os consumidores excluem a execução marcada só pelo dono (`sql_counts_for_progression`, `counts_for_progression`, `is_form_practice`). As guardas `aprimoramento_tecnica_filtro_sql` (filtro, comparação ou agregação da coluna em SQL) e `aprimoramento_tecnica_chave_literal` (leitura por chave literal sem o dono) barram a regra fora do dono.
- Limitações conhecidas: no treino livre com troca de equipamento a continuação não herda a marca; uma segunda execução do mesmo item da ficha na mesma sessão herda a marca e não pode ser desmarcada no Modo Treino (pelo Registro, sim, #205); se a leitura da marcação falhar no job de IA, a série é avaliada como normal (log `evaluate_set.form_practice_failed`); a importação que recria os exercícios (`PUT /activities/{id}`) perde a marcação; os prompts de IA de resumo não conhecem a marcação.
- Uma marca errada no Modo Treino só se corrige pelo Registro (revisão #205). Pelo Registro (#205), a reavaliação roda em toda edição e exclusão de exercício de sessão encerrada (lotes A e B1), não só na troca da marca.

## Revisão pela #205 (lotes A, B1 e B2, 2026-11-18)

**Contexto.** O usuário pediu (issue #205) que a marca possa ser corrigida depois da 1ª série, pelo Registro de exercícios. Com D2 absoluta, uma marca errada nunca saía.

**Alternativas.**

- Recalcular tudo com `rebuild_for_user` a cada correção. Descartada: o replay completo do usuário 1 faz 973 a 975 escritas e pode estourar o teto de 29 s do API Gateway (uma chamada da Data API por linha).
- Não reavaliar: a progressão ficaria com a marca antiga. Descartada, porque a marca precisa mudar o resultado.
- Reavaliar só a partir da sessão corrigida, na mesma transação da edição (escolhida).

**Decisão.**

- D2 deixa de ser absoluta: no Modo Treino a marca continua travada depois da 1ª série; pelo Registro pode ser posta e retirada, inclusive em treino encerrado. D1 não muda.
- Backend: `PUT /exercises/logs/{id}` (rota e permissão `treino.registro`/`editar` inalteradas; nenhuma rota nova). `ExerciseLogUpdateIn.form_practice` é opcional; omitido/null preserva a marca (`resolve_form_practice`, dono em `api/app/domain/form_practice.py`). `services.update_exercise_log` grava a coluna pela constante e, em toda edição de sessão `done` com `user_id` (não só quando a marca muda), chama `progression_state.reevaluate_after_correction` na mesma transação, pelo helper `services._reevaluate_if_done` (erro desfaz a edição). Lote B1: `services.delete_exercise_log` também chama o helper depois de excluir. A resposta do PUT traz `form_practice` e `progression_reevaluated`, verdadeiro só se a reavaliação devolveu algo (falso com o motor desligado).
- `reevaluate_after_correction` (`api/app/progression_state.py`) não cria regra: lê o histórico paginado (`replay_progression.fetch_sessions_paged`), avalia como `rebuild_for_user` (`build_session_inputs` + `evaluate_history_by_goal`), grava só a diferença de grupos e trilhas, remove os que sumiram e regrava só as decisões EVALUATION da sessão corrigida em diante (`only_activity_ids`). Respeita `shadow_enabled()` e é idempotente.
- Recorde, e1RM e referência de carga leem a coluna na consulta (`sql_counts_for_progression`); não precisam de reprocessamento.
- Frontend (só apresentação): Switch nos formulários de `WorkoutDetailClient.tsx` e `TreinoDetalheScreen.tsx`, com `FORM_PRACTICE_LABEL` e `FORM_PRACTICE_REGISTRO_HELP`; `saveExerciseLog` envia o campo no PUT e no POST; no sucesso da gravação e da exclusão de exercício, as telas invalidam `PROGRESSION_QUERY_KEYS` (`frontend/src/lib/exercise-log-save.ts`; a exclusão é o lote B1).
- Lote B2, deload da sessão encerrada (`api/app/domain/deload.py`, `api/app/workout_sessions.py`): `can_toggle_deload` e `toggled_deload_source` recebem `session_status`; sessão `done` liga e desliga o deload mesmo com séries gravadas. Sessão em andamento mantém a trava (com série gravada, `DeloadToggleLocked` -> 409 `deload_locked`). `PATCH /workout-sessions/{id}/meta` reavalia a progressão, na mesma transação, só quando a origem gravada muda em sessão `done`, chamando `progression_state.reevaluate_after_correction` (dono da reavaliação; a rota não decide). O Switch "Deload" fica nas duas telas de detalhe do Registro, não no modal "Editar treino". Deload sai da lista de ajustes não editáveis depois do treino; tipo de série e dor por série continuam fora (issue #216 e seguintes).

**Trade-offs.**

- Sessão antiga corrigida regrava todas as decisões dali em diante: o custo cresce com a antiguidade da sessão e pode se aproximar do teto de 29 s (não medido em produção).
- A correção não reprocessa a leitura da série nem a avaliação por IA calculadas no registro original.
- Concorrência com o encerramento de outro treino do mesmo usuário não está tratada.
- Escopo do Registro no lote B1: a mudança de data/hora do treino pelo cabeçalho (`PATCH /workout-sessions/{id}/meta`) não reavalia; chamadas sem `user_id` não reavaliam; tipo de série, dor por série e objetivo do treino não são editáveis depois do treino (issue #216 e seguintes). O deload saiu dessa lista no lote B2.
- Lote B2: a série já gravada não é recalculada quando o deload muda depois (`rpe_target_effective` e os status de esforço ficam com o alvo da época, porque a sessão foi executada contra ele). Mudar data/hora pelo cabeçalho continua sem reavaliar.
- O frontend não usa `progression_reevaluated`.

**Impacto medido** (Tech Lead, 2026-11-18, MySQL do DEV com os dados do usuário 1 copiados da produção: 82 sessões encerradas, 258 trilhas, 19 grupos; transação desfeita por rollback). Marcar/desmarcar a sessão mais recente: 11 a 12 comandos de escrita, cerca de 3 s. Sessão de uma semana antes (6 sessões regravadas, 41 decisões): 43 escritas, cerca de 3 s. Edição que não muda a avaliação: cerca de 3 s e ~10 escritas (as decisões da sessão editada são regravadas); edição que muda: 11 a 43 escritas. Replay completo do mesmo usuário: 973 a 975 escritas. Depois da reavaliação, `state_snapshot` e as decisões de avaliação (sem ids) ficaram iguais aos do replay completo. Não medido: tempo na RDS Data API de produção.

Lote B2 (deload), mesmo MySQL do DEV, usuário 1 (82 sessões), transação desfeita por rollback, mudando `activities.deload_source` e chamando `reevaluate_after_correction` direto: sessão mais recente, ligar 3,1 s (1 sessão regravada, 0 decisões, 12 trilhas removidas) e desligar 3,5 s (9 decisões); oitava sessão mais recente, ligar 3,3 s (8 sessões regravadas, 47 decisões, 1 trilha removida) e desligar 3,3 s (52 decisões). Nos quatro casos, `state_snapshot` e as decisões (sem ids) iguais às do `rebuild_for_user`. Sem migração; rota e permissão inalteradas. Não medido: a rota contra MySQL real e a RDS Data API de produção.

**Testes.** `api/tests/unit/test_reavaliacao_retroativa_progressao.py`, `api/tests/unit/test_form_practice_edicao_registro.py`, `api/tests/unit/test_form_practice_sessao.py`, `frontend/src/lib/exercise-log-save.test.ts` e, no lote B1, `api/tests/unit/test_registro_edicao_reavalia_progressao.py` (novo) com `test_form_practice_edicao_registro.py` (atualizado). Lote B2: `api/tests/unit/test_deload_correcao_treino_encerrado.py` (novo). A execução destes testes não está registrada aqui.

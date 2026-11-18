# ADR 0017: Aprimoramento de técnica fora da progressão (issue #196)

- **Status**: Aceita. Lote 1 (motor) implementado na branch `feat/tecnica-fora-da-progressao-196-lote1`; lote 2 (frontend) na branch `feat/tecnica-marcacao-196-lote2`. Nada foi publicado nem promovido a produção.
- **Data**: 2026-11-18

## Contexto

O usuário às vezes faz um exercício para treinar a execução: carga baixa, foco no movimento. Hoje o motor trata essa execução como qualquer outra. Uma carga abaixo da referência conta como regressão, uma série fácil pode contar como sessão qualificada, a calibração de máquina conta a sessão, e recorde, e1RM e carga de partida passam a usar esses números.

## Decisão

Coluna `exercises.form_practice` (`TINYINT(1) NOT NULL DEFAULT 0`, migração 120). Dono da regra: `api/app/domain/form_practice.py` (mapa §105).

- **D1.** A execução marcada fica neutra para progressão (avanço, regressão, platô, sequências de sessões qualificadas, mudança de fase, ajustes), calibração, recorde, e1RM e referência de carga. Continua contando para volume semanal, recuperação/fadiga, tonelagem, tempo de treino e aderência.
- **D2.** A marca só entra na criação do exercício da sessão (antes da 1ª série) e não é editável depois.

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
- **Permitir marcar a qualquer momento ou no histórico.** Descartada: mudaria resultados já gravados e exigiria replay das trilhas e do estado dos grupos.
- **Usar o desfecho `NEUTRAL` do motor.** Descartada: a sessão neutra ainda adota a carga executada e conta para a calibração da trilha.

## Consequências

- Sem marca, nada muda: a coluna nasce 0 em todo dado existente. Medido em produção em 2026-11-18 por SELECT: `exercises` tem 867 linhas, `TOTAL_ROW_VERSIONS` 2 de 64, MySQL 8.0.39 / Aurora 3.08.2, coluna ainda inexistente.
- Migração 120 no MySQL do DEV local: 1 statement, 329 ms, coluna `tinyint(1)`, `NOT NULL`, default 0. Replay `api/scripts/replay_diff_progressao.py --user-id 1` no DEV (cópia de produção até 2026-11-16), `origin/develop` (c994597e) contra a branch: 82 sessões em cada versão, 0 diferenças (QUALIFY 213, RESET 116, NEUTRAL 362 nas duas). O Tech Lead repetiu o replay do usuário 1 sobre o estado final do lote, depois das correções de revisão, com o mesmo resultado (82 sessões, 0 diferenças).
- Ordem de deploy obrigatória: o código novo referencia a coluna em todas as rotas de treino e de progressão (inclusive o INSERT do POST de exercício). A promoção exige `RUN_DB_MIGRATIONS=true` (o pipeline roda a migração antes de atualizar as Lambdas e para se ela falhar), e a variável volta a `false` depois. Sem isso, iniciar exercício, registrar série, progressão, recordes, home e recuperação respondem 500. O código antigo é compatível com a coluna presente. Rollback: reverter o código primeiro; a coluna só depois. PR com rótulo `ci-completo`.
- Não medido: replay para outros usuários além do 1; execução do ALTER em produção.
- Os consumidores excluem a execução marcada só pelo dono (`sql_counts_for_progression`, `counts_for_progression`, `is_form_practice`). As guardas `aprimoramento_tecnica_filtro_sql` (filtro, comparação ou agregação da coluna em SQL) e `aprimoramento_tecnica_chave_literal` (leitura por chave literal sem o dono) barram a regra fora do dono.
- Limitações conhecidas: no treino livre com troca de equipamento a continuação não herda a marca; uma segunda execução do mesmo item da ficha na mesma sessão herda a marca e não pode ser desmarcada; se a leitura da marcação falhar no job de IA, a série é avaliada como normal (log `evaluate_set.form_practice_failed`); a importação que recria os exercícios (`PUT /activities/{id}`) perde a marcação; os prompts de IA de resumo não conhecem a marcação.
- Uma marca errada não tem correção pela interface (D2).

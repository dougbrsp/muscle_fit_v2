# ADR 0018: Edição de treino encerrado pelo Registro e reavaliação da progressão (issues #215, #216 e #219)

- **Status**: Aceita. Branch `fix/registro-treino-encerrado-216-219-215`, sem commit neste checkout no momento da redação. Nada foi publicado nem promovido a produção.
- **Data**: 2026-11-19

## Contexto

O Registro de exercícios (`/registro-exercicios`) corrige treinos já encerrados pelo "Finalizar treino" (`activities.session_status = 'done'`). A ADR 0017 (Revisão pela #205) já tinha feito a edição e a exclusão de exercício reavaliarem a progressão. Faltavam três coisas, pedidas nas issues #215, #216 e #219 do GitHub (`dougbrsp/muscle_fit_v2`):

1. Adicionar um exercício novo, com séries, a um treino encerrado. As rotas do Modo Treino recusam sessão encerrada (`api/app/workout_sessions.py`, `_ensure_not_finished`, 409).
2. Corrigir a data ou a hora de um treino encerrado. A mudança não reavaliava a progressão, então o histórico ficava com a posição cronológica antiga.
3. Ligar ou desligar o deload de um treino encerrado. A trava por série gravada impedia a correção, e as séries já gravadas mantinham o `rpe_target_effective` da época.

Além disso, a regra do alvo gravado da série precisava valer igual em três caminhos (POST da série do Modo Treino, recálculo do deload e PUT do exercício), e o predicado "sessão encerrada" aparecia repetido em vários módulos.

## Alternativas consideradas

- **Reavaliar com `rebuild_for_user` a cada correção.** Descartada. A ADR 0017 mediu 973 a 975 escritas no replay completo do usuário 1 (DEV com dados de produção), com risco de estourar o teto de 29 s do API Gateway.
- **Reavaliar só a partir da sessão corrigida, na mesma transação da edição.** Escolhida (base da ADR 0017, com a âncora de data e hora descrita abaixo).
- **Reavaliar a partir da posição da sessão no histórico, sem âncora de data e hora.** Descartada para correção de data e hora: quando a sessão corrigida sai do histórico (por exemplo, o último exercício foi excluído), não há posição para começar. A âncora (`correction_anchor`) cobre esse caso.
- **Recalcular `rpe_target_effective` só no POST da série.** Descartada: as séries já gravadas ficariam com o alvo da época, que era a limitação do lote B2 da ADR 0017.
- **Um dono separado do alvo gravado por caminho.** Descartada: o POST, o recálculo do deload e o PUT passariam a ter regras paralelas. Ficou um único dono (`effective_intensity.stored_rpe_target`).

## Decisão

- **Dono do estado "encerrada"**: `api/app/domain/session_status.py`. Define `SESSION_STATUS_DONE`, `is_closed_session(status)` (`status == 'done'`) e `reevaluates_progression(status, *, changed=True)`. Sessão com `session_status` NULL (importada ou legada) não é encerrada e continua fora do histórico da progressão (`api/app/domain/training_history.py`, filtro `a.session_status = 'done'`). `domain/deload.py` reexporta `SESSION_STATUS_DONE`; `nutrition_targets_service.py` mantém `DONE_SESSION_STATUS` como alias.
- **Edição, exclusão e criação de exercício em treino encerrado**:
  - `PUT /exercises/logs/{id}` (`api/app/main.py`) chama `services._update_exercise_log_tx`, que reavalia a progressão na mesma transação em qualquer edição de sessão encerrada com `user_id`. A âncora de data e hora vale quando `local_date` ou `started_at` do corpo mudam em relação ao gravado (`started_at` ausente não é mudança; com fuso, é convertido para a hora do usuário).
  - `DELETE /exercises/logs/{id}` reavalia com a âncora da data e hora da sessão, porque o exercício excluído pode ter sido o único da sessão.
  - `POST /exercises/logs` (rota nova, `main.py`, `services.create_exercise_log`) cria exercício com séries numa sessão encerrada do próprio usuário. Sessão alheia responde 404; sessão não encerrada responde 409 (`SessionNotClosedError`). Usa `treino.registro`/`editar`, como o PUT. Reavalia uma vez, pelo mesmo `_update_exercise_log_tx`.
- **Deload de sessão encerrada** (`api/app/domain/deload.py`, `can_toggle_deload` e `toggled_deload_source`): sessão encerrada liga e desliga o deload mesmo com séries gravadas. Sessão em andamento mantém a trava (série gravada, 409 `deload_locked`). `GET /workout-sessions/{id}/deload` devolve `can_toggle = false` sem `treino.modo_treino`/`editar`.
- **Correção de data, hora ou deload** (`api/app/workout_sessions.py`, `update_session_meta`, `PATCH /workout-sessions/{id}/meta`): em sessão encerrada, mudar o deload, a data ou a hora dispara a reavaliação, na mesma transação e depois do UPDATE. Mudança de deload chama `_refresh_deload_targets`, que regrava o `rpe_target_effective` das séries gravadas: a série com alvo de protocolo gravado mantém esse alvo (`keeps_protocol_target`, a mesma regra do PUT), sem recálculo ao vivo; as demais usam a prescrição atual, e só as séries cujo alvo muda são regravadas. Mudança de data ou hora usa a âncora das datas antiga e nova.
- **Alvo gravado da série**: `api/app/domain/effective_intensity.py`, `stored_rpe_target(set_type, intensity, *, protocol_target=None, deload_source=None, technique_code=None)`. Regra: série preparatória não tem alvo (salvo alvo de protocolo); alvo de protocolo vale; sessão de deload usa `deload_effort_target`; senão, o alvo efetivo da prescrição. Consumidores: `workout_sessions.add_session_set` (POST da série do Modo Treino), `_refresh_deload_targets` e `services._update_exercise_log_tx` (PUT e POST do Registro).
- **Âncora e gatilho da reavaliação** (`api/app/progression_state.py`): `correction_anchor(*moments)` devolve o instante mais antigo entre os informados (`date` vale a meia-noite; `None` é ignorado). `reevaluate_after_correction(..., anchor_at=None)` começa a regravar na posição mais antiga entre a sessão corrigida e a âncora (`progression_state._first_index_from`). As sessões anteriores não são regravadas. O instante da sessão tem um único dono, `api/app/domain/progression_evaluator.py::session_moment` (sobre `SessionRecord.occurred_at`, que `api/app/domain/training_history.py` preenche com `_SESSION_ORDER`); `progression_state._first_index_from` consome esse instante para achar a posição da âncora, sem ordenar nem recalcular. Se houver decisões legadas sem sessão (`activity_id` NULL), regrava todas e apaga as legadas. Emite o log estruturado `progression.reevaluated` com `duration_ms`, `sessions_read`, `sessions_rewritten`, `decisions_written` e `legacy_decisions_replaced`.
- **Mescla das séries** (`api/app/exercise_set_update_merge.py`, `merge_sets_for_update`): `ExerciseSetUpdateIn.source_set_number` (`api/app/models.py`). Se alguma série enviada traz o campo, o pareamento com a série gravada é por ele (remover ou reordenar não desloca o que é preservado). Sem o campo (cliente antigo), o pareamento é por `set_number`. Série gravada pendente que recebe reps nulas ou 0 continua pendente (`resolve_reps_pending`; a tela só lê `reps_pending` do servidor e não decide a pendência). Série que continua pendente depois do merge não é cobrada pelas reps da medida: `measure_codes_for_merged_set` (mesmo módulo) tira `REPS_REQUIRED` e `REPS_BELOW_MIN` da validação; o `services._update_exercise_log_tx` só chama. `muscle_pain` e `joint_pain` enviados como null são limpos.
- **Frontend (só apresentação)**:
  - `frontend/src/lib/exercise-set-edit.ts`: rótulo "Repetições (pendente)" quando a série está pendente, reps pendentes continuam nulas no payload, e o payload leva `source_set_number`.
  - `frontend/src/lib/exercise-log-save.ts`: `POST /exercises/logs` quando o treino está encerrado.
  - `frontend/src/lib/deload.ts`: `DELOAD_LABEL` é a única definição, usada em `WorkoutDetailClient.tsx`, `TreinoDetalheScreen.tsx` e `DeloadPanels.tsx`.
  - O Switch de deload fica na tela de detalhe do Registro, fora do modal "Editar treino" (`NewWorkoutModal`).

## Trade-offs

- Ganha: a correção de data, hora, deload e exercício produz no motor o mesmo estado do replay completo, sem o replay completo. Para a marca e o deload, a equivalência com `rebuild_for_user` foi medida na ADR 0017; para data e hora, não foi medida.
- Ganha: uma regra só para o alvo gravado e para o estado encerrado, com consumidores listados no mapa.
- Paga: o custo da reavaliação cresce com a antiguidade da sessão corrigida, porque todas as decisões dali em diante são regravadas.
- Paga: o `rpe_target_effective` das séries é regravado quando o deload muda. O status de esforço continua sendo lido das séries na consulta, então não é regravado.

## Consequências

- Sem migração. O rollback é reverter o código.
- Mudam em produção só depois da promoção: a data e a hora de treino encerrado passam a reavaliar; o deload de treino encerrado passa a se corrigir e a regravar o alvo das séries; o `POST /exercises/logs` passa a existir; `muscle_pain`/`joint_pain` enviados como null passam a limpar o valor gravado (no PUT e no POST); o GET de deload devolve `can_toggle` falso sem `treino.modo_treino`/`editar`; o POST exige ao menos uma série; PUT e POST recusam mais de 100 séries (teto novo nos dois, `MAX_SETS_PER_LOGGED_EXERCISE`); a série pendente deixa de ser cobrada pelas reps da medida.
- Impacto em produção medido em 19/11/2026 (leitura por SELECT, RDS Data API): 85 sessões encerradas de 2 usuários (maior histórico: 84); 0 sessões encerradas com deload, 0 séries nelas, 0 séries de protocolo nelas e 0 séries com `reps_pending = 1` em sessão encerrada; 23 sessões com `session_status` nulo, fora do histórico da progressão. Logo, nenhuma linha gravada hoje muda com o alvo de deload (mudança a) nem com a pendência (mudança c). A mudança (b) (data ou hora reavalia a partir da posição mais antiga) tem custo medido só pelo log `progression.reevaluated`; o tempo na RDS Data API não foi medido antes da entrega (a referência de cerca de 3 s da issue #219 não foi conferida). Teto de 100 séries (medido na mesma leitura): o exercício com mais séries gravadas tem 13; nenhum tem mais de 100 (nem mais de 30); nenhum registro existente é afetado. A mescla de séries e a validação do item da ficha no POST não foram medidos em produção.
- A reavaliação em produção passa a ser medida pelo log `progression.reevaluated` (`api/app/progression_state.py`).
- Este ADR complementa a "Revisão pela #205" da ADR 0017 em dois pontos: mudança de data ou hora passa a reavaliar, e o `rpe_target_effective` deixa de ficar com o alvo da época. A decisão D1 e a D2 revista da ADR 0017 continuam valendo; por isso a ADR 0017 não é marcada como substituída.
- O mapa `docs/motores/FONTES_DA_VERDADE.md` (§105, linhas de encerramento, alvo gravado, âncora e deload) é a referência de dono para estas regras.
- Limites conhecidos (não resolvidos neste lote):
  - Ligar e depois desligar o deload de uma sessão encerrada não devolve a série de protocolo ao alvo original: o alvo gravado passa a ser o de deload e é ele que se mantém (`_refresh_deload_targets` mantém o protocolo gravado, `keeps_protocol_target`). É teórico enquanto `DELOAD_ALLOWED_TECHNIQUES` for vazio (`api/app/domain/deload.py`). Há um comentário `shortcut` em `workout_sessions._refresh_deload_targets` (linha 655) com essa condição para revisar.
  - O caminho de `started_at` com fuso no PUT (conversão por `_to_user_wall_clock`) não tem teste unitário próprio; os testes de âncora de `started_at` usam valor sem fuso (`api/tests/unit/test_registro_edicao_treino_encerrado.py`, `test_put_que_muda_so_started_at_ancora_no_instante_antigo_e_no_novo`).
  - `_refresh_deload_targets` e `POST /exercises/logs` foram provados só por teste unitário. O MySQL real só roda no CI de integração, e nenhuma tela foi verificada em navegador.
  - O SQL com o literal `'done'` continua existindo fora do dono: `training_history.py` (`SESSIONS_SQL`). `progression_state.py` agora interpola `SESSION_STATUS_DONE`. Não há guarda para o literal; a regra nova é o predicado Python.
  - Exceção conhecida de literal de status (consumo, não regra; anterior a este lote, não tocada): o frontend compara `'done'` em `frontend/src/lib/exercise-log-save.ts` (escolhe a rota), em `WorkoutDetailClient.tsx` e `TreinoDetalheScreen.tsx` (`canFinish`) e em `workout-mode-plan-flow.ts`; e `intelligence/dashboard/metrics_registry.py` (`normalize_session_status`) compara o texto em Python. O servidor decide: sessão não encerrada responde 409. Registro no mapa, §105, linha de `domain/session_status.py`.
  - Tempo da reavaliação na RDS Data API de produção: não medido antes da entrega.
- Sugestões de revisão não bloqueantes, aceitas como estão:
  - Não há teto de exercícios acrescentados a uma sessão encerrada, nem limite de taxa por usuário no `POST /exercises/logs`. O custo recai só sobre o histórico do próprio usuário, e o custo por requisição é o mesmo do `PUT`.
  - `_refresh_deload_targets` não recalcula o alvo de protocolo gravado: ele é mantido (`keeps_protocol_target`, `protocol_target` gravado, `protocol_kept`), e só as séries sem protocolo recebem o alvo da prescrição atual. Regrava só a série cujo alvo muda (`api/app/workout_sessions.py`, comparação antes do UPDATE).
  - O frontend compara `session_status === 'done'` para escolher a rota (`frontend/src/lib/exercise-log-save.ts`). É consumo de status, não regra; a API não entrega um booleano pronto.

## Itens das issues descartados, com o motivo

- **Sessão com `session_status` NULL (importada ou legada) não reavalia nem tem o deload destravado.** Ela está fora do histórico da progressão por regra (`training_history.py`, filtro `session_status = 'done'`), então não há decisão para regravar. Para o deload, `can_toggle_deload` só destrava com `is_closed_session`; sem isso, vale a trava antiga por série gravada.
- **`progression_reevaluated` vem `false` com o motor desligado.** Já era assim: `services._reevaluate_if_done` devolve `reevaluate_after_correction(...) is not None`, e a função devolve `None` com `shadow_enabled()` falso (`progression_state.py`). O relato da issue #215 sobre isso não se confirmou no código.
- **O frontend não consome `progression_reevaluated`.** Campo informativo; nenhuma tela depende dele (busca em `frontend/src` sem resultado).
- **O interruptor de deload fica na tela de detalhe, fora do modal "Editar treino".** É uma ação imediata com efeito próprio (reavalia e regrava), não um campo do formulário.
- **Chamadas sem `user_id` (Telegram, MCP) não reavaliam.** Não existe chamador sem `user_id` neste repositório: as três chamadas de `services` (`update_exercise_log`, `create_exercise_log`, `delete_exercise_log`) vêm de `main.py` com `identity.user_id`. O ramo fica coberto pelo teste de exclusão sem `user_id` em `api/tests/unit/test_registro_edicao_treino_encerrado.py`.
- **Concorrência entre a reavaliação e o `finish` de outro treino do mesmo usuário não é serializada.** Nem a reavaliação (`progression_state.py`, `replay_progression.py`) nem `on_session_finish` tomam trava por usuário. O projeto usa `SELECT ... FOR UPDATE` sobre `users` em outros fluxos (`deload_periods.py`, `nutrition_ai_quota.py`, `users.py`), mas não aqui. A janela é estreita, e o estado se corrige no próximo `finish` ou no replay. Limite aceito.
- **Tempo da reavaliação na RDS Data API de produção.** Não medido antes da entrega; passa a ser medido pelo log `progression.reevaluated`.

## Testes

Arquivos deste lote: `api/tests/unit/test_session_status_dono.py`, `api/tests/unit/test_reavaliacao_meta_e_exclusao.py`, `api/tests/unit/test_registro_edicao_treino_encerrado.py`, `api/tests/unit/test_deload_correcao_treino_encerrado.py`, `api/tests/unit/test_reavaliacao_retroativa_progressao.py`, `api/tests/unit/test_form_practice_sessao.py`, `api/tests/unit/test_access_guarda_cobertura_rotas.py`, `api/tests/unit/test_access_paridade_guards.py` e os testes do frontend `frontend/src/lib/deload.test.ts`, `frontend/src/lib/exercise-log-save.test.ts` e `frontend/src/lib/exercise-set-edit.test.ts`. A execução destes testes não está registrada neste ADR.

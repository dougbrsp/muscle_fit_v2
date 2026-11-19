# ADR 0019: Tipo de série, medida, dor por série e objetivo editáveis depois do treino pelo Registro (issue #219, lote 2)

- **Status**: Aceita. Branch `feat/registro-campos-pos-treino-219`, sem commit no checkout no momento da redação. Nada foi publicado nem promovido a produção.
- **Data**: 2026-11-19
- **Complementa**: ADR 0017 (Revisão pela #205) e ADR 0018 (edição de treino encerrado). Nenhuma das duas é substituída; a regra de reavaliação e de alvo gravado da 0018 continua valendo.

## Contexto

O Registro de exercícios (`/registro-exercicios`) corrige treinos já encerrados. A ADR 0018 deixou fora da edição quatro campos, sem decisão explícita:

1. o tipo de série (`set_type`), que ficava travado depois da gravação;
2. a medida da série (`measure`), também travada;
3. a dor por série (`muscle_pain`, `joint_pain`), que só era gravada no exercício e não era consolidada a partir das séries;
4. o objetivo do treino do exercício (`exercises.training_goal`, vocabulário de `api/app/domain/training_goal.py`), que o PUT não aceitava.

Antes deste lote, `merge_sets_for_update` (`api/app/exercise_set_update_merge.py`) gravava o que a rota recebia, sem trava de tipo nem de medida. Isso permitia que uma série de técnica (`rest_pause`, `drop_set`, `forced_rep`) ou laboral virasse outro tipo na edição, sem regra no motor dono.

Em 19/11/2026 o usuário decidiu tornar os quatro campos editáveis depois do treino, pelo Registro de exercícios, com a regra de troca no motor dono e não na tela. O lote saiu da issue #219 do GitHub (`dougbrsp/muscle_fit_v2`).

## Alternativas consideradas

- **Manter a trava de tipo e medida em qualquer edição (status quo).** Descartada: o usuário pediu a troca de tipo entre séries comuns (aquecimento, feeler, backoff e trabalho), e a medição mostra 2.944 de 3.046 séries nessa situação.
- **Liberar tudo, gravando o que vier.** Descartada: uma série de técnica ou laboral poderia virar série comum e distorcer volume, progressão e e1RM, que leem `exercise_sets` pelo tipo (`api/app/set_types.py`). A regra de quais tipos podem trocar tem que ficar num dono só.
- **Trava na tela.** Descartada: a regra ficaria duplicada no frontend. O frontend só esconde a opção (`setTypeSelectData`, `canEditSetKind` em `frontend/src/lib/exercise-set-edit.ts`); quem decide é o servidor.
- **Trava dentro de `merge_sets_for_update` por padrão.** Descartada: a importação e os testes que usam a mescla fora da rota quebrariam. Ficou um parâmetro `lock_types=False` por padrão, ligado só pela rota de edição.
- **Dor por série consolidada no frontend.** Descartada: a dor do exercício é um valor gravado, e a regra de máximo tem de bater com o Modo Treino. Ficou um dono só (`rollup_exercise_pain`).
- **Validar o objetivo no schema Pydantic ou na tela.** Descartada: o vocabulário é do dono `training_goal`. A rota chama `optional_goal_value` e converte o erro em 422.

## Decisão

1. **Troca de tipo** (`api/app/set_types.py`): só série comum troca de tipo entre si. `SWAPPABLE_SET_TYPES` = `working`, `backoff`, `warmup`, `feeler`; `is_swappable_set_type`, `can_change_set_type(stored, requested)` e `can_add_set_type(requested)` decidem. O espelho `frontend/src/lib/set-types.generated.json` leva os campos `swappable` e `is_swappable`, com paridade em `api/tests/unit/test_m76_lote3b_set_types_fe.py`. `frontend/src/lib/set-types.ts` (`SWAPPABLE_SET_TYPES`, `isSwappableSetType`) só lê o espelho.
2. **Trava de tipo e medida** (`api/app/exercise_set_update_merge.py`): `merge_sets_for_update(incoming, stored, *, lock_types=False)`. Com `lock_types=True`, `_enforce_edit_locks` levanta `SetEditLockedError` (códigos `set_type_locked` e `set_measure_locked`). Série de técnica e série laboral mantêm tipo e medida. Série nova só nasce com tipo comum. Reenviar o mesmo tipo sempre passa.
3. **Rotas** (`api/app/services.py::_update_exercise_log_tx`, usado pelo `PUT /exercises/logs/{exercise_id}` e pelo `POST /exercises/logs`): liga `lock_types=True`. `api/app/main.py` traduz `SetEditLockedError` em 422 com `{code, set_number, message}`.
4. **Objetivo do treino**: `ExerciseLogUpdateIn.training_goal` (`api/app/models.py`) é opcional. `optional_goal_value` (`api/app/domain/training_goal.py`) é o dono da validação; valor fora do vocabulário responde 422 `training_goal_invalid`. A gravação usa `exercises.training_goal = COALESCE(enviado, gravado)` (`services.py`, UPDATE da linha 1367): ausente ou null preserva o gravado. No POST, o valor enviado prevalece sobre o do item da ficha. Em sessão encerrada, a reavaliação da progressão roda depois da gravação.
5. **Dor por série** (`api/app/workout_sessions.py::rollup_exercise_pain(cur, exercise_id, *, keep_stored_when_empty=True)`, com `_read_pain_rollup`): regrava `muscle_pain` e `joint_pain` do exercício com o máximo das séries confirmadas. Sem dor em nenhuma série, vale o campo do exercício gravado. A edição chama a função depois de regravar as séries (`services.py`, linha 1471). O Modo Treino (`_refresh_exercise_aggregates`) lê o mesmo `_read_pain_rollup`.
6. **Frontend (só apresentação)**: `frontend/src/lib/exercise-set-edit.ts` (`setTypeSelectData`, `canEditSetKind`, `changeSetMeasure`, `setPainRollup`), `frontend/src/lib/exercise-log-save.ts` (`trainingGoalPayload`, `exerciseLogSaveErrorMessage`), telas `frontend/src/app/(app)/registro-exercicios/[activityId]/WorkoutDetailClient.tsx` e `frontend/src/mobile/screens/telas/TreinoDetalheScreen.tsx`. `setPainRollup` não decide nada gravado.
7. **Sobras do lote anterior**: comentário de `workout_sessions.add_session_set`, docstring de `api/tests/unit/test_guarda_series_pendentes.py` e teste do `started_at` com fuso no PUT.

O mapa `docs/motores/FONTES_DA_VERDADE.md` registra o dono de cada uma dessas regras (linha da troca de tipo, §105 com a mescla, a dor e o objetivo, e o bloco "Lote 2 de 19/11/2026" em Impacto em produção).

## Trade-offs

- Ganha: uma regra só para troca de tipo, medida, dor e objetivo, com consumidores listados no mapa. A tela não decide mais nada.
- Ganha: o tipo de técnica e laboral fica protegido, sem bloquear a correção de aquecimento, feeler, backoff e trabalho.
- Paga: uma edição que antes passava agora responde 422. Quem enviava um tipo travado pelo cliente antigo recebe o erro.
- Paga: a dor do exercício passa a ser recalculada a cada PUT e POST. O custo é uma leitura das séries do exercício, que é pequena.
- Paga: a função `rollup_exercise_pain` e o UPDATE de `_refresh_exercise_aggregates` leem a mesma consulta, mas cada um grava. Divergência aberta, registrada no mapa.

## Consequências

- Sem migração. Nenhum dado gravado muda no deploy; muda só o que a edição aceita e grava daqui para frente. O rollback é reverter o código.
- Medição de produção em 19/11/2026 (leitura por SELECT, RDS Data API, feita pelo Tech Lead):
  - 3.046 séries, todas com medida `reps`. Por tipo: `working` 2.609, `feeler` 268, `warmup` 67, `rest_pause` 50, `forced_rep` 19, `laboral` 17, `drop_set` 16.
  - 102 séries ficam com tipo e medida travados (85 de técnica e 17 laborais). As outras 2.944 são trocáveis.
  - 877 exercícios, todos `hypertrophy`.
  - Dor: 2 séries com dor muscular gravada e 2.207 com dor articular gravada (4 acima de zero). Nenhum exercício tem dor diferente do máximo das próprias séries, então a consolidação não muda nenhum valor existente.
- Três mudanças de semântica para quem consome a API:
  - troca de tipo ou medida travada responde 422 com `{code, set_number, message}`;
  - todo PUT e POST consolida a dor do exercício pelo máximo das séries;
  - mudar tipo, medida ou objetivo em sessão encerrada reavalia a progressão, pela mesma regra da ADR 0018. O efeito não foi medido, porque depende de quem fizer a edição.
- A nova regra de troca de tipo e medida é a fonte única; qualquer tela ou relatório que quiser outra regra tem de passar pelo dono (`api/app/set_types.py`, `api/app/exercise_set_update_merge.py`).
- Itens de revisão do lote anterior, aceitos como estão, com o motivo:
  - `gym_id` e `equipment_id` do corpo do PUT e do POST de `/exercises/logs` não são validados contra o catálogo. Já era assim no PUT; a referência fica só na linha do próprio usuário, então é problema de integridade, não de exposição.
  - `sequence = MAX + 1` do exercício novo não tem trava. Duas criações simultâneas na mesma sessão poderiam repetir a sequência. Só afeta o próprio usuário, e a tela cria um exercício por vez.
- Limites conhecidos (não resolvidos neste lote):
  - nenhuma tela foi verificada em navegador ou iPhone;
  - a trava e a consolidação da dor foram exercitadas só por teste unitário (`api/tests/unit/test_registro_edicao_campos_pos_treino.py`, `test_registro_edicao_tipo_e_dor.py`); a integração com MySQL roda só no CI;
  - `merge_sets_for_update` mantém `lock_types=False` por padrão; são as rotas que ligam a trava;
  - os rótulos "Série de trabalho" e "Backoff" são novos e só de exibição;
  - `setPainRollup` no frontend é exibição e não tem fixture de paridade;
  - a dor tem duas escritas sobre a mesma consulta de leitura (`rollup_exercise_pain` e o UPDATE de `_refresh_exercise_aggregates`); unificar quando o arquivo for tocado.

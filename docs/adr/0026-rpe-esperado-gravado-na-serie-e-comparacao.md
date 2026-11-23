# ADR 0026: RPE esperado gravado na série e comparação com o realizado (issue #182)

- **Status**: Aceita; regra de gravação revista pela #241 em 21/11/2026 (ver "Revisão pela #241"); critério de comparação do detalhe alinhado ao histórico e ao relatório em 22/11/2026 (limite (h) resolvido; ver "Atualização de 22/11/2026"); trava de envio simultâneo e confirmação de série pendente no lote 32 (ver "Atualização de 22/11/2026 (lote 32, #241)"). Branch `feat/rpe-esperado-realizado-182`; revisão na branch `fix/rpe-fora-de-ordem-l19`. Não publicada nem promovida a produção.
- **Data**: 2026-11-20
- **Complementa**: ADR 0013, pendência 1 de "Fora desta entrega" (comparação esperado × realizado em histórico). Nenhuma decisão anterior é substituída. O alvo efetivo da série (`rpe_target_effective`, ADRs 0013 e 0024) não muda de sentido.

## Contexto

O ADR 0013 deixou fora da entrega a comparação esperado × realizado em histórico e relatórios, porque a sessão não gravava o alvo planejado na série. O Modo Treino mostrava o esperado ("RPE esperado: N", `frontend/src/lib/workout-mode-execution-view.ts`), mas esse valor não ficava no banco.

Sem o valor gravado, a comparação só seria possível recalculando o esperado depois. Esse recálculo não reproduz o que o atleta viu: o alvo cobrado depende do teto de carga e da prontidão do dia do treino (`api/app/domain/effective_intensity.py::set_intensity_reading`, L505, e `charged_rpe_targets_by_set`, L559), que mudam depois. A ficha congela o alvo da rampa no snapshot (`prescription_json`, ADR 0013, seção "Snapshot"), mas não guarda o teto do dia.

Restrições reais:

- o valor gravado vem do dono, não da rota nem da tela. `set_intensity_reading` resolve o degrau da rampa (`planned_ramp_rpe`), o protocolo da técnica e o deload; `charged_rpe_targets_by_set` é o valor que a ficha expõe;
- `rpe_target_effective` é o alvo efetivo gravado em cada série (coluna TINYINT de `exercise_sets`, `api/db/migrations/037_prescricao_congelada.py:22`). Ele já é lido pelo status de intensidade da sessão, pelo deload e pelo protocolo, e não pode mudar de sentido;
- a comparação é leitura de esforço. Não se confunde com `progression_engine.set_intensity_status` (alcance do alvo e teto de segurança), que não muda;
- regra dos motores (`docs/motores/FONTES_DA_VERDADE.md`; guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`): a conta fica no dono.

## Alternativas consideradas

- **Calcular o esperado na leitura**, a partir da prescrição e do teto atuais. Recusada: o resultado mudaria depois do treino, e a comparação deixaria de ser a que o atleta viu no Modo Treino.
- **Backfill das séries antigas.** Recusada: não é reconstruível, porque o teto e a prontidão do dia não estão gravados.
- **Tolerância na comparação** (ex.: diferença de até 0,5 conta como "no esperado"). Recusada nesta entrega: seria número novo sem decisão do usuário. A escala é de meio em meio ponto e a comparação é exata.
- **Guardar o esperado em `rpe_target_effective`.** Recusada: esse campo é lido pelo status de intensidade, pelo deload e pelo protocolo. Misturar os dois mudaria leituras existentes.
- **Tabela própria de comparação.** Recusada: o esperado é atributo da série. Uma tabela nova duplicaria `exercise_sets`.

## Decisão

1. **Coluna e migração.** `exercise_sets.rpe_target_planned DECIMAL(3,1) NULL DEFAULT NULL`, em `api/db/migrations/121_rpe_alvo_planejado_da_serie.py` (`add_column_sql`, com `ALGORITHM=INSTANT`). Sem FK, sem índice e sem backfill. `plan(db)` é idempotente: coluna ausente gera o `ADD COLUMN`; coluna presente com o tipo esperado não faz nada; coluna divergente levanta `RpeTargetPlannedError` antes de qualquer statement. `down` executa `DROP COLUMN`. Plano coberto por `api/tests/unit/test_migration_121_plano.py`.
2. **Dono do valor.** `effective_intensity.planned_set_rpe_target(set_type, charged_target)` (L407) devolve o alvo cobrado da série. Devolve nulo quando a série é preparatória (`PREPARATORY_SET_TYPES` em `api/app/set_types.py:54`: `warmup`, `feeler`, `laboral`) ou quando não há alvo. Recebe `SetIntensityReading.target`, que `set_intensity_reading` calcula para qualquer tipo de série.
3. **Gravação** (revista pela #241 para série que chega fora de ordem; ver "Revisão pela #241"). `workout_sessions.add_session_set` (`api/app/workout_sessions.py`, L1477) calcula o valor (L1615) e o grava no INSERT (L1643-1662). A resposta o devolve (L1739). Na repetição idempotente (mesmo `client_key`), `_duplicate_set_response` (L1359; L1407) devolve o valor GRAVADO, e não o recalculado. O PATCH, `update_session_set` (`api/app/workout_sessions.py`), não aceita o campo: ele não está em `PATCHABLE_SET_FIELDS` (`api/app/exercise_set_update_merge.py:102`). Só o regrava ao confirmar série pendente (lote 32, ver a atualização).
4. **Preservação.** `SERVER_KEPT_SET_FIELDS` (`api/app/exercise_set_update_merge.py:89-97`) inclui `rpe_target_planned`. Na edição pelo Registro, `services._update_exercise_log_tx` (`api/app/services.py:1275`; L1452 e L1483) mantém o valor da série gravada com o mesmo número. Série criada pelo Registro fica nula. A importação (`upsert_activity`, `services.py:467`) não cita a coluna e fica nula.
5. **Comparação sem tolerância.** `effective_intensity.rpe_vs_expected(actual, expected)` (L442) devolve `None` se faltar um dos dois valores. Senão, `delta` = realizado − esperado, e `status` é `below`, `on_target` ou `above` (constantes `RPE_VS_BELOW`, `RPE_VS_ON_TARGET` e `RPE_VS_ABOVE`, L422-424). Os rótulos estão em `RPE_VS_LABELS` (L425): "Abaixo do esperado", "No esperado", "Acima do esperado". A conta é em `Decimal`. `rpe_vs_expected_counts(rows)` (L453) agrega `below`, `on_target`, `above` e `compared`; série sem os dois valores não conta.
6. **Leituras.**
   - Detalhe da atividade: `services.activity_detail` devolve em cada série `rpe_target_planned` (número ou nulo) e `rpe_vs_expected` (objeto ou nulo). A comparação sai de `effective_intensity.set_rpe_vs_expected`, que só compara série de trabalho desde 22/11/2026 (ver "Atualização de 22/11/2026"). Em série por lado, o RPE comparado é `exercise_sets.rpe`, que é o canônico da série.
   - Histórico de progressão: `analytics._fetch_exercise_set_rows` (`api/app/analytics.py:1179`, SELECT com `es.rpe_target_planned` em L1261) alimenta `analytics._build_progression_sessions` (L1279). Só séries de trabalho entram na contagem (L1310). O ponto de sessão ganha `rpe_vs_expected_counts` (L1374), nulo quando nenhuma série foi comparada (`_rpe_counts_or_none`, L1381).
   - Frontend, só formatação: `frontend/src/lib/rpe-expected-vs-actual.ts` (`rpeExpectedSuffix`, `rpeExpectedCountsText`, `rpeExpectedReadonly`). `frontend/src/lib/exercise-set-edit.ts::formatLoggedSets` (L289) mostra `@RPE 8 (esperado 9 · Abaixo do esperado)` na linha de resumo, usada em `frontend/src/app/(app)/registro-exercicios/[activityId]/WorkoutDetailClient.tsx:1153` (web) e `frontend/src/mobile/screens/telas/TreinoDetalheScreen.tsx:937` (mobile). `ProgressaoScreen.tsx:588-589` mostra a contagem da sessão. O PUT do Registro (`setUpdatePayload`) não envia os campos novos, como cobre `frontend/src/lib/rpe-expected-vs-actual.test.ts` (L55-58).
7. **Casos de borda.** O valor gravado é o que a ficha mostrou no Modo Treino, e não o degrau cru da rampa:
   - rampa: o degrau da série, cortado pelo teto, via `set_effort_target` com `planned_ramp_rpe`;
   - sem rampa: o alvo efetivo da prescrição;
   - série preparatória: nulo;
   - treino sem ficha: nulo;
   - deload: o alvo que `set_intensity_reading` resolve para a sessão de deload;
   - protocolo de técnica: o alvo do protocolo, quando a série o tem.
   Séries de backoff e de aplicação de técnica (`rest_pause`, `drop_set`, `forced_rep`) não são preparatórias, então recebem o alvo cobrado. Desde 22/11/2026, nem o detalhe nem a contagem as comparam: o critério de série de trabalho é o mesmo em todas as leituras (ver Limites, h).

## Revisão pela #241 (21/11/2026)

Decisão do usuário de 21/11/2026, opção "Regravar na mesma sessão". A regra de gravação única das Decisões 3 e dos Trade-offs deixa de valer para a chegada fora de ordem. O restante desta decisão continua valendo.

**Motivo.** A posição ordinal de uma série de trabalho depende das séries anteriores já gravadas. Com a rampa 7-8-9 enviada na ordem 1, 3, 2, a série 3 gravava 8 (o degrau da série 2, que ainda não estava no banco) e a comparação esperado × realizado ficava um degrau errada.

**Regra nova.**
- O esperado continua gravado no envio da série.
- Depois de gravar uma série de trabalho confirmada, por repetições, sem protocolo, fora de deload e sem a marca de técnica, num exercício com rampa de RPE, o servidor relê as séries de trabalho do exercício por número e regrava o `rpe_target_planned` das de número maior cujo degrau mudou. Isso ocorre na mesma transação do envio (`workout_sessions._resync_planned_rpe_after_late_set`, chamada por `add_session_set` depois do INSERT).
- A posição é a ordinal entre as séries de trabalho, contando a pendente, como no envio.
- O degrau sai de `effective_intensity.ramp_planned_rpe_for_position`, o dono da composição. Só `rpe_target_planned` é escrito; `rpe_target_effective` não muda.

**Não muda.**
- Repetição idempotente (mesmo `client_key`) não recalcula nem regrava. Edição de série (PATCH) não recalcula, salvo a confirmação de série pendente (lote 32).
- O Registro não recalcula.
- Séries de técnica, de protocolo, de backoff, preparatórias e de deload não são regravadas.
- Exercício sem rampa não ganha leitura extra: a verificação de rampa sai antes de qualquer consulta.

**Limites da revisão.**
- A carga sugerida da próxima série, já devolvida ao aparelho antes da correção, não é corrigida.
- Série que chega PENDENTE e é confirmada depois (PATCH): RESOLVIDO no lote 32. A confirmação grava o degrau da posição dela e regrava as seguintes (ver "Atualização de 22/11/2026 (lote 32, #241)").
- Exclusão de série (renumeração) não dispara a correção.
- Concorrência entre dois envios do mesmo exercício: RESOLVIDO no lote 32 pela trava da linha do exercício (ver a atualização). A chave única de número da série (409 `set_number_conflict`) continua valendo.
- A conta do degrau cobrado tem um helper único no dono, `effective_intensity._charged_for_position`, usado por `ramp_planned_rpe_for_position` (regravação) e `charged_rpe_targets_by_set` (ficha). O envio da série (POST, em `workout_sessions.py`) continua compondo por `set_intensity_reading`, de propósito: a rota precisa do degrau cru para a carga da próxima série e decide protocolo e deload. A paridade entre o POST e o dono é garantida por `test_dono_igual_ao_post` (`api/tests/unit/test_rpe_esperado_fora_de_ordem.py`), que cobre 3 alvos efetivos e 4 posições.

**Verificação** (resultados reportados pelo autor da entrega; não reexecutados por este documento):
- 16 testes em `api/tests/unit/test_rpe_esperado_fora_de_ordem.py` (contagem conferida no arquivo): ordens 1,3,2; 3,2,1; 2,1,3; 2,3,1; 3,1,2 terminando em 7, 8, 9; ordem certa sem regravação; aquecimento atrasado; backoff e esperado nulo intactos; deload e alvo único sem leitura extra; repetição idempotente; PATCH; pendente; rampa mais curta que as séries; paridade com o POST.
- Com a função de regravação desligada, 8 dos 16 testes falham.
- Bateria dirigida do backend: 675 passaram.

## Trade-offs

- Ganha: o esperado fica fixo no momento em que o atleta o viu (revisto pela #241: fixo no envio, com correção só para série de trabalho que chega fora de ordem). A comparação não muda quando o teto ou o Modelo mudam depois. Não há tabela nova nem leitura nova de prescrição.
- Paga: séries anteriores ao deploy ficam sem comparação para sempre.
- Paga: a comparação usa um número por série, sem tolerância. Um realizado de 8,5 frente a um esperado de 8 conta como "acima do esperado".
- Paga: o campo novo é coluna de `exercise_sets`, com janela de migração (ver Plano).

## Consequências

- **Dados.** Uma coluna nova, nula, sem efeito nos dados existentes.
- **Contrato.** `services.activity_detail` e o histórico de progressão ganham campos aditivos.
- **Operação.** A promoção exige `RUN_DB_MIGRATIONS=true` (ver Plano).
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md` ganha a linha do alvo gravado da série (§105) e o bloco "Lote 9 de 20/11/2026 (#182)" em "Impacto em produção". A linha de RPE-alvo (§3.1) deixa de dizer que a comparação está fora. Pela revisão de 21/11/2026 ganha também o bloco "Lote 19 de 21/11/2026 (#241)".
- **Próximas decisões.** Tolerância, comparação no relatório Markdown e uso na progressão são decisões novas, com replay pelo motor antes.

## Plano de migração e rollback

1. A migração 121 vai ao Aurora antes das Lambdas. O pipeline roda o `migrate` antes do `update-function-code`: `.github/workflows/deploy-producao.yml:425-427` (gate `vars.RUN_DB_MIGRATIONS == 'true'`) e `:434` (Lambdas). Sem a variável ligada, o código novo sobe sem a coluna, e o POST da série falha com coluna inexistente (MySQL 1054). Por isso a promoção exige `RUN_DB_MIGRATIONS=true`, e a variável volta a `false` depois do deploy, como nos lotes anteriores.
2. Rollback de código: seguro. O código anterior não lê nem grava a coluna (ela não existe na `main`), então reverter só o código deixa a coluna inofensiva.
3. Rollback de dados: `down` executa `DROP COLUMN`, que apaga os valores gravados desde o deploy. Usar só se a comparação for retirada.

## Limites e pendências

- (a) O relatório em Markdown não mostra a comparação. `api/app/intelligence/report/daily_breakdown.py` lê só agregados por SQL. Contar por série exigiria consulta nova de séries ou comparar em SQL, o que sai do dono. Resolvido pelo ADR 0030 (#241): o relatório lê as séries numa consulta da janela e a contagem sai de `effective_intensity.rpe_vs_expected_counts`.
- (b) A tela web de progressão (`frontend/src/app/(app)/dashboard/progressao/DashboardProgressaoClient.tsx`) não mostra a contagem. Só a tela mobile (`ProgressaoScreen.tsx:588`) mostrava. Entregue pelo ADR 0030 (#241): a contagem também aparece na tela web (`DashboardProgressaoClient.tsx`, L407-412).
- (c) O esperado não aparece na tela de EDIÇÃO da série, só na linha de resumo. `rpeExpectedReadonly` existe e não é usada por nenhuma tela.
- (d) Em série com repetições pendentes ou medida que não é por repetições, o valor gravado é o alvo do exercício ou do protocolo, e não o degrau da rampa.
- (e) Séries anteriores ao deploy, criadas pelo Registro ou importadas (inclusive do relógio) ficam sem esperado.
- (f) Os rótulos da contagem no histórico ("abaixo", "no esperado", "acima") são texto de apresentação definido na função pura do frontend (`rpeExpectedCountsText`), porque a API manda só os números. Mudar o rótulo não muda a comparação.
- (g) Nenhuma tela foi vista em navegador nem em iPhone.
- (h) RESOLVIDO em 22/11/2026 (#241, escolha do usuário). Antes, o detalhe comparava também séries de backoff e de aplicação de técnica, e o histórico e o relatório contavam só séries de trabalho. Agora o detalhe segue o mesmo critério, no dono (`set_rpe_vs_expected`). O `rpe_target_planned` dessas séries continua gravado e na resposta do detalhe; só a comparação some.
- (i) A expressão "alvo do exercício" no docstring da migração 121 (L6) não corresponde à coluna, que é gravada por série (`037_prescricao_congelada.py:22`).

## Atualização de 22/11/2026 (#241, detalhe do treino)

Escolha do usuário de 22/11/2026: o detalhe do treino compara o RPE esperado com o realizado só nas séries de trabalho, pelo mesmo critério do histórico e do relatório (limite (h)).

Estado atual:

- Dono: `effective_intensity.set_rpe_vs_expected(row)` devolve a comparação só para série de trabalho (`set_types.is_working_set`, que inclui o legado sem tipo). `rpe_vs_expected_counts` passou a usar essa mesma função. `services.activity_detail` a chama por série.
- Backoff, técnica e preparatória saem sem comparação no detalhe. O `rpe_target_planned` continua na resposta.
- Histórico (`analytics`) e relatório (`daily_breakdown`) não mudaram de número: já filtravam antes de contar.
- Frontend: `rpeExpectedSuffix` (`frontend/src/lib/rpe-expected-vs-actual.ts`) só mostra "(esperado X)" quando o servidor manda a comparação. Rótulo vazio cai na reserva do módulo.

Impacto em produção (medição do Tech Lead em 22/11/2026, só leitura, no banco de desenvolvimento, cópia da produção do usuário 1): `exercise_sets` por tipo: working 2.533, feeler 260, warmup 63, rest_pause 50, forced_rep 19, drop_set 16, laboral 15. Nenhuma série tem `rpe_target_planned` gravado (a coluna entrou em produção em 22/11/2026, migração 121). Logo nenhum detalhe de treino existente muda; a mudança vale para treinos registrados depois.

Não medido: nada em tela.

Limite: PWA antigo em cache mostra "(esperado X)" sem rótulo em série que não é de trabalho, até atualizar.

## Atualização de 22/11/2026 (lote 32, #241)

**Trava de envio simultâneo.** `workout_sessions.add_session_set` trava a linha do exercício (`SELECT ... FROM exercises WHERE id = %s FOR UPDATE`) em todo envio novo. O reenvio idempotente (mesmo `client_key`) responde antes da trava. A contagem de séries de trabalho e a regravação (`_resync_planned_rpe_after_late_set`, que lê as séries de trabalho com `FOR UPDATE`) saem depois dela, na leitura corrente. Dois envios do mesmo exercício ficam, assim, em fila. Ordem única de travas, no envio e na confirmação: primeiro a linha do exercício (`exercises`), depois as séries (`exercise_sets`), para não haver deadlock entre os dois. Não medido: a trava em banco real sob concorrência (roda só no CI, com `ci-completo`).

**Confirmação de série pendente.** Ao confirmar, por `update_session_set` (PATCH), uma série de trabalho que tinha o esperado gravado no envio, o servidor grava `rpe_target_planned` com o degrau da posição dela, pela mesma composição do envio (`effective_intensity.planned_set_rpe_target`), e regrava as seguintes (`_resync_planned_rpe_after_late_set`). Condições: a série era pendente e agora não é, é de trabalho, por repetições, sem protocolo, fora de deload e sem a marca de técnica. Pendente sem esperado no envio segue sem esperado. Série de técnica (`technique_code` preenchido) fica fora da regravação. Sem retroativo: as séries já confirmadas não mudam. `rpe_target_effective` não muda.

**Escolhas do usuário de 22/11/2026 que tocam esta decisão** (pelo formulário, todas "não fazer"):

- mostrar o esperado no formulário de edição da série: não feito (limite (c) segue);
- isolar a falha da correção do esperado do envio: não feito; a correção segue na transação do envio;
- trava no deploy contra migração desligada: não feito; o Plano, item 1, segue dependendo de `RUN_DB_MIGRATIONS=true` manual;
- corrigir o comentário da migração 121: não feito; a docstring (L6) segue com "alvo do exercicio" (limite (i)).

Impacto em produção e medições: ver o bloco "Lote 32" em `docs/motores/FONTES_DA_VERDADE.md`.

## Verificação

- Suíte unitária inteira do backend, local: 17.903 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Frontend: 71 testes Vitest dos diretórios tocados passaram; `tsc --noEmit` sem erro.
- Não verificado: a migração 121 contra banco real; os testes de integração (rodam no CI com o rótulo `ci-completo`); nenhuma tela foi vista em navegador nem em iPhone.

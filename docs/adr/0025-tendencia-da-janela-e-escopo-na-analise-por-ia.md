# ADR 0025: Tendência da janela do exercício e escopo do snapshot da análise por IA (issues #176 e #177)

- **Status**: Aceita. Branch `feat/tendencia-janela-e-escopo-176-177`. Não publicada nem promovida a produção.
- **Data**: 2026-11-20
- **Complementa**: ADR 0012 (escopo das leituras de progressão, issue #152). Nenhuma decisão anterior é substituída: o status da última exposição vs. a anterior continua sendo a leitura principal do exercício, e a tendência é uma segunda leitura ao lado dela.

## Contexto

A ADR 0012 deixou duas pendências explícitas no seu bloco "Fora desta decisão":

1. Separar, no detalhe do exercício, "última exposição vs. anterior" de "tendência da janela". Só existia a primeira leitura, e criar a tendência seria regra nova de motor.
2. O selo do exercício no snapshot da análise por IA (`frontend/src/app/(app)/dashboard/treinos/AnaliseIaSnapshot.tsx` e `frontend/src/mobile/screens/evolucao/AnaliseIaSnapshotMobile.tsx`) vinha sem dizer o que compara.

O selo "PROGREDINDO" de uma tela diz o que a última exposição fez frente à anterior. Quem olha a evolução de um exercício em um período também quer saber para onde a carga de força vai dentro da janela, e essa pergunta não tinha resposta no motor. Ela também não podia ser respondida no frontend (regra dos motores: `docs/motores/FONTES_DA_VERDADE.md`, §"Progresso / regressão / platô por exercício", linha 71, e a regra "Regras só nos motores").

Restrições reais:

- o dono do veredito do exercício é `api/app/intelligence/dashboard/comparability.py` (`classify_exercise_progression`, L1168), e a janela do veredito é `VERDICT_LOOKBACK_EXPOSURES`;
- o descritor de escopo é do dono `api/app/domain/progression_status_labels.py` (`PROGRESSION_SCOPES`, `progression_scope(code)`, ADR 0012);
- o snapshot da análise por IA é montado por `api/app/intelligence/progression_service.py::exercise_progression_context` e entra no prompt em `api/app/report_ai.py`; o prompt tem versão (`PROMPT_VERSION`) e não pode mudar de texto por causa de um campo de tela;
- sem migração e sem mudança em nenhum status, contagem ou percentual existente.

## Alternativas consideradas

- **Inclinação (regressão linear) do e1RM na janela.** Apresentada e recusada na decisão do usuário de 19/11/2026. O motivo da recusa não está registrado no código nem nesta entrega.
- **Primeira contra a última exposição da janela.** Apresentada e recusada na mesma decisão de 19/11/2026. Mesma observação: o motivo não está registrado.
- **Tendência no frontend (a tela calcula a média das metades).** Descartada: viola a regra de consumidor que não recalcula, e deixaria a regra sem fonte única.
- **Tendência também no snapshot da IA, em vez de só o escopo.** Descartada nesta entrega: a #177 pede só o escopo do selo, e levar a tendência ao prompt mudaria a análise que o usuário recebe. Fica como pendência (ver Limites, d).
- **Enum e motivos novos para a tendência** (ex.: `TREND_UP`). Descartada: o enum `ExerciseProgressionStatus` e os códigos de motivo já existentes cobrem progredindo, estável, regredindo e dados insuficientes; um rótulo novo criaria um segundo vocabulário para o mesmo conceito.

## Decisão

1. **Regra no dono do veredito.** `api/app/intelligence/dashboard/comparability.py`: dataclass `WindowTrend` (L1358) e função pura `window_trend(exposures, *, thresholds=THRESHOLDS)` (L1372), exportadas em `__all__`. Entram as exposições da janela exibida que:
   - têm a mesma chave de comparabilidade da última (`keys_match`, L179: exercício, equipamento, convenção de carga, contexto de fadiga, objetivo e academia);
   - são por repetições (`has_non_rep_sets` falso);
   - têm e1RM confiável (não nulo e `e1rm_reps` até `e1rm_max_reliable_reps`, o mesmo limite de confiabilidade que `e1rm_is_decisive` (L578) aplica a cada lado do par; aqui é verificado por exposição).
   Com N válidas em ordem cronológica, compara a média das primeiras `N // 2` com a média das últimas `N // 2`; com N ímpar, a exposição do meio fica de fora. A variação é `(final − inicial) / inicial`. Progride se ≥ `e1rm_progress_pct` (3%), regride se ≤ o negativo, senão é estável.
2. **Mínimo e calibração.** Menos de `window_trend_min_exposures` válidas (limiar nomeado, 4, em `api/app/intelligence/dashboard/metrics_registry.py::THRESHOLDS`, L370) ou máquina em calibração (`in_equipment_calibration`, L793) dá `INSUFFICIENT_DATA` com motivo.
3. **Sem vocabulário novo.** Reutiliza `ExerciseProgressionStatus` e os motivos já existentes (`REASON_E1RM_UP`, `REASON_E1RM_DOWN`, `REASON_NO_CHANGE`, `REASON_NOT_ENOUGH_EXPOSURES`, `REASON_EQUIPMENT_CALIBRATING`, `REASON_NO_E1RM_WITHOUT_RPE`). Os rótulos vêm de `EXERCISE_PROGRESSION_LABELS`.
4. **Versão da regra.** `metrics_registry.py::METRIC_VERSIONS` ganha `exercise_window_trend` = `exercise-window-trend-v1` (L53), separada de `progression-status-v2`.
5. **Descritor de escopo.** `api/app/domain/progression_status_labels.py`: `SCOPE_EXERCISE_WINDOW_TREND` (L48) com código `exercise_window_e1rm_trend`, rótulo "Tendência da janela", comparador `first_half_vs_second_half_of_window` (entrada em `PROGRESSION_SCOPES`, L111). São cinco descritores.
6. **Detalhe do exercício.** `api/app/intelligence/dashboard/exposure_service.py::_window_trend_payload` (L997) monta o bloco com `status`, `label`, `reasons`, `change_pct`, `initial_mean`, `final_mean`, `exposures_used`, `rule_version` e `scope`; `build_exercise_progress` o expõe na chave `window_trend` (L1084). `api/app/intelligence/dashboard/contracts.py`: `WindowTrendOut` (L413) e o campo aditivo `window_trend: WindowTrendOut | None` em `ExerciseProgressResponse` (L441). A leitura atual (`status`, `status_scope`, `progression-status-v2`, "Última exposição vs. anterior") não muda. `VERDICT_LOOKBACK_EXPOSURES` não muda: a tendência usa a janela exibida inteira, não o sufixo do veredito.
7. **Frontend só formata.** `frontend/src/lib/exercise-window-trend.ts` (`buildWindowTrendView`) formata o que veio da API e devolve `null` sem o campo. O título do bloco vem de `windowTrendHeading(scope)`: o rótulo do escopo enviado pela API, ou `WINDOW_TREND_FALLBACK_TITLE` quando o campo não vem. `frontend/src/app/(app)/dashboard/exercicios/[exerciseId]/ExerciseProgressClient.tsx` mostra a "Leitura atual" (com o `ScopeCaption` do status) e, abaixo, o bloco da tendência, sem legenda de escopo. `frontend/src/mobile/screens/evolucao/ExercicioScreen.tsx` mostra o bloco da tendência do mesmo jeito. Resposta sem o campo não mostra a seção.
8. **Escopo no snapshot da IA (#177).** `api/app/intelligence/progression_service.py::exercise_progression_context` (L101) põe `status_scope` = `progression_scope(SCOPE_EXERCISE_LAST_VS_PREVIOUS)` em cada item. `api/app/report_ai.py::_ai_training_payload` (L126-130) retira `status_scope` de cada item antes de montar o prompt, sem mutar a entrada. `PROMPT_VERSION` não muda. A tendência NÃO entra no snapshot.
9. **Escopo na tela da IA.** `frontend/src/app/(app)/dashboard/treinos/AnaliseIaSnapshot.tsx` mostra o escopo de cada item com `ScopeCaption`. `frontend/src/mobile/screens/evolucao/AnaliseIaSnapshotMobile.tsx` põe o rótulo (`scopeLabel`) no subtítulo da linha, em texto, sem selo. As duas só mostram quando o snapshot traz o campo.

## Trade-offs

- Ganha: a tela de detalhe responde duas perguntas diferentes, cada uma com selo e escopo próprios, sem mudar o status existente.
- Ganha: o snapshot da IA ganha a informação de escopo sem mudar o prompt; a análise recebida pelo usuário não muda.
- Paga: a tendência depende da janela escolhida. Quem muda o período muda o número (ver Limites, b).
- Paga: com N ímpar a exposição do meio não conta, o que descarta informação real em janelas pequenas.
- Paga: o texto da regra e o limiar vivem no backend; corrigir a descrição exige deploy da API.

## Consequências

- **Dados.** Sem migração. Nenhum dado gravado muda. Nenhuma leitura existente muda de valor.
- **Contrato.** `ExerciseProgressResponse` ganha `window_trend` (aditivo, opcional no frontend). O snapshot da IA ganha `status_scope` em cada item de `exercise_progression`, que não vai para o prompt.
- **Operação.** Nenhuma nova consulta SQL: a tendência é calculada sobre as exposições que o detalhe já lê.
- **Próximas decisões.** Levar a tendência ao painel geral, à progressão por grupo ou à análise por IA é decisão nova, com replay pelo motor antes. Qualquer nova leitura de tendência entra em `comparability.py`, nunca em consumidor.
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md`: linha 71 (dono), linha 146 (escopos, agora cinco) e "Lote 8 de 20/11/2026" em "Impacto em produção".

## Limites e pendências

- (a) Exposição sem RPE não entra na tendência, porque sem RPE não há e1RM confiável (caso geral: menos de 4 válidas, `REASON_NOT_ENOUGH_EXPOSURES`; média inicial não positiva também devolve `REASON_NOT_ENOUGH_EXPOSURES`, corrigido em 21/11/2026, ver Atualização).
- (b) A janela é a exibida no detalhe: mudar o período muda a tendência. Isso é intencional (a tendência é do período que a tela mostra), mas precisa ser lido com o selo "Tendência da janela".
- (c) Com N ímpar a exposição do meio não conta nas médias.
- (d) A tendência não está no painel geral (`overview_service`), na progressão por grupo nem na análise por IA.
- (e) Não há teste de renderização do detalhe do exercício (`ExerciseProgressClient.tsx`, `ExercicioScreen.tsx`). A cobertura é da função pura `window_trend`, do contrato e de `buildWindowTrendView`.
- (f) No mobile do snapshot, o escopo aparece como texto no subtítulo da linha, não como selo.
- (g) Nenhuma tela foi vista em navegador nem em iPhone.
- (h) Resolvido em 21/11/2026 pelo replay no banco de desenvolvimento (ver Atualização): 10 de 81 pares atingem 4 exposições comparáveis. A base de 93 pares citada no Lote 8 do mapa não coincide com a do replay (81 pares, janela de 90 dias).

## Verificação

- Suíte unitária inteira do backend, local: 17.877 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Guarda dos motores, executada depois das mudanças do frontend: 485 passaram; a allowlist não mudou.
- Frontend: 61 testes Vitest dos diretórios tocados passaram; `tsc --noEmit` sem erro.
- Não verificado: nenhuma tela foi vista em navegador nem em iPhone; a tendência em produção (o replay de 21/11/2026 rodou no banco de desenvolvimento, cópia de produção).

## Atualização de 21/11/2026 (#239 e #177)

**Replay com dados reais (#239).** Feito em 21/11/2026 no banco de desenvolvimento (cópia de produção), pelo próprio motor e só com leitura: `exposure_service.build_exercise_progress` para cada item de `get_exercises_progression_batch`, janela de 90 dias, por objetivo. Um usuário, 81 pares exercício×objetivo:

- 71 com dados insuficientes: 51 por `not_enough_comparable_exposures` e 20 por `equipment_context_in_calibration`;
- 8 progredindo (`e1rm_up`), 2 estáveis (`no_change`), nenhum regredindo.

Exposições comparáveis usadas por par: 0 em 33 pares, 1 em 20, 2 em 10, 3 em 8 e 4 ou mais em 10.

Consequência: só 10 de 81 pares atingem o mínimo de 4 exposições comparáveis (`THRESHOLDS.window_trend_min_exposures`, `metrics_registry.py` L370). A tendência aparece hoje em cerca de um a cada oito exercícios. O mínimo e a janela não mudaram, por decisão do usuário de 19/11/2026 (metades da janela, mínimo de 4). Mudar um dos dois é decisão nova, com replay antes.

**Motivo na média inicial não positiva.** `comparability.window_trend` (L1409-1410) devolve `REASON_NOT_ENOUGH_EXPOSURES` quando a média inicial não é positiva. A versão anterior devolvia `REASON_NO_E1RM_WITHOUT_RPE`, que fala de RPE e não descrevia o caso. O item (a) dos limites está corrigido.

**Título do bloco (web e mobile).** `frontend/src/lib/exercise-window-trend.ts::windowTrendHeading(scope)` devolve o rótulo do escopo enviado pela API como título, e a descrição do escopo como `title` do elemento. Sem o campo `scope`, o título é `WINDOW_TREND_FALLBACK_TITLE` ("Tendência da janela"). Usado em `ExerciseProgressClient.tsx` e `ExercicioScreen.tsx`. A legenda que repetia o escopo saiu do bloco.

**Testes do frontend.** `frontend/src/lib/exercise-window-trend.test.ts` usa o código de motivo real (`not_enough_comparable_exposures`). `frontend/src/app/(app)/dashboard/exercicios/[exerciseId]/ExerciseProgressClient.titulos.test.ts` cobre `windowTrendHeading`, inclusive a reserva sem escopo.

**Guarda dos motores.** `api/tests/unit/test_guarda_fontes_da_verdade.py` ganha o padrão `tendencia_janela_paralela`. Ele acusa atribuição de `initial_mean`/`final_mean` (ou `initialMean`/`finalMean`) fora de `comparability.py` e média de fatia de janela (`sum(x[:k]) / k`) fora dele. A allowlist nasce vazia. Como o ramo de média de fatia foi generalizado no lote 32 (ver a atualização de 22/11/2026), ele não depende mais do nome da variável. A forma antiga com `half` e qualquer divisor continua acusada; a forma geral exige `:` na fatia e a mesma variável na fatia e no divisor.

**Limites que continuam.** (e), (f) e (g) acima seguem valendo: sem teste de renderização do detalhe, telas não vistas em navegador nem em iPhone, e o escopo do mobile do snapshot como texto. A tendência continua fora do painel geral, da progressão por grupo e da análise por IA (item d).

## Atualização de 22/11/2026 (lote 32)

- **Guarda.** O ramo de média de fatia de `tendencia_janela_paralela` (`api/tests/unit/test_guarda_fontes_da_verdade.py`) acusa `sum(x[:k]) / k`, `sum(x[-k:]) / k` e `sum(x[:len(x)//2]) / n`. Antes só acusava a forma com o nome `half`; agora acusa também a média de fatia com qualquer nome de variável, e a forma com `half` foi mantida. A allowlist continua vazia.
- **Sem mudança de número.** Status, tendência, limiar, janela e contrato não mudaram. Não há replay novo: o replay de 21/11/2026 segue valendo.
- **Limites (e), (f) e (g) seguem.**
